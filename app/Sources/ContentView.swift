import SwiftUI

struct ContentView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        NavigationSplitView {
            List(selection: Binding(
                get: { model.pane },
                set: { model.pane = $0 ?? .overview })) {
                Section {
                    ForEach(AppModel.Pane.allCases) { pane in
                        Label(pane.label, systemImage: pane.symbol)
                            .tag(pane)
                    }
                }
                Section("Status") {
                    HelperStatusView()
                }
            }
            .navigationSplitViewColumnWidth(min: 180, ideal: 210)
            .navigationTitle("DNSBench")
        } detail: {
            switch model.pane {
            case .overview:
                OverviewView()
            case .benchmark:
                BenchmarkView()
            case .rankings:
                RankingsView()
            case .explore:
                ExploreView()
            case .history:
                HistoryView()
            }
        }
        .inspector(isPresented: .constant(model.selectedIP != nil)) {
            if let ip = model.selectedIP {
                ResolverInspector(ip: ip)
                    .inspectorColumnWidth(min: 320, ideal: 360)
            }
        }
        .onChange(of: model.store.runs) { _, runs in
            // auto-select the newest complete run once the first scan lands
            if model.selectedRunID == nil, let first = runs.first(where: \.complete) {
                model.selectedRunID = first.id
            }
        }
    }
}

struct HelperStatusView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Circle()
                    .fill(statusColor)
                    .frame(width: 7, height: 7)
                Text(statusText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let ping = model.pingInfo {
                Text("Python \(ping.pythonVersion)")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                if let n = ping.poolEligible {
                    Text("Pool: \(n) eligible")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
        }
        .padding(.vertical, 2)
        .help("Measurement helper process status")
    }

    private var statusColor: Color {
        switch model.helper.state {
        case .ready: .green
        case .busy: .orange
        case .idle, .starting: .yellow
        case .stopping: .orange
        case .exited: .red
        }
    }

    private var statusText: String {
        switch model.helper.state {
        case .idle: "Idle"
        case .starting: "Starting helper…"
        case .ready: "Helper ready"
        case .busy: "Measuring…"
        case .stopping: "Stopping…"
        case .exited(let s): "Helper exited (\(s))"
        }
    }
}
