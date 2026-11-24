import SwiftUI
import WidgetKit

// MARK: - Lightweight Codable Models & Networking (Zero AppIntents Metadata Dependency)

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
    static func resolveSharedConfig() -> (base: String, space: String, token: String) {
        let shared = SharedWidgetBridge.loadConfig()
        let base = shared.serverURL
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let effBase = base.isEmpty ? SharedWidgetBridge.cloudServerURL : base
        let space = shared.spaceId.trimmingCharacters(in: .whitespacesAndNewlines)
        let token = shared.authToken.trimmingCharacters(in: .whitespacesAndNewlines)
        return (effBase, space.isEmpty ? "default" : space, token)
    }

    static func decodeEntry(
        from data: Data,
        base: String,
        space: String,
        token: String
    ) throws -> TraceTodoWidgetEntry {
        let dto = try JSONDecoder().decode(RawMatrixDTO.self, from: data)
        let filtered = dto.sortedTasks.filter { t in
            t.payload.taskStatus != "done" && t.payload.taskStatus != "canceled"
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
            q1Count: dto.summaryCounts.q1,
            q2Count: dto.summaryCounts.q2,
            q34Count: dto.summaryCounts.q3 + dto.summaryCounts.q4,
            totalTodo: dto.summaryCounts.totalTodo,
            totalDone: dto.summaryCounts.totalDone,
            overdueCount: dto.summaryCounts.overdue,
            tasks: filtered,
            needsLogin: false,
            isOffline: false
        )
    }

    private static func cachedFallbackEntry(
        base: String,
        space: String,
        token: String,
        needsLogin: Bool = false
    ) -> TraceTodoWidgetEntry {
        if let cachedData = SharedWidgetBridge.loadMatrixSnapshotData(),
           let entry = try? decodeEntry(from: cachedData, base: base, space: space, token: token) {
            return entry
        }
        return .placeholder(base: base, space: space, needsLogin: needsLogin, isOffline: !needsLogin)
    }

    static func fetchEntry() async -> TraceTodoWidgetEntry {
        let resolved = resolveSharedConfig()
        let base = resolved.base
        let space = resolved.space
        let cleanTok = resolved.token

        // First check if we already have an instant snapshot written by the main app AND no network token yet
        guard var comps = URLComponents(string: "\(base)/api/tasks/matrix") else {
            return cachedFallbackEntry(base: base, space: space, token: cleanTok)
        }
        var qItems: [URLQueryItem] = [
            URLQueryItem(name: "sort_by", value: "priority"),
            URLQueryItem(name: "include_done", value: "false")
        ]
        // Crucial: when authenticated with Bearer token, omit `space_id=default` so the backend automatically resolves `current_user.space_id` (avoids HTTP 403 for normal users and empty default space for admins)
        if cleanTok.isEmpty || (!space.isEmpty && space != "default") {
            qItems.append(URLQueryItem(name: "space_id", value: space.isEmpty ? "default" : space))
        }
        comps.queryItems = qItems

        guard let url = comps.url else {
            return cachedFallbackEntry(base: base, space: space, token: cleanTok)
        }
        var req = URLRequest(url: url)
        req.timeoutInterval = 8
        if !cleanTok.isEmpty {
            req.setValue("Bearer \(cleanTok)", forHTTPHeaderField: "Authorization")
        }

        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse {
                if http.statusCode == 401 || http.statusCode == 403 {
                    // Retry once without explicit space_id if 403 occurred on a stale spaceId
                    if http.statusCode == 403, !cleanTok.isEmpty {
                        var retryComps = URLComponents(string: "\(base)/api/tasks/matrix")
                        retryComps?.queryItems = [
                            URLQueryItem(name: "sort_by", value: "priority"),
                            URLQueryItem(name: "include_done", value: "false")
                        ]
                        if let retryURL = retryComps?.url {
                            var retryReq = URLRequest(url: retryURL)
                            retryReq.timeoutInterval = 8
                            retryReq.setValue("Bearer \(cleanTok)", forHTTPHeaderField: "Authorization")
                            if let (rData, rResp) = try? await URLSession.shared.data(for: retryReq),
                               let rHttp = rResp as? HTTPURLResponse,
                               (200...299).contains(rHttp.statusCode),
                               let decoded = try? decodeEntry(from: rData, base: base, space: "我的空间", token: cleanTok) {
                                SharedWidgetBridge.saveMatrixSnapshotData(rData)
                                return decoded
                            }
                        }
                    }
                    return cachedFallbackEntry(base: base, space: space, token: cleanTok, needsLogin: true)
                }
                guard (200...299).contains(http.statusCode) else {
                    return cachedFallbackEntry(base: base, space: space, token: cleanTok)
                }
            }
            SharedWidgetBridge.saveMatrixSnapshotData(data)
            return try decodeEntry(from: data, base: base, space: space, token: cleanTok)
        } catch {
            return cachedFallbackEntry(base: base, space: space, token: cleanTok)
        }
    }
}

// MARK: - Timeline Entry & Static Provider

struct TraceTodoWidgetEntry: TimelineEntry {
    let date: Date
    let serverURL: String
    let spaceId: String
    let authToken: String
    let q1Count: Int
    let q2Count: Int
    let q34Count: Int
    let totalTodo: Int
    let totalDone: Int
    let overdueCount: Int
    let tasks: [WidgetTaskRow]
    let needsLogin: Bool
    let isOffline: Bool

    static func placeholder(
        base: String = SharedWidgetBridge.cloudServerURL,
        space: String = "default",
        needsLogin: Bool = false,
        isOffline: Bool = false
    ) -> TraceTodoWidgetEntry {
        let isEmptyState = needsLogin || isOffline
        return TraceTodoWidgetEntry(
            date: Date(),
            serverURL: base,
            spaceId: space,
            authToken: "",
            q1Count: isEmptyState ? 0 : 2,
            q2Count: isEmptyState ? 0 : 1,
            q34Count: 0,
            totalTodo: isEmptyState ? 0 : 3,
            totalDone: isEmptyState ? 0 : 1,
            overdueCount: 0,
            tasks: isEmptyState ? [] : [
                WidgetTaskRow(id: "demo1", title: "准备学术会议论文汇报", quadrant: "Q1_do_first", priorityScore: 123, dueLabel: "本周六", version: 1, isOverdue: false, subtaskProgress: ""),
                WidgetTaskRow(id: "demo2", title: "整理阶段复盘笔记", quadrant: "Q1_do_first", priorityScore: 121, dueLabel: "今日", version: 1, isOverdue: false, subtaskProgress: "1/3"),
                WidgetTaskRow(id: "demo3", title: "阅读深度专业书籍", quadrant: "Q2_schedule", priorityScore: 88, dueLabel: "", version: 1, isOverdue: false, subtaskProgress: "")
            ],
            needsLogin: needsLogin,
            isOffline: isOffline
        )
    }
}

struct TraceTodoStaticProvider: TimelineProvider {
    func placeholder(in context: Context) -> TraceTodoWidgetEntry {
        .placeholder()
    }

    func getSnapshot(in context: Context, completion: @escaping (TraceTodoWidgetEntry) -> Void) {
        if context.isPreview {
            if let cached = SharedWidgetBridge.loadMatrixSnapshotData(),
               let entry = try? WidgetAPI.decodeEntry(from: cached, base: SharedWidgetBridge.cloudServerURL, space: "default", token: "") {
                completion(entry)
                return
            }
            completion(.placeholder())
            return
        }
        Task {
            let entry = await WidgetAPI.fetchEntry()
            completion(entry)
        }
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<TraceTodoWidgetEntry>) -> Void) {
        Task {
            let entry = await WidgetAPI.fetchEntry()
            let mins = entry.needsLogin ? 5 : 15
            let nextRefresh = Calendar.current.date(byAdding: .minute, value: mins, to: Date()) ?? Date().addingTimeInterval(Double(mins * 60))
            completion(Timeline(entries: [entry], policy: .after(nextRefresh)))
        }
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
        #if os(iOS)
        case .accessoryRectangular:
            accessoryRectView
        case .accessoryCircular:
            accessoryCircularView
        case .accessoryInline:
            accessoryInlineView
        #endif
        default:
            mediumView
        }
    }

    @ViewBuilder
    private var emptyOrStatusPrompt: some View {
        if entry.needsLogin {
            Link(destination: URL(string: "tracetodo://login")!) {
                VStack(alignment: .leading, spacing: 4) {
                    Label("点击打开 App 登录同步", systemImage: "person.crop.circle.badge.plus")
                        .font(.caption.bold())
                        .foregroundStyle(Color(red: 0.23, green: 0.50, blue: 0.96))
                    Text("登录后四象限待办将自动实时显示在桌面")
                        .font(.system(size: 10.5))
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }
        } else if entry.isOffline {
            Link(destination: URL(string: "tracetodo://refresh")!) {
                VStack(alignment: .leading, spacing: 4) {
                    Label("轻点打开 TraceTodo 同步数据", systemImage: "arrow.triangle.2.circlepath")
                        .font(.caption.bold())
                        .foregroundStyle(.orange)
                    Text("首次添加后打开主应用即可缓存最新待办")
                        .font(.system(size: 10.5))
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }
        } else {
            Label("今日待办已全部清空 🎉", systemImage: "checkmark.seal.fill")
                .font(.caption.bold())
                .foregroundStyle(.green)
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

                Link(destination: URL(string: "tracetodo://refresh")!) {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.secondary)
                }
            }

            if entry.tasks.isEmpty {
                Spacer(minLength: 2)
                emptyOrStatusPrompt
                Spacer(minLength: 2)
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
        .widgetURL(URL(string: entry.needsLogin ? "tracetodo://login" : "tracetodo://overview"))
    }

    private var mediumView: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text("TODO")
                        .font(.system(size: 10, weight: .bold))
                        .tracking(1)
                        .foregroundStyle(.white.opacity(0.85))
                    Spacer()
                    Link(destination: URL(string: "tracetodo://refresh")!) {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 11, weight: .bold))
                            .foregroundStyle(.white.opacity(0.9))
                    }
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

                if entry.tasks.isEmpty {
                    Spacer(minLength: 2)
                    emptyOrStatusPrompt
                    Spacer(minLength: 2)
                } else {
                    ForEach(entry.tasks.prefix(3)) { task in
                        interactiveTaskRow(task)
                    }
                    Spacer(minLength: 0)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .widgetURL(URL(string: entry.needsLogin ? "tracetodo://login" : "tracetodo://overview"))
    }

    private var largeView: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                VStack(alignment: .leading, spacing: 1) {
                    Text("TraceTodo · 四象限工作台")
                        .font(.system(size: 14, weight: .bold))
                    Text("已完成 \(entry.totalDone) · 待办 \(entry.totalTodo)")
                        .font(.system(size: 10.5))
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Link(destination: URL(string: "tracetodo://refresh")!) {
                    Image(systemName: "arrow.clockwise.circle.fill")
                        .font(.title3)
                        .foregroundStyle(Color(red: 0.24, green: 0.51, blue: 0.96))
                }

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

            if entry.tasks.isEmpty {
                Spacer()
                emptyOrStatusPrompt
                    .frame(maxWidth: .infinity, alignment: .leading)
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
        .widgetURL(URL(string: entry.needsLogin ? "tracetodo://login" : "tracetodo://list"))
    }

    #if os(iOS)
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
                Text(entry.needsLogin ? "打开 App 登录同步" : (entry.isOffline ? "轻点同步待办" : "全部待办已清空 🎉"))
                    .font(.caption)
            }
        }
        .widgetURL(URL(string: entry.needsLogin ? "tracetodo://login" : "tracetodo://overview"))
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
    #endif

    private func interactiveTaskRow(_ task: WidgetTaskRow, showDetails: Bool = false) -> some View {
        let completeURL = URL(string: "tracetodo://complete?id=\(task.id.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? task.id)&v=\(task.version)")!
        return HStack(spacing: 7) {
            Link(destination: completeURL) {
                Image(systemName: "circle")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(task.tintColor)
            }

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

    #if os(iOS)
    private let families: [WidgetFamily] = [
        .systemSmall,
        .systemMedium,
        .systemLarge,
        .accessoryRectangular,
        .accessoryCircular,
        .accessoryInline
    ]
    #else
    private let families: [WidgetFamily] = [
        .systemSmall,
        .systemMedium,
        .systemLarge
    ]
    #endif

    var body: some WidgetConfiguration {
        StaticConfiguration(
            kind: kind,
            provider: TraceTodoStaticProvider()
        ) { entry in
            TraceTodoWidgetContainer(entry: entry)
        }
        .configurationDisplayName("TraceTodo 四象限待办")
        .description("实时展示今日四象限待办，自动跟随主应用账号同步。")
        .supportedFamilies(families)
    }
}

private struct TraceTodoWidgetContainer: View {
    @Environment(\.widgetFamily) private var family
    let entry: TraceTodoWidgetEntry

    var body: some View {
        #if os(iOS)
        switch family {
        case .accessoryCircular, .accessoryRectangular, .accessoryInline:
            TraceTodoWidgetEntryView(entry: entry)
                .containerBackground(.clear, for: .widget)
        default:
            TraceTodoWidgetEntryView(entry: entry)
                .containerBackground(.fill.tertiary, for: .widget)
        }
        #else
        TraceTodoWidgetEntryView(entry: entry)
            .containerBackground(.fill.tertiary, for: .widget)
        #endif
    }
}

// MARK: - Countdown & Anniversary Widget (StaticConfiguration)

struct WidgetCountdownRow: Identifiable, Hashable {
    let id: String
    let title: String
    let icon: String
    let headerBanner: String
    let displayNumber: String
    let unitLabel: String
    let badgeLabel: String
    let displaySolarDate: String
    let lunarLabel: String
    let colorTheme: String

    var bannerGradient: LinearGradient {
        let colors: [Color]
        switch colorTheme {
        case "crimson":
            colors = [Color(red: 0.86, green: 0.15, blue: 0.15), Color(red: 0.96, green: 0.25, blue: 0.37)]
        case "lavender":
            colors = [Color(red: 0.49, green: 0.23, blue: 0.93), Color(red: 0.65, green: 0.55, blue: 0.98)]
        case "pink":
            colors = [Color(red: 0.86, green: 0.15, blue: 0.47), Color(red: 0.96, green: 0.45, blue: 0.71)]
        case "mint":
            colors = [Color(red: 0.02, green: 0.59, blue: 0.41), Color(red: 0.20, green: 0.83, blue: 0.60)]
        case "peach":
            colors = [Color(red: 0.92, green: 0.35, blue: 0.05), Color(red: 0.98, green: 0.57, blue: 0.24)]
        default:
            colors = [Color(red: 0.01, green: 0.52, blue: 0.78), Color(red: 0.22, green: 0.74, blue: 0.97)]
        }
        return LinearGradient(colors: colors, startPoint: .topLeading, endPoint: .bottomTrailing)
    }
}

private struct RawCountdownListDTO: Decodable {
    let todayLunar: String?
    let items: [RawCountdownItemDTO]

    enum CodingKeys: String, CodingKey {
        case todayLunar = "today_lunar"
        case items
    }
}

private struct RawCountdownItemDTO: Decodable {
    let countdownId: String
    let title: String
    let icon: String?
    let headerBanner: String?
    let displayNumber: String?
    let unitLabel: String?
    let badgeLabel: String?
    let displaySolarDate: String?
    let lunarLabel: String?
    let colorTheme: String?

    enum CodingKeys: String, CodingKey {
        case countdownId = "countdown_id"
        case title, icon
        case headerBanner = "header_banner"
        case displayNumber = "display_number"
        case unitLabel = "unit_label"
        case badgeLabel = "badge_label"
        case displaySolarDate = "display_solar_date"
        case lunarLabel = "lunar_label"
        case colorTheme = "color_theme"
    }
}

struct TraceCountdownEntry: TimelineEntry {
    let date: Date
    let todayLunar: String
    let items: [WidgetCountdownRow]

    static func placeholder() -> TraceCountdownEntry {
        TraceCountdownEntry(
            date: Date(),
            todayLunar: "丙午马年 · 农历冬月",
            items: [
                WidgetCountdownRow(
                    id: "cd1",
                    title: "春节团圆",
                    icon: "🧧",
                    headerBanner: "距离 春节团圆 还有",
                    displayNumber: "76",
                    unitLabel: "天",
                    badgeLabel: "还有",
                    displaySolarDate: "2027-02-06",
                    lunarLabel: "农历正月初一",
                    colorTheme: "crimson"
                ),
                WidgetCountdownRow(
                    id: "cd2",
                    title: "里程碑发布",
                    icon: "🚀",
                    headerBanner: "距离 里程碑发布 还有",
                    displayNumber: "1",
                    unitLabel: "天",
                    badgeLabel: "还有",
                    displaySolarDate: "明日",
                    lunarLabel: "置顶目标",
                    colorTheme: "lavender"
                )
            ]
        )
    }
}

struct TraceCountdownStaticProvider: TimelineProvider {
    func placeholder(in context: Context) -> TraceCountdownEntry {
        .placeholder()
    }

    func getSnapshot(in context: Context, completion: @escaping (TraceCountdownEntry) -> Void) {
        if context.isPreview {
            if let cached = SharedWidgetBridge.loadCountdownsSnapshotData(),
               let parsed = try? Self.decodeCountdownData(cached),
               !parsed.items.isEmpty {
                completion(parsed)
                return
            }
            completion(.placeholder())
            return
        }
        Task {
            let entry = await Self.fetchCountdownEntry()
            completion(entry)
        }
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<TraceCountdownEntry>) -> Void) {
        Task {
            let entry = await Self.fetchCountdownEntry()
            let nextRefresh = Calendar.current.date(byAdding: .minute, value: 30, to: Date()) ?? Date().addingTimeInterval(1800)
            completion(Timeline(entries: [entry], policy: .after(nextRefresh)))
        }
    }

    static func decodeCountdownData(_ data: Data) throws -> TraceCountdownEntry {
        let dto = try JSONDecoder().decode(RawCountdownListDTO.self, from: data)
        let rows = dto.items.map { it in
            WidgetCountdownRow(
                id: it.countdownId,
                title: it.title,
                icon: it.icon ?? "⏳",
                headerBanner: it.headerBanner ?? it.title,
                displayNumber: it.displayNumber ?? "0",
                unitLabel: it.unitLabel ?? "",
                badgeLabel: it.badgeLabel ?? "还有",
                displaySolarDate: it.displaySolarDate ?? "",
                lunarLabel: it.lunarLabel ?? "",
                colorTheme: it.colorTheme ?? "crimson"
            )
        }
        return TraceCountdownEntry(date: Date(), todayLunar: dto.todayLunar ?? "", items: rows)
    }

    static func fetchCountdownEntry() async -> TraceCountdownEntry {
        let resolved = WidgetAPI.resolveSharedConfig()
        let base = resolved.base
        let space = resolved.space
        let token = resolved.token

        if var comps = URLComponents(string: "\(base)/api/countdowns/list") {
            if token.isEmpty || (!space.isEmpty && space != "default") {
                comps.queryItems = [URLQueryItem(name: "space_id", value: space.isEmpty ? "default" : space)]
            }
            if let url = comps.url {
                var req = URLRequest(url: url)
                req.timeoutInterval = 8
                if !token.isEmpty {
                    req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
                }
                if let (data, resp) = try? await URLSession.shared.data(for: req),
                   let http = resp as? HTTPURLResponse,
                   (200...299).contains(http.statusCode),
                   let parsed = try? decodeCountdownData(data),
                   !parsed.items.isEmpty {
                    SharedWidgetBridge.saveCountdownsSnapshotData(data)
                    return parsed
                }
            }
        }

        if let cached = SharedWidgetBridge.loadCountdownsSnapshotData(),
           let parsed = try? decodeCountdownData(cached),
           !parsed.items.isEmpty {
            return parsed
        }
        return .placeholder()
    }
}

struct TraceCountdownWidgetEntryView: View {
    @Environment(\.widgetFamily) private var family
    let entry: TraceCountdownEntry

    var body: some View {
        switch family {
        case .systemSmall:
            smallCard
        case .systemMedium:
            mediumWall
        #if os(iOS)
        case .accessoryCircular:
            lockCircular
        case .accessoryRectangular:
            lockRectangular
        case .accessoryInline:
            lockInline
        #endif
        default:
            smallCard
        }
    }

    private var primaryItem: WidgetCountdownRow {
        entry.items.first ?? TraceCountdownEntry.placeholder().items[0]
    }

    private var smallCard: some View {
        let item = primaryItem
        return VStack(spacing: 0) {
            HStack {
                Text("\(item.icon) \(item.headerBanner)")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(.white)
                    .lineLimit(1)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(item.bannerGradient)
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))

            Spacer(minLength: 4)

            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(item.displayNumber)
                    .font(.system(size: 38, weight: .heavy, design: .rounded))
                    .monospacedDigit()
                if !item.unitLabel.isEmpty {
                    Text(item.unitLabel)
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(.secondary)
                }
            }

            Spacer(minLength: 4)

            VStack(spacing: 1) {
                Text(item.displaySolarDate)
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.secondary)
                if !item.lunarLabel.isEmpty {
                    Text(item.lunarLabel)
                        .font(.system(size: 9.5))
                        .foregroundStyle(.tertiary)
                }
            }
        }
        .widgetURL(URL(string: "tracetodo://countdown"))
    }

    private var mediumWall: some View {
        let top = primaryItem
        let others = Array(entry.items.dropFirst().prefix(2))
        return HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text("\(top.icon) \(top.title)")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(.white)
                    .lineLimit(1)
                Text(top.badgeLabel)
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.85))
                Spacer(minLength: 2)
                HStack(alignment: .firstTextBaseline, spacing: 2) {
                    Text(top.displayNumber)
                        .font(.system(size: 32, weight: .heavy, design: .rounded))
                        .foregroundStyle(.white)
                    Text(top.unitLabel)
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(.white.opacity(0.9))
                }
                Text(top.lunarLabel.isEmpty ? top.displaySolarDate : top.lunarLabel)
                    .font(.system(size: 9.5, weight: .medium))
                    .foregroundStyle(.white.opacity(0.88))
                    .lineLimit(1)
            }
            .padding(11)
            .frame(width: 126)
            .frame(maxHeight: .infinity, alignment: .leading)
            .background(top.bannerGradient)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))

            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("⏳ 倒数纪念日")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(.secondary)
                    Spacer()
                    Link(destination: URL(string: "tracetodo://countdown")!) {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(.secondary)
                    }
                }

                if others.isEmpty {
                    Spacer()
                    Text(entry.todayLunar.isEmpty ? "在 App 日历页添加更多公农历纪念日" : entry.todayLunar)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Spacer()
                } else {
                    ForEach(others) { row in
                        HStack {
                            VStack(alignment: .leading, spacing: 1) {
                                Text("\(row.icon) \(row.title)")
                                    .font(.system(size: 12, weight: .semibold))
                                    .lineLimit(1)
                                Text(row.lunarLabel.isEmpty ? row.displaySolarDate : row.lunarLabel)
                                    .font(.system(size: 9.5))
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                            Spacer(minLength: 4)
                            VStack(alignment: .trailing, spacing: 0) {
                                Text("\(row.displayNumber)\(row.unitLabel)")
                                    .font(.system(size: 14, weight: .heavy, design: .rounded))
                                Text(row.badgeLabel)
                                    .font(.system(size: 9))
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(.vertical, 2)
                    }
                    Spacer(minLength: 0)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .widgetURL(URL(string: "tracetodo://countdown"))
    }

    #if os(iOS)
    private var lockCircular: some View {
        let item = primaryItem
        return ZStack {
            AccessoryWidgetBackground()
            VStack(spacing: 0) {
                Text(item.displayNumber)
                    .font(.system(size: 17, weight: .heavy, design: .rounded))
                    .widgetAccentable()
                Text(item.unitLabel.isEmpty ? item.icon : "\(item.badgeLabel)\(item.unitLabel)")
                    .font(.system(size: 8, weight: .medium))
            }
        }
        .widgetURL(URL(string: "tracetodo://countdown"))
    }

    private var lockRectangular: some View {
        let item = primaryItem
        return VStack(alignment: .leading, spacing: 2) {
            Text("\(item.icon) \(item.title)")
                .font(.caption.bold())
                .lineLimit(1)
            Text("\(item.badgeLabel) \(item.displayNumber) \(item.unitLabel)")
                .font(.system(size: 13, weight: .heavy, design: .rounded))
                .widgetAccentable()
            Text("\(item.displaySolarDate) · \(item.lunarLabel)")
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .widgetURL(URL(string: "tracetodo://countdown"))
    }

    private var lockInline: some View {
        let item = primaryItem
        return Text("\(item.icon) \(item.title) · \(item.badgeLabel)\(item.displayNumber)\(item.unitLabel)")
            .widgetURL(URL(string: "tracetodo://countdown"))
    }
    #endif
}

struct TraceCountdownWidget: Widget {
    let kind: String = "TraceCountdownWidget"

    #if os(iOS)
    private let families: [WidgetFamily] = [
        .systemSmall,
        .systemMedium,
        .accessoryCircular,
        .accessoryRectangular,
        .accessoryInline
    ]
    #else
    private let families: [WidgetFamily] = [
        .systemSmall,
        .systemMedium
    ]
    #endif

    var body: some WidgetConfiguration {
        StaticConfiguration(
            kind: kind,
            provider: TraceCountdownStaticProvider()
        ) { entry in
            TraceCountdownWidgetContainer(entry: entry)
        }
        .configurationDisplayName("TraceTodo 倒数纪念日")
        .description("桌面与锁屏展示公农历倒数日与正数纪念日（春节、生日、周年纪念）。")
        .supportedFamilies(families)
    }
}

private struct TraceCountdownWidgetContainer: View {
    @Environment(\.widgetFamily) private var family
    let entry: TraceCountdownEntry

    var body: some View {
        #if os(iOS)
        switch family {
        case .accessoryCircular, .accessoryRectangular, .accessoryInline:
            TraceCountdownWidgetEntryView(entry: entry)
                .containerBackground(.clear, for: .widget)
        default:
            TraceCountdownWidgetEntryView(entry: entry)
                .containerBackground(.fill.tertiary, for: .widget)
        }
        #else
        TraceCountdownWidgetEntryView(entry: entry)
            .containerBackground(.fill.tertiary, for: .widget)
        #endif
    }
}

@main
struct TraceTodoWidgetsBundle: WidgetBundle {
    var body: some Widget {
        TraceTodoWidget()
        TraceCountdownWidget()
    }
}
