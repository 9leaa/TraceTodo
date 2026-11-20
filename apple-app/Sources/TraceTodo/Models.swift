import Foundation
import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

@MainActor
func dismissAppKeyboard() {
    #if canImport(UIKit)
    UIApplication.shared.sendAction(
        #selector(UIResponder.resignFirstResponder),
        to: nil,
        from: nil,
        for: nil
    )
    #endif
}

extension View {
    @ViewBuilder
    func iosScrollDismissesKeyboard() -> some View {
        #if os(iOS)
        self.scrollDismissesKeyboard(.interactively)
        #else
        self
        #endif
    }

    @ViewBuilder
    func iosKeyboardDoneToolbar() -> some View {
        #if os(iOS)
        self.toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("收起键盘") {
                    dismissAppKeyboard()
                }
                .font(.subheadline.weight(.semibold))
            }
        }
        #else
        self
        #endif
    }
}

enum QuadrantKind: String, CaseIterable, Codable, Identifiable {
    case q1 = "Q1_do_first"
    case q2 = "Q2_schedule"
    case q3 = "Q3_delegate"
    case q4 = "Q4_eliminate"

    var id: String { rawValue }

    var shortCode: String {
        switch self {
        case .q1: return "Q1"
        case .q2: return "Q2"
        case .q3: return "Q3"
        case .q4: return "Q4"
        }
    }

    var title: String {
        switch self {
        case .q1: return "Q1 重要且紧急"
        case .q2: return "Q2 重要不紧急"
        case .q3: return "Q3 紧急不重要"
        case .q4: return "Q4 稍后 / 精简"
        }
    }

    var subtitle: String {
        switch self {
        case .q1: return "马上做 · 核心交付与临期急事"
        case .q2: return "计划做 · 长期高价值与成长"
        case .q3: return "速办/授权 · 临时协作杂事"
        case .q4: return "精简/延后 · 低优闲暇事项"
        }
    }

    var iconName: String {
        switch self {
        case .q1: return "flame.fill"
        case .q2: return "calendar.badge.clock"
        case .q3: return "bolt.fill"
        case .q4: return "leaf.fill"
        }
    }

    var tintColor: Color {
        switch self {
        case .q1: return Color(red: 0.86, green: 0.15, blue: 0.15)
        case .q2: return Color(red: 0.15, green: 0.39, blue: 0.92)
        case .q3: return Color(red: 0.85, green: 0.47, blue: 0.02)
        case .q4: return Color(red: 0.39, green: 0.45, blue: 0.55)
        }
    }

    var softBackground: Color {
        tintColor.opacity(0.08)
    }
}

enum MacaronTheme: String, CaseIterable, Codable, Identifiable {
    case lavender
    case pink
    case sky
    case butter
    case mint
    case peach

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .lavender: return "薰衣草紫"
        case .pink: return "樱花粉"
        case .sky: return "天空蓝"
        case .butter: return "奶油黄"
        case .mint: return "薄荷绿"
        case .peach: return "蜜桃橙"
        }
    }

    var cardBackground: Color {
        switch self {
        case .lavender: return Color(red: 0.93, green: 0.90, blue: 1.00)
        case .pink:     return Color(red: 1.00, green: 0.89, blue: 0.93)
        case .sky:      return Color(red: 0.74, green: 0.92, blue: 1.00)
        case .butter:   return Color(red: 1.00, green: 0.95, blue: 0.69)
        case .mint:     return Color(red: 0.79, green: 0.97, blue: 0.81)
        case .peach:    return Color(red: 1.00, green: 0.88, blue: 0.73)
        }
    }

    var dotColor: Color {
        switch self {
        case .lavender: return Color(red: 0.85, green: 0.71, blue: 1.00)
        case .pink:     return Color(red: 0.98, green: 0.75, blue: 0.85)
        case .sky:      return Color(red: 0.49, green: 0.83, blue: 0.99)
        case .butter:   return Color(red: 0.99, green: 0.89, blue: 0.48)
        case .mint:     return Color(red: 0.53, green: 0.94, blue: 0.67)
        case .peach:    return Color(red: 0.99, green: 0.73, blue: 0.46)
        }
    }

    var accentColor: Color {
        switch self {
        case .lavender: return Color(red: 0.55, green: 0.36, blue: 0.96)
        case .pink:     return Color(red: 0.93, green: 0.28, blue: 0.60)
        case .sky:      return Color(red: 0.05, green: 0.65, blue: 0.91)
        case .butter:   return Color(red: 0.85, green: 0.65, blue: 0.04)
        case .mint:     return Color(red: 0.06, green: 0.73, blue: 0.51)
        case .peach:    return Color(red: 0.97, green: 0.45, blue: 0.09)
        }
    }
}

enum TaskModeOption: String, CaseIterable, Codable, Identifiable {
    case personal
    case multiplayer

    var id: String { rawValue }

    var label: String {
        switch self {
        case .personal: return "个人任务"
        case .multiplayer: return "多人任务"
        }
    }
}

enum RepeatRuleOption: String, CaseIterable, Codable, Identifiable {
    case never
    case daily
    case weekdays
    case weekly
    case monthly

    var id: String { rawValue }

    var label: String {
        switch self {
        case .never: return "永不"
        case .daily: return "每天"
        case .weekdays: return "工作日"
        case .weekly: return "每周"
        case .monthly: return "每月"
        }
    }
}

enum ReminderOffsetOption: Int, CaseIterable, Identifiable {
    case none = -1
    case onTime = 0
    case min15 = 15
    case hour1 = 60

    var id: Int { rawValue }

    var label: String {
        switch self {
        case .none: return "无提醒"
        case .onTime: return "准时提醒"
        case .min15: return "提前 15 分钟"
        case .hour1: return "提前 1 小时"
        }
    }

    var apiValue: Int? {
        self == .none ? nil : rawValue
    }

    static func from(apiValue: Int?) -> ReminderOffsetOption {
        guard let v = apiValue else { return .none }
        return ReminderOffsetOption(rawValue: v) ?? .onTime
    }
}

enum TaskSortOption: String, CaseIterable, Identifiable {
    case priority = "priority"
    case urgency = "urgency"
    case importance = "importance"
    case updated = "updated"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .priority: return "综合优先级"
        case .urgency: return "紧急程度"
        case .importance: return "重要程度"
        case .updated: return "更新时间"
        }
    }
}

struct SubtaskItem: Codable, Identifiable, Hashable {
    var id: String
    var title: String
    var done: Bool
}

struct NudgeItem: Codable, Identifiable, Hashable {
    let id: String
    let fromUser: String
    let kind: String
    let message: String
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id
        case fromUser = "from_user"
        case kind
        case message
        case createdAt = "created_at"
    }
}

struct TaskPayloadModel: Codable, Hashable {
    let taskStatus: String
    let closeReason: String?
    let importance: Int
    let urgency: Int
    let effectiveUrgency: Int?
    let quadrant: String
    let quadrantLabel: String?
    let priorityScore: Double
    let priorityReason: String?
    let dueAt: String?
    let isOverdue: Bool?
    let assignee: String?
    let colorTheme: String?
    let taskMode: String?
    let supervisionEnabled: Bool?
    let isAllDay: Bool?
    let repeatRule: String?
    let reminderOffsetMinutes: Int?
    let autoRollover: Bool?
    let rolloverCount: Int?
    let lastRolloverDate: String?
    let subtasks: [SubtaskItem]?
    let focusLockEnabled: Bool?
    let focusMinutes: Int?
    let focusSessionsCompleted: Int?
    let nudges: [NudgeItem]?

    var quadrantKind: QuadrantKind {
        QuadrantKind(rawValue: quadrant) ?? .q4
    }

    var themeKind: MacaronTheme {
        MacaronTheme(rawValue: colorTheme ?? "sky") ?? .sky
    }

    var isDone: Bool {
        taskStatus == "done"
    }

    var subtaskList: [SubtaskItem] {
        subtasks ?? []
    }

    var nudgeList: [NudgeItem] {
        nudges ?? []
    }

    enum CodingKeys: String, CodingKey {
        case taskStatus = "task_status"
        case closeReason = "close_reason"
        case importance
        case urgency
        case effectiveUrgency = "effective_urgency"
        case quadrant
        case quadrantLabel = "quadrant_label"
        case priorityScore = "priority_score"
        case priorityReason = "priority_reason"
        case dueAt = "due_at"
        case isOverdue = "is_overdue"
        case assignee
        case colorTheme = "color_theme"
        case taskMode = "task_mode"
        case supervisionEnabled = "supervision_enabled"
        case isAllDay = "is_all_day"
        case repeatRule = "repeat_rule"
        case reminderOffsetMinutes = "reminder_offset_minutes"
        case autoRollover = "auto_rollover"
        case rolloverCount = "rollover_count"
        case lastRolloverDate = "last_rollover_date"
        case subtasks
        case focusLockEnabled = "focus_lock_enabled"
        case focusMinutes = "focus_minutes"
        case focusSessionsCompleted = "focus_sessions_completed"
        case nudges
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        taskStatus = try c.decodeIfPresent(String.self, forKey: .taskStatus) ?? "todo"
        closeReason = try c.decodeIfPresent(String.self, forKey: .closeReason)
        importance = try c.decodeIfPresent(Int.self, forKey: .importance) ?? 3
        urgency = try c.decodeIfPresent(Int.self, forKey: .urgency) ?? 3
        effectiveUrgency = try c.decodeIfPresent(Int.self, forKey: .effectiveUrgency)
        quadrant = try c.decodeIfPresent(String.self, forKey: .quadrant) ?? "Q4_eliminate"
        quadrantLabel = try c.decodeIfPresent(String.self, forKey: .quadrantLabel)
        priorityScore = try c.decodeIfPresent(Double.self, forKey: .priorityScore) ?? 0.0
        priorityReason = try c.decodeIfPresent(String.self, forKey: .priorityReason)
        dueAt = try c.decodeIfPresent(String.self, forKey: .dueAt)
        isOverdue = try c.decodeIfPresent(Bool.self, forKey: .isOverdue)
        assignee = try c.decodeIfPresent(String.self, forKey: .assignee)
        colorTheme = try c.decodeIfPresent(String.self, forKey: .colorTheme)
        taskMode = try c.decodeIfPresent(String.self, forKey: .taskMode)
        supervisionEnabled = try c.decodeIfPresent(Bool.self, forKey: .supervisionEnabled)
        isAllDay = try c.decodeIfPresent(Bool.self, forKey: .isAllDay)
        repeatRule = try c.decodeIfPresent(String.self, forKey: .repeatRule)
        reminderOffsetMinutes = try c.decodeIfPresent(Int.self, forKey: .reminderOffsetMinutes)
        autoRollover = try c.decodeIfPresent(Bool.self, forKey: .autoRollover)
        rolloverCount = try c.decodeIfPresent(Int.self, forKey: .rolloverCount)
        lastRolloverDate = try c.decodeIfPresent(String.self, forKey: .lastRolloverDate)
        subtasks = try c.decodeIfPresent([SubtaskItem].self, forKey: .subtasks)
        focusLockEnabled = try c.decodeIfPresent(Bool.self, forKey: .focusLockEnabled)
        focusMinutes = try c.decodeIfPresent(Int.self, forKey: .focusMinutes)
        focusSessionsCompleted = try c.decodeIfPresent(Int.self, forKey: .focusSessionsCompleted)
        nudges = try c.decodeIfPresent([NudgeItem].self, forKey: .nudges)
    }
}

struct EvidenceRefModel: Codable, Hashable {
    let noteId: String
    let noteRevision: Int
    let quote: String

    enum CodingKeys: String, CodingKey {
        case noteId = "note_id"
        case noteRevision = "note_revision"
        case quote
    }
}

struct TaskItemModel: Codable, Identifiable, Hashable {
    let memoryId: String
    let permalink: String
    let uri: String
    let title: String
    let summary: String
    let version: Int
    let userLocked: Bool
    let payload: TaskPayloadModel
    let evidences: [EvidenceRefModel]?
    let updatedAt: String

    var id: String { memoryId }

    enum CodingKeys: String, CodingKey {
        case memoryId = "memory_id"
        case permalink
        case uri
        case title
        case summary
        case version
        case userLocked = "user_locked"
        case payload
        case evidences
        case updatedAt = "updated_at"
    }
}

struct QuadrantBucket: Codable {
    let quadrant: String
    let label: String
    let count: Int
    let tasks: [TaskItemModel]
}

struct MatrixSummaryCounts: Codable {
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

struct MatrixResponse: Codable {
    let summaryCounts: MatrixSummaryCounts
    let quadrants: [String: QuadrantBucket]
    let sortedTasks: [TaskItemModel]
    let diagnostics: [String]

    enum CodingKeys: String, CodingKey {
        case summaryCounts = "summary_counts"
        case quadrants
        case sortedTasks = "sorted_tasks"
        case diagnostics
    }
}

struct DecomposeResponse: Codable {
    let subtasks: [SubtaskItem]
    let memoryContextUsed: [String]

    enum CodingKeys: String, CodingKey {
        case subtasks
        case memoryContextUsed = "memory_context_used"
    }
}

struct SubtaskToggleResponse: Codable {
    let memory: TaskItemModel?
    let allSubtasksDone: Bool

    enum CodingKeys: String, CodingKey {
        case memory
        case allSubtasksDone = "all_subtasks_done"
    }
}

struct TaskTransitionResponse: Codable {
    let memory: TaskItemModel?
    let nextTask: TaskItemModel?

    enum CodingKeys: String, CodingKey {
        case memory
        case nextTask = "next_task"
    }
}

struct SupervisionSummary: Codable {
    let totalSupervised: Int
    let doneSupervised: Int
    let todoSupervised: Int
    let completionRate: Double

    enum CodingKeys: String, CodingKey {
        case totalSupervised = "total_supervised"
        case doneSupervised = "done_supervised"
        case todoSupervised = "todo_supervised"
        case completionRate = "completion_rate"
    }
}

struct SupervisionBoardResponse: Codable {
    let spaceId: String
    let summary: SupervisionSummary
    let assignees: [String]
    let tasks: [TaskItemModel]

    enum CodingKeys: String, CodingKey {
        case spaceId = "space_id"
        case summary
        case assignees
        case tasks
    }
}

struct SourceNoteDetail: Codable, Identifiable {
    let noteId: String
    let citedRevision: Int
    let visibleContent: String
    let eventTime: String

    var id: String { "\(noteId)-\(citedRevision)" }

    enum CodingKeys: String, CodingKey {
        case noteId = "note_id"
        case citedRevision = "cited_revision"
        case visibleContent = "visible_content"
        case eventTime = "event_time"
    }
}

struct VersionHistoryItem: Codable, Identifiable {
    let version: Int
    let summary: String
    let status: String
    let changeReason: String
    let origin: String
    let createdAt: String

    var id: Int { version }

    enum CodingKeys: String, CodingKey {
        case version
        case summary
        case status
        case changeReason = "change_reason"
        case origin
        case createdAt = "created_at"
    }
}

struct CitedMemoryItem: Codable, Identifiable, Hashable {
    let memoryId: String
    let memoryType: String
    let title: String
    let summary: String
    let uri: String
    let version: Int
    let assertionKind: String

    var id: String { memoryId }

    var typeDisplayName: String {
        switch memoryType {
        case "task": return "任务"
        case "preference": return "偏好"
        case "semantic": return "事实"
        case "episodic": return "经历"
        default: return memoryType
        }
    }

    enum CodingKeys: String, CodingKey {
        case memoryId = "memory_id"
        case memoryType = "memory_type"
        case title
        case summary
        case uri
        case version
        case assertionKind = "assertion_kind"
    }
}

struct CitedNoteItem: Codable, Identifiable, Hashable {
    let noteId: String
    let content: String
    let sourceType: String
    let eventTime: String

    var id: String { noteId }

    enum CodingKeys: String, CodingKey {
        case noteId = "note_id"
        case content
        case sourceType = "source_type"
        case eventTime = "event_time"
    }
}

struct AskCitations: Codable, Hashable {
    let memories: [CitedMemoryItem]?
    let notes: [CitedNoteItem]?
}

enum JSONValue: Codable, Hashable {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)
    case array([JSONValue])
    case object([String: JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let b = try? container.decode(Bool.self) {
            self = .bool(b)
        } else if let i = try? container.decode(Int.self) {
            self = .int(i)
        } else if let d = try? container.decode(Double.self) {
            self = .double(d)
        } else if let s = try? container.decode(String.self) {
            self = .string(s)
        } else if let arr = try? container.decode([JSONValue].self) {
            self = .array(arr)
        } else if let obj = try? container.decode([String: JSONValue].self) {
            self = .object(obj)
        } else {
            self = .null
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let s): try container.encode(s)
        case .int(let i): try container.encode(i)
        case .double(let d): try container.encode(d)
        case .bool(let b): try container.encode(b)
        case .array(let a): try container.encode(a)
        case .object(let o): try container.encode(o)
        case .null: try container.encodeNil()
        }
    }

    var stringValue: String {
        switch self {
        case .string(let s): return s
        case .int(let i): return String(i)
        case .double(let d): return String(d)
        case .bool(let b): return b ? "true" : "false"
        default: return ""
        }
    }

    var stringArrayValue: [String] {
        guard case .array(let arr) = self else { return [] }
        return arr.map(\.stringValue).filter { !$0.isEmpty }
    }

    var rawAny: Any {
        switch self {
        case .string(let s): return s
        case .int(let i): return i
        case .double(let d): return d
        case .bool(let b): return b
        case .array(let a): return a.map(\.rawAny)
        case .object(let o): return o.mapValues(\.rawAny)
        case .null: return NSNull()
        }
    }
}

struct MemoryItemModel: Codable, Identifiable, Hashable {
    let memoryId: String
    let permalink: String
    let uri: String
    let memoryType: String
    let title: String
    let summary: String
    let payload: [String: JSONValue]
    let tags: [String]?
    let assertionKind: String
    let status: String
    let version: Int
    let userLocked: Bool
    let evidences: [EvidenceRefModel]?
    let eventTime: String?
    let updatedAt: String

    var id: String { memoryId }

    var typeDisplayName: String {
        switch memoryType {
        case "task": return "任务"
        case "preference": return "偏好"
        case "semantic": return "语义事实"
        case "episodic": return "情景经历"
        default: return memoryType
        }
    }

    var typeTintColor: Color {
        switch memoryType {
        case "task": return .blue
        case "preference": return .purple
        case "semantic": return .teal
        case "episodic": return .orange
        default: return .secondary
        }
    }

    func stringField(_ key: String, default fallback: String = "") -> String {
        let val = payload[key]?.stringValue ?? ""
        return val.isEmpty ? fallback : val
    }

    func stringListField(_ key: String) -> [String] {
        payload[key]?.stringArrayValue ?? []
    }

    var rawPayloadDict: [String: Any] {
        var out: [String: Any] = [:]
        for (k, v) in payload {
            if case .null = v { continue }
            out[k] = v.rawAny
        }
        return out
    }

    enum CodingKeys: String, CodingKey {
        case memoryId = "memory_id"
        case permalink
        case uri
        case memoryType = "memory_type"
        case title
        case summary
        case payload
        case tags
        case assertionKind = "assertion_kind"
        case status
        case version
        case userLocked = "user_locked"
        case evidences
        case eventTime = "event_time"
        case updatedAt = "updated_at"
    }
}

struct MemorySearchHitModel: Codable, Identifiable {
    let memory: MemoryItemModel
    let rrfScore: Double
    let bm25Rank: Int?
    let vectorRank: Int?
    let vectorScore: Double

    var id: String { memory.memoryId }

    enum CodingKeys: String, CodingKey {
        case memory
        case rrfScore = "rrf_score"
        case bm25Rank = "bm25_rank"
        case vectorRank = "vector_rank"
        case vectorScore = "vector_score"
    }
}

struct MemorySearchResponse: Codable {
    let query: String
    let spaceId: String
    let memories: [MemorySearchHitModel]

    enum CodingKeys: String, CodingKey {
        case query
        case spaceId = "space_id"
        case memories
    }
}

struct MemoryDetailResponse: Codable {
    let memory: MemoryItemModel?
    let sources: [SourceNoteDetail]
    let history: [VersionHistoryItem]
    let markdown: String?
}

struct MemoryProposeResponse: Codable {
    let proposalId: String?
    let status: String
    let memory: MemoryItemModel?
    let message: String?

    enum CodingKeys: String, CodingKey {
        case proposalId = "proposal_id"
        case status
        case memory
        case message
    }
}

struct AskResponse: Codable, Identifiable {
    let question: String
    let queryMode: String
    let answer: String
    let hasSufficientEvidence: Bool
    let citations: AskCitations?
    let traceId: String

    var id: String { traceId }

    enum CodingKeys: String, CodingKey {
        case question
        case queryMode = "query_mode"
        case answer
        case hasSufficientEvidence = "has_sufficient_evidence"
        case citations
        case traceId = "trace_id"
    }
}

struct PendingOfflineNote: Codable, Identifiable {
    let id: String
    let content: String
    let spaceId: String
    let createdAt: Date
}

struct QuotaUsageModel: Codable {
    let notesCount: Int
    let blockedNoiseCount: Int
    let llmCalls: Int
    let embedCalls: Int
    let tokensUsed: Int

    enum CodingKeys: String, CodingKey {
        case notesCount = "notes_count"
        case blockedNoiseCount = "blocked_noise_count"
        case llmCalls = "llm_calls"
        case embedCalls = "embed_calls"
        case tokensUsed = "tokens_used"
    }
}

struct UserAccountModel: Codable, Identifiable {
    let userId: String
    let username: String
    let displayName: String
    let role: String
    let status: String
    let spaceId: String
    let dailyNoteLimit: Int
    let dailyLlmLimit: Int
    let dailyTokenLimit: Int
    let lastLoginAt: String?

    var id: String { userId }
    var isAdmin: Bool { role == "admin" }

    enum CodingKeys: String, CodingKey {
        case userId = "user_id"
        case username
        case displayName = "display_name"
        case role
        case status
        case spaceId = "space_id"
        case dailyNoteLimit = "daily_note_limit"
        case dailyLlmLimit = "daily_llm_limit"
        case dailyTokenLimit = "daily_token_limit"
        case lastLoginAt = "last_login_at"
    }
}

struct AuthStatusResponse: Codable {
    let authRequired: Bool
    let allowRegistration: Bool
    let hasUsers: Bool
    let authenticated: Bool
    let user: UserAccountModel?
    let quota: QuotaUsageModel?

    enum CodingKeys: String, CodingKey {
        case authRequired = "auth_required"
        case allowRegistration = "allow_registration"
        case hasUsers = "has_users"
        case authenticated
        case user
        case quota
    }
}

struct AuthSessionResponse: Codable {
    let token: String
    let expiresAt: String
    let user: UserAccountModel
    let quota: QuotaUsageModel?

    enum CodingKeys: String, CodingKey {
        case token
        case expiresAt = "expires_at"
        case user
        case quota
    }
}

struct NoteRecordResponse: Codable {
    let extractionStatus: String?
    let filtered: Bool?
    let filterReason: String?
    let errorMessage: String?
    let jobId: String?

    enum CodingKeys: String, CodingKey {
        case extractionStatus = "extraction_status"
        case filtered
        case filterReason = "filter_reason"
        case errorMessage = "error_message"
        case jobId = "job_id"
    }
}

struct IngestStatusResponse: Codable {
    let spaceId: String?
    let queued: Int
    let processing: Int
    let pending: Int
    let lastCompletedAt: String?

    enum CodingKeys: String, CodingKey {
        case spaceId = "space_id"
        case queued
        case processing
        case pending
        case lastCompletedAt = "last_completed_at"
    }
}

struct SyncStateResponse: Codable {
    let spaceId: String
    let syncVersion: Int
    let lastReason: String?
    let updatedAt: String?
    let pendingIngest: Int?
    let serverTime: String?
    let beijingDate: String?

    enum CodingKeys: String, CodingKey {
        case spaceId = "space_id"
        case syncVersion = "sync_version"
        case lastReason = "last_reason"
        case updatedAt = "updated_at"
        case pendingIngest = "pending_ingest"
        case serverTime = "server_time"
        case beijingDate = "beijing_date"
    }
}

struct TaskStatusRefreshResponse: Codable {
    let checkedSpaces: Int?
    let rolledCount: Int?
    let refreshedCount: Int?
    let syncVersion: Int?
    let beijingDate: String?

    enum CodingKeys: String, CodingKey {
        case checkedSpaces = "checked_spaces"
        case rolledCount = "rolled_count"
        case refreshedCount = "refreshed_count"
        case syncVersion = "sync_version"
        case beijingDate = "beijing_date"
    }
}

struct SupervisionShareResponse: Codable {
    let shareId: String
    let shareToken: String
    let shareQuery: String?

    enum CodingKeys: String, CodingKey {
        case shareId = "share_id"
        case shareToken = "share_token"
        case shareQuery = "share_query"
    }
}

struct AdminUserItemModel: Codable, Identifiable {
    let userId: String
    let username: String
    let displayName: String
    let role: String
    let status: String
    let spaceId: String
    let dailyNoteLimit: Int
    let dailyLlmLimit: Int
    let dailyTokenLimit: Int
    let todayUsage: QuotaUsageModel?
    let counts: AdminUserCountsModel?

    var id: String { userId }

    enum CodingKeys: String, CodingKey {
        case userId = "user_id"
        case username
        case displayName = "display_name"
        case role
        case status
        case spaceId = "space_id"
        case dailyNoteLimit = "daily_note_limit"
        case dailyLlmLimit = "daily_llm_limit"
        case dailyTokenLimit = "daily_token_limit"
        case todayUsage = "today_usage"
        case counts
    }
}

struct AdminUserCountsModel: Codable {
    let notesTotal: Int
    let memoriesTotal: Int
    let openTasks: Int

    enum CodingKeys: String, CodingKey {
        case notesTotal = "notes_total"
        case memoriesTotal = "memories_total"
        case openTasks = "open_tasks"
    }
}

struct AdminSettingsModel: Codable {
    var allowRegistration: Bool
    var noiseFilterEnabled: Bool
    var maxNoteChars: Int
    var minMeaningfulChars: Int
    var rateLimitPerMinute: Int
    var duplicateWindowSeconds: Int
    var defaultDailyNoteLimit: Int
    var defaultDailyLlmLimit: Int
    var defaultDailyTokenLimit: Int

    enum CodingKeys: String, CodingKey {
        case allowRegistration = "allow_registration"
        case noiseFilterEnabled = "noise_filter_enabled"
        case maxNoteChars = "max_note_chars"
        case minMeaningfulChars = "min_meaningful_chars"
        case rateLimitPerMinute = "rate_limit_per_minute"
        case duplicateWindowSeconds = "duplicate_window_seconds"
        case defaultDailyNoteLimit = "default_daily_note_limit"
        case defaultDailyLlmLimit = "default_daily_llm_limit"
        case defaultDailyTokenLimit = "default_daily_token_limit"
    }
}

struct AdminOverviewResponse: Codable {
    let today: String
    let users: AdminUsersSummary
    let content: AdminContentSummary
    let todayUsage: QuotaUsageModel
    let settings: AdminSettingsModel

    enum CodingKeys: String, CodingKey {
        case today
        case users
        case content
        case todayUsage = "today_usage"
        case settings
    }
}

struct AdminUsersSummary: Codable {
    let total: Int
    let active: Int
    let disabled: Int
}

struct AdminContentSummary: Codable {
    let rawNotesTotal: Int
    let activeMemories: Int
    let openTasks: Int
    let doneTasks: Int

    enum CodingKeys: String, CodingKey {
        case rawNotesTotal = "raw_notes_total"
        case activeMemories = "active_memories"
        case openTasks = "open_tasks"
        case doneTasks = "done_tasks"
    }
}

struct AdminUsersResponse: Codable {
    let users: [AdminUserItemModel]
}
