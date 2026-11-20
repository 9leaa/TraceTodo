import AppIntents
import SwiftUI
import WidgetKit

// MARK: - Widget Configuration & Interactive Intents

enum WidgetQuadrantFilter: String, AppEnum {
    case all = "all"
    case q1 = "Q1_do_first"
    case q2 = "Q2_schedule"

    static var typeDisplayRepresentation: TypeDisplayRepresentation = TypeDisplayRepresentation(name: "展示范围")
    static var caseDisplayRepresentations: [WidgetQuadrantFilter: DisplayRepresentation] = [
        .all: "全部优先级待办",
        .q1: "仅 Q1 重要且紧急",
        .q2: "仅 Q2 重要不紧急"
    ]
}

struct TraceTodoWidgetConfigIntent: WidgetConfigurationIntent {
    static var title: LocalizedStringResource = "TraceTodo 桌面待办"
    static var description = IntentDescription("实时展示四象限待办，自动读取主 App 配置（默认线上环境），支持桌面打卡完成与锁屏展示。")

    @Parameter(title: "服务端地址（留空自动跟随主 App）", default: "")
    var serverURL: String

    @Parameter(title: "记忆空间 Space ID（留空自动跟随主 App）", default: "")
    var spaceId: String

    @Parameter(title: "访问令牌 Token（留空自动跟随主 App）", default: "")
    var authToken: String

    @Parameter(title: "显示象限", default: .all)
    var filter: WidgetQuadrantFilter
}

struct RefreshTodoWidgetIntent: AppIntent {
    static var title: LocalizedStringResource = "刷新桌面待办"

    func perform() async throws -> some IntentResult {
        WidgetCenter.shared.reloadAllTimelines()
        return .result()
    }
}

struct CompleteWidgetTaskIntent: AppIntent {
    static var title: LocalizedStringResource = "在桌面打卡完成任务"

    @Parameter(title: "任务 ID")
    var memoryId: String

    @Parameter(title: "版本号")
    var version: Int

    @Parameter(title: "服务端地址")
    var serverURL: String

    @Parameter(title: "Space ID")
    var spaceId: String

    @Parameter(title: "访问令牌 Token")
    var authToken: String

    init() {}

    init(memoryId: String, version: Int, serverURL: String, spaceId: String, authToken: String) {
        self.memoryId = memoryId
        self.version = version
        self.serverURL = serverURL
        self.spaceId = spaceId
        self.authToken = authToken
    }

    func perform() async throws -> some IntentResult {
        await WidgetAPI.completeTask(
            serverURL: serverURL,
            spaceId: spaceId,
            authToken: authToken,
            memoryId: memoryId,
            expectedVersion: version
        )
        WidgetCenter.shared.reloadAllTimelines()
        return .result()
    }
}

// MARK: - Lightweight Codable Models & Networking

struct WidgetTaskRow: Identifiable, Hashable {
    let id: String
    let title: String
    let quadrant: String
    let priorityScore: Double
    let dueLabel: String
    let version: Int
    let isOverdue: Bool
    let subtaskProgress: String

    var shortCode: String {
        String(quadrant.prefix(2))
    }

    var tintColor: Color {
        switch quadrant {
        case "Q1_do_first": return Color(red: 0.90, green: 0.22, blue: 0.27)
        case "Q2_schedule": return Color(red: 0.20, green: 0.48, blue: 0.96)
        case "Q3_delegate": return Color(red: 0.92, green: 0.56, blue: 0.08)
        default: return Color(red: 0.42, green: 0.48, blue: 0.58)
        }
    }
}

private struct RawMatrixDTO: Decodable {
    let summaryCounts: RawSummaryDTO
    let sortedTasks: [RawTaskDTO]

    enum CodingKeys: String, CodingKey {
        case summaryCounts = "summary_counts"
        case sortedTasks = "sorted_tasks"
    }
}

private struct RawSummaryDTO: Decodable {
    let totalTodo: Int
    let totalDone: Int
    let overdue: Int
    let q1: Int
    let q2: Int
    let q3: Int
    let q4: Int

    enum CodingKeys: String, CodingKey {
        case totalTodo = "total_todo"
        case totalDone = "total_done"
        case overdue
        case q1 = "Q1_do_first"
        case q2 = "Q2_schedule"
        case q3 = "Q3_delegate"
        case q4 = "Q4_eliminate"
    }
}

private struct RawTaskDTO: Decodable {
    let memoryId: String
    let title: String
    let version: Int
    let payload: RawPayloadDTO

    enum CodingKeys: String, CodingKey {
        case memoryId = "memory_id"
        case title
        case version
        case payload
    }
}

private struct RawSubDTO: Decodable {
    let done: Bool
}

private struct RawPayloadDTO: Decodable {
    let taskStatus: String?
    let quadrant: String?
    let priorityScore: Double?
    let dueAt: String?
    let isOverdue: Bool?
    let subtasks: [RawSubDTO]?

    enum CodingKeys: String, CodingKey {
        case taskStatus = "task_status"
        case quadrant
        case priorityScore = "priority_score"
        case dueAt = "due_at"
        case isOverdue = "is_overdue"
        case subtasks
    }
}

enum WidgetAPI {
    static func normalizedBase(_ raw: String) -> String {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if !trimmed.isEmpty {
            return trimmed
        }
        let shared = SharedWidgetBridge.loadConfig().serverURL
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        return shared.isEmpty ? SharedWidgetBridge.cloudServerURL : shared
    }

    static func resolveConfig(_ config: TraceTodoWidgetConfigIntent) -> (base: String, space: String, token: String) {
        let shared = SharedWidgetBridge.loadConfig()
        let base = normalizedBase(config.serverURL)
        let rawSpace = config.spaceId.trimmingCharacters(in: .whitespacesAndNewlines)
        let space = rawSpace.isEmpty ? shared.spaceId : rawSpace
        let rawTok = config.authToken.trimmingCharacters(in: .whitespacesAndNewlines)
        let token = rawTok.isEmpty ? shared.authToken : rawTok
        return (base, space.isEmpty ? "default" : space, token)
    }

    private static func decodeEntry(
        from data: Data,
        base: String,
        space: String,
        token: String,
        filter: WidgetQuadrantFilter
    ) throws -> TraceTodoWidgetEntry {
        let dto = try JSONDecoder().decode(RawMatrixDTO.self, from: data)
        let filtered = dto.sortedTasks.filter { t in
            guard t.payload.taskStatus != "done" else { return false }
            if filter == .q1 { return t.payload.quadrant == "Q1_do_first" }
            if filter == .q2 { return t.payload.quadrant == "Q2_schedule" }
            return true
        }.map { t -> WidgetTaskRow in
            let subs = t.payload.subtasks ?? []
            let prog = subs.isEmpty ? "" : "\(subs.filter(\.done).count)/\(subs.count)"
            let due = t.payload.dueAt.map { String($0.prefix(10)) } ?? ""
            return WidgetTaskRow(
                id: t.memoryId,
                title: t.title,
                quadrant: t.payload.quadrant ?? "Q4_eliminate",
                priorityScore: t.payload.priorityScore ?? 0,
                dueLabel: due,
                version: t.version,
                isOverdue: t.payload.isOverdue ?? false,
                subtaskProgress: prog
            )
        }
        return TraceTodoWidgetEntry(
            date: Date(),
            serverURL: base,
            spaceId: space,
            authToken: token,
            filter: filter,
            q1Count: dto.summaryCounts.q1,
            q2Count: dto.summaryCounts.q2,
            q34Count: dto.summaryCounts.q3 + dto.summaryCounts.q4,
            totalTodo: dto.summaryCounts.totalTodo,
            totalDone: dto.summaryCounts.totalDone,
            overdueCount: dto.summaryCounts.overdue,
            tasks: filtered,
            isOffline: false
        )
    }

    private static func cachedFallbackEntry(
        config: TraceTodoWidgetConfigIntent,
        base: String,
        space: String,
        token: String
    ) -> TraceTodoWidgetEntry {
        if let cachedData = SharedWidgetBridge.loadMatrixSnapshotData(),
           let entry = try? decodeEntry(from: cachedData, base: base, space: space, token: token, filter: config.filter) {
            return entry
        }
        return .placeholder(config: config, isOffline: true)
    }

    static func fetchEntry(config: TraceTodoWidgetConfigIntent) async -> TraceTodoWidgetEntry {
        let resolved = resolveConfig(config)
        let base = resolved.base
        let space = resolved.space
        let cleanTok = resolved.token
        guard var comps = URLComponents(string: "\(base)/api/tasks/matrix") else {
            return cachedFallbackEntry(config: config, base: base, space: space, token: cleanTok)
        }
        comps.queryItems = [
            URLQueryItem(name: "space_id", value: space),
            URLQueryItem(name: "sort_by", value: "priority"),
            URLQueryItem(name: "include_done", value: "false")
        ]
        guard let url = comps.url else {
            return cachedFallbackEntry(config: config, base: base, space: space, token: cleanTok)
        }
        var req = URLRequest(url: url)
        req.timeoutInterval = 8
        if !cleanTok.isEmpty {
            req.setValue("Bearer \(cleanTok)", forHTTPHeaderField: "Authorization")
        }

        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
                return cachedFallbackEntry(config: config, base: base, space: space, token: cleanTok)
            }
            SharedWidgetBridge.saveMatrixSnapshotData(data)
            return try decodeEntry(from: data, base: base, space: space, token: cleanTok, filter: config.filter)
        } catch {
            return cachedFallbackEntry(config: config, base: base, space: space, token: cleanTok)
        }
    }

    static func completeTask(serverURL: String, spaceId: String, authToken: String, memoryId: String, expectedVersion: Int) async {
        let base = normalizedBase(serverURL)
        guard let url = URL(string: "\(base)/api/tasks/transition") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 6
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let cleanTok = authToken.trimmingCharacters(in: .whitespacesAndNewlines)
        if !cleanTok.isEmpty {
            req.setValue("Bearer \(cleanTok)", forHTTPHeaderField: "Authorization")
        }
        let body: [String: Any] = [
            "space_id": spaceId.isEmpty ? "default" : spaceId,
            "memory_id": memoryId,
            "action": "complete_task",
            "expected_version": expectedVersion
        ]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        _ = try? await URLSession.shared.data(for: req)
    }
}

// MARK: - Timeline Entry & Provider

struct TraceTodoWidgetEntry: TimelineEntry {
    let date: Date
    let serverURL: String
    let spaceId: String
    let authToken: String
    let filter: WidgetQuadrantFilter
    let q1Count: Int
    let q2Count: Int
    let q34Count: Int
    let totalTodo: Int
    let totalDone: Int
    let overdueCount: Int
    let tasks: [WidgetTaskRow]
    let isOffline: Bool

    static func placeholder(config: TraceTodoWidgetConfigIntent, isOffline: Bool = false) -> TraceTodoWidgetEntry {
        TraceTodoWidgetEntry(
            date: Date(),
            serverURL: WidgetAPI.normalizedBase(config.serverURL),
            spaceId: config.spaceId,
            authToken: config.authToken.trimmingCharacters(in: .whitespacesAndNewlines),
            filter: config.filter,
            q1Count: 2,
            q2Count: 1,
            q34Count: 0,
            totalTodo: 3,
            totalDone: 1,
            overdueCount: 0,
            tasks: isOffline ? [] : [
                WidgetTaskRow(id: "demo1", title: "准备学术会议论文汇报", quadrant: "Q1_do_first", priorityScore: 123, dueLabel: "本周六", version: 1, isOverdue: false, subtaskProgress: ""),
                WidgetTaskRow(id: "demo2", title: "购买婚庆用品", quadrant: "Q1_do_first", priorityScore: 121, dueLabel: "12-24", version: 1, isOverdue: false, subtaskProgress: "1/3"),
                WidgetTaskRow(id: "demo3", title: "学习算法知识", quadrant: "Q2_schedule", priorityScore: 88, dueLabel: "", version: 1, isOverdue: false, subtaskProgress: "")
            ],
            isOffline: isOffline
        )
    }
}

struct TraceTodoTimelineProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> TraceTodoWidgetEntry {
        .placeholder(config: TraceTodoWidgetConfigIntent())
    }

    func snapshot(for configuration: TraceTodoWidgetConfigIntent, in context: Context) async -> TraceTodoWidgetEntry {
        if context.isPreview {
            return .placeholder(config: configuration)
        }
        return await WidgetAPI.fetchEntry(config: configuration)
    }

    func timeline(for configuration: TraceTodoWidgetConfigIntent, in context: Context) async -> Timeline<TraceTodoWidgetEntry> {
        let entry = await WidgetAPI.fetchEntry(config: configuration)
        let nextRefresh = Calendar.current.date(byAdding: .minute, value: 15, to: Date()) ?? Date().addingTimeInterval(900)
        return Timeline(entries: [entry], policy: .after(nextRefresh))
    }
}

// MARK: - Widget Views

struct TraceTodoWidgetEntryView: View {
    @Environment(\.widgetFamily) private var family
    let entry: TraceTodoWidgetEntry

    var body: some View {
        switch family {
        case .systemSmall:
            smallView
        case .systemMedium:
            mediumView
        case .systemLarge:
            largeView
        case .accessoryRectangular:
            accessoryRectView
        case .accessoryCircular:
            accessoryCircularView
        case .accessoryInline:
            accessoryInlineView
        default:
            mediumView
        }
    }

    private var smallView: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                HStack(spacing: 4) {
                    Image(systemName: "flame.fill")
                        .font(.caption2.bold())
                        .foregroundStyle(.white)
                    Text("Q1 \(entry.q1Count) · 待办 \(entry.totalTodo)")
                        .font(.system(size: 10.5, weight: .bold))
                        .foregroundStyle(.white)
                }
                .padding(.horizontal, 7)
                .padding(.vertical, 3)
                .background(
                    LinearGradient(
                        colors: [Color(red: 0.24, green: 0.51, blue: 0.96), Color(red: 0.52, green: 0.35, blue: 0.96)],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                )
                .clipShape(Capsule())

                Spacer()

                Button(intent: RefreshTodoWidgetIntent()) {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
            }

            if entry.isOffline {
                Spacer()
                Text("离线或未连接\n请轻点进入设置")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Spacer()
            } else if entry.tasks.isEmpty {
                Spacer()
                Label("今日待办已清空 🎉", systemImage: "checkmark.seal.fill")
                    .font(.caption.bold())
                    .foregroundStyle(.green)
                Spacer()
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(entry.tasks.prefix(3)) { task in
                        HStack(spacing: 5) {
                            RoundedRectangle(cornerRadius: 2)
                                .fill(task.tintColor)
                                .frame(width: 3, height: 14)
                            Text(task.title)
                                .font(.system(size: 12, weight: .medium))
                                .lineLimit(1)
                        }
                    }
                }
                Spacer(minLength: 0)
            }

            HStack {
                Text("TraceTodo")
                    .font(.system(size: 9.5, weight: .semibold))
                    .foregroundStyle(.tertiary)
                Spacer()
                Link(destination: URL(string: "tracetodo://new")!) {
                    Image(systemName: "plus.circle.fill")
                        .font(.system(size: 14))
                        .foregroundStyle(Color(red: 0.42, green: 0.34, blue: 0.96))
                }
            }
        }
        .widgetURL(URL(string: "tracetodo://overview"))
    }

    private var mediumView: some View {
        HStack(spacing: 12) {
            // Left Vibrant Summary Card
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text("TODO")
                        .font(.system(size: 10, weight: .bold))
                        .tracking(1)
                        .foregroundStyle(.white.opacity(0.85))
                    Spacer()
                    Button(intent: RefreshTodoWidgetIntent()) {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 11, weight: .bold))
                            .foregroundStyle(.white.opacity(0.9))
                    }
                    .buttonStyle(.plain)
                }

                Text("\(entry.totalTodo)")
                    .font(.system(size: 30, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)

                Spacer(minLength: 2)

                VStack(alignment: .leading, spacing: 3) {
                    Text("Q1 急事: \(entry.q1Count)")
                    Text("Q2 规划: \(entry.q2Count)")
                }
                .font(.system(size: 10.5, weight: .semibold))
                .foregroundStyle(.white.opacity(0.92))

                Link(destination: URL(string: "tracetodo://new")!) {
                    Label("新建", systemImage: "plus")
                        .font(.system(size: 10.5, weight: .bold))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(Color.white.opacity(0.22))
                        .foregroundStyle(.white)
                        .clipShape(Capsule())
                }
            }
            .padding(12)
            .frame(width: 112)
            .frame(maxHeight: .infinity)
            .background(
                LinearGradient(
                    colors: [Color(red: 0.23, green: 0.50, blue: 0.96), Color(red: 0.51, green: 0.34, blue: 0.97)],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            )
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))

            // Right Interactive Top 3 Tasks
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text("优先待办")
                        .font(.system(size: 11.5, weight: .bold))
                        .foregroundStyle(.secondary)
                    Spacer()
                    if entry.overdueCount > 0 {
                        Text("逾期 \(entry.overdueCount)")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundStyle(.red)
                    }
                }

                if entry.isOffline {
                    Spacer()
                    Text("当前无法连接服务 \(entry.serverURL)")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Spacer()
                } else if entry.tasks.isEmpty {
                    Spacer()
                    Text("当前筛选下待办已清空 🎉")
                        .font(.caption.bold())
                        .foregroundStyle(.green)
                    Spacer()
                } else {
                    ForEach(entry.tasks.prefix(3)) { task in
                        interactiveTaskRow(task)
                    }
                    Spacer(minLength: 0)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .widgetURL(URL(string: "tracetodo://overview"))
    }

    private var largeView: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                VStack(alignment: .leading, spacing: 1) {
                    Text("TraceTodo · 四象限工作台")
                        .font(.system(size: 14, weight: .bold))
                    Text("空间: \(entry.spaceId) · 已完成 \(entry.totalDone)")
                        .font(.system(size: 10.5))
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button(intent: RefreshTodoWidgetIntent()) {
                    Image(systemName: "arrow.clockwise.circle.fill")
                        .font(.title3)
                        .foregroundStyle(Color(red: 0.24, green: 0.51, blue: 0.96))
                }
                .buttonStyle(.plain)

                Link(destination: URL(string: "tracetodo://new")!) {
                    Image(systemName: "plus.circle.fill")
                        .font(.title3)
                        .foregroundStyle(Color(red: 0.51, green: 0.34, blue: 0.97))
                }
            }

            HStack(spacing: 8) {
                statPill(title: "Q1 急事", value: entry.q1Count, color: Color(red: 0.93, green: 0.26, blue: 0.34))
                statPill(title: "Q2 规划", value: entry.q2Count, color: Color(red: 0.22, green: 0.49, blue: 0.96))
                statPill(title: "Q3/Q4", value: entry.q34Count, color: Color(red: 0.08, green: 0.72, blue: 0.65))
                statPill(title: "总待办", value: entry.totalTodo, color: Color(red: 0.49, green: 0.33, blue: 0.96))
            }

            Divider()

            if entry.isOffline {
                Spacer()
                Text("当前离线，请检查服务器设置 \(entry.serverURL)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
            } else if entry.tasks.isEmpty {
                Spacer()
                Label("全部待办已完成 🎉", systemImage: "checkmark.seal.fill")
                    .font(.subheadline.bold())
                    .foregroundStyle(.green)
                    .frame(maxWidth: .infinity)
                Spacer()
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(entry.tasks.prefix(6)) { task in
                        interactiveTaskRow(task, showDetails: true)
                    }
                }
                Spacer(minLength: 0)
            }
        }
        .widgetURL(URL(string: "tracetodo://list"))
    }

    private var accessoryRectView: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Label("TraceTodo", systemImage: "checkmark.circle.fill")
                    .font(.caption2.bold())
                Spacer()
                Text("Q1:\(entry.q1Count) 总:\(entry.totalTodo)")
                    .font(.caption2)
            }
            if let top = entry.tasks.first {
                Text("[\(top.shortCode)] \(top.title)")
                    .font(.caption)
                    .lineLimit(1)
            } else {
                Text(entry.isOffline ? "离线模式" : "全部待办已清空 🎉")
                    .font(.caption)
            }
        }
        .widgetURL(URL(string: "tracetodo://overview"))
    }

    private var accessoryCircularView: some View {
        ZStack {
            AccessoryWidgetBackground()
            VStack(spacing: 0) {
                Text("\(entry.totalTodo)")
                    .font(.system(size: 18, weight: .bold, design: .rounded))
                    .widgetAccentable()
                Text("待办")
                    .font(.system(size: 8.5, weight: .medium))
            }
        }
        .widgetURL(URL(string: "tracetodo://overview"))
    }

    private var accessoryInlineView: some View {
        Group {
            if let first = entry.tasks.first {
                Label("Q1:\(entry.q1Count) · \(first.title)", systemImage: "checkmark.circle")
            } else {
                Label("今日待办 \(entry.totalTodo) 项", systemImage: "checkmark.seal")
            }
        }
        .widgetURL(URL(string: "tracetodo://overview"))
    }

    private func interactiveTaskRow(_ task: WidgetTaskRow, showDetails: Bool = false) -> some View {
        HStack(spacing: 7) {
            Button(
                intent: CompleteWidgetTaskIntent(
                    memoryId: task.id,
                    version: task.version,
                    serverURL: entry.serverURL,
                    spaceId: entry.spaceId,
                    authToken: entry.authToken
                )
            ) {
                Image(systemName: "circle")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(task.tintColor)
            }
            .buttonStyle(.plain)

            VStack(alignment: .leading, spacing: 1) {
                Text(task.title)
                    .font(.system(size: 12.5, weight: .semibold))
                    .lineLimit(1)
                    .foregroundStyle(.primary)

                if showDetails {
                    HStack(spacing: 6) {
                        Text(task.shortCode)
                            .font(.system(size: 9.5, weight: .bold))
                            .foregroundStyle(task.tintColor)
                        if !task.dueLabel.isEmpty {
                            Text(task.dueLabel)
                                .font(.system(size: 9.5))
                                .foregroundStyle(task.isOverdue ? .red : .secondary)
                        }
                        if !task.subtaskProgress.isEmpty {
                            Text("📋 \(task.subtaskProgress)")
                                .font(.system(size: 9.5))
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }

            Spacer(minLength: 4)

            if !showDetails {
                Text(task.shortCode)
                    .font(.system(size: 9.5, weight: .bold))
                    .padding(.horizontal, 5)
                    .padding(.vertical, 2)
                    .background(task.tintColor.opacity(0.14))
                    .foregroundStyle(task.tintColor)
                    .clipShape(Capsule())
            } else {
                Text("\(Int(task.priorityScore))分")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 3)
    }

    private func statPill(title: String, value: Int, color: Color) -> some View {
        VStack(spacing: 1) {
            Text("\(value)")
                .font(.system(size: 15, weight: .bold, design: .rounded))
                .foregroundStyle(.white)
            Text(title)
                .font(.system(size: 9.5, weight: .semibold))
                .foregroundStyle(.white.opacity(0.9))
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 6)
        .background(color)
        .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
    }
}

struct TraceTodoWidget: Widget {
    let kind: String = "TraceTodoWidget"

    var body: some WidgetConfiguration {
        AppIntentConfiguration(
            kind: kind,
            intent: TraceTodoWidgetConfigIntent.self,
            provider: TraceTodoTimelineProvider()
        ) { entry in
            TraceTodoWidgetContainer(entry: entry)
        }
        .configurationDisplayName("TraceTodo 四象限待办")
        .description("支持锁屏壁纸与桌面展示今日四象限待办，直接打卡完成并自动同步云端。")
        .supportedFamilies([
            .systemSmall,
            .systemMedium,
            .systemLarge,
            .accessoryRectangular,
            .accessoryCircular,
            .accessoryInline
        ])
    }
}

private struct TraceTodoWidgetContainer: View {
    @Environment(\.widgetFamily) private var family
    let entry: TraceTodoWidgetEntry

    var body: some View {
        switch family {
        case .accessoryCircular, .accessoryRectangular, .accessoryInline:
            TraceTodoWidgetEntryView(entry: entry)
                .containerBackground(.clear, for: .widget)
        default:
            TraceTodoWidgetEntryView(entry: entry)
                .containerBackground(.fill.tertiary, for: .widget)
        }
    }
}

@main
struct TraceTodoWidgetsBundle: WidgetBundle {
    var body: some Widget {
        TraceTodoWidget()
    }
}
