import SwiftUI

/// Go menu with Cmd-N pane shortcuts (direct sidebar navigation).
struct PaneCommands: Commands {
    let model: AppModel

    var body: some Commands {
        CommandMenu("Go") {
            Button("Overview") { model.pane = .overview }
                .keyboardShortcut("1", modifiers: .command)
            Button("Benchmark") { model.pane = .benchmark }
                .keyboardShortcut("2", modifiers: .command)
            Button("Rankings") { model.pane = .rankings }
                .keyboardShortcut("3", modifiers: .command)
            Button("Explore") { model.pane = .explore }
                .keyboardShortcut("4", modifiers: .command)
            Button("History") { model.pane = .history }
                .keyboardShortcut("5", modifiers: .command)
            Divider()
            Button("Next Run") {
                if let id = model.selectedRunID,
                   let idx = model.store.runs.firstIndex(where: { $0.id == id }),
                   idx > 0 {
                    model.selectedRunID = model.store.runs[idx - 1].id
                }
            }
            .keyboardShortcut("]", modifiers: .command)
            .disabled(model.store.runs.isEmpty)
            Button("Previous Run") {
                if let id = model.selectedRunID,
                   let idx = model.store.runs.firstIndex(where: { $0.id == id }),
                   idx + 1 < model.store.runs.count {
                    model.selectedRunID = model.store.runs[idx + 1].id
                }
            }
            .keyboardShortcut("[", modifiers: .command)
            .disabled(model.store.runs.isEmpty)
        }
    }
}
