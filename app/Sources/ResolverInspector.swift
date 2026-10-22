import SwiftUI
import Charts

/// Native detail inspector for one resolver within the selected run.
/// Shows per-server stats, sample distributions (kernel-adjusted and raw),
/// and the provider identity caveat where applicable.
struct ResolverInspector: View {
    @Environment(AppModel.self) private var model
    let ip: String

    @State private var samples: ServerSamples?
    @State private var queries: [QueryRecord] = []
    @State private var loaded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            if let s = samples {
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        ResolverStatsPanel(s: s)
                        if !queries.isEmpty {
                            querySection
                        }
                        provenance
                    }
                    .padding(14)
                }
            } else if loaded {
                // partial/cancelled run, or resolver absent from this run
                EmptyStateView(symbol: "questionmark.folder",
                               title: "No samples",
                               detail: "\(ip) has no per-server samples in " +
                                       "\(model.selectedRunID ?? "the selected run").")
            } else {
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .task(id: "\(model.selectedRunKey)#\(ip)") {
            samples = nil
            queries = []
            loaded = false
            defer { loaded = true }
            guard let runID = model.selectedRunID else { return }
            async let s = model.store.loadSamples(runID)
            let q = await model.store.loadQueries(runID, limit: 60_000)
            let all = await s
            samples = all.first { $0.ip == ip }
            queries = q.filter { $0.ip == ip }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(ip)
                .font(.title3.monospaced().weight(.semibold))
                .textSelection(.enabled)
            if let ptr = rankedEntry?.ptr, ptr != "?" {
                Text(ptr).font(.caption.monospaced()).foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            if let s = samples {
                if let engine = s.engine {
                    Text("engine: \(engine)").font(.caption).foregroundStyle(.secondary)
                }
                Text(identityNote)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(12)
    }

    private var rankedEntry: RankedServer? {
        model.store.cachedDetail(model.selectedRunID)?.rankedFile?.ranked
            .first { $0.ip == ip }
    }

    /// Honest provider-identity label (PTR-derived = provisional).
    private var identityNote: String {
        "Provider identity is PTR-derived and provisional."
    }

    private var querySection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Recent transactions")
                .font(.headline)
            let rows = queries.suffix(200)
            let timeouts = queries.filter(\.isTimeout).count
            Text("\(queries.count) recorded · \(timeouts) timed out · showing last \(min(200, rows.count))")
                .font(.caption)
                .foregroundStyle(.secondary)
            Table(Array(rows.reversed())) {
                TableColumn("Kind") { r in Text(r.kind).font(.caption) }
                TableColumn("ms") { r in
                    Text(Fmt.ms1(r.ms)).monospacedDigit()
                }
                TableColumn("rcode") { r in
                    let color: Color = r.isTimeout ? .orange : .primary
                    Text(r.isTimeout ? "timeout" : String(r.rcode))
                        .foregroundStyle(color)
                        .font(.caption)
                }
                TableColumn("answers") { r in Text(String(r.an)).monospacedDigit() }
                TableColumn("kern. delay") { r in
                    Text(Fmt.ms1(r.kd)).monospacedDigit()
                }
            }
            .frame(height: 220)
        }
    }

    private var provenance: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Provenance")
                .font(.headline)
            LabeledContent("Run", value: model.selectedRunID ?? "—")
            LabeledContent("Source",
                           value: "deepbench-results.jsonl / deepbench-perquery.jsonl")
            LabeledContent("Engine", value: samples?.engine ?? "—")
        }
        .font(.caption)
        .foregroundStyle(.secondary)
    }
}

// MARK: - statistics helpers (display only; ranking math stays in Python)

/// Stats grid + distribution charts for one server's samples. Standalone so
/// it can be snapshot-rendered in tests without the app environment.
struct ResolverStatsPanel: View {
    let s: ServerSamples

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            statsGrid
            distributionSection
        }
    }

    private var statsGrid: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible()),
                            GridItem(.flexible())], spacing: 10) {
            statCell("Uncached med.", Fmt.ms1(median(s.uncached)))
            statCell("Cached med.", Fmt.ms1(median(s.cached)))
            statCell("p90", Fmt.ms1(percentile(s.uncached, 0.9)))
            statCell("p99", Fmt.ms1(percentile(s.uncached, 0.99)))
            statCell("Samples", "u \(s.uncached.count) / c \(s.cached.count)")
            statCell("Timeouts", "\(s.toU + s.toC)")
            statCell("Invalid cached", String(s.badC))
            statCell("Uncached failures", String(s.failU ?? 0))
        }
    }

    private func statCell(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.callout.monospacedDigit())
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(8)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 6))
    }

    @ViewBuilder
    private var distributionSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Latency distribution (ms)")
                .font(.headline)
            Chart {
                ForEach(bins(s.uncached, count: 24), id: \.0) { bin in
                    BarMark(
                        x: .value("ms", bin.mid),
                        y: .value("count", bin.count),
                        width: .fixed(9)
                    )
                    .foregroundStyle(Color.accentColor)
                }
                if let uMed = median(s.uncached) {
                    RuleMark(x: .value("median", uMed))
                        .foregroundStyle(.primary)
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
                        .annotation(position: .top) {
                            Text("med \(Fmt.ms1(uMed))")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                }
            }
            .chartXAxisLabel("uncached ms")
            .frame(height: 130)
            if !s.cached.isEmpty {
                Chart {
                    ForEach(bins(s.cached, count: 24), id: \.0) { bin in
                        BarMark(
                            x: .value("ms", bin.mid),
                            y: .value("count", bin.count),
                            width: .fixed(9)
                        )
                        .foregroundStyle(.teal)
                    }
                }
                .chartXAxisLabel("cached ms")
                .frame(height: 110)
            }
            if let kd = s.kdeltas, !kd.isEmpty {
                Text("Kernel→user receive delay: median \(Fmt.ms1(median(kd))), p95 \(Fmt.ms1(percentile(kd, 0.95)))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

func median(_ xs: [Double]) -> Double? {
    guard !xs.isEmpty else { return nil }
    let s = xs.sorted()
    let n = s.count
    return n % 2 == 1 ? s[n / 2] : (s[n / 2 - 1] + s[n / 2]) / 2
}

func percentile(_ xs: [Double], _ p: Double) -> Double? {
    guard !xs.isEmpty else { return nil }
    let s = xs.sorted()
    let scaled = Double(s.count - 1) * p
    let idx = Int(scaled.rounded(.down))
    let clamped = max(0, min(s.count - 1, idx))
    return s[clamped]
}

typealias Bin = (mid: Double, count: Int)

func bins(_ xs: [Double], count: Int) -> [Bin] {
    guard !xs.isEmpty else { return [] }
    let lo = xs.min() ?? 0
    let hi = xs.max() ?? 0
    guard hi > lo else { return [(mid: lo, count: xs.count)] }
    var out = [Bin](repeating: (0, 0), count: count)
    let width = (hi - lo) / Double(count)
    for x in xs {
        let k = min(count - 1, Int((x - lo) / width))
        out[k].count += 1
        out[k].mid = lo + (Double(k) + 0.5) * width
    }
    return out
}
