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

    var id: String { date }

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

struct CalendarTimelineView: View {
    @EnvironmentObject var store: TodoStore
    @State private var mode: CalendarCanvasMode = .week
    @State private var selectedDate: String = Self.todayYMD()
    @State private var overview: CalendarOverviewResponse?
    @State private var isLoading: Bool = false
    @State private var slotTarget: CalendarSlotTarget?
    @State private var editingTask: TaskItemModel?

    private let hourHeight: CGFloat = 46

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

                    Spacer()

                    Button {
                        slotTarget = CalendarSlotTarget(date: selectedDate, hour: 9, isAllDay: false)
                    } label: {
                        Label("时段排期", systemImage: "plus")
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(store.activeTheme.primaryColor)
                }
            }
        }
    }

    @ViewBuilder
    private var statsStrip: some View {
        if let s = overview?.summary {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
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
            ScrollView(.horizontal, showsIndicators: true) {
                VStack(spacing: 0) {
                    // 1. Sticky Day Headers
                    HStack(spacing: 0) {
                        Text("UTC+8")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundStyle(.secondary)
                            .frame(width: 48)

                        ForEach(days) { d in
                            let busyH = Double(d.busyMinutes) / 60.0
                            let freeH = Double(d.awakeFreeMinutes) / 60.0
                            VStack(spacing: 2) {
                                Text("\(d.weekdayLabel)\(d.isToday ? "·今" : "")")
                                    .font(.system(size: 11, weight: .bold))
                                    .foregroundStyle(d.isToday ? store.activeTheme.primaryColor : .secondary)
                                Text("\(d.month)/\(d.day)")
                                    .font(.system(size: 14, weight: .heavy, design: .rounded))
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
                            .background(d.isToday ? store.activeTheme.primaryColor.opacity(0.10) : Color.clear)
                            .overlay(alignment: .bottom) {
                                if d.date == selectedDate {
                                    Rectangle()
                                        .fill(store.activeTheme.primaryColor)
                                        .frame(height: 2.5)
                                }
                            }
                            .contentShape(Rectangle())
                            .onTapGesture {
                                selectedDate = d.date
                            }
                        }
                    }
                    .background(Color.primary.opacity(0.03))

                    Divider()

                    // 2. All-day row
                    HStack(alignment: .top, spacing: 0) {
                        Text("全天")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundStyle(.secondary)
                            .frame(width: 48, height: 32)

                        ForEach(days) { d in
                            VStack(alignment: .leading, spacing: 3) {
                                if d.allDayTasks.isEmpty {
                                    Text("+ 全天")
                                        .font(.system(size: 9.5))
                                        .foregroundStyle(.tertiary)
                                } else {
                                    ForEach(d.allDayTasks) { item in
                                        Text("\(item.isDone ? "✓ " : "")\(item.title)")
                                            .font(.system(size: 10, weight: .semibold))
                                            .lineLimit(1)
                                            .padding(.horizontal, 5)
                                            .padding(.vertical, 2)
                                            .frame(maxWidth: .infinity, alignment: .leading)
                                            .background(item.macaron.cardBackground)
                                            .foregroundStyle(Color.black.opacity(0.82))
                                            .clipShape(RoundedRectangle(cornerRadius: 5))
                                            .onTapGesture {
                                                openTaskEditor(memoryId: item.memoryId)
                                            }
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

                    Divider()

                    // 3. 00:00 - 24:00 Scrollable Time Grid
                    ScrollViewReader { proxy in
                        ScrollView(.vertical, showsIndicators: true) {
                            HStack(alignment: .top, spacing: 0) {
                                // Hour Labels Gutter
                                VStack(spacing: 0) {
                                    ForEach(0..<24, id: \.self) { h in
                                        Text(String(format: "%02d:00", h))
                                            .font(.system(size: 10, weight: .semibold, design: .monospaced))
                                            .foregroundStyle(.secondary)
                                            .frame(width: 48, height: hourHeight, alignment: .topTrailing)
                                            .padding(.trailing, 4)
                                            .id("cal_hour_\(h)")
                                    }
                                }

                                // 7 Day Columns
                                ForEach(days) { d in
                                    dayTimelineColumn(d)
                                }
                            }
                        }
                        .frame(height: 460)
                        .onAppear {
                            proxy.scrollTo("cal_hour_8", anchor: .top)
                        }
                    }
                }
                .frame(minWidth: 48 + CGFloat(max(7, days.count)) * 106)
            }
            .background(.background)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(Color.primary.opacity(0.08), lineWidth: 1)
            )
        }
    }

    private func dayTimelineColumn(_ day: CalendarDayBucket) -> some View {
        let colWidth: CGFloat = 106
        return ZStack(alignment: .topLeading) {
            // 24 Clickable Hour Slots
            VStack(spacing: 0) {
                ForEach(0..<24, id: \.self) { h in
                    let isRest = h < 8 || h >= 22
                    Rectangle()
                        .fill(isRest ? Color.primary.opacity(0.025) : Color.clear)
                        .frame(width: colWidth, height: hourHeight)
                        .overlay(alignment: .bottom) {
                            Rectangle()
                                .fill(Color.primary.opacity(0.06))
                                .frame(height: 0.6)
                        }
                        .contentShape(Rectangle())
                        .onTapGesture {
                            selectedDate = day.date
                            slotTarget = CalendarSlotTarget(date: day.date, hour: h, isAllDay: false)
                        }
                }
            }

            // Completed Focus Track Pills
            ForEach(day.focusBlocks) { fb in
                let topY = (CGFloat(fb.startMinute) / 60.0) * hourHeight
                let hVal = max(18, (CGFloat(fb.durationMinutes) / 60.0) * hourHeight)
                Text("🍅\(fb.durationMinutes)m")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(.green)
                    .padding(.horizontal, 4)
                    .frame(height: hVal, alignment: .topLeading)
                    .background(Color.green.opacity(0.14))
                    .clipShape(RoundedRectangle(cornerRadius: 5))
                    .offset(x: colWidth - 46, y: topY)
                    .allowsHitTesting(false)
            }

            // Scheduled Macaron Task Blocks
            ForEach(day.timedBlocks) { blk in
                let topY = (CGFloat(blk.startMinute) / 60.0) * hourHeight
                let hVal = max(22, (CGFloat(blk.durationMinutes) / 60.0) * hourHeight - 2)
                let totalCols = CGFloat(max(1, blk.colTotal))
                let idx = CGFloat(min(max(0, blk.colIdx), max(0, blk.colTotal - 1)))
                let blkW = max(36, (colWidth - 4) / totalCols - 2)
                let offsetX = 2 + idx * ((colWidth - 4) / totalCols)

                VStack(alignment: .leading, spacing: 1) {
                    Text(String(blk.timeRangeLabel.prefix(5)))
                        .font(.system(size: 8.5, weight: .bold))
                        .foregroundStyle(blk.macaron.accentColor)
                    Text("\(blk.isDone ? "✓ " : "")\(blk.title)")
                        .font(.system(size: 10.5, weight: .bold))
                        .foregroundStyle(Color.black.opacity(0.84))
                        .strikethrough(blk.isDone)
                        .lineLimit(hVal > 40 ? 2 : 1)
                }
                .padding(.horizontal, 5)
                .padding(.vertical, 3)
                .frame(width: blkW, height: hVal, alignment: .topLeading)
                .background(blk.macaron.cardBackground)
                .overlay(alignment: .leading) {
                    Rectangle()
                        .fill(blk.macaron.accentColor)
                        .frame(width: 3)
                }
                .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
                .opacity(blk.isDone ? 0.6 : 1.0)
                .offset(x: offsetX, y: topY)
                .onTapGesture {
                    openTaskEditor(memoryId: blk.memoryId)
                }
            }

            // Current Time Red Indicator Line
            if day.isToday, let nowMin = overview?.nowMinute {
                let nowY = (CGFloat(nowMin) / 60.0) * hourHeight
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
        .frame(width: colWidth, height: 24 * hourHeight)
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
                ForEach(weekdays, id: \.self) { wd in
                    Text(wd)
                        .font(.caption2.bold())
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity)
                }

                ForEach(days) { d in
                    let count = d.allDayTasks.count + d.timedBlocks.count
                    let isSel = d.date == selectedDate
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 3) {
                            Text("\(d.day)")
                                .font(.system(size: 12, weight: d.isToday ? .heavy : .semibold))
                                .foregroundStyle(d.isToday ? store.activeTheme.primaryColor : .primary)
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

                        if let cd = d.countdowns.first {
                            Text("\(cd.icon)\(cd.title)")
                                .font(.system(size: 9, weight: .bold))
                                .foregroundStyle(store.activeTheme.primaryColor)
                                .lineLimit(1)
                        }

                        ForEach(d.timedBlocks.prefix(2)) { tb in
                            Text(tb.title)
                                .font(.system(size: 9.5, weight: .medium))
                                .lineLimit(1)
                                .padding(.horizontal, 4)
                                .padding(.vertical, 1)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(tb.macaron.cardBackground)
                                .foregroundStyle(Color.black.opacity(0.82))
                                .clipShape(RoundedRectangle(cornerRadius: 4))
                        }
                        if d.timedBlocks.count < 2 {
                            ForEach(d.allDayTasks.prefix(2 - d.timedBlocks.count)) { ad in
                                Text(ad.title)
                                    .font(.system(size: 9.5, weight: .medium))
                                    .lineLimit(1)
                                    .padding(.horizontal, 4)
                                    .padding(.vertical, 1)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .background(ad.macaron.cardBackground)
                                    .foregroundStyle(Color.black.opacity(0.82))
                                    .clipShape(RoundedRectangle(cornerRadius: 4))
                            }
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(6)
                    .frame(minHeight: 68, alignment: .topLeading)
                    .background(d.isToday ? store.activeTheme.primaryColor.opacity(0.08) : Color.primary.opacity(0.02))
                    .opacity(d.isCurrentMonth ? 1.0 : 0.42)
                    .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 9, style: .continuous)
                            .stroke(isSel ? store.activeTheme.primaryColor : Color.primary.opacity(0.07), lineWidth: isSel ? 2 : 1)
                    )
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

    // MARK: - Selected Day Agenda & Unscheduled Pool

    private var selectedDayAndUnscheduledSection: some View {
        let dayTasks = (store.matrix?.sortedTasks ?? []).filter { t in
            guard let due = t.payload.dueAt, due.count >= 10 else { return false }
            return String(due.prefix(10)) == selectedDate
        }
        let unscheduled = overview?.unscheduledTasks ?? []

        return VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("📅 \(selectedDate) 排期详情 (\(dayTasks.count) 项)")
                        .font(.subheadline.bold())
                    Spacer()
                    Button("+ 添加时段") {
                        slotTarget = CalendarSlotTarget(date: selectedDate, hour: 10, isAllDay: false)
                    }
                    .font(.caption.bold())
                    .buttonStyle(.bordered)
                }

                if dayTasks.isEmpty {
                    Text("当日暂无排期事项，轻点上方任意小时格即可一键排期。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 8)
                } else {
                    ForEach(dayTasks) { task in
                        TaskCardView(task: task)
                    }
                }
            }
            .padding(12)
            .background(.background)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))

            if !unscheduled.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("📥 待排期任务池 (\(unscheduled.count) 项)")
                        .font(.subheadline.bold())

                    ForEach(unscheduled.prefix(8)) { u in
                        HStack {
                            Circle()
                                .fill(u.macaron.accentColor)
                                .frame(width: 8, height: 8)
                            Text(u.title)
                                .font(.subheadline.weight(.medium))
                                .lineLimit(1)
                            Spacer()
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
                            .buttonStyle(.bordered)
                        }
                        .padding(.vertical, 4)
                    }
                }
                .padding(12)
                .background(.background)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
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
