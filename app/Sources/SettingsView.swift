import SwiftUI

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var dataDirText = ""

    var body: some View {
        Form {
            Section("Data") {
                LabeledContent("Data directory") {
                    Text(dataDirText.isEmpty
                         ? model.helper.dataDir.path : dataDirText)
                        .font(.caption.monospaced())
                        .lineLimit(2)
                        .textSelection(.enabled)
                }
                HStack {
                    Button("Choose…") { chooseDataDir() }
                    Spacer()
                    Button("Reload runs") {
                        Task { await model.store.scan() }
                    }
                }
                Text("Run data in \(DataPaths.runs(model.helper.dataDir)) is read-only " +
                     "for this app; measurement writes go through the helper into " +
                     "the same location.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section("Diagnostics") {
                Button("Restart helper") {
                    Task { await model.restartHelper() }
                }
                if let ping = model.pingInfo {
                    LabeledContent("Helper", value: "v\(ping.helperVersion), protocol v\(ping.protocolVersion)")
                    LabeledContent("Python", value: ping.pythonVersion)
                    LabeledContent("Data dir", value: ping.dataDir)
                }
            }
        }
        .formStyle(.grouped)
        .onAppear { dataDirText = model.helper.dataDir.path }
    }

    private func chooseDataDir() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.message = "Choose the DNSBench data directory (contains pool.json and runs/)."
        if panel.runModal() == .OK, let url = panel.url {
            Task { await model.setDataDir(url) }
            dataDirText = url.path
        }
    }
}
