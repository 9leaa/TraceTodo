import Foundation

enum APIError: LocalizedError {
    case invalidURL
    case serverError(Int, String, String?)

    var statusCode: Int? {
        if case .serverError(let code, _, _) = self { return code }
        return nil
    }

    var errorCode: String? {
        if case .serverError(_, let codeStr, _) = self { return codeStr }
        return nil
    }

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "服务端地址格式无效"
        case .serverError(let code, _, let msg):
            let clean = msg?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return clean.isEmpty ? "服务端响应错误 (\(code))" : clean
        }
    }
}

struct TraceMemoryAPIClient {
    let baseURL: String
    let spaceId: String
    var authToken: String = ""

    private var cleanBase: String {
        baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }

    private func makeURL(path: String, queryItems: [URLQueryItem] = [], includeSpaceId: Bool = true) throws -> URL {
        guard var comps = URLComponents(string: cleanBase + path) else {
            throw APIError.invalidURL
        }
        var items = queryItems
        if includeSpaceId {
            items.append(URLQueryItem(name: "space_id", value: spaceId))
        }
        if !items.isEmpty {
            comps.queryItems = items
        }
        guard let url = comps.url else {
            throw APIError.invalidURL
        }
        return url
    }

    private static func decodeServerError(code: Int, data: Data) -> APIError {
        if let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            let errMsg = (dict["error"] as? String) ?? (dict["detail"] as? String)
            let errCode = dict["code"] as? String
            return APIError.serverError(code, errCode ?? "", errMsg)
        }
        let text = String(data: data, encoding: .utf8) ?? ""
        return APIError.serverError(code, "", text)
    }

    private func get<T: Decodable>(_ path: String, queryItems: [URLQueryItem] = [], includeSpaceId: Bool = true) async throws -> T {
        let url = try makeURL(path: path, queryItems: queryItems, includeSpaceId: includeSpaceId)
        var req = URLRequest(url: url)
        req.timeoutInterval = 20
        let cleanTok = authToken.trimmingCharacters(in: .whitespacesAndNewlines)
        if !cleanTok.isEmpty {
            req.setValue("Bearer \(cleanTok)", forHTTPHeaderField: "Authorization")
        }
        let (data, resp) = try await URLSession.shared.data(for: req)
        if let http = resp as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw Self.decodeServerError(code: http.statusCode, data: data)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    @discardableResult
    private func postRaw(_ path: String, body: [String: Any]) async throws -> Data {
        let url = try makeURL(path: path)
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 35
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let cleanTok = authToken.trimmingCharacters(in: .whitespacesAndNewlines)
        if !cleanTok.isEmpty {
            req.setValue("Bearer \(cleanTok)", forHTTPHeaderField: "Authorization")
        }
        var payload = body
        payload["space_id"] = spaceId
        req.httpBody = try JSONSerialization.data(withJSONObject: payload)

        let (data, resp) = try await URLSession.shared.data(for: req)
        if let http = resp as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw Self.decodeServerError(code: http.statusCode, data: data)
        }
        return data
    }

    func checkHealth() async throws -> [String: Any] {
        let url = try makeURL(path: "/api/health")
        var req = URLRequest(url: url)
        req.timeoutInterval = 15
        let (data, resp) = try await URLSession.shared.data(for: req)
        if let http = resp as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw Self.decodeServerError(code: http.statusCode, data: data)
        }
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }

    func fetchAuthStatus() async throws -> AuthStatusResponse {
        try await get("/api/auth/status")
    }

    func fetchAuthCaptcha() async throws -> AuthCaptchaResponse {
        try await get("/api/auth/captcha", includeSpaceId: false)
    }

    func login(username: String, password: String) async throws -> AuthSessionResponse {
        let data = try await postRaw(
            "/api/auth/login",
            body: ["username": username, "password": password, "device_label": "apple-app"]
        )
        return try JSONDecoder().decode(AuthSessionResponse.self, from: data)
    }

    func register(
        username: String,
        password: String,
        displayName: String,
        captchaId: String = "",
        captchaCode: String = ""
    ) async throws -> AuthSessionResponse {
        let data = try await postRaw(
            "/api/auth/register",
            body: [
                "username": username,
                "password": password,
                "display_name": displayName,
                "device_label": "apple-app",
                "captcha_id": captchaId,
                "captcha_code": captchaCode
            ]
        )
        return try JSONDecoder().decode(AuthSessionResponse.self, from: data)
    }

    func logout() async throws {
        try await postRaw("/api/auth/logout", body: [:])
    }

    func createSupervisionShare() async throws -> SupervisionShareResponse {
        let data = try await postRaw("/api/supervision/share", body: ["label": "好友监督"])
        return try JSONDecoder().decode(SupervisionShareResponse.self, from: data)
    }

    func fetchAdminOverview() async throws -> AdminOverviewResponse {
        try await get("/api/admin/overview")
    }

    func fetchAdminUsers() async throws -> AdminUsersResponse {
        try await get("/api/admin/users")
    }

    func updateAdminUser(userId: String, status: String? = nil, role: String? = nil) async throws {
        var body: [String: Any] = ["user_id": userId]
        if let status { body["status"] = status }
        if let role { body["role"] = role }
        try await postRaw("/api/admin/users/update", body: body)
    }

    func updateAdminSettings(_ s: AdminSettingsModel) async throws -> AdminSettingsModel {
        let body: [String: Any] = [
            "allow_registration": s.allowRegistration,
            "noise_filter_enabled": s.noiseFilterEnabled,
            "max_note_chars": s.maxNoteChars,
            "min_meaningful_chars": s.minMeaningfulChars,
            "rate_limit_per_minute": s.rateLimitPerMinute,
            "duplicate_window_seconds": s.duplicateWindowSeconds,
            "default_daily_note_limit": s.defaultDailyNoteLimit,
            "default_daily_llm_limit": s.defaultDailyLlmLimit,
            "default_daily_token_limit": s.defaultDailyTokenLimit
        ]
        let data = try await postRaw("/api/admin/settings", body: body)
        if let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let nested = dict["settings"] {
            let nestedData = try JSONSerialization.data(withJSONObject: nested)
            return try JSONDecoder().decode(AdminSettingsModel.self, from: nestedData)
        }
        return s
    }

    func fetchMatrix(sortBy: TaskSortOption, includeDone: Bool) async throws -> MatrixResponse {
        try await get(
            "/api/tasks/matrix",
            queryItems: [
                URLQueryItem(name: "sort_by", value: sortBy.rawValue),
                URLQueryItem(name: "include_done", value: includeDone ? "true" : "false")
            ]
        )
    }

    @discardableResult
    func recordNote(
        content: String,
        idempotencyKey: String = "",
        asyncMode: Bool = true,
        mediaIds: [String] = []
    ) async throws -> NoteRecordResponse {
        var payload: [String: Any] = ["content": content, "async_mode": asyncMode]
        if !idempotencyKey.isEmpty {
            payload["idempotency_key"] = idempotencyKey
        }
        if !mediaIds.isEmpty {
            payload["media_ids"] = mediaIds
        }
        let data = try await postRaw("/api/notes/record", body: payload)
        return (try? JSONDecoder().decode(NoteRecordResponse.self, from: data))
            ?? NoteRecordResponse(note: nil, extractionStatus: "completed", filtered: false, filterReason: nil, errorMessage: nil, jobId: nil)
    }

    func fetchBootstrap(explicitSpaceId: String? = nil) async throws -> BootstrapResponse {
        if let sp = explicitSpaceId?.trimmingCharacters(in: .whitespacesAndNewlines), !sp.isEmpty {
            return try await get("/api/bootstrap", queryItems: [URLQueryItem(name: "space_id", value: sp)], includeSpaceId: false)
        }
        return try await get("/api/bootstrap", includeSpaceId: false)
    }

    func uploadMedia(
        imageBase64: String,
        thumbBase64: String = "",
        width: Int = 0,
        height: Int = 0
    ) async throws -> MediaUploadResponse {
        let data = try await postRaw(
            "/api/media/upload",
            body: [
                "image_base64": imageBase64,
                "thumb_base64": thumbBase64,
                "width": width,
                "height": height
            ]
        )
        return try JSONDecoder().decode(MediaUploadResponse.self, from: data)
    }

    func deleteMedia(mediaId: String) async throws {
        try await postRaw("/api/media/delete", body: ["media_id": mediaId])
    }

    func fetchIngestStatus(jobId: String = "", waitMs: Int = 0, sinceVersion: Int = -1) async throws -> IngestStatusResponse {
        var items: [URLQueryItem] = []
        if !jobId.isEmpty {
            items.append(URLQueryItem(name: "job_id", value: jobId))
        }
        if waitMs > 0 {
            items.append(URLQueryItem(name: "wait_ms", value: String(waitMs)))
        }
        if sinceVersion >= 0 {
            items.append(URLQueryItem(name: "since_version", value: String(sinceVersion)))
        }
        return try await get("/api/notes/ingest-status", queryItems: items)
    }

    func streamSyncEvents(
        sinceVersion: Int,
        onEvent: @escaping (_ eventName: String, _ state: SyncStateResponse) async -> Void
    ) async throws {
        let url = try makeURL(
            path: "/api/sync/stream",
            queryItems: [URLQueryItem(name: "since", value: String(max(0, sinceVersion)))]
        )
        var req = URLRequest(url: url)
        req.timeoutInterval = 300
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        let cleanTok = authToken.trimmingCharacters(in: .whitespacesAndNewlines)
        if !cleanTok.isEmpty {
            req.setValue("Bearer \(cleanTok)", forHTTPHeaderField: "Authorization")
        }

        let (bytes, resp) = try await URLSession.shared.bytes(for: req)
        if let http = resp as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw APIError.serverError(http.statusCode, "", "SSE HTTP \(http.statusCode)")
        }

        var currentEvent = "message"
        var dataLines: [String] = []
        let decoder = JSONDecoder()

        for try await line in bytes.lines {
            if Task.isCancelled { break }
            if line.isEmpty {
                if !dataLines.isEmpty {
                    let jsonText = dataLines.joined(separator: "\n")
                    if let payloadData = jsonText.data(using: .utf8),
                       let parsed = try? decoder.decode(SyncStateResponse.self, from: payloadData) {
                        await onEvent(currentEvent, parsed)
                    }
                    dataLines.removeAll(keepingCapacity: true)
                }
                currentEvent = "message"
                continue
            }
            if line.hasPrefix(":") {
                continue
            }
            if line.hasPrefix("event:") {
                currentEvent = line.dropFirst(6).trimmingCharacters(in: .whitespaces)
            } else if line.hasPrefix("data:") {
                dataLines.append(line.dropFirst(5).trimmingCharacters(in: .whitespaces))
            }
        }
        if !Task.isCancelled && !dataLines.isEmpty {
            let jsonText = dataLines.joined(separator: "\n")
            if let payloadData = jsonText.data(using: .utf8),
               let parsed = try? decoder.decode(SyncStateResponse.self, from: payloadData) {
                await onEvent(currentEvent, parsed)
            }
        }
    }

    func fetchSyncState() async throws -> SyncStateResponse {
        try await get("/api/sync/state")
    }

    func fetchPreferences() async throws -> SpacePreferencesResponse {
        try await get("/api/preferences")
    }

    func updatePreferences(body: [String: Any]) async throws -> SpacePreferencesResponse {
        let data = try await postRaw("/api/preferences", body: body)
        return try JSONDecoder().decode(SpacePreferencesResponse.self, from: data)
    }

    func fetchNotesList(query: String = "", limit: Int = 50) async throws -> RawNoteListResponse {
        var items = [URLQueryItem(name: "limit", value: String(limit))]
        let clean = query.trimmingCharacters(in: .whitespacesAndNewlines)
        if !clean.isEmpty {
            items.append(URLQueryItem(name: "q", value: clean))
        }
        return try await get("/api/notes/list", queryItems: items)
    }

    func forgetNote(noteId: String) async throws {
        try await postRaw("/api/notes/forget", body: ["note_id": noteId])
    }

    func refreshTaskStatuses() async throws -> TaskStatusRefreshResponse {
        let data = try await postRaw("/api/tasks/refresh-status", body: [:])
        return try JSONDecoder().decode(TaskStatusRefreshResponse.self, from: data)
    }

    func saveTask(body: [String: Any]) async throws {
        try await postRaw("/api/tasks/save", body: body)
    }

    func decomposeTask(title: String, summary: String = "", memoryId: String = "") async throws -> DecomposeResponse {
        let data = try await postRaw(
            "/api/tasks/decompose",
            body: [
                "title": title,
                "summary": summary,
                "memory_id": memoryId
            ]
        )
        return try JSONDecoder().decode(DecomposeResponse.self, from: data)
    }

    func toggleSubtask(memoryId: String, subtaskId: String, done: Bool, expectedVersion: Int) async throws -> SubtaskToggleResponse {
        let data = try await postRaw(
            "/api/tasks/subtasks/toggle",
            body: [
                "memory_id": memoryId,
                "subtask_id": subtaskId,
                "done": done,
                "expected_version": expectedVersion
            ]
        )
        return try JSONDecoder().decode(SubtaskToggleResponse.self, from: data)
    }

    func transitionTask(
        memoryId: String,
        action: String,
        expectedVersion: Int? = nil,
        undoNextTaskId: String = ""
    ) async throws -> TaskTransitionResponse {
        var body: [String: Any] = [
            "memory_id": memoryId,
            "action": action
        ]
        if let expectedVersion, expectedVersion >= 1 {
            body["expected_version"] = expectedVersion
        }
        if !undoNextTaskId.isEmpty {
            body["undo_next_task_id"] = undoNextTaskId
        }
        let data = try await postRaw("/api/tasks/transition", body: body)
        return try JSONDecoder().decode(TaskTransitionResponse.self, from: data)
    }

    func updateQuadrant(memoryId: String, expectedVersion: Int, quadrant: QuadrantKind) async throws {
        try await postRaw(
            "/api/tasks/quadrant",
            body: [
                "memory_id": memoryId,
                "expected_version": expectedVersion,
                "quadrant": quadrant.rawValue
            ]
        )
    }

    func recordFocusEvent(
        memoryId: String,
        event: String,
        reason: String = "",
        elapsedSeconds: Int = 0,
        expectedVersion: Int
    ) async throws {
        try await postRaw(
            "/api/tasks/focus",
            body: [
                "memory_id": memoryId,
                "event": event,
                "reason": reason,
                "elapsed_seconds": elapsedSeconds,
                "expected_version": expectedVersion
            ]
        )
    }

    func fetchFocusOverview(limit: Int = 60) async throws -> FocusOverviewResponse {
        try await get("/api/focus/overview", queryItems: [URLQueryItem(name: "limit", value: String(limit))])
    }

    func recordFocusSession(body: [String: Any]) async throws {
        try await postRaw("/api/focus/record", body: body)
    }

    func deleteFocusSession(sessionId: String) async throws {
        try await postRaw("/api/focus/delete", body: ["session_id": sessionId])
    }

    func fetchSupervisionBoard(assignee: String = "") async throws -> SupervisionBoardResponse {
        var items: [URLQueryItem] = []
        if !assignee.isEmpty {
            items.append(URLQueryItem(name: "assignee", value: assignee))
        }
        return try await get("/api/supervision/board", queryItems: items)
    }

    func sendNudge(memoryId: String, kind: String, fromUser: String) async throws {
        try await postRaw(
            "/api/supervision/nudge",
            body: [
                "memory_id": memoryId,
                "kind": kind,
                "from_user": fromUser
            ]
        )
    }

    func fetchMemoryDetail(memoryId: String) async throws -> MemoryDetailResponse {
        try await get("/api/memories/detail", queryItems: [URLQueryItem(name: "id", value: memoryId)])
    }

    func searchMemories(query: String = "", memoryType: String = "", topK: Int = 50) async throws -> MemorySearchResponse {
        try await get(
            "/api/memories/search",
            queryItems: [
                URLQueryItem(name: "q", value: query),
                URLQueryItem(name: "memory_type", value: memoryType),
                URLQueryItem(name: "top_k", value: String(topK))
            ]
        )
    }

    func proposeMemoryChange(body: [String: Any]) async throws -> MemoryProposeResponse {
        let data = try await postRaw("/api/memories/propose", body: body)
        return try JSONDecoder().decode(MemoryProposeResponse.self, from: data)
    }

    func saveMemoryMarkdown(memoryId: String, expectedVersion: Int, markdown: String) async throws -> MemoryProposeResponse {
        let data = try await postRaw(
            "/api/memories/markdown/save",
            body: [
                "memory_id": memoryId,
                "expected_version": expectedVersion,
                "markdown": markdown
            ]
        )
        return try JSONDecoder().decode(MemoryProposeResponse.self, from: data)
    }

    func ask(question: String) async throws -> AskResponse {
        let data = try await postRaw("/api/ask", body: ["question": question])
        return try JSONDecoder().decode(AskResponse.self, from: data)
    }

    func fetchGraphCanvas(
        seed: String = "",
        memoryType: String = "",
        subject: String = "",
        maxHops: Int = 2,
        includeSemantic: Bool = true,
        semanticThreshold: Double = 0.52,
        includeUnresolved: Bool = true
    ) async throws -> GraphCanvasResponse {
        try await get(
            "/api/graph/canvas",
            queryItems: [
                URLQueryItem(name: "seed", value: seed),
                URLQueryItem(name: "memory_type", value: memoryType),
                URLQueryItem(name: "subject", value: subject),
                URLQueryItem(name: "max_hops", value: String(maxHops)),
                URLQueryItem(name: "include_semantic", value: includeSemantic ? "1" : "0"),
                URLQueryItem(
                    name: "semantic_threshold",
                    value: String(format: "%.2f", locale: Locale(identifier: "en_US_POSIX"), semanticThreshold)
                ),
                URLQueryItem(name: "include_unresolved", value: includeUnresolved ? "1" : "0")
            ]
        )
    }

    func runDream() async throws {
        try await postRaw("/api/dream", body: [:])
    }

    func seedDemoData() async throws {
        try await postRaw("/api/seed-demo", body: [:])
    }

    // MARK: - Phase 3: Calendar Timeline & Slot Scheduling

    func fetchCalendarOverview(view: String = "week", anchorDate: String = "", includeDone: Bool = true) async throws -> CalendarOverviewResponse {
        try await get(
            "/api/calendar/overview",
            queryItems: [
                URLQueryItem(name: "view", value: view),
                URLQueryItem(name: "anchor_date", value: anchorDate),
                URLQueryItem(name: "include_done", value: includeDone ? "true" : "false")
            ]
        )
    }

    func scheduleCalendarSlot(body: [String: Any]) async throws {
        try await postRaw("/api/calendar/schedule", body: body)
    }

    // MARK: - Phase 3: Countdowns & Anniversaries

    func fetchCountdowns() async throws -> CountdownListResponse {
        try await get("/api/countdowns/list")
    }

    func saveCountdown(body: [String: Any]) async throws -> CountdownItemModel {
        let data = try await postRaw("/api/countdowns/save", body: body)
        return try JSONDecoder().decode(CountdownItemModel.self, from: data)
    }

    func deleteCountdown(countdownId: String) async throws {
        try await postRaw("/api/countdowns/delete", body: ["countdown_id": countdownId])
    }

    // MARK: - Phase 4: Pinned Memos & One-Page-Per-Day Mood Journal

    func fetchMemos(query: String = "", tag: String = "", pinnedOnly: Bool = false, limit: Int = 80) async throws -> MemoListResponse {
        try await get(
            "/api/memos/list",
            queryItems: [
                URLQueryItem(name: "q", value: query),
                URLQueryItem(name: "tag", value: tag),
                URLQueryItem(name: "pinned_only", value: pinnedOnly ? "true" : "false"),
                URLQueryItem(name: "limit", value: String(limit))
            ]
        )
    }

    func saveMemo(body: [String: Any]) async throws -> MemoSaveResponse {
        let data = try await postRaw("/api/memos/save", body: body)
        return try JSONDecoder().decode(MemoSaveResponse.self, from: data)
    }

    func toggleMemoPin(noteId: String, pinned: Bool) async throws {
        try await postRaw("/api/memos/pin", body: ["note_id": noteId, "pinned": pinned])
    }

    func fetchJournalOverview(date: String = "", limit: Int = 30) async throws -> JournalOverviewResponse {
        try await get(
            "/api/journal/overview",
            queryItems: [
                URLQueryItem(name: "date", value: date),
                URLQueryItem(name: "limit", value: String(limit))
            ]
        )
    }

    func saveJournal(body: [String: Any]) async throws -> JournalSaveResponse {
        let data = try await postRaw("/api/journal/save", body: body)
        return try JSONDecoder().decode(JournalSaveResponse.self, from: data)
    }

    func generateJournalAISummary(date: String, draftContent: String, triggerDream: Bool = false) async throws -> JournalAISummaryResponse {
        let data = try await postRaw(
            "/api/journal/ai-summary",
            body: [
                "date": date,
                "draft_content": draftContent,
                "trigger_dream": triggerDream
            ]
        )
        return try JSONDecoder().decode(JournalAISummaryResponse.self, from: data)
    }
}
