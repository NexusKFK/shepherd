import Foundation
import Combine
import AppKit

indirect enum J: Codable, Sendable, Identifiable {
    case object([String: J]), array([J]), string(String), number(Double), bool(Bool), null
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode([J].self) { self = .array(v) }
        else { self = .object(try c.decode([String:J].self)) }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .object(let v): try c.encode(v)
        case .array(let v): try c.encode(v)
        case .string(let v): try c.encode(v)
        case .number(let v): try c.encode(v)
        case .bool(let v): try c.encode(v)
        case .null: try c.encodeNil()
        }
    }
    subscript(_ key: String) -> J { if case .object(let v) = self { return v[key] ?? .null }; return .null }
    var fields: [String:J] { if case .object(let v) = self { return v }; return [:] }
    var items: [J] { if case .array(let v) = self { return v }; return [] }
    var s: String { switch self { case .string(let v): return v; case .number(let n): return String(format: "%g", n); case .bool(let b): return b ? "true" : "false"; default: return "" } }
    var n: Double? { if case .number(let n) = self { return n }; return nil }
    var b: Bool { if case .bool(let v) = self { return v }; return false }
    var strings: [String] { items.map(\.s) }
    var id: String { self["id"].s.isEmpty ? (self["sha"].s.isEmpty ? self["symbol"].s : self["sha"].s) : self["id"].s }
    static func str(_ value: String) -> J { .string(value) }
}
func num(_ value: J, digits: Int = 2) -> String {
    guard let n = value.n, n.isFinite else { return "—" }
    return unicodeMinus(n.formatted(.number.precision(.fractionLength(0...digits))))
}
/// 负号统一用 −（和金额一致），不留 -0。
func unicodeMinus(_ text: String) -> String {
    if text == "-0" { return "0" }
    return text.hasPrefix("-") ? "−" + text.dropFirst() : text
}
/// 只有美元一种货币，直接写 $，不写 US$。负数用 −$。
func dollars(_ value: Double, digits: ClosedRange<Int> = 0...2) -> String {
    let a = abs(value)
    let cents = digits.upperBound > 0 && abs(a - a.rounded()) >= 0.005   // 有零头就固定两位小数，整数不带小数
    return (value < 0 ? "−" : "") + "$" + a.formatted(.number.precision(.fractionLength(cents ? 2 : 0)))
}
func usd(_ value: J) -> String { value.n.map { dollars($0) } ?? "—" }
func pct(_ value: J) -> String { value.n.map { unicodeMinus($0.formatted(.number.precision(.fractionLength(0...1)))) + "%" } ?? "未知" }
func statusName(_ status: String) -> String { ["draft":"草稿", "ready":"待执行", "holding":"持仓中", "closed":"已平仓", "cancelled":"已取消", "pending":"待核对", "partial":"部分登记", "recorded":"已登记", "dismissed":"已忽略", "corrected":"已更正", "error":"解析失败", "auto":"已自动入账"][status] ?? status }

struct EngineError: LocalizedError { let message: String; var errorDescription: String? { message } }
final class Engine: @unchecked Sendable {
    private let queue = DispatchQueue(label: "app.shepherd.engine", qos: .userInitiated)
    private var process: Process?
    private var input: FileHandle?
    private var output: FileHandle?
    private var buffer = Data()
    let dataDirectory: URL
    /// Demo mode (`--demo` or SHEPHERD_DEMO=1): a throwaway folder of invented sources and ledger, built by workbench.demo.
    static let demo = CommandLine.arguments.contains("--demo") || ProcessInfo.processInfo.environment["SHEPHERD_DEMO"] == "1"
    private let demoRoot: URL?
    private var demoBuilt = false
    init() {
        let env = ProcessInfo.processInfo.environment
        if Self.demo {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent("Shepherd-Demo-\(ProcessInfo.processInfo.processIdentifier)")
            demoRoot = root
            dataDirectory = root.appendingPathComponent("data")
        } else {
            demoRoot = nil
            dataDirectory = env["SHEPHERD_DATA_DIR"].map { URL(fileURLWithPath: $0) }
                ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Shepherd/TradingDesk")
        }
    }
    /// Runs the bundled Python once to write the demo folder; it never reads the real home folder or the network.
    private func buildDemo(runtime: URL, resources: URL) throws {
        guard let root = demoRoot, !demoBuilt else { return }
        try? FileManager.default.removeItem(at: root)
        let child = Process()
        child.executableURL = runtime.appendingPathComponent("bin/python3")
        child.currentDirectoryURL = resources
        child.arguments = ["-B", "-s", "-c", "import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('workbench.demo',run_name='__main__')", resources.path, "--out", root.path]
        var env = ProcessInfo.processInfo.environment.filter { !$0.key.hasPrefix("PYTHON") && !$0.key.hasPrefix("SHEPHERD_") && !$0.key.hasPrefix("TRADING_") }
        env["PYTHONHOME"] = runtime.path
        child.environment = env
        child.standardOutput = FileHandle.nullDevice
        let err = Pipe(); child.standardError = err
        try child.run(); child.waitUntilExit()
        guard child.terminationStatus == 0 else {
            let message = String(data: err.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            throw EngineError(message: "演示数据生成失败：" + String(message.suffix(300)))
        }
        demoBuilt = true
    }
    private func launch() throws {
        if let process, process.isRunning { return }
        let fm = FileManager.default
        guard let resources = Bundle.main.resourceURL else { throw EngineError(message: "应用资源缺失，请重新安装。") }
        let runtime = resources.appendingPathComponent("Python")
        let executable = runtime.appendingPathComponent("bin/python3")
        guard fm.isExecutableFile(atPath: executable.path) else { throw EngineError(message: "内置计算引擎缺失，请运行 build-workbench.sh 构建完整 App。") }
        try buildDemo(runtime: runtime, resources: resources)
        try fm.createDirectory(at: dataDirectory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let log = dataDirectory.appendingPathComponent("native-engine.log")
        if !fm.fileExists(atPath: log.path) { fm.createFile(atPath: log.path, contents: nil, attributes: [.posixPermissions: 0o600]) }
        let logHandle = try FileHandle(forWritingTo: log); try logHandle.seekToEnd()
        let child = Process(), incoming = Pipe(), outgoing = Pipe()
        child.executableURL = executable
        child.currentDirectoryURL = resources
        child.arguments = ["-B", "-s", "-u", "-c", "import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('workbench.bridge',run_name='__main__')", resources.path, "--data-dir", dataDirectory.path]
        var env = ProcessInfo.processInfo.environment.filter { !$0.key.hasPrefix("PYTHON") }
        if let root = demoRoot {  // demo: nothing from the real setup may leak in through overrides
            env = env.filter { !$0.key.hasPrefix("SHEPHERD_") && !$0.key.hasPrefix("TRADING_") }
            child.arguments! += ["--source-home", root.appendingPathComponent("sources").path,
                                 "--econf-inbox", root.appendingPathComponent("sources/tools/schwab_confirms/inbox").path]
        }
        env["PYTHONHOME"] = runtime.path
        if let source = env["SHEPHERD_SOURCE_HOME"] { child.arguments! += ["--source-home", source] }
        if let inbox = env["SHEPHERD_ECONF_INBOX"] { child.arguments! += ["--econf-inbox", inbox] }
        child.environment = env
        child.standardInput = incoming; child.standardOutput = outgoing; child.standardError = logHandle
        try child.run()
        process = child; input = incoming.fileHandleForWriting; output = outgoing.fileHandleForReading; buffer = Data()
    }
    func call(_ method: String, _ params: [String:J] = [:]) async throws -> J {
        try await withCheckedThrowingContinuation { continuation in
            queue.async { [self] in
                do {
                    try launch()
                    let id = UUID().uuidString
                    let request = J.object(["id": .string(id), "method": .string(method), "params": .object(params)])
                    var data = try JSONEncoder().encode(request); data.append(10)
                    guard let input, let output, let child = process else { throw EngineError(message: "计算引擎未启动") }
                    let deadline = DispatchWorkItem { if child.isRunning { child.terminate() } }
                    DispatchQueue.global().asyncAfter(deadline: .now() + 45, execute: deadline)
                    defer { deadline.cancel() }
                    try input.write(contentsOf: data)
                    while buffer.firstIndex(of: 10) == nil {
                        let chunk = output.availableData
                        if chunk.isEmpty { throw EngineError(message: "计算引擎已退出或超时；请重新读取。记录仍保存在本机。") }
                        buffer.append(chunk)
                        if buffer.count > 64 * 1024 * 1024 { throw EngineError(message: "数据响应过大") }
                    }
                    let end = buffer.firstIndex(of: 10)!
                    let line = buffer.prefix(upTo: end); buffer.removeSubrange(...end)
                    let response = try JSONDecoder().decode(J.self, from: line)
                    guard response["id"].s == id else { throw EngineError(message: "计算引擎响应编号不匹配") }
                    if !response["error"].s.isEmpty { throw EngineError(message: response["error"].s) }
                    continuation.resume(returning: response["result"])
                } catch { continuation.resume(throwing: error) }
            }
        }
    }
    func stop() {
        try? input?.close(); if let process, process.isRunning { process.terminate() }
        if let demoRoot { try? FileManager.default.removeItem(at: demoRoot) }
    }
    deinit { stop() }
}

@MainActor final class DeskModel: ObservableObject {
    @Published var state: J = .null
    @Published var busy = false
    @Published var error: String?
    @Published var notice = ""
    @Published var editor: EditorSpec?
    @Published var inspectingPlan: String?
    @Published var historical: J?
    /// First-run walkthrough step (0 认识 R, 1 定你的 R, 2 选模块); nil when closed.
    @Published var onboarding: Int?
    /// Interface language; changing it reloads the table and redraws every view (they all read the model).
    @Published var appearance: AppAppearance = AppAppearance.saved {
        didSet { AppLanguage.defaults.set(appearance.rawValue, forKey: AppAppearance.defaultsKey); appearance.apply() }
    }
    /// Copies a holdings JSON into the data folder; the original file is left untouched.
    func importSnapshot() {
        let panel = NSOpenPanel(); panel.allowedContentTypes = [.json]; panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        panel.message = L("选择包含 positions 与 accounts 的持仓快照；会复制到 App 数据目录并保留原文件。")
        if panel.runModal() == .OK, let url = panel.url { action("import-snapshot", ["path": .string(url.path)]) }
    }
    @Published var language: AppLanguage = AppLanguage.saved {
        didSet {
            AppLanguage.defaults.set(language.rawValue, forKey: AppLanguage.defaultsKey)
            Translator.shared.load(language.resolved)
        }
    }
    let engine = Engine()
    var positions: [J] { state["positions"].items }
    var plans: [J] { state["plans"].items }
    var day: String { state["day"].s }
    var ready: Bool { !day.isEmpty }
    func refresh() async {
        guard !busy else { return }; busy = true; defer { busy = false }
        do { state = try await engine.call("refresh") } catch { self.error = error.localizedDescription }
        if onboarding == nil, case .bool(false) = state["modules"]["onboarded"] { onboarding = 0 }
    }
    /// Pages the user switched off in 「选择模块」.
    var hiddenPages: Set<String> { Set(state["modules"]["hidden_pages"].items.map(\.s)) }
    /// A second copy of the app on invented data; the real ledger is not touched.
    func openDemo() {
        let config = NSWorkspace.OpenConfiguration()
        config.createsNewApplicationInstance = true
        config.arguments = ["--demo"]
        NSWorkspace.shared.openApplication(at: Bundle.main.bundleURL, configuration: config) { _, error in
            if let error { Task { @MainActor in self.error = "演示打不开：" + error.localizedDescription } }
        }
    }
    func save(_ method: String, _ values: [String:J]) async throws -> J {
        let result = try await engine.call(method, values)
        state = try await engine.call("state")
        notice = "已保存到本机"
        return result
    }
    func action(_ method: String, _ values: [String:J] = [:]) {
        guard !busy else { return }; busy = true
        Task { defer { busy = false }; do { _ = try await save(method, values) } catch { self.error = error.localizedDescription } }
    }
    func backup() {
        guard !busy else { return }; busy = true
        Task { defer { busy = false }; do {
            let result = try await engine.call("backup")
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: result["path"].s)])
            notice = "已导出账本、审计与持仓快照历史"
        } catch { self.error = error.localizedDescription } }
    }
    func history(_ id: String) {
        Task { do { historical = try await engine.call("snapshot-history", ["id": .string(id)]) } catch { self.error = error.localizedDescription } }
    }
}
