import SwiftUI

// 周线观察（~/tools/weekly_watch/out/state.json，经 workbench/weekly_watch.py 读入 state["weekly_watch"]）。
// RSP、VUG 的周线 + 20/50/100/200 周 EMA + 旧高 + 预警/升红条件 + 慢熊信号。只描述，不改宏观灯。
struct WeeklyWatchView: View {
    @EnvironmentObject var model: DeskModel
    var watch: J { model.state["weekly_watch"] }
    /// The file's own order when it gives one, then the author's two ETFs, then anything else alphabetically.
    var order: [String] {
        let given = watch["order"].items.map(\.s).filter { !$0.isEmpty }
        if !given.isEmpty { return given }
        let keys = watch["symbols"].fields.keys
        return ["VUG", "RSP"].filter { keys.contains($0) } + keys.filter { $0 != "VUG" && $0 != "RSP" }.sorted()
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                PageHeading(title: "周线观察", subtitle: "宽基 ETF 的周线和四条周 EMA：四档观察位、预先约定的预警与升红条件，以及慢熊信号")
                if watch["status"].s != "ok" {
                    Label(watch["notice"].s.isEmpty ? "周线观察数据不可用" : watch["notice"].s, systemImage: "exclamationmark.triangle")
                        .font(.callout).foregroundStyle(PortfolioStyle.warn)
                }
                if !watch["triggers"].items.isEmpty {
                    Panel(title: "预警与升红条件（按收完的周判断）") {
                        ForEach(Array(watch["triggers"].items.enumerated()), id: \.offset) { WeeklyTriggerRow(t: $0.element) }
                        Text(watch["light_note"].s).font(.caption).foregroundStyle(.secondary)
                    }
                }
                if !watch["slow_bear"].items.isEmpty {
                    Panel(title: "慢熊信号（从历史最高收盘算起，按周收盘）") {
                        Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 8) {
                            GridRow {
                                ForEach(["", "距最高收盘", "更低的高点、再跌破前低", "反弹失败"], id: \.self) {
                                    Text($0).font(.caption).foregroundStyle(.secondary).lineLimit(1).fixedSize()
                                }
                            }
                            ForEach(Array(watch["slow_bear"].items.enumerated()), id: \.offset) { _, row in
                                GridRow(alignment: .firstTextBaseline) {
                                    Text(row["name"].s).font(.callout.weight(.semibold))
                                    Text(signedPct(row["drop_pct"].n, digits: 1) + " · 第 " + num(row["weeks_since_peak"], digits: 0) + " 周")
                                        .font(.callout).monospacedDigit()
                                    WeeklyMarkerCell(m: row["m2"])
                                    WeeklyMarkerCell(m: row["m4"])
                                }
                            }
                        }
                        Text(watch["slow_note"].s).font(.caption).foregroundStyle(.secondary)
                    }
                }
                ForEach(order, id: \.self) { sym in
                    if !watch["symbols"][sym]["weeks"].items.isEmpty {
                        WeeklySymbolCard(s: watch["symbols"][sym], tint: sym == order.first ? .orange : PortfolioStyle.data)
                    }
                }
                Notes(values: [watch["history_note"].s, watch["source"].s].filter { !$0.isEmpty })
            }.padding(20)
        }
    }
}

struct WeeklyTriggerRow: View {
    let t: J
    var state: (String, Color) {
        switch t["status"].s {
        case "on": t["severity"].s == "amber" ? ("已出现", PortfolioStyle.warn) : ("已满足", .red)
        case "unknown": ("读不到", PortfolioStyle.warn)
        default: ("未满足", .secondary)
        }
    }
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(state.0).font(.caption.weight(.semibold)).foregroundStyle(state.1)
                .padding(.horizontal, 8).padding(.vertical, 2).background(state.1.opacity(0.14), in: Capsule())
            VStack(alignment: .leading, spacing: 2) {
                Text(t["label"].s).font(.callout)
                Text(t["detail"].s).font(.caption).foregroundStyle(.secondary).monospacedDigit()
            }
        }
    }
}

struct WeeklyMarkerCell: View {
    let m: J
    var on: Bool { m["status"].s == "on" }
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(on ? "已出现 " + m["week"].s : "未出现").font(.callout.weight(on ? .semibold : .regular))
                .foregroundStyle(on ? Color.red : Color.secondary)
            Text(m["detail"].s).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }.frame(maxWidth: 320, alignment: .leading)
    }
}

enum WeeklyEMA {
    static let spans = [20, 50, 100, 200]
    static func color(_ span: Int) -> Color {
        switch span { case 20: .gray; case 50: .primary; case 100: .teal; default: .purple }
    }
    static func dash(_ span: Int) -> [CGFloat] { span == 100 ? [6, 3] : [] }
}

struct WeeklySymbolCard: View {
    let s: J; let tint: Color
    var tiers: [J] { s["tiers"].items }
    func tone(_ v: Double?) -> Color { guard let v else { return .secondary }; return v >= 0 ? .green : .red }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(s["symbol"].s).font(.title3.weight(.semibold))
                Text(num(s["close"])).font(.title3).monospacedDigit()
                Text("本周 " + signedPct(s["week_change_pct"].n, digits: 2)).font(.callout).monospacedDigit().foregroundStyle(tone(s["week_change_pct"].n))
                Text("距 52 周高 " + signedPct(s["from_high_pct"].n, digits: 1) + " · 周 ATR " + num(s["atr"])).font(.callout).monospacedDigit().foregroundStyle(.secondary)
                if s["partial_week"].s == "true" { Text("本周未走完").font(.caption).foregroundStyle(PortfolioStyle.warn) }
                Spacer()
                Text("截至 " + s["last_day"].s).font(.caption).foregroundStyle(.secondary)
            }
            WeeklyCandleChart(s: s, tint: tint)
            HStack(spacing: 14) {
                ForEach(WeeklyEMA.spans, id: \.self) { span in
                    HStack(spacing: 5) {
                        RoundedRectangle(cornerRadius: 1).fill(WeeklyEMA.color(span)).frame(width: 14, height: 3)
                        Text("\(span) 周 EMA").font(.caption).foregroundStyle(.secondary)
                    }
                }
                HStack(spacing: 5) { Rectangle().fill(PortfolioStyle.warn.opacity(0.35)).frame(width: 14, height: 8); Text("50 周 EMA 和旧高叠加区").font(.caption).foregroundStyle(.secondary) }
            }
            Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 6) {
                GridRow {
                    ForEach(["周 EMA", "现值", "离收盘", "折 ATR", "本周", "历史上碰到过", "最深停在这一档"], id: \.self) {
                        Text($0).font(.caption).foregroundStyle(.secondary).lineLimit(1).fixedSize()
                    }
                }
                ForEach(Array(tiers.enumerated()), id: \.offset) { _, t in
                    GridRow {
                        HStack(spacing: 6) {
                            RoundedRectangle(cornerRadius: 1).fill(WeeklyEMA.color(Int(t["span"].n ?? 0))).frame(width: 10, height: 3)
                            Text(num(t["span"], digits: 0) + " 周")
                        }
                        Text(num(t["ema"])).monospacedDigit()
                        Text(signedPct(t["dist_pct"].n, digits: 1)).monospacedDigit()
                        Text(num(t["dist_atr"], digits: 1)).monospacedDigit()
                        Text(t["first_touch"].s == "true" ? "第一次碰到" : t["touched_this_week"].s == "true" ? "碰到" : t["close_below"].s == "true" ? "收在线下" : "—")
                            .foregroundStyle(t["first_touch"].s == "true" ? PortfolioStyle.warn : t["close_below"].s == "true" ? Color.red : Color.secondary)
                        Text(num(t["hist_reach"], digits: 0) + "%").monospacedDigit()
                        Text(num(t["hist_deepest"], digits: 0) + "%").monospacedDigit()
                    }.font(.callout)
                }
            }
            levels.font(.caption).foregroundStyle(.secondary)
        }.card()
    }
    var levels: some View {
        let highs = s["old_highs"].items.map { num($0["price"]) + "（" + $0["week"].s + "）" }.joined(separator: "、")
        let lows = s["swing_lows"].items.map { num($0["price"]) + "（" + $0["week"].s + "）" }.joined(separator: "、")
        return VStack(alignment: .leading, spacing: 3) {
            if !highs.isEmpty { Text("下方旧高：" + highs) }
            if !lows.isEmpty { Text("近期周线低点：" + lows) }
            if !s["zone"]["label"].s.isEmpty { Text("叠加区 " + num(s["zone"]["low"]) + "–" + num(s["zone"]["high"]) + "：" + s["zone"]["label"].s) }
        }.textSelection(.enabled)
    }
}

/// 周线蜡烛图：涨绿跌红，阳线空心、阴线实心，四条周 EMA、叠加区、旧高和近期低点。Canvas 一次画完，悬停只刷新这个小视图。
struct WeeklyCandleChart: View {
    let s: J; let tint: Color
    @ViewState private var hover: Int? = nil
    var weeks: [J] { s["weeks"].items }
    static let left: CGFloat = 8, right: CGFloat = 74, top: CGFloat = 10, bottom: CGFloat = 22

    func plot(_ size: CGSize) -> CGRect {
        CGRect(x: Self.left, y: Self.top, width: max(size.width - Self.left - Self.right, 1), height: max(size.height - Self.top - Self.bottom, 1))
    }
    var body: some View {
        GeometryReader { geo in
            Canvas { ctx, size in draw(ctx, size) }
                .onContinuousHover { phase in
                    switch phase {
                    case .active(let p):
                        let r = plot(geo.size), n = weeks.count
                        let i = Int(((p.x - r.minX) / r.width * CGFloat(n)).rounded(.down))
                        hover = (i >= 0 && i < n) ? i : nil
                    case .ended:
                        hover = nil
                    }
                }
        }
        .frame(height: 340)
        .accessibilityElement().accessibilityLabel(s["symbol"].s + " 周线图")
    }

    func draw(_ context: GraphicsContext, _ size: CGSize) {
        let ctx = context
        let r = plot(size), n = weeks.count
        guard n > 1 else { return }
        func val(_ i: Int, _ k: String) -> Double? { weeks[i][k].n }
        var lo = Double.infinity, hi = -Double.infinity
        for i in 0..<n {
            for k in ["l", "e200", "e100"] { if let v = val(i, k) { lo = min(lo, v) } }
            if let v = val(i, "h") { hi = max(hi, v) }
        }
        guard lo < hi else { return }
        let pad = (hi - lo) * 0.04
        let yLo = lo - pad, yHi = hi + pad
        let step = r.width / CGFloat(n)
        func px(_ i: Int) -> CGFloat { r.minX + (CGFloat(i) + 0.5) * step }
        func py(_ v: Double) -> CGFloat { r.maxY - CGFloat((v - yLo) / (yHi - yLo)) * r.height }
        func hline(_ y: CGFloat) -> Path { var p = Path(); p.move(to: CGPoint(x: r.minX, y: y)); p.addLine(to: CGPoint(x: r.maxX, y: y)); return p }
        let grid = Color.secondary.opacity(0.18)

        // 网格、价格刻度（右侧）、年份
        var labels: [(y: CGFloat, text: Text)] = []
        for t in PayoffChart.ticks(yLo, yHi, 5) {
            ctx.stroke(hline(py(t)), with: .color(grid), lineWidth: 0.5)
            labels.append((py(t), Text(num(.number(t), digits: 0)).font(.caption2).foregroundStyle(.secondary)))
        }
        var lastYear = ""
        for i in 0..<n {
            let year = String(weeks[i]["t"].s.prefix(4))
            if year != lastYear {
                if !lastYear.isEmpty {
                    var p = Path(); p.move(to: CGPoint(x: px(i), y: r.minY)); p.addLine(to: CGPoint(x: px(i), y: r.maxY))
                    ctx.stroke(p, with: .color(grid), style: StrokeStyle(lineWidth: 0.5, dash: [2, 3]))
                    ctx.draw(Text(year).font(.caption2).foregroundStyle(.secondary), at: CGPoint(x: px(i) + 3, y: r.maxY + 4), anchor: .topLeading)
                }
                lastYear = year
            }
        }

        // 叠加区、旧高、近期低点
        let warn = PortfolioStyle.warn
        if let a = s["zone"]["low"].n, let b = s["zone"]["high"].n {
            ctx.fill(Path(CGRect(x: r.minX, y: py(b), width: r.width, height: max(py(a) - py(b), 2))), with: .color(warn.opacity(0.18)))
        }
        for h in s["old_highs"].items {
            guard let p = h["price"].n, p > yLo, p < yHi else { continue }
            ctx.stroke(hline(py(p)), with: .color(warn.opacity(0.85)), style: StrokeStyle(lineWidth: 1, dash: [5, 3]))
            labels.append((py(p), Text("旧高 " + num(.number(p))).font(.caption2.weight(.semibold)).foregroundStyle(warn)))
        }
        for l in s["swing_lows"].items.suffix(2) {
            guard let p = l["price"].n, p > yLo, p < yHi else { continue }
            ctx.stroke(hline(py(p)), with: .color(Color.secondary.opacity(0.7)), style: StrokeStyle(lineWidth: 1, dash: [1, 3]))
            labels.append((py(p), Text("低 " + num(.number(p))).font(.caption2).foregroundStyle(.secondary)))
        }

        // 蜡烛：涨绿跌红，阳线空心、阴线实心
        let bodyW = max(1.2, step * 0.62)
        for i in 0..<n {
            guard let o = val(i, "o"), let h = val(i, "h"), let l = val(i, "l"), let c = val(i, "c") else { continue }
            let up = c >= o, color: Color = up ? .green : .red  // 常规配色：涨绿跌红，阳线空心、阴线实心
            var wick = Path(); wick.move(to: CGPoint(x: px(i), y: py(h))); wick.addLine(to: CGPoint(x: px(i), y: py(l)))
            ctx.stroke(wick, with: .color(color), lineWidth: 1)
            let top = py(max(o, c)), bottom = py(min(o, c))
            let rect = CGRect(x: px(i) - bodyW / 2, y: top, width: bodyW, height: max(bottom - top, 1))
            if up { ctx.fill(Path(rect), with: .color(Color(nsColor: .windowBackgroundColor))); ctx.stroke(Path(rect), with: .color(color), lineWidth: 1) }
            else { ctx.fill(Path(rect), with: .color(color)) }
        }

        // 四条周 EMA，右侧标当前值
        for span in WeeklyEMA.spans.reversed() {
            var p = Path(); var started = false
            for i in 0..<n {
                guard let v = val(i, "e\(span)") else { continue }
                let pt = CGPoint(x: px(i), y: py(v))
                if started { p.addLine(to: pt) } else { p.move(to: pt); started = true }
            }
            ctx.stroke(p, with: .color(WeeklyEMA.color(span).opacity(span == 50 ? 0.85 : 0.95)),
                       style: StrokeStyle(lineWidth: span == 50 || span == 200 ? 1.8 : 1.4, lineJoin: .round, dash: WeeklyEMA.dash(span)))
            if let last = val(n - 1, "e\(span)") {
                labels.append((py(last), Text("\(span) " + num(.number(last))).font(.caption2.weight(.semibold)).foregroundStyle(WeeklyEMA.color(span))))
            }
        }

        // 右侧标签：按高度排开，挤在一起时往下推，免得重叠
        let gap: CGFloat = 12
        var placed: [CGFloat] = []
        for item in labels.sorted(by: { $0.y < $1.y }) {
            var y = max(item.y, r.minY)
            if let prev = placed.last, y - prev < gap { y = prev + gap }
            if y > r.maxY + 8 { continue }
            placed.append(y)
            ctx.draw(item.text, at: CGPoint(x: r.maxX + 6, y: y), anchor: .leading)
        }

        // 悬停读数
        guard let i = hover, i < n else { return }
        var vline = Path(); vline.move(to: CGPoint(x: px(i), y: r.minY)); vline.addLine(to: CGPoint(x: px(i), y: r.maxY))
        ctx.stroke(vline, with: .color(Color.primary.opacity(0.6)), lineWidth: 1)
        let w = weeks[i]
        let lines: [Text] = [
            Text(w["t"].s + " 那周").font(.caption.weight(.semibold)),
            Text("开 " + num(w["o"]) + "  高 " + num(w["h"]) + "  低 " + num(w["l"]) + "  收 " + num(w["c"])).font(.caption).monospacedDigit(),
            Text("EMA 20 / 50 周  " + num(w["e20"]) + " / " + num(w["e50"])).font(.caption).monospacedDigit(),
            Text("EMA 100 / 200 周  " + num(w["e100"]) + " / " + num(w["e200"])).font(.caption).monospacedDigit()]
        let resolved = lines.map { ctx.resolve($0) }
        let sizes = resolved.map { $0.measure(in: CGSize(width: 600, height: 100)) }
        let bw = (sizes.map(\.width).max() ?? 0) + 16, bh = sizes.map(\.height).reduce(0, +) + 16 + CGFloat(max(sizes.count - 1, 0)) * 2
        let x = px(i) + 12 + bw > r.maxX ? px(i) - 12 - bw : px(i) + 12
        let box = CGRect(x: max(r.minX, x), y: r.minY + 4, width: bw, height: bh)
        ctx.fill(Path(roundedRect: box, cornerRadius: 8), with: .color(Color(nsColor: .windowBackgroundColor).opacity(0.95)))
        ctx.stroke(Path(roundedRect: box, cornerRadius: 8), with: .color(Color.secondary.opacity(0.3)), lineWidth: 1)
        var y = box.minY + 8
        for (k, t) in resolved.enumerated() { ctx.draw(t, at: CGPoint(x: box.minX + 8, y: y), anchor: .topLeading); y += sizes[k].height + 2 }
    }
}
