import SwiftUI
import AppKit

// Name the stable property-wrapper type explicitly. Xcode 27 also exports a
// State macro whose plugin is not included in standalone Command Line Tools.
typealias ViewState<Value> = SwiftUI.State<Value>

final class ShepherdDelegate: NSObject, NSApplicationDelegate {
    weak var model: DeskModel?
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if model?.busy == true {
            let alert = NSAlert(); alert.messageText = L("操作正在保存，请稍后退出"); alert.runModal(); return .terminateCancel
        }
        if model?.editor != nil {
            let alert = NSAlert(); alert.messageText = L("还有打开的编辑表单"); alert.informativeText = L("返回保存，或放弃这次输入。已保存的记录不会受影响。")
            alert.addButton(withTitle: L("返回")); alert.addButton(withTitle: L("放弃并退出"))
            if alert.runModal() != .alertSecondButtonReturn { return .terminateCancel }
        }
        return .terminateNow
    }
    func applicationWillTerminate(_ notification: Notification) { model?.engine.stop() }
}

@main struct ShepherdApp: App {
    @NSApplicationDelegateAdaptor(ShepherdDelegate.self) var delegate
    @StateObject private var model = DeskModel()
    var body: some Scene {
        Window("Shepherd", id: "main") {
            RootView().environmentObject(model)
                .frame(minWidth: 1000, minHeight: 700)
                .onAppear { delegate.model = model; model.appearance.apply() }
                .task { await model.refresh() }
        }
        .defaultSize(width: 1360, height: 900)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("新建交易计划") { model.editor = .plan() }.keyboardShortcut("n")
            }
            CommandGroup(after: .newItem) {
                Button("重新读取资料") { Task { await model.refresh() } }.keyboardShortcut("r")
                Button("导出账本与快照备份") { model.backup() }.disabled(model.busy)
                Divider()
                Button("认识 R…") { model.onboarding = 0 }.disabled(!model.ready)
                Button("打开演示（虚构数据）") { model.openDemo() }.disabled(Engine.demo)
            }
        }
        Settings { SettingsView().environmentObject(model) }
    }
}

enum Page: String, CaseIterable, Identifiable {
    case overview = "总览", weekly = "周线观察", holdings = "持仓", risk = "风险", plans = "交易计划", journal = "成交与复盘", paper = "纸盘", optionPlans = "期权演算", calendar = "日历", preparation = "盘前准备", data = "数据与留存"
    var id: String { rawValue }
    /// Name shared with the engine (`attention[].page`, `modules.hidden_pages`).
    var engineKey: String {
        switch self {
        case .overview: "overview"; case .weekly: "weekly"; case .holdings: "holdings"; case .risk: "risk"; case .plans: "plans"
        case .journal: "journal"; case .paper: "paper"; case .optionPlans: "optionPlans"; case .calendar: "calendar"
        case .preparation: "preparation"; case .data: "data"
        }
    }
    /// Sidebar order: what to look at today, the book, the paperwork, then housekeeping.
    static let groups: [(title: String, pages: [Page])] = [
        ("今日", [.overview, .weekly, .preparation, .calendar]), ("组合", [.holdings, .risk]), ("交易", [.plans, .journal, .optionPlans, .paper]), ("系统", [.data])
    ]
    var icon: String {
        switch self { case .overview: "square.grid.2x2"; case .weekly: "chart.bar.xaxis"; case .holdings: "square.stack.3d.up"; case .risk: "chart.xyaxis.line"; case .plans: "list.bullet.rectangle"; case .journal: "book.closed"; case .paper: "flask"; case .optionPlans: "chart.line.uptrend.xyaxis"; case .calendar: "calendar"; case .preparation: "sun.horizon"; case .data: "externaldrive" }
    }
    /// Key used by the engine's overview digest (`attention[].page`).
    init?(engineKey: String) {
        switch engineKey {
        case "holdings": self = .holdings; case "weekly": self = .weekly; case "risk": self = .risk; case "plans": self = .plans; case "journal": self = .journal; case "paper": self = .paper; case "optionPlans": self = .optionPlans
        case "calendar": self = .calendar; case "preparation": self = .preparation; case "data": self = .data
        default: return nil
        }
    }
}
private enum Presentation: Identifiable {
    case editor(EditorSpec), plan(String), history(J), onboarding(Int)
    var id: String {
        switch self {
        case .onboarding: "onboarding"
        case .editor(let spec): "editor-" + spec.id.uuidString
        case .plan(let id): "plan-" + id
        case .history(let snapshot): "history-" + snapshot["source_fingerprint"].s
        }
    }
}
struct RootView: View {
    @EnvironmentObject var model: DeskModel
    @ViewState private var destination: Destination? = .page(.overview)
    private var page: Page? { if case .page(let p) = destination { return p }; return nil }
    private var localTitle: String? { if case .local(let id) = destination { return LocalRegistry.pages.first { $0.id == id }?.title }; return nil }
    private var presentation: Binding<Presentation?> {
        Binding(get: {
            if let step = model.onboarding { return .onboarding(step) }
            if let spec = model.editor { return .editor(spec) }
            if let id = model.inspectingPlan { return .plan(id) }
            if let snapshot = model.historical { return .history(snapshot) }
            return nil
        }, set: { value in
            if value == nil { model.editor = nil; model.inspectingPlan = nil; model.historical = nil
                if model.state["modules"]["onboarded"].b { model.onboarding = nil } }
        })
    }
    /// Counts that mean "something is waiting": red/amber digest lines on the overview, unreviewed receipts on the journal.
    private func badge(_ item: Page) -> Int {
        switch item {
        case .overview: return model.state["overview"]["attention"].items.filter { $0["severity"].s != "info" }.count
        case .journal: return Int(model.state["econf"]["pending"].n ?? 0)
        default: return 0
        }
    }
    /// The built-in pages of a group that are not hidden, each followed by the local pages that ask to come after it.
    private func sidebarEntries(_ pages: [Page]) -> [Destination] {
        var out: [Destination] = []
        for item in pages {
            if !model.hiddenPages.contains(item.engineKey) { out.append(.page(item)) }
            out += LocalRegistry.pages.filter { $0.after == item.engineKey }.map { .local($0.id) }
        }
        return out
    }
    var body: some View {
        NavigationSplitView {
            List(selection: $destination) {
                ForEach(Page.groups, id: \.title) { group in
                    let entries = sidebarEntries(group.pages)
                    if !entries.isEmpty {
                        Section(group.title) {
                            ForEach(entries, id: \.self) { entry in
                                switch entry {
                                case .page(let item): Label(item.rawValue, systemImage: item.icon).badge(badge(item)).tag(entry)
                                case .local(let id):
                                    if let extra = LocalRegistry.pages.first(where: { $0.id == id }) { Label(extra.title, systemImage: extra.icon).tag(entry) }
                                }
                            }
                        }
                    }
                }
            }
            .navigationSplitViewColumnWidth(min: 175, ideal: 190)
            .safeAreaInset(edge: .top, spacing: 0) {
                HStack(spacing: 8) {
                    Image(nsImage: AppLogo.image).resizable().interpolation(.high).frame(width: 24, height: 24).accessibilityHidden(true)
                    Text("SHEPHERD").font(.caption.weight(.semibold)).tracking(2).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 16).padding(.top, 10).padding(.bottom, 4)
            }
            .safeAreaInset(edge: .bottom) { SidebarFooter().environmentObject(model) }
        } detail: {
            Group {
                if !model.ready { VStack(spacing: 16) { ProgressView(); Text("正在读取本机持仓与账本") } }
                else {
                    if case .local(let id) = destination {
                        if let extra = LocalRegistry.pages.first(where: { $0.id == id }) { extra.view() } else { OverviewView(go: { destination = .page($0) }, goLocal: { destination = .local($0) }) }
                    } else {
                    switch page ?? .overview {
                    case .overview: OverviewView(go: { destination = .page($0) }, goLocal: { destination = .local($0) })
                    case .weekly: WeeklyWatchView()
                    case .holdings: HoldingsView()
                    case .risk: RiskView()
                    case .plans: PlansView()
                    case .journal: JournalView()
                    case .paper: PaperView()
                    case .optionPlans: OptionPlansView()
                    case .calendar: CalendarPage()
                    case .preparation: PreparationView()
                    case .data: DataView()
                    }
                    }
                }
            }
            .navigationTitle(localTitle ?? page?.rawValue ?? "总览")
            .onChange(of: model.hiddenPages) { _, hidden in if let page, hidden.contains(page.engineKey) { self.destination = .page(.overview) } }
            .safeAreaInset(edge: .top, spacing: 0) {
                if Engine.demo {
                    Label("演示模式：账户、持仓、行情和计划全是虚构的，关闭窗口即丢弃，不碰你的真实账本。", systemImage: "theatermasks")
                        .font(.caption).foregroundStyle(.orange).frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 20).padding(.vertical, 5).background(Color(nsColor: .windowBackgroundColor))
                }
            }
            .toolbar {
                ToolbarItemGroup {
                    if model.busy { ProgressView().controlSize(.small) }
                    Button { Task { await model.refresh() } } label: { Label("重新读取", systemImage: "arrow.clockwise") }.disabled(model.busy)
                    Button { model.editor = .plan() } label: { Label("新建计划", systemImage: "plus") }.disabled(!model.ready)
                    SettingsLink { Label("设置", systemImage: "gearshape") }.help("设置")
                }
            }
            .safeAreaInset(edge: .bottom) {
                HStack { Text(model.notice.isEmpty ? "资料存于本机 · 成交登记不会向券商下单" : model.notice); Spacer(); Text(model.day + " ET") }
                    .font(.caption).foregroundStyle(.secondary).padding(.horizontal, 20).padding(.vertical, 8).background(.bar)
            }
        }
        .tint(.orange)
        .sheet(item: presentation) { item in
            switch item {
            case .editor(let spec): NativeEditor(spec: spec).environmentObject(model).interactiveDismissDisabled()
            case .plan(let id): PlanDetail(id: id).environmentObject(model)
            case .history(let snapshot): HistoricalSnapshotView(snapshot: snapshot).environmentObject(model)
            case .onboarding(let step): OnboardingView(step: step).environmentObject(model).interactiveDismissDisabled(!model.state["modules"]["onboarded"].b)
            }
        }
        .alert("操作未完成", isPresented: Binding(get: { model.error != nil }, set: { if !$0 { model.error = nil } })) {
            Button("知道了") { model.error = nil }
        } message: { Text(model.error ?? "") }
    }
}

/// Sidebar logo: the icon's source artwork (no macOS grid padding, so it stays legible at 24 pt).
/// Falls back to the app icon when the resource is missing (e.g. a build that skipped the copy step).
enum AppLogo {
    static let image: NSImage = Bundle.main.url(forResource: "Logo", withExtension: "png").flatMap(NSImage.init(contentsOf:))
        ?? NSApp.applicationIconImage ?? NSImage()
}

/// The toolbar already carries the page name; the page only adds its one-line purpose.
struct PageHeading: View {
    let title: String; let subtitle: String
    var body: some View {
        Text(subtitle).font(.callout).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityLabel(title + "，" + subtitle)
    }
}
struct Metric: View {
    let label: String; let value: String; var note = ""
    var valueColor: Color? = nil; var noteColor: Color? = nil   // 盈亏数字传 pnlTint，其余保持默认
    var body: some View { VStack(alignment: .leading, spacing: 7) { Text(label).font(.caption).foregroundStyle(.secondary); Text(value).font(.title2.weight(.semibold)).monospacedDigit().foregroundStyle(valueColor ?? Color.primary); if !note.isEmpty { Text(note).font(.caption).foregroundStyle(noteColor ?? Color.secondary) } }.card() }
}
struct Notes: View {
    let values: [String]
    var body: some View { VStack(alignment: .leading, spacing: 7) { ForEach(Array(values.enumerated()), id: \.offset) { _, item in Label(item, systemImage: "info.circle").font(.caption).foregroundStyle(.secondary).textSelection(.enabled) } }.frame(maxWidth: .infinity, alignment: .leading) }
}
struct Panel<Content: View>: View {
    let title: String; @ViewBuilder var content: () -> Content
    var body: some View { VStack(alignment: .leading, spacing: 12) { Text(title).font(.headline); content() }.card() }
}

struct FreshnessDot: View {
    let level: String
    var body: some View { Circle().fill(PortfolioStyle.level(level)).frame(width: 7, height: 7).accessibilityHidden(true) }
}
/// Replaces the static "local workspace" footnote: one dot per data source, amber/red when it is behind.
struct SidebarFooter: View {
    @EnvironmentObject var model: DeskModel
    var rows: [J] { model.state["overview"]["freshness"].items }
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            if !model.ready { Text("读取资料…").font(.caption2).foregroundStyle(.secondary) }
            ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                HStack(spacing: 6) {
                    FreshnessDot(level: row["level"].s)
                    Text(row["label"].s + " " + (row["day"].s.isEmpty ? "无" : shortDay(row["day"].s))).font(.caption2).monospacedDigit()
                        .foregroundStyle(row["level"].s == "ok" ? Color.secondary : PortfolioStyle.level(row["level"].s))
                }.help(freshnessHelp(row))
            }
            Text("Shepherd \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "") · " + (Engine.demo ? "演示" : "本机")).font(.caption2).foregroundStyle(.tertiary).padding(.top, 2)
        }.frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 16).padding(.vertical, 12)
    }
}
