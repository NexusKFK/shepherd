import SwiftUI
import Charts

struct RiskModelDetails:View {
    @EnvironmentObject var model:DeskModel
    @ViewState private var selected:String?
    var exposure:J{model.state["exposure"]}
    var summary:J{exposure["summary"]}
    var coverage:J{exposure["coverage"]}
    var risk:J{model.state["risk"]}
    var body:some View {ScrollView{VStack(alignment:.leading,spacing:22){
        Panel(title:"科技与行业集中提醒 · 不限制开仓"){
            Text("占比按已覆盖持仓的绝对 Delta 美元计算，缺失持仓不按零风险处理。") .font(.callout).foregroundStyle(.secondary)
            LazyVGrid(columns:[GridItem(.adaptive(minimum:180))],spacing:12){ForEach(Array(exposure["concentrations"].items.enumerated()),id:\.offset){_,c in Metric(label:c["label"].s,value:pct(c["gross_share_pct"]),note:c["kind"].s=="theme" ? "共同主题":"行业")}}
            DisclosureGroup("计算边界与资料缺口"){Notes(values:exposure["notices"].strings)}
        }
        Panel(title:"逐仓 Delta 与 Beta"){
            Table(exposure["rows"].items,selection:$selected){
                TableColumn("标的 / 账户"){p in VStack(alignment:.leading){Text(p["symbol"].s).bold();Text(p["broker"].s).font(.caption).foregroundStyle(.secondary)}}.width(min:80,ideal:100)
                TableColumn("Delta 美元"){p in VStack(alignment:.leading){Text(usd(p["dollar_delta"])).monospacedDigit();Text("Delta "+num(p["position_delta"])).font(.caption)}}.width(min:100,ideal:120)
                TableColumn("净值 / 权重"){p in VStack(alignment:.leading){Text(pct(p["nlv_pct"]));Text("绝对 "+pct(p["gross_share_pct"])).font(.caption)}}.width(min:90,ideal:100)
                TableColumn("Beta / R²"){p in VStack(alignment:.leading){Text(num(p["beta"]));Text("R² "+num(p["r_squared"])).font(.caption)}}.width(85)
                TableColumn("年化波动"){p in Text(pct(p["annualized_vol_pct"]))}.width(90)
                TableColumn("时间 / 缺口"){p in VStack(alignment:.leading){Text(p["price_asof"].s);Text(p["issues"].strings.joined(separator:"；")).font(.caption).foregroundStyle(PortfolioStyle.warn).lineLimit(2)}}.width(min:180,ideal:250)
            }.frame(height:420)
            if let p=model.positions.first(where:{$0.id==selected}){HStack{Text(p["sym"].s+" · "+p["label"].s);Spacer();Button("补充风险数据"){model.editor = .exposure(p,state:model.state)}}}
            Text(exposure["method"].s).font(.caption).foregroundStyle(.secondary)
        }
        DisclosureGroup("期权策略与操作批次") { StrategyPanel() }
        Panel(title:"历史损失情景 · 股票子组合"){
            HStack{Metric(label:"1 日 VaR 95%",value:usd(risk["var95"]));Metric(label:"VaR 99%",value:usd(risk["var99"]));Metric(label:"ES 95%",value:usd(risk["es95"]));Metric(label:"覆盖",value:"\(risk["covered"].items.count) / \(model.positions.count)")}
            LossHistogram(risk: risk)
            Notes(values:risk["errors"].strings + ["历史模拟不是最大亏损。未覆盖期权非线性、跳空与流动性风险；全组合 VaR 是否可用取决于完整估值数据。"])
        }
    }.padding(24)}}
}
struct MarketScenarioPanel:View {
    let value:J
    func rText(_ x:J)->String{x["r"].n == nil ? (x["r_note"].s.isEmpty ? "R 未知":x["r_note"].s) : num(x["r"])+"R"}
    var body:some View {Panel(title:"大盘情景 · 按 R 读"){
        HStack{
            Metric(label:"Beta 加权净敞口",value:usd(value["beta_net_dollar_delta"]),note:"占快照净值 "+pct(value["beta_net_nlv_pct"])+" · 约 100% ≈ 满仓持有 "+(value["proxy"].b ? "SPY":"SPX"))
            Metric(label:"绝对 Delta 美元（不按 Beta）",value:usd(value["gross_dollar_delta"]),note:"对照 · 同一参与子集 · 占快照净值 "+pct(value["gross_nlv_pct"]))
        }
        HStack{ForEach(Array(value["scenarios"].items.enumerated()),id:\.offset){_,x in Metric(label:x["label"].s,value:usd(x["pnl"]),note:rText(x)+" · 线性估算")}}
        Text("参与 \(num(value["coverage"]["used"])) / \(num(value["coverage"]["total"])) 条仓位"+(value["coverage"]["subset"].b ? " · 仅覆盖子集":"")).font(.callout).foregroundStyle(PortfolioStyle.warn)
        Text(value["one_r"].n == nil ? "R 分母：" + value["r_note"].s : "R 分母：全局定仓 1R "+usd(value["one_r"])+"（定仓净值 "+usd(value["sizing_nlv"])+" × "+num(value["risk_pct"])+"%）· 占比分母：账户快照净值 "+usd(value["snapshot_nlv"])).font(.caption).foregroundStyle(.secondary)
        if !value["low_r_squared"].items.isEmpty {DisclosureGroup("\(value["low_r_squared"].items.count) 个低 R² 标的 · Beta 加权会低估这些标的的自身波动"){
            ForEach(Array(value["low_r_squared"].items.enumerated()),id:\.offset){_,r in HStack{Text(r["symbol"].s+" · "+r["broker"].s).font(.callout);Spacer();Text("R² "+num(r["r_squared"])+" · Beta "+num(r["beta"])+" · 年化波动 "+pct(r["annualized_vol_pct"])).font(.caption).monospacedDigit()}}
        }}
        if !value["coverage"]["excluded"].items.isEmpty {DisclosureGroup("\(value["coverage"]["excluded"].items.count) 条仓位未参与（缺 Delta 或缺 Beta，不当作零）"){
            ForEach(Array(value["coverage"]["excluded"].items.enumerated()),id:\.offset){_,r in VStack(alignment:.leading){Text(r["symbol"].s+" · "+r["broker"].s+" · "+r["missing"].s).font(.callout);Text(r["reason"].s).font(.caption).foregroundStyle(PortfolioStyle.warn).lineLimit(2)}}
        }}
        Notes(values:value["notices"].strings)
    }}
}
/// One line saying where a stock stop sits against the cost basis. The heat itself is measured from the current price.
func stopCostLine(_ x: J) -> String {
    guard let r = x["cost_result_usd"].n else { return "" }
    var s = r >= 0 ? "止损在成本之上：被打掉仍盈利 " + usd(.number(r)) + "（已锁定）" : "止损在成本之下：被打掉相对成本亏 " + usd(.number(-r))
    s += "；从现价回吐 " + usd(x["heat_usd"])
    if let risk = x["initial_risk_usd"].n, let m = x["stop_multiple"].n {
        s += "。初始止损 " + num(x["level0"]) + "，初始风险 " + usd(.number(risk)) + "，止损触发时为 " + unicodeMinus(String(format: "%+.2f", m)) + " 倍"
        if let o = x["open_multiple"].n { s += "，现浮盈 " + unicodeMinus(String(format: "%+.2f", o)) + " 倍" }
    } else if x["level0"].n != nil {
        s += "。第一次设的止损已在成本之上，没有初始风险，不算倍数"
    }
    if let n = x["moves"].n, n > 0 { s += "；已上移 \(Int(n)) 次" }
    return s
}
struct PortfolioHeatPanel:View {
    let value:J
    var tone:Color{value["status"].s=="over" ? .red : ["near", "partial"].contains(value["status"].s) ? PortfolioStyle.warn : .blue}
    var body:some View {Panel(title:"热度明细 · 从当前价起算"){
        HStack{
            Metric(label:"已用热度",value:value["heat_r"].n == nil ? "R 未知" : num(value["heat_r"])+"R / "+num(value["cap_r"])+"R",note:usd(value["heat_usd"])+" · 从当前价起算")
            Metric(label:"已覆盖口径余量",value:value["headroom_r"].n == nil ? "—" : num(value["headroom_r"])+"R",note:value["one_r"].n == nil ? "未确认净值" : "≈ "+usd(J.number((value["headroom_r"].n ?? 0)*(value["one_r"].n ?? 0)))+" 止损风险")
            Metric(label:"止损已锁定盈利",value:usd(value["locked_usd"]),note:"止损在成本之上的正股 · 另有 "+usd(value["loss_risk_usd"])+" 的止损仍在成本之下")
            Metric(label:"无止损正股",value:usd(value["no_stop_usd"]),note:value["no_stop"].items.isEmpty ? "没有未处理的" : "\(value["no_stop"].items.count) 只 · 只计跳空压力，不计热度")
            Metric(label:"免判据持仓",value:usd(value["exempt_usd"]),note:"\(value["exempt"].items.count) 只 · 你裁定不设止损，不计热度")
        }
        if let used=value["heat_r"].n, let cap=value["cap_r"].n, cap > 0 {
            Gauge(value:max(0,min(used,cap)),in:0...cap){EmptyView()}.gaugeStyle(.accessoryLinearCapacity).tint(tone)
                .accessibilityLabel("总热度").accessibilityValue(num(value["heat_r"])+"R / "+num(value["cap_r"])+"R")
        }
        DisclosureGroup("\(value["items"].items.count) 笔计入热度"){ForEach(Array(value["items"].items.enumerated()),id:\.offset){_,x in VStack(alignment:.leading,spacing:2){
            HStack{Text(x["symbol"].s+" · "+x["broker"].s+" · "+x["label"].s).font(.callout);Spacer();Text(usd(x["heat_usd"])+" · "+num(x["heat_r"])+"R").font(.callout).monospacedDigit()}
            Text(x["method"].s+(x["stop"].s.isEmpty ? "" : " · "+x["stop"].s)).font(.caption).foregroundStyle(.secondary)
            if !stopCostLine(x).isEmpty {Text(stopCostLine(x)).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)}
            if !x["flag"].s.isEmpty {Text(x["flag"].s).font(.caption).foregroundStyle(.red)}
        }}}
        if !value["no_stop"].items.isEmpty {DisclosureGroup("\(value["no_stop"].items.count) 只无止损正股 · 不计热度"){ForEach(Array(value["no_stop"].items.enumerated()),id:\.offset){_,x in
            HStack{Text(x["symbol"].s+" · "+x["broker"].s+(x["group"].s.isEmpty ? "" : " · "+x["group"].s)).font(.callout);Spacer();Text(usd(x["value_usd"])).font(.callout).monospacedDigit()}
        }}}
        if !value["exempt"].items.isEmpty {DisclosureGroup("\(value["exempt"].items.count) 只免判据持仓 · 不设止损，不计热度"){ForEach(Array(value["exempt"].items.enumerated()),id:\.offset){_,x in
            HStack{Text(x["symbol"].s+" · "+x["broker"].s+(x["group"].s.isEmpty ? "" : " · "+x["group"].s)).font(.callout);Spacer();Text(usd(x["value_usd"])).font(.callout).monospacedDigit()}
        }}}
        ForEach(Array((value["unbounded"].items+value["unknown"].items).enumerated()),id:\.offset){_,x in Text("未计入 "+x["symbol"].s+" · "+x["broker"].s+" · "+x["label"].s+"："+x["reason"].s).font(.caption).foregroundStyle(PortfolioStyle.warn).lineLimit(2)}
        Notes(values:value["notices"].strings)
    }}
}
struct FactorTechnicalDetails:View {
    let value:J
    func tone(_ status:String)->Color{status=="over" ? .red : status=="near" ? PortfolioStyle.warn : .secondary}
    func stateText(_ status:String)->String{["over":"已超上限","near":"接近上限（≥75%）","ok":"上限以内","r_unavailable":"未确认净值，无法折算 R","unclustered":"未分簇 · 不计压力"][status] ?? status}
    var body:some View {Panel(title:"大类跳空压力 · 按 R 读 · 只提示不拦截"){
        Text("大类（科技 / 非科技 / 黄金 / 利率 / 宽基）设上限，子项各用自己的代理与冲击后相加；压力 = 代理 ETF 历史最差单日 × 敏感度 × Delta 美元，模拟隔夜跳空、止损来不及成交。").font(.callout).foregroundStyle(.secondary)

        DisclosureGroup("逐大类明细 · 子项、成员、敏感度与未计入仓位"){
        LazyVGrid(columns:[GridItem(.adaptive(minimum:260))],spacing:12){ForEach(Array(value["clusters"].items.enumerated()),id:\.offset){_,g in
            VStack(alignment:.leading,spacing:6){
                HStack{Text(g["factor"].s).font(.headline);Spacer();Text(stateText(g["status"].s)).font(.caption).foregroundStyle(tone(g["status"].s))}
                Text(g["stress_loss_r"].n == nil ? "—" : num(g["stress_loss_r"])+"R"+(g["cap_r"].n == nil ? "" : " / 上限 "+num(g["cap_r"])+"R")).font(.title2.weight(.semibold)).monospacedDigit().foregroundStyle(g["status"].s=="over" ? Color.red : g["status"].s=="near" ? PortfolioStyle.warn : Color.primary)
                Text("净敞口 "+usd(g["net_dollar_delta"])+" · 占净值 "+pct(g["net_nlv_pct"])+(g["headroom_r"].n == nil ? "" : " · 余量 "+num(g["headroom_r"])+"R")).font(.caption).monospacedDigit()
                if !g["note"].s.isEmpty {Text(g["note"].s).font(.caption).foregroundStyle(.secondary)}
                ForEach(Array(g["sleeves"].items.enumerated()),id:\.offset){_,sl in VStack(alignment:.leading,spacing:1){
                    HStack{Text(sl["name"].s).font(.callout.weight(.medium));Spacer();Text(num(sl["stress_loss_r"])+"R").font(.callout).monospacedDigit()}
                    Text("冲击 "+num(sl["shock_pct"])+"% · 代理 "+sl["proxy"].s+(sl["headroom_proxy_dollars"].n == nil ? "" : " · 还可加约 "+usd(sl["headroom_proxy_dollars"])+" 代理名义")).font(.caption).foregroundStyle(.secondary)
                }}
                if !g["uncovered"].items.isEmpty {Text("\(g["uncovered"].items.count) 条未计入："+g["uncovered"].items.map{$0["symbol"].s}.joined(separator:"、")+" · 不是零风险").font(.caption).foregroundStyle(PortfolioStyle.warn)}
                DisclosureGroup("\(g["members"].items.count) 条仓位"){ForEach(Array(g["members"].items.enumerated()),id:\.offset){_,m in VStack(alignment:.leading,spacing:2){
                    HStack{Text(m["symbol"].s+" · "+m["broker"].s+" · "+m["sleeve"].s).font(.callout);Spacer();Text(usd(m["stress_pnl"])).font(.callout).monospacedDigit()}
                    Text(usd(m["dollar_delta"])+" × 敏感度 "+num(m["sensitivity"])+" · "+m["sensitivity_method"].s).font(.caption).foregroundStyle(.secondary)
                }}
                ForEach(Array(g["uncovered"].items.enumerated()),id:\.offset){_,u in Text("未计入 "+u["symbol"].s+" · "+u["broker"].s+"："+u["reason"].s).font(.caption).foregroundStyle(PortfolioStyle.warn).lineLimit(2)}}
            }.padding(14).background(.quaternary.opacity(0.45),in:RoundedRectangle(cornerRadius:12))
        }}
        }
        Text(value["one_r"].n == nil ? "R 分母：未确认净值" : "R 分母：全局定仓 1R "+usd(value["one_r"])+" · 余量按各子项代理 ETF 名义（敏感度 1）折算").font(.caption).foregroundStyle(.secondary)
        if !value["linked_pairs"].items.isEmpty {DisclosureGroup("\(value["linked_pairs"].items.count) 对跨大类高联动（相关 ≥ "+num(value["pair_thresholds"]["linked"])+"）· 可能其实是同一类"){
            ForEach(Array(value["linked_pairs"].items.enumerated()),id:\.offset){_,x in Text(x["a"].s+"（"+x["sleeve_a"].s+"）↔ "+x["b"].s+"（"+x["sleeve_b"].s+"）· 相关 "+num(x["correlation"])).font(.caption).monospacedDigit()}
        }}
        if !value["weak_pairs"].items.isEmpty {DisclosureGroup("\(value["weak_pairs"].items.count) 对同子项联动弱（相关 < "+num(value["pair_thresholds"]["weak"])+"）· 标签可能不准"){
            ForEach(Array(value["weak_pairs"].items.enumerated()),id:\.offset){_,x in Text(x["a"].s+" ↔ "+x["b"].s+"（"+x["sleeve_a"].s+"）· 相关 "+num(x["correlation"])).font(.caption).monospacedDigit()}
        }}
        Notes(values:value["notices"].strings)
    }}
}
struct StrategyPanel:View {
    @EnvironmentObject var model:DeskModel
    var body:some View {Panel(title:"期权策略与操作批次"){
        let strategies=model.state["strategies"]
        ForEach(strategies["groups"].items){g in StrategyCard(value:g,confirmed:true)}
        if strategies["groups"].items.isEmpty{Text("暂无足够证据确认同批建仓的操作；当前形态仍可识别。").foregroundStyle(.secondary)}
        DisclosureGroup("当前持仓形态 · 不等于同批建仓"){
            ForEach(strategies["current_structures"].items){g in StrategyCard(value:g,confirmed:false)}
        }
        DisclosureGroup("\(strategies["ungrouped"].items.count) 条期权的原始批次未知"){
            ForEach(strategies["ungrouped"].items){g in HStack{Text(g["symbol"].s+" · "+g["label"].s).font(.callout);Spacer();if let p=model.positions.first(where:{$0.id==(g["position_id"].s.isEmpty ? g.id:g["position_id"].s)}){Button("补批次"){model.editor = .exposure(p,state:model.state)}}}}
        }
        Notes(values:strategies["notices"].strings)
    }}
}
struct StrategyCard:View {
    let value:J;let confirmed:Bool
    var body:some View{VStack(alignment:.leading,spacing:7){
        Text(value["symbol"].s+" · "+value["strategy"]["label"].s).font(.headline)
        Text(value["broker"].s + (confirmed ? " · 已有批次证据":" · 当前形态")).font(.caption).foregroundStyle(.secondary)
        ForEach(Array(value["legs"].items.enumerated()),id:\.offset){_,leg in Text((leg["side"].s=="short" ? "卖出 ":"买入 ")+num(leg["qty"])+" × "+leg["label"].s).font(.callout)}
        Notes(values:value["notes"].strings+value["strategy"]["notes"].strings);Divider()
    }.padding(.vertical,8)}
}
