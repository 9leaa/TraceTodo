import SwiftUI

// MARK: - Calendar Timeline DTOs

struct CalendarAllDayTaskItem: Codable, Identifiable, Hashable {
    let memoryId: String
    let version: Int
    let title: String
    let quadrant: String
    let taskStatus: String
    let isDone: Bool
    let isOverdue: Bool
    let colorTheme: String
    let dueAt: String

    var id: String { memoryId }
    var macaron: MacaronTheme { MacaronTheme(rawValue: colorTheme) ?? .sky }

    enum CodingKeys: String, CodingKey {
        case memoryId = "memory_id"
        case version
        case title
        case quadrant
        case taskStatus = "task_status"
        case isDone = "is_done"
        case isOverdue = "is_overdue"
        case colorTheme = "color_theme"
        case dueAt = "due_at"
    }
}

struct CalendarTimedBlockItem: Codable, Identifiable, Hashable {
    let memoryId: String
    let version: Int
    let title: String
    let quadrant: String
    let taskStatus: String
    let isDone: Bool
    let isOverdue: Bool
    let colorTheme: String
    let dueAt: String
    let startMinute: Int
    let endMinute: Int
    let durationMinutes: Int
    let timeRangeLabel: String
    let colIdx: Int
    let colTotal: Int

    var id: String { memoryId }
    var macaron: MacaronTheme { MacaronTheme(rawValue: colorTheme) ?? .sky }

    enum CodingKeys: String, CodingKey {
        case memoryId = "memory_id"
        case version
        case title
        case quadrant
        case taskStatus = "task_status"
        case isDone = "is_done"
        case isOverdue = "is_overdue"
        case colorTheme = "color_theme"
        case dueAt = "due_at"
        case startMinute = "start_minute"
        case endMinute = "end_minute"
        case durationMinutes = "duration_minutes"
        case timeRangeLabel = "time_range_label"
        case colIdx = "col_idx"
        case colTotal = "col_total"
    }
}

struct CalendarFocusBlockItem: Codable, Identifiable, Hashable {
    let sessionId: String
    let taskMemoryId: String
    let title: String
    let mode: String
    let startMinute: Int
    let endMinute: Int
    let durationMinutes: Int
    let timeRangeLabel: String

    var id: String { sessionId }

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case taskMemoryId = "task_memory_id"
        case title
        case mode
        case startMinute = "start_minute"
        case endMinute = "end_minute"
        case durationMinutes = "duration_minutes"
        case timeRangeLabel = "time_range_label"
    }
}

struct CalendarCountdownBadge: Codable, Identifiable, Hashable {
    let countdownId: String
    let title: String
    let icon: String
    let daysDelta: Int?

    var id: String { countdownId }

    enum CodingKeys: String, CodingKey {
        case countdownId = "countdown_id"
        case title
        case icon
        case daysDelta = "days_delta"
    }
}

struct CalendarDayBucket: Codable, Identifiable, Hashable {
    let date: String
    let year: Int
    let month: Int
    let day: Int
    let weekday: Int
    let weekdayLabel: String
    let isToday: Bool
    let isCurrentMonth: Bool
    let isWeekend: Bool
    let allDayTasks: [CalendarAllDayTaskItem]
    let timedBlocks: [CalendarTimedBlockItem]
    let focusBlocks: [CalendarFocusBlockItem]
    let countdowns: [CalendarCountdownBadge]
    let noteCount: Int
    let busyMinutes: Int
    let awakeFreeMinutes: Int
    let focusMinutes: Int
    let lunarMonthCn: String?
    let lunarDayCn: String?
    let lunarShort: String?
    let lunarFull: String?
    let solarTerm: String?
    let festivals: [String]?
    let calendarSubLabel: String?
    let calendarSubKind: String?
    let holidayStatus: String?
    let holidayName: String?
    let isOffDay: Bool?

    var id: String { date }

    var effectiveIsOffDay: Bool {
        if let v = isOffDay { return v }
        if holidayStatus == "rest" { return true }
        if holidayStatus == "work" { return false }
        return isWeekend
    }

    var tooltipSummary: String {
        var parts: [String] = [date]
        if let lf = lunarFull, !lf.isEmpty {
            parts.append(lf)
        } else if let ls = lunarShort, !ls.isEmpty {
            parts.append("农历\(ls)")
        }
        if let st = solarTerm, !st.isEmpty {
            parts.append("节气：\(st)")
        }
        if let fests = festivals, !fests.isEmpty {
            parts.append("节日：\(fests.joined(separator: "、"))")
        }
        if holidayStatus == "rest" {
            let name = (holidayName ?? "").isEmpty ? "休息日" : (holidayName ?? "休息日")
            parts.append("法定休假：\(name)")
        } else if holidayStatus == "work" {
            let name = (holidayName ?? "").isEmpty ? "调休" : (holidayName ?? "调休")
            parts.append("调休补班：\(name)")
        } else if isWeekend {
            parts.append("周末休息日")
        }
        return parts.joined(separator: " · ")
    }

    enum CodingKeys: String, CodingKey {
        case date, year, month, day, weekday
        case weekdayLabel = "weekday_label"
        case isToday = "is_today"
        case isCurrentMonth = "is_current_month"
        case isWeekend = "is_weekend"
        case allDayTasks = "all_day_tasks"
        case timedBlocks = "timed_blocks"
        case focusBlocks = "focus_blocks"
        case countdowns
        case noteCount = "note_count"
        case busyMinutes = "busy_minutes"
        case awakeFreeMinutes = "awake_free_minutes"
        case focusMinutes = "focus_minutes"
        case lunarMonthCn = "lunar_month_cn"
        case lunarDayCn = "lunar_day_cn"
        case lunarShort = "lunar_short"
        case lunarFull = "lunar_full"
        case solarTerm = "solar_term"
        case festivals
        case calendarSubLabel = "calendar_sub_label"
        case calendarSubKind = "calendar_sub_kind"
        case holidayStatus = "holiday_status"
        case holidayName = "holiday_name"
        case isOffDay = "is_off_day"
    }
}

struct CalendarUnscheduledTask: Codable, Identifiable, Hashable {
    let memoryId: String
    let version: Int
    let title: String
    let quadrant: String
    let quadrantLabel: String
    let colorTheme: String
    let durationMinutes: Int

    var id: String { memoryId }
    var macaron: MacaronTheme { MacaronTheme(rawValue: colorTheme) ?? .sky }

    enum CodingKeys: String, CodingKey {
        case memoryId = "memory_id"
        case version
        case title
        case quadrant
        case quadrantLabel = "quadrant_label"
        case colorTheme = "color_theme"
        case durationMinutes = "duration_minutes"
    }
}

struct CalendarSummaryStats: Codable, Hashable {
    let totalScheduledTasks: Int
    let totalAllDayTasks: Int
    let totalTimedBlocks: Int
    let totalBusyMinutes: Int
    let totalBusyHours: Double
    let totalFocusSessions: Int
    let totalFocusMinutes: Int
    let unscheduledCount: Int

    enum CodingKeys: String, CodingKey {
        case totalScheduledTasks = "total_scheduled_tasks"
        case totalAllDayTasks = "total_all_day_tasks"
        case totalTimedBlocks = "total_timed_blocks"
        case totalBusyMinutes = "total_busy_minutes"
        case totalBusyHours = "total_busy_hours"
        case totalFocusSessions = "total_focus_sessions"
        case totalFocusMinutes = "total_focus_minutes"
        case unscheduledCount = "unscheduled_count"
    }
}

struct CalendarOverviewResponse: Codable {
    let spaceId: String
    let view: String
    let anchorDate: String
    let todayDate: String
    let nowMinute: Int
    let periodLabel: String
    let rangeStart: String
    let rangeEnd: String
    let days: [CalendarDayBucket]
    let unscheduledTasks: [CalendarUnscheduledTask]
    let summary: CalendarSummaryStats
    let todayLunarFull: String?

    enum CodingKeys: String, CodingKey {
        case spaceId = "space_id"
        case view
        case anchorDate = "anchor_date"
        case todayDate = "today_date"
        case nowMinute = "now_minute"
        case periodLabel = "period_label"
        case rangeStart = "range_start"
        case rangeEnd = "range_end"
        case days
        case unscheduledTasks = "unscheduled_tasks"
        case summary
        case todayLunarFull = "today_lunar_full"
    }
}

// MARK: - Calendar Subview Enum & Slot Target

enum CalendarCanvasMode: String, CaseIterable, Identifiable {
    case week = "week"
    case month = "month"
    case countdown = "countdown"

    var id: String { rawValue }
    var label: String {
        switch self {
        case .week: return "📊 周时间轴"
        case .month: return "🗓️ 月全景"
        case .countdown: return "⏳ 倒数日"
        }
    }
}

struct CalendarSlotTarget: Identifiable {
    let id = UUID()
    let date: String
    let hour: Int
    let isAllDay: Bool
}

// MARK: - Main Calendar Timeline & Month Panorama View

struct CalendarFoldBand: Identifiable, Equatable {
    let startHour: Int
    let endHour: Int // exclusive
    var id: String { "\(startHour)-\(endHour)" }
    var label: String { String(format: "%02d–%02d", startHour, endHour) }
}

struct CalendarWeekLayoutMetrics {
    let hourHeights: [CGFloat]
    let hourOffsets: [CGFloat]
    let totalHeight: CGFloat
    let foldedBands: [CalendarFoldBand]

    func y(forMinute minute: Int) -> CGFloat {
        let clamped = max(0, min(1440, minute))
        if clamped >= 1440 { return totalHeight }
        let h = clamped / 60
        let rem = CGFloat(clamped % 60) / 60.0
        return hourOffsets[h] + rem * hourHeights[h]
    }

    func foldedBandStarting(at hour: Int) -> CalendarFoldBand? {
        foldedBands.first { $0.startHour == hour }
    }

    func isInsideFoldedBand(_ hour: Int) -> Bool {
        foldedBands.contains { hour >= $0.startHour && hour < $0.endHour }
    }

    func heightForBand(_ band: CalendarFoldBand) -> CGFloat {
        (band.startHour..<band.endHour).reduce(CGFloat(0)) { $0 + hourHeights[$1] }
    }
}

struct CalendarTimelineView: View {
    @EnvironmentObject var store: TodoStore
    @State private var mode: CalendarCanvasMode = .week
    @State private var selectedDate: String = Self.todayYMD()
    @State private var overview: CalendarOverviewResponse?
    @State private var isLoading: Bool = false
    @State private var slotTarget: CalendarSlotTarget?
    @State private var editingTask: TaskItemModel?
    @State private var adaptiveDensity: Bool = true
    @State private var expandedBandIds: Set<String> = []

    // In-Place Calendar Task Editing State (`在原位置直接修改`)
    @State private var inlineTaskId: String = ""
    @State private var inlineTitle: String = ""
    @State private var inlineDate: Date = Date()
    @State private var inlineIsAllDay: Bool = false
    @State private var inlineHour: Int = 9
    @State private var inlineMinute: Int = 0
    @State private var inlineDuration: Int = 60
    @State private var inlineMacaron: MacaronTheme = .sky
    @State private var isSavingInline: Bool = false

    private let hourHeight: CGFloat = 44

    private func buildWeekLayout(days: [CalendarDayBucket]) -> CalendarWeekLayoutMetrics {
        guard adaptiveDensity, !days.isEmpty else {
            let heights = Array(repeating: hourHeight, count: 24)
            var offsets: [CGFloat] = []
            var acc: CGFloat = 0
            for h in 0..<24 {
                offsets.append(acc)
                acc += heights[h]
            }
            return CalendarWeekLayoutMetrics(hourHeights: heights, hourOffsets: offsets, totalHeight: acc, foldedBands: [])
        }

        var busyHours = Set<Int>()
        for d in days {
            for blk in d.timedBlocks {
                let sH = max(0, blk.startMinute / 60)
                let eH = min(23, max(sH, (max(blk.startMinute + 1, blk.endMinute) - 1) / 60))
                for h in sH...eH { busyHours.insert(h) }
            }
            for fb in d.focusBlocks {
                let sH = max(0, fb.startMinute / 60)
                let eH = min(23, max(sH, (max(fb.startMinute + 1, fb.endMinute) - 1) / 60))
                for h in sH...eH { busyHours.insert(h) }
            }
        }
        if days.contains(where: \.isToday), let nowMin = overview?.nowMinute {
            busyHours.insert(min(23, max(0, nowMin / 60)))
        }

        // Find contiguous idle runs across the entire 7-day week
        var bands: [CalendarFoldBand] = []
        var h = 0
        while h < 24 {
            if busyHours.contains(h) {
                h += 1
                continue
            }
            let start = h
            while h < 24 && !busyHours.contains(h) {
                h += 1
            }
            let end = h
            let len = end - start
            let touchesSleep = start < 7 || end > 22
            if (touchesSleep && len >= 2) || len >= 2 {
                let cand = CalendarFoldBand(startHour: start, endHour: end)
                if !expandedBandIds.contains(cand.id) {
                    bands.append(cand)
                }
            }
        }

        var heights = Array(repeating: hourHeight, count: 24)
        for b in bands {
            let len = max(1, b.endHour - b.startHour)
            let perHour = max(5.0, 28.0 / CGFloat(len))
            for hr in b.startHour..<b.endHour {
                heights[hr] = perHour
            }
        }

        var offsets: [CGFloat] = []
        var acc: CGFloat = 0
        for hr in 0..<24 {
            offsets.append(acc)
            acc += heights[hr]
        }
        return CalendarWeekLayoutMetrics(hourHeights: heights, hourOffsets: offsets, totalHeight: acc, foldedBands: bands)
    }

    static func todayYMD() -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "Asia/Shanghai") ?? .current
        return f.string(from: Date())
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                headerToolbar

                if mode == .countdown && store.isModuleEnabled(.countdown) {
                    CountdownWallView()
                } else {
                    statsStrip
                    if mode == .week {
                        weekTimelineCard
                    } else {
                        monthPanoramaCard
                    }
                    selectedDayAndUnscheduledSection
                }
            }
            .padding(14)
        }
        .task {
            await reloadCalendar()
        }
        .onChange(of: store.matrix?.summaryCounts.totalTodo) { _, _ in
            Task { await reloadCalendar() }
        }
        .sheet(item: $slotTarget) { target in
            SlotQuickScheduleSheet(
                initialDate: target.date,
                initialHour: target.hour,
                initialAllDay: target.isAllDay,
                unscheduledTasks: overview?.unscheduledTasks ?? []
            ) { scheduledDate in
                selectedDate = scheduledDate
                Task {
                    await store.refresh()
                    await reloadCalendar()
                }
            }
            .environmentObject(store)
        }
        .sheet(item: $editingTask) { task in
            TaskEditorSheet(existingTask: task)
                .environmentObject(store)
        }
    }

    private var headerToolbar: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(overview?.periodLabel ?? "时间画布")
                        .font(.headline.bold())
                    Text("零重复录入：共享四象限任务真值 + 番茄专注轨迹")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Picker("模式", selection: $mode) {
                    Text(CalendarCanvasMode.week.label).tag(CalendarCanvasMode.week)
                    Text(CalendarCanvasMode.month.label).tag(CalendarCanvasMode.month)
                    if store.isModuleEnabled(.countdown) {
                        Text(CalendarCanvasMode.countdown.label).tag(CalendarCanvasMode.countdown)
                    }
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 290)
                .onChange(of: mode) { _, newVal in
                    if newVal == .countdown {
                        Task { await store.refreshCountdowns() }
                    } else {
                        Task { await reloadCalendar() }
                    }
                }
            }

            if mode != .countdown {
                HStack(spacing: 8) {
                    Button {
                        shiftPeriod(-1)
                    } label: {
                        Image(systemName: "chevron.left")
                    }
                    .buttonStyle(.bordered)

                    Button("今天") {
                        selectedDate = Self.todayYMD()
                        Task { await reloadCalendar() }
                    }
                    .buttonStyle(.bordered)

                    Button {
                        shiftPeriod(1)
                    } label: {
                        Image(systemName: "chevron.right")
                    }
                    .buttonStyle(.bordered)

                    if mode == .week {
                        Button {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                if adaptiveDensity && !expandedBandIds.isEmpty {
                                    expandedBandIds.removeAll()
                                } else {
                                    adaptiveDensity.toggle()
                                    expandedBandIds.removeAll()
                                }
                            }
                        } label: {
                            Text(adaptiveDensity ? "↕ 自适应折叠" : "═ 等高展开")
                                .font(.caption.bold())
                        }
                        .buttonStyle(.bordered)
                    }

                    Spacer()

                    Button {
                        slotTarget = CalendarSlotTarget(date: selectedDate, hour: 9, isAllDay: false)
                    } label: {
                        Label("时段排期", systemImage: "plus")
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(store.activeTheme.primaryFillColor)
                }
            }
        }
    }

    private var offDayRedColor: Color {
        store.activeTheme.isDark
            ? Color(red: 0.98, green: 0.44, blue: 0.52)
            : Color(red: 0.88, green: 0.11, blue: 0.28)
    }

    private var solarTermTealColor: Color {
        store.activeTheme.isDark
            ? Color(red: 0.18, green: 0.83, blue: 0.75)
            : Color(red: 0.05, green: 0.58, blue: 0.53)
    }

    @ViewBuilder
    private func holidayMicroBadge(_ status: String?) -> some View {
        if status == "rest" {
            Text("休")
                .font(.system(size: 8.5, weight: .bold))
                .padding(.horizontal, 3.5)
                .padding(.vertical, 0.5)
                .background(offDayRedColor.opacity(0.15))
                .foregroundStyle(offDayRedColor)
                .clipShape(RoundedRectangle(cornerRadius: 3.5, style: .continuous))
        } else if status == "work" {
            Text("班")
                .font(.system(size: 8.5, weight: .bold))
                .padding(.horizontal, 3.5)
                .padding(.vertical, 0.5)
                .background(Color.orange.opacity(0.18))
                .foregroundStyle(store.activeTheme.isDark ? Color(red: 0.99, green: 0.75, blue: 0.25) : Color(red: 0.76, green: 0.36, blue: 0.04))
                .clipShape(RoundedRectangle(cornerRadius: 3.5, style: .continuous))
        }
    }

    @ViewBuilder
    private func calendarSubLabelText(_ d: CalendarDayBucket, size: CGFloat = 9.5) -> some View {
        let sub = (d.calendarSubLabel ?? "").isEmpty ? (d.lunarDayCn ?? "") : (d.calendarSubLabel ?? "")
        if !sub.isEmpty {
            let kind = d.calendarSubKind ?? "lunar_day"
            let fg: Color = {
                switch kind {
                case "festival":
                    return offDayRedColor
                case "solar_term":
                    return solarTermTealColor
                case "festival_minor":
                    return store.activeTheme.primaryColor
                case "lunar_month":
                    return .primary.opacity(0.82)
                default:
                    return .secondary
                }
            }()
            Text(sub)
                .font(.system(size: size, weight: (kind == "festival" || kind == "solar_term" || kind == "lunar_month") ? .bold : .medium))
                .underline(kind == "lunar_month")
                .foregroundStyle(fg)
                .lineLimit(1)
        }
    }

    @ViewBuilder
    private var statsStrip: some View {
        if let s = overview?.summary {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    if let lunarFull = overview?.todayLunarFull, !lunarFull.isEmpty {
                        statChip("🏮 \(lunarFull)")
                    }
                    statChip("📌 已排期 \(s.totalScheduledTasks) 项 (\(String(format: "%.1f", s.totalBusyHours))h)")
                    statChip("🍅 专注轨迹 \(s.totalFocusSessions) 次 (\(s.totalFocusMinutes)m)")
                    statChip("📥 待排期池 \(s.unscheduledCount) 项")
                }
            }
        }
    }

    private func statChip(_ text: String) -> some View {
        Text(text)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(store.activeTheme.primaryColor.opacity(0.10))
            .foregroundStyle(store.activeTheme.primaryColor)
            .clipShape(Capsule())
    }

    // MARK: - Week 00:00 - 24:00 Macaron Timeline

    @ViewBuilder
    private var weekTimelineCard: some View {
        let days = overview?.days ?? []
        if days.isEmpty && isLoading {
            ProgressView("正在加载周时间轴...")
                .frame(maxWidth: .infinity, minHeight: 240)
        } else {
            let minGridWidth = 52 + CGFloat(max(7, days.count)) * 106
            ScrollView(.horizontal, showsIndicators: true) {
                VStack(spacing: 0) {
                    weekHeaderRow(days: days)
                    Divider()
                    weekAllDayRow(days: days)
                    Divider()
                    weekTimeGridSection(days: days)
                }
                .frame(minWidth: minGridWidth)
            }
            .background(store.cardSurfaceBackground)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(Color.primary.opacity(0.09), lineWidth: 1)
            )
        }
    }

    private func weekHeaderRow(days: [CalendarDayBucket]) -> some View {
        HStack(spacing: 0) {
            Text("UTC+8")
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(.secondary)
                .frame(width: 52)

            ForEach(days) { d in
                weekDayHeaderCell(d)
            }
        }
        .background(Color.primary.opacity(0.03))
    }

    private func weekDayHeaderCell(_ d: CalendarDayBucket) -> some View {
        let busyH = Double(d.busyMinutes) / 60.0
        let freeH = Double(d.awakeFreeMinutes) / 60.0
        let offDay = d.effectiveIsOffDay
        return VStack(spacing: 2) {
            HStack(spacing: 3) {
                Text("\(d.weekdayLabel)\(d.isToday ? "·今" : "")")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(d.isToday ? store.activeTheme.primaryColor : (offDay ? offDayRedColor : .secondary))
                holidayMicroBadge(d.holidayStatus)
            }
            HStack(spacing: 4) {
                Text("\(d.month)/\(d.day)")
                    .font(.system(size: 14, weight: .heavy, design: .rounded))
                    .foregroundStyle(offDay ? offDayRedColor : (d.isToday ? store.activeTheme.primaryColor : .primary))
                calendarSubLabelText(d, size: 9.5)
            }
            Text("忙\(String(format: "%.1f", busyH))h 闲\(String(format: "%.0f", freeH))h")
                .font(.system(size: 9.5))
                .foregroundStyle(.secondary)
            if let cd = d.countdowns.first {
                Text("\(cd.icon) \(cd.title)")
                    .font(.system(size: 9.5, weight: .bold))
                    .foregroundStyle(store.activeTheme.primaryColor)
                    .lineLimit(1)
            }
        }
        .frame(width: 106)
        .padding(.vertical, 6)
        .background(d.isToday ? store.activeTheme.primaryColor.opacity(0.10) : (d.holidayStatus == "rest" ? offDayRedColor.opacity(0.05) : Color.clear))
        .overlay(alignment: .bottom) {
            if d.date == selectedDate {
                Rectangle()
                    .fill(store.activeTheme.primaryColor)
                    .frame(height: 2.5)
            }
        }
        .help(d.tooltipSummary)
        .contentShape(Rectangle())
        .onTapGesture {
            selectedDate = d.date
        }
    }

    private func weekAllDayRow(days: [CalendarDayBucket]) -> some View {
        let isDark = store.activeTheme.isDark
        return HStack(alignment: .top, spacing: 0) {
            Text("全天")
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(.secondary)
                .frame(width: 52, height: 32)

            ForEach(days) { d in
                VStack(alignment: .leading, spacing: 3) {
                    if d.allDayTasks.isEmpty {
                        Text("+ 全天")
                            .font(.system(size: 9.5))
                            .foregroundStyle(.tertiary)
                    } else {
                        ForEach(d.allDayTasks) { item in
                            Button {
                                openTaskEditor(memoryId: item.memoryId)
                            } label: {
                                Text("\(item.isDone ? "✓ " : "")\(item.title)")
                                    .font(.system(size: 10, weight: .semibold))
                                    .lineLimit(1)
                                    .padding(.horizontal, 5)
                                    .padding(.vertical, 2)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .background(isDark ? item.macaron.accentColor.opacity(0.24) : item.macaron.cardBackground)
                                    .foregroundStyle(isDark ? Color.white.opacity(0.92) : Color.black.opacity(0.82))
                                    .clipShape(RoundedRectangle(cornerRadius: 5))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
                .padding(4)
                .frame(width: 106, alignment: .topLeading)
                .contentShape(Rectangle())
                .onTapGesture {
                    slotTarget = CalendarSlotTarget(date: d.date, hour: 9, isAllDay: true)
                }
            }
        }
    }

    private func weekTimeGridSection(days: [CalendarDayBucket]) -> some View {
        let layout = buildWeekLayout(days: days)
        return ScrollView(.vertical, showsIndicators: true) {
            HStack(alignment: .top, spacing: 0) {
                weekHourGutter(layout: layout)
                ForEach(days) { d in
                    dayTimelineColumn(d, layout: layout)
                }
            }
        }
        .frame(maxHeight: 540)
    }

    private func weekHourGutter(layout: CalendarWeekLayoutMetrics) -> some View {
        ZStack(alignment: .topTrailing) {
            Color.clear.frame(width: 52, height: layout.totalHeight)

            ForEach(0..<24, id: \.self) { h in
                if !layout.isInsideFoldedBand(h) {
                    let prevFolded = h > 0 && layout.isInsideFoldedBand(h - 1)
                    Text(String(format: "%02d:00", h))
                        .font(.system(size: 10, weight: .semibold, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .padding(.trailing, 5)
                        .offset(y: layout.hourOffsets[h] + (prevFolded ? 2 : 0))
                }
            }

            ForEach(layout.foldedBands) { band in
                let bandTop = layout.hourOffsets[band.startHour]
                let bandH = layout.heightForBand(band)
                Button {
                    _ = withAnimation(.easeInOut(duration: 0.2)) {
                        expandedBandIds.insert(band.id)
                    }
                } label: {
                    Text(band.label)
                        .font(.system(size: 9.5, weight: .bold, design: .monospaced))
                        .padding(.horizontal, 5)
                        .padding(.vertical, 2)
                        .background(store.cardSurfaceBackground)
                        .foregroundStyle(.secondary)
                        .clipShape(Capsule())
                        .overlay(Capsule().stroke(Color.primary.opacity(0.14), lineWidth: 0.8))
                }
                .buttonStyle(.plain)
                .frame(width: 50, height: bandH, alignment: .center)
                .offset(y: bandTop)
                .help("全周空闲时段 \(band.label)，点击展开")
            }
        }
        .frame(width: 52, height: layout.totalHeight)
    }

    private func dayTimelineColumn(_ day: CalendarDayBucket, layout: CalendarWeekLayoutMetrics) -> some View {
        let colWidth: CGFloat = 106
        let isDark = store.activeTheme.isDark
        return ZStack(alignment: .topLeading) {
            // 24 Clickable Hour / Folded Band Slots
            VStack(spacing: 0) {
                ForEach(0..<24, id: \.self) { h in
                    let folded = layout.isInsideFoldedBand(h)
                    let isRest = h < 8 || h >= 22
                    Rectangle()
                        .fill(folded ? Color.primary.opacity(0.035) : (isRest ? Color.primary.opacity(0.018) : Color.clear))
                        .frame(width: colWidth, height: layout.hourHeights[h])
                        .overlay(alignment: .bottom) {
                            if !folded || layout.foldedBands.contains(where: { $0.endHour - 1 == h }) {
                                Rectangle()
                                    .fill(Color.primary.opacity(0.06))
                                    .frame(height: 0.6)
                            }
                        }
                        .contentShape(Rectangle())
                        .onTapGesture {
                            if let band = layout.foldedBands.first(where: { h >= $0.startHour && h < $0.endHour }) {
                                _ = withAnimation(.easeInOut(duration: 0.2)) {
                                    expandedBandIds.insert(band.id)
                                }
                            } else {
                                selectedDate = day.date
                                slotTarget = CalendarSlotTarget(date: day.date, hour: h, isAllDay: false)
                            }
                        }
                }
            }

            // Completed Focus Track Pills
            ForEach(day.focusBlocks) { fb in
                let topY = layout.y(forMinute: fb.startMinute)
                let botY = layout.y(forMinute: fb.endMinute)
                let hVal = max(17, botY - topY)
                Text("🍅\(fb.durationMinutes)m")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(isDark ? store.activeTheme.primaryColor : .green)
                    .lineLimit(1)
                    .padding(.horizontal, 4)
                    .frame(height: hVal, alignment: .center)
                    .background((isDark ? store.activeTheme.primaryColor : Color.green).opacity(0.16))
                    .clipShape(RoundedRectangle(cornerRadius: 5))
                    .offset(x: colWidth - 48, y: topY)
                    .allowsHitTesting(false)
            }

            // Scheduled Macaron Task Blocks (single-line when compact, multi-line when tall — zero text overlap!)
            ForEach(day.timedBlocks) { blk in
                let topY = layout.y(forMinute: blk.startMinute)
                let botY = layout.y(forMinute: blk.endMinute)
                let hVal = max(20, botY - topY - 1.5)
                let totalCols = CGFloat(max(1, blk.colTotal))
                let idx = CGFloat(min(max(0, blk.colIdx), max(0, blk.colTotal - 1)))
                let blkW = max(36, (colWidth - 4) / totalCols - 2)
                let offsetX = 2 + idx * ((colWidth - 4) / totalCols)
                let cardBg = isDark ? blk.macaron.accentColor.opacity(0.28) : blk.macaron.cardBackground
                let titleFg = isDark ? Color.white.opacity(0.94) : Color.black.opacity(0.85)
                let timeFg = isDark ? Color.white.opacity(0.78) : blk.macaron.accentColor

                Button {
                    openTaskEditor(memoryId: blk.memoryId)
                } label: {
                    Group {
                        if hVal < 35 {
                            HStack(spacing: 3) {
                                Text(String(blk.timeRangeLabel.prefix(5)))
                                    .font(.system(size: 8.5, weight: .bold, design: .monospaced))
                                    .foregroundStyle(timeFg)
                                Text("\(blk.isDone ? "✓" : "")\(blk.title)")
                                    .font(.system(size: 10, weight: .bold))
                                    .foregroundStyle(titleFg)
                                    .strikethrough(blk.isDone)
                                    .lineLimit(1)
                            }
                            .padding(.horizontal, 5)
                            .frame(width: blkW, height: hVal, alignment: .leading)
                        } else {
                            VStack(alignment: .leading, spacing: 1.5) {
                                Text(blk.timeRangeLabel)
                                    .font(.system(size: 8.5, weight: .bold, design: .monospaced))
                                    .foregroundStyle(timeFg)
                                    .lineLimit(1)
                                Text("\(blk.isDone ? "✓ " : "")\(blk.title)")
                                    .font(.system(size: 10.5, weight: .bold))
                                    .foregroundStyle(titleFg)
                                    .strikethrough(blk.isDone)
                                    .lineLimit(hVal > 52 ? 3 : 2)
                            }
                            .padding(.horizontal, 5)
                            .padding(.vertical, 3)
                            .frame(width: blkW, height: hVal, alignment: .topLeading)
                        }
                    }
                    .background(cardBg)
                    .overlay(alignment: .leading) {
                        Rectangle()
                            .fill(blk.macaron.accentColor)
                            .frame(width: 3)
                    }
                    .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
                    .opacity(blk.isDone ? 0.62 : 1.0)
                }
                .buttonStyle(.plain)
                .offset(x: offsetX, y: topY)
            }

            // Current Time Red Indicator Line
            if day.isToday, let nowMin = overview?.nowMinute {
                let nowY = layout.y(forMinute: nowMin)
                HStack(spacing: 0) {
                    Circle()
                        .fill(Color.red)
                        .frame(width: 7, height: 7)
                    Rectangle()
                        .fill(Color.red)
                        .frame(height: 1.8)
                }
                .offset(x: 0, y: max(0, nowY - 3))
                .allowsHitTesting(false)
            }
        }
        .frame(width: colWidth, height: layout.totalHeight)
        .overlay(alignment: .leading) {
            Rectangle()
                .fill(Color.primary.opacity(0.07))
                .frame(width: 0.7)
        }
    }

    // MARK: - Month 42-Cell Panorama

    @ViewBuilder
    private var monthPanoramaCard: some View {
        let days = overview?.days ?? []
        let cols = Array(repeating: GridItem(.flexible(), spacing: 6), count: 7)
        let weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

        VStack(alignment: .leading, spacing: 8) {
            LazyVGrid(columns: cols, spacing: 6) {
                ForEach(Array(weekdays.enumerated()), id: \.offset) { idx, wd in
                    Text(wd)
                        .font(.caption2.bold())
                        .foregroundStyle(idx >= 5 ? offDayRedColor : .secondary)
                        .frame(maxWidth: .infinity)
                }

                ForEach(days) { d in
                    let count = d.allDayTasks.count + d.timedBlocks.count
                    let isSel = d.date == selectedDate
                    let offDay = d.effectiveIsOffDay
                    VStack(alignment: .leading, spacing: 2.5) {
                        HStack(spacing: 3) {
                            Text("\(d.day)")
                                .font(.system(size: 13, weight: (d.isToday || offDay) ? .heavy : .semibold, design: .rounded))
                                .foregroundStyle(offDay ? offDayRedColor : (d.isToday ? store.activeTheme.primaryColor : .primary))
                            holidayMicroBadge(d.holidayStatus)
                            if d.isToday {
                                Text("今")
                                    .font(.system(size: 8, weight: .bold))
                                    .padding(.horizontal, 3.5)
                                    .padding(.vertical, 0.5)
                                    .background(store.activeTheme.primaryColor)
                                    .foregroundStyle(.white)
                                    .clipShape(RoundedRectangle(cornerRadius: 3.5, style: .continuous))
                            }
                            Spacer(minLength: 0)
                            if d.focusMinutes > 0 {
                                Text("🍅").font(.system(size: 9))
                            }
                            if d.noteCount > 0 {
                                Text("📝").font(.system(size: 9))
                            }
                            if count > 0 {
                                Text("\(count)")
                                    .font(.system(size: 9.5, weight: .bold))
                                    .foregroundStyle(store.activeTheme.primaryColor)
                            }
                        }

                        calendarSubLabelText(d, size: 9.5)

                        if let cd = d.countdowns.first {
                            Text("\(cd.icon)\(cd.title)")
                                .font(.system(size: 9, weight: .bold))
                                .foregroundStyle(store.activeTheme.primaryColor)
                                .lineLimit(1)
                        }

                        ForEach(d.timedBlocks.prefix(2)) { tb in
                            Button {
                                selectedDate = d.date
                                openTaskEditor(memoryId: tb.memoryId)
                            } label: {
                                Text("\(tb.isDone ? "✓ " : "")\(tb.title)")
                                    .font(.system(size: 9.5, weight: .medium))
                                    .strikethrough(tb.isDone)
                                    .lineLimit(1)
                                    .padding(.horizontal, 4)
                                    .padding(.vertical, 1)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .background(tb.macaron.cardBackground)
                                    .foregroundStyle(Color.black.opacity(0.82))
                                    .clipShape(RoundedRectangle(cornerRadius: 4))
                            }
                            .buttonStyle(.plain)
                        }
                        if d.timedBlocks.count < 2 {
                            ForEach(d.allDayTasks.prefix(2 - d.timedBlocks.count)) { ad in
                                Button {
                                    selectedDate = d.date
                                    openTaskEditor(memoryId: ad.memoryId)
                                } label: {
                                    Text("\(ad.isDone ? "✓ " : "")\(ad.title)")
                                        .font(.system(size: 9.5, weight: .medium))
                                        .strikethrough(ad.isDone)
                                        .lineLimit(1)
                                        .padding(.horizontal, 4)
                                        .padding(.vertical, 1)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                        .background(ad.macaron.cardBackground)
                                        .foregroundStyle(Color.black.opacity(0.82))
                                        .clipShape(RoundedRectangle(cornerRadius: 4))
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(6)
                    .frame(minHeight: 76, alignment: .topLeading)
                    .background(
                        d.isToday
                            ? store.activeTheme.primaryColor.opacity(0.09)
                            : (d.holidayStatus == "rest" ? offDayRedColor.opacity(0.045) : Color.primary.opacity(0.02))
                    )
                    .opacity(d.isCurrentMonth ? 1.0 : 0.42)
                    .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 9, style: .continuous)
                            .stroke(isSel ? store.activeTheme.primaryColor : (d.isToday ? store.activeTheme.primaryColor.opacity(0.6) : Color.primary.opacity(0.07)), lineWidth: isSel ? 2 : 1)
                    )
                    .help(d.tooltipSummary)
                    .contentShape(Rectangle())
                    .onTapGesture {
                        selectedDate = d.date
                    }
                }
            }
        }
        .padding(12)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(Color.primary.opacity(0.08), lineWidth: 1)
        )
    }

    // MARK: - Selected Day Agenda & Unscheduled Pool (Click Detail + In-Place Direct Edit)

    private struct AgendaSlotRowItem: Identifiable {
        let memoryId: String
        let title: String
        let isDone: Bool
        let isAllDay: Bool
        let startMinute: Int
        let durationMinutes: Int
        let timeLabel: String
        let macaron: MacaronTheme

        var id: String { memoryId }
    }

    private func buildSelectedDayAgendaItems() -> [AgendaSlotRowItem] {
        var result: [AgendaSlotRowItem] = []
        var seen = Set<String>()

        if let bucket = (overview?.days ?? []).first(where: { $0.date == selectedDate }) {
            for ad in bucket.allDayTasks where !seen.contains(ad.memoryId) {
                seen.insert(ad.memoryId)
                result.append(AgendaSlotRowItem(
                    memoryId: ad.memoryId,
                    title: ad.title,
                    isDone: ad.isDone,
                    isAllDay: true,
                    startMinute: 9 * 60,
                    durationMinutes: 60,
                    timeLabel: "全天",
                    macaron: ad.macaron
                ))
            }
            for tb in bucket.timedBlocks where !seen.contains(tb.memoryId) {
                seen.insert(tb.memoryId)
                result.append(AgendaSlotRowItem(
                    memoryId: tb.memoryId,
                    title: tb.title,
                    isDone: tb.isDone,
                    isAllDay: false,
                    startMinute: tb.startMinute,
                    durationMinutes: tb.durationMinutes,
                    timeLabel: tb.timeRangeLabel,
                    macaron: tb.macaron
                ))
            }
        }

        for t in (store.matrix?.sortedTasks ?? []) where !seen.contains(t.memoryId) {
            let startYmd = t.payload.startAt.map { String($0.prefix(10)) } ?? ""
            let dueYmd = t.payload.dueAt.map { String($0.prefix(10)) } ?? ""
            guard startYmd == selectedDate || dueYmd == selectedDate else { continue }
            seen.insert(t.memoryId)
            let allDay = t.payload.isAllDay ?? (t.payload.startAt == nil && (t.payload.dueAt?.count ?? 0) <= 10)
            let dur = t.payload.durationMinutes ?? t.payload.focusMinutes ?? 60
            let rawIso = t.payload.startAt ?? t.payload.dueAt ?? ""
            let hhmm = rawIso.count >= 16 ? String(rawIso.dropFirst(11).prefix(5)) : "09:00"
            let parts = hhmm.split(separator: ":")
            let sMin = (Int(parts.first ?? "9") ?? 9) * 60 + (Int(parts.dropFirst().first ?? "0") ?? 0)
            result.append(AgendaSlotRowItem(
                memoryId: t.memoryId,
                title: t.title,
                isDone: t.payload.isDone,
                isAllDay: allDay,
                startMinute: sMin,
                durationMinutes: dur,
                timeLabel: allDay ? "全天" : hhmm,
                macaron: t.payload.themeKind
            ))
        }
        return result
    }

    private var selectedDayAndUnscheduledSection: some View {
        let dayItems = buildSelectedDayAgendaItems()
        let unscheduled = overview?.unscheduledTasks ?? []
        let selectedBucket = (overview?.days ?? []).first(where: { $0.date == selectedDate })

        return VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    Text("📅 \(selectedDate) 排期详情 (\(dayItems.count) 项)")
                        .font(.subheadline.bold())
                    if let sb = selectedBucket {
                        if let ls = sb.lunarShort, !ls.isEmpty {
                            Text("农历\(ls)")
                                .font(.caption2.bold())
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(Color.primary.opacity(0.07))
                                .foregroundStyle(.secondary)
                                .clipShape(Capsule())
                        }
                        if let st = sb.solarTerm, !st.isEmpty {
                            Text("🌱 \(st)")
                                .font(.caption2.bold())
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(solarTermTealColor.opacity(0.14))
                                .foregroundStyle(solarTermTealColor)
                                .clipShape(Capsule())
                        }
                        if let fests = sb.festivals, let firstFest = fests.first, !firstFest.isEmpty {
                            Text("🎊 \(firstFest)")
                                .font(.caption2.bold())
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(offDayRedColor.opacity(0.14))
                                .foregroundStyle(offDayRedColor)
                                .clipShape(Capsule())
                        }
                        holidayMicroBadge(sb.holidayStatus)
                    }
                    Spacer()
                    Button("+ 添加时段") {
                        slotTarget = CalendarSlotTarget(date: selectedDate, hour: 10, isAllDay: false)
                    }
                    .font(.caption.bold())
                    .buttonStyle(.bordered)
                }

                if dayItems.isEmpty {
                    Text("当日暂无排期事项，轻点上方任意小时格即可一键排期。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 8)
                } else {
                    ForEach(dayItems) { item in
                        if inlineTaskId == item.memoryId {
                            calendarInlineEditorCard(memoryId: item.memoryId, isCurrentlyScheduled: true)
                        } else {
                            calendarAgendaRow(item)
                        }
                    }
                }
            }
            .padding(12)
            .background(.background)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))

            if !unscheduled.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("📥 待排期任务池 (\(unscheduled.count) 项 · 支持点击详情与原地排期修改)")
                        .font(.subheadline.bold())

                    ForEach(unscheduled.prefix(12)) { u in
                        if inlineTaskId == u.memoryId {
                            calendarInlineEditorCard(memoryId: u.memoryId, isCurrentlyScheduled: false)
                        } else {
                            unscheduledTaskRow(u)
                        }
                    }
                }
                .padding(12)
                .background(.background)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
        }
    }

    private func calendarAgendaRow(_ item: AgendaSlotRowItem) -> some View {
        HStack(spacing: 10) {
            Button {
                openTaskEditor(memoryId: item.memoryId)
            } label: {
                HStack(spacing: 10) {
                    RoundedRectangle(cornerRadius: 3)
                        .fill(item.macaron.accentColor)
                        .frame(width: 4, height: 34)

                    VStack(alignment: .leading, spacing: 2) {
                        Text("\(item.isDone ? "✓ " : "")\(item.title)")
                            .font(.subheadline.weight(.semibold))
                            .strikethrough(item.isDone)
                            .foregroundStyle(item.isDone ? .secondary : .primary)
                            .lineLimit(1)
                        Text("⏰ \(item.timeLabel)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 4)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if item.isDone {
                Button("↩️ 恢复") {
                    Task {
                        await store.reopenTaskById(item.memoryId)
                        await reloadCalendar()
                    }
                }
                .font(.caption.bold())
                .buttonStyle(.bordered)
                .tint(.green)
                .controlSize(.small)
            }

            Button("🔍 详情") {
                openTaskEditor(memoryId: item.memoryId)
            }
            .font(.caption)
            .buttonStyle(.bordered)
            .controlSize(.small)

            Button("✏️ 原地改") {
                startInlineTaskEdit(
                    memoryId: item.memoryId,
                    title: item.title,
                    ymd: selectedDate,
                    isAllDay: item.isAllDay,
                    startMinute: item.startMinute,
                    durationMinutes: item.durationMinutes,
                    macaron: item.macaron
                )
            }
            .font(.caption.bold())
            .buttonStyle(.borderedProminent)
            .tint(store.activeTheme.primaryColor)
            .controlSize(.small)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(item.macaron.cardBackground.opacity(store.activeTheme.isDark ? 0.16 : 0.35))
        .clipShape(RoundedRectangle(cornerRadius: 11, style: .continuous))
    }

    private func unscheduledTaskRow(_ u: CalendarUnscheduledTask) -> some View {
        HStack(spacing: 8) {
            Button {
                openTaskEditor(memoryId: u.memoryId)
            } label: {
                HStack(spacing: 8) {
                    Circle()
                        .fill(u.macaron.accentColor)
                        .frame(width: 8, height: 8)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(u.title)
                            .font(.subheadline.weight(.medium))
                            .foregroundStyle(.primary)
                            .lineLimit(1)
                        Text("\(u.quadrantLabel) · 预计 \(u.durationMinutes)m")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 4)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            Button("✏️ 原地改") {
                startInlineTaskEdit(
                    memoryId: u.memoryId,
                    title: u.title,
                    ymd: selectedDate,
                    isAllDay: false,
                    startMinute: 10 * 60,
                    durationMinutes: u.durationMinutes,
                    macaron: u.macaron
                )
            }
            .font(.caption)
            .buttonStyle(.bordered)
            .controlSize(.small)

            Button("排入选中日") {
                Task {
                    try? await store.client.scheduleCalendarSlot(body: [
                        "memory_id": u.memoryId,
                        "date": selectedDate,
                        "time": "10:00",
                        "is_all_day": false,
                        "duration_minutes": u.durationMinutes
                    ])
                    await store.refresh()
                    await reloadCalendar()
                }
            }
            .font(.caption.bold())
            .buttonStyle(.borderedProminent)
            .tint(store.activeTheme.primaryColor)
            .controlSize(.small)
        }
        .padding(.vertical, 5)
    }

    private func calendarInlineEditorCard(memoryId: String, isCurrentlyScheduled: Bool) -> some View {
        let presets = [30, 45, 60, 90, 120]
        return VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("✏️ 日历原地修改")
                    .font(.caption2.bold())
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(store.activeTheme.primaryColor)
                    .foregroundStyle(.white)
                    .clipShape(Capsule())
                Spacer()
                if isCurrentlyScheduled {
                    Button("📥 移回待排期池") {
                        unscheduleInlineTask(memoryId: memoryId)
                    }
                    .font(.caption2)
                    .buttonStyle(.bordered)
                    .controlSize(.mini)
                }
            }

            HStack(spacing: 6) {
                TextField("事项标题...", text: $inlineTitle)
                    .textFieldStyle(.roundedBorder)
                    .font(.subheadline.bold())
                VoiceMicButton(text: $inlineTitle, compact: true)
            }

            HStack(spacing: 12) {
                DatePicker("日期", selection: $inlineDate, displayedComponents: [.date])
                    .labelsHidden()
                Toggle("全天", isOn: $inlineIsAllDay)
                    .toggleStyle(.switch)
                    .controlSize(.small)
                    .font(.caption)
                if !inlineIsAllDay {
                    Stepper(
                        String(format: "%02d:%02d", inlineHour, inlineMinute),
                        value: $inlineHour,
                        in: 0...23
                    )
                    .font(.caption.monospacedDigit().bold())
                }
                Spacer(minLength: 0)
            }

            if !inlineIsAllDay {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        Text("时长：")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        ForEach(presets, id: \.self) { mins in
                            Button("\(mins)m") {
                                inlineDuration = mins
                            }
                            .font(.caption2.bold())
                            .padding(.horizontal, 9)
                            .padding(.vertical, 4)
                            .background(inlineDuration == mins ? store.activeTheme.primaryColor : Color.primary.opacity(0.06))
                            .foregroundStyle(inlineDuration == mins ? Color.white : .primary)
                            .clipShape(Capsule())
                            .buttonStyle(.plain)
                        }
                    }
                }
            }

            HStack(spacing: 8) {
                ForEach(MacaronTheme.allCases) { theme in
                    Button {
                        inlineMacaron = theme
                    } label: {
                        Circle()
                            .fill(theme.accentColor)
                            .frame(width: 18, height: 18)
                            .overlay(
                                Circle()
                                    .stroke(Color.primary, lineWidth: inlineMacaron == theme ? 2 : 0)
                            )
                    }
                    .buttonStyle(.plain)
                }
                Spacer()
                Button("取消") {
                    inlineTaskId = ""
                }
                .font(.caption)
                .buttonStyle(.bordered)

                Button(isSavingInline ? "保存中..." : "💾 保存") {
                    saveInlineCalendarTask(memoryId: memoryId)
                }
                .font(.caption.bold())
                .buttonStyle(.borderedProminent)
                .tint(store.activeTheme.primaryColor)
                .disabled(isSavingInline || inlineTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(12)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(store.activeTheme.primaryColor, lineWidth: 2)
        )
    }

    private func startInlineTaskEdit(
        memoryId: String,
        title: String,
        ymd: String,
        isAllDay: Bool,
        startMinute: Int,
        durationMinutes: Int,
        macaron: MacaronTheme
    ) {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "Asia/Shanghai") ?? .current
        inlineTaskId = memoryId
        inlineTitle = title
        inlineDate = f.date(from: ymd) ?? Date()
        inlineIsAllDay = isAllDay
        inlineHour = max(0, min(23, startMinute / 60))
        inlineMinute = max(0, min(59, startMinute % 60))
        inlineDuration = max(15, durationMinutes)
        inlineMacaron = macaron
    }

    private func saveInlineCalendarTask(memoryId: String) {
        let cleanTitle = inlineTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanTitle.isEmpty else { return }
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "Asia/Shanghai") ?? .current
        let dateStr = f.string(from: inlineDate)

        isSavingInline = true
        dismissAppKeyboard()
        Task {
            defer { isSavingInline = false }
            try? await store.client.scheduleCalendarSlot(body: [
                "memory_id": memoryId,
                "title": cleanTitle,
                "date": dateStr,
                "time": String(format: "%02d:%02d", inlineHour, inlineMinute),
                "is_all_day": inlineIsAllDay,
                "duration_minutes": inlineDuration,
                "color_theme": inlineMacaron.rawValue
            ])
            selectedDate = dateStr
            inlineTaskId = ""
            store.showBanner("✅ 日历事项已原地保存")
            await store.refresh()
            await reloadCalendar()
        }
    }

    private func unscheduleInlineTask(memoryId: String) {
        isSavingInline = true
        Task {
            defer { isSavingInline = false }
            try? await store.client.scheduleCalendarSlot(body: [
                "memory_id": memoryId,
                "unschedule": true
            ])
            inlineTaskId = ""
            store.showBanner("📥 已移回待排期任务池")
            await store.refresh()
            await reloadCalendar()
        }
    }

    // MARK: - Helpers

    private func shiftPeriod(_ delta: Int) {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "Asia/Shanghai") ?? .current
        let cur = f.date(from: selectedDate) ?? Date()
        let cal = Calendar(identifier: .gregorian)
        let nextDate: Date
        if mode == .week {
            nextDate = cal.date(byAdding: .day, value: delta * 7, to: cur) ?? cur
        } else {
            nextDate = cal.date(byAdding: .month, value: delta, to: cur) ?? cur
        }
        selectedDate = f.string(from: nextDate)
        Task { await reloadCalendar() }
    }

    private func openTaskEditor(memoryId: String) {
        if let found = (store.matrix?.sortedTasks ?? []).first(where: { $0.memoryId == memoryId }) {
            editingTask = found
            return
        }
        Task {
            if let fullMatrix = try? await store.client.fetchMatrix(sortBy: store.sortBy, includeDone: true),
               let found = fullMatrix.sortedTasks.first(where: { $0.memoryId == memoryId }) {
                editingTask = found
            }
        }
    }

    private func reloadCalendar() async {
        isLoading = true
        defer { isLoading = false }
        if let res = try? await store.client.fetchCalendarOverview(
            view: mode.rawValue,
            anchorDate: selectedDate,
            includeDone: true
        ) {
            overview = res
        }
    }
}

// MARK: - Slot Quick-Schedule Sheet

struct SlotQuickScheduleSheet: View {
    @EnvironmentObject var store: TodoStore
    @Environment(\.dismiss) private var dismiss

    let initialDate: String
    let initialHour: Int
    let initialAllDay: Bool
    let unscheduledTasks: [CalendarUnscheduledTask]
    let onScheduled: (String) -> Void

    @State private var title: String = ""
    @State private var hour: Int = 9
    @State private var minute: Int = 0
    @State private var isAllDay: Bool = false
    @State private var durationMinutes: Int = 60
    @State private var macaron: MacaronTheme = .sky
    @State private var isSubmitting: Bool = false

    private let durationPresets = [30, 45, 60, 90, 120]

    var body: some View {
        NavigationStack {
            Form {
                Section("目标时间段（\(initialDate)）") {
                    Toggle("全天事项", isOn: $isAllDay)
                    if !isAllDay {
                        Stepper("开始时刻：\(String(format: "%02d:%02d", hour, minute))", value: $hour, in: 0...23)
                        Picker("预计时长", selection: $durationMinutes) {
                            ForEach(durationPresets, id: \.self) { d in
                                Text("\(d) 分钟").tag(d)
                            }
                        }
                        .pickerStyle(.segmented)
                    }
                }

                Section("✨ 新建事项并排入此时段") {
                    HStack {
                        TextField("输入事项标题...", text: $title)
                        VoiceMicButton(text: $title, compact: true)
                    }

                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 10) {
                            ForEach(MacaronTheme.allCases) { theme in
                                Button {
                                    macaron = theme
                                } label: {
                                    HStack(spacing: 4) {
                                        Circle()
                                            .fill(theme.accentColor)
                                            .frame(width: 12, height: 12)
                                        Text(theme.displayName)
                                            .font(.caption)
                                    }
                                    .padding(.horizontal, 9)
                                    .padding(.vertical, 5)
                                    .background(macaron == theme ? theme.cardBackground : Color.primary.opacity(0.05))
                                    .clipShape(Capsule())
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }

                    Button {
                        submitNew()
                    } label: {
                        Text(isSubmitting ? "排期中..." : "创建并排入此时段")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(store.activeTheme.primaryColor)
                    .disabled(isSubmitting || title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }

                if !unscheduledTasks.isEmpty {
                    Section("📥 或从待排期任务池一键挂载") {
                        ForEach(unscheduledTasks) { u in
                            HStack {
                                Circle()
                                    .fill(u.macaron.accentColor)
                                    .frame(width: 9, height: 9)
                                Text(u.title)
                                    .font(.subheadline)
                                    .lineLimit(1)
                                Spacer()
                                Button("挂载") {
                                    assignExisting(u.memoryId)
                                }
                                .buttonStyle(.bordered)
                                .disabled(isSubmitting)
                            }
                        }
                    }
                }
            }
            .navigationTitle("时段快速排期")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
            }
            .onAppear {
                hour = max(0, min(23, initialHour))
                isAllDay = initialAllDay
            }
        }
        #if os(macOS)
        .frame(minWidth: 460, minHeight: 440)
        #endif
    }

    private func submitNew() {
        let clean = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        isSubmitting = true
        Task {
            defer { isSubmitting = false }
            try? await store.client.scheduleCalendarSlot(body: [
                "title": clean,
                "date": initialDate,
                "time": String(format: "%02d:%02d", hour, minute),
                "is_all_day": isAllDay,
                "duration_minutes": durationMinutes,
                "color_theme": macaron.rawValue
            ])
            onScheduled(initialDate)
            dismiss()
        }
    }

    private func assignExisting(_ memoryId: String) {
        isSubmitting = true
        Task {
            defer { isSubmitting = false }
            try? await store.client.scheduleCalendarSlot(body: [
                "memory_id": memoryId,
                "date": initialDate,
                "time": String(format: "%02d:%02d", hour, minute),
                "is_all_day": isAllDay,
                "duration_minutes": durationMinutes
            ])
            onScheduled(initialDate)
            dismiss()
        }
    }
}
