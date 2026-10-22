import Foundation
import SwiftUI

/// App-wide state. Single owner of the run store, helper controller, benchmark
/// coordinator, and cross-view selection.
@MainActor
@Observable
final class AppModel {
    let store = RunStore()
    private(set) var helper: HelperController
    /// Application-scoped benchmark lifecycle. Views observe; never own.
    private(set) var benchmark: BenchmarkCoordinator!

    enum Pane: String, CaseIterable, Identifiable {
        case overview, benchmark, rankings, explore, history
        var id: String { rawValue }

        var label: String {
            switch self {
            case .overview: "Overview"
            case .benchmark: "Benchmark"
            case .rankings: "Rankings"
            case .explore: "Explore"
            case .history: "History"
            }
        }

        var symbol: String {
            switch self {
            case .overview: "gauge"
            case .benchmark: "stopwatch"
            case .rankings: "list.number"
            case .explore: "magnifyingglass"
            case .history: "clock.arrow.circlepath"
            }
        }
    }

    var pane: Pane = .overview
    /// Run selected for analysis (rankings/explore sources).
    var selectedRunID: String?
    /// Resolver selected within a run (drives the inspector).
    var selectedIP: String?
    var pingInfo: PingInfo?

    static let dataDirKey = "dataDir"
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let makeHelper: (URL) -> HelperController

    init(defaults: UserDefaults = .standard,
         makeHelper: @escaping (URL) -> HelperController = { HelperController(dataDir: $0) }) {
        self.defaults = defaults
        self.makeHelper = makeHelper
        let dir: URL
        if let raw = defaults.string(forKey: Self.dataDirKey),
           !raw.isEmpty {
            dir = URL(fileURLWithPath: (raw as NSString).expandingTildeInPath)
        } else {
            dir = URL(fileURLWithPath: NSString(string: "~/Library/Application Support/DNSBench").expandingTildeInPath)
        }
        helper = makeHelper(dir)
        store.setDataDir(dir)
        benchmark = BenchmarkCoordinator(model: self)
        Task { await refreshHelperInfo() }
    }

    /// Switches data directory. The previous helper is shut down first (its
    /// pipes do not close on deallocation, so dropping it would orphan the
    /// process and any job it was running).
    func setDataDir(_ url: URL) async {
        if benchmark.isRunning { await benchmark.cancelAndWait() }
        await helper.shutdown()
        defaults.set(url.path, forKey: Self.dataDirKey)
        pingInfo = nil
        selectedRunID = nil
        selectedIP = nil
        helper = makeHelper(url)
        store.setDataDir(url)
        await refreshHelperInfo()
    }

    func restartHelper() async {
        if benchmark.isRunning { await benchmark.cancelAndWait() }
        await helper.shutdown()
        helper.start()
        await refreshHelperInfo()
    }

    func refreshHelperInfo() async {
        if !helper.isRunning { helper.start() }
        pingInfo = try? await helper.ping()
    }

    var selectedRunSummary: RunSummary? {
        store.runs.first { $0.id == selectedRunID }
    }

    /// Changes whenever the selected run changes OR its on-disk state does
    /// (completed, cancelled, gained an aggregate): views key reloads on it.
    var selectedRunKey: String {
        let s = selectedRunSummary
        return "\(selectedRunID ?? "")#\(s?.complete ?? false)#\(s?.cancelled ?? false)" +
            "#\(s?.aggregateAvailable ?? false)#\(s?.nRanked ?? -1)"
    }
}

/// Owns the execution lifecycle of long helper jobs (benchmark, discovery).
/// Lives as long as the app; panes can appear and disappear freely while a
/// job runs, and every view reads the same state.
@MainActor
@Observable
final class BenchmarkCoordinator {
    struct Config: Equatable {
        var n = 16
        var workers = 60
        var windows = 3
        var engine = "kqueue"
        var timeout = 2.0
    }

    enum Job: String { case benchmark, discovery }

    var config = Config()
    private(set) var activeJob: Job?
    private(set) var startedAt: Date?
    private(set) var lastError: String?
    private(set) var lastRunDirs: [String] = []
    private(set) var lastBenchmarkID: String?
    private(set) var lastOutcomeCancelled = false

    @ObservationIgnored private weak var model: AppModel?
    @ObservationIgnored private var task: Task<Void, Never>?

    init(model: AppModel) { self.model = model }

    var isRunning: Bool { activeJob != nil }

    /// Why Start is unavailable, or nil when a benchmark can start.
    var startBlockedReason: String? {
        guard let model else { return "App is shutting down." }
        if let job = activeJob { return "A \(job.rawValue) is already running." }
        guard let ping = model.pingInfo else { return "Helper is not responding." }
        if ping.poolEligible == nil {
            return "No candidate pool at \(DataPaths.pool(model.helper.dataDir)). " +
                "Build one with Discovery first."
        }
        return nil
    }

    var canStartDiscovery: Bool { !isRunning && model?.pingInfo != nil }

    @discardableResult
    func startBenchmark() -> Bool {
        guard startBlockedReason == nil, let model else { return false }
        if !model.helper.isRunning { model.helper.start() }  // recover after a crash
        begin(.benchmark)
        let cfg = config
        let helper = model.helper
        task = Task { [weak self, weak model] in
            do {
                let outcome = try await helper.measure(
                    n: cfg.n, workers: cfg.workers, windows: cfg.windows,
                    engine: cfg.engine, timeout: cfg.timeout)
                self?.lastRunDirs = outcome.runDirs
                self?.lastBenchmarkID = outcome.benchmarkID
                self?.lastOutcomeCancelled = outcome.cancelled
                if outcome.cancelled {
                    self?.lastError = "Cancelled — partial run data preserved."
                }
                await model?.store.scan()
                if let first = outcome.runDirs.first {
                    // Selection follows the new run; navigation stays with the user.
                    model?.selectedRunID = URL(fileURLWithPath: first).lastPathComponent
                }
            } catch {
                self?.lastError = error.localizedDescription
            }
            self?.end()
        }
        return true
    }

    @discardableResult
    func startDiscovery(mode: String = "auto") -> Bool {
        guard canStartDiscovery, let model else { return false }
        if !model.helper.isRunning { model.helper.start() }
        begin(.discovery)
        let helper = model.helper
        task = Task { [weak self, weak model] in
            do {
                _ = try await helper.discovery(mode: mode)
                await model?.refreshHelperInfo()   // pool_eligible now known
                await model?.store.scan()
            } catch {
                self?.lastError = error.localizedDescription
            }
            self?.end()
        }
        return true
    }

    func cancel() async {
        guard isRunning else { return }
        await model?.helper.cancel()
    }

    func cancelAndWait() async {
        await cancel()
        await task?.value
    }

    private func begin(_ job: Job) {
        activeJob = job
        startedAt = Date()
        lastError = nil
        lastRunDirs = []
        lastBenchmarkID = nil
        lastOutcomeCancelled = false
    }

    private func end() {
        activeJob = nil
        task = nil
    }
}

/// Real on-disk locations under the configured data directory.
enum DataPaths {
    static func pool(_ dataDir: URL) -> String {
        dataDir.appendingPathComponent("pool.json").path
    }

    static func runs(_ dataDir: URL) -> String {
        dataDir.appendingPathComponent("runs").path
    }
}
