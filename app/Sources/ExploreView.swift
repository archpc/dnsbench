import SwiftUI

/// Per-transaction explorer: full query log for the selected run with
/// filtering by kind and outcome.
struct ExploreView: View {
    @Environment(AppModel.self) private var model
    @State private var queries: [QueryRecord] = []
    @State private var loading = false
    @State private var kindFilter = "all"
    @State private var outcomeFilter = "all"
    @State private var ipFilter = ""

    private var kinds: [String] {
        Array(Set(queries.map(\.kind))).sorted()
    }

    private var filtered: [QueryRecord] {
        queries.filter { q in
            (kindFilter == "all" || q.kind == kindFilter)
            && (outcomeFilter == "all"
                || (outcomeFilter == "timeout" ? q.isTimeout : q.isValidAnswer))
            && (ipFilter.isEmpty || q.ip.localizedCaseInsensitiveContains(ipFilter))
        }
    }

    var body: some View {
        Group {
            if let runID = model.selectedRunID {
                VStack(spacing: 0) {
                    filterBar
                    Divider()
                    if loading {
                        ProgressView("Loading transactions for \(runID)…")
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else {
                        Table(filtered) {
                            TableColumn("Resolver") { q in
                                Text(q.ip).monospaced()
                            }
                            TableColumn("Kind") { q in Text(q.kind) }
                            TableColumn("Query") { q in
                                Text(q.name ?? "—").font(.caption)
                            }
                            TableColumn("ms") { q in
                                Text(Fmt.ms1(q.ms)).monospacedDigit()
                            }
                            TableColumn("Outcome") { q in
                                if q.isTimeout {
                                    Label("timeout", systemImage: "hourglass")
                                        .foregroundStyle(.orange)
                                } else if q.isValidAnswer {
                                    Label("ok (\(q.an) answers)",
                                          systemImage: "checkmark.circle")
                                        .foregroundStyle(.secondary)
                                } else {
                                    Label("rcode \(q.rcode)",
                                          systemImage: "xmark.circle")
                                        .foregroundStyle(.red)
                                }
                            }
                            TableColumn("Kern. delay") { q in
                                Text(Fmt.ms1(q.kd)).monospacedDigit()
                            }
                        }
                        .overlay(alignment: .bottomTrailing) {
                            Text("\(filtered.count) of \(queries.count) transactions")
                                .font(.caption)
                                .foregroundStyle(.tertiary)
                                .padding(8)
                        }
                    }
                }
                .navigationTitle("Explore — \(runID)")
                .task(id: runID) {
                    loading = true
                    queries = await model.store.loadQueries(runID)
                    loading = false
                }
            } else {
                EmptyStateView(symbol: "magnifyingglass",
                               title: "No run selected",
                               detail: "Pick a run in History to inspect individual DNS transactions.")
            }
        }
    }

    private var filterBar: some View {
        HStack(spacing: 12) {
            TextField("Filter by IP", text: $ipFilter)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 220)
            Picker("Kind", selection: $kindFilter) {
                Text("All kinds").tag("all")
                ForEach(kinds, id: \.self) { k in Text(k).tag(k) }
            }
            .frame(width: 150)
            Picker("Outcome", selection: $outcomeFilter) {
                Text("All").tag("all")
                Text("Valid answers").tag("valid")
                Text("Timeouts").tag("timeout")
            }
            .frame(width: 160)
            Spacer()
        }
        .padding(10)
        .background(.bar)
    }
}
