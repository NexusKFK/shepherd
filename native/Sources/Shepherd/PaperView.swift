import SwiftUI
import Charts

// 纸盘（~/tools/paper_book/out/state.json，经 workbench/paper.py 读入 state["paper"]）。
// 只观察：不进热度、敞口、VaR，也不进真实账本。估值与自动平仓由 paper_book/mark.py 在收盘后完成。
struct PaperView: View {
    @EnvironmentObject var model: DeskModel
    var paper: J { model.state["paper"] }
    var trades: [J] { paper["trades"].items }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                PageHeading(title: "纸盘", subtitle: "观察用的模拟仓：按收盘价估值，不计入热度、风险和真实账本")
                if paper["status"].s != "ok" {
                    Label(paper["notice"].s.isEmpty ? "纸盘数据不可用" : paper["notice"].s, systemImage: "exclamationmark.triangle")
                        .font(.callout).foregroundStyle(PortfolioStyle.warn)
                }
                if !trades.isEmpty {
                    HStack(spacing: 12) {
                        Metric(label: "持仓中", value: num(paper["totals"]["open"], digits: 0) + " 笔",
                               note: "已平仓 " + num(paper["totals"]["closed"], digits: 0) + " 笔")
                        Metric(label: "合计盈亏（含成本）", value: signedUSD(paper["totals"]["pnl"].n),
                               note: signedR(paper["totals"]["pnl_r"].n) + " · 1R = " + usd0(paper["one_r"].n), valueColor: pnlTint(paper["totals"]["pnl"].n))
                        Metric(label: "持仓中占用预算", value: num(paper["totals"]["budget_r_open"]) + "R", note: "每笔开仓时定的风险预算")
                        Metric(label: "估值日", value: paper["data_day"].s.isEmpty ? "—" : paper["data_day"].s, note: "收盘价")
                    }
                }
                ForEach(trades, id: \.id) { PaperTradeCard(trade: $0) }
                Notes(values: [paper["source"].s, paper["costs_note"].s, "1R 口径：" + paper["r_source"].s].filter { !$0.isEmpty && $0 != "1R 口径：" })
            }.padding(20)
        }
    }
}

func signedR(_ value: Double?) -> String {
    guard let value else { return "—" }
    return (value >= 0 ? "+" : "−") + num(.number(abs(value))) + "R"
}

struct PaperTradeCard: View {
    let trade: J
    @ViewState private var expanded = false
    var series: [J] { trade["series"].items }
    var pending: Bool { !trade["pending"].s.isEmpty }
    var closed: Bool { trade["status"].s == "closed" }
    var pnlColor: Color { (trade["pnl"].n ?? 0) >= 0 ? .green : .red }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text(trade["id"].s).font(.caption.monospaced()).foregroundStyle(.secondary)
                Text(trade["name"].s).font(.headline)
                Text(pending ? "等收盘" : closed ? "已平仓" : "持仓中").font(.caption)
                    .padding(.horizontal, 7).padding(.vertical, 2)
                    .background((closed ? Color.secondary : PortfolioStyle.data).opacity(0.15), in: Capsule())
                Spacer()
                if !pending {
                    Text(signedUSD(trade["pnl"].n)).font(.title3.weight(.semibold)).monospacedDigit().foregroundStyle(pnlColor)
                    Text(signedR(trade["pnl_r"].n)).font(.callout).monospacedDigit().foregroundStyle(.secondary)
                }
            }
            Text(trade["thesis"].s).font(.callout).foregroundStyle(.secondary)
            if pending {
                Text(trade["pending"].s + " · 开仓 " + trade["entry_day"].s + " · 预算 " + num(trade["budget_r"]) + "R")
                    .font(.callout).foregroundStyle(PortfolioStyle.warn)
            } else {
                HStack(spacing: 20) {
                    fact("开仓", heldText(trade))
                    fact("预算", num(trade["budget_r"]) + "R（" + usd0(trade["budget_usd"].n) + "）")
                    fact("毛敞口", usd0(trade["gross"].n) + " · 收益 " + signedPct(trade["ret_on_gross_pct"].n, digits: 2))
                    fact(trade["ratio_name"].s, num(trade["ratio"], digits: 5) + " · 较开仓 " + signedPct(trade["ratio_vs_entry_pct"].n, digits: 2))
                    if !closed { fact("距止损", stopGap) }
                    fact("期间最好／最差", signedR(trade["max_pnl_r"].n) + "／" + signedR(trade["min_pnl_r"].n))
                }
                if series.count >= 2 { charts }
            }
            legs
            Label(trade["stop_text"].s, systemImage: "hand.raised").font(.callout)
            if closed {
                Text("平仓 " + trade["closed_day"].s + "：" + trade["close_reason"].s).font(.callout).foregroundStyle(PortfolioStyle.warn)
            }
            DisclosureGroup("定仓算法与记录", isExpanded: $expanded) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(trade["sizing"].s).font(.callout)
                    Text("开仓价来源：" + trade["entry_source"].s + " · 复核日 " + trade["review_on"].s).font(.caption).foregroundStyle(.secondary)
                    ForEach(Array(trade["events"].items.enumerated()), id: \.offset) { _, e in
                        Text(e["day"].s + "  " + e["text"].s).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                    }
                }.padding(.top, 6)
            }.font(.callout)
        }.card()
    }
    var stopGap: String {
        if let gap = trade["stop_gap_pct"].n { return "比值还差 " + num(.number(gap)) + "%" }
        if let gap = trade["stop_gap_r"].n { return "还差 " + num(.number(gap)) + "R" }
        return "—"
    }
    func fact(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.callout).monospacedDigit()
        }
    }
    var charts: some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text("盈亏（R，含成本）").font(.caption).foregroundStyle(.secondary)
                Chart {
                    ForEach(Array(series.enumerated()), id: \.offset) { _, p in
                        LineMark(x: .value("日", p["day"].s), y: .value("R", p["pnl_r"].n ?? 0)).foregroundStyle(PortfolioStyle.data)
                    }
                    RuleMark(y: .value("零", 0)).foregroundStyle(.secondary.opacity(0.4))
                    if trade["stop_kind"].s == "pnl_r" {
                        RuleMark(y: .value("止损", -(trade["budget_r"].n ?? 0))).foregroundStyle(.red.opacity(0.6))
                            .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3]))
                    }
                }.chartXAxis(.hidden).chartYScale(domain: pnlDomain).frame(height: 120)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(trade["ratio_name"].s + " 与止损线").font(.caption).foregroundStyle(.secondary)
                Chart {
                    ForEach(Array(series.enumerated()), id: \.offset) { _, p in
                        LineMark(x: .value("日", p["day"].s), y: .value("比值", p["ratio"].n ?? 0), series: .value("线", "比值"))
                            .foregroundStyle(PortfolioStyle.data)
                        if let line = p["stop_line"].n {
                            LineMark(x: .value("日", p["day"].s), y: .value("比值", line), series: .value("线", "止损"))
                                .foregroundStyle(.red.opacity(0.6)).lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3]))
                        }
                    }
                }.chartXAxis(.hidden).chartYScale(domain: .automatic(includesZero: false)).frame(height: 120)
            }
        }
    }
    /// Keep zero and (for an R stop) the stop line in view; Charts does not widen the domain for RuleMarks.
    var pnlDomain: ClosedRange<Double> {
        let values = series.compactMap { $0["pnl_r"].n } + [0] + (trade["stop_kind"].s == "pnl_r" ? [-(trade["budget_r"].n ?? 0)] : [])
        let lo = values.min() ?? -1, hi = values.max() ?? 1, pad = max((hi - lo) * 0.08, 0.05)
        return (lo - pad)...(hi + pad)
    }
    var legs: some View {
        Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 6) {
            GridRow {
                ForEach(["标的", "方向", "股数", "开仓价", "收盘", "腿收益", "腿盈亏"], id: \.self) {
                    Text($0).font(.caption).foregroundStyle(.secondary)
                }
            }
            ForEach(Array(trade["legs"].items.enumerated()), id: \.offset) { _, l in
                GridRow {
                    Text(l["sym"].s).font(.callout.weight(.medium))
                    Text(l["side"].s == "long" ? "多" : "空")
                    Text(num(l["qty"], digits: 0)).monospacedDigit()
                    Text(num(l["entry"])).monospacedDigit()
                    Text(num(l["last"])).monospacedDigit()
                    Text(signedPct(l["ret_pct"].n, digits: 2)).monospacedDigit().foregroundStyle(pnlTint(l["pnl"].n))
                    Text(signedUSD(l["pnl"].n)).monospacedDigit().foregroundStyle(pnlTint(l["pnl"].n))
                }.font(.callout)
            }
        }
    }
}

private func heldText(_ trade: J) -> String {
    let day = trade["entry_day"].s, n = num(trade["held_days"], digits: 0)
    return "\(day) · 第 \(n) 天"
}
