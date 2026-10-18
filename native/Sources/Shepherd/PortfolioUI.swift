import SwiftUI
import AppKit
import Charts

// Presentation only: values come from the existing state response.
enum PortfolioStyle {
    static let data = Color.blue
    /// Amber for "look at this". Deliberately not the orange accent (navigation / selection), so a
    /// warning is never mistaken for a highlighted control.
    static let warn = Color(nsColor: NSColor(name: nil) { appearance in
        appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            ? NSColor(red: 1.0, green: 0.80, blue: 0.22, alpha: 1)
            : NSColor(red: 0.70, green: 0.48, blue: 0.0, alpha: 1)
    })
    static func risk(_ status: String) -> Color {
        status == "over" ? .red : ["near", "partial"].contains(status) ? warn : data
    }
    static func level(_ level: String) -> Color {
        switch level { case "bad", "red": return .red; case "warn", "amber": return warn; case "ok": return .green; default: return .secondary }
    }
}

extension View {
    /// The one card look used across pages (was three: GroupBox, bordered panel, tinted fill).
    func card(padding: CGFloat = 16) -> some View {
        self.padding(padding).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Color.secondary.opacity(0.15)))
    }
}

private let isoDay: DateFormatter = {
    let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; f.locale = Locale(identifier: "en_US_POSIX")
    f.timeZone = TimeZone(identifier: "UTC"); return f
}()
/// Whole days from one ISO date string to another (both read as plain calendar dates).
func daysBetween(_ from: String, _ to: String) -> Int? {
    guard let a = isoDay.date(from: String(from.prefix(10))), let b = isoDay.date(from: String(to.prefix(10))) else { return nil }
    return Calendar(identifier: .gregorian).dateComponents([.day], from: a, to: b).day
}
func countdown(_ days: Int) -> String { days <= 0 ? (days == 0 ? "今天" : "已过") : days == 1 ? "明天" : "\(days) 天后" }
func dayLabel(_ iso: String) -> String {
    guard let d = isoDay.date(from: String(iso.prefix(10))) else { return iso }
    let f = DateFormatter(); f.locale = Locale(identifier: "zh_CN"); f.timeZone = TimeZone(identifier: "UTC"); f.dateFormat = "M月d日 EEEE"
    return f.string(from: d)
}
func shortDay(_ iso: String) -> String { iso.count >= 10 ? String(iso.dropFirst(5).prefix(5)) : iso }
/// Open-vs-snapshot-mark result. A snapshot price, not an intraday P&L.
func unrealized(_ p: J) -> Double? {
    guard let open = p["open"].n, let mark = p["mark"].n, let qty = p["qty"].n else { return nil }
    return (mark - open) * qty * (p["mult"].n ?? 1) * (p["side"].s == "short" ? -1 : 1)
}
func unrealizedPct(_ p: J) -> Double? {
    guard let open = p["open"].n, let mark = p["mark"].n, open != 0 else { return nil }
    return (mark - open) / abs(open) * 100 * (p["side"].s == "short" ? -1 : 1)
}
func signedUSD(_ value: Double?) -> String {
    guard let value else { return "—" }
    let text = dollars(abs(value), digits: 0...0)
    return abs(value) < 0.5 ? text : (value >= 0 ? "+" : "−") + text
}
func signedPct(_ value: Double?, digits: Int = 1) -> String {
    guard let value else { return "—" }
    let text = num(.number(abs(value)), digits: digits) + "%"
    return abs(value) < 0.5 * pow(10, -Double(digits)) ? text : (value >= 0 ? "+" : "−") + text
}
func usd0(_ value: Double?) -> String { value.map { dollars($0, digits: 0...0) } ?? "—" }
/// 盈亏的颜色：赚绿、亏红，不到 0.5 美元算零。
func pnlTint(_ value: Double?) -> Color { guard let value, abs(value) >= 0.5 else { return .secondary }; return value > 0 ? .green : .red }
func compactUSD(_ value: Double?) -> String {
    guard let value else { return "—" }
    let a = abs(value)
    let text = a >= 1000 ? "$" + (a / 1000).formatted(.number.precision(.fractionLength(1))) + "k" : "$" + a.formatted(.number.precision(.fractionLength(0)))
    return (value < 0 ? "−" : "") + text
}

struct CashSnapshot {
    let amount: Double?
    let percent: Double?
    init(_ snapshot: J) {
        let accounts = snapshot["accounts"].items
        let cash = accounts.compactMap { $0["cash"].n }
        amount = !accounts.isEmpty && cash.count == accounts.count && cash.allSatisfy(\.isFinite) ? cash.reduce(0, +) : nil
        if let amount, let nlv = snapshot["nlv"].n, nlv > 0 { percent = amount / nlv * 100 }
        else { percent = nil }
    }
}

struct DashboardMetric: View {
    let title: String
    let value: String
    var suffix = ""
    var detail = ""
    var warning = ""
    var note = ""
    var fraction: Double? = nil
    var color: Color = .blue
    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            Text(title).font(.callout).foregroundStyle(.secondary)
            HStack(alignment: .firstTextBaseline, spacing: 5) {
                Text(value).font(.system(size: 28, weight: .semibold, design: .rounded)).monospacedDigit()
                if !suffix.isEmpty { Text(suffix).font(.callout).foregroundStyle(.secondary) }
            }.lineLimit(1).minimumScaleFactor(0.8)
            if let fraction {
                GeometryReader { geometry in
                    ZStack(alignment: .leading) {
                        Capsule().fill(Color.secondary.opacity(0.12))
                        Capsule().fill(color).frame(width: geometry.size.width * min(1, max(0, fraction)))
                    }
                }.frame(height: 5).accessibilityHidden(true)
            }
            Text(detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if !warning.isEmpty { Label(warning, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(PortfolioStyle.warn).fixedSize(horizontal: false, vertical: true) }
            if !note.isEmpty { Label(note, systemImage: "checkmark.shield").font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
            Spacer(minLength: 0)
        }.card()
    }
}

struct PortfolioPanel<Content: View>: View {
    let title: String
    @ViewBuilder var content: () -> Content
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title).font(.headline)
            content()
        }.card()
    }
}

struct RiskScaleBar: View {
    let value: Double?
    let lower: Double
    let upper: Double
    var cap: Double? = nil
    var color: Color = .blue
    func fraction(_ number: Double) -> Double { min(1, max(0, (number - lower) / max(upper - lower, 0.0001))) }
    var body: some View {
        GeometryReader { geometry in
            let zero = fraction(0) * geometry.size.width
            ZStack(alignment: .leading) {
                Capsule().fill(Color.secondary.opacity(0.10)).frame(height: 7)
                if let value {
                    let end = fraction(value) * geometry.size.width
                    RoundedRectangle(cornerRadius: 3).fill(color).frame(width: abs(end - zero), height: 9).offset(x: min(end, zero))
                }
                if lower < 0 { Rectangle().fill(.secondary).frame(width: 1, height: 17).offset(x: zero) }
                if let cap { Rectangle().fill(.primary.opacity(0.7)).frame(width: 2, height: 18).offset(x: min(geometry.size.width - 2, fraction(cap) * geometry.size.width)) }
            }.frame(height: 20)
        }.frame(height: 20).accessibilityHidden(true)
    }
}

struct RiskScaleLabels: View {
    let lower: Double
    let upper: Double
    var unit = "R"
    var body: some View {
        HStack {
            Text(num(.number(lower)))
            Spacer()
            Text(num(.number((lower + upper) / 2)))
            Spacer()
            Text(num(.number(upper)) + " " + unit)
        }.font(.caption2).monospacedDigit().foregroundStyle(.secondary)
    }
}

struct LossHistogram: View {
    let risk: J
    struct Bin: Identifiable { let id: Int; let lower: Double; let upper: Double; let count: Int }
    var values: [Double] { risk["losses"].items.compactMap(\.n).filter(\.isFinite) }
    var bins: [Bin] {
        guard let low = values.min(), let high = values.max() else { return [] }
        let count = high == low ? 1 : min(24, max(6, Int(Double(values.count).squareRoot())))
        let width = high == low ? max(1, abs(low) * 0.02) : (high - low) / Double(count)
        let start = high == low ? low - width / 2 : low
        var counts = Array(repeating: 0, count: count)
        for value in values { counts[min(count - 1, max(0, Int((value - start) / width)))] += 1 }
        return counts.enumerated().map { Bin(id: $0.offset, lower: start + Double($0.offset) * width, upper: start + Double($0.offset + 1) * width, count: $0.element) }
    }
    var body: some View {
        if !bins.isEmpty {
            Chart {
                ForEach(bins) { bin in
                    RectangleMark(xStart: .value("损失下界", bin.lower), xEnd: .value("损失上界", bin.upper),
                                  yStart: .value("历史样本数", 0), yEnd: .value("历史样本数", bin.count))
                        .foregroundStyle(PortfolioStyle.data.opacity(0.7))
                        .accessibilityLabel("损失 \(usd(.number(bin.lower))) 至 \(usd(.number(bin.upper)))")
                        .accessibilityValue("\(bin.count) 个样本")
                }
                if let var95 = risk["var95"].n {
                    RuleMark(x: .value("VaR 95%", var95)).foregroundStyle(PortfolioStyle.warn).lineStyle(StrokeStyle(lineWidth: 2, dash: [4, 3]))
                        .annotation(position: .top, alignment: .trailing) { Text("VaR 95%").font(.caption) }
                }
            }.chartXAxisLabel("一日损失（美元，负数为盈利）").chartYAxisLabel("样本数")
                .frame(height: 220).accessibilityLabel("股票子组合历史一日损失直方图")
        }
    }
}
