import SwiftUI

/// The rankings Table, isolated in its own struct (10-column SwiftUI Tables
/// otherwise blow the type-checker budget inside the parent view).
struct RunTable: View {
    let rows: [RankedServer]
    let ranked: RankedFile
    @Binding var selection: String?
    @Binding var sortOrder: [KeyPathComparator<RankedServer>]

    var body: some View {
        Table(rows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("#") { row in
                Text(rankPosition(row))
                    .foregroundStyle(.tertiary)
                    .monospacedDigit()
            }
            .width(28)
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
            TableColumn("Invalid", value: \.badC) { row in
                invalidCell(row)
            }
            TableColumn("Provider", value: \.sortCat) { row in
                Text(row.category ?? "Other")
            }
        }
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
    }

    private func timeoutCell(_ row: RankedServer) -> some View {
        let color: Color = row.totalTimeouts > 0 ? .orange : .secondary
        return Text("\(row.totalTimeouts)")
            .monospacedDigit()
            .foregroundStyle(color)
    }

    private func invalidCell(_ row: RankedServer) -> some View {
        let color: Color = row.badC > 0 ? .orange : .secondary
        return Text(String(row.badC))
            .monospacedDigit()
            .foregroundStyle(color)
    }

    private func rankPosition(_ row: RankedServer) -> String {
        let sorted = ranked.ranked
            .sorted { $0.sortU < $1.sortU }
        guard let idx = sorted.firstIndex(where: { $0.ip == row.ip }) else {
            return "—"
        }
        return "\(idx + 1)"
    }
}
