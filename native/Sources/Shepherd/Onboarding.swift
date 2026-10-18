import SwiftUI
import AppKit

// First run: what R is (with a worked example), set your own R, then pick the optional modules.
// Also reachable later from the menu: 「认识 R…」 starts at step 0, 「选择模块…」 at step 2.

struct OnboardingView: View {
    @EnvironmentObject var model: DeskModel
    @ViewState var step: Int
    @ViewState private var nlv = ""
    @ViewState private var riskPct = 2.0
    @ViewState private var enabled: [String: Bool] = [:]
    @ViewState private var saving = false
    private var policy: J { model.state["risk_policy"] }
    private var catalog: [J] { model.state["modules"]["catalog"].items }
    private var onboarded: Bool { model.state["modules"]["onboarded"].b }
    private let titles = ["认识 R", "定你的 R", "选模块"]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 18) {
                ForEach(Array(titles.enumerated()), id: \.offset) { i, title in
                    HStack(spacing: 6) {
                        Text("\(i + 1)").font(.caption.weight(.bold)).frame(width: 20, height: 20)
                            .background(Circle().fill(i == step ? Color.orange : Color.secondary.opacity(0.25)))
                            .foregroundStyle(i == step ? Color.white : Color.secondary)
                        Text(title).font(.callout.weight(i == step ? .semibold : .regular)).foregroundStyle(i == step ? Color.primary : Color.secondary)
                    }
                }
                Spacer()
                if Engine.demo { Text("演示模式 · 虚构数据").font(.caption).foregroundStyle(.orange) }
            }.padding(.horizontal, 28).padding(.vertical, 16)
            Divider()
            ScrollView {
                Group {
                    switch step {
                    case 0: RExplainer()
                    case 1: setR
                    default: pickModules
                    }
                }.padding(28).frame(maxWidth: .infinity, alignment: .leading)
            }
            Divider()
            HStack {
                if step > 0 { Button("上一步") { step -= 1 } }
                Spacer()
                if onboarded { Button("关闭") { model.onboarding = nil }.keyboardShortcut(.cancelAction) }
                switch step {
                case 0: Button("下一步：定你的 R") { step = 1 }.keyboardShortcut(.defaultAction)
                case 1: Button(policyConfirmed && !changed ? "沿用，下一步" : "确认这个 R") { Task { await saveR() } }
                        .keyboardShortcut(.defaultAction).disabled(saving || nlvValue == nil)
                default: Button("完成") { Task { await saveModules() } }.keyboardShortcut(.defaultAction).disabled(saving || !policyConfirmed)
                }
            }.padding(.horizontal, 28).padding(.vertical, 14)
        }
        .frame(width: 820, height: 680)
        .onAppear(perform: seed)
    }

    // MARK: step 2 — set R
    private var policyConfirmed: Bool { policy["nlv"].n != nil }
    private var nlvValue: Double? { Double(nlv.replacingOccurrences(of: ",", with: "")).flatMap { $0 > 0 ? $0 : nil } }
    private var changed: Bool { nlvValue != policy["nlv"].n || abs(riskPct - (policy["risk_pct"].n ?? 2)) > 0.0001 }
    private var oneR: Double? { nlvValue.map { $0 * riskPct / 100 } }
    private var setR: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("先定好你的 1R").font(.title2.weight(.semibold))
            Text("1R = 定仓净值 × 单笔风险比例。之后每一笔新计划都按这个 1R 反推数量：打到止损，亏的就是 1R。")
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .firstTextBaseline) {
                    Text("定仓净值（美元）").frame(width: 150, alignment: .leading)
                    TextField("例如 100000", text: $nlv).textFieldStyle(.roundedBorder).frame(width: 180).monospacedDigit()
                    if let snap = model.state["snapshot"]["nlv"].n, nlvValue != snap {
                        Button("用持仓快照的 " + dollars(snap)) { nlv = String(format: "%.0f", snap) }.buttonStyle(.link)
                    }
                }
                HStack(alignment: .firstTextBaseline) {
                    Text("单笔风险比例").frame(width: 150, alignment: .leading)
                    Slider(value: $riskPct, in: 0.25...4, step: 0.25).frame(width: 260)
                    Text(String(format: "%.2g%%", riskPct)).monospacedDigit().frame(width: 50, alignment: .leading)
                }
                HStack(alignment: .firstTextBaseline) {
                    Text("你的 1R").frame(width: 150, alignment: .leading)
                    Text(oneR.map { dollars($0) } ?? "—").font(.title.weight(.semibold)).monospacedDigit().foregroundStyle(.orange)
                }
            }.card()
            Notes(values: [
                "净值是冻结的预算基准，不随每天的账户市值自动变；想改就回来改，只影响之后的新计划。",
                "比例上限 4%。常见做法是 0.5%–2%：新手、连亏期、波动大的品种取低的一端。",
                "已经待执行或持仓中的计划保留当时的 1R，不会被这里的修改追溯。",
                "没有确认 1R 之前，App 不会给出计划数量，也不能把计划标成待执行。"])
        }
    }

    // MARK: step 3 — modules
    private var pickModules: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("选你要用的模块").font(.title2.weight(.semibold))
            Text("核心部分一直开着：总览、持仓、风险、交易计划、成交与复盘、数据与留存。下面这些按需打开，之后随时能在菜单「选择模块…」里改。")
                .foregroundStyle(.secondary)
            ForEach(Array(catalog.enumerated()), id: \.offset) { _, m in
                HStack(alignment: .top, spacing: 14) {
                    Toggle("", isOn: Binding(get: { enabled[m["key"].s] ?? false }, set: { enabled[m["key"].s] = $0 }))
                        .toggleStyle(.switch).labelsHidden()
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 8) {
                            Text(m["title"].s).font(.headline)
                            if m["data_found"].b { Text("已找到数据").font(.caption2).padding(.horizontal, 6).padding(.vertical, 2)
                                .background(Capsule().fill(Color.green.opacity(0.18))).foregroundStyle(.green) }
                        }
                        Text(m["summary"].s).font(.callout)
                        Text(m["needs"].s).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                }.card(padding: 12)
            }
            Text("每个模块的数据文件格式见 docs/data-formats.md。没有数据的模块打开后会如实显示「没有找到数据文件」，不会假装正常。")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func seed() {
        if let n = policy["nlv"].n { nlv = String(format: "%.0f", n) }
        else if let n = model.state["snapshot"]["nlv"].n { nlv = String(format: "%.0f", n) }
        riskPct = policy["risk_pct"].n ?? 2
        for (k, v) in model.state["modules"]["enabled"].fields { enabled[k] = v.b }
    }
    private func saveR() async {
        guard let value = nlvValue else { return }
        if policyConfirmed && !changed { step = 2; return }
        saving = true; defer { saving = false }
        do {
            _ = try await model.save("risk-policy", ["version": policy["version"], "nlv": .number(value), "risk_pct": .number(riskPct)])
            step = 2
        } catch { model.error = error.localizedDescription }
    }
    private func saveModules() async {
        saving = true; defer { saving = false }
        do {
            _ = try await model.save("modules", ["enabled": .object(enabled.mapValues { .bool($0) }), "onboarded": .bool(true)])
            model.onboarding = nil
        } catch { model.error = error.localizedDescription }
    }
}

/// Step 1: R in one worked example. Every number on the page follows the four inputs.
struct RExplainer: View {
    @ViewState private var nlv = 100_000.0
    @ViewState private var pct = 2.0
    @ViewState private var entry = 100.0
    @ViewState private var stopPrice = 95.0
    private var oneR: Double { nlv * pct / 100 }
    private var perShare: Double { max(entry - stopPrice, 0.01) }
    private var shares: Int { Int((oneR / perShare).rounded(.down)) }
    private var position: Double { Double(shares) * entry }
    private func price(atR r: Double) -> Double { entry + r * perShare }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("R 是什么").font(.title2.weight(.semibold))
            Text("R 是你每一笔交易愿意亏的钱。先定 1R，再用止损的远近决定买多少：止损近就多买，止损远就少买。这样无论哪一笔被止损，亏的都是同样的 1R。")
            HStack(alignment: .top, spacing: 16) {
                VStack(alignment: .leading, spacing: 12) {
                    row("净值", $nlv, 10_000...500_000, 10_000, dollars(nlv))
                    row("单笔风险", $pct, 0.25...4, 0.25, String(format: "%.2g%%", pct))
                    row("入场价", $entry, 20...300, 1, String(format: "%.0f", entry))
                    row("止损价", $stopPrice, 10...max(entry - 1, 11), 1, String(format: "%.0f", stopPrice))
                }.frame(maxWidth: .infinity, alignment: .leading).card()
                RLadder(entry: entry, perShare: perShare, oneR: oneR).frame(height: 230).frame(maxWidth: .infinity).card()
            }
            VStack(alignment: .leading, spacing: 8) {
                line("1R", dollars(oneR), "= 净值 \(dollars(nlv)) × \(String(format: "%.2g", pct))%")
                line("每股风险", String(format: "$%.2f", perShare), "= 入场 \(Int(entry)) − 止损 \(Int(stopPrice))")
                line("能买", "\(shares) 股", "= 1R ÷ 每股风险，向下取整；仓位 \(dollars(position))，占净值 \(String(format: "%.0f", position / nlv * 100))%")
                line("打到止损", "−" + dollars(Double(shares) * perShare), "≈ −1R，亏多少事先就知道")
                line("涨到 \(String(format: "%.0f", price(atR: 3)))", "+" + dollars(Double(shares) * perShare * 3), "= +3R：盈利也按 R 记，不同大小的交易能放在一起比")
            }.card()
            Notes(values: [
                "仓位大小由止损距离算出来，不是凭感觉。止损放得很近时数量会很大，这时要留意名义金额占净值的比例。",
                "热度 = 所有止损同时打到会亏多少 R。Shepherd 在总览和风险页一直显示它。",
                "跳空、滑点和费用会让实际亏损超过 1R；R 是计划时的预算，不是保证。"])
        }
    }
    private func row(_ label: String, _ value: Binding<Double>, _ range: ClosedRange<Double>, _ step: Double, _ text: String) -> some View {
        HStack {
            Text(label).frame(width: 64, alignment: .leading)
            Slider(value: value, in: range, step: step)
            Text(text).monospacedDigit().frame(width: 76, alignment: .trailing)
        }
    }
    private func line(_ label: String, _ value: String, _ note: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(label).foregroundStyle(.secondary).frame(width: 84, alignment: .leading)
            Text(value).font(.headline).monospacedDigit().frame(width: 110, alignment: .leading)
            Text(note).font(.callout).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// Price ladder: the stop is −1R, the entry 0, and every step of one "per-share risk" above is another R.
struct RLadder: View {
    let entry: Double; let perShare: Double; let oneR: Double
    var body: some View {
        Canvas { ctx, size in
            let rs: [Double] = [-1, 0, 1, 2, 3]
            let top: CGFloat = 14, bottom = size.height - 14
            func y(_ r: Double) -> CGFloat { bottom - CGFloat((r + 1) / 4) * (bottom - top) }
            let barX: CGFloat = 70, barW: CGFloat = 26
            ctx.fill(Path(CGRect(x: barX, y: y(0), width: barW, height: y(-1) - y(0))), with: .color(.red.opacity(0.35)))
            ctx.fill(Path(CGRect(x: barX, y: y(3), width: barW, height: y(0) - y(3))), with: .color(.green.opacity(0.25)))
            for r in rs {
                var p = Path(); p.move(to: CGPoint(x: 40, y: y(r))); p.addLine(to: CGPoint(x: size.width - 10, y: y(r)))
                ctx.stroke(p, with: .color(r == 0 ? .primary : .secondary.opacity(0.5)), style: StrokeStyle(lineWidth: r == 0 ? 1.5 : 0.8, dash: r == 0 ? [] : [3, 3]))
                let label = r == 0 ? "入场 0R" : (r < 0 ? "止损 −1R" : "+\(Int(r))R")
                ctx.draw(Text(String(format: "%.0f", entry + r * perShare)).font(.caption).monospacedDigit(), at: CGPoint(x: 20, y: y(r)))
                ctx.draw(Text(label).font(.caption.weight(.semibold)).foregroundColor(r < 0 ? .red : (r == 0 ? .primary : .green)),
                         at: CGPoint(x: barX + barW + 12, y: y(r) - 8), anchor: .leading)
                ctx.draw(Text((r < 0 ? "−" : (r == 0 ? "" : "+")) + dollars(abs(r) * oneR)).font(.caption).monospacedDigit().foregroundColor(.secondary),
                         at: CGPoint(x: size.width - 12, y: y(r) - 8), anchor: .trailing)
            }
        }
        .accessibilityLabel("价格阶梯：止损是负 1R，入场是 0，往上每多一个每股风险就是多 1R")
    }
}
