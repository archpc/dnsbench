import SwiftUI

// MARK: - helpers

private func medOf(_ r: (name: String, med: [Double], n: Int)) -> Double {
    median(r.med) ?? .infinity
}

struct OverviewView: View {
    @Environment(AppModel.self) private var model
    @State private var latest: RunDetail?

    var body: some View {
        Group {
            if let latest, let ranked = latest.rankedFile {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        headline(ranked)
                        if let agg = latest.aggregate {
                            aggregateSection(agg)
                        }
                        providerSection(ranked)
                        caveatSection
                    }
                    .padding(18)
                }
            } else {
                EmptyStateView(symbol: "gauge",
                               title: "No benchmark data",
                               detail: "Choose a data directory in Settings, then run a benchmark.")
            }
        }
        .navigationTitle("Overview")
        .task(id: model.store.runs.first {
            $0.id == Self.runID(selected: model.selectedRunID, runs: model.store.runs)
        }) {
            guard let id = Self.runID(selected: model.selectedRunID,
                                      runs: model.store.runs)
            else { latest = nil; return }
            latest = await model.store.loadDetail(id)
        }
    }

    /// The run the Overview describes: the selection, else the newest complete.
    static func runID(selected: String?, runs: [RunSummary]) -> String? {
        selected ?? runs.first(where: \.complete)?.id
    }

    static func title(runID: String, runs: [RunSummary]) -> String {
        runs.first(where: \.complete)?.id == runID ? "Latest run: \(runID)" : "Run \(runID)"
    }

    @ViewBuilder
    private func headline(_ ranked: RankedFile) -> some View {
        let top = ranked.ranked
            .sorted { ($0.uMed ?? .infinity) < ($1.uMed ?? .infinity) }
            .prefix(5)
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(Self.title(runID: latest?.summary.id ?? "", runs: model.store.runs))
                    .font(.title2.weight(.semibold))
                Spacer()
                Text("\(ranked.nRanked ?? ranked.ranked.count) resolvers")
                    .foregroundStyle(.secondary)
            }
            ForEach(Array(top.enumerated()), id: \.element.ip) { i, s in
                HStack {
                    Text("\(i + 1).")
                        .monospacedDigit()
                        .foregroundStyle(.tertiary)
                        .frame(width: 26, alignment: .trailing)
                    Text(s.ip).monospaced()
                    Text(s.category ?? "Other")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Text("med \(Fmt.ms1(s.uMed))")
                        .monospacedDigit()
                }
                .contentShape(Rectangle())
                .onTapGesture { model.selectedIP = s.ip }
            }
            Text("Fastest observed in this window — not a statistically established ranking.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func aggregateSection(_ agg: AggregateRecord) -> some View {
        let band1 = agg.servers.filter { $0.rankBand == 1 }
        VStack(alignment: .leading, spacing: 8) {
            Text("Fastest band: not statistically resolved (\(agg.windows.count) windows)")
                .font(.headline)
            Text("95% CIs overlap within the band, so these resolvers cannot be " +
                 "ordered from this data. Overlap does not prove they are equivalent.")
                .font(.caption)
                .foregroundStyle(.secondary)
            ForEach(band1.prefix(10)) { s in
                HStack {
                    Text(s.ip).monospaced()
                    Text(s.provider).font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    if let ci = s.ci95, ci.count == 2 {
                        Text("95% CI \(Fmt.ms1(ci[0]))–\(Fmt.ms1(ci[1]))")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Text("med \(Fmt.ms1(s.uMed))").monospacedDigit()
                }
                .contentShape(Rectangle())
                .onTapGesture { model.selectedIP = s.ip }
            }
            if let note = agg.notes?["exploratory"] {
                Label(note, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        }
        .padding(12)
        .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
    }

    @ViewBuilder
    private func providerSection(_ ranked: RankedFile) -> some View {
        let groups = Dictionary(grouping: ranked.ranked) { $0.category ?? "Other" }
        let rows = groups.map { (name: $0.key,
                                 med: $0.value.compactMap(\.uMed).sorted(),
                                 n: $0.value.count) }
            .filter { !$0.med.isEmpty }
            .sorted { medOf($0) < medOf($1) }
        VStack(alignment: .leading, spacing: 8) {
            Text("Providers (PTR-derived, provisional)")
                .font(.headline)
            ForEach(rows.prefix(8), id: \.name) { r in
                HStack {
                    Text(r.name)
                    Spacer()
                    Text("n=\(r.n)")
                        .foregroundStyle(.tertiary)
                        .monospacedDigit()
                    Text("med \(Fmt.ms1(medOf(r)))")
                        .monospacedDigit()
                }
                .font(.callout)
            }
        }
        .padding(12)
        .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
    }

    private var caveatSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Reading these numbers")
                .font(.headline)
            Label("Single-window medians are observations, not guarantees; " +
                  "run 3 windows for rank bands.",
                  systemImage: "info.circle")
            Label("Provider identity comes from PTR records and may be absent " +
                  "or misleading.",
                  systemImage: "person.crop.square.badge.questionmark")
            Label("Back-to-back windows measure short-timescale variation only.",
                  systemImage: "clock.badge.exclamationmark")
        }
        .font(.caption)
        .foregroundStyle(.secondary)
        .padding(12)
        .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
    }
}
