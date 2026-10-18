import SwiftUI

extension OptModel {
    /// The number the chart plots: 0 盈亏美元, 1 盈亏占成本 %, 2 合约价值. `factor` rescales to the contract count on screen.
    func display(_ S: Double, days: Double, ivScale: Double, factor: Double, metric: Int) -> Double {
        let v = value(S, days: days, ivScale: ivScale) * factor
        let pnl = v - cost * factor
        switch metric {
        case 1: return cost == 0 ? 0 : pnl / abs(cost * factor) * 100
        case 2: return v
        default: return pnl
        }
    }
}

func optionLevelColor(_ kind: String) -> Color {
    switch kind { case "invalid": return .red; case "target": return .green; case "entry": return PortfolioStyle.data; default: return .gray }
}

/// 期权计划页的盈亏图。用 Canvas 一次画完，不用 Swift Charts：后者每个点是一个图表元素，
/// 拖滑块时整页要重新排版，会卡。曲线仍由 OptModel 现算；鼠标悬停只刷新这一个小视图。
struct PayoffChart: View {
    let model: OptModel
    let levels: [(price: Double, kind: String)]
    let breakevens: [Double]
    let days: Double
    let ivScale: Double
    let factor: Double
    let rangePct: Double
    let metric: Int
    let dayText: String
    let name: String
    @ViewState private var hoverX: Double? = nil

    static let left: CGFloat = 54, right: CGFloat = 10, top: CGFloat = 18, bottom: CGFloat = 24

    var showSelected: Bool { days < model.firstDTE }
    var xDomain: ClosedRange<Double> { (model.spot * (1 - rangePct / 100))...(model.spot * (1 + rangePct / 100)) }
    func plot(_ size: CGSize) -> CGRect {
        CGRect(x: Self.left, y: Self.top, width: max(size.width - Self.left - Self.right, 1), height: max(size.height - Self.top - Self.bottom, 1))
    }
    func value(_ S: Double, _ d: Double) -> Double { model.display(S, days: d, ivScale: ivScale, factor: factor, metric: metric) }
    func fmt(_ v: Double) -> String {
        switch metric { case 1: return signedPct(v, digits: 0); case 2: return usd0(v); default: return signedUSD(v) }
    }
    func axisLabel(_ v: Double) -> String { metric == 1 ? num(.number(v), digits: 0) + "%" : compactUSD(v) }

    /// Round tick values (1, 2 or 5 times a power of ten) covering lo...hi, about `target` of them.
    static func ticks(_ lo: Double, _ hi: Double, _ target: Int) -> [Double] {
        guard hi > lo else { return [lo] }
        let raw = (hi - lo) / Double(max(target, 1))
        let mag = pow(10, floor(log10(raw)))
        let step = [1.0, 2.0, 5.0, 10.0].map { $0 * mag }.first { $0 >= raw } ?? 10 * mag
        var out: [Double] = []
        var t = (lo / step).rounded(.up) * step
        while t <= hi + step * 1e-9 { out.append(abs(t) < step * 1e-9 ? 0 : t); t += step }
        return out
    }

    /// An even grid across the price range, plus every strike so the kinks of the expiry curve are exact.
    func sampleXs() -> [Double] {
        let d = xDomain, n = 120
        var xs = (0...n).map { d.lowerBound + (d.upperBound - d.lowerBound) * Double($0) / Double(n) }
        xs += model.legs.map(\.strike).filter { d.contains($0) }
        return Array(Set(xs)).sorted()
    }

    var chartLabel: String { let title = name; return "\(title) 盈亏图" }
    var body: some View {
        GeometryReader { geo in
            Canvas { ctx, size in draw(ctx, size) }
                .onContinuousHover { phase in
                    switch phase {
                    case .active(let p):
                        let r = plot(geo.size), d = xDomain
                        let t = Double((p.x - r.minX) / r.width)
                        hoverX = (t >= 0 && t <= 1) ? d.lowerBound + (d.upperBound - d.lowerBound) * t : nil
                    case .ended:
                        hoverX = nil
                    }
                }
        }
        .frame(height: 300)
        .accessibilityElement().accessibilityLabel(chartLabel)
    }

    func draw(_ context: GraphicsContext, _ size: CGSize) {
        var ctx = context
        let r = plot(size), dom = xDomain
        let xs = sampleXs()
        let expiry = xs.map { value($0, model.firstDTE) }
        let sel: [Double] = showSelected ? xs.map { value($0, days) } : []
        var lo = min(expiry.min() ?? -1, sel.min() ?? .infinity), hi = max(expiry.max() ?? 1, sel.max() ?? -.infinity)
        if metric != 2 { lo = min(lo, 0); hi = max(hi, 0) }
        let pad = max((hi - lo) * 0.08, 1)
        let yLo = lo - pad, yHi = hi + pad
        func px(_ x: Double) -> CGFloat { r.minX + CGFloat((x - dom.lowerBound) / (dom.upperBound - dom.lowerBound)) * r.width }
        func py(_ y: Double) -> CGFloat { r.maxY - CGFloat((y - yLo) / (yHi - yLo)) * r.height }
        func segment(_ a: CGPoint, _ b: CGPoint) -> Path { var p = Path(); p.move(to: a); p.addLine(to: b); return p }

        // 网格和刻度
        let grid = Color.secondary.opacity(0.18)
        for t in Self.ticks(yLo, yHi, 5) {
            let y = py(t)
            ctx.stroke(segment(CGPoint(x: r.minX, y: y), CGPoint(x: r.maxX, y: y)), with: .color(grid), lineWidth: 0.5)
            ctx.draw(Text(axisLabel(t)).font(.caption2).foregroundStyle(.secondary), at: CGPoint(x: r.minX - 6, y: y), anchor: .trailing)
        }
        let xStep = Self.ticks(dom.lowerBound, dom.upperBound, 6)
        let digits = (xStep.count > 1 && abs(xStep[1] - xStep[0]) < 1) ? 1 : 0
        for t in xStep {
            let x = px(t)
            ctx.stroke(segment(CGPoint(x: x, y: r.minY), CGPoint(x: x, y: r.maxY)), with: .color(grid), style: StrokeStyle(lineWidth: 0.5, dash: [2, 3]))
            ctx.draw(Text(num(.number(t), digits: digits)).font(.caption2).foregroundStyle(.secondary), at: CGPoint(x: x, y: r.maxY + 5), anchor: .top)
        }

        // 红绿填充：曲线与零线之间的面积，按零线上下分别裁剪后涂渐变
        let yz = py(0)
        let filled = showSelected ? sel : expiry
        if metric != 2 {
            var area = Path()
            area.move(to: CGPoint(x: px(xs[0]), y: yz))
            for (i, x) in xs.enumerated() { area.addLine(to: CGPoint(x: px(x), y: py(filled[i]))) }
            area.addLine(to: CGPoint(x: px(xs[xs.count - 1]), y: yz)); area.closeSubpath()
            let top = py(max(filled.max() ?? 0, 0)), bottom = py(min(filled.min() ?? 0, 0))
            if yz > r.minY {
                ctx.drawLayer { l in
                    l.clip(to: Path(CGRect(x: r.minX, y: r.minY, width: r.width, height: yz - r.minY)))
                    l.fill(area, with: .linearGradient(Gradient(colors: [Color.green.opacity(0.55), Color.green.opacity(0.03)]),
                                                       startPoint: CGPoint(x: 0, y: min(top, yz - 1)), endPoint: CGPoint(x: 0, y: yz)))
                }
            }
            if yz < r.maxY {
                ctx.drawLayer { l in
                    l.clip(to: Path(CGRect(x: r.minX, y: yz, width: r.width, height: r.maxY - yz)))
                    l.fill(area, with: .linearGradient(Gradient(colors: [Color.red.opacity(0.03), Color.red.opacity(0.55)]),
                                                       startPoint: CGPoint(x: 0, y: yz), endPoint: CGPoint(x: 0, y: max(bottom, yz + 1))))
                }
            }
        }

        // 曲线
        func line(_ vs: [Double]) -> Path {
            var p = Path()
            for (i, x) in xs.enumerated() {
                let pt = CGPoint(x: px(x), y: py(vs[i]))
                if i == 0 { p.move(to: pt) } else { p.addLine(to: pt) }
            }
            return p
        }
        ctx.stroke(line(expiry), with: .color(.primary), style: StrokeStyle(lineWidth: 2, lineJoin: .round))
        if showSelected { ctx.stroke(line(sel), with: .color(.orange), style: StrokeStyle(lineWidth: 2.2, lineJoin: .round)) }
        if metric != 2 { ctx.stroke(segment(CGPoint(x: r.minX, y: yz), CGPoint(x: r.maxX, y: yz)), with: .color(.gray.opacity(0.6)), lineWidth: 1) }

        // 竖线：现价、价位、盈亏平衡价
        func vrule(_ x: Double, _ color: Color, _ style: StrokeStyle) {
            guard dom.contains(x) else { return }
            ctx.stroke(segment(CGPoint(x: px(x), y: r.minY), CGPoint(x: px(x), y: r.maxY)), with: .color(color), style: style)
        }
        vrule(model.spot, Color.primary.opacity(0.55), StrokeStyle(lineWidth: 1, dash: [2, 3]))
        ctx.draw(Text("现价 " + num(.number(model.spot))).font(.caption2).foregroundStyle(.secondary),
                 at: CGPoint(x: px(model.spot) + 3, y: r.minY - 9), anchor: .leading)
        for l in levels { vrule(l.price, optionLevelColor(l.kind).opacity(0.7), StrokeStyle(lineWidth: 1, dash: [4, 3])) }
        for b in breakevens where dom.contains(b) {
            vrule(b, PortfolioStyle.data.opacity(0.8), StrokeStyle(lineWidth: 1))
            ctx.draw(Text(num(.number(b))).font(.caption2).foregroundStyle(PortfolioStyle.data), at: CGPoint(x: px(b), y: r.minY - 9), anchor: .center)
        }

        // 悬停：竖线、两个小圆点、读数框
        guard let hx = hoverX else { return }
        vrule(hx, Color.primary.opacity(0.8), StrokeStyle(lineWidth: 1))
        let atExpiry = value(hx, model.firstDTE), atDay = value(hx, days)
        func dot(_ v: Double, _ color: Color) {
            ctx.fill(Path(ellipseIn: CGRect(x: px(hx) - 3.5, y: py(v) - 3.5, width: 7, height: 7)), with: .color(color))
        }
        dot(atExpiry, .primary)
        if showSelected { dot(atDay, .orange) }
        var lines: [(Text, Bool)] = [
            (Text("标的 " + num(.number(hx)) + "（较现价 " + signedPct((hx / model.spot - 1) * 100, digits: 1) + "）").font(.caption.weight(.semibold)).foregroundStyle(.primary), false),
            (Text("到期  " + fmt(atExpiry)).font(.caption).monospacedDigit().foregroundStyle(.primary), false)]
        if showSelected { lines.append((Text(verbatim: L(dayText) + "  " + fmt(atDay)).font(.caption).monospacedDigit().foregroundStyle(.orange), true)) }
        let resolved = lines.map { ctx.resolve($0.0) }
        let sizes = resolved.map { $0.measure(in: CGSize(width: 600, height: 100)) }
        let w = (sizes.map(\.width).max() ?? 0) + 16, h = sizes.map(\.height).reduce(0, +) + 16 + CGFloat(max(sizes.count - 1, 0)) * 2
        let box = CGRect(x: min(max(px(hx) - w / 2, r.minX), max(r.maxX - w, r.minX)), y: r.minY + 4, width: w, height: h)
        ctx.fill(Path(roundedRect: box, cornerRadius: 8), with: .color(Color(nsColor: .windowBackgroundColor).opacity(0.95)))
        ctx.stroke(Path(roundedRect: box, cornerRadius: 8), with: .color(Color.secondary.opacity(0.3)), lineWidth: 1)
        var y = box.minY + 8
        for (i, t) in resolved.enumerated() {
            ctx.draw(t, at: CGPoint(x: box.minX + 8, y: y), anchor: .topLeading)
            y += sizes[i].height + 2
        }
    }
}
