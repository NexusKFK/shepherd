import SwiftUI
import AppKit

// Display-time translation. The engine and the app keep their Chinese wording internally (it is also what the
// logic compares against); every string is translated only when it is shown. Tables live in
// Resources/workbench/i18n/<language>.json as {"简体原文": "译文"}; keys with {0}, {1} are templates.
//
// Lookup order: exact match → whole-string template (filled values translated recursively) → greedy fragments.
// If any Chinese character is left uncovered the original is shown unchanged, so user-entered text such as a
// plan title is never half translated.

enum AppLanguage: String, CaseIterable, Identifiable {
    case system, zhHans = "zh-Hans", zhHant = "zh-Hant", en
    var id: String { rawValue }
    /// Each option names itself in its own language, so a user can always find their way back.
    var title: String {
        switch self { case .system: "跟随系统 · System"; case .zhHans: "简体中文"; case .zhHant: "繁體中文"; case .en: "English" }
    }
    static let defaultsKey = "ShepherdLanguage"
    /// The demo keeps its own preferences, so trying a language or appearance there never changes the real app.
    static var defaults: UserDefaults { isDemo ? (UserDefaults(suiteName: "local.shepherd.tradingdesk.demo") ?? .standard) : .standard }
    /// Same rule as Engine.demo, kept here so this file compiles on its own (the translation tests build it alone).
    static let isDemo = CommandLine.arguments.contains("--demo") || ProcessInfo.processInfo.environment["SHEPHERD_DEMO"] == "1"
    /// SHEPHERD_LANG overrides the saved choice for one launch (screenshots and tests), without touching it.
    static var saved: AppLanguage {
        if let forced = ProcessInfo.processInfo.environment["SHEPHERD_LANG"], let lang = AppLanguage(rawValue: forced) { return lang }
        return AppLanguage(rawValue: defaults.string(forKey: defaultsKey) ?? "") ?? .system
    }
    /// The table actually used: a system preference resolves to the first supported language in macOS settings.
    var resolved: String {
        if self != .system { return rawValue }
        for code in Locale.preferredLanguages {
            let c = code.lowercased()
            if c.hasPrefix("zh-hant") || c.hasPrefix("zh-tw") || c.hasPrefix("zh-hk") || c.hasPrefix("zh-mo") { return "zh-Hant" }
            if c.hasPrefix("zh") { return "zh-Hans" }
            if c.hasPrefix("en") { return "en" }
        }
        return "en"
    }
}

final class Translator: @unchecked Sendable {
    static let shared = Translator()
    private(set) var language = "zh-Hans"
    private var exact: [String: String] = [:]
    private var templates: [(regex: NSRegularExpression, anchor: String, target: String)] = []
    private var fragments: [Character: [(key: String, value: String)]] = [:]
    private var cache: [String: String] = [:]
    private var misses: Set<String> = []
    private var produced: Set<String> = []          // translations already handed out; shown again as they are
    private let lock = NSLock()
    private static let slot = try! NSRegularExpression(pattern: "(?<!\\{)\\{(\\d+)\\}(?!\\})")
    /// "1 items" → "1 item": the tables count with plurals, the number is only known at run time.
    private static let singular = try! NSRegularExpression(
        pattern: "(?<![\\d.,])1 (item|version|day|week|share|leg|contract|plan|fill|record|position|file|snapshot|month|year|hour|minute|broker|account|confirmation)s\\b")
    private static let singularEntry = try! NSRegularExpression(pattern: "(?<![\\d.,])1 entries\\b")

    init() { load(AppLanguage.saved.resolved) }

    func load(_ code: String) {
        lock.lock(); defer { lock.unlock() }
        language = code
        exact = [:]; templates = []; fragments = [:]; cache = [:]; produced = []
        let folder = ProcessInfo.processInfo.environment["SHEPHERD_I18N_DIR"].map { URL(fileURLWithPath: $0) }
            ?? Bundle.main.resourceURL?.appendingPathComponent("workbench/i18n")
        guard code != "zh-Hans",
              let url = folder?.appendingPathComponent("\(code).json"),
              let data = try? Data(contentsOf: url),
              let table = try? JSONDecoder().decode([String: String].self, from: data) else { return }
        var frag: [Character: [(String, String)]] = [:]
        for (key, value) in table where !value.isEmpty {
            let ns = key as NSString
            let slots = Self.slot.matches(in: key, range: NSRange(location: 0, length: ns.length))
            if slots.isEmpty {
                exact[key] = value
                if let first = key.first { frag[first, default: []].append((key, value)) }
                continue
            }
            var pattern = "^", anchor = "", cursor = 0
            for m in slots {
                let literal = Self.unescape(ns.substring(with: NSRange(location: cursor, length: m.range.location - cursor)))
                if anchor.count < literal.count { anchor = literal }
                pattern += NSRegularExpression.escapedPattern(for: literal) + "(.+?)"
                cursor = m.range.location + m.range.length
            }
            let tail = Self.unescape(ns.substring(from: cursor))
            if anchor.count < tail.count { anchor = tail }
            pattern += NSRegularExpression.escapedPattern(for: tail) + "$"
            if let regex = try? NSRegularExpression(pattern: pattern, options: [.dotMatchesLineSeparators]) {
                templates.append((regex, anchor, value))
            }
        }
        for (k, list) in frag { fragments[k] = list.sorted { $0.0.count > $1.0.count }.map { (key: $0.0, value: $0.1) } }
    }

    private static func unescape(_ s: String) -> String { s.replacingOccurrences(of: "{{", with: "{").replacingOccurrences(of: "}}", with: "}") }
    static func hasHan(_ s: String) -> Bool { s.unicodeScalars.contains { (0x4E00...0x9FFF).contains($0.value) } }

    func translate(_ s: String) -> String {
        if language == "zh-Hans" || !Self.hasHan(s) { return s }
        lock.lock(); defer { lock.unlock() }
        if let hit = cache[s] { return hit }
        if produced.contains(s) { return s }
        let translated = inner(s)
        let result = translated.map(finish) ?? s
        if translated == nil, ProcessInfo.processInfo.environment["SHEPHERD_I18N_LOG"] != nil, !misses.contains(s) {
            misses.insert(s)
            FileHandle.standardError.write(("I18N-MISS\t" + s.replacingOccurrences(of: "\n", with: "\\n") + "\n").data(using: .utf8)!)
        }
        cache[s] = result
        if result != s { produced.insert(result) }
        return result
    }

    /// nil when some Chinese text in `s` has no translation.
    private func inner(_ s: String) -> String? {
        if !Self.hasHan(s) { return s }
        if let e = exact[s] { return e }
        let ns = s as NSString
        for t in templates where t.anchor.isEmpty || s.contains(t.anchor) {
            guard let m = t.regex.firstMatch(in: s, range: NSRange(location: 0, length: ns.length)) else { continue }
            var filled = Self.unescape(t.target), ok = true
            for i in (1..<m.numberOfRanges).reversed() {      // reversed: {10} before {1}
                guard let v = inner(ns.substring(with: m.range(at: i))) else { ok = false; break }
                filled = filled.replacingOccurrences(of: "{\(i - 1)}", with: v)
            }
            if ok { return filled }
        }
        if let f = fragmentsOnly(s) { return f }
        // Pieces joined by a separator ("日期：第 7 天", "券商 A · 正股 83"): translate each piece, all or nothing.
        // A piece may also be a table entry together with its separator (" · 正股 {0}").
        for sep in [" · ", "：", "；", "，", " | ", "、", " "] where s.contains(sep) {
            var out = "", ok = true
            for (i, part) in s.components(separatedBy: sep).enumerated() {
                if let t = inner(part) { out += (i > 0 ? sep : "") + t; continue }
                if i > 0, let t = direct(sep + part) { out += t; continue }
                ok = false; break
            }
            if ok { return out }
        }
        return nil
    }

    /// Exact entry or whole-string template only; no splitting, so it cannot recurse back into the separator loop.
    private func direct(_ s: String) -> String? {
        if let e = exact[s] { return e }
        let ns = s as NSString
        for t in templates where t.anchor.isEmpty || s.contains(t.anchor) {
            guard let m = t.regex.firstMatch(in: s, range: NSRange(location: 0, length: ns.length)) else { continue }
            var filled = Self.unescape(t.target), ok = true
            for i in (1..<m.numberOfRanges).reversed() {
                guard let v = inner(ns.substring(with: m.range(at: i))) else { ok = false; break }
                filled = filled.replacingOccurrences(of: "{\(i - 1)}", with: v)
            }
            if ok { return filled }
        }
        return nil
    }

    private func fragmentsOnly(_ s: String) -> String? {
        var out = "", i = s.startIndex
        while i < s.endIndex {
            if let list = fragments[s[i]], let hit = list.first(where: { s[i...].hasPrefix($0.key) }) {
                out += hit.value
                i = s.index(i, offsetBy: hit.key.count)
                continue
            }
            if Self.hasHan(String(s[i])) { return nil }
            out.append(s[i]); i = s.index(after: i)
        }
        return out
    }

    /// English reads better with ASCII punctuation once all the Chinese is gone.
    private func finish(_ s: String) -> String {
        guard language == "en" else { return s }
        var r = s
        for (a, b) in [("，", ", "), ("：", ": "), ("；", "; "), ("（", " ("), ("）", ") "), ("、", ", "), ("。", ". "),
                       ("「", "\""), ("」", "\""), ("“", "\""), ("”", "\""), ("！", "! "), ("？", "? "), ("～", "~")] {
            r = r.replacingOccurrences(of: a, with: b)
        }
        while r.contains("  ") { r = r.replacingOccurrences(of: "  ", with: " ") }
        r = Self.singular.stringByReplacingMatches(in: r, range: NSRange(location: 0, length: (r as NSString).length), withTemplate: "1 $1")
        r = Self.singularEntry.stringByReplacingMatches(in: r, range: NSRange(location: 0, length: (r as NSString).length), withTemplate: "1 entry")
        for (a, b) in [(" )", ")"), ("( ", "("), (" ,", ","), (" .", "."), (" :", ":"), (" ;", ";"), (") ,", "),"), (") .", ").")] {
            r = r.replacingOccurrences(of: a, with: b)
        }
        return r.trimmingCharacters(in: .whitespaces)
    }
}

func L(_ s: String) -> String { Translator.shared.translate(s) }

// MARK: SwiftUI entry points: plain strings (literals included) go through L() before they are shown.

extension Text { init(_ s: String) { self.init(verbatim: L(s)) } }
extension Button where Label == Text {
    init(_ title: String, action: @escaping () -> Void) { self.init(action: action) { Text(verbatim: L(title)) } }
    init(_ title: String, role: ButtonRole?, action: @escaping () -> Void) { self.init(role: role, action: action) { Text(verbatim: L(title)) } }
}
extension SwiftUI.Label where Title == Text, Icon == Image {
    init(_ title: String, systemImage name: String) { self.init { Text(verbatim: L(title)) } icon: { Image(systemName: name) } }
}
extension Toggle where Label == Text {
    init(_ title: String, isOn: Binding<Bool>) { self.init(isOn: isOn) { Text(verbatim: L(title)) } }
}
extension Picker where Label == Text {
    init(_ title: String, selection: Binding<SelectionValue>, @ViewBuilder content: () -> Content) {
        self.init(selection: selection, content: content) { Text(verbatim: L(title)) }
    }
}
extension TextField where Label == Text {
    init(_ title: String, text: Binding<String>) { self.init(text: text, prompt: Text(verbatim: L(title))) { Text(verbatim: L(title)) } }
    init(_ title: String, text: Binding<String>, prompt: Text?) { self.init(text: text, prompt: prompt) { Text(verbatim: L(title)) } }
}
extension Section where Parent == Text, Content: View, Footer == EmptyView {
    init(_ title: String, @ViewBuilder content: () -> Content) { self.init(content: content, header: { Text(verbatim: L(title)) }) }
}
extension DisclosureGroup where Label == Text {
    init(_ title: String, @ViewBuilder content: @escaping () -> Content) { self.init(content: content) { Text(verbatim: L(title)) } }
    init(_ title: String, isExpanded: Binding<Bool>, @ViewBuilder content: @escaping () -> Content) {
        self.init(isExpanded: isExpanded, content: content) { Text(verbatim: L(title)) }
    }
}
extension Stepper where Label == Text {
    init<V: Strideable>(_ title: String, value: Binding<V>, in bounds: ClosedRange<V>) { self.init(value: value, in: bounds) { Text(verbatim: L(title)) } }
}
extension LabeledContent where Label == Text, Content: View {
    init(_ title: String, @ViewBuilder content: () -> Content) { self.init(content: content) { Text(verbatim: L(title)) } }
}
extension LabeledContent where Label == Text, Content == Text {
    init(_ title: String, value: String) { self.init { Text(verbatim: L(value)) } label: { Text(verbatim: L(title)) } }
}
extension View {
    func help(_ s: String) -> some View { help(Text(verbatim: L(s))) }
    func navigationTitle(_ s: String) -> some View { navigationTitle(Text(verbatim: L(s))) }
    func accessibilityLabel(_ s: String) -> ModifiedContent<Self, AccessibilityAttachmentModifier> { accessibilityLabel(Text(verbatim: L(s))) }
    func alert<A: View, M: View>(_ title: String, isPresented: Binding<Bool>, @ViewBuilder actions: () -> A, @ViewBuilder message: () -> M) -> some View {
        alert(Text(verbatim: L(title)), isPresented: isPresented, actions: actions, message: message)
    }
    func confirmationDialog<A: View>(_ title: String, isPresented: Binding<Bool>, @ViewBuilder actions: () -> A) -> some View {
        confirmationDialog(Text(verbatim: L(title)), isPresented: isPresented, actions: actions)
    }
}
