import XCTest
@testable import DNSBench

/// Review-fix tests that exercise APIs introduced by the fixes
/// (BenchmarkCoordinator, DataPaths, Overview/RunTable helpers).
@MainActor
final class ReviewFixAPITests: XCTestCase {
    private var tmp: URL!

    override func setUp() async throws {
        tmp = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("dnsbench-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
    }

    override func tearDown() async throws {
        try? FileManager.default.removeItem(at: tmp)
    }

    private func fakeHelper(sleep: Double = 0, poolEligible: Int? = 10) throws -> String {
        try ReviewFixTests.writeFakeHelper(in: tmp, measure: "ok", sleep: sleep,
                                           poolEligible: poolEligible)
    }

    private func waitUntil(_ seconds: Double = 5, _ cond: () -> Bool) async {
        let end = Date().addingTimeInterval(seconds)
        while !cond() && Date() < end {
            try? await Task.sleep(nanoseconds: 20_000_000)
        }
    }

    private static var python: String { ReviewFixTests.python }

    func testMissingHelperResourcesFailExplicitly() {
        let helper = HelperController(dataDir: tmp,
            pythonPath: tmp.appendingPathComponent("missing-python").path,
            helperScript: tmp.appendingPathComponent("missing-helper.py").path)
        helper.start()
        XCTAssertEqual(helper.state, .exited(-1))
        XCTAssertTrue(helper.diagnostics.contains { $0.contains("Missing helper resources") })
        XCTAssertFalse(helper.isRunning)
    }

    func testDefaultHelperUsesBundledRuntimeAndPings() async throws {
        let helper = HelperController(dataDir: tmp)
        let resources = try XCTUnwrap(Bundle.main.resourceURL)
        XCTAssertEqual(helper.pythonPath, resources.appendingPathComponent("python/bin/python3.13").path)
        XCTAssertEqual(helper.helperScript, resources.appendingPathComponent("engine/helper.py").path)
        helper.start()
        _ = try await helper.ping()
        await helper.shutdown()
    }

    // MARK: #4 benchmark lifecycle is app-scoped, not view-scoped

    func testCoordinatorOwnsRunAndDoesNotHijackNavigation() async throws {
        let script = try fakeHelper(sleep: 0.6)
        let defaults = UserDefaults(suiteName: "dnsbench-tests-\(UUID().uuidString)")!
        let model = AppModel(defaults: defaults) { dir in
            HelperController(dataDir: dir, pythonPath: Self.python,
                             helperScript: script)
        }
        await waitUntil { model.pingInfo != nil }
        model.pane = .benchmark
        XCTAssertTrue(model.benchmark.startBenchmark())
        XCTAssertTrue(model.benchmark.isRunning)
        XCTAssertFalse(model.benchmark.startBenchmark(), "second start must be refused")
        model.pane = .history                       // user navigates away mid-run
        await waitUntil(5) { !model.benchmark.isRunning }
        XCTAssertEqual(model.benchmark.lastRunDirs, ["/x/20990101T000000Z"])
        XCTAssertEqual(model.pane, .history, "completion must not steal navigation")
        XCTAssertEqual(model.selectedRunID, "20990101T000000Z")
        await model.helper.shutdown()
    }

    // MARK: #6 no pool -> benchmark blocked with a reason; discovery offered

    func testStartBlockedWithoutPool() async throws {
        let script = try fakeHelper(poolEligible: nil)
        let defaults = UserDefaults(suiteName: "dnsbench-tests-\(UUID().uuidString)")!
        let model = AppModel(defaults: defaults) { dir in
            HelperController(dataDir: dir, pythonPath: Self.python,
                             helperScript: script)
        }
        await waitUntil { model.pingInfo != nil }
        XCTAssertNotNil(model.benchmark.startBlockedReason)
        XCTAssertFalse(model.benchmark.startBenchmark())
        XCTAssertTrue(model.benchmark.canStartDiscovery)
        await model.helper.shutdown()
    }

    // MARK: #18 / #19 / #20 small correctness items

    func testOverviewShowsSelectedRunNotLatestLabel() {
        let runs = [RunSummary(id: "B", timestamp: .now, isWindow: false, windowIndex: nil,
                               windowGroupID: "B", complete: true, nRanked: 1,
                               engine: nil, aggregateAvailable: false),
                    RunSummary(id: "A", timestamp: .distantPast, isWindow: false,
                               windowIndex: nil, windowGroupID: "A", complete: true,
                               nRanked: 1, engine: nil, aggregateAvailable: false)]
        XCTAssertEqual(OverviewView.runID(selected: "A", runs: runs), "A")
        XCTAssertEqual(OverviewView.runID(selected: nil, runs: runs), "B")
        XCTAssertEqual(OverviewView.title(runID: "A", runs: runs), "Run A")
        XCTAssertEqual(OverviewView.title(runID: "B", runs: runs), "Latest run: B")
    }

    func testPathTextUsesActualDataDir() {
        let dir = URL(fileURLWithPath: "/tmp/somewhere")
        XCTAssertEqual(DataPaths.pool(dir), "/tmp/somewhere/pool.json")
        XCTAssertEqual(DataPaths.runs(dir), "/tmp/somewhere/runs")
    }

    func testRankPositionsMatchSortedMedians() {
        let rows = [RankedServer(ip: "b", uMed: 30), RankedServer(ip: "a", uMed: 10),
                    RankedServer(ip: "c", uMed: nil)]
        let ranks = RunTable.rankPositions(rows)
        XCTAssertEqual(ranks, ["a": 1, "b": 2, "c": 3])
    }

    func testProviderMedianUsesTrueMedian() {
        XCTAssertEqual(median([10, 20]), 15)  // Overview previously took 20
    }
}
