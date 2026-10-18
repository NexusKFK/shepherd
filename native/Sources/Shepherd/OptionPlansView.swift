import SwiftUI

// 期权计划：计划卡（~/tools/option_plans/*.json，用户自己写的 JSON）与账本里已持有的期权，
// 经 workbench/optionviz.py 整理后放进 state["option_plans"]。
// 图上的曲线在界面里用 OptionMath 现算（日期、波动率、价格范围滑块即时生效，鼠标悬停读数），
// Python 只给出腿、成本、波动率这些原料。计划卡只是「可能做的计划」，持仓期权来自账本；
// 两者都不改账本、不进热度。曲线是 Black-Scholes 估算（不含波动率偏斜与提前行权），不是成交价。
struct OptionPlansView: View {
    @EnvironmentObject var model: DeskModel
    @ViewState private var mode = 0
    var plans: J { model.state["option_plans"] }
    var cards: [J] { plans["cards"].items }
    var held: [J] { plans["held"].items }
    var shown: [J] { mode == 0 ? cards : held }
    var emptyCardsNote: String { let dir = plans["dir"].s; return "还没有计划卡。把计划卡 JSON 放进 \(dir) 以后会出现在这里。" }
    var oneRNote: String { let oneR = usd0(plans["one_r"].n); return "1R 取自设置里的定仓净值和单笔风险（现在是 \(oneR)），不随账户每天的市值自动变。" }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                PageHeading(title: "期权计划", subtitle: "把计划卡和已持有的期权画成盈亏图：拖动日期、波动率和价格范围，鼠标移到图上读数")
                Picker("", selection: $mode) {
                    Text("计划卡（\(cards.count)）").tag(0)
                    Text("持仓期权（\(held.count)）").tag(1)
                }.pickerStyle(.segmented).frame(maxWidth: 380)
                if plans["status"].s == "error" || plans["status"].s.isEmpty {
                    Label("期权计划数据不可用", systemImage: "exclamationmark.triangle").font(.callout).foregroundStyle(PortfolioStyle.warn)
                }
                ForEach(Array(plans["notices"].strings.enumerated()), id: \.offset) { _, text in
                    Label(text, systemImage: "info.circle").font(.callout).foregroundStyle(.secondary)
                }
                if shown.isEmpty {
                    Text(mode == 0 ? emptyCardsNote : "账本里没有可以画图的期权持仓。")
                        .font(.callout).foregroundStyle(.secondary).card()
                }
                ForEach(shown, id: \.id) { OptionCard(card: $0, today: plans["today"].s) }
                Notes(values: ["曲线用 Black-Scholes 估算，各腿的隐含波动率由报价或持仓标价反推，不含波动率偏斜和提前行权；实际成交价会有偏差。",
                               oneRNote])
            }.padding(20)
        }
    }
}

struct OptionCard: View {
    let card: J
    let today: String
    let model: OptModel?
    let isHeld: Bool
    @ViewState private var days: Double
    @ViewState private var ivScale = 1.0
    @ViewState private var rangePct: Double
    @ViewState private var metric = 0       // 0 盈亏美元  1 盈亏占成本%  2 合约价值
    @ViewState private var showTable = false
    @ViewState private var count: Int
    @ViewState private var expanded: Bool           // 持仓期权默认只显示标题和汇总，点标题展开；计划卡默认展开

    init(card: J, today: String) {
        self.card = card; self.today = today
        let m = OptModel(card["calc"])
        self.model = m
        self.isHeld = card["source"].s == "held"
        _expanded = ViewState(initialValue: card["source"].s != "held")
        _days = ViewState(initialValue: card["calc"]["default_days"].n ?? 0)
        _count = ViewState(initialValue: Int(card["calc"]["contracts"].n ?? 1))
        var need = 10.0
        if let m {
            let spot = m.spot
            var prices = m.legs.map(\.strike)
            prices += card["levels"].items.compactMap { $0["price"].n }
            prices += card["breakevens"].items.compactMap { $0.n }
            let widest = prices.map { abs($0 / spot - 1) * 100 }.max() ?? 0
            need = min(max(10, (widest * 1.1 / 5).rounded(.up) * 5), 40)
        }
        _rangePct = ViewState(initialValue: need)
    }

    var ok: Bool { card["status"].s == "ok" && model != nil }
    var factor: Double { guard let m = model else { return 1 }; return Double(count) / m.contracts }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header
            if !ok {
                Label(card["error"].s.isEmpty ? "这张卡无法计算" : card["error"].s, systemImage: "exclamationmark.triangle")
                    .font(.callout).foregroundStyle(PortfolioStyle.warn)
            } else if let m = model {
                if expanded, !card["thesis"].s.isEmpty { Text(card["thesis"].s).font(.callout).foregroundStyle(.secondary) }
                metrics(m)
                if expanded {
                    controls(m)
                    if showTable { table(m) } else { chart(m) }
                    legend
                    legsTable
                    greeks
                    if !card["levels"].items.isEmpty { levelsTable(m) }
                    if !card["order_draft"].s.isEmpty { Label(card["order_draft"].s, systemImage: "doc.text").font(.callout) }
                    Notes(values: card["notes"].strings)
                }
            }
        }.card()
    }

    // MARK: 标题与汇总
    var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            if ok {
                Image(systemName: expanded ? "chevron.down" : "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.secondary).frame(width: 12)
            }
            Text(card["name"].s).font(.headline)
            Text(statusText).font(.caption)
                .padding(.horizontal, 7).padding(.vertical, 2)
                .background(statusTint.opacity(0.15), in: Capsule())
            if isHeld, ok, let u = card["unrealized"].n {
                Text("浮盈亏 " + signedUSD(u)).font(.callout.weight(.semibold)).monospacedDigit().foregroundStyle(pnlTint(u))
            }
            if card["stale"].b {
                Text(staleText).font(.caption).foregroundStyle(PortfolioStyle.warn)
            }
            Spacer()
            if ok {
                Text(expiryText).font(.callout).monospacedDigit().foregroundStyle(.secondary)
            }
        }
        .contentShape(Rectangle())
        .onTapGesture { if ok { expanded.toggle() } }
        .help(ok ? (expanded ? "点击收起" : "点击展开") : "")
    }
    // Built outside the view builder: older Swift compilers time out type-checking these inline.
    var statusText: String {
        if isHeld { return "持仓" }
        let s: String = card["plan_status"].s
        return s.isEmpty ? "计划" : s
    }
    var statusTint: Color { isHeld ? Color.secondary : PortfolioStyle.data }
    var staleText: String { "报价 " + num(card["age_days"], digits: 0) + " 天前" }
    var expiryText: String {
        let head: String = card["underlying"].s + " " + num(card["spot"])
        let tail: String = " · 到期 " + shortDay(card["expiry"].s) + "（" + num(card["dte"], digits: 0) + " 天）"
        return head + tail
    }
    func scaled(_ v: J) -> Double? { v.n.map { $0 * factor } }
    func gridCaption(_ title: String) -> String {
        let vol: String = num(.number(ivScale))
        return "标的价格 × 日期（" + title + "；波动率 ×" + vol + "，" + "\(count) 张）"
    }
    func metrics(_ m: OptModel) -> some View {
        let nowPnl = m.pnl(m.spot, days: days, ivScale: ivScale) * factor
        let costNote: String = isHeld ? "浮盈亏 " + signedUSD(card["unrealized"].n)
            : "成交价 " + num(card["entry_price"]) + (card["natural_price"].n.map { "（自然价 " + num(.number($0)) + "）" } ?? "")
        let lossText: String = scaled(card["max_loss"]).map { signedUSD($0) } ?? "无上限"
        let lossNote: String = card["max_loss_r"].n.map { num(.number($0 * factor)) + "R" } ?? card["r_note"].s
        let profitText: String = scaled(card["max_profit"]).map { signedUSD($0) } ?? "无上限"
        let bes: String = card["breakevens"].items.compactMap { $0.n }.map { num(.number($0)) }.joined(separator: " / ")
        return HStack(spacing: 12) {
            Metric(label: isHeld ? "成本（净）" : "净成本", value: signedUSD(m.cost * factor), note: costNote,
                   noteColor: isHeld ? pnlTint(card["unrealized"].n) : nil)
            Metric(label: "最大亏损", value: lossText, note: lossNote, valueColor: .red)
            Metric(label: "最大盈利", value: profitText, note: "到期，价格越过外侧行权价", valueColor: .green)
            Metric(label: "盈亏平衡价（到期）", value: bes, note: unchangedNote(nowPnl), noteColor: pnlTint(nowPnl))
        }
    }

    func unchangedNote(_ pnl: Double) -> String { let day = dayText, value = signedUSD(pnl); return "现价不动、\(day)：\(value)" }

    // MARK: 控件
    var dayText: String {
        guard let m = model else { return "" }
        if days >= m.firstDTE { return "到期" }
        let n = Int(days), date = dateLabel(n)
        return days == 0 ? "今天" : "第 \(n) 天（\(date)）"
    }
    func dateLabel(_ add: Int) -> String {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = TimeZone(identifier: "UTC")
        guard let d = f.date(from: today), let t = Calendar(identifier: .gregorian).date(byAdding: .day, value: add, to: d) else { return "" }
        return shortDay(f.string(from: t))
    }
    func controls(_ m: OptModel) -> some View {
        let presets: [(String, Double)] = [("今天", 0), ("+3 个交易日", 4), ("+5 个交易日", 7), ("到期", m.firstDTE)]
        return VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Text("日期").font(.caption).foregroundStyle(.secondary)
                ForEach(presets, id: \.0) { item in
                    let value = min(item.1, m.firstDTE)
                    Button(item.0) { days = value }.buttonStyle(.bordered).controlSize(.small)
                        .tint(abs(days - value) < 0.5 ? PortfolioStyle.data : nil)
                }
                Spacer()
                Picker("", selection: $metric) {
                    Text("盈亏（美元）").tag(0); Text("盈亏（%）").tag(1); Text("合约价值").tag(2)
                }.pickerStyle(.segmented).labelsHidden().fixedSize()
                Picker("", selection: $showTable) { Text("图").tag(false); Text("表").tag(true) }
                    .pickerStyle(.segmented).labelsHidden().fixedSize()
            }
            HStack(spacing: 22) {
                slider(dateSliderTitle, value: $days, range: 0...max(m.firstDTE, 1), step: 1)
                slider("价格范围 ±" + num(.number(rangePct), digits: 0) + "%", value: $rangePct, range: 5...40, step: 1)
                slider("隐含波动率 " + num(.number(m.averageIV * ivScale * 100), digits: 1) + "%（×" + num(.number(ivScale)) + "）", value: $ivScale, range: 0.5...2.0, step: 0.05)
                if !isHeld {
                    Stepper("张数 \(count)", value: $count, in: 1...10).font(.callout).fixedSize()
                }
            }
        }
    }
    func slider(_ title: String, value: Binding<Double>, range: ClosedRange<Double>, step: Double) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption).foregroundStyle(.secondary).monospacedDigit()
            Slider(value: value, in: range, step: step)
        }.frame(minWidth: 150, maxWidth: .infinity)
    }

    // MARK: 图
    func yValue(_ m: OptModel, _ S: Double, _ d: Double) -> Double {
        let value = m.value(S, days: d, ivScale: ivScale) * factor
        let pnl = value - m.cost * factor
        switch metric {
        case 1: return m.cost == 0 ? 0 : pnl / abs(m.cost * factor) * 100
        case 2: return value
        default: return pnl
        }
    }
    var xDomain: ClosedRange<Double> {
        let s = model?.spot ?? 1
        return (s * (1 - rangePct / 100))...(s * (1 + rangePct / 100))
    }
    func levelColor(_ kind: String) -> Color { optionLevelColor(kind) }
    var dateSliderTitle: String { let day = dayText; return "日期：\(day)" }
    var selectedDateLegend: String { let day = dayText; return "所选日期：\(day)" }
    var selectedDateHead: String { let day = dayText; return "所选日期盈亏（\(day)）" }
    var showSelected: Bool { guard let m = model else { return false }; return days < m.firstDTE }

    func chart(_ m: OptModel) -> some View {
        PayoffChart(model: m,
                    levels: card["levels"].items.compactMap { l in l["price"].n.map { ($0, l["kind"].s) } },
                    breakevens: metric == 0 ? card["breakevens"].items.compactMap { $0.n } : [],
                    days: days, ivScale: ivScale, factor: factor, rangePct: rangePct, metric: metric,
                    dayText: dayText, name: card["name"].s)
    }
    var legend: some View {
        let kinds: [(String, String, Color)] = [("invalid", "作废线", Color.red), ("target", "目标", Color.green), ("entry", "入场区", PortfolioStyle.data)]
        return HStack(spacing: 14) {
            legendItem(Color.primary, "到期")
            if showSelected { legendItem(Color.orange, selectedDateLegend) }
            ForEach(kinds, id: \.0) { item in
                if card["levels"].items.contains(where: { $0["kind"].s == item.0 }) { legendItem(item.2.opacity(0.8), item.1) }
            }
        }
    }
    func legendItem(_ color: Color, _ text: String) -> some View {
        HStack(spacing: 5) { RoundedRectangle(cornerRadius: 1).fill(color).frame(width: 16, height: 3); Text(text).font(.caption).foregroundStyle(.secondary) }
    }

    // MARK: 表（价格 × 日期，颜色深浅按盈亏大小）
    func table(_ m: OptModel) -> some View {
        let d = xDomain
        let width = d.upperBound - d.lowerBound
        let rough = width / 12
        let step: Double = [0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100].first(where: { $0 >= rough }) ?? 100
        var rows: [Double] = []
        var p = (d.lowerBound / step).rounded(.up) * step
        while p <= d.upperBound { rows.append(p); p += step }
        if !rows.contains(where: { abs($0 - m.spot) < step * 0.5 }) { rows.append(m.spot) }
        let prices: [Double] = rows.sorted().reversed()
        var cols: [Double] = [0, 1, 2, 3, 5, 7, 10, 14, 21, 30, 45].filter { $0 < m.firstDTE }
        cols.append(m.firstDTE)
        cols = Array(cols.suffix(9))
        let all: [Double] = prices.flatMap { p in cols.map { yValue(m, p, $0) } }
        let big = max(all.map { abs($0) }.max() ?? 1, 1)
        let title = ["盈亏，美元", "盈亏，占成本 %", "合约价值，美元"][metric]
        return VStack(alignment: .leading, spacing: 6) {
            Text(gridCaption(title)).font(.caption).foregroundStyle(.secondary)
            Grid(horizontalSpacing: 4, verticalSpacing: 3) {
                GridRow {
                    Text("标的价").font(.caption).foregroundStyle(.secondary).gridColumnAlignment(.leading)
                    ForEach(cols, id: \.self) { c in
                        let isNow = abs(c - days) < 0.5
                        Text(c >= m.firstDTE ? "到期" : (c == 0 ? "今天" : dateLabel(Int(c)))).font(.caption.weight(isNow ? .bold : .regular))
                            .foregroundStyle(isNow ? Color.orange : Color.secondary).frame(minWidth: 62)
                    }
                }
                ForEach(prices, id: \.self) { p in
                    GridRow {
                        Text(num(.number(p))).font(.callout.weight(abs(p - m.spot) < 1e-9 ? .bold : .regular)).monospacedDigit().gridColumnAlignment(.leading)
                        ForEach(cols, id: \.self) { c in
                            let v = yValue(m, p, c)
                            let text: String = metric == 1 ? num(.number(v), digits: 0) + "%" : (metric == 2 ? num(.number(v), digits: 0) : signedUSD(v))
                            let tint: Color = metric == 2 ? Color.blue : (v >= 0 ? Color.green : Color.red)
                            Text(text).font(.caption).monospacedDigit().frame(minWidth: 62).padding(.vertical, 3)
                                .background(tint.opacity(min(abs(v) / big, 1) * 0.5), in: RoundedRectangle(cornerRadius: 4))
                        }
                    }
                }
            }
        }
    }

    // MARK: 腿、希腊值、价位
    func cell(_ text: String, bold: Bool = false) -> some View { Text(text).font(bold ? .callout.weight(.medium) : .callout).monospacedDigit() }
    var legsTable: some View {
        Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 5) {
            GridRow { ForEach(["方向", "类型", "行权价", "到期", "张数"] + (isHeld ? ["开仓价", "现价", "浮盈亏"] : ["买价 / 卖价"]) + ["隐含波动率"], id: \.self) { Text($0).font(.caption).foregroundStyle(.secondary) } }
            ForEach(Array(card["legs"].items.enumerated()), id: \.offset) { _, l in
                GridRow {
                    cell(l["side"].s == "buy" ? "买" : "卖", bold: true)
                    cell(l["right"].s == "C" ? "看涨" : "看跌")
                    cell(num(l["strike"]))
                    cell(shortDay(l["expiry"].s))
                    cell(num(.number((l["qty"].n ?? 0) * factor), digits: 0))
                    if isHeld {
                        cell(l["open"].n == nil ? "—" : num(l["open"]))
                        cell(l["mark"].n == nil ? "—" : num(l["mark"]))
                        cell(signedUSD(l["pnl_usd"].n)).foregroundStyle(pnlTint(l["pnl_usd"].n))
                    } else {
                        cell(l["bid"].n == nil ? "—" : num(l["bid"]) + " / " + num(l["ask"]))
                    }
                    cell(num(.number((l["iv"].n ?? 0) * 100), digits: 1) + "%").help(l["iv_source"].s)
                }
            }
        }
    }
    var greeks: some View {
        let g = card["greeks"]
        return HStack(spacing: 18) {
            Text("Delta 相当 " + num(.number((g["delta_shares"].n ?? 0) * factor), digits: 1) + " 股（" + usd0((g["delta_usd"].n ?? 0) * factor) + "）")
            Text("每天时间损耗 " + signedUSD((g["theta_usd_day"].n ?? 0) * factor))
            Text("波动率每升 1 点 " + signedUSD((g["vega_usd_pt"].n ?? 0) * factor))
            if card["friction_usd"].n != nil {
                Text("往返点差摩擦约 " + usd0((card["friction_usd"].n ?? 0) * factor) + "（" + num(card["friction_pct"]) + "% 的净成本）")
            }
        }.font(.caption).foregroundStyle(.secondary).monospacedDigit()
    }
    func levelsTable(_ m: OptModel) -> some View {
        let heads = ["价位", "含义", "到期时盈亏", selectedDateHead]
        return Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 5) {
            GridRow { ForEach(heads, id: \.self) { Text($0).font(.caption).foregroundStyle(.secondary) } }
            ForEach(Array(card["levels"].items.enumerated()), id: \.offset) { _, l in
                let price = l["price"].n ?? 0
                GridRow {
                    cell(num(l["price"]), bold: true).foregroundStyle(levelColor(l["kind"].s))
                    Text(l["label"].s).font(.callout)
                    let atExpiry = m.pnl(price, days: m.firstDTE, ivScale: ivScale) * factor, atDay = m.pnl(price, days: days, ivScale: ivScale) * factor
                    cell(signedUSD(atExpiry)).foregroundStyle(pnlTint(atExpiry))
                    cell(signedUSD(atDay)).foregroundStyle(pnlTint(atDay))
                }
            }
        }
    }
}

extension OptModel {
    /// Reads the "calc" block of a card; nil (and the card shows its error) if anything needed is missing.
    init?(_ calc: J) {
        guard let spot = calc["spot"].n, let cost = calc["cost"].n, let first = calc["first_dte"].n, !calc["legs"].items.isEmpty else { return nil }
        var parsed: [OptLeg] = []
        for l in calc["legs"].items {
            guard let k = l["strike"].n, let d = l["dte"].n, let q = l["qty"].n, let iv = l["iv"].n else { return nil }
            parsed.append(OptLeg(right: l["right"].s, strike: k, dte: d, qty: q, mult: l["mult"].n ?? 100, iv: iv))
        }
        self.init(spot: spot, cost: cost, contracts: calc["contracts"].n ?? 1, firstDTE: first, legs: parsed)
    }
}
