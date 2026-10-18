import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct DataView:View {
    @EnvironmentObject var model:DeskModel
    var storage:J{model.state["storage"]}
    var body:some View {ScrollView{VStack(alignment:.leading,spacing:20){
        PageHeading(title:"数据与留存",subtitle:"交易账本、来源快照与历史版本分开保存。升级或替换 App 不会覆盖账户资料。")
        HStack{Metric(label:"来源持仓",value:num(storage["snapshot_positions"])+" 条");Metric(label:"本机计划",value:num(storage["local_plans"])+" 笔");Metric(label:"已登记成交",value:num(storage["local_fills"])+" 条");Metric(label:"历史快照",value:"\(storage["history"].items.count) 份")}
        Panel(title:"交易账本 · SQLite"){
            Text(storage["ledger_path"].s).font(.system(.callout,design:.monospaced)).textSelection(.enabled)
            Text("保存交易计划、逐笔成交、期初余额、风险标准、复盘、日历、审计和持仓快照历史。原持仓接入前仍可查看，只有确认后才生成账本记录。").foregroundStyle(.secondary)
            HStack{Button("导出完整备份"){model.backup()};Button("在 Finder 查看数据目录"){NSWorkspace.shared.open(model.engine.dataDirectory)}}
        }
        Panel(title:"当前持仓来源"){
            Text(storage["source_path"].s).font(.system(.callout,design:.monospaced)).textSelection(.enabled)
            Text(model.state["snapshot"]["asof"].s).foregroundStyle(.secondary)
            HStack{Button("导入持仓 JSON…"){model.importSnapshot()};Button("使用本机采集源"){model.action("use-local-source")}}
            Notes(values:["App 读取本机已有资料，不自动连接券商拉取实时账户。导入只更换快照来源，不生成成交。","每次读取发现内容变化，就保存一个新历史版本。以前未保存的旧版本无法补造。","上游损坏或缺失时，最近留存版本会显著标为历史参考，不当作实时持仓。"])
        }
        Panel(title:"持仓快照历史"){
            ForEach(storage["history"].items){item in HStack{VStack(alignment:.leading,spacing:5){Text(historyTitle(item)).font(.headline);Text("首次留存 "+item["observed_at"].s).font(.caption).foregroundStyle(.secondary)};Spacer();Button("查看快照"){model.history(item.id)}}}
            if storage["history"].items.isEmpty{Text("还没有有效快照；成功读取后自动留存。").foregroundStyle(.secondary)}
        }
        if !model.state["econf"]["disabled"].b { Panel(title:"券商确认单投喂箱"){
            Text(model.state["econf"]["inbox"].s).font(.system(.callout,design:.monospaced)).textSelection(.enabled)
            Text("原始文件只读，核对入账后保留文件指纹与来源。确认单并不等同于完整的当前持仓，不能用某一张回单覆盖整个账户。").foregroundStyle(.secondary)
        } }
        Panel(title:"设置"){
            Text("界面语言、外观、1R 和模块都在设置里（⌘,）。").foregroundStyle(.secondary)
            SettingsLink { Text("打开设置…") }
        }
    }.padding(24)}}
}

private func historyTitle(_ item: J) -> String {
    let day = item["day"].s, n = num(item["positions"])
    return "\(day) · \(n) 条持仓"
}
