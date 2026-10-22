import SwiftUI
import Charts

/// Sortable, filterable native rankings for the selected run.
@MainActor
struct RankingsView: View {
    @Environment(AppModel.self) private var model
    @State private var detail: RunDetail?
    @State private var loadFailed = false
    @State private var searchText = ""
    @State private var providerFilter: String?
    @State private var sortOrder = [KeyPathComparator(\RankedServer.sortU,
                                                     order: .forward)]

    /// Seeded from the store cache so pane revisit renders the table on the
    /// first frame instead of flashing the loading state.
    init(model: AppModel) {
        _detail = State(initialValue: model.store.cachedDetail(model.selectedRunID))
    }

    private var providers: [String] {
        guard let ranked = detail?.rankedFile?.ranked else { return [] }
        return Array(Set(ranked.compactMap { $0.category })).sorted()
    }

    private var rows: [RankedServer] {
        guard let ranked = detail?.rankedFile?.ranked else { return [] }
        var out = ranked
        if !searchText.isEmpty {
            out = out.filter {
                $0.ip.localizedCaseInsensitiveContains(searchText)
                    || ($0.ptr?.localizedCaseInsensitiveContains(searchText) ?? false)
                    || ($0.category?.localizedCaseInsensitiveContains(searchText) ?? false)
            }
        }
        if let providerFilter {
            out = out.filter { $0.category == providerFilter }
        }
        return out.sorted(using: sortOrder)
    }

    var body: some View {
        Group {
            if let detail, let ranked = detail.rankedFile {
                rankedTable(ranked)
            } else if let id = model.selectedRunID, loadFailed {
                EmptyStateView(symbol: "exclamationmark.triangle",
                               title: "Couldn't load run",
                               detail: "No readable ranked.json in \(id).")
            } else if model.selectedRunID != nil {
                RunTableSkeleton()
            } else {
                EmptyStateView(symbol: "list.number",
                               title: "No run selected",
                               detail: "Pick a run in History to see its rankings.")
            }
        }
        .task(id: model.selectedRunKey) {
            guard let id = model.selectedRunID else { detail = nil; return }
            loadFailed = false
            detail = nil
            detail = await model.store.loadDetail(id)
            if detail == nil || detail?.rankedFile == nil { loadFailed = true }
        }
        .navigationTitle("Rankings")
    }

    @ViewBuilder
    private func rankedTable(_ ranked: RankedFile) -> some View {
        VStack(spacing: 0) {
            summaryBar(ranked)
            RunTable(rows: rows, ranked: ranked,
                     bands: Dictionary((detail?.aggregate?.servers ?? [])
                        .map { ($0.ip, $0.rankBand) }, uniquingKeysWith: { a, _ in a }),
                     selection: Binding(
                        get: { model.selectedIP },
                        set: { model.selectedIP = $0 }),
                     sortOrder: $sortOrder)
        }
        .searchable(text: $searchText, prompt: "Filter by IP, PTR, provider")
        .toolbar {
            Picker("Provider", selection: $providerFilter) {
                Text("All providers").tag(String?.none)
                ForEach(providers, id: \.self) { p in
                    Text(p).tag(String?.some(p))
                }
            }
        }
    }

    @ViewBuilder
    private func summaryBar(_ ranked: RankedFile) -> some View {
        HStack(spacing: 16) {
            if let run = detail?.summary {
                LabeledContent {
                    Text(run.id).monospaced().font(.callout)
                } label: {
                    Text("Run").foregroundStyle(.secondary)
                }
                LabeledContent {
                    Text("\(ranked.nRanked ?? ranked.ranked.count)")
                        .monospacedDigit()
                } label: {
                    Text("Ranked").foregroundStyle(.secondary)
                }
                if let incomplete = ranked.incompleteCached, !incomplete.isEmpty {
                    LabeledContent {
                        Text("\(incomplete.count)")
                            .foregroundStyle(.orange)
                    } label: {
                        Text("Incomplete cached").foregroundStyle(.secondary)
                    }
                    .help(incomplete.joined(separator: ", "))
                }
            }
            Spacer()
            if let agg = detail?.aggregate {
                Label("Benchmark \(detail?.summary.windowGroupID ?? "?") · " +
                      "\(agg.windows.count) windows · band 1: \(agg.rankBand1.count) unresolved",
                      systemImage: "chart.bar.doc.horizontal")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .help(agg.notes?["exploratory"]
                          ?? "Rank bands merge servers whose 95% CIs overlap: their order is not statistically resolved.")
                if agg.notes?["exploratory"] != nil {
                    Text("Exploratory")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.orange)
                        .help(agg.notes?["exploratory"] ?? "")
                }
            }
        }
        .font(.callout)
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(.bar)
    }
}

/// Table-shaped placeholder shown on first load of a run (no cached detail).
/// Mimics the rankings table silhouette instead of a bare centered spinner.
@MainActor
private struct RunTableSkeleton: View {
    @State private var pulse = false

    private let columnWidths: [(CGFloat, Alignment)] = [
        (140, .leading),    // Resolver
        (44, .trailing),    // Uncached
        (44, .trailing),    // Cached
        (36, .trailing),    // p90
        (36, .trailing),    // Max
        (36, .trailing),    // Kern delay
        (28, .trailing),    // Timeo.
        (28, .trailing)     // Invalid
    ]

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 16) {
                bar(width: 150, height: 12)
                bar(width: 30, height: 12)
                Spacer()
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            Divider()
            VStack(spacing: 0) {
                ForEach(0..<12, id: \.self) { i in
                    HStack(spacing: 16) {
                        capsule(width: 16, height: 10)
                        ForEach(columnWidths.indices, id: \.self) { c in
                            let w = columnWidths[c]
                            bar(width: w.0 * 0.8, height: 10)
                                .frame(width: w.0, alignment: w.1)
                                .frame(maxWidth: c == 0 ? .infinity : nil,
                                       alignment: w.1)
                        }
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 7)
                    Divider()
                }
            }
            Spacer()
        }
        .opacity(pulse ? 0.5 : 1)
        .animation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true),
                   value: pulse)
        .onAppear { pulse = true }
        .accessibilityHidden(true)
    }

    private func bar(width: CGFloat, height: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: 3)
            .fill(.quaternary)
            .frame(width: width, height: height)
    }

    private func capsule(width: CGFloat, height: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: height / 2)
            .fill(.quaternary)
            .frame(width: width, height: height)
    }
}
