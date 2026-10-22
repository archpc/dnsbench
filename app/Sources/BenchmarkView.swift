import SwiftUI

/// Benchmark configuration, live progress, and cancellation. Pure observer of
/// the app-scoped BenchmarkCoordinator: leaving this pane never affects a run.
struct BenchmarkView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        HSplitView {
            configPane
                .frame(minWidth: 300, maxWidth: 380)
            progressPane
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .navigationTitle("Benchmark")
    }

    private var coordinator: BenchmarkCoordinator { model.benchmark }

    private var configPane: some View {
        @Bindable var c = model.benchmark
        return Form {
            Section("Candidate pool") {
                Stepper(value: $c.config.n, in: 4...150, step: 1) {
                    LabeledContent("Finalists", value: String(c.config.n))
                }
                LabeledContent("Pool source") {
                    Text(poolSource).font(.caption).foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                Button {
                    coordinator.startDiscovery()
                } label: {
                    Label(model.pingInfo?.poolEligible == nil
                          ? "Build candidate pool…" : "Rebuild candidate pool…",
                          systemImage: "magnifyingglass.circle")
                }
                .disabled(!coordinator.canStartDiscovery)
                .help("Discovery screens ~60k public resolvers (15–20 minutes, " +
                      "resumes from checkpoints).")
            }
            .disabled(coordinator.isRunning)
            Section("Engine") {
                Picker("Engine", selection: $c.config.engine) {
                    Text("kqueue (interleaved)").tag("kqueue")
                    Text("thread (baseline)").tag("thread")
                }
                .pickerStyle(.radioGroup)
                Stepper(value: $c.config.workers, in: 1...128) {
                    LabeledContent("Workers (thread engine)", value: String(c.config.workers))
                }
                LabeledContent("Timeout") {
                    TextField("", value: $c.config.timeout, format: .number)
                        .frame(width: 64)
                        .multilineTextAlignment(.trailing)
                }
            }
            .disabled(coordinator.isRunning)
            Section("Statistical windows") {
                Stepper(value: $c.config.windows, in: 1...5) {
                    LabeledContent("Windows", value: String(c.config.windows))
                }
                Text(c.config.windows == 1
                     ? "Single window: fastest-observed only."
                     : "\(c.config.windows) independent windows produce rank bands (exploratory with <4 windows).")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .disabled(coordinator.isRunning)
            Section {
                if coordinator.isRunning {
                    Button(role: .destructive) {
                        Task { await coordinator.cancel() }
                    } label: {
                        Label("Cancel \(coordinator.activeJob?.rawValue ?? "job")",
                              systemImage: "stop.fill")
                    }
                    .keyboardShortcut(".", modifiers: .command)
                } else {
                    Button {
                        coordinator.startBenchmark()
                    } label: {
                        Label("Start benchmark", systemImage: "play.fill")
                    }
                    .keyboardShortcut("r", modifiers: .command)
                    .disabled(coordinator.startBlockedReason != nil)
                    if let reason = coordinator.startBlockedReason {
                        Text(reason)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                if let err = coordinator.lastError {
                    Text(err)
                        .font(.caption)
                        .foregroundStyle(.red)
                        .textSelection(.enabled)
                }
                if !coordinator.lastRunDirs.isEmpty {
                    Text("Output: \(coordinator.lastRunDirs.joined(separator: ", "))")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .textSelection(.enabled)
                    if !coordinator.isRunning {
                        Button("Show Rankings") { model.pane = .rankings }
                    }
                }
            }
        }
        .formStyle(.grouped)
        .padding(8)
    }

    private var poolSource: String {
        let path = DataPaths.pool(model.helper.dataDir)
        if let e = model.pingInfo?.poolEligible {
            return "\(path) — \(e) eligible"
        }
        return model.pingInfo == nil
            ? "\(path) — unknown (helper offline)"
            : "\(path) — missing"
    }

    private var progressPane: some View {
        VStack(alignment: .leading, spacing: 12) {
            let p = model.helper.progress
            if let job = coordinator.activeJob {
                Text("Running: \(job.rawValue)")
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
                if let t0 = coordinator.startedAt {
                    // ticks on its own; progress events stop during aggregation
                    TimelineView(.periodic(from: .now, by: 1)) { ctx in
                        LabeledContent("Elapsed",
                                       value: Duration.seconds(ctx.date.timeIntervalSince(t0))
                                           .formatted(.time(pattern: .minuteSecond)))
                            .monospacedDigit()
                    }
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
}
