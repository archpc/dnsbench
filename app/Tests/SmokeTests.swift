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

    @MainActor
    func testRunStoreScanFindsRealRuns() async throws {
        let store = RunStore()
        store.setDataDir(URL(fileURLWithPath: "/Users/sein/Code/dnsbench/datasets"))
        // wait for the async scan to land
        for _ in 0..<50 {
            if !store.runs.isEmpty { break }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        XCTAssertGreaterThan(store.runs.count, 50, "expected real historical runs")
        XCTAssertTrue(store.runs.allSatisfy { $0.timestamp < Date() })
        let complete = try XCTUnwrap(store.runs.first(where: \.complete))
        let detail = await store.loadDetail(complete.id)
        XCTAssertNotNil(detail?.rankedFile, "complete run must parse")
        XCTAssertNotNil(detail?.rankedFile?.ranked.first?.uMed)
        let samples = await store.loadSamples(complete.id)
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
