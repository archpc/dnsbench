import SwiftUI

@main
struct DNSBenchApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(model)
                .frame(minWidth: 980, minHeight: 600)
        }
        .windowToolbarStyle(.unified)
        .defaultSize(width: 1180, height: 760)

        Settings {
            SettingsView()
                .environment(model)
                .frame(width: 460)
        }
    }
}
