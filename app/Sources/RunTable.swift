import SwiftUI

/// The rankings Table, isolated in its own struct (10-column SwiftUI Tables
/// otherwise blow the type-checker budget inside the parent view).
struct RunTable: View {
    let rows: [RankedServer]
    let ranked: RankedFile
    /// ip -> aggregate rank band (multi-window benchmarks only).
    var bands: [String: Int] = [:]
    @Binding var selection: String?
    @Binding var sortOrder: [KeyPathComparator<RankedServer>]

    /// Rank by uncached median over the whole run (not the filtered rows),
    /// computed once per render instead of re-sorting for every row.
    static func rankPositions(_ servers: [RankedServer]) -> [String: Int] {
        var out: [String: Int] = [:]
        for (i, s) in servers.sorted(by: { $0.sortU < $1.sortU }).enumerated() {
            out[s.ip] = i + 1
        }
        return out
    }

    var body: some View {
        let ranks = Self.rankPositions(ranked.ranked)
        Table(rows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("#") { (row: RankedServer) in
                rankCell(rank: ranks[row.ip], band: bands[row.ip])
            }
            .width(bands.isEmpty ? 28 : 56)
            TableColumn("Resolver", value: \.ip) { row in
                resolverCell(row)
            }
            TableColumn("Uncached med.", value: \.sortU) { row in
                Text(Fmt.ms1(row.uMed)).monospacedDigit()
            }
            TableColumn("Cached med.", value: \.sortC) { row in
                Text(Fmt.ms1(row.cMed)).monospacedDigit()
            }
            TableColumn("p90", value: \.sortP90) { row in
                Text(Fmt.ms1(row.uP90)).monospacedDigit()
            }
            TableColumn("Max", value: \.sortMax) { row in
                Text(Fmt.ms1(row.uMax)).monospacedDigit()
            }
            TableColumn("Kern. delay", value: \.sortKd) { row in
                Text(Fmt.ms1(row.kdMed)).monospacedDigit()
            }
            TableColumn("Timeo.", value: \.totalTimeouts) { row in
                timeoutCell(row)
            }
            TableColumn("Invalid", value: \.totalInvalid) { row in
                invalidCell(row)
            }
            TableColumn("Provider", value: \.sortCat) { row in
                Text(row.category ?? "Other")
            }
        }
    }

    /// Window rank, plus the aggregate rank band when the benchmark has one
    /// (servers sharing a band cannot be ordered from this data).
    private func rankCell(rank: Int?, band: Int?) -> some View {
        HStack(spacing: 4) {
            Text(rank.map(String.init) ?? "—")
                .foregroundStyle(.tertiary)
            if let band {
                Text("b\(band)")
                    .font(.caption2.weight(band == 1 ? .semibold : .regular))
                    .foregroundStyle(band == 1 ? .primary : .secondary)
                    .help("Aggregate rank band \(band): 95% CIs overlap within a band, so their order is not statistically resolved")
            }
        }
        .monospacedDigit()
    }

    private func resolverCell(_ row: RankedServer) -> some View {
        let unreach = row.unreach ?? 0
        return HStack(spacing: 6) {
            Text(row.ip).monospaced()
            if unreach > 0 {
                Image(systemName: "wifi.slash")
                    .foregroundStyle(.orange)
                    .help("\(unreach) unreachable probes")
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(row.category ?? "Unknown provider"), \(row.ip), " +
            "uncached median \(row.uMed.map { String(format: "%.1f", $0) } ?? "none") milliseconds")
    }

    private func timeoutCell(_ row: RankedServer) -> some View {
        let color: Color = row.totalTimeouts > 0 ? .orange : .secondary
        return Text("\(row.totalTimeouts)")
            .monospacedDigit()
            .foregroundStyle(color)
    }

    private func invalidCell(_ row: RankedServer) -> some View {
        let color: Color = row.totalInvalid > 0 ? .orange : .secondary
        return Text(String(row.totalInvalid))
            .monospacedDigit()
            .foregroundStyle(color)
            .help("\(row.failU ?? 0) uncached SERVFAIL/REFUSED/other, " +
                  "\(row.badC) invalid cached answers")
    }
}
