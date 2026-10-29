import SwiftUI

/// 月历视图：一格一天，格子里放当天的事件小标签，点一天在下面看全部。数据和列表视图是同一份。
struct CalendarGrid: View {
    let events: [J]
    let today: String
    @ViewState private var monthOffset = 0
    @ViewState private var picked: String?
    private static let cal: Calendar = {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        c.firstWeekday = 2                                // 周一排第一列
        return c
    }()
    private static let iso: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC"); return f
    }()
    private var cal: Calendar { Self.cal }
    private var todayDate: Date { Self.iso.date(from: String(today.prefix(10))) ?? Date() }
    private var monthStart: Date {
        let first = cal.date(from: cal.dateComponents([.year, .month], from: todayDate)) ?? todayDate
        return cal.date(byAdding: .month, value: monthOffset, to: first) ?? first
    }
    private var byDay: [String: [J]] { Dictionary(grouping: events, by: { $0["day"].s }) }
    /// 每格一个日期字符串；月初前、月末后的空位是 nil，补满整周。
    private var cells: [String?] {
        let lead = (cal.component(.weekday, from: monthStart) - cal.firstWeekday + 7) % 7
        let count = cal.range(of: .day, in: .month, for: monthStart)?.count ?? 30
        var out: [String?] = Array(repeating: nil, count: lead)
        for d in 0..<count { out.append(Self.iso.string(from: cal.date(byAdding: .day, value: d, to: monthStart) ?? monthStart)) }
        while out.count % 7 != 0 { out.append(nil) }
        return out
    }
    private var title: String {
        let c = cal.dateComponents([.year, .month], from: monthStart)
        return "\(c.year ?? 0) 年 \(c.month ?? 0) 月"
    }
    private var chosen: String { picked ?? today }
    private let weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    static func tint(_ e: J) -> Color {
        switch e["kind"].s {
        case "财报": return e["held"].b ? PortfolioStyle.data : e["core"].b ? Color.purple : Color.secondary
        case "到期": return PortfolioStyle.warn
        case "复核", "自定义": return Color.green
        default: return Color.secondary
        }
    }
    /// 小标签上写的字：财报和到期只写代码，其余写事件名。持仓财报前面加「★」。
    static func chip(_ e: J) -> String {
        switch e["kind"].s {
        case "财报": return (e["held"].b ? "★ " : e["core"].b ? "◆ " : "") + e["symbol"].s + " 财报"
        case "到期": return e["symbol"].s + " 到期"
        default: return e["name"].s
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Button { monthOffset -= 1 } label: { Image(systemName: "chevron.left") }.buttonStyle(.borderless)
                Text(title).font(.title3.weight(.semibold)).frame(minWidth: 120)
                Button { monthOffset += 1 } label: { Image(systemName: "chevron.right") }.buttonStyle(.borderless)
                Button("今天") { monthOffset = 0; picked = nil }.disabled(monthOffset == 0 && picked == nil)
                Spacer()
                legend
            }
            HStack(spacing: 4) {
                ForEach(weekdays, id: \.self) { Text($0).font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity) }
            }
            let rows = cells.chunked(7)
            VStack(spacing: 4) {
                ForEach(Array(rows.enumerated()), id: \.offset) { _, week in
                    HStack(spacing: 4) {
                        ForEach(Array(week.enumerated()), id: \.offset) { _, day in cell(day) }
                    }
                }
            }
            if events.isEmpty || !hasEventsInMonth {
                Text("这个月没有事件。日历数据覆盖最近 7 天到未来 90 天（大科技财报看到 200 天）。").font(.caption).foregroundStyle(.secondary)
            }
            detail
        }
    }

    private var hasEventsInMonth: Bool { cells.contains { $0.map { byDay[$0] != nil } ?? false } }

    private var legend: some View {
        HStack(spacing: 10) {
            ForEach([("★ 持仓财报", PortfolioStyle.data), ("◆ 大科技财报", Color.purple), ("到期", PortfolioStyle.warn), ("宏观 / 其他财报", Color.secondary), ("复核 / 自定义", Color.green)], id: \.0) { item in
                HStack(spacing: 4) { Circle().fill(item.1).frame(width: 7, height: 7); Text(item.0).font(.caption2).foregroundStyle(.secondary) }
            }
        }
    }

    @ViewBuilder private func cell(_ day: String?) -> some View {
        if let day {
            let list = byDay[day] ?? []
            let isToday = day == today.prefix(10).description
            let isChosen = day == chosen
            Button { picked = day } label: {
                VStack(alignment: .leading, spacing: 3) {
                    HStack {
                        Text(String(Int(day.suffix(2)) ?? 0)).font(.caption.weight(isToday ? .bold : .regular)).monospacedDigit()
                            .foregroundStyle(isToday ? Color.white : Color.primary)
                            .padding(.horizontal, isToday ? 5 : 0).background(isToday ? Color.accentColor : Color.clear, in: Capsule())
                        Spacer(minLength: 0)
                    }
                    ForEach(Array(list.prefix(3).enumerated()), id: \.offset) { _, e in
                        Text(Self.chip(e)).font(.caption2.weight(e["held"].b || e["core"].b ? .semibold : .regular)).lineLimit(1)
                            .padding(.horizontal, 4).padding(.vertical, 1).frame(maxWidth: .infinity, alignment: .leading)
                            .background(Self.tint(e).opacity(0.2), in: RoundedRectangle(cornerRadius: 4))
                            .foregroundStyle(Self.tint(e))
                    }
                    if list.count > 3 { Text("还有 \(list.count - 3) 条").font(.caption2).foregroundStyle(.secondary) }
                    Spacer(minLength: 0)
                }
                .padding(5).frame(maxWidth: .infinity, minHeight: 96, maxHeight: 96, alignment: .topLeading)
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(isChosen ? Color.accentColor : Color.secondary.opacity(0.15), lineWidth: isChosen ? 2 : 1))
            }.buttonStyle(.plain).help(list.map { $0["name"].s }.joined(separator: "\n"))
        } else {
            Color.clear.frame(maxWidth: .infinity, minHeight: 96, maxHeight: 96)
        }
    }

    @ViewBuilder private var detail: some View {
        let list = byDay[chosen] ?? []
        VStack(alignment: .leading, spacing: 6) {
            let days = daysBetween(today, chosen)
            Text(dayLabel(chosen) + (days.map { $0 >= 0 ? " · " + countdown($0) : " · 已过" } ?? "")).font(.subheadline.weight(.semibold))
            if list.isEmpty { Text("这一天没有事件").font(.callout).foregroundStyle(.secondary) }
            else {
                VStack(spacing: 0) {
                    ForEach(Array(list.enumerated()), id: \.offset) { index, e in
                        if index > 0 { Divider() }
                        EventRow(event: e).padding(.vertical, 8)
                    }
                }.padding(.horizontal, 14).padding(.vertical, 2).card(padding: 0)
            }
        }
    }
}

extension Array {
    func chunked(_ size: Int) -> [[Element]] {
        stride(from: 0, to: count, by: size).map { Array(self[$0..<Swift.min($0 + size, count)]) }
    }
}
