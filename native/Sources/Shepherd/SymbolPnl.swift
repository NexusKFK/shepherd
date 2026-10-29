import SwiftUI

/// 一个票今年的盈亏：已落袋（IBKR 平仓成交加 ToS 账本平仓，来自 state 的 realized）加现仓浮盈（开仓价对快照价）。
struct SymbolPnl: Identifiable {
    let sym: String
    let realized: Double
    let unrealized: Double?      // nil：现在没有持仓
    let held: Bool
    let count: Int
    let events: [J]              // 最近的平仓，新的在前
    var id: String { sym }
    var total: Double { realized + (unrealized ?? 0) }
    var unrealizedSort: Double { unrealized ?? -.greatestFiniteMagnitude }
    var lastEvent: String {
        guard let e = events.first else { return "—" }
        return shortDay(e["date"].s) + " " + e["text"].s + " " + signedUSD(e["realized"].n)
    }
}

enum SymbolPnlBook {
    /// 持仓里的组合汇总行（display_only）只是各腿的重复展示，不计入浮盈。
    static func rows(positions: [J], realized: J) -> [SymbolPnl] {
        var floating: [String: Double] = [:]
        var held = Set<String>()
        for p in positions where !p["display_only"].b {
            held.insert(p["sym"].s)
            if let u = unrealized(p) { floating[p["sym"].s, default: 0] += u }
        }
        let by = realized["by_sym"].fields
        var out = by.map { sym, rec in
            SymbolPnl(sym: sym, realized: rec["realized"].n ?? 0, unrealized: held.contains(sym) ? floating[sym] ?? 0 : nil,
                      held: held.contains(sym), count: Int(rec["count"].n ?? 0), events: rec["events"].items)
        }
        out += held.filter { by[$0] == nil }.map {
            SymbolPnl(sym: $0, realized: 0, unrealized: floating[$0] ?? 0, held: true, count: 0, events: [])
        }
        return out
    }
    static func row(_ sym: String, positions: [J], realized: J) -> SymbolPnl? {
        rows(positions: positions, realized: realized).first { $0.sym == sym }
    }
}

/// 「今年盈亏」：每个票一行，已落袋、现仓浮盈和合计分开写。
struct SymbolPnlTable: View {
    let rows: [SymbolPnl]
    let realized: J
    @ViewState private var sortOrder = [KeyPathComparator(\SymbolPnl.total, order: .reverse)]
    var sorted: [SymbolPnl] { rows.sorted(using: sortOrder) }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Table(sorted, sortOrder: $sortOrder) {
                TableColumn(L("票"), value: \.sym) { row in
                    VStack(alignment: .leading, spacing: 3) {
                        Text(row.sym).font(.callout.weight(.semibold))
                        Text(row.held ? "持有中" : "已清仓").font(.caption).foregroundStyle(.secondary)
                    }.padding(.vertical, 4)
                }.width(min: 80, ideal: 95)
                TableColumn(L("已落袋（含佣金）"), value: \.realized) { row in
                    VStack(alignment: .trailing, spacing: 3) {
                        Text(signedUSD(row.realized)).monospacedDigit().foregroundStyle(pnlTint(row.realized))
                        Text("\(row.count) 笔平仓").font(.caption).foregroundStyle(.secondary)
                    }.frame(maxWidth: .infinity, alignment: .trailing)
                }.width(min: 110, ideal: 130)
                TableColumn(L("现仓浮盈"), value: \.unrealizedSort) { row in
                    Text(row.unrealized == nil ? "—" : signedUSD(row.unrealized)).monospacedDigit()
                        .foregroundStyle(pnlTint(row.unrealized)).frame(maxWidth: .infinity, alignment: .trailing)
                }.width(min: 80, ideal: 100)
                TableColumn(L("今年合计"), value: \.total) { row in
                    Text(signedUSD(row.total)).font(.callout.weight(.semibold)).monospacedDigit()
                        .foregroundStyle(pnlTint(row.total)).frame(maxWidth: .infinity, alignment: .trailing)
                }.width(min: 90, ideal: 110)
                TableColumn(L("最近一笔平仓")) { row in
                    Text(row.lastEvent).font(.caption).foregroundStyle(.secondary).lineLimit(2).help(
                        row.events.prefix(6).map { shortDay($0["date"].s) + " " + $0["text"].s + " " + signedUSD($0["realized"].n) }.joined(separator: "\n"))
                }.width(min: 200, ideal: 320)
            }.frame(minHeight: 200)
            Text(realized["error"].s.isEmpty ? realized["note"].s : "已落袋盈亏汇总失败：" + realized["error"].s)
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

/// 持仓详情里的「本轮」小节：这一轮已经止盈或平掉了多少。
struct RoundSection: View {
    let round: J
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text("本轮已落袋（自 \(shortDay(round["since"].s)) 买入起）").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text(signedUSD(round["realized"].n)).font(.headline).monospacedDigit().foregroundStyle(pnlTint(round["realized"].n))
            }
            ForEach(Array(round["events"].items.prefix(5).enumerated()), id: \.offset) { _, e in
                HStack(alignment: .firstTextBaseline) {
                    Text(shortDay(e["date"].s) + " " + e["text"].s).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    Spacer()
                    Text(signedUSD(e["realized"].n)).font(.caption).monospacedDigit().foregroundStyle(pnlTint(e["realized"].n))
                }
            }
            Text("今年的盈亏看「今年盈亏」页").font(.caption2).foregroundStyle(.secondary)
        }
    }
}
