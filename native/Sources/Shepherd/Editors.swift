import SwiftUI

struct InputField: Identifiable {
    enum Kind { case text, paragraph, number, date, toggle, choice([(String,String)]) }
    let key: String; let label: String; var kind: Kind = .text; var hint = ""
    var id: String { key }
}
struct EditorSpec: Identifiable {
    let id = UUID()
    var title: String
    var method: String
    var fields: [InputField]
    var seed: [String:J] = [:]
    var fixed: [String:J] = [:]
    var explanation = ""
    var calculate = false
    var receiptNew = false
    var checksRequired: [String] = []
    static let directions = [("long", "买入 / 做多"), ("short", "卖出 / 做空")]
    static let kinds = [("stock", "股票 / ETF"), ("option", "期权 / 定义风险结构")]
    static let instruments: [InputField] = [
        .init(key:"symbol",label:"标的代码"), .init(key:"title",label:"计划名称"),
        .init(key:"kind",label:"资产类型",kind:.choice(kinds)), .init(key:"side",label:"方向",kind:.choice(directions)),
        .init(key:"broker",label:"券商 / 账户"), .init(key:"contract",label:"完整合约 / 结构说明"),
        .init(key:"mult",label:"合约乘数",kind:.number), .init(key:"expiry",label:"期权到期日",kind:.date)
    ]
    static func plan(_ plan: J = .null) -> Self {
        let defaults: [String:J] = ["kind":.string("stock"),"side":.string("long"),"broker":.string("IBKR"),"mult":.number(100),"estimated_costs":.number(0)]
        return Self(title: plan.id.isEmpty ? "新建交易计划" : "编辑交易计划", method:"plans", fields: instruments + [
            .init(key:"thesis",label:"交易理由与触发条件",kind:.paragraph),
            .init(key:"invalidation",label:"失效条件",kind:.paragraph), .init(key:"exit_rule",label:"退出安排",kind:.paragraph),
            .init(key:"entry",label:"入场价 / 每份权利金",kind:.number), .init(key:"stop",label:"股票失效价",kind:.number),
            .init(key:"max_loss",label:"卖方定义风险结构：每组最大亏损",kind:.number,hint:"必须是已核对的定义风险结构；裸卖期权不适用。"),
            .init(key:"estimated_costs",label:"费用与滑点预留（美元）",kind:.number),
            .init(key:"planned_qty",label:"完整计划数量（可留空）",kind:.number,hint:"留空采用系统目标；部分成交单独登记。"),
            .init(key:"review_day",label:"计划复核日",kind:.date)
        ], seed: defaults.merging(plan.fields) { _,new in new }, fixed: plan.id.isEmpty ? [:] : ["id":plan["id"],"version":plan["version"]],
                    explanation:"所有实际开仓共用全局 1R；先保存草稿，满足 0.9R–1R 才能标记待执行。",calculate:true)
    }
    static func riskPolicy(_ state: J) -> Self {
        let policy = state["risk_policy"]
        return Self(title:"统一 1R 标准",method:"risk-policy",fields:[
            .init(key:"nlv",label:"确认后的定仓净值（美元）",kind:.number),
            .init(key:"risk_pct",label:"统一单笔风险（%）",kind:.number,hint:"默认 2%，上限 4%；已冻结计划沿用原始 1R。")
        ],seed:["nlv":policy["nlv"].n == nil ? state["snapshot"]["nlv"] : policy["nlv"],"risk_pct":policy["risk_pct"]],fixed:["version":policy["version"]],explanation:"净值是冻结的定仓预算基准，不随快照自动改变；修改只用于后续计划。")
    }
    static func exposure(_ p: J, state: J) -> Self {
        let old = state["risk_inputs"][p.id]
        let row = state["exposure"]["rows"].items.first { $0.id == p.id } ?? .null
        var fields: [InputField] = []
        if p["kind"].s == "option" { fields.append(.init(key:"delta",label:"每份买方合约 Delta（-1 到 1）",kind:.number,hint:"Call 正、Put 负；系统再乘多空方向、数量及合约乘数。")) }
        fields += [.init(key:"underlying_price",label:"标的价格（非期权权利金）",kind:.number), .init(key:"asof",label:"数据日期",kind:.date), .init(key:"source",label:"数据来源与核对凭据"), .init(key:"sector",label:"行业"),.init(key:"theme",label:"共同风险主题"),.init(key:"factor",label:"因子簇（同涨同跌的一组）")]
        if p["kind"].s == "option" { fields += [.init(key:"account_id",label:"账户别名"),.init(key:"operation_id",label:"同批操作编号",hint:"仅在确认同一次操作后填写同一编号；不同时间分别记录。"),.init(key:"executed_at",label:"精确成交时间（带时区，可留空）",hint:"例如 2026-09-23T10:00:00-04:00")] }
        return Self(title:"补充风险数据 · " + p["sym"].s,method:"exposure-input",fields:fields,seed:["asof":state["day"],"sector":row["sector"],"theme":row["theme"],"factor":row["factor"]].merging(old.fields){_,new in new},fixed:["position_id":.string(p.id)],explanation:"缺失数据保持未知。这些标注不改变真实成交和成本。")
    }
    static func fill(_ p: J, action: String, day: String) -> Self {
        Self(title:action == "entry" ? "登记真实入场成交" : "登记真实退出成交",method:"fills",fields:[
            .init(key:"qty",label:"实际成交数量",kind:.number),.init(key:"price",label:"实际成交价格",kind:.number),.init(key:"fee",label:"本次费用（USD）",kind:.number),.init(key:"day",label:"成交日期",kind:.date),.init(key:"note",label:"成交说明",kind:.paragraph)
        ],seed:["qty": action == "exit" ? p["open_qty"] : .string(""),"fee":.number(0),"day":.string(day)],fixed:["id":p["id"],"version":p["version"],"action":.string(action),"request_id":.string(UUID().uuidString)],explanation:p["symbol"].s + " · " + p["title"].s + "。这里只登记券商已经发生的成交，不发送订单。")
    }
    static func adopt(_ p: J, day: String) -> Self {
        Self(title:"核对并接入 · " + p["sym"].s,method:"adopt",fields:[
            .init(key:"qty",label:"已核对持仓数量",kind:.number),.init(key:"open",label:"已核对平均成本",kind:.number),.init(key:"day",label:"余额核对日期",kind:.date),.init(key:"invalidation",label:"当前失效条件",kind:.paragraph),.init(key:"exit_rule",label:"退出安排",kind:.paragraph),.init(key:"confirmed",label:"已在券商核对实际数量和成本，接入的是期初余额",kind:.toggle)
        ],seed:["qty":p["qty"],"open":p["open"],"day":.string(day)],fixed:["id":p["id"],"fingerprint":p["fingerprint"]],explanation:"接入保存期初余额与后续成交，不推断过去的交易，不把快照当成新下单。",checksRequired:["confirmed"])
    }
    static func review(_ p: J) -> Self { Self(title:"交易复盘",method:"review",fields:[.init(key:"note",label:"复盘 / 后续处理记录",kind:.paragraph)],seed:["note":p["review_note"]],fixed:["id":p["id"],"version":p["version"]]) }
    static func voidFill(_ p: J) -> Self {
        Self(title:"更正上一条错误登记",method:"void-fill",fields:[.init(key:"reason",label:"更正原因",kind:.paragraph)],fixed:["id":p["id"],"version":p["version"],"fill_id":p["fills"].items.last(where:{!$0["voided"].b})?["id"] ?? .null],explanation:"只撤销最近一条非期初登记，原记录与更正原因保留在审计中。")
    }
    static func event(day: String) -> Self { Self(title:"添加日历事件",method:"events",fields:[.init(key:"name",label:"事件名称"),.init(key:"day",label:"日期",kind:.date),.init(key:"time",label:"时间与时区"),.init(key:"symbol",label:"关联标的（可选）")],seed:["day":.string(day)]) }
    static func preparation(_ state: J) -> Self {
        let old = state["preparation"]
        var seed: [String:J] = ["note":old["note"]]
        for key in ["snapshot","events","plans"] { seed[key] = .bool(old["checks"].strings.contains(key)) }
        return Self(title:"盘前检查与笔记",method:"preparation",fields:[.init(key:"snapshot",label:"已核对持仓与数据时间",kind:.toggle),.init(key:"events",label:"已核对今天的重要事件",kind:.toggle),.init(key:"plans",label:"已写清交易计划与风险预算",kind:.toggle),.init(key:"note",label:"今天的交易笔记",kind:.paragraph)],seed:seed,fixed:["day":state["day"]])
    }
    static func receipt(_ file: J, leg: J, state: J) -> Self {
        let option = leg["option"]
        let isOption = !option["root"].s.isEmpty
        let candidates = state["plans"].items.filter { leg["candidates"].strings.contains($0.id) }
        var choices: [(String,String)] = [("", "请选择…")]
        if leg["effect"].s != "close" { choices.append(("new", "登记为新持仓")) }
        choices += candidates.map { ($0.id,$0["broker"].s + " · " + $0["symbol"].s + " · " + $0["title"].s) }
        var seed: [String:J] = ["target": candidates.count == 1 ? .string(candidates[0].id) : .string(""),"kind":.string(isOption ? "option" : "stock"),"symbol":isOption ? option["root"] : .string(String(leg["description"].s.split(separator:" ").first ?? "")),"title":leg["description"],"contract":leg["description"],"side":.string(leg["side"].s == "sell" ? "short" : "long"),"broker":.string("Schwab"),"mult":.number(isOption ? 100 : 1),"expiry":option["expiry"],"qty":leg["qty"],"price":leg["price"],"day":file["proposal"]["trade_day"],"action":.string("")]
        if file["proposal"]["legs"].items.count == 1, let c = file["proposal"]["commission"].n, let f = file["proposal"]["fees"].n { seed["fee"] = .number(c+f) }
        var fields: [InputField] = [.init(key:"target",label:"登记到",kind:.choice(choices)),.init(key:"action",label:"已有计划的成交方向",kind:.choice([("","根据真实买卖方向判断"),("entry","入场"),("exit","退出")]))]
        fields += instruments + [.init(key:"qty",label:"成交数量",kind:.number),.init(key:"price",label:"成交价格",kind:.number),.init(key:"fee",label:"已核对费用（未知不能填 0）",kind:.number),.init(key:"day",label:"成交日期",kind:.date),.init(key:"confirmed",label:"已核对账户、完整证券/合约、方向、数量、价格、费用与日期",kind:.toggle)]
        if leg["dupe"].b { fields.append(.init(key:"duplicate_confirmed",label:"已核对这是另一笔真实成交，不是重复邮件",kind:.toggle)) }
        var fixed: [String:J] = ["sha":file["sha"],"file":file["file"],"leg":leg["index"]]
        for p in candidates { fixed["version:"+p.id] = p["version"] }
        return Self(title:"核对并登记确认单",method:"econf-record",fields:fields,seed:seed,fixed:fixed,explanation:"来源：" + file["file"].s + "。选择新持仓时填写计划字段；并入已有计划时沿用其合约与账户。",receiptNew:true,checksRequired: leg["dupe"].b ? ["confirmed","duplicate_confirmed"] : ["confirmed"])
    }
}

struct NativeEditor: View {
    let spec: EditorSpec
    @EnvironmentObject var model: DeskModel
    @ViewState private var values: [String:String]
    @ViewState private var error = ""
    @ViewState private var result: J = .null
    @ViewState private var working = false
    @ViewState private var dirty = false
    @ViewState private var confirmDiscard = false
    init(spec: EditorSpec) { self.spec = spec; _values = ViewState(initialValue: spec.seed.mapValues(\.s)) }
    func binding(_ key: String) -> Binding<String> { Binding(get:{values[key] ?? ""},set:{values[key]=$0;dirty=true;result = .null}) }
    func payload() -> [String:J] {
        var payload = spec.fixed
        for f in spec.fields {
            let value = values[f.key] ?? ""
            if case .toggle = f.kind { payload[f.key] = .bool(value == "true") }
            else { payload[f.key] = .string(value) }
        }
        if spec.method == "preparation" { payload["checks"] = .array(["snapshot","events","plans"].filter { values[$0] == "true" }.map(J.string)); for key in ["snapshot","events","plans"] { payload.removeValue(forKey:key) } }
        if spec.receiptNew {
            let target = values["target"] ?? ""
            payload["instrument_confirmed"] = payload["confirmed"]
            if target == "new" {
                payload["new_plan"] = .bool(true)
                payload["plan"] = .object(Dictionary(uniqueKeysWithValues: EditorSpec.instruments.map { ($0.key,J.string(values[$0.key] ?? "")) }))
            } else { payload["plan_id"] = .string(target); payload["version"] = spec.fixed["version:"+target] }
            for key in payload.keys.filter({$0.hasPrefix("version:")}) { payload.removeValue(forKey:key) }
        }
        return payload
    }
    var body: some View {
        VStack(alignment:.leading,spacing:0) {
            HStack { Text(spec.title).font(.title2.bold()); Spacer(); Button("取消") { if dirty { confirmDiscard=true } else {model.editor=nil} }.disabled(working) }.padding(24)
            Divider()
            Form {
                if !spec.explanation.isEmpty { Text(spec.explanation).font(.callout).foregroundStyle(.secondary) }
                if spec.calculate { LabeledContent("全局 1R",value:usd(.number((model.state["risk_policy"]["nlv"].n ?? 0)*(model.state["risk_policy"]["risk_pct"].n ?? 0)/100))) }
                ForEach(spec.fields) { field in
                    if !spec.receiptNew || !EditorSpec.instruments.contains(where:{$0.key==field.key}) || values["target"] == "new" {
                        VStack(alignment:.leading,spacing:5) {
                            switch field.kind {
                            case .paragraph:
                                Text(field.label).font(.callout)
                                TextEditor(text:binding(field.key)).frame(minHeight:75).font(.body).border(.quaternary)
                            case .toggle:
                                Toggle(field.label,isOn:Binding(get:{values[field.key]=="true"},set:{values[field.key]=$0 ? "true":"false";dirty=true}))
                            case .choice(let options):
                                Picker(field.label,selection:binding(field.key)) { ForEach(options,id:\.0) { key,label in Text(label).tag(key) } }
                            default:
                                TextField(field.label,text:binding(field.key),prompt:Text({if case .date=field.kind{return "YYYY-MM-DD（可选时留空）"};return ""}()))
                            }
                            if !field.hint.isEmpty { Text(field.hint).font(.caption).foregroundStyle(.secondary) }
                        }
                    }
                }
                if spec.calculate {
                    Button("计算完整目标仓位") { calculate() }.disabled(working)
                    if !result.fields.isEmpty { SizingView(value:result) }
                }
            }.formStyle(.grouped)
            if !error.isEmpty { Text(error).foregroundStyle(.red).font(.callout).textSelection(.enabled).padding(.horizontal,24).padding(.vertical,10) }
            Divider()
            HStack { Text("保存在本机 · 不发送券商订单").font(.caption).foregroundStyle(.secondary); Spacer(); if working {ProgressView().controlSize(.small)}; Button(spec.method == "plans" ? "保存草稿" : "确认保存") { submit() }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(working) }.padding(20)
        }.frame(width:720,height:720)
        .confirmationDialog("放弃尚未保存的修改？",isPresented:$confirmDiscard) { Button("放弃修改",role:.destructive){model.editor=nil};Button("继续编辑",role:.cancel){} }
    }
    func calculate() { working=true;error=""; Task { defer{working=false};do{result=try await model.engine.call("size",payload())}catch{self.error=error.localizedDescription} } }
    func submit() {
        if spec.checksRequired.contains(where:{values[$0] != "true"}) {error="请完成勾选的核对项后再保存。";return}
        working=true;model.busy=true;error=""
        Task { defer{working=false;model.busy=false};do{_ = try await model.save(spec.method,payload());model.editor=nil}catch{self.error=error.localizedDescription} }
    }
}
struct SizingView: View {
    let value:J
    var body: some View { VStack(alignment:.leading,spacing:8) {
        Text(value["eligible"].b ? "符合完整开仓标准" : "保持观察 · 暂不可执行").font(.headline).foregroundStyle(value["eligible"].b ? .green : PortfolioStyle.warn)
        Text("目标 \(num(value["qty"])) 单位 · 完整计划 \(num(value["planned_qty"])) 单位 · 1R \(usd(value["budget"]))")
        Text("计划风险 \(usd(value["planned_risk"])) · 名义金额 \(usd(value["notional"]))").font(.callout)
        Notes(values:[value["basis"].s]+value["blockers"].strings+value["warnings"].strings)
    }.padding(.vertical,8) }
}
