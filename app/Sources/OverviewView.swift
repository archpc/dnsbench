import SwiftUI

// MARK: - helpers

private func medOf(_ r: (name: String, med: [Double], n: Int)) -> Double {
    guard !r.med.isEmpty else { return .infinity }
    return r.med[r.med.count / 2]
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
        .task(id: model.store.runs.first(where: \.complete)?.id) {
            let id = model.selectedRunID
                ?? model.store.runs.first(where: \.complete)?.id
            guard let id else { latest = nil; return }
            latest = await model.store.loadDetail(id)
        }
    }

    @ViewBuilder
    private func headline(_ ranked: RankedFile) -> some View {
        let top = ranked.ranked
            .sorted { ($0.uMed ?? .infinity) < ($1.uMed ?? .infinity) }
            .prefix(5)
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Latest run: \(latest?.summary.id ?? "")")
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
            Text("Fastest observed, single window — not a statistically established ranking.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
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
