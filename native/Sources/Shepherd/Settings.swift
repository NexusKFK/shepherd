import SwiftUI
import AppKit

// The Settings window (⌘, or the gear in the toolbar): language and appearance, the 1R standard, optional modules,
// and where the data lives. The first-run walkthrough reuses the same R fields and module list.

enum AppAppearance: String, CaseIterable, Identifiable {
    case system, dark, light
    var id: String { rawValue }
    var title: String { switch self { case .system: "跟随系统"; case .dark: "深色"; case .light: "浅色" } }
    static let defaultsKey = "ShepherdAppearance"
    static var saved: AppAppearance { AppAppearance(rawValue: AppLanguage.defaults.string(forKey: defaultsKey) ?? "") ?? .system }
    func apply() {
        switch self {
        case .system: NSApp.appearance = nil
        case .dark: NSApp.appearance = NSAppearance(named: .darkAqua)
        case .light: NSApp.appearance = NSAppearance(named: .aqua)
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject var model: DeskModel
    var body: some View {
        TabView {
            GeneralSettings().tabItem { Label("通用", systemImage: "gearshape") }
            RiskSettings().tabItem { Label("风险与 1R", systemImage: "scalemass") }
            ModuleSettings().tabItem { Label("模块", systemImage: "square.grid.2x2") }
            DataSettings().tabItem { Label("数据", systemImage: "externaldrive") }
        }
        .frame(width: 640, height: 560)
    }
}

private struct GeneralSettings: View {
    @EnvironmentObject var model: DeskModel
    var body: some View {
        Form {
            Picker("界面语言", selection: $model.language) { ForEach(AppLanguage.allCases) { Text(verbatim: $0.title).tag($0) } }
            Picker("外观", selection: $model.appearance) { ForEach(AppAppearance.allCases) { Text($0.title).tag($0) } }
            LabeledContent("演示") { Button("打开演示（虚构数据）") { model.openDemo() }.disabled(Engine.demo) }
            LabeledContent("引导") { Button("认识 R…") { model.onboarding = 0 }.disabled(!model.ready) }
        }
        .formStyle(.grouped)
    }
}

private struct RiskSettings: View {
    @EnvironmentObject var model: DeskModel
    @ViewState private var nlv = ""
    @ViewState private var riskPct = 2.0
    @ViewState private var saving = false
    private var policy: J { model.state["risk_policy"] }
    private var nlvValue: Double? { RPolicyFields.parse(nlv) }
    private var changed: Bool { nlvValue != policy["nlv"].n || abs(riskPct - (policy["risk_pct"].n ?? 2)) > 0.0001 }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("统一 1R 标准").font(.title3.weight(.semibold))
                Text("1R = 定仓净值 × 单笔风险比例。之后每一笔新计划都按这个 1R 反推数量：打到止损，亏的就是 1R。").foregroundStyle(.secondary)
                RPolicyFields(nlv: $nlv, riskPct: $riskPct)
                HStack {
                    Button("认识 R…") { model.onboarding = 0 }.disabled(!model.ready)
                    Spacer()
                    Button("保存") { Task { await save() } }.keyboardShortcut(.defaultAction)
                        .disabled(saving || nlvValue == nil || !changed || !model.ready)
                }
                Notes(values: RPolicyFields.notes)
            }.padding(24)
        }
        .onAppear(perform: seed)
        .onChange(of: model.state["risk_policy"]["version"].n) { _, _ in seed() }
    }
    private func seed() {
        if let n = policy["nlv"].n { nlv = String(format: "%.0f", n) }
        else if let n = model.state["snapshot"]["nlv"].n { nlv = String(format: "%.0f", n) }
        riskPct = policy["risk_pct"].n ?? 2
    }
    private func save() async {
        guard let value = nlvValue else { return }
        saving = true; defer { saving = false }
        do { _ = try await model.save("risk-policy", ["version": policy["version"], "nlv": .number(value), "risk_pct": .number(riskPct)]) }
        catch { model.error = error.localizedDescription }
    }
}

private struct ModuleSettings: View {
    @EnvironmentObject var model: DeskModel
    @ViewState private var enabled: [String: Bool] = [:]
    @ViewState private var saving = false
    private var saved: [String: Bool] { model.state["modules"]["enabled"].fields.mapValues { $0.b } }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("核心部分一直开着：总览、持仓、风险、交易计划、成交与复盘、数据与留存。下面这些按需打开。").foregroundStyle(.secondary)
                ModuleToggles(enabled: $enabled)
                HStack {
                    Spacer()
                    Button("保存") { Task { await save() } }.keyboardShortcut(.defaultAction).disabled(saving || enabled == saved || !model.ready)
                }
            }.padding(24)
        }
        .onAppear { enabled = saved }
    }
    private func save() async {
        saving = true; defer { saving = false }
        do { _ = try await model.save("modules", ["enabled": .object(enabled.mapValues { .bool($0) }), "onboarded": .bool(true)]) }
        catch { model.error = error.localizedDescription }
    }
}

private struct DataSettings: View {
    @EnvironmentObject var model: DeskModel
    var storage: J { model.state["storage"] }
    var body: some View {
        Form {
            Section("交易账本 · SQLite") {
                Text(storage["ledger_path"].s).font(.system(.callout, design: .monospaced)).textSelection(.enabled)
                HStack { Button("在 Finder 查看数据目录") { NSWorkspace.shared.open(model.engine.dataDirectory) }; Button("导出完整备份") { model.backup() } }
            }
            Section("当前持仓来源") {
                Text(storage["source_path"].s).font(.system(.callout, design: .monospaced)).textSelection(.enabled)
                HStack { Button("导入持仓 JSON…") { model.importSnapshot() }; Button("使用本机采集源") { model.action("use-local-source") } }
            }
            if !model.state["econf"]["disabled"].b {
                Section("券商确认单投喂箱") {
                    Text(model.state["econf"]["inbox"].s).font(.system(.callout, design: .monospaced)).textSelection(.enabled)
                }
            }
        }
        .formStyle(.grouped)
    }
}

/// Sizing net value, risk per trade and the resulting 1R. Shared by Settings and the walkthrough's second step.
struct RPolicyFields: View {
    @EnvironmentObject var model: DeskModel
    @Binding var nlv: String
    @Binding var riskPct: Double
    static func parse(_ s: String) -> Double? { Double(s.replacingOccurrences(of: ",", with: "")).flatMap { $0 > 0 ? $0 : nil } }
    static let notes = [
        "净值是冻结的预算基准，不随每天的账户市值自动变；想改就回来改，只影响之后的新计划。",
        "比例上限 4%。常见做法是 0.5%–2%：新手、连亏期、波动大的品种取低的一端。",
        "已经待执行或持仓中的计划保留当时的 1R，不会被这里的修改追溯。",
        "没有确认 1R 之前，App 不会给出计划数量，也不能把计划标成待执行。"]
    private var oneR: Double? { Self.parse(nlv).map { $0 * riskPct / 100 } }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                Text("定仓净值（美元）").frame(width: 150, alignment: .leading)
                TextField("例如 100000", text: $nlv).textFieldStyle(.roundedBorder).frame(width: 180).monospacedDigit()
                if let snap = model.state["snapshot"]["nlv"].n, Self.parse(nlv) != snap {
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
    }
}

/// The optional modules with their switches. Shared by Settings and the walkthrough's third step.
struct ModuleToggles: View {
    @EnvironmentObject var model: DeskModel
    @Binding var enabled: [String: Bool]
    private var catalog: [J] { model.state["modules"]["catalog"].items }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
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
        }
    }
}
