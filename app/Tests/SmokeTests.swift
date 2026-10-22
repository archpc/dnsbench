import XCTest
@testable import DNSBench

final class SmokeTests: XCTestCase {
    func testParseRunID() {
        let r = RunParser.parseRunID("20261016T215155Z")
        XCTAssertNotNil(r)
        XCTAssertEqual(r?.window, nil)
        let w = RunParser.parseRunID("20261016T215155Z_w2")
        XCTAssertEqual(w?.window, 2)
        XCTAssertNil(RunParser.parseRunID("not-a-run"))
    }

    /// Hermetic: builds a fixture data dir (one complete run, one partial)
    /// instead of depending on whatever the developer's datasets/ holds.
    @MainActor
    func testRunStoreScanFindsRuns() async throws {
        let dir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("dnsbench-smoke-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: dir) }
        let runs = dir.appendingPathComponent("runs")
        let complete = runs.appendingPathComponent("20261016T215155Z")
        let partial = runs.appendingPathComponent("20261016T220000Z")
        for d in [complete, partial] {
            try FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        }
        try #"{"n_ranked":1,"ranked":[{"ip":"192.0.2.1","u_med":20.5,"c_med":5,"to_u":0,"to_c":0,"bad_c":0,"strays":0}]}"#
            .write(to: complete.appendingPathComponent("ranked.json"), atomically: true, encoding: .utf8)
        try (#"{"ip":"192.0.2.1","uncached":[20.5,21],"cached":[5],"to_u":0,"to_c":0,"bad_c":0,"strays":0}"# + "\n")
            .write(to: complete.appendingPathComponent("deepbench-results.jsonl"), atomically: true, encoding: .utf8)
        let store = RunStore()
        store.setDataDir(dir)
        await store.scan()
        XCTAssertEqual(store.runs.map(\.id), ["20261016T220000Z", "20261016T215155Z"])
        XCTAssertTrue(store.runs.allSatisfy { $0.timestamp < Date() })
        XCTAssertEqual(store.runs.filter(\.complete).map(\.id), ["20261016T215155Z"])
        let completeRun = try XCTUnwrap(store.runs.first(where: \.complete))
        let detail = await store.loadDetail(completeRun.id)
        XCTAssertNotNil(detail?.rankedFile, "complete run must parse")
        XCTAssertNotNil(detail?.rankedFile?.ranked.first?.uMed)
        let samples = await store.loadSamples(completeRun.id)
        XCTAssertFalse(samples.isEmpty)
    }

    func testMedianAndBins() {
        XCTAssertEqual(median([3, 1, 2]), 2)
        XCTAssertEqual(median([1, 2, 3, 4]), 2.5)
        XCTAssertNil(median([]))
        let b = bins([1, 1, 2, 2, 3, 3, 4, 4, 5, 5], count: 5)
        XCTAssertEqual(b.reduce(0) { $0 + $1.count }, 10)
        XCTAssertTrue(b.allSatisfy { $0.count > 0 })
    }
}
