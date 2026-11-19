import Foundation
import SwiftUI
#if os(iOS)
import WidgetKit
#endif

@MainActor
final class TodoStore: ObservableObject {
    @Published var serverURL: String {
        didSet { UserDefaults.standard.set(serverURL, forKey: "tm_server_url") }
    }
    @Published var spaceId: String {
        didSet { UserDefaults.standard.set(spaceId, forKey: "tm_space_id") }
    }
    @Published var authToken: String {
        didSet { UserDefaults.standard.set(authToken, forKey: "tm_auth_token") }
    }
    @Published var sortBy: TaskSortOption = .priority
    @Published var includeDone: Bool = false

    @Published private(set) var matrix: MatrixResponse?
    @Published private(set) var supervisionBoard: SupervisionBoardResponse?
    @Published private(set) var pendingNotes: [PendingOfflineNote] = []
    @Published private(set) var isOnline: Bool = false
    @Published private(set) var engineBadge: String = "未连接"
    @Published private(set) var lastConnectionError: String?
    @Published private(set) var isBusy: Bool = false
    @Published var statusBanner: String?

    @Published private(set) var authRequired: Bool = false
    @Published private(set) var allowRegistration: Bool = true
    @Published private(set) var hasUsers: Bool = true
    @Published private(set) var currentUser: UserAccountModel?
    @Published private(set) var currentQuota: QuotaUsageModel?
    @Published var isPresentingAuthSheet: Bool = false

    @Published var isPresentingNewTaskSheet: Bool = false
    @Published var editingTaskTarget: TaskItemModel?
    @Published var activeFocusTask: TaskItemModel?

    static var defaultServerURL: String {
        "http://127.0.0.1:18090"
    }

    init() {
        let savedURL = (UserDefaults.standard.string(forKey: "tm_server_url") ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if savedURL.isEmpty {
            self.serverURL = Self.defaultServerURL
            UserDefaults.standard.set(Self.defaultServerURL, forKey: "tm_server_url")
        } else {
            self.serverURL = savedURL
        }
        self.spaceId = UserDefaults.standard.string(forKey: "tm_space_id") ?? "default"
        self.authToken = UserDefaults.standard.string(forKey: "tm_auth_token") ?? ""
        loadOfflineState()
    }

    var isAuthenticated: Bool {
        currentUser != nil && !authToken.isEmpty
    }

    var needsLoginGate: Bool {
        authRequired && !isAuthenticated
    }

    var client: TraceMemoryAPIClient {
        let effSpace = (currentUser != nil && currentUser?.isAdmin == false)
            ? (currentUser?.spaceId ?? spaceId)
            : spaceId
        return TraceMemoryAPIClient(baseURL: serverURL, spaceId: effSpace, authToken: authToken)
    }

    var q1Count: Int { matrix?.summaryCounts.q1 ?? 0 }
    var totalTodoCount: Int { matrix?.summaryCounts.totalTodo ?? 0 }

    func tasks(for quadrant: QuadrantKind) -> [TaskItemModel] {
        matrix?.quadrants[quadrant.rawValue]?.tasks ?? []
    }

    private var cacheDir: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        let dir = base.appendingPathComponent("TraceTodo", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    private var matrixCacheFile: URL {
        cacheDir.appendingPathComponent("matrix_\(spaceId).json")
    }

    private var pendingQueueFile: URL {
        cacheDir.appendingPathComponent("pending_notes.json")
    }

    private func loadOfflineState() {
        if let data = try? Data(contentsOf: matrixCacheFile),
           let cached = try? JSONDecoder().decode(MatrixResponse.self, from: data) {
            self.matrix = cached
        }
        if let qData = try? Data(contentsOf: pendingQueueFile),
           let queued = try? JSONDecoder().decode([PendingOfflineNote].self, from: qData) {
            self.pendingNotes = queued
        }
    }

    private func saveMatrixCache(_ resp: MatrixResponse) {
        if let data = try? JSONEncoder().encode(resp) {
            try? data.write(to: matrixCacheFile)
        }
    }

    private func savePendingQueue() {
        if let data = try? JSONEncoder().encode(pendingNotes) {
            try? data.write(to: pendingQueueFile)
        }
    }

    func refresh() async {
        loadOfflineState()
        do {
            let authStatus = try await client.fetchAuthStatus()
            self.authRequired = authStatus.authRequired
            self.allowRegistration = authStatus.allowRegistration
            self.hasUsers = authStatus.hasUsers
            if authStatus.authenticated, let u = authStatus.user {
                self.currentUser = u
                self.currentQuota = authStatus.quota
                if !u.isAdmin || self.spaceId == "default" {
                    self.spaceId = u.spaceId
                }
            } else {
                if !authToken.isEmpty {
                    self.authToken = ""
                }
                self.currentUser = nil
                self.currentQuota = nil
            }

            if self.needsLoginGate {
                self.isOnline = true
                self.engineBadge = "待登录"
                self.lastConnectionError = nil
                return
            }

            await flushOfflineQueueIfNeeded()
            let health = try await client.checkHealth()
            let fresh = try await client.fetchMatrix(sortBy: sortBy, includeDone: includeDone)
            self.matrix = fresh
            self.isOnline = true
            self.lastConnectionError = nil
            let llm = (health["llm_provider"] as? String) ?? "llm"
            self.engineBadge = "在线 · \(llm)"
            saveMatrixCache(fresh)
            #if os(iOS)
            WidgetCenter.shared.reloadAllTimelines()
            #endif
            NotificationManager.shared.syncReminders(for: fresh.sortedTasks)
            if let currentFocus = activeFocusTask,
               let updatedFocus = fresh.sortedTasks.first(where: { $0.memoryId == currentFocus.memoryId }) {
                self.activeFocusTask = updatedFocus
            }
        } catch let apiErr as APIError where apiErr.statusCode == 401 {
            self.authToken = ""
            self.currentUser = nil
            self.isOnline = true
            self.engineBadge = "待登录"
            self.lastConnectionError = apiErr.localizedDescription
            self.isPresentingAuthSheet = true
        } catch {
            self.isOnline = false
            self.engineBadge = "离线缓存模式"
            self.lastConnectionError = error.localizedDescription
        }
    }

    func login(username: String, password: String) async throws {
        let sess = try await client.login(username: username, password: password)
        self.authToken = sess.token
        self.currentUser = sess.user
        self.currentQuota = sess.quota
        self.spaceId = sess.user.spaceId
        self.isPresentingAuthSheet = false
        showBanner("欢迎回来，\(sess.user.displayName)")
        await refresh()
    }

    func register(username: String, password: String, displayName: String) async throws {
        let sess = try await client.register(username: username, password: password, displayName: displayName)
        self.authToken = sess.token
        self.currentUser = sess.user
        self.currentQuota = sess.quota
        self.spaceId = sess.user.spaceId
        self.isPresentingAuthSheet = false
        showBanner("注册并登录成功")
        await refresh()
    }

    func logout() async {
        try? await client.logout()
        self.authToken = ""
        self.currentUser = nil
        self.currentQuota = nil
        showBanner("已退出登录")
        await refresh()
    }

    func flushOfflineQueueIfNeeded() async {
        guard !pendingNotes.isEmpty else { return }
        if needsLoginGate { return }
        var remaining: [PendingOfflineNote] = []
        var syncedCount = 0
        for item in pendingNotes {
            let itemClient = TraceMemoryAPIClient(baseURL: serverURL, spaceId: item.spaceId, authToken: authToken)
            do {
                _ = try await itemClient.recordNote(content: item.content, idempotencyKey: "offline:\(item.id)")
                syncedCount += 1
            } catch let apiErr as APIError {
                // Drop rejected validation/quota errors cleanly instead of looping forever unless auth is missing
                if apiErr.statusCode == 401 || apiErr.statusCode == 429 {
                    remaining.append(item)
                }
            } catch {
                remaining.append(item)
            }
        }
        pendingNotes = remaining
        savePendingQueue()
        if syncedCount > 0 {
            showBanner("已自动同步 \(syncedCount) 条离线待办")
        }
    }

    func submitQuickNote(_ rawText: String) async {
        let text = rawText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        isBusy = true
        defer { isBusy = false }

        let key = "app:\(UUID().uuidString)"
        do {
            let res = try await client.recordNote(content: text, idempotencyKey: key)
            if res.filtered == true || res.extractionStatus == "filtered_noise" {
                let reason = res.filterReason ?? res.errorMessage ?? "无效或水话输入"
                showBanner("🛡️ 已拦截：\(reason)（0 Token 消耗）")
                return
            }
            showBanner("已通过 AI 抽取并更新四象限")
            await refresh()
        } catch let apiErr as APIError {
            if apiErr.statusCode == 401 {
                self.isPresentingAuthSheet = true
            }
            showBanner("⚠️ \(apiErr.localizedDescription)")
        } catch {
            let queued = PendingOfflineNote(
                id: UUID().uuidString,
                content: text,
                spaceId: spaceId,
                createdAt: Date()
            )
            pendingNotes.append(queued)
            savePendingQueue()
            isOnline = false
            engineBadge = "离线缓存模式"
            showBanner("服务端未连接：已存入本地离线队列 (\(pendingNotes.count) 条)")
        }
    }

    func saveTaskCard(payloadBody: [String: Any], isEdit: Bool) async -> Bool {
        isBusy = true
        defer { isBusy = false }
        do {
            try await client.saveTask(body: payloadBody)
            if payloadBody["reminder_offset_minutes"] != nil {
                NotificationManager.shared.requestPermissionIfNeeded()
            }
            showBanner(isEdit ? "任务卡片已保存" : "新任务已创建并排入四象限")
            await refresh()
            return true
        } catch {
            showBanner("保存失败：\(error.localizedDescription)")
            return false
        }
    }

    func toggleSubtask(_ task: TaskItemModel, subtask: SubtaskItem) async {
        do {
            let res = try await client.toggleSubtask(
                memoryId: task.memoryId,
                subtaskId: subtask.id,
                done: !subtask.done,
                expectedVersion: task.version
            )
            if res.allSubtasksDone {
                showBanner("🎉 所有子步骤已完成，可直接点击完成主任务")
            }
            await refresh()
        } catch {
            showBanner("更新子步骤失败：\(error.localizedDescription)")
        }
    }

    func transitionTask(_ task: TaskItemModel, action: String) async {
        do {
            let res = try await client.transitionTask(
                memoryId: task.memoryId,
                action: action,
                expectedVersion: task.version
            )
            if let next = res.nextTask, let nextDue = next.payload.dueAt {
                showBanner("✅ 本期已完成，已生成下一期 (\(String(nextDue.prefix(10))))")
            }
            await refresh()
            await refreshSupervisionBoard()
        } catch {
            showBanner("状态更新失败：\(error.localizedDescription)")
        }
    }

    func moveTaskQuadrant(_ task: TaskItemModel, to quadrant: QuadrantKind) async {
        do {
            try await client.updateQuadrant(
                memoryId: task.memoryId,
                expectedVersion: task.version,
                quadrant: quadrant
            )
            showBanner("已移至 \(quadrant.shortCode) 并加锁保护")
            await refresh()
        } catch {
            showBanner("象限切换失败：\(error.localizedDescription)")
        }
    }

    func completeFocusSession(_ task: TaskItemModel, markTaskDone: Bool) async {
        do {
            try await client.recordFocusEvent(
                memoryId: task.memoryId,
                event: "complete_session",
                expectedVersion: task.version
            )
            if markTaskDone {
                await refresh()
                if let latest = matrix?.sortedTasks.first(where: { $0.memoryId == task.memoryId }) {
                    await transitionTask(latest, action: "complete_task")
                }
            } else {
                showBanner("🎉 已记录一轮深度专注")
                await refresh()
            }
            activeFocusTask = nil
        } catch {
            showBanner("记录专注失败：\(error.localizedDescription)")
        }
    }

    func abortFocusSession(_ task: TaskItemModel, reason: String, elapsedSeconds: Int) async {
        do {
            try await client.recordFocusEvent(
                memoryId: task.memoryId,
                event: "abort_session",
                reason: reason,
                elapsedSeconds: elapsedSeconds,
                expectedVersion: task.version
            )
            showBanner("已记录中断原因：「\(reason)」")
            activeFocusTask = nil
            await refresh()
        } catch {
            activeFocusTask = nil
            showBanner("记录中断失败：\(error.localizedDescription)")
        }
    }

    func refreshSupervisionBoard(assignee: String = "") async {
        do {
            supervisionBoard = try await client.fetchSupervisionBoard(assignee: assignee)
        } catch {
            // Keep cached board if temporarily offline
        }
    }

    func sendNudge(to task: TaskItemModel, kind: String, fromUser: String) async {
        do {
            try await client.sendNudge(memoryId: task.memoryId, kind: kind, fromUser: fromUser)
            showBanner(kind == "nudge" ? "⚡ 已发送催一催提醒" : "👍 已发送点赞鼓励")
            await refreshSupervisionBoard()
            await refresh()
        } catch {
            showBanner("互动失败：\(error.localizedDescription)")
        }
    }

    func seedDemo() async {
        isBusy = true
        defer { isBusy = false }
        do {
            try await client.seedDemoData()
            showBanner("已载入多象限演示数据")
            await refresh()
        } catch {
            showBanner("载入失败，请确认 trace-memory 服务已启动")
        }
    }

    func showBanner(_ message: String) {
        statusBanner = message
        Task {
            try? await Task.sleep(nanoseconds: 3_400_000_000)
            if statusBanner == message {
                statusBanner = nil
            }
        }
    }
}
