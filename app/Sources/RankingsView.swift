import SwiftUI
import Charts

/// Sortable, filterable native rankings for the selected run.
struct RankingsView: View {
    @Environment(AppModel.self) private var model
    @State private var detail: RunDetail?
    @State private var searchText = ""
    @State private var providerFilter: String?
    @State private var sortOrder = [KeyPathComparator(\RankedServer.sortU,
                                                     order: .forward)]

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
            } else if let id = model.selectedRunID {
                ProgressView("Loading run \(id)…")
                    .padding(40)
            } else {
                EmptyStateView(symbol: "list.number",
                               title: "No run selected",
                               detail: "Pick a run in History to see its rankings.")
            }
        }
        .task(id: model.selectedRunID) {
            guard let id = model.selectedRunID else { detail = nil; return }
            detail = nil
            detail = await model.store.loadDetail(id)
        }
        .navigationTitle("Rankings")
    }

    @ViewBuilder
    private func rankedTable(_ ranked: RankedFile) -> some View {
        VStack(spacing: 0) {
            summaryBar(ranked)
            RunTable(rows: rows, ranked: ranked,
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
            if detail?.aggregate != nil {
                Label("Aggregate available", systemImage: "chart.bar.doc.horizontal")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .font(.callout)
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(.bar)
    }
}
