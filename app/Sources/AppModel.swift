import Foundation
import SwiftUI

/// App-wide state. Single owner of the run store, helper controller, and
/// cross-view selection.
@MainActor
@Observable
final class AppModel {
    let store = RunStore()
    var helper: HelperController!

    enum Pane: String, CaseIterable, Identifiable {
        case overview, benchmark, rankings, explore, history
        var id: String { rawValue }

        var label: String {
            switch self {
            case .overview: "Overview"
            case .benchmark: "Benchmark"
            case .rankings: "Rankings"
            case .explore: "Explore"
            case .history: "History"
            }
        }

        var symbol: String {
            switch self {
            case .overview: "gauge"
            case .benchmark: "stopwatch"
            case .rankings: "list.number"
            case .explore: "magnifyingglass"
            case .history: "clock.arrow.circlepath"
            }
        }
    }

    var pane: Pane = .overview
    /// Run selected for analysis (rankings/explore sources).
    var selectedRunID: String?
    /// Resolver selected within a run (drives the inspector).
    var selectedIP: String?
    var pingInfo: PingInfo?

    static let dataDirKey = "dataDir"

    init() {
        let dir: URL
        if let raw = UserDefaults.standard.string(forKey: Self.dataDirKey),
           !raw.isEmpty {
            dir = URL(fileURLWithPath: (raw as NSString).expandingTildeInPath)
        } else {
            dir = URL(fileURLWithPath: NSString(string: "~/Library/Application Support/DNSBench").expandingTildeInPath)
        }
        helper = HelperController(dataDir: dir)
        store.setDataDir(dir)
        Task { await refreshHelperInfo() }
    }

    func setDataDir(_ url: URL) {
        UserDefaults.standard.set(url.path, forKey: Self.dataDirKey)
        helper = HelperController(dataDir: url)
        store.setDataDir(url)
        Task { await refreshHelperInfo() }
    }

    func refreshHelperInfo() async {
        if !helper.isRunning { helper.start() }
        pingInfo = try? await helper.ping()
    }

    var selectedRunSummary: RunSummary? {
        store.runs.first { $0.id == selectedRunID }
    }
}
