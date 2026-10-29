import SwiftUI
import AppKit

struct PlansView: View {
    @EnvironmentObject var model: DeskModel
    @ViewState private var filter = "active"
    var rows:[J] { model.plans.filter { filter == "all" || ["pending","draft","ready","holding"].contains($0["status"].s) } }
    var body:some View { ScrollView { VStack(alignment:.leading,spacing:20) {
        PageHeading(title:"交易计划",subtitle:"所有计划的家（股票/期权/结构）：先定义失效条件，再用统一 1R 计算完整仓位；成交登记与复盘也在计划卡里。")
        HStack { Metric(label:"统一 1R",value:usd(.number((model.state["risk_policy"]["nlv"].n ?? 0)*(model.state["risk_policy"]["risk_pct"].n ?? 0)/100)),note:"完整计划 0.9R–1R"); Button("设置统一标准"){model.editor = .riskPolicy(model.state)} }
        Picker("范围",selection:$filter){Text("进行中").tag("active");Text("全部计划").tag("all")}.pickerStyle(.segmented).frame(width:240)
        if rows.isEmpty { ContentUnavailableView { Label("尚无交易计划",systemImage:"list.bullet.rectangle") } description: { Text("已有持仓在“持仓”中查看。新建计划，或核对已有持仓后接入账本。") } actions: { Button("新建计划"){model.editor = .plan()}.buttonStyle(.borderedProminent) }.frame(maxWidth:.infinity) }
        ForEach(rows) { p in PlanRow(plan:p) }
    }.padding(24) } }
}
struct PlanRow: View {
    let plan:J
    @EnvironmentObject var model:DeskModel
    var body:some View { HStack(alignment:.top) {
        VStack(alignment:.leading,spacing:8){ Text(plan["symbol"].s+" · "+plan["title"].s).font(.headline); Text((plan["kind"].s == "option" ? "期权" : "股票")+" · "+plan["broker"].s+" · "+statusName(plan["status"].s)).font(.caption).foregroundStyle(.secondary); Text(plan["thesis"].s).lineLimit(2).font(.callout); Text("剩余 \(num(plan["open_qty"])) · 已实现 \(usd(plan["realized_pnl"]))").font(.callout).monospacedDigit() }
        Spacer();Button("查看记录"){model.inspectingPlan=plan.id}
    }.card() }
}
struct PlanDetail: View {
    let id:String
    @EnvironmentObject var model:DeskModel
    var plan:J {model.plans.first{$0.id==id} ?? .null}
    func edit(_ spec:EditorSpec) {model.inspectingPlan=nil;model.editor=spec}
    var body:some View { VStack(alignment:.leading,spacing:16){
        HStack{Text(plan["symbol"].s+" · "+plan["title"].s).font(.title2.bold());Spacer();Button("关闭"){model.inspectingPlan=nil}}
        ScrollView{VStack(alignment:.leading,spacing:16){
            Text(statusName(plan["status"].s)+" · "+plan["broker"].s).foregroundStyle(.secondary)
            ForEach([("交易理由","thesis"),("失效条件","invalidation"),("退出安排","exit_rule")],id:\.0){label,key in Panel(title:label){Text(plan[key].s.isEmpty ? "待补充" : plan[key].s).textSelection(.enabled)}}
            if !plan["sizing"].fields.isEmpty {SizingView(value:plan["sizing"])}
            if !plan["discipline"].fields.isEmpty {let d=plan["discipline"]; Panel(title:"执行纪律 · "+d["label"].s){
                HStack {Metric(label:"原始 1R",value:usd(d["baseline_risk"]));Metric(label:"累计入场风险",value:num(d["actual_entry_r"])+" R");Metric(label:"当前剩余风险",value:num(d["current_r"])+" R")}
                Notes(values:d["warnings"].strings)
            }}
            HStack {
                if plan["status"].s == "pending" { Button("核对通过"){model.action("status",["id":plan["id"],"version":plan["version"],"status":.string("draft")])}.buttonStyle(.borderedProminent);Button("退回作废",role:.destructive){model.action("status",["id":plan["id"],"version":plan["version"],"status":.string("cancelled")])} }
                if plan["status"].s == "draft" { Button("编辑计划"){edit(.plan(plan))};Button("标记待执行"){model.action("status",["id":plan["id"],"version":plan["version"],"status":.string("ready")])}.buttonStyle(.borderedProminent) }
                if ["draft","ready"].contains(plan["status"].s) {Button("取消计划",role:.destructive){model.action("status",["id":plan["id"],"version":plan["version"],"status":.string("cancelled")])}}
                if ["ready","holding"].contains(plan["status"].s) {Button("登记入场"){edit(.fill(plan,action:"entry",day:model.day))}}
                if plan["status"].s == "holding" {Button("登记退出"){edit(.fill(plan,action:"exit",day:model.day))}}
            }.disabled(model.busy)
            Panel(title:"执行留痕"){
                ForEach(plan["fills"].items){fill in VStack(alignment:.leading,spacing:5){Text((fill["voided"].b ? "[已撤销] " : "")+fill["day"].s+" · "+(fill["opening_balance"].b ? "期初余额" : fill["action"].s=="entry" ? "入场" : "退出")+" \(num(fill["qty"])) @ \(num(fill["price"]))").font(.headline);Text(fill["note"].s+" · 费用 "+usd(fill["fee"])).font(.caption).foregroundStyle(.secondary);Divider()}}
                if plan["fills"].items.isEmpty {Text("尚无已登记成交").foregroundStyle(.secondary)}
                if let last=plan["fills"].items.last(where:{!$0["voided"].b}),!last["opening_balance"].b {Button("更正上一条错误登记"){edit(.voidFill(plan))}}
            }
            Panel(title:"复盘"){Text(plan["review_note"].s.isEmpty ? "尚无复盘" : plan["review_note"].s);Button("记录复盘"){edit(.review(plan))}}
        }}
    }.padding(24).frame(width:850,height:750) }
}
struct JournalView:View {
    @EnvironmentObject var model:DeskModel
    var body:some View {ScrollView{VStack(alignment:.leading,spacing:20){
        PageHeading(title:"成交与复盘",subtitle:model.state["econf"]["disabled"].b ? "登记券商里已经发生的成交、撤销误登记、写复盘。App 不会向券商下单。" : "登记已经发生的成交和复盘；券商确认单解析成提案后，在这里核对入账。")
        if !model.state["econf"]["disabled"].b { Panel(title:"券商确认单"){
            HStack{Text("\(num(model.state["econf"]["pending"])) 份待处理");Spacer();MailFetchControl();Button("重新扫描"){model.action("econf-scan")}}
            Notes(values:model.state["econf"]["errors"].strings)
            // 只有还要人处理的（待核对、部分登记、解析失败）才铺开；已自动入账、已登记、已忽略的收进折叠区，不再带提示
            let attention = model.state["econf"]["files"].items.filter{["pending","partial","error"].contains($0["status"].s)}
            let settled = model.state["econf"]["files"].items.filter{!["pending","partial","error"].contains($0["status"].s)}
            if attention.isEmpty && !settled.isEmpty {Text("没有需要处理的确认单。").foregroundStyle(.secondary)}
            ForEach(attention){file in VStack(alignment:.leading,spacing:10){
                HStack{Text(file["file"].s).font(.headline);Spacer();Text(statusName(file["status"].s)).font(.caption);Button("忽略"){model.action("econf-dismiss",["sha":file["sha"],"file":file["file"]])}}
                Notes(values:[file["error"].s].filter{!$0.isEmpty}+file["proposal"]["review_required"].strings+file["proposal"]["warnings"].strings)
                ForEach(Array(file["proposal"]["legs"].items.enumerated()),id:\.offset){_,leg in HStack {
                    VStack(alignment:.leading){Text(leg["description"].s);Text(leg["side"].s+" · \(num(leg["qty"])) @ \(num(leg["price"])) · "+file["proposal"]["trade_day"].s).font(.caption).foregroundStyle(.secondary)}
                    Spacer()
                    if leg["status"].s=="pending" {Button("核对登记"){model.editor = .receipt(file,leg:leg,state:model.state)}.disabled(!file["proposal"]["review_required"].items.isEmpty)}
                    else {Text(statusName(leg["status"].s)).font(.caption)}
                }}
                Divider()
            }}
            if !settled.isEmpty {
                DisclosureGroup("已处理 \(settled.count) 份（已自动入账、已登记或已忽略）"){
                    VStack(alignment:.leading,spacing:6){
                        ForEach(settled){file in HStack(alignment:.firstTextBaseline){
                            Text(file["file"].s).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                            Text(file["proposal"]["legs"].items.map{$0["description"].s+" "+$0["side"].s+" "+num($0["qty"])}.joined(separator:"；")).font(.caption).lineLimit(1)
                            Spacer()
                            Text(statusName(file["status"].s)).font(.caption).foregroundStyle(.secondary)
                            if file["status"].s=="dismissed" {Button("恢复处理"){model.action("econf-restore",["sha":file["sha"],"file":file["file"]])}.font(.caption)}
                        }}
                    }.padding(.top,6)
                }.font(.callout)
            }
            if model.state["econf"]["files"].items.isEmpty {Text("投喂箱暂时没有确认单。文件来源见“数据与留存”。").foregroundStyle(.secondary)}
        } }
        Text("交易记录").font(.title2.bold())
        ForEach(model.plans.filter{!$0["fills"].items.isEmpty}){p in PlanRow(plan:p)}
    }.padding(24)}}
}
struct PreparationView:View {
    @EnvironmentObject var model:DeskModel
    var brief:J {model.state["brief"]}
    var body:some View {ScrollView{VStack(alignment:.leading,spacing:16){
        PageHeading(title:"盘前准备",subtitle:"\(model.day) · 美东交易日")
        if brief["status"].s == "stale" || !brief["error"].s.isEmpty {
            Label(brief["error"].s.isEmpty ? "盘前资料不是今天的" : brief["error"].s, systemImage:"exclamationmark.triangle.fill")
                .font(.callout).foregroundStyle(PortfolioStyle.warn).padding(12).frame(maxWidth:.infinity,alignment:.leading)
                .background(PortfolioStyle.warn.opacity(0.14),in:RoundedRectangle(cornerRadius:10))
        }
        Panel(title:"今天的检查与笔记"){
            HStack(alignment:.firstTextBaseline){
                Text("已完成 \(model.state["preparation"]["checks"].items.count) / 3 项").font(.callout.weight(.medium))
                Spacer()
                Button("填写检查与笔记"){model.editor = .preparation(model.state)}
            }
            Text(model.state["preparation"]["note"].s.isEmpty ? "尚未填写盘前笔记" : model.state["preparation"]["note"].s).foregroundStyle(model.state["preparation"]["note"].s.isEmpty ? .secondary : .primary).textSelection(.enabled)
        }
        Panel(title:"盘前观察 · "+brief["day"].s){
            if brief["items"].items.isEmpty { Text("没有可显示的观察").foregroundStyle(.secondary) }
            ForEach(Array(brief["items"].items.enumerated()),id:\.offset){index,item in
                if index > 0 { Divider() }
                BriefRow(item:item)
            }
        }
    }.padding(24)}}
}
/// One symbol: headline always visible, the full fact list behind a click. Long source URLs live in the detail.
struct BriefRow:View {
    let item:J
    @ViewState private var open = false
    static let order = ["rsi6","rsi6_new_low","confluence","confluence_evidence","market_day"]
    static func label(_ key:String)->String {
        switch key {
        case "rsi6": return "RSI6"; case "rsi6_new_low": return "新低"; case "confluence": return "共振"; case "confluence_evidence": return "共振依据"
        case "market_day": return "大盘背景"; case "context_unknown": return "背景缺口"
        default: return key.hasPrefix("context_event") ? "事件" : key.hasPrefix("context") ? "背景" : key
        }
    }
    var symbol:String { item["symbol"].s.isEmpty ? item["sym"].s : item["symbol"].s }
    var ticker:String { symbol.split(separator:":").last.map(String.init) ?? symbol }
    var exchange:String { symbol.contains(":") ? String(symbol.split(separator:":").first ?? "") : "" }
    var pairs:[(String,String)] {
        let facts = item["facts"].fields
        if facts.isEmpty { return item["fact"].s.isEmpty ? [] : [("",item["fact"].s)] }
        func rank(_ key:String)->Int { key == "context_unknown" ? 90 : (Self.order.firstIndex(of:key) ?? 50) }
        return facts.sorted { (rank($0.key),$0.key) < (rank($1.key),$1.key) }.map { (Self.label($0.key),$0.value.s) }
    }
    var body:some View {
        VStack(alignment:.leading,spacing:8){
            Button { open.toggle() } label: {
                HStack(alignment:.firstTextBaseline,spacing:10){
                    Text(ticker).font(.headline).frame(width:64,alignment:.leading)
                    if let close = item["close"].n { Text(num(.number(close))).font(.callout).monospacedDigit().foregroundStyle(.secondary).frame(width:64,alignment:.trailing) }
                    Text(pairs.first?.1 ?? "无可显示事实").font(.callout).foregroundStyle(.secondary).lineLimit(1)
                    Spacer(minLength:8)
                    Image(systemName: open ? "chevron.down" : "chevron.right").font(.caption2).foregroundStyle(.tertiary)
                }.contentShape(Rectangle())
            }.buttonStyle(.plain).help(exchange.isEmpty ? symbol : symbol + "（" + exchange + "）")
            if open {
                VStack(alignment:.leading,spacing:6){
                    ForEach(Array(pairs.enumerated()),id:\.offset){_,pair in
                        HStack(alignment:.firstTextBaseline,spacing:10){
                            Text(pair.0).font(.caption).foregroundStyle(.secondary).frame(width:64,alignment:.leading)
                            Text(pair.1).font(.callout).textSelection(.enabled).fixedSize(horizontal:false,vertical:true)
                        }
                    }
                }.padding(.leading,8).padding(.bottom,4)
            }
        }
    }
}
struct CalendarPage: View {
    @EnvironmentObject var model: DeskModel
    @ViewState private var range = 30       // days ahead; 0 = everything, past included
    @ViewState private var asMonth = true   // 月历 or 列表
    struct Entry: Identifiable { let event: J; let days: Int; var id: String { event.id } }
    var entries: [Entry] {
        model.state["calendar"]["events"].items.compactMap { e -> Entry? in
            guard let d = daysBetween(model.day, e["day"].s) else { return nil }
            if range != 0 && (d < 0 || d > range) { return nil }
            return Entry(event: e, days: d)
        }.sorted { ($0.event["day"].s, $0.event["time"].s) < ($1.event["day"].s, $1.event["time"].s) }
    }
    func tentative(_ e: Entry) -> Bool { e.event["name"].s.contains("待确认") }
    var firm: [Entry] { entries.filter { !tentative($0) } }
    var unsure: [Entry] { entries.filter(tentative) }
    var days: [(day: String, days: Int, rows: [Entry])] {
        Dictionary(grouping: firm, by: { $0.event["day"].s }).map { (day: $0.key, days: $0.value[0].days, rows: $0.value) }.sorted { $0.day < $1.day }
    }
    func header(_ g: (day: String, days: Int, rows: [Entry])) -> String {
        dayLabel(g.day) + (g.days >= 0 ? " · " + countdown(g.days) : " · 已过")
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .top) {
                    PageHeading(title: "交易日历", subtitle: "宏观、财报、期权到期与计划复核；来源时间按原资料保留。")
                    Picker("视图", selection: $asMonth) { Text("月历").tag(true); Text("列表").tag(false) }
                        .pickerStyle(.segmented).labelsHidden().frame(width: 120)
                    if !asMonth {
                        Picker("范围", selection: $range) { Text("7 天").tag(7); Text("30 天").tag(30); Text("全部").tag(0) }
                            .pickerStyle(.segmented).labelsHidden().frame(width: 180)
                    }
                    Button("添加事件") { model.editor = .event(day: model.day) }
                }
                Notes(values: model.state["calendar"]["errors"].strings)
                if asMonth { CalendarGrid(events: model.state["calendar"]["events"].items, today: model.day) }
                else if firm.isEmpty { Text(range == 0 ? "日历里没有已确认事件" : "这个范围内没有已确认事件").foregroundStyle(.secondary) }
                if !asMonth { ForEach(days, id: \.day) { group in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(header(group)).font(.subheadline.weight(.semibold)).foregroundStyle(group.days <= 1 && group.days >= 0 ? PortfolioStyle.warn : Color.secondary)
                        VStack(spacing: 0) {
                            ForEach(Array(group.rows.enumerated()), id: \.offset) { index, entry in
                                if index > 0 { Divider() }
                                EventRow(event: entry.event).padding(.vertical, 8)
                            }
                        }.padding(.horizontal, 14).padding(.vertical, 2).card(padding: 0)
                    }
                } }
                if !asMonth, !unsure.isEmpty {
                    DisclosureGroup("待确认日期的财报（\(unsure.count)）") {
                        VStack(spacing: 0) {
                            ForEach(Array(unsure.enumerated()), id: \.offset) { index, entry in
                                if index > 0 { Divider() }
                                HStack {
                                    Text(entry.event["name"].s.replacingOccurrences(of: "日期待确认", with: "")).font(.callout)
                                    Spacer()
                                    Text(shortDay(entry.event["day"].s) + " · " + countdown(entry.days)).font(.caption).foregroundStyle(.secondary).monospacedDigit()
                                }.padding(.vertical, 6)
                            }
                            Text("缓存里的预估日期，需核对公司公告后再当作已确认。").font(.caption).foregroundStyle(.secondary).padding(.top, 6)
                        }.padding(.top, 6)
                    }.font(.callout)
                }
            }.padding(24)
        }
    }
}
struct EventRow: View {
    let event: J
    @EnvironmentObject var model: DeskModel
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(event["time"].s.isEmpty ? "全天" : event["time"].s).font(.caption).monospacedDigit().foregroundStyle(.secondary).frame(width: 62, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                Text(event["name"].s).font(.callout)
                if event["core"].b { Text("大科技：" + event["core_name"].s + (event["confirmed"].b ? " · 公司已公告" : " · 日期是预估")).font(.caption).foregroundStyle(Color.purple) }
                if event["held"].b, !event["held_note"].s.isEmpty { Text("我的持仓：" + event["held_note"].s).font(.caption).foregroundStyle(PortfolioStyle.data) }
                let detail = [event["symbol"].s, event["source"].s].filter { !$0.isEmpty }.joined(separator: " · ")
                if !detail.isEmpty { Text(detail).font(.caption2).foregroundStyle(.secondary) }
            }
            Spacer(minLength: 8)
            Text(event["held"].b ? "持仓财报" : event["core"].b ? "大科技财报" : event["kind"].s).font(.caption2).padding(.horizontal, 7).padding(.vertical, 2)
                .background(CalendarGrid.tint(event).opacity(event["kind"].s == "到期" || event["held"].b ? 0.18 : 0.12), in: Capsule())
                .foregroundStyle(CalendarGrid.tint(event))
            if event["kind"].s == "自定义" {
                Button("删除", role: .destructive) { model.action("event-delete", ["id": event["id"]]) }.buttonStyle(.borderless).font(.caption)
            }
        }
    }
}
