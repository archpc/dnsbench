import Foundation

// MARK: - Run directory access
// Historical runs are immutable raw data (source-traceable). This store only
// READS run dirs; measurement writes happen exclusively inside the helper.

struct RunSummary: Identifiable, Hashable, Sendable {
    /// Directory name, e.g. "20261016T215155Z" or "20261016T215155Z_w2".
    var id: String
    var timestamp: Date
    var isWindow: Bool
    var windowIndex: Int?
    var windowGroupID: String
    var complete: Bool
    var nRanked: Int?
    var engine: String?
    var aggregateAvailable: Bool
}

struct RunDetail: Sendable {
    var summary: RunSummary
    var rankedFile: RankedFile?
    var samples: [ServerSamples]
    var aggregate: AggregateRecord?
    /// nil when the run has no pool checkpoint (deep-bench-only run).
    var poolMeta: PoolFile.PoolMeta?
}

enum RunParser {
    /// Parses "20261016T215155Z[_wN]"; returns nil for non-conforming names.
    static func parseRunID(_ id: String) -> (date: Date, window: Int?)? {
        var base = id
        var window: Int?
        if let r = id.range(of: "_w"),
           let w = Int(id[r.upperBound...]), w >= 1 {
            base = String(id[..<r.lowerBound])
            window = w
        }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyyMMdd'T'HHmmss'Z'"
        guard let d = f.date(from: base) else { return nil }
        return (d, window)
    }

    static func json<T: Decodable>(_ type: T.Type, at url: URL) -> T? {
        guard let data = try? Data(contentsOf: url, options: .mappedIfSafe) else {
            return nil
        }
        return try? JSONDecoder().decode(T.self, from: data)
    }

    static func jsonLines<T: Decodable>(_ type: T.Type, at url: URL,
                                        limit: Int? = nil) -> [T] {
        guard let data = try? Data(contentsOf: url, options: .mappedIfSafe) else {
            return []
        }
        var out: [T] = []
        var start = data.startIndex
        while start < data.endIndex {
            guard let nl = data.firstRange(of: Data([0x0A]), in: start..<data.endIndex)
            else { break }
            let line = data[start..<nl.lowerBound]
            start = nl.upperBound
            if line.isEmpty { continue }
            if let obj = try? JSONDecoder().decode(T.self, from: line) {
                out.append(obj)
                if let limit, out.count >= limit { break }
            }
        }
        return out
    }

    /// engine= line inside the ranking header comment, if present.
    static func engineHint(runURL: URL) -> String? {
        guard let text = try? String(contentsOf: runURL.appendingPathComponent("DNSBench-custom-list.txt"),
                                     encoding: .utf8) else { return nil }
        for line in text.split(separator: "\n").reversed() {
            if line.contains("engine=") {
                for tok in line.split(separator: " ") {
                    if tok.hasPrefix("engine=") { return String(tok.dropFirst(7)) }
                }
            }
            if !line.hasPrefix("#") { break }
        }
        return nil
    }
}

@Observable
@MainActor
final class RunStore {
    private(set) var dataDir: URL?
    private(set) var runs: [RunSummary] = []
    private(set) var scanError: String?
    private var detailCache: [String: RunDetail] = [:]
    private var samplesCache: [String: [ServerSamples]] = [:]

    var runsDir: URL? { dataDir?.appendingPathComponent("runs") }

    func setDataDir(_ url: URL) {
        guard url != dataDir else { return }
        dataDir = url
        detailCache.removeAll()
        samplesCache.removeAll()
        Task { await scan() }
    }

    func scan() async {
        guard let runsDir else { return }
        let dir = runsDir
        let summaries: [RunSummary] = await Task.detached(priority: .userInitiated) {
            var out: [RunSummary] = []
            let fm = FileManager.default
            guard let names = try? fm.contentsOfDirectory(atPath: dir.path) else {
                return out
            }
            for name in names.sorted().reversed() {
                let runURL = dir.appendingPathComponent(name)
                var isDir: ObjCBool = false
                guard fm.fileExists(atPath: runURL.path, isDirectory: &isDir),
                      isDir.boolValue,
                      let parsed = RunParser.parseRunID(name) else { continue }
                let ranked = RunParser.json(RankedFile.self,
                                            at: runURL.appendingPathComponent("ranked.json"))
                out.append(RunSummary(
                    id: name,
                    timestamp: parsed.date,
                    isWindow: parsed.window != nil,
                    windowIndex: parsed.window,
                    windowGroupID: parsed.window == nil
                        ? name : String(name.split(separator: "_")[0]),
                    complete: ranked != nil,
                    nRanked: ranked?.nRanked,
                    engine: RunParser.engineHint(runURL: runURL),
                    aggregateAvailable: fm.fileExists(
                        atPath: runURL.appendingPathComponent("aggregate.json").path)))
            }
            return out
        }.value
        runs = summaries
        scanError = nil
    }

    /// Synchronous cache probe: powers first-frame hydration when the pane is
    /// revisited (avoids re-entering the loading state for a cached run).
    func cachedDetail(_ id: String?) -> RunDetail? {
        guard let id else { return nil }
        return detailCache[id]
    }

    /// Full detail without raw samples (cheap; used by tables).
    func loadDetail(_ id: String) async -> RunDetail? {
        if let cached = detailCache[id] { return cached }
        guard let runsDir else { return nil }
        let runURL = runsDir.appendingPathComponent(id)
        guard let parsed = RunParser.parseRunID(id) else { return nil }
        let dir = runsDir
        let detail: RunDetail? = await Task.detached(priority: .userInitiated) {
            let ranked = RunParser.json(RankedFile.self,
                                        at: runURL.appendingPathComponent("ranked.json"))
            let aggregate = RunParser.json(AggregateRecord.self,
                                           at: runURL.appendingPathComponent("aggregate.json"))
            let pool = RunParser.json(PoolFile.self,
                                      at: runURL.appendingPathComponent("checkpoint-pool.json"))
            var isDir: ObjCBool = false
            guard FileManager.default.fileExists(atPath: runURL.path,
                                                 isDirectory: &isDir),
                  isDir.boolValue else { return nil }
            let summary = RunSummary(
                id: id, timestamp: parsed.date,
                isWindow: parsed.window != nil, windowIndex: parsed.window,
                windowGroupID: parsed.window == nil
                    ? id : String(id.split(separator: "_")[0]),
                complete: ranked != nil, nRanked: ranked?.nRanked,
                engine: RunParser.engineHint(runURL: runURL),
                aggregateAvailable: aggregate != nil)
            return RunDetail(summary: summary, rankedFile: ranked,
                             samples: [], aggregate: aggregate,
                             poolMeta: pool?.meta)
        }.value
        if let detail { detailCache[id] = detail }
        return detail
    }

    /// Raw per-server samples for distributions (heavier; loaded on demand).
    func loadSamples(_ id: String) async -> [ServerSamples] {
        if let cached = samplesCache[id] { return cached }
        guard let runsDir else { return [] }
        let url = runsDir.appendingPathComponent(id)
            .appendingPathComponent("deepbench-results.jsonl")
        let dir = runsDir
        let samples: [ServerSamples] = await Task.detached(priority: .utility) {
            RunParser.jsonLines(ServerSamples.self, at: url)
        }.value
        samplesCache[id] = samples
        _ = dir
        return samples
    }

    /// Per-query transactions for the Explore inspector (bounded).
    func loadQueries(_ id: String, limit: Int = 200_000) async -> [QueryRecord] {
        guard let runsDir else { return [] }
        let url = runsDir.appendingPathComponent(id)
            .appendingPathComponent("deepbench-perquery.jsonl")
        return await Task.detached(priority: .utility) {
            RunParser.jsonLines(QueryRecord.self, at: url, limit: limit)
        }.value
    }

    /// pool.json at the data dir (screening result).
    func loadPool() -> PoolFile? {
        guard let dataDir else { return nil }
        return RunParser.json(PoolFile.self,
                              at: dataDir.appendingPathComponent("pool.json"))
    }

    func dropCache(for id: String) {
        detailCache.removeValue(forKey: id)
        samplesCache.removeValue(forKey: id)
    }
}
