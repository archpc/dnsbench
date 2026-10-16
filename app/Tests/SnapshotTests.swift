import XCTest
import SwiftUI
@testable import DNSBench

/// Renders real historical-run data through the actual views via
/// ImageRenderer (off-screen; no screen-capture permission needed) and writes
/// PNGs to /tmp/dnsbench-snapshots for inspection. Also asserts displayed
/// values equal the parsed source records (data fidelity).
final class SnapshotTests: XCTestCase {
    private let datasets = URL(fileURLWithPath: "/Users/sein/Code/dnsbench/datasets")

    @MainActor
    private func render(_ view: some View, width: CGFloat, height: CGFloat,
                        name: String) throws -> URL {
        let renderer = ImageRenderer(content: view.frame(width: width,
                                                         height: height))
        renderer.scale = 2
        let cg = try XCTUnwrap(renderer.cgImage, "render failed: \(name)")
        let rep = NSBitmapImageRep(cgImage: cg)
        let png = try XCTUnwrap(rep.representation(using: .png, properties: [:]),
                                 "png encode failed: \(name)")
        let dir = URL(fileURLWithPath: "/tmp/dnsbench-snapshots")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent(name)
        try png.write(to: url)
        return url
    }

    /// Picks the newest complete single-window run.
    private func newestCompleteRun() -> (id: String, ranked: RankedFile,
                                         samples: [ServerSamples])? {
        let runsDir = datasets.appendingPathComponent("runs")
        guard let names = try? FileManager.default.contentsOfDirectory(
            atPath: runsDir.path).sorted().reversed() else { return nil }
        for name in names {
            guard RunParser.parseRunID(name) != nil,
                  name.range(of: "_w") == nil else { continue }
            let dir = runsDir.appendingPathComponent(name)
            if let ranked = RunParser.json(RankedFile.self,
                                           at: dir.appendingPathComponent("ranked.json")) {
                let samples = RunParser.jsonLines(
                    ServerSamples.self,
                    at: dir.appendingPathComponent("deepbench-results.jsonl"))
                return (name, ranked, samples)
            }
        }
        return nil
    }

    @MainActor
    func testRenderRankingsTable() throws {
        guard let run = newestCompleteRun() else {
            throw XCTSkip("no complete run available")
        }
        let table = RunTable(rows: run.ranked.ranked, ranked: run.ranked,
                             selection: .constant(run.ranked.ranked.first?.ip),
                             sortOrder: .constant([KeyPathComparator(\RankedServer.sortU,
                                                                     order: .forward)]))
            .frame(width: 1060, height: 560)
            .background(Color(nsColor: .windowBackgroundColor))
        let url = try render(table, width: 1060, height: 560,
                             name: "rankings-table.png")
        XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))

        // data fidelity: displayed (sorted) first row equals source record
        let sorted = run.ranked.ranked.sorted { $0.sortU < $1.sortU }
        let src = sorted[0]
        XCTAssertNotNil(src.uMed)
        XCTAssertTrue(run.ranked.ranked.contains { $0.ip == src.ip && $0.uMed == src.uMed })
    }

    private func rankedEntry(for ip: String, in f: RankedFile) -> RankedServer? {
        f.ranked.first { $0.ip == ip }
    }

    @MainActor
    func testRenderResolverStats() throws {
        guard let run = newestCompleteRun(), let s = run.samples.first else {
            throw XCTSkip("no samples available")
        }
        let panel = ResolverStatsPanel(s: s)
            .padding(16)
            .frame(width: 460, height: 480)
            .background(Color(nsColor: .windowBackgroundColor))
        let url = try render(panel, width: 460, height: 480,
                             name: "resolver-stats.png")
        XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))

        // fidelity: bins cover every sample; median matches the stored
        // u_med (ranked.json stores round(median, 1))
        XCTAssertEqual(bins(s.uncached, count: 24).reduce(0) { $0 + $1.count },
                       s.uncached.count)
        let med = median(s.uncached) ?? -1
        if let uMed = rankedEntry(for: s.ip, in: run.ranked)?.uMed {
            XCTAssertEqual(med, uMed, accuracy: 0.06,
                           "display median must match stored u_med within rounding")
        }
    }

    @MainActor
    func testRenderOverviewHeadline() throws {
        guard let run = newestCompleteRun() else {
            throw XCTSkip("no complete run available")
        }
        let ranked = run.ranked.ranked
        let top = Array(ranked.sorted { $0.sortU < $1.sortU }.prefix(5))
        let view = VStack(alignment: .leading, spacing: 8) {
            Text("Latest run: \(run.id)").font(.title2.weight(.semibold))
            ForEach(Array(top.enumerated()), id: \.element.ip) { pair in
                OverviewRow(index: pair.offset, server: pair.element)
            }
            Text("Fastest observed, single window — not a statistically established ranking.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(18)
        .frame(width: 560, height: 300, alignment: .topLeading)
        .background(Color(nsColor: .windowBackgroundColor))
        _ = try render(view, width: 560, height: 300, name: "overview-headline.png")
    }
}

struct OverviewRow: View {
    let index: Int
    let server: RankedServer

    var body: some View {
        HStack {
            Text("\(index + 1).").monospacedDigit()
                .foregroundStyle(.tertiary)
                .frame(width: 26, alignment: .trailing)
            Text(server.ip).monospaced()
            Text(server.category ?? "Other").font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
            Text("med \(Fmt.ms1(server.uMed))").monospacedDigit()
        }
    }
}
