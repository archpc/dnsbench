import XCTest
@testable import DNSBench

/// Regression tests for the 2026-10-21 review findings. Hermetic: fixture
/// data dirs under NSTemporaryDirectory and a scripted fake helper process
/// that speaks protocol v1 (no network, no user defaults, no real datasets).
@MainActor
final class ReviewFixTests: XCTestCase {
    private var tmp: URL!

    override func setUp() async throws {
        tmp = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("dnsbench-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
    }

    override func tearDown() async throws {
        try? FileManager.default.removeItem(at: tmp)
    }

    // MARK: fake helper

    static var python: String {
        for p in ["/opt/homebrew/bin/python3.15", "/opt/homebrew/bin/python3",
                  "/usr/bin/python3"] where FileManager.default.fileExists(atPath: p) {
            return p
        }
        return "/usr/bin/python3"
    }

    /// Writes a protocol-v1 fake helper. `measure` behaviour: "crash" exits 3,
    /// otherwise sleeps `sleep` seconds then answers with one run dir.
    private func fakeHelper(measure: String = "ok", sleep: Double = 0,
                            poolEligible: Int? = 10) throws -> String {
        try Self.writeFakeHelper(in: tmp, measure: measure, sleep: sleep,
                                 poolEligible: poolEligible)
    }

    static func writeFakeHelper(in tmp: URL, measure: String, sleep: Double,
                                poolEligible: Int?) throws -> String {
        let pool = poolEligible.map(String.init) ?? "None"
        let src = """
        import json, sys, time, os
        def reply(i, data):
            sys.stdout.write(json.dumps({"v": 1, "id": i, "ok": True, "data": data}) + "\\n")
            sys.stdout.flush()
        for line in sys.stdin:
            r = json.loads(line); i = r.get("id"); c = r.get("cmd")
            if c == "ping":
                reply(i, {"helper": 2, "protocol": 1, "python": "fake",
                          "data_dir": os.environ.get("DNSBENCH_DATA_DIR", ""),
                          "pool_eligible": \(pool)})
            elif c == "measure":
                if "\(measure)" == "crash":
                    os._exit(3)
                time.sleep(\(sleep))
                reply(i, {"cancelled": False, "run_dirs": ["/x/20990101T000000Z"],
                          "benchmark_id": "20990101T000000Z", "manifest": None,
                          "ranked": None, "aggregate": None})
            elif c == "cancel":
                reply(i, {"cancelled": True})
            elif c == "shutdown":
                reply(i, {"bye": True}); sys.exit(0)
        """
        let url = tmp.appendingPathComponent("fake-helper-\(UUID().uuidString).py")
        try src.write(to: url, atomically: true, encoding: .utf8)
        return url.path
    }

    private func controller(_ script: String) -> HelperController {
        HelperController(dataDir: tmp, pythonPath: Self.python, helperScript: script)
    }

    private func waitUntil(_ seconds: Double = 5, _ cond: () -> Bool) async {
        let end = Date().addingTimeInterval(seconds)
        while !cond() && Date() < end {
            try? await Task.sleep(nanoseconds: 20_000_000)
        }
    }

    // MARK: real helper.py over real IPC (wire keys, empty data dir -> no pool)

    func testRealHelperPingDecodesWireKeysAndReportsMissingPool() async throws {
        let repo = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent()
        let script = repo.appendingPathComponent("engine/helper.py").path
        try XCTSkipUnless(FileManager.default.fileExists(atPath: script))
        let h = HelperController(dataDir: tmp, pythonPath: Self.python, helperScript: script)
        h.start()
        let info = try await h.ping()
        XCTAssertEqual(info.protocolVersion, 1)
        XCTAssertEqual(info.dataDir, tmp.path)       // "data_dir" on the wire
        XCTAssertNil(info.poolEligible)               // "pool_eligible": null
        let st = try await h.selftest()
        XCTAssertEqual(st.passed, st.total)
        XCTAssertEqual(st.total, 10)
        await h.shutdown()
        XCTAssertEqual(h.state, .exited(0))
    }

    // MARK: #8 helper crash must not read as "ready"

    func testHelperCrashDuringMeasureLeavesExitedState() async throws {
        let h = controller(try fakeHelper(measure: "crash"))
        h.start()
        _ = try await h.ping()
        do {
            _ = try await h.measure(n: 1, workers: 1, windows: 1, engine: "kqueue",
                                    timeout: nil)
            XCTFail("measure should throw when the helper dies")
        } catch {}
        await waitUntil { if case .exited = h.state { return true }; return false }
        XCTAssertEqual(h.state, .exited(3))
        XCTAssertFalse(h.isRunning)
    }

    // MARK: restart race: a stale termination must not clobber the new process

    func testRestartKeepsNewProcess() async throws {
        let h = controller(try fakeHelper())
        h.start()
        _ = try await h.ping()
        await h.shutdown()
        h.start()
        try await Task.sleep(nanoseconds: 300_000_000)  // let stale handlers drain
        XCTAssertTrue(h.isRunning)
        XCTAssertEqual(h.state, .ready)
        let info = try await h.ping()
        XCTAssertEqual(info.pythonVersion, "fake")
    }

    // MARK: #5 changing data dir must stop the old helper

    func testSetDataDirStopsOldHelper() async throws {
        let script = try fakeHelper()
        let defaults = UserDefaults(suiteName: "dnsbench-tests-\(UUID().uuidString)")!
        let model = AppModel(defaults: defaults) { dir in
            HelperController(dataDir: dir, pythonPath: Self.python,
                             helperScript: script)
        }
        await waitUntil { model.pingInfo != nil }
        let oldPID = try XCTUnwrap(model.helper.processIdentifier)
        await model.setDataDir(tmp.appendingPathComponent("other"))
        await waitUntil { kill(oldPID, 0) != 0 }
        XCTAssertNotEqual(kill(oldPID, 0), 0, "old helper \(oldPID) still alive")
        XCTAssertNotEqual(model.helper.processIdentifier, oldPID)
        await model.helper.shutdown()
    }

    // MARK: #2 / #3 / #21 grouping and aggregates (legacy + new layouts)

    private func writeRun(_ runs: URL, _ name: String, ranked: Bool = true,
                          cancelled: Bool = false) throws {
        let d = runs.appendingPathComponent(name)
        try FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        if ranked {
            try #"{"n_ranked":1,"ranked":[{"ip":"192.0.2.1","u_med":20,"c_med":5,"to_u":0,"to_c":0,"bad_c":0,"strays":0,"fail_u":3}]}"#
                .write(to: d.appendingPathComponent("ranked.json"), atomically: true, encoding: .utf8)
        }
        if cancelled {
            try #"{"cancelled":true}"#.write(to: d.appendingPathComponent("cancelled.json"),
                                             atomically: true, encoding: .utf8)
        }
    }

    private func aggregateJSON(_ windows: [String], manifest: String) -> String {
        let w = windows.map { "\"\($0)\"" }.joined(separator: ",")
        return """
        {"manifest":"\(manifest)","windows":[\(w)],"rank_band_1":["192.0.2.1"],
         "servers":[{"ip":"192.0.2.1","u_med":20,"wire_med":19,"between_range_ms":1,
                     "rank_band":1,"success_u":1,"success_c":1,"provider":"X"}],
         "notes":{"exploratory":"3 window(s): exploratory"}}
        """
    }

    func testLegacyWindowsGroupedViaAggregateAndManifest() async throws {
        let runs = tmp.appendingPathComponent("runs")
        let names = ["20261020T005905Z_w1", "20261020T005927Z_w2", "20261020T010319Z_w3"]
        for n in names { try writeRun(runs, n) }
        try writeRun(runs, "20261019T120000Z")
        let manifest = runs.appendingPathComponent("windows-20261020T005905Z.json")
        try "{\"windows\":[\(names.map { "\"\(runs.path)/\($0)\"" }.joined(separator: ","))]}"
            .write(to: manifest, atomically: true, encoding: .utf8)
        try aggregateJSON(names, manifest: manifest.path)
            .write(to: runs.appendingPathComponent("aggregate.json"), atomically: true,
                   encoding: .utf8)
        let store = RunStore()
        store.setDataDir(tmp)
        await store.scan()
        let ws = store.runs.filter(\.isWindow)
        XCTAssertEqual(ws.count, 3)
        XCTAssertEqual(Set(ws.map(\.windowGroupID)), ["20261020T005905Z"])
        XCTAssertTrue(ws.allSatisfy(\.aggregateAvailable))
        let d = await store.loadDetail("20261020T005927Z_w2")
        XCTAssertEqual(d?.aggregate?.rankBand1, ["192.0.2.1"])
        XCTAssertEqual(d?.aggregate?.notes?["exploratory"], "3 window(s): exploratory")
        let single = await store.loadDetail("20261019T120000Z")
        XCTAssertNil(single?.aggregate)
    }

    func testNewLayoutPerBenchmarkAggregate() async throws {
        let runs = tmp.appendingPathComponent("runs")
        for w in 1...2 { try writeRun(runs, "20261101T000000Z_w\(w)") }
        for w in 1...2 { try writeRun(runs, "20261102T000000Z_w\(w)") }
        let a = runs.appendingPathComponent("windows-20261101T000000Z.json")
        try aggregateJSON(["20261101T000000Z_w1", "20261101T000000Z_w2"],
                          manifest: a.path)
            .write(to: runs.appendingPathComponent("windows-20261101T000000Z.aggregate.json"),
                   atomically: true, encoding: .utf8)
        let store = RunStore()
        store.setDataDir(tmp)
        await store.scan()
        let first = store.runs.filter { $0.windowGroupID == "20261101T000000Z" }
        let second = store.runs.filter { $0.windowGroupID == "20261102T000000Z" }
        XCTAssertEqual(first.count, 2)
        XCTAssertEqual(second.count, 2)
        XCTAssertTrue(first.allSatisfy(\.aggregateAvailable))
        XCTAssertFalse(second.contains(where: \.aggregateAvailable))
    }

    // MARK: review R1: helper collision IDs must round-trip through the app

    func testCollisionSuffixedRunIDsParseAndGroup() async throws {
        XCTAssertNotNil(RunParser.parseRunID("20261022T012444Z-2"))
        let w = RunParser.parseRunID("20261022T012444Z-2_w3")
        XCTAssertEqual(w?.window, 3)
        XCTAssertNil(RunParser.parseRunID("20261022T012444Z-x"))
        let runs = tmp.appendingPathComponent("runs")
        for n in ["20261022T012444Z_w1", "20261022T012444Z-2_w1", "20261022T012444Z-2_w2"] {
            try writeRun(runs, n)
        }
        let store = RunStore()
        store.setDataDir(tmp)
        await store.scan()
        XCTAssertEqual(store.runs.count, 3)
        XCTAssertEqual(store.runs.filter { $0.windowGroupID == "20261022T012444Z-2" }.count, 2)
        let d = await store.loadDetail("20261022T012444Z-2_w2")
        XCTAssertNotNil(d?.rankedFile)
    }

    // MARK: review R3: a rescan must refresh cached detail when a run changes

    func testRescanRefreshesCachedPartialRun() async throws {
        let runs = tmp.appendingPathComponent("runs")
        try writeRun(runs, "20261106T000000Z", ranked: false)
        let store = RunStore()
        store.setDataDir(tmp)
        await store.scan()
        let before = await store.loadDetail("20261106T000000Z")
        XCTAssertEqual(before?.summary.complete, false)
        _ = await store.loadSamples("20261106T000000Z")       // cached empty
        // the window finishes: ranking + samples land on disk
        let d = runs.appendingPathComponent("20261106T000000Z")
        try #"{"n_ranked":1,"ranked":[{"ip":"192.0.2.9","u_med":9}]}"#
            .write(to: d.appendingPathComponent("ranked.json"), atomically: true, encoding: .utf8)
        try (#"{"ip":"192.0.2.9","uncached":[9],"cached":[1]}"# + "\n")
            .write(to: d.appendingPathComponent("deepbench-results.jsonl"),
                   atomically: true, encoding: .utf8)
        await store.scan()
        XCTAssertEqual(store.runs.first?.complete, true)
        let after = await store.loadDetail("20261106T000000Z")
        XCTAssertEqual(after?.summary.complete, true, "stale cached detail")
        XCTAssertEqual(after?.rankedFile?.ranked.first?.ip, "192.0.2.9")
        let samples = await store.loadSamples("20261106T000000Z")
        XCTAssertEqual(samples.map(\.ip), ["192.0.2.9"], "stale cached samples")
    }

    // MARK: #10 cancelled partial windows are labelled, not "complete"

    func testCancelledWindowIsPartial() async throws {
        let runs = tmp.appendingPathComponent("runs")
        try writeRun(runs, "20261103T000000Z", ranked: false, cancelled: true)
        let store = RunStore()
        store.setDataDir(tmp)
        await store.scan()
        let r = try XCTUnwrap(store.runs.first)
        XCTAssertFalse(r.complete)
        XCTAssertTrue(r.cancelled)
    }

    // MARK: #15 failures decode and surface

    func testRankedServerDecodesFailU() async throws {
        let runs = tmp.appendingPathComponent("runs")
        try writeRun(runs, "20261104T000000Z")
        let store = RunStore()
        store.setDataDir(tmp)
        await store.scan()
        let d = await store.loadDetail("20261104T000000Z")
        XCTAssertEqual(d?.rankedFile?.ranked.first?.failU, 3)
    }

    // MARK: #16 per-query rows need unique identity

    func testQueryRecordsHaveUniqueIDs() async throws {
        let runs = tmp.appendingPathComponent("runs")
        try writeRun(runs, "20261105T000000Z")
        let line = #"{"ip":"172.64.37.222","kind":"cached","name":"google.com","ms":32.1,"rcode":0,"an":6}"#
        try (line + "\n" + line + "\n").write(
            to: runs.appendingPathComponent("20261105T000000Z/deepbench-perquery.jsonl"),
            atomically: true, encoding: .utf8)
        let store = RunStore()
        store.setDataDir(tmp)
        let q = await store.loadQueries("20261105T000000Z")
        XCTAssertEqual(q.count, 2)
        XCTAssertEqual(Set(q.map(\.id)).count, 2)
    }

}
