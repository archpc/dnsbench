import SwiftUI

/// Live benchmark configuration, progress, and cancellation.
struct BenchmarkView: View {
    @Environment(AppModel.self) private var model

    @State private var n = 16
    @State private var workers = 60
    @State private var windows = 3
    @State private var engine = "kqueue"
    @State private var timeout = 2.0
    @State private var running = false
    @State private var lastError: String?
    @State private var lastRunDirs: [String] = []

    var body: some View {
        HSplitView {
            configPane
                .frame(minWidth: 300, maxWidth: 380)
            progressPane
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .navigationTitle("Benchmark")
    }

    private var configPane: some View {
        Form {
            Section("Candidate pool") {
                Stepper(value: $n, in: 4...150, step: 1) {
                    LabeledContent("Finalists", value: String(n))
                }
                LabeledContent("Pool source") {
                    Text(poolSource).font(.caption).foregroundStyle(.secondary)
                }
            }
            Section("Engine") {
                Picker("Engine", selection: $engine) {
                    Text("kqueue (interleaved)").tag("kqueue")
                    Text("thread (baseline)").tag("thread")
                }
                .pickerStyle(.radioGroup)
                Stepper(value: $workers, in: 1...128) {
                    LabeledContent("Workers (thread engine)", value: String(workers))
                }
                LabeledContent("Timeout") {
                    TextField("", value: $timeout, format: .number)
                        .frame(width: 64)
                        .multilineTextAlignment(.trailing)
                }
            }
            Section("Statistical windows") {
                Stepper(value: $windows, in: 1...5) {
                    LabeledContent("Windows", value: String(windows))
                }
                Text(windows == 1
                     ? "Single window: fastest-observed only."
                     : "\(windows) independent windows produce rank bands (exploratory with <4 windows).")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section {
                if running {
                    Button(role: .destructive) {
                        Task { await model.helper.cancel() }
                    } label: {
                        Label("Cancel benchmark", systemImage: "stop.fill")
                    }
                    .keyboardShortcut(".", modifiers: .command)
                } else {
                    Button {
                        Task { await startBenchmark() }
                    } label: {
                        Label("Start benchmark", systemImage: "play.fill")
                    }
                    .keyboardShortcut("r", modifiers: .command)
                    .disabled(model.pingInfo == nil)
                }
                if let lastError {
                    Text(lastError)
                        .font(.caption)
                        .foregroundStyle(.red)
                }
                if !lastRunDirs.isEmpty {
                    Text("Output: \(lastRunDirs.joined(separator: ", "))")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .textSelection(.enabled)
                }
            }
        }
        .formStyle(.grouped)
        .padding(8)
    }

    private var poolSource: String {
        if let e = model.pingInfo?.poolEligible {
            return "datasets/pool.json — \(e) eligible"
        }
        return "datasets/pool.json — unknown (helper offline)"
    }

    private var progressPane: some View {
        VStack(alignment: .leading, spacing: 12) {
            let p = model.helper.progress
            if running {
                Text(p.command.map { "Running: \($0)" } ?? "Working…")
                    .font(.title3.weight(.medium))
                if p.windowTotal > 0 {
                    LabeledContent("Window",
                                   value: "\(p.windowIndex)/\(p.windowTotal)")
                }
                if let phase = p.phase {
                    LabeledContent("Phase", value: phase)
                    if p.phaseTotal > 0 {
                        ProgressView(value: Double(p.phaseDone),
                                     total: Double(max(1, p.phaseTotal))) {
                            Text("\(p.phaseDone)/\(p.phaseTotal)")
                                .monospacedDigit()
                        }
                    }
                }
                LabeledContent("Queries recorded",
                               value: String(p.queryCount))
                    .monospacedDigit()
                if let t0 = p.startedAt {
                    LabeledContent("Elapsed",
                                   value: Duration.seconds(-t0.timeIntervalSinceNow)
                                       .formatted(.time(pattern: .minuteSecond)))
                }
                Spacer()
            } else {
                EmptyStateView(symbol: "stopwatch",
                               title: "No benchmark running",
                               detail: "Configure a run on the left and press Start. " +
                                       "Cancelled runs keep their partial data.")
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private func startBenchmark() async {
        running = true
        lastError = nil
        lastRunDirs = []
        defer { running = false }
        if !model.helper.isRunning { model.helper.start() }
        do {
            let outcome = try await model.helper.measure(
                n: n, workers: workers, windows: windows,
                engine: engine, timeout: timeout)
            if outcome.cancelled {
                lastRunDirs = outcome.runDirs
                lastError = "Cancelled — partial run data preserved."
            } else {
                lastRunDirs = outcome.runDirs
            }
            await model.store.scan()
            if let first = outcome.runDirs.first {
                // helper returns absolute run dirs; selection elsewhere uses run IDs
                model.selectedRunID = URL(fileURLWithPath: first).lastPathComponent
                model.pane = .rankings
            }
        } catch {
            lastError = error.localizedDescription
        }
    }
}
