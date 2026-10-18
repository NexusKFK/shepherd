import SwiftUI

// Landing page. Everything here restates state the engine already computed (see workbench/overview.py
// for the digest rules); nothing is advice, and nothing is calculated that the other pages do not show.
struct OverviewView: View {
    @EnvironmentObject var model: DeskModel
    let go: (Page) -> Void
    private var overview: J { model.state["overview"] }
    private var exposure: J { model.state["exposure"] }
    private var heat: J { exposure["portfolio_heat"] }
    private var scenario: J { exposure["market_scenario"] }
    private var downFive: J { scenario["scenarios"].items.first { $0["key"].s == "down5" } ?? .null }
    private var cash: CashSnapshot { CashSnapshot(model.state["snapshot"]) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                header
                if !overview["error"].s.isEmpty { Label(overview["error"].s, systemImage: "xmark.octagon.fill").foregroundStyle(.red) }
                if model.positions.isEmpty && model.plans.isEmpty { StartCard(go: go).environmentObject(model) }
                BudgetCard(macro: exposure["macro_panel"], heat: heat, go: go)
                HStack(alignment: .top, spacing: 16) {
                    AttentionCard(items: overview["attention"].items, go: go)
                    UpcomingCard(items: overview["upcoming"].items, go: go).frame(width: 320)
                }
                metrics
                HStack(alignment: .top, spacing: 16) {
                    PressureCard(clusters: exposure["factor_clusters"]["clusters"].items, go: go)
                    ExposureCard(rows: exposure["rows"].items, go: go)
                }
            }.padding(24)
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(dayLabel(model.day)).font(.title2.weight(.semibold))
                Text("美东交易日 · 只汇总已有资料，不构成建议").font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 12)
            ForEach(Array(overview["freshness"].items.enumerated()), id: \.offset) { _, row in FreshnessChip(row: row) }
        }
    }

    private var accountsNote: String {
        let parts = model.state["snapshot"]["accounts"].items.compactMap { a in a["nlv"].n.map { a["id"].s + " " + compactUSD($0) } }
        return parts.isEmpty ? "快照 " + model.state["snapshot"]["day"].s : parts.joined(separator: " · ")
    }
    private var metrics: some View {
        let policy = model.state["risk_policy"]
        return HStack(alignment: .top, spacing: 12) {
            DashboardMetric(title: "快照净值", value: usd(model.state["snapshot"]["nlv"]), detail: accountsNote)
            DashboardMetric(title: "现金占净值", value: cash.percent.map { num(.number($0), digits: 1) } ?? "—", suffix: "%",
                            detail: cash.amount.map { usd(.number($0)) } ?? "账户现金数据不完整", fraction: cash.percent.map { $0 / 100 })
            DashboardMetric(title: "统一 1R", value: usd(heat["one_r"]),
                            detail: policy["nlv"].n == nil ? "定仓净值未确认" : "定仓净值 " + compactUSD(policy["nlv"].n) + " × " + num(policy["risk_pct"]) + "%")
            DashboardMetric(title: downFive["label"].s.isEmpty ? "大盘下跌 5%" : downFive["label"].s, value: num(downFive["r"]), suffix: "R",
                            detail: usd(downFive["pnl"]) + " · 线性估算",
                            warning: scenario["coverage"]["subset"].b ? "仅覆盖 \(num(scenario["coverage"]["used"])) / \(num(scenario["coverage"]["total"])) 条" : (scenario["one_r"].n == nil ? "定仓净值未确认" : ""))
        }.fixedSize(horizontal: false, vertical: true)
    }
}

/// Hover text for a freshness dot: what is wrong (if anything), how often the source refreshes, the next refresh.
func freshnessHelp(_ row: J) -> String {
    var lines = [row["note"].s.isEmpty ? "数据日期正常（绿色只表示数据不落后，不是市场信号）" : row["note"].s]
    if !row["cadence"].s.isEmpty { lines.append("更新：" + row["cadence"].s) }
    if !row["next"].s.isEmpty { lines.append("下次更新：" + row["next"].s + "（本机时间，不含美股假日）") }
    return lines.joined(separator: "\n")
}

struct FreshnessChip: View {
    let row: J
    var chipText: String {
        let day: String = row["day"].s.isEmpty ? "无" : shortDay(row["day"].s)
        return row["label"].s + " " + day
    }
    var chipFill: Color { row["level"].s == "ok" ? Color.secondary.opacity(0.10) : PortfolioStyle.level(row["level"].s).opacity(0.16) }
    var chipTint: Color { row["level"].s == "ok" ? Color.secondary : PortfolioStyle.level(row["level"].s) }
    var spoken: String {
        let note: String = row["note"].s.isEmpty ? "" : "，" + row["note"].s
        return row["label"].s + " " + row["day"].s + note
    }
    var body: some View {
        let level = row["level"].s
        HStack(spacing: 5) {
            FreshnessDot(level: level)
            Text(chipText).font(.caption).monospacedDigit()
        }
        .padding(.horizontal, 9).padding(.vertical, 4)
        .background(chipFill, in: Capsule())
        .foregroundStyle(chipTint)
        .help(freshnessHelp(row))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(spoken)
    }
}

private struct Stat: View {
    let label: String; let value: String; var note = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.title3.weight(.semibold)).monospacedDigit().lineLimit(1).minimumScaleFactor(0.8)
            if !note.isEmpty { Text(note).font(.caption2).foregroundStyle(.secondary) }
        }
    }
}

struct BudgetCard: View {
    let macro: J; let heat: J; let go: (Page) -> Void
    private var ok: Bool { macro["status"].s == "ok" }
    private var light: String { macro["light"].s }
    private var trends: [J] { macro["trends"].items }
    private func names(_ state: String) -> String { trends.filter { $0["state"].s == state }.map { $0["name"].s }.joined(separator: " · ") }
    var body: some View {
        Button { go(.risk) } label: {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text("新开仓预算").font(.headline)
                    Spacer()
                    Label("风险页详情", systemImage: "chevron.right").labelStyle(.titleAndIcon).font(.caption).foregroundStyle(.secondary)
                }
                if macro["status"].s == "disabled" {
                    HStack(spacing: 26) { Stat(label: "已计入热度", value: heatText); Stat(label: "热度上限", value: num(heat["cap_r"]) + " R", note: "固定值（宏观灯模块未开启）") }
                } else if !ok {
                    Label(macro["notice"].s.isEmpty ? "宏观面板不可用，上限按静态值。" : macro["notice"].s, systemImage: "exclamationmark.triangle.fill")
                        .font(.callout).foregroundStyle(PortfolioStyle.warn)
                    HStack(spacing: 26) { Stat(label: "已计入热度", value: heatText); Stat(label: "热度上限", value: num(heat["cap_r"]) + " R") }
                } else {
                    HStack(alignment: .center, spacing: 24) {
                        HStack(spacing: 12) {
                            Circle().fill(MacroStyle.color(light)).frame(width: 26, height: 26).accessibilityHidden(true)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(light + "灯" + (macro["stage"].s == "rel1" ? " · 释放第一步" : "")).font(.title2.weight(.semibold))
                                Text("燃料" + (macro["fuel_high"].b ? "高" : "不高") + " · 数据 " + shortDay(macro["data_day"].s)).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        Divider().frame(height: 38)
                        Stat(label: "已计入热度", value: heatText, note: heat["heat_r"].n == nil ? "定仓净值未确认" : "上限只作提示")
                        Stat(label: "桶上限", value: "×" + num(macro["bucket_scale"]))
                        Stat(label: "保护", value: macro["vix_cheap"].b ? "便宜" : "不便宜", note: macro["vix_cheap"].b ? "VIX < 20" : "")
                        Spacer(minLength: 0)
                    }
                    VStack(alignment: .leading, spacing: 5) {
                        trendLine("顺风", "顺风", names("顺风"))
                        trendLine("过渡", "过渡", names("过渡"))
                        trendLine("逆风", "逆风", names("逆风"))
                    }
                    if !macro["unconfirmed"].s.isEmpty { Text("未确认档：" + macro["unconfirmed"].s).font(.callout) }
                }
            }.card().contentShape(Rectangle())
        }.buttonStyle(.plain)
    }
    private var heatText: String { heat["heat_r"].n == nil ? "—" : num(heat["heat_r"]) + " / " + num(heat["cap_r"]) + " R" }
    @ViewBuilder private func trendLine(_ title: String, _ state: String, _ text: String) -> some View {
        if !text.isEmpty {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(title).font(.caption.weight(.semibold)).foregroundStyle(MacroStyle.color(state)).frame(width: Translator.shared.language == "en" ? 74 : 34, alignment: .leading)
                Text(text).font(.callout).lineLimit(2)
            }
        }
    }
}

struct AttentionCard: View {
    let items: [J]; let go: (Page) -> Void
    private func count(_ severity: String) -> Int { items.filter { $0["severity"].s == severity }.count }
    private func icon(_ severity: String) -> String {
        severity == "red" ? "exclamationmark.octagon.fill" : severity == "amber" ? "exclamationmark.triangle.fill" : "info.circle"
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text("需要处理").font(.headline)
                if count("red") > 0 { pill(count("red"), .red) }
                if count("amber") > 0 { pill(count("amber"), PortfolioStyle.warn) }
                Spacer()
            }
            if items.isEmpty {
                Label("没有触发任何提示规则", systemImage: "checkmark.circle.fill").foregroundStyle(.green).padding(.top, 4)
                Text("只表示下列规则都没触发：止损位、临近到期、大类上限、未计入热度、数据过期、待核对。不代表没有风险。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                if index > 0 { Divider() }
                Button { if let page = Page(engineKey: item["page"].s) { go(page) } } label: {
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: icon(item["severity"].s)).foregroundStyle(PortfolioStyle.level(item["severity"].s)).frame(width: 18)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item["title"].s).font(.callout).multilineTextAlignment(.leading)
                            if !item["detail"].s.isEmpty { Text(item["detail"].s).font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.leading) }
                        }
                        Spacer(minLength: 8)
                        Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.tertiary).padding(.top, 4)
                    }.padding(.vertical, 3).contentShape(Rectangle())
                }.buttonStyle(.plain)
            }
        }.card()
    }
    private func pill(_ n: Int, _ color: Color) -> some View {
        Text("\(n)").font(.caption.weight(.semibold)).monospacedDigit().padding(.horizontal, 7).padding(.vertical, 1)
            .background(color.opacity(0.18), in: Capsule()).foregroundStyle(color)
    }
}

struct UpcomingCard: View {
    let items: [J]; let go: (Page) -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack { Text("接下来").font(.headline); Spacer(); Button("全部日历") { go(.calendar) }.buttonStyle(.link).font(.caption) }
            if items.isEmpty { Text("日历里没有之后的事件").font(.callout).foregroundStyle(.secondary) }
            ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                if index > 0 { Divider() }
                let days = Int(item["days"].n ?? 99)
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(countdown(days)).font(.caption.weight(.semibold)).monospacedDigit()
                        .foregroundStyle(days <= 1 ? PortfolioStyle.warn : Color.secondary).frame(width: Translator.shared.language == "en" ? 70 : 46, alignment: .leading)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(item["name"].s.replacingOccurrences(of: "日期待确认", with: "")).font(.callout).lineLimit(2)
                            .foregroundStyle(item["tentative"].b ? Color.secondary : .primary)
                        Text([shortDay(item["day"].s), item["time"].s, item["kind"].s + (item["tentative"].b ? " · 日期待确认" : "")]
                            .filter { !$0.isEmpty }.joined(separator: " · ")).font(.caption2).foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                }
            }
        }.card()
    }
}

struct PressureCard: View {
    let clusters: [J]; let go: (Page) -> Void
    private func fraction(_ c: J) -> Double { (c["stress_loss_r"].n ?? 0) / max(c["cap_r"].n ?? 1, 0.0001) }
    private var rows: [J] { Array(clusters.filter { $0["stress_loss_r"].n != nil }.sorted { fraction($0) > fraction($1) }.prefix(4)) }
    private var upper: Double { max(1, rows.flatMap { [$0["stress_loss_r"].n, $0["cap_r"].n].compactMap { $0 } }.max() ?? 1) * 1.12 }
    private func label(_ c: J) -> String {
        if !c["uncovered"].items.isEmpty { return "\(c["uncovered"].items.count) 条未计入 · 仅覆盖子集" }
        return ["over": "达到或超过上限", "near": "接近上限", "ok": "上限以内", "r_unavailable": "定仓净值未确认"][c["status"].s] ?? "待核对"
    }
    var body: some View {
        Button { go(.risk) } label: {
            VStack(alignment: .leading, spacing: 10) {
                HStack { Text("大类跳空压力").font(.headline); Spacer(); Text("按占上限比例排序").font(.caption).foregroundStyle(.secondary) }
                if rows.isEmpty { Text("暂无可比较的大类压力").font(.callout).foregroundStyle(.secondary) }
                ForEach(Array(rows.enumerated()), id: \.offset) { _, c in
                    VStack(alignment: .leading, spacing: 3) {
                        HStack {
                            Text(c["factor"].s).font(.callout.weight(.semibold))
                            Spacer()
                            Text(num(c["stress_loss_r"]) + " / " + num(c["cap_r"]) + " R").font(.callout).monospacedDigit()
                        }
                        RiskScaleBar(value: c["stress_loss_r"].n, lower: 0, upper: upper, cap: c["cap_r"].n, color: PortfolioStyle.risk(c["status"].s))
                        Text(label(c)).font(.caption).foregroundStyle(c["uncovered"].items.isEmpty && c["status"].s != "near" ? Color.secondary : PortfolioStyle.risk(c["status"].s))
                    }
                }
            }.card().contentShape(Rectangle())
        }.buttonStyle(.plain)
    }
}

struct ExposureCard: View {
    @EnvironmentObject var model: DeskModel
    let rows: [J]; let go: (Page) -> Void
    private var top: [J] { Array(rows.filter { $0["dollar_delta"].n != nil }.sorted { abs($0["dollar_delta"].n ?? 0) > abs($1["dollar_delta"].n ?? 0) }.prefix(5)) }
    var body: some View {
        Button { go(.holdings) } label: {
            VStack(alignment: .leading, spacing: 10) {
                HStack { Text("最大敞口").font(.headline); Spacer(); Text("Delta 美元 · 占快照净值 · 浮盈按快照价").font(.caption).foregroundStyle(.secondary) }
                if top.isEmpty { Text("暂无可计量的持仓").font(.callout).foregroundStyle(.secondary) }
                ForEach(Array(top.enumerated()), id: \.offset) { index, row in
                    if index > 0 { Divider() }
                    let position = model.positions.first { $0.id == row.id } ?? .null
                    let pct = unrealizedPct(position)
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(row["symbol"].s + (position["kind"].s == "option" ? " " + position["label"].s : "")).font(.callout.weight(.semibold)).lineLimit(1)
                            Text(row["broker"].s + (position["kind"].s == "stock" ? " · 正股 \(num(position["qty"]))" : "")).font(.caption2).foregroundStyle(.secondary)
                        }
                        Spacer(minLength: 6)
                        VStack(alignment: .trailing, spacing: 1) {
                            Text(usd0(row["dollar_delta"].n)).font(.callout).monospacedDigit()
                            Text(row["nlv_pct"].n.map { num(.number($0), digits: 1) + "%" } ?? "—").font(.caption2).foregroundStyle(.secondary).monospacedDigit()
                        }
                        Text(signedPct(pct, digits: 0)).font(.caption).monospacedDigit()
                            .foregroundStyle(abs(pct ?? 0) < 0.5 ? Color.secondary : (pct ?? 0) > 0 ? Color.green : Color.red).frame(width: 46, alignment: .trailing)
                    }
                }
            }.card().contentShape(Rectangle())
        }.buttonStyle(.plain)
    }
}

/// Shown only while the ledger has no plans and no holdings: the three ways to get started.
private struct StartCard: View {
    @EnvironmentObject var model: DeskModel
    let go: (Page) -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("从这里开始").font(.headline)
            Text("账本还是空的。你的 1R 是 " + usd(.number((model.state["risk_policy"]["nlv"].n ?? 0) * (model.state["risk_policy"]["risk_pct"].n ?? 0) / 100))
                 + "，下面三件事任选一件。").foregroundStyle(.secondary)
            HStack(spacing: 12) {
                Button { model.editor = .plan() } label: { Label("新建一笔交易计划", systemImage: "plus") }.buttonStyle(.borderedProminent)
                Button { go(.data) } label: { Label("导入持仓 JSON", systemImage: "square.and.arrow.down") }
                Button { model.openDemo() } label: { Label("先看演示（虚构数据）", systemImage: "theatermasks") }.disabled(Engine.demo)
            }
            Text("计划按 1R 自动算数量；成交登记只记账，不会向券商下单。没有接数据的模块不会报警，可以在菜单「选择模块…」里再打开。")
                .font(.caption).foregroundStyle(.secondary)
        }.frame(maxWidth: .infinity, alignment: .leading).card()
    }
}
