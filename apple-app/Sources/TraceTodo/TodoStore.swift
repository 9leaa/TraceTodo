import Foundation
import SwiftUI
#if os(iOS)
import WidgetKit
#endif

@MainActor
final class TodoStore: ObservableObject {
    @Published var serverURL: String {
        didSet {
            UserDefaults.standard.set(serverURL, forKey: "tm_server_url")
            syncSharedWidgetConfig()
        }
    }
    @Published var spaceId: String {
        didSet {
            UserDefaults.standard.set(spaceId, forKey: "tm_space_id")
            syncSharedWidgetConfig()
        }
    }
    @Published var authToken: String {
        didSet {
            UserDefaults.standard.set(authToken, forKey: "tm_auth_token")
            syncSharedWidgetConfig()
        }
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
    @Published private(set) var pendingIngestCount: Int = 0
    @Published private(set) var optimisticIngestItems: [OptimisticIngestItem] = []
    @Published private(set) var lastKnownSyncVersion: Int = -1
    @Published private(set) var lastKnownBeijingDate: String = ""
    @Published var statusBanner: String?
    private var ingestPollTask: Task<Void, Never>?
    private var autoSyncTask: Task<Void, Never>?
    private var sseStreamTask: Task<Void, Never>?
    private var watchedIngestNoteIds: Set<String> = []
    private var lastIngestCompletedAt: String = ""

    @Published private(set) var authRequired: Bool = false
    @Published private(set) var allowRegistration: Bool = true
    @Published private(set) var hasUsers: Bool = true
    @Published private(set) var currentUser: UserAccountModel?
    @Published private(set) var currentQuota: QuotaUsageModel?
    @Published var isPresentingAuthSheet: Bool = false

    @Published var isPresentingNewTaskSheet: Bool = false
    @Published var isPresentingSettingsSheet: Bool = false
    @Published var isPresentingSupervisionSheet: Bool = false
    @Published var editingTaskTarget: TaskItemModel?
    @Published var activeFocusTask: TaskItemModel?
    @Published var activeFocusLaunch: ActiveFocusLaunchConfig?
    @Published private(set) var focusOverview: FocusOverviewResponse?
    @Published private(set) var countdownList: CountdownListResponse?

    @Published var activeTheme: AppThemeKind = .classic
    @Published var enabledModules: [String: Bool] = [
        "calendar": true,
        "focus": true,
        "notes": true,
        "journal": true,
        "countdown": true,
        "supervision": true
    ]
    @Published var defaultPomodoroMinutes: Int = 25

    static var defaultServerURL: String {
        SharedWidgetBridge.cloudServerURL
    }

    static var prodCloudServerURL: String {
        SharedWidgetBridge.prodCloudServerURL
    }

    static var testCloudServerURL: String {
        SharedWidgetBridge.testCloudServerURL
    }

    static var localServerURL: String {
        SharedWidgetBridge.localServerURL
    }

    init() {
        let savedURL = (UserDefaults.standard.string(forKey: "tm_server_url") ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let userExplicitlyPickedEnv = UserDefaults.standard.bool(forKey: "tm_env_user_selected")
        if savedURL.isEmpty
            || (!userExplicitlyPickedEnv && (savedURL == Self.localServerURL || (!SharedWidgetBridge.isTestBuild && savedURL == SharedWidgetBridge.prodCloudDirectIPURL))) {
            // Default to online HTTPS production environment unless user explicitly selected local/custom environment
            self.serverURL = Self.defaultServerURL
            UserDefaults.standard.set(Self.defaultServerURL, forKey: "tm_server_url")
        } else {
            self.serverURL = savedURL
        }
        self.spaceId = UserDefaults.standard.string(forKey: "tm_space_id") ?? "default"
        self.authToken = UserDefaults.standard.string(forKey: "tm_auth_token") ?? ""
        if let savedTheme = UserDefaults.standard.string(forKey: "tm_theme_id"),
           let parsedTheme = AppThemeKind(rawValue: savedTheme) {
            self.activeTheme = parsedTheme
        }
        if let savedMods = UserDefaults.standard.dictionary(forKey: "tm_enabled_modules") as? [String: Bool] {
            for (k, v) in savedMods {
                self.enabledModules[k] = v
            }
        }
        let savedPom = UserDefaults.standard.integer(forKey: "tm_pomodoro_minutes")
        if savedPom > 0 {
            self.defaultPomodoroMinutes = savedPom
        }
        syncSharedWidgetConfig()
        loadOfflineState()
    }

    func switchServerURL(_ newURL: String) {
        let clean = newURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        UserDefaults.standard.set(true, forKey: "tm_env_user_selected")
        self.serverURL = clean
        self.lastKnownSyncVersion = -1
        sseStreamTask?.cancel()
        sseStreamTask = nil
        startSyncStreamIfNeeded()
    }

    func syncSharedWidgetConfig() {
        let effSpace = (currentUser != nil && currentUser?.isAdmin == false)
            ? (currentUser?.spaceId ?? spaceId)
            : spaceId
        SharedWidgetBridge.saveConfig(
            serverURL: serverURL,
            spaceId: effSpace,
            authToken: authToken
        )
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
        let folder = SharedWidgetBridge.isTestBuild ? "TraceTodo-Test" : "TraceTodo"
        let dir = base.appendingPathComponent(folder, isDirectory: true)
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
            SharedWidgetBridge.saveMatrixSnapshotData(data)
        }
    }

    private func savePendingQueue() {
        if let data = try? JSONEncoder().encode(pendingNotes) {
            try? data.write(to: pendingQueueFile)
        }
    }

    func refreshTasksFast() async {
        guard !needsLoginGate else { return }
        do {
            let fresh = try await client.fetchMatrix(sortBy: sortBy, includeDone: includeDone)
            self.matrix = fresh
            self.isOnline = true
            self.lastConnectionError = nil
            saveMatrixCache(fresh)
            #if os(iOS)
            WidgetCenter.shared.reloadAllTimelines()
            #endif
            NotificationManager.shared.syncReminders(for: fresh.sortedTasks)
            if let currentFocus = activeFocusTask,
               let updatedFocus = fresh.sortedTasks.first(where: { $0.memoryId == currentFocus.memoryId }) {
                self.activeFocusTask = updatedFocus
            }
        } catch {
            // Preserve current cached matrix on transient failure
        }
    }

    func refresh() async {
        loadOfflineState()
        do {
            let explicitBootSpace: String? = (currentUser != nil && currentUser?.isAdmin == true && spaceId != "default") ? spaceId : nil
            let boot = try await client.fetchBootstrap(explicitSpaceId: explicitBootSpace)
            if let authStatus = boot.auth {
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
            }

            if self.needsLoginGate {
                self.isOnline = true
                self.engineBadge = "待登录"
                self.lastConnectionError = nil
                return
            }

            if let prefs = boot.preferences {
                applyPreferencesResponse(prefs)
            } else {
                await refreshPreferences()
            }
            if let syncSt = boot.syncState {
                self.lastKnownSyncVersion = syncSt.syncVersion
                self.pendingIngestCount = syncSt.pendingIngest ?? 0
                if let doneAt = syncSt.lastCompletedAt, !doneAt.isEmpty {
                    self.lastIngestCompletedAt = doneAt
                }
                if let bjDate = syncSt.beijingDate, !bjDate.isEmpty {
                    self.lastKnownBeijingDate = bjDate
                }
            }

            await flushOfflineQueueIfNeeded()
            async let matrixTask = client.fetchMatrix(sortBy: sortBy, includeDone: includeDone)
            async let focusTask: FocusOverviewResponse? = try? client.fetchFocusOverview(limit: 60)
            async let countdownTask: CountdownListResponse? = try? client.fetchCountdowns()

            let fresh = try await matrixTask
            let (foc, cd) = await (focusTask, countdownTask)

            self.matrix = fresh
            if let foc { self.focusOverview = foc }
            if let cd {
                self.countdownList = cd
                if let encoded = try? JSONEncoder().encode(cd) {
                    SharedWidgetBridge.saveCountdownsSnapshotData(encoded)
                }
            }
            self.isOnline = true
            self.lastConnectionError = nil
            let llm = boot.health?.llmProvider ?? "llm"
            self.engineBadge = "在线 · \(llm)"
            saveMatrixCache(fresh)
            syncSharedWidgetConfig()
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
        sseStreamTask?.cancel()
        sseStreamTask = nil
        showBanner("欢迎回来，\(sess.user.displayName)")
        await refresh()
        startSyncStreamIfNeeded()
    }

    func register(username: String, password: String, displayName: String) async throws {
        let sess = try await client.register(username: username, password: password, displayName: displayName)
        self.authToken = sess.token
        self.currentUser = sess.user
        self.currentQuota = sess.quota
        self.spaceId = sess.user.spaceId
        self.isPresentingAuthSheet = false
        sseStreamTask?.cancel()
        sseStreamTask = nil
        showBanner("注册并登录成功")
        await refresh()
        startSyncStreamIfNeeded()
    }

    func logout() async {
        sseStreamTask?.cancel()
        sseStreamTask = nil
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

    func startAutoSyncLoop() {
        startSyncStreamIfNeeded()
        guard autoSyncTask == nil else { return }
        autoSyncTask = Task { [weak self] in
            guard let self else { return }
            defer { self.autoSyncTask = nil }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 15_000_000_000)
                if Task.isCancelled { break }
                if self.sseStreamTask == nil {
                    self.startSyncStreamIfNeeded()
                }
                await self.syncIfChanged()
            }
        }
    }

    func stopAutoSyncLoop() {
        autoSyncTask?.cancel()
        autoSyncTask = nil
        sseStreamTask?.cancel()
        sseStreamTask = nil
    }

    private func startSyncStreamIfNeeded() {
        guard sseStreamTask == nil, !needsLoginGate else { return }
        sseStreamTask = Task { [weak self] in
            guard let self else { return }
            defer { self.sseStreamTask = nil }
            while !Task.isCancelled && !self.needsLoginGate {
                do {
                    try await self.client.streamSyncEvents(sinceVersion: max(0, self.lastKnownSyncVersion)) { [weak self] evName, st in
                        guard let self else { return }
                        await self.handleRealtimeSyncState(st, isInitialSnapshot: evName == "sync_state")
                    }
                } catch {
                    if Task.isCancelled { break }
                    try? await Task.sleep(nanoseconds: 2_500_000_000)
                }
            }
        }
    }

    private func reconcileRecentJobs(_ jobs: [RecentIngestJobModel]?, pending: Int) {
        if let jobs, !jobs.isEmpty {
            var settledNotes = Set<String>()
            var settledJobs = Set<String>()
            for j in jobs {
                if j.status == "completed" || j.status == "failed" || j.status == "cancelled" {
                    settledNotes.insert(j.noteId)
                    settledJobs.insert(j.jobId)
                    if watchedIngestNoteIds.remove(j.noteId) != nil {
                        if j.status == "completed" && (j.changesCount ?? 1) == 0 {
                            showBanner("🤔 AI 未识别出待办或记忆，原话已保存在「随记」中")
                        } else if j.status == "failed" {
                            showBanner("⚠️ AI 整理失败：\(j.errorMessage ?? "请稍后重试")")
                        }
                    }
                }
            }
            if !settledNotes.isEmpty || !settledJobs.isEmpty {
                withAnimation(.easeOut(duration: 0.18)) {
                    optimisticIngestItems.removeAll { item in
                        (item.noteId != nil && settledNotes.contains(item.noteId!))
                            || (item.jobId != nil && settledJobs.contains(item.jobId!))
                    }
                }
            }
        }
        if pending <= 0 && !optimisticIngestItems.isEmpty {
            let now = Date()
            withAnimation(.easeOut(duration: 0.18)) {
                optimisticIngestItems.removeAll { $0.ackReceived || now.timeIntervalSince($0.createdAt) > 15.0 }
            }
        }
    }

    private func handleRealtimeSyncState(_ st: SyncStateResponse, isInitialSnapshot: Bool) async {
        let newVer = st.syncVersion
        let bjDate = st.beijingDate ?? ""
        let pending = st.pendingIngest ?? 0
        self.pendingIngestCount = pending
        reconcileRecentJobs(st.recentJobs, pending: pending)

        let doneAt = st.lastCompletedAt ?? ""
        let completedAdvanced = !doneAt.isEmpty && !lastIngestCompletedAt.isEmpty && doneAt != lastIngestCompletedAt
        if !doneAt.isEmpty {
            lastIngestCompletedAt = doneAt
        }

        let versionAdvanced = !isInitialSnapshot && lastKnownSyncVersion >= 0 && newVer > lastKnownSyncVersion
        let dayRolled = !isInitialSnapshot && !lastKnownBeijingDate.isEmpty && !bjDate.isEmpty && bjDate != lastKnownBeijingDate

        if lastKnownSyncVersion < 0 || newVer > lastKnownSyncVersion {
            lastKnownSyncVersion = newVer
        }
        if !bjDate.isEmpty {
            lastKnownBeijingDate = bjDate
        }

        if dayRolled && !isPresentingNewTaskSheet && editingTaskTarget == nil {
            await refresh()
            showBanner("🌅 已进入新的一天（\(bjDate)），自动刷新任务到期状态")
            return
        }

        if completedAdvanced || versionAdvanced {
            let prevIds = Set((matrix?.sortedTasks ?? []).map(\.memoryId))
            await refreshTasksFast()
            let reason = st.lastReason ?? ""
            if versionAdvanced && reason != "note_queued" && watchedIngestNoteIds.isEmpty && optimisticIngestItems.isEmpty {
                let newlyArrived = (matrix?.sortedTasks ?? []).filter { !$0.payload.isDone && !prevIds.contains($0.memoryId) }
                if !newlyArrived.isEmpty {
                    NotificationManager.shared.notifyRemoteTasksArrived(newlyArrived)
                    showBanner("🔄 已自动同步跨端新任务：\(newlyArrived[0].title)")
                }
            }
        }
    }

    func syncIfChanged() async {
        if needsLoginGate || isPresentingNewTaskSheet || editingTaskTarget != nil {
            return
        }
        do {
            let st = try await client.fetchSyncState()
            await handleRealtimeSyncState(st, isInitialSnapshot: false)
        } catch {
            // Silent ignore on transient polling errors
        }
    }

    func triggerDailyStatusCheck() async {
        do {
            let rep = try await client.refreshTaskStatuses()
            let rolled = rep.rolledCount ?? 0
            let refreshed = rep.refreshedCount ?? 0
            showBanner("🔄 到期状态检查完成：顺延 \(rolled) 项，提权刷新 \(refreshed) 项")
            await refresh()
        } catch {
            showBanner("到期检查失败：\(error.localizedDescription)")
        }
    }

    func startIngestPollingIfNeeded() {
        guard ingestPollTask == nil else { return }
        ingestPollTask = Task { [weak self] in
            guard let self else { return }
            defer { self.ingestPollTask = nil }
            for attempt in 0..<40 {
                if Task.isCancelled { break }
                if attempt > 0 {
                    try? await Task.sleep(nanoseconds: 220_000_000)
                }
                if self.needsLoginGate { break }
                do {
                    let st = try await self.client.fetchIngestStatus(
                        waitMs: 4500,
                        sinceVersion: self.lastKnownSyncVersion
                    )
                    self.pendingIngestCount = st.pending
                    if let sv = st.syncVersion, sv > self.lastKnownSyncVersion {
                        self.lastKnownSyncVersion = sv
                    }
                    self.reconcileRecentJobs(st.recentJobs, pending: st.pending)
                    let doneAt = st.lastCompletedAt ?? ""
                    let doneAdvanced = !doneAt.isEmpty && !self.lastIngestCompletedAt.isEmpty && doneAt != self.lastIngestCompletedAt
                    if !doneAt.isEmpty {
                        self.lastIngestCompletedAt = doneAt
                    }
                    if doneAdvanced {
                        await self.refreshTasksFast()
                    }
                    if st.pending == 0 {
                        await self.refreshTasksFast()
                        break
                    }
                } catch {
                    try? await Task.sleep(nanoseconds: 600_000_000)
                }
            }
            self.pendingIngestCount = 0
            self.reconcileRecentJobs(nil, pending: 0)
        }
    }

    func submitQuickNote(_ rawText: String, mediaIds: [String] = []) async {
        let text = rawText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty || !mediaIds.isEmpty else { return }

        let optId = UUID().uuidString
        let displayLabel = text.isEmpty ? "[图片记录 × \(mediaIds.count)]" : text
        let optItem = OptimisticIngestItem(
            id: optId,
            text: displayLabel,
            noteId: nil,
            jobId: nil,
            ackReceived: false,
            createdAt: Date()
        )
        withAnimation(.easeOut(duration: 0.18)) {
            optimisticIngestItems.insert(optItem, at: 0)
        }

        let key = "app:\(optId)"
        do {
            let res = try await client.recordNote(content: text, idempotencyKey: key, asyncMode: true, mediaIds: mediaIds)
            if res.filtered == true || res.extractionStatus == "filtered_noise" {
                withAnimation(.easeOut(duration: 0.15)) {
                    optimisticIngestItems.removeAll { $0.id == optId }
                }
                let reason = res.filterReason ?? res.errorMessage ?? "无效或水话输入"
                showBanner("🛡️ 已拦截：\(reason)（0 Token 消耗）")
                return
            }
            if res.extractionStatus == "queued" {
                if let idx = optimisticIngestItems.firstIndex(where: { $0.id == optId }) {
                    optimisticIngestItems[idx].noteId = res.note?.noteId
                    optimisticIngestItems[idx].jobId = res.jobId
                    optimisticIngestItems[idx].ackReceived = true
                }
                if let nid = res.note?.noteId, !nid.isEmpty {
                    watchedIngestNoteIds.insert(nid)
                }
                pendingIngestCount = max(1, pendingIngestCount + 1)
                startSyncStreamIfNeeded()
                startIngestPollingIfNeeded()
                return
            }
            withAnimation(.easeOut(duration: 0.15)) {
                optimisticIngestItems.removeAll { $0.id == optId }
            }
            showBanner("已通过 AI 抽取并更新四象限")
            await refreshTasksFast()
        } catch let apiErr as APIError {
            withAnimation(.easeOut(duration: 0.15)) {
                optimisticIngestItems.removeAll { $0.id == optId }
            }
            if apiErr.statusCode == 401 {
                self.isPresentingAuthSheet = true
            }
            showBanner("⚠️ \(apiErr.localizedDescription)")
        } catch {
            withAnimation(.easeOut(duration: 0.15)) {
                optimisticIngestItems.removeAll { $0.id == optId }
            }
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

    func refreshFocusOverview() async {
        if let ov = try? await client.fetchFocusOverview(limit: 60) {
            self.focusOverview = ov
        }
    }

    func recordUnifiedFocusSession(
        mode: FocusTimerMode,
        status: String,
        plannedMinutes: Int,
        durationSeconds: Int,
        task: TaskItemModel?,
        customTitle: String,
        abortReason: String = "",
        completeTask: Bool = false,
        startedAtIso: String = ""
    ) async {
        var body: [String: Any] = [
            "mode": mode.rawValue,
            "status": status,
            "planned_minutes": plannedMinutes,
            "duration_seconds": max(1, durationSeconds),
            "task_memory_id": task?.memoryId ?? "",
            "task_title": task?.title ?? (customTitle.isEmpty ? "自由专注" : customTitle),
            "abort_reason": abortReason,
            "complete_task": completeTask
        ]
        if let v = task?.version {
            body["expected_version"] = v
        }
        if !startedAtIso.isEmpty {
            body["started_at"] = startedAtIso
        }
        do {
            try await client.recordFocusSession(body: body)
            if status == "aborted" {
                showBanner("已记录中断原因：「\(abortReason)」")
            } else {
                showBanner(mode == .stopwatch ? "🎉 正计时专注已记录" : "🍅 已完成一轮番茄专注")
            }
        } catch {
            showBanner("记录专注失败：\(error.localizedDescription)")
        }
        activeFocusTask = nil
        activeFocusLaunch = nil
        await refresh()
        await refreshFocusOverview()
    }

    func removeFocusSession(_ sessionId: String) async {
        do {
            try await client.deleteFocusSession(sessionId: sessionId)
            await refreshFocusOverview()
        } catch {
            showBanner("删除记录失败：\(error.localizedDescription)")
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

    func isModuleEnabled(_ key: ModuleToggleKey) -> Bool {
        enabledModules[key.rawValue] ?? true
    }

    func refreshPreferences() async {
        guard let prefs = try? await client.fetchPreferences() else { return }
        applyPreferencesResponse(prefs)
    }

    func refreshCountdowns() async {
        guard let cd = try? await client.fetchCountdowns() else { return }
        self.countdownList = cd
        if let encoded = try? JSONEncoder().encode(cd) {
            SharedWidgetBridge.saveCountdownsSnapshotData(encoded)
        }
        #if os(iOS)
        WidgetCenter.shared.reloadAllTimelines()
        #endif
    }

    private func applyPreferencesResponse(_ prefs: SpacePreferencesResponse) {
        if let th = AppThemeKind(rawValue: prefs.themeId) {
            self.activeTheme = th
            UserDefaults.standard.set(th.rawValue, forKey: "tm_theme_id")
        }
        for (k, v) in prefs.enabledModules {
            self.enabledModules[k] = v
        }
        UserDefaults.standard.set(self.enabledModules, forKey: "tm_enabled_modules")
        if let pm = prefs.pomodoroMinutes, pm > 0 {
            self.defaultPomodoroMinutes = pm
            UserDefaults.standard.set(pm, forKey: "tm_pomodoro_minutes")
        }
    }

    func selectTheme(_ theme: AppThemeKind) async {
        self.activeTheme = theme
        UserDefaults.standard.set(theme.rawValue, forKey: "tm_theme_id")
        if let updated = try? await client.updatePreferences(body: ["theme_id": theme.rawValue]) {
            applyPreferencesResponse(updated)
            showBanner("🎨 已切换主题：\(theme.title)")
        }
    }

    func setModuleEnabled(_ key: ModuleToggleKey, enabled: Bool) async {
        self.enabledModules[key.rawValue] = enabled
        UserDefaults.standard.set(self.enabledModules, forKey: "tm_enabled_modules")
        if let updated = try? await client.updatePreferences(body: ["module_key": key.rawValue, "enabled": enabled]) {
            applyPreferencesResponse(updated)
            showBanner(enabled ? "✅ 已启用 \(key.title)" : "🙈 已隐藏 \(key.title)")
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
