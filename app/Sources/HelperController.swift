import Foundation

// MARK: - Helper process controller (IPC protocol v1)
//
// Owns the bundled Python helper process. Contract (engine/helper.py):
// - line-framed JSON on stdin/stdout; stderr = diagnostics
// - every accepted request gets exactly one terminal response
// - cooperative cancel + orderly shutdown with bounded grace
// - partial run dirs are preserved on cancel/crash (never deleted)

enum HelperError: LocalizedError {
    case notRunning
    case timeout(String)
    case helperError(String)

    var errorDescription: String? {
        switch self {
        case .notRunning: return "Helper process is not running."
        case .timeout(let what): return "Helper timed out: \(what)"
        case .helperError(let e): return e
        }
    }
}

struct PingInfo: Sendable {
    var helperVersion: Int
    var protocolVersion: Int
    var pythonVersion: String
    var dataDir: String
    var poolEligible: Int?
}

struct LiveProgress: Sendable, Equatable {
    var command: String?
    var state: String?            // started / done / cancelled / error
    var phase: String?
    var phaseDone: Int = 0
    var phaseTotal: Int = 0
    var windowIndex: Int = 0
    var windowTotal: Int = 0
    var queryCount: Int = 0
    var startedAt: Date?
}

@MainActor
@Observable
final class HelperController {
    enum State: Equatable {
        case idle
        case starting
        case ready
        case busy
        case stopping
        case exited(Int32)
    }

    private(set) var state: State = .idle
    private(set) var progress = LiveProgress()
    /// Rolling diagnostic log (stderr + notable events), newest last.
    private(set) var diagnostics: [String] = []

    private var process: Process?
    private var stdoutBuffer = ""
    private var pending: [Int: CheckedContinuation<Data?, Never>] = [:]
    private var nextID = 1
    private var stderrTail: [String] = []
    private var expectedExit: Bool = false
    private let queue = DispatchQueue(label: "helper.ipc")

    // Resolved paths (set at init; documented for the final report).
    let pythonPath: String
    let helperScript: String
    let dataDir: URL

    init(dataDir: URL) {
        self.dataDir = dataDir
        let res = Bundle.main.resourceURL!
        let bundledPython = res.appendingPathComponent("python/bin/python3.13")
        let bundledHelper = res.appendingPathComponent("engine/helper.py")
        if FileManager.default.fileExists(atPath: bundledPython.path),
           FileManager.default.fileExists(atPath: bundledHelper.path) {
            pythonPath = bundledPython.path
            helperScript = bundledHelper.path
        } else {
            // Dev fallback: repository checkout + Homebrew interpreter.
            // Documented in the README; the packaged app uses the bundle.
            let repo = URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()   // Sources
                .deletingLastPathComponent()   // app
                .deletingLastPathComponent()   // repo root
            pythonPath = "/opt/homebrew/bin/python3.15"
            helperScript = repo.appendingPathComponent("engine/helper.py").path
        }
    }

    var isRunning: Bool {
        guard let p = process else { return false }
        return p.isRunning
    }

    // MARK: lifecycle

    func start() {
        guard process == nil else { return }
        state = .starting
        expectedExit = false
        progress = LiveProgress()
        let p = Process()
        p.executableURL = URL(fileURLWithPath: pythonPath)
        p.arguments = [helperScript]
        var env = ProcessInfo.processInfo.environment
        env["DNSBENCH_DATA_DIR"] = dataDir.path
        env["PYTHONUNBUFFERED"] = "1"
        env.removeValue(forKey: "PYTHONPATH")
        env.removeValue(forKey: "PYTHONHOME")
        p.environment = env
        let outPipe = Pipe(), errPipe = Pipe(), inPipe = Pipe()
        p.standardOutput = outPipe
        p.standardError = errPipe
        p.standardInput = inPipe
        p.terminationHandler = { [weak self] proc in
            let status = proc.terminationStatus
            Task { @MainActor [weak self] in
                self?.handleTermination(status: status)
            }
        }
        do {
            try p.run()
        } catch {
            log("failed to start helper: \(error)")
            state = .exited(-1)
            return
        }
        process = p
        outPipe.fileHandleForReading.readabilityHandler = { [weak self] fh in
            let data = fh.availableData
            guard !data.isEmpty else { return }
            self?.queue.async { self?.consume(data) }
        }
        errPipe.fileHandleForReading.readabilityHandler = { [weak self] fh in
            let data = fh.availableData
            guard !data.isEmpty, let s = String(data: data, encoding: .utf8) else {
                return
            }
            let text = s
            Task { @MainActor [weak self] in
                self?.log(text.trimmingCharacters(in: .newlines))
            }
        }
        state = .ready
    }

    private func handleTermination(status: Int32) {
        process = nil
        // fail all pending requests so continuations never leak
        let waiting = pending
        pending.removeAll()
        for (_, cont) in waiting {
            cont.resume(returning: nil)
        }
        if !expectedExit {
            log("helper terminated unexpectedly (status \(status)); partial run data preserved")
        }
        state = .exited(status)
    }

    /// Orderly shutdown: request + wait bounded, then SIGTERM fallback.
    func shutdown() async {
        guard let p = process else { return }
        state = .stopping
        expectedExit = true
        do {
            _ = try await request("shutdown", timeout: 10)
        } catch {
            log("shutdown request failed: \(error.localizedDescription)")
        }
        // helper exits itself after draining (<= 20 s grace); wait a bit more
        if await waitExit(p, seconds: 25) { return }
        log("helper exceeded shutdown grace; sending SIGTERM")
        p.terminate()
        if await waitExit(p, seconds: 5) { return }
        kill(p.processIdentifier, SIGKILL)
    }

    private func waitExit(_ p: Process, seconds: Double) async -> Bool {
        let deadline = Date().addingTimeInterval(seconds)
        while p.isRunning && Date() < deadline {
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        return !p.isRunning
    }

    // MARK: requests

    private func request(_ cmd: String, args: [String: Any] = [:],
                         timeout: Double = 60) async throws -> Data {
        guard process != nil else { throw HelperError.notRunning }
        let id = nextID
        nextID += 1
        var req: [String: Any] = ["v": 1, "id": id, "cmd": cmd]
        for (k, v) in args { req[k] = v }
        let line = try JSONSerialization.data(withJSONObject: req) + Data([0x0A])
        let data: Data? = await withCheckedContinuation { cont in
            pending[id] = cont
            inPipe()?.write(line)
            self.queue.asyncAfter(deadline: .now() + timeout) { [weak self] in
                guard let self, let c = self.pending.removeValue(forKey: id) else {
                    return
                }
                c.resume(returning: nil)  // timeout sentinel
            }
        }
        guard let data else {
            throw process == nil
                ? HelperError.notRunning
                : HelperError.timeout(cmd)
        }
        let obj = try JSONSerialization.jsonObject(with: data)
        guard let dict = obj as? [String: Any] else {
            throw HelperError.helperError("malformed response")
        }
        if dict["ok"] as? Bool == true {
            if let d = dict["data"] {
                return try JSONSerialization.data(withJSONObject: d)
            }
            return Data("{}".utf8)
        }
        throw HelperError.helperError(dict["error"] as? String ?? "unknown helper error")
    }

    private func inPipe() -> FileHandle? {
        (process?.standardInput as? Pipe)?.fileHandleForWriting
    }

    func ping() async throws -> PingInfo {
        let d = try await request("ping", timeout: 15)
        let o = try JSONDecoder().decode(PingRaw.self, from: d)
        return PingInfo(helperVersion: o.helper, protocolVersion: o.protocolVersion,
                        pythonVersion: o.python, dataDir: o.dataDir,
                        poolEligible: o.poolEligible)
    }

    private struct PingRaw: Decodable {
        var helper: Int
        var protocolVersion: Int
        var python: String
        var dataDir: String
        var poolEligible: Int?

        enum CodingKeys: String, CodingKey {
            case helper, python, dataDir, poolEligible
            case protocolVersion = "protocol"
        }
    }

    func selftest() async throws -> (passed: Int, total: Int) {
        let d = try await request("selftest")
        let o = try JSONDecoder().decode(SelftestRaw.self, from: d)
        return (o.passed, o.total)
    }

    private struct SelftestRaw: Decodable {
        var passed: Int
        var total: Int
    }

    // MARK: long jobs

    struct MeasureOutcome: Sendable {
        var cancelled: Bool
        var runDirs: [String]
        var manifest: String?
        var rankedJSON: Data?
        var aggregateJSON: Data?
    }

    func measure(n: Int, workers: Int, windows: Int, engine: String,
                 timeout: Double?) async throws -> MeasureOutcome {
        var args: [String: Any] = ["n": n, "workers": workers,
                                   "windows": windows, "engine": engine]
        if let timeout { args["timeout"] = timeout }
        progress = LiveProgress(command: "measure", state: "started",
                                startedAt: Date())
        state = .busy
        defer { state = .ready }
        let d = try await request("measure", args: args, timeout: .infinity)
        let o = try JSONDecoder().decode(MeasureRaw.self, from: d)
        return MeasureOutcome(cancelled: o.cancelled, runDirs: o.runDirs,
                              manifest: o.manifest,
                              rankedJSON: o.ranked.flatMap {
                                  try? JSONSerialization.data(withJSONObject: $0)
                              },
                              aggregateJSON: o.aggregate.flatMap {
                                  try? JSONSerialization.data(withJSONObject: $0)
                              })
    }

    private struct MeasureRaw: Decodable {
        var cancelled: Bool
        var runDirs: [String]
        var manifest: String?
        var ranked: JSONValue?
        var aggregate: JSONValue?
    }

    func discovery(mode: String) async throws -> Data {
        state = .busy
        defer { state = .ready }
        progress = LiveProgress(command: "discovery", state: "started",
                                startedAt: Date())
        return try await request("discovery", args: ["mode": mode],
                                 timeout: .infinity)
    }

    func cancel() async {
        guard process != nil else { return }
        do {
            _ = try await request("cancel", timeout: 5)
            log("cancellation requested")
        } catch {
            log("cancel request failed: \(error.localizedDescription)")
        }
    }

    // MARK: line parsing

    private func consume(_ data: Data) {
        stdoutBuffer += String(decoding: data, as: UTF8.self)
        while let nl = stdoutBuffer.firstIndex(of: "\n") {
            let line = String(stdoutBuffer[..<nl])
            stdoutBuffer.removeSubrange(..<stdoutBuffer.index(after: nl))
            guard !line.isEmpty else { continue }
            Task { @MainActor [weak self] in self?.handleLine(line) }
        }
    }

    private func handleLine(_ line: String) {
        guard let obj = try? JSONSerialization.jsonObject(with: Data(line.utf8))
                as? [String: Any] else {
            log("unparseable stdout line: \(line.prefix(120))")
            return
        }
        if let id = obj["id"] as? Int, obj["ok"] != nil {
            // response
            if let cont = pending.removeValue(forKey: id) {
                if let data = try? JSONSerialization.data(
                    withJSONObject: obj, options: [.sortedKeys]) {
                    cont.resume(returning: data)
                }
            }
            return
        }
        guard obj["event"] != nil else { return }
        switch obj["event"] as? String {
        case "job":
            progress.state = obj["state"] as? String
            if progress.state == "done" || progress.state == "cancelled"
                || progress.state == "error" {
                progress.command = nil
            }
        case "phase":
            progress.phase = obj["name"] as? String
            progress.phaseDone = obj["done"] as? Int ?? 0
            progress.phaseTotal = obj["total"] as? Int ?? 0
        case "window":
            progress.windowIndex = obj["index"] as? Int ?? 0
            progress.windowTotal = obj["total"] as? Int ?? 0
            progress.phase = "deep"
        case "window_done":
            progress.phase = "rank"
        case "query":
            progress.queryCount += 1
        default:
            break
        }
    }

    private func log(_ s: String) {
        diagnostics.append(s)
        if diagnostics.count > 400 { diagnostics.removeFirst(diagnostics.count - 400) }
    }
}

// MARK: - JSONValue (loose decode for pass-through payloads)

enum JSONValue: Decodable, Sendable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let d = try? c.decode(Double.self) { self = .number(d) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else if let o = try? c.decode([String: JSONValue].self) { self = .object(o) }
        else { throw DecodingError.dataCorrupted(
            DecodingError.Context(codingPath: decoder.codingPath,
                                  debugDescription: "unsupported JSON value")) }
    }
}
