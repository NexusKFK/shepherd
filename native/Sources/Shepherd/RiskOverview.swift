import SwiftUI

struct RiskView: View {
    @EnvironmentObject var model: DeskModel
    @ViewState private var detail = false
    var exposure: J { model.state["exposure"] }
    var heat: J { exposure["portfolio_heat"] }
    var scenario: J { exposure["market_scenario"] }
    var downFive: J { scenario["scenarios"].items.first { $0["key"].s == "down5" } ?? .null }
    var cash: CashSnapshot { CashSnapshot(model.state["snapshot"]) }
    var heatWarning: String {
        let missing = heat["unknown"].items.count + heat["unbounded"].items.count
        if missing > 0 { return "\(missing) 组未计入 · 总风险未完整覆盖" }
        if !heat["no_stop"].items.isEmpty {
            return "无止损正股 " + usd(heat["no_stop_usd"]) + " · " + heat["no_stop"].items.map { $0["symbol"].s }.joined(separator: "、")
        }
        return ""
    }
    var exemptNote: String {
        if heat["exempt"].items.isEmpty { return "" }
        return "免判据持仓 " + usd(heat["exempt_usd"]) + " · " + heat["exempt"].items.map { $0["symbol"].s }.joined(separator: "、") + " · 不设止损，不计热度"
    }
    var body: some View {
        GeometryReader { geometry in
            VStack(spacing: 0) {
                HStack(alignment: .top) {
                    PageHeading(title: "组合风险", subtitle: "预算使用、压力来源与覆盖范围")
                    VStack(alignment: .trailing, spacing: 8) {
                        Text("1R " + usd(heat["one_r"])).font(.headline).monospacedDigit()
                        Picker("视图", selection: $detail) { Text("总览").tag(false); Text("逐仓与模型").tag(true) }
                            .pickerStyle(.segmented).labelsHidden().frame(width: 210)
                    }
                }.padding(24).padding(.bottom, -8)
                if detail { RiskModelDetails() }
                else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 18) {
                            HStack(alignment: .top, spacing: 12) {
                                DashboardMetric(title: "已计入热度", value: num(heat["heat_r"]), suffix: "/ " + num(heat["cap_r"]) + " R",
                                    detail: usd(heat["heat_usd"]) + " · 上限只作提示", warning: heatWarning, note: exemptNote,
                                    fraction: heat["heat_r"].n.flatMap { used in heat["cap_r"].n.flatMap { $0 > 0 ? used / $0 : nil } }, color: PortfolioStyle.risk(heat["status"].s))
                                DashboardMetric(title: downFive["label"].s.isEmpty ? "大盘下跌 5%" : downFive["label"].s,
                                    value: num(downFive["r"]), suffix: "R", detail: usd(downFive["pnl"]) + " · 线性估算",
                                    warning: scenario["coverage"]["subset"].b ? "仅覆盖 \(num(scenario["coverage"]["used"])) / \(num(scenario["coverage"]["total"])) 条" : (scenario["one_r"].n == nil ? "定仓净值未确认" : ""))
                                DashboardMetric(title: "现金 / 快照净值", value: cash.percent.map { num(.number($0), digits: 1) } ?? "—", suffix: "%",
                                    detail: cash.amount.map { usd(.number($0)) + " · 资金比例，非风险余量" } ?? "账户现金数据不完整",
                                    fraction: cash.percent.map { $0 / 100 })
                            }.fixedSize(horizontal: false, vertical: true)
                            dataStatus
                            MacroLightCard(value: exposure["macro_panel"])
                            FactorOverview(value: exposure["factor_clusters"], compact: geometry.size.width < 1050)
                            ScenarioComparison(value: scenario)
                            DisclosureGroup("热度明细与未计入仓位") { PortfolioHeatPanel(value: heat).padding(.top, 8) }
                            DisclosureGroup("Beta、覆盖与估算说明") { MarketScenarioPanel(value: scenario).padding(.top, 8) }
                            DisclosureGroup("大类代理、敏感度与相关性") { FactorTechnicalDetails(value: exposure["factor_clusters"]).padding(.top, 8) }
                        }.padding(24)
                    }
                }
            }
        }
    }
    var dataStatus: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 18) {
                Label("持仓 " + model.state["snapshot"]["day"].s, systemImage: "clock")
                Text("行情 " + exposure["market_day"].s)
                Text("Delta \(num(exposure["coverage"]["delta_covered"])) / \(num(exposure["coverage"]["total"]))")
                Spacer(minLength: 0)
            }.font(.caption).foregroundStyle(.secondary)
            if !exposure["display_summaries"].items.isEmpty {
                Text("\(exposure["display_summaries"].items.filter { $0["display_only"].b }.count) 条已关联组合汇总仅展示，合约腿参与计算。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if exposure["status"].s == "stale" || model.state["snapshot"]["status"].s != "available" {
                Label("资料过期或来源不可用 · 当前显示历史参考", systemImage: "exclamationmark.triangle.fill").font(.callout).foregroundStyle(PortfolioStyle.warn)
            }
            if !model.state["conflicts"].items.isEmpty { Notes(values: model.state["conflicts"].strings) }
            if !exposure["coverage"]["delta_complete"].b && !exposure["rows"].items.isEmpty {
                Label("有记录未计入敞口，查看“逐仓与模型”补充资料。", systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(PortfolioStyle.warn)
            }
        }
    }
}

struct FactorOverview: View {
    let value: J
    let compact: Bool
    @ViewState private var selected = ""
    var clusters: [J] { value["clusters"].items }
    var rows: [J] {
        let existing = Set(clusters.map { $0["factor"].s })
        return clusters + ["科技", "非科技", "黄金", "利率", "宽基"].filter { !existing.contains($0) }.map {
            J.object(["factor": .string($0), "status": .string("empty")])
        }
    }
    var active: J { rows.first { $0["factor"].s == selected } ?? rows.first ?? .null }
    var lower: Double { min(0, (clusters.compactMap { $0["stress_loss_r"].n }.min() ?? 0) * 1.1) }
    var upper: Double { max(1, (clusters.flatMap { [$0["stress_loss_r"].n, $0["cap_r"].n].compactMap { $0 } }.max() ?? 1) * 1.12) }
    func status(_ group: J) -> String {
        if group["status"].s == "empty" { return "无持仓记录" }
        if !group["uncovered"].items.isEmpty { return "\(group["uncovered"].items.count) 条未计入 · 仅覆盖子集" }
        return ["over": "达到或超过上限", "near": "接近上限", "ok": "上限以内", "r_unavailable": "定仓净值未确认", "unclustered": "未分簇，未估算压力"][group["status"].s] ?? "待核对"
    }
    var body: some View {
        Group {
            if compact { VStack(spacing: 16) { overview; inspector } }
            else { HStack(alignment: .top, spacing: 16) { overview.frame(maxWidth: .infinity); inspector.frame(width: 320) } }
        }
    }
    var overview: some View {
        PortfolioPanel(title: "大类跳空压力") {
            HStack { Text("点选大类查看持仓贡献"); Spacer(); Text("│ 上限") }.font(.caption).foregroundStyle(.secondary)
            VStack(spacing: 2) {
                ForEach(Array(rows.enumerated()), id: \.offset) { _, group in
                    Button { selected = group["factor"].s } label: {
                        FactorBucketRow(group: group, label: status(group), lower: lower, upper: upper, selected: isActive(group))
                    }.buttonStyle(.plain)
                        .accessibilityLabel(rowLabel(group))
                        .accessibilityAddTraits(isActive(group) ? .isSelected : [])
                }
            }
            RiskScaleLabels(lower: lower, upper: upper).padding(.horizontal, 10)
            Text("各行使用同一 R 刻度；负值为该情景下的风险抵减。大类压力不能相加当作全组合最大损失。")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
    func isActive(_ group: J) -> Bool { group["factor"].s == active["factor"].s }
    func rowLabel(_ group: J) -> String {
        let name: String = group["factor"].s
        return name + "，压力 " + num(group["stress_loss_r"]) + " R，" + status(group)
    }
    var members: [J] { active["members"].items.sorted { abs($0["stress_pnl"].n ?? 0) > abs($1["stress_pnl"].n ?? 0) } }
    func contribution(_ member: J) -> Double? {
        guard let pnl = member["stress_pnl"].n, let oneR = value["one_r"].n, oneR > 0 else { return nil }
        return -pnl / oneR
    }
    var contributionRange: Double { max(0.1, members.compactMap { contribution($0).map(abs) }.max() ?? 0.1) }
    var inspector: some View {
        PortfolioPanel(title: active["factor"].s + " · 持仓贡献") {
            HStack(alignment: .firstTextBaseline) {
                Text(num(active["stress_loss_r"]) + " R").font(.title2.weight(.semibold)).monospacedDigit()
                Spacer()
                Text(status(active)).font(.caption).foregroundStyle(.secondary)
            }
            Text("净敞口 " + usd(active["net_dollar_delta"])).font(.caption).foregroundStyle(.secondary)
            if let excess = active["excess_r"].n, excess > 0 {
                VStack(alignment: .leading, spacing: 2) {
                    Label("超出上限 " + num(.number(excess)) + " R：减仓或买保护补齐，时点看 PA", systemImage: "shield.lefthalf.filled").font(.caption).foregroundStyle(.red)
                    ForEach(Array(active["sleeves"].items.filter { $0["hedge_proxy_dollars"].n != nil }.enumerated()), id: \.offset) { _, sl in
                        Text("若全由" + sl["name"].s + "补：约 " + usd(sl["hedge_proxy_dollars"]) + " " + sl["proxy"].s + " 空头 Delta（如 put 的 Delta 名义）")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            if members.isEmpty { Text("暂无可展示的持仓贡献").font(.callout).foregroundStyle(.secondary).padding(.vertical, 20) }
            ForEach(Array(members.prefix(6).enumerated()), id: \.offset) { _, member in
                VStack(alignment: .leading, spacing: 2) {
                    HStack {
                        Text(member["symbol"].s + " · " + member["broker"].s).font(.callout)
                        Spacer()
                        Text(contribution(member).map { num(.number($0)) + " R" } ?? "—").font(.callout).monospacedDigit()
                    }
                    RiskScaleBar(value: contribution(member), lower: members.contains { (contribution($0) ?? 0) < 0 } ? -contributionRange : 0, upper: contributionRange)
                    Text(member["sleeve"].s).font(.caption2).foregroundStyle(.secondary)
                }
            }
            if members.count > 6 { Text("其余 \(members.count - 6) 条见下方大类明细").font(.caption).foregroundStyle(.secondary) }
            if !active["uncovered"].items.isEmpty {
                Label("未计入：" + active["uncovered"].items.map { $0["symbol"].s }.joined(separator: "、"), systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(PortfolioStyle.warn)
            }
            Divider()
            Text("贡献按本大类自适应刻度展示。\n已有数据的估算余量不等于可加仓额度。")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct FactorBucketRow: View {
    let group: J
    let label: String
    let lower: Double
    let upper: Double
    let selected: Bool
    var capNote: String {
        guard let base = group["cap_base_r"].n, let cap = group["cap_r"].n, abs(base - cap) > 0.001 else { return "" }
        return "（宏观灯：" + num(.number(base)) + "→" + num(.number(cap)) + "）"
    }
    var reading: String { group["stress_loss_r"].n == nil ? "—" : num(group["stress_loss_r"]) + " / " + num(group["cap_r"]) + " R" + capNote }
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(group["factor"].s).font(.callout.weight(.semibold))
                Spacer()
                Text(reading).monospacedDigit().font(.callout)
            }
            RiskScaleBar(value: group["stress_loss_r"].n, lower: lower, upper: upper, cap: group["cap_r"].n, color: PortfolioStyle.risk(group["status"].s))
            Text(label).font(.caption).foregroundStyle(group["uncovered"].items.isEmpty ? Color.secondary : PortfolioStyle.warn)
        }.padding(10).contentShape(Rectangle())
            .background(selected ? Color.blue.opacity(0.08) : Color.clear, in: RoundedRectangle(cornerRadius: 8))
    }
}

struct ScenarioComparison: View {
    let value: J
    var useR: Bool { value["one_r"].n != nil }
    var magnitude: Double { max(0.01, value["scenarios"].items.compactMap { $0[useR ? "r" : "pnl"].n }.map(abs).max() ?? 1) * 1.1 }
    var body: some View {
        PortfolioPanel(title: "同一组合 · 不同市场情景") {
            ForEach(Array(value["scenarios"].items.enumerated()), id: \.offset) { _, row in
                HStack(spacing: 18) {
                    Text(row["label"].s).font(.callout).frame(width: 115, alignment: .leading)
                    RiskScaleBar(value: row[useR ? "r" : "pnl"].n, lower: -magnitude, upper: magnitude)
                    VStack(alignment: .trailing, spacing: 2) {
                        Text(useR ? num(row["r"]) + " R" : usd(row["pnl"])).font(.callout.weight(.medium))
                        if useR { Text(usd(row["pnl"])).font(.caption).foregroundStyle(.secondary) }
                    }.monospacedDigit().frame(width: 115, alignment: .trailing)
                }
            }
            HStack(spacing: 18) { Spacer().frame(width: 115); RiskScaleLabels(lower: -magnitude, upper: magnitude, unit: useR ? "R" : "$"); Spacer().frame(width: 115) }
            Text("参与 \(num(value["coverage"]["used"])) / \(num(value["coverage"]["total"])) 条 · 线性近似，不含 Gamma / Vega / 跳空 / 指派，不是最大损失或 VaR。")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}
