import SwiftUI

struct HistoryView: View {
    @Environment(AppModel.self) private var model
    @State private var searchText = ""

    private var filtered: [RunSummary] {
        guard !searchText.isEmpty else { return model.store.runs }
        return model.store.runs.filter {
            $0.id.localizedCaseInsensitiveContains(searchText)
        }
    }

    var body: some View {
        Group {
            if model.store.runs.isEmpty {
                EmptyStateView(
                    symbol: "clock.arrow.circlepath",
                    title: "No benchmark runs",
                    detail: model.store.scanError
                        ?? "Run a benchmark or choose a data directory in Settings.")
            } else {
                Table(filtered, selection: Binding(
                    get: { model.selectedRunID },
                    set: { model.selectedRunID = $0 })) {
                    TableColumn("Run") { run in
                        HStack(spacing: 6) {
                            Image(systemName: run.isWindow ? "square.stack.3d.up" : "doc.badge.clock")
                                .foregroundStyle(.secondary)
                            Text(run.id)
                        }
                    }
                    TableColumn("Date") { run in
                        Text(Fmt.dateTime(run.timestamp))
                    }
                    TableColumn("Ranked") { run in
                        Text(run.nRanked.map(String.init) ?? "—")
                    }
                    TableColumn("Engine") { run in
                        Text(run.engine ?? "—").monospaced()
                    }
                    TableColumn("State") { run in
                        if run.complete {
                            Label("Complete", systemImage: "checkmark.circle")
                                .foregroundStyle(.secondary)
                        } else if run.cancelled {
                            Label("Cancelled", systemImage: "xmark.octagon")
                                .foregroundStyle(.orange)
                        } else {
                            Label("Partial", systemImage: "exclamationmark.triangle")
                                .foregroundStyle(.orange)
                        }
                    }
                    TableColumn("Benchmark") { run in
                        HStack(spacing: 4) {
                            Text(run.isWindow
                                 ? "\(run.windowGroupID) · w\(run.windowIndex ?? 0)"
                                 : "—")
                                .monospaced()
                            if run.aggregateAvailable {
                                Image(systemName: "chart.bar.doc.horizontal")
                                    .foregroundStyle(.secondary)
                                    .help("Aggregate (rank bands) available")
                            }
                        }
                    }
                }
                .searchable(text: $searchText, prompt: "Filter runs")
                .navigationTitle("History")
                .overlay(alignment: .bottomTrailing) {
                    Text("\(model.store.runs.count) runs")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                        .padding(8)
                }
            }
        }
        .task { await model.store.scan() }
        .toolbar {
            Button {
                Task { await model.store.scan() }
            } label: {
                Label("Rescan", systemImage: "arrow.clockwise")
            }
            .keyboardShortcut("r", modifiers: .command)
        }
    }
}

struct EmptyStateView: View {
    let symbol: String
    let title: String
    let detail: String

    var body: some View {
        ContentUnavailableView {
            Label(title, systemImage: symbol)
        } description: {
            Text(detail)
        }
    }
}
