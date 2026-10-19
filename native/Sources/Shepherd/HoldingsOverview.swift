import SwiftUI
import Charts

struct HoldingDisplayRow: Identifiable {
    let position: J
    var children: [HoldingDisplayRow]?
    var delta: Double?
    var absDelta = 0.0          // combos sum their legs so they sort with the book, not at the bottom
    var nlvPct: Double?
    var pnl: Double?
    var pnlPct: Double?
    var days: Int?
    var id: String { position.id }
    var sym: String { position["sym"].s }
    var pnlSort: Double { pnl ?? -.greatestFiniteMagnitude }
}

struct HoldingsView: View {
    @EnvironmentObject var model: DeskModel
    @ViewState private var search = ""
    @ViewState private var account = "全部账户"
    @ViewState private var selected: String?
    @ViewState private var grouped = true
    @ViewState private var sortOrder = [KeyPathComparator(\HoldingDisplayRow.absDelta, order: .reverse)]
    var selectedRow: J? { model.positions.first { $0.id == selected } }
    var exposureRows: [J] { model.state["exposure"]["rows"].items }
    var accounts: [String] { Array(Set(model.positions.map { $0["broker"].s })).sorted() }
    var summaryCount: Int { model.positions.filter { $0["display_only"].b }.count }
    /// Sum over measured rows only; combo summaries repeat their legs and are skipped.
    var totalPnl: Double? {
        let values = model.positions.filter { !$0["display_only"].b }.compactMap(unrealized)
        return values.isEmpty ? nil : values.reduce(0, +)
    }
    func matches(_ p: J) -> Bool {
        (account == "全部账户" || p["broker"].s == account) &&
        (search.isEmpty || (p["sym"].s + " " + p["label"].s + " " + p["group"].s).localizedCaseInsensitiveContains(search))
    }
    func make(_ p: J, children: [HoldingDisplayRow]? = nil) -> HoldingDisplayRow {
        let e = exposure(p)
        var row = HoldingDisplayRow(position: p, children: children)
        row.delta = p["display_only"].b ? nil : e["dollar_delta"].n
        row.absDelta = row.delta.map(abs) ?? (children ?? []).reduce(0) { $0 + $1.absDelta }
        row.nlvPct = p["display_only"].b ? nil : e["nlv_pct"].n
        row.pnl = unrealized(p); row.pnlPct = unrealizedPct(p)
        row.days = p["expiry"].s.isEmpty ? nil : daysBetween(model.day, p["expiry"].s)
        return row
    }
    var nodes: [HoldingDisplayRow] {
        let raw = model.positions
        if !grouped { return raw.filter(matches).map { make($0) }.sorted(using: sortOrder) }
        var childIDs = Set<String>()
        var parents: [String: [HoldingDisplayRow]] = [:]
        for p in raw where p["display_only"].b {
            let ids = Set(p["component_ids"].strings)
            let children = raw.filter { ids.contains($0.id) && !childIDs.contains($0.id) }
            if !children.isEmpty { parents[p.id] = children.map { make($0) }; childIDs.formUnion(children.map(\.id)) }
        }
        return raw.filter { !childIDs.contains($0.id) }.compactMap { p -> HoldingDisplayRow? in
            let children = parents[p.id]
            if matches(p) { return make(p, children: children) }
            let filtered = children?.filter { matches($0.position) } ?? []
            return filtered.isEmpty ? nil : make(p, children: filtered)
        }.sorted(using: sortOrder)
    }
    var visibleIDs: Set<String> { Set(nodes.flatMap { [$0.id] + ($0.children?.map(\.id) ?? []) }) }
    func exposure(_ p: J) -> J { exposureRows.first { $0.id == p.id } ?? .null }
    /// Only exceptions get a badge; a healthy snapshot row says nothing.
    func flag(_ p: J) -> (text: String, warn: Bool)? {
        if p["display_only"].b { return ("汇总", false) }
        if p["confirm"].b { return ("待核对", true) }
        if !exposure(p)["issues"].items.isEmpty { return ("数据待补", true) }
        return nil
    }
    var body: some View {
        GeometryReader { geometry in
            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .top) {
                    PageHeading(title: "持仓", subtitle: "账户、合约与风险资料集中查看")
                    VStack(alignment: .trailing, spacing: 4) {
                        Text(usd(model.state["snapshot"]["nlv"])).font(.title2.weight(.semibold)).monospacedDigit()
                        Text("快照净值 · " + model.state["snapshot"]["day"].s).font(.caption).foregroundStyle(.secondary)
                    }
                }
                HStack(spacing: 18) {
                    Label("\(model.positions.count - summaryCount) 条持仓记录", systemImage: "square.stack.3d.up")
                    Text("\(summaryCount) 条组合汇总")
                    Text("\(model.plans.filter { $0["status"].s == "holding" }.count) 笔已接入账本")
                    Spacer(minLength: 0)
                    if let totalPnl {
                        Text("浮盈合计 " + signedUSD(totalPnl)).monospacedDigit().foregroundStyle(pnlColor(totalPnl))
                            .help("开仓成本对快照价，不是今日盈亏；期权按乘数计")
                    }
                }.font(.callout).foregroundStyle(.secondary)
                HStack(spacing: 12) {
                    TextField("搜索标的、合约或组合", text: $search).textFieldStyle(.roundedBorder).frame(maxWidth: 300)
                    Picker("账户", selection: $account) { Text("全部账户").tag("全部账户"); ForEach(accounts, id: \.self) { Text($0).tag($0) } }.frame(width: 185)
                    Spacer(minLength: 0)
                    Picker("显示", selection: $grouped) { Text("组合展开").tag(true); Text("全部记录").tag(false) }.pickerStyle(.segmented).labelsHidden().frame(width: 180)
                }
                if model.state["snapshot"]["status"].s != "available" || model.state["snapshot"]["cached"].b {
                    Label("当前显示历史持仓，请核对来源日期。", systemImage: "exclamationmark.triangle.fill").font(.callout).foregroundStyle(PortfolioStyle.warn)
                }
                if !model.state["snapshot"]["errors"].items.isEmpty || !model.state["conflicts"].items.isEmpty {
                    Notes(values: model.state["snapshot"]["errors"].strings + model.state["conflicts"].strings)
                }
                if geometry.size.width >= 1110, let p = selectedRow {
                    HStack(alignment: .top, spacing: 16) {
                        holdingsTable
                        ScrollView { HoldingInspector(position: p, onClose: { selected = nil }).environmentObject(model) }.frame(width: 310)
                    }
                } else {
                    // Narrow window: the detail floats over the right side of the table, so the table and footer keep their size.
                    holdingsTable.overlay(alignment: .trailing) {
                        if let p = selectedRow {
                            ScrollView { HoldingInspector(position: p, onClose: { selected = nil }).environmentObject(model) }
                                .frame(width: 340)
                                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
                                .shadow(color: .black.opacity(0.35), radius: 14, x: -4, y: 0)
                                .padding(.vertical, 6)
                                .transition(.move(edge: .trailing))
                        }
                    }.animation(.easeOut(duration: 0.15), value: selected)
                }
                HStack {
                    Text(nodes.isEmpty ? "没有匹配的持仓" : "点选查看详情 · 点表头排序 · 展开组合查看合约腿")
                    Spacer()
                    Text("浮盈按快照价 · 组合汇总不重复计量")
                }.font(.caption).foregroundStyle(.secondary)
            }.padding(24)
                .onKeyPress(.escape) { if selected == nil { return .ignored }; selected = nil; return .handled }
                .onChange(of: search) { _, _ in clearHiddenSelection() }
                .onChange(of: account) { _, _ in clearHiddenSelection() }
                .onChange(of: model.state["generated_at"].s) { _, _ in clearHiddenSelection() }
        }
    }
    func clearHiddenSelection() { if let selected, !visibleIDs.contains(selected) { self.selected = nil } }
    func pnlColor(_ value: Double?) -> Color { pnlTint(value) }
    var holdingsTable: some View {
        Table(nodes, children: \.children, selection: $selected, sortOrder: $sortOrder) {
            TableColumn(L("持仓 / 账户"), value: \.sym) { row in
                VStack(alignment: .leading, spacing: 3) {
                    Text(row.position["sym"].s).font(.callout.weight(.semibold))
                    Text(row.position["broker"].s).font(.caption).foregroundStyle(.secondary)
                }.padding(.vertical, 4)
            }.width(min: 90, ideal: 105)
            TableColumn(L("合约 / 到期")) { row in
                VStack(alignment: .leading, spacing: 3) {
                    Text(row.position["label"].s.isEmpty ? "股票 / ETF" : row.position["label"].s).lineLimit(2).help(row.position["label"].s)
                    if row.position["display_only"].b { Text("\(row.position["component_ids"].items.count) 条合约腿").font(.caption).foregroundStyle(.secondary) }
                    else if let days = row.days {
                        Text("到期 " + shortDay(row.position["expiry"].s) + " · " + countdown(days)).font(.caption)
                            .foregroundStyle(days <= 1 ? Color.red : days <= 7 ? PortfolioStyle.warn : Color.secondary)
                    }
                }
            }.width(min: 120, ideal: 175)
            TableColumn(L("数量")) { row in
                VStack(alignment: .trailing, spacing: 3) {
                    Text(num(row.position["qty"])).monospacedDigit()
                    Text(row.position["side"].s == "short" ? "空头" : "多头").font(.caption).foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity, alignment: .trailing)
            }.width(54)
            TableColumn(L("成本 / 快照价")) { row in
                VStack(alignment: .trailing, spacing: 3) {
                    HStack(spacing: 3) {
                        if row.position["mark_stale"].b {
                            Text("旧价").font(.caption2).foregroundStyle(PortfolioStyle.warn)
                        }
                        Text(num(row.position["mark"], digits: 3)).monospacedDigit()
                    }
                    Text("成本 " + num(row.position["open"], digits: 3)).font(.caption).monospacedDigit().foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity, alignment: .trailing)
                .help(row.position["mark_stale"].b ? "本轮没取到价，沿用账本：" + row.position["mark_missing_reason"].s
                      : row.position["mark_source"].s.isEmpty ? "来源未标注" : row.position["mark_source"].s)
            }.width(min: 90, ideal: 102)
            TableColumn(L("浮盈"), value: \.pnlSort) { row in
                VStack(alignment: .trailing, spacing: 3) {
                    Text(signedUSD(row.pnl)).monospacedDigit().foregroundStyle(pnlColor(row.pnl))
                    Text(signedPct(row.pnlPct))
                        .font(.caption).monospacedDigit().foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity, alignment: .trailing)
            }.width(min: 80, ideal: 92)
            TableColumn(L("Delta 美元"), value: \.absDelta) { row in
                VStack(alignment: .trailing, spacing: 3) {
                    Text(row.position["display_only"].b ? "见合约腿" : usd0(row.delta))
                        .monospacedDigit().foregroundStyle(row.position["display_only"].b ? Color.secondary : .primary)
                    if let pct = row.nlvPct { Text(num(.number(pct), digits: 1) + "% 净值").font(.caption).monospacedDigit().foregroundStyle(.secondary) }
                }.frame(maxWidth: .infinity, alignment: .trailing)
            }.width(min: 96, ideal: 112)
            TableColumn(L("状态")) { row in
                if let flag = flag(row.position) {
                    Text(flag.text).font(.caption).foregroundStyle(flag.warn ? PortfolioStyle.warn : Color.secondary)
                        .help(exposure(row.position)["issues"].strings.joined(separator: "；"))
                }
            }.width(58)
        }.frame(minHeight: 200)
    }
}

struct HoldingInspector: View {
    let position: J
    var onClose: () -> Void = {}
    @EnvironmentObject var model: DeskModel
    var exposure: J { model.state["exposure"]["rows"].items.first { $0.id == position.id } ?? .null }
    var heat: J { model.state["exposure"]["portfolio_heat"] }
    var heatItem: J? { heat["items"].items.first { $0.id.split(separator: ",").map(String.init).contains(position.id) } }
    var body: some View {
        VStack(alignment: .leading, spacing: 13) {
            HStack { Text(position["sym"].s).font(.title2.weight(.semibold)); Spacer(); Text(position["broker"].s).font(.callout).foregroundStyle(.secondary)
                Button(action: onClose) { Image(systemName: "xmark.circle.fill").font(.title3).foregroundStyle(.secondary) }
                    .buttonStyle(.plain).keyboardShortcut(.cancelAction).help("关闭详情（Esc）") }
            Text(position["label"].s).font(.headline)
            if position["display_only"].b {
                Label(position["summary_note"].s, systemImage: "square.stack.3d.up").font(.callout).foregroundStyle(.secondary)
                Text("该行的成本与价格保留原始快照口径；敞口和热度请查看合约腿。").font(.caption).foregroundStyle(.secondary)
            } else {
                HStack {
                    VStack(alignment: .leading, spacing: 4) { Text("Delta 美元").font(.caption).foregroundStyle(.secondary); Text(usd(exposure["dollar_delta"])).font(.headline).monospacedDigit() }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 4) {
                        Text(heatItem?.id.contains(",") == true ? "所属组合热度" : "当前热度").font(.caption).foregroundStyle(.secondary)
                        Text(heatItem.map { num($0["heat_r"]) + " R" } ?? "未计入").font(.headline).monospacedDigit()
                    }
                }
                if let item = heatItem { Text(item["method"].s).font(.caption).foregroundStyle(.secondary) }
                if let item = heatItem, !stopCostLine(item).isEmpty { Text(stopCostLine(item)).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
                else if heat["no_stop"].items.contains(where: { $0.id == position.id }) { Text("无止损正股单列市值，不计入热度。").font(.caption).foregroundStyle(PortfolioStyle.warn) }
                else if heat["exempt"].items.contains(where: { $0.id == position.id }) { Text("免判据持仓：你裁定不设止损，不计入热度。").font(.caption).foregroundStyle(.secondary) }
                else { Notes(values: (heat["unknown"].items + heat["unbounded"].items).filter { $0.id.split(separator: ",").map(String.init).contains(position.id) }.map { $0["reason"].s }) }
                if position["kind"].s == "stock" { PositionPriceLevels(position: position) }
                if !position["expiry"].s.isEmpty { Label("到期 " + position["expiry"].s, systemImage: "calendar").font(.callout) }
                Notes(values: exposure["issues"].strings)
                if !exposure["price_asof"].s.isEmpty { Text("风险价格日期 " + exposure["price_asof"].s).font(.caption).foregroundStyle(.secondary) }
            }
            Divider()
            VStack(alignment: .leading, spacing: 8) {
                if !position["display_only"].b { Button("补充 Delta / 批次") { model.editor = .exposure(position, state: model.state) } }
                if position["source"].s == "local" { Button("查看交易记录") { model.inspectingPlan = position["plan_id"].s } }
                else if !position["display_only"].b { Button("核对并接入账本") { model.editor = .adopt(position, day: model.day) } }
            }
            DisclosureGroup("原始备注与来源") {
                Notes(values: [position["reconcile_note"].s, position["tailNote"].s].filter { !$0.isEmpty })
                Text(model.state["snapshot"]["asof"].s).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            }
        }.padding(16).frame(maxWidth: .infinity, alignment: .leading)
            .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 12))
    }
}

struct PositionPriceLevels: View {
    let position: J
    struct Level: Identifiable { let id: String; let price: Double }
    var levels: [Level] {
        var result: [Level] = []
        if let price = position["open"].n { result.append(Level(id: "成本", price: price)) }
        if let price = position["mark"].n { result.append(Level(id: "快照价", price: price)) }
        for (index, trigger) in position["trigs"].items.enumerated() where trigger["kind"].s == "止损" && ["und", "mark"].contains(trigger["basis"].s) {
            if let price = trigger["level"].n { result.append(Level(id: "止损 \(index + 1)", price: price)) }
        }
        return result
    }
    var domain: ClosedRange<Double> {
        let low = levels.map(\.price).min() ?? 0, high = levels.map(\.price).max() ?? 1
        let padding = max(1, (high - low) * 0.12)
        return (low - padding)...(high + padding)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("价格与止损位置").font(.caption).foregroundStyle(.secondary)
            Chart(levels) { level in
                PointMark(x: .value("价格", level.price), y: .value("类型", L(level.id)))
                    .symbolSize(45).foregroundStyle(level.id.hasPrefix("止损") ? PortfolioStyle.warn : .blue)
                    .accessibilityLabel(L(level.id)).accessibilityValue(num(.number(level.price)))
            }.chartXScale(domain: domain).frame(height: CGFloat(max(2, levels.count)) * 27 + 30)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 115), alignment: .leading)], alignment: .leading, spacing: 4) {
                ForEach(levels) { level in Text(L(level.id) + " " + num(.number(level.price))).font(.caption2).monospacedDigit() }
            }
        }
    }
}
