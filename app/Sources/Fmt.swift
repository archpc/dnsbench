import Foundation

/// Display formatting helpers. Pure functions (no shared formatter state,
/// strict concurrency safe).
enum Fmt {
    static func ms1(_ v: Double?) -> String {
        guard let v else { return "—" }
        return String(format: "%.1f ms", v)
    }

    static func pct(_ v: Double?) -> String {
        guard let v else { return "—" }
        return String(format: "%.1f%%", v * 100)
    }

    static func int(_ v: Int?) -> String { "\(v ?? 0)" }

    static func dateTime(_ d: Date) -> String {
        d.formatted(date: .abbreviated, time: .shortened)
    }
}
