import SwiftUI

// 宏观面板灯（~/tools/macro_panel/out/state.json，经 workbench/macro.py 读入 exposure["macro_panel"]）。
// 灯只管新开的仓：热度上限、桶上限缩放、未确认档；已有仓位归止损管。
enum MacroStyle {
    static func color(_ grade: String) -> Color {
        switch grade {
        case "红": return .red
        case "黄", "释放1", "过渡": return PortfolioStyle.warn
        case "绿", "顺风": return .green
        case "逆风": return .red
        default: return .secondary
        }
    }
}

struct MacroLightCard: View {
    let value: J
    @ViewState private var expanded = false
    var ok: Bool { value["status"].s == "ok" }
    var light: String { value["light"].s }
    var families: [J] { value["families"].items }
    var body: some View {
        PortfolioPanel(title: "宏观灯 · 新开仓的预算") {
            if !ok {
                Label(value["notice"].s.isEmpty ? "宏观面板不可用，上限按静态值。" : value["notice"].s, systemImage: "exclamationmark.triangle")
                    .font(.callout).foregroundStyle(PortfolioStyle.warn)
            } else {
                HStack(alignment: .center, spacing: 14) {
                    Circle().fill(MacroStyle.color(light)).frame(width: 22, height: 22)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(headline).font(.title3.weight(.semibold))
                        Text(statusLine).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 3) {
                        Text(capLine).font(.callout).monospacedDigit()
                        Text(value["vix_cheap"].b ? "保护便宜（VIX<20）" : "保护不便宜").font(.caption).foregroundStyle(.secondary)
                    }
                }
                TrendGrid(items: value["trends"].items)
                BreadthLine(value: value["breadth"])
                Text("未确认档：" + value["unconfirmed"].s).font(.callout)
                Text("保护：" + value["insurance"].s).font(.callout)
                if ["红", "释放1"].contains(light) {
                    Text(disarmLine).font(.caption).foregroundStyle(.secondary)
                }
                DisclosureGroup("口径与五个系各管什么", isExpanded: $expanded) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("方向看趋势：顺风逢低买 · 逆风反弹减多仓（裸空只做 PA 确认）· 过渡等方向。大小看灯。").font(.callout)
                        ForEach(Array(families.enumerated()), id: \.offset) { _, f in
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Circle().fill(MacroStyle.color(f["grade"].s)).frame(width: 9, height: 9)
                                Text(f["name"].s).font(.callout.weight(.medium)).frame(width: 72, alignment: .leading)
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(f["reading"].s).font(.callout).monospacedDigit()
                                    Text(f["role"].s).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                        HStack(spacing: 4) {
                            Text("近 12 周").font(.caption).foregroundStyle(.secondary)
                            ForEach(Array(value["history"].items.enumerated()), id: \.offset) { _, h in
                                RoundedRectangle(cornerRadius: 2).fill(MacroStyle.color(h["effective"].s)).frame(width: 12, height: 12)
                                    .help(h["week"].s + " 生效 " + h["effective"].s + " · 当周 " + h["raw"].s)
                            }
                        }.padding(.top, 4)
                        Notes(values: noteLines)
                    }.padding(.top, 6)
                }.font(.callout)
            }
        }
    }
    // Strings are built outside the view builder: older Swift compilers time out type-checking long concatenations inline.
    var headline: String { light + (value["stage"].s == "rel1" ? " · 红灯释放第一步" : "") }
    var statusLine: String {
        let fuel: String = value["fuel_high"].b ? "高" : "不高"
        return "燃料" + fuel + " · 火苗 " + fires + " · 数据 " + value["data_day"].s
    }
    var capLine: String { "热度上限 " + num(value["heat_cap_r"]) + "R · 桶上限 ×" + num(value["bucket_scale"]) }
    var disarmLine: String {
        let d = value["disarm"]
        let steps: String = "释放第一步：" + d["rel1"].s + "；第二步：" + d["rel2"].s
        let levels: String = "（标普 " + num(d["spx"], digits: 0) + " · 50 日线 " + num(d["ma50"], digits: 0) + " · 200 日线 " + num(d["ma200"], digits: 0) + "）"
        return steps + levels
    }
    var noteLines: [String] {
        let evidence: [String] = value["trend_evidence"].s.isEmpty ? [] : ["趋势证据：" + value["trend_evidence"].s]
        return value["notes"].strings + evidence
    }
    var fires: String {
        let lit = families.filter { ["F3", "F4", "F5"].contains($0["key"].s) && $0["grade"].s != "绿" }.map { $0["name"].s }
        return lit.isEmpty ? "无" : lit.joined(separator: "、")
    }
}

struct TrendGrid: View {
    let items: [J]
    let columns = [GridItem(.adaptive(minimum: 128), spacing: 8)]
    var body: some View {
        LazyVGrid(columns: columns, alignment: .leading, spacing: 8) {
            ForEach(Array(items.enumerated()), id: \.offset) { _, t in
                HStack(spacing: 6) {
                    RoundedRectangle(cornerRadius: 2).fill(MacroStyle.color(t["state"].s)).frame(width: 4, height: 30)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(t["name"].s + " " + t["state"].s).font(.callout.weight(.medium))
                        Text(t["proxy"].s + (t["vs_ma200_pct"].n.map { " · " + ($0 >= 0 ? "线上 " : "线下 ") + num(.number(abs($0)), digits: 1) + "%" } ?? ""))
                            .font(.caption2).foregroundStyle(.secondary).monospacedDigit().lineLimit(1)
                    }
                }.help(t["rule"].s + "（相对 200 日线；200 日线" + (t["ma200_rising"].b ? "上升" : "下降") + "）")
            }
        }
    }
}

struct BreadthLine: View {
    let value: J
    var line: String {
        let head: String = "广度：站上 200 日线 " + num(value["pct_above_200"], digits: 0) + "% · 13 周背离速度 " + num(value["div13"], digits: 0)
        let tail: String = "（第 " + num(value["div13_pctile"], digits: 0) + " 分位，" + value["label"].s + "）· 只显示，未验证"
        return head + tail
    }
    var body: some View {
        if value["div13"].n != nil {
            let weak = (value["div13"].n ?? 0) <= -11
            HStack(spacing: 6) {
                Image(systemName: weak ? "exclamationmark.triangle" : "chart.bar").foregroundStyle(weak ? PortfolioStyle.warn : Color.secondary)
                Text(line).font(.caption).foregroundStyle(weak ? PortfolioStyle.warn : Color.secondary)
            }.help(value["evidence"].s)
        } else if !value["error"].s.isEmpty {
            Text("广度读数暂不可用：" + value["error"].s).font(.caption).foregroundStyle(.secondary)
        }
    }
}
