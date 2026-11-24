import SwiftUI

// MARK: - Memo & Journal Models

struct MoodCatalogItem: Codable, Identifiable, Hashable {
    let id: String
    let icon: String
    let label: String
    let color: String
    let group: String?
}

struct MemoTagCountItem: Codable, Identifiable, Hashable {
    let tag: String
    let count: Int
    var id: String { tag }
}

struct RichNoteItemModel: Codable, Identifiable, Hashable {
    let noteId: String
    let spaceId: String
    let content: String
    let sourceType: String
    let revision: Int
    let eventTime: String
    let createdAt: String
    let updatedAt: String
    let noteKind: String?
    let title: String?
    let pinned: Bool?
    let tags: [String]?
    let mood: String?
    let moodMeta: MoodCatalogItem?
    let journalDate: String?
    let lunarLabel: String?
    let weekdayLabel: String?
    let extractMemory: Bool?
    let media: [MediaAttachmentModel]?

    var id: String { noteId }

    enum CodingKeys: String, CodingKey {
        case noteId = "note_id"
        case spaceId = "space_id"
        case content
        case sourceType = "source_type"
        case revision
        case eventTime = "event_time"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case noteKind = "note_kind"
        case title, pinned, tags, mood, media
        case moodMeta = "mood_meta"
        case journalDate = "journal_date"
        case lunarLabel = "lunar_label"
        case weekdayLabel = "weekday_label"
        case extractMemory = "extract_memory"
    }
}

struct MemoListResponse: Codable {
    let spaceId: String
    let memos: [RichNoteItemModel]
    let total: Int
    let pinnedCount: Int
    let tagsCatalog: [MemoTagCountItem]

    enum CodingKeys: String, CodingKey {
        case spaceId = "space_id"
        case memos, total
        case pinnedCount = "pinned_count"
        case tagsCatalog = "tags_catalog"
    }
}

struct MemoSaveResponse: Codable {
    let memo: RichNoteItemModel
    let jobId: String?

    enum CodingKeys: String, CodingKey {
        case memo
        case jobId = "job_id"
    }
}

struct JournalFootprintModel: Codable, Hashable {
    let date: String
    let completedCount: Int
    let openDueCount: Int
    let focusSessionsCount: Int
    let focusPomodoros: Int
    let focusMinutes: Int
    let memoCount: Int

    enum CodingKeys: String, CodingKey {
        case date
        case completedCount = "completed_count"
        case openDueCount = "open_due_count"
        case focusSessionsCount = "focus_sessions_count"
        case focusPomodoros = "focus_pomodoros"
        case focusMinutes = "focus_minutes"
        case memoCount = "memo_count"
    }
}

struct JournalOverviewResponse: Codable {
    let spaceId: String
    let date: String
    let weekdayLabel: String
    let lunarLabel: String
    let currentEntry: RichNoteItemModel?
    let dayFootprint: JournalFootprintModel
    let timeline: [RichNoteItemModel]
    let moodCatalog: [MoodCatalogItem]

    enum CodingKeys: String, CodingKey {
        case spaceId = "space_id"
        case date
        case weekdayLabel = "weekday_label"
        case lunarLabel = "lunar_label"
        case currentEntry = "current_entry"
        case dayFootprint = "day_footprint"
        case timeline
        case moodCatalog = "mood_catalog"
    }
}

struct JournalSaveResponse: Codable {
    let journal: RichNoteItemModel
    let jobId: String?

    enum CodingKeys: String, CodingKey {
        case journal
        case jobId = "job_id"
    }
}

struct JournalAISummaryResponse: Codable {
    let date: String
    let summaryMarkdown: String
    let provider: String

    enum CodingKeys: String, CodingKey {
        case date
        case summaryMarkdown = "summary_markdown"
        case provider
    }
}

// MARK: - Unified Notes Hub (Memos + Daily Journal)

enum NotesHubSubmode: String, Identifiable {
    case memo = "memo"
    case journal = "journal"
    var id: String { rawValue }
}

struct MemoAndJournalStudioView: View {
    @EnvironmentObject var store: TodoStore
    @State private var submode: NotesHubSubmode = .memo

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("📝 备忘录与每日日记")
                            .font(.headline.bold())
                        Text("备忘卡片、每日心情日记与 AI 今日小结")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    if store.isModuleEnabled(.journal) {
                        Picker("随记模式", selection: $submode) {
                            Text("📌 备忘录").tag(NotesHubSubmode.memo)
                            Text("📖 每日日记").tag(NotesHubSubmode.journal)
                        }
                        .pickerStyle(.segmented)
                        .frame(maxWidth: 210)
                    }
                }

                if submode == .journal && store.isModuleEnabled(.journal) {
                    JournalDailyStudioView()
                } else {
                    MemosGalleryView()
                }
            }
            .padding(14)
        }
        .iosScrollDismissesKeyboard()
    }
}

// MARK: - Subview 1: Pinned Memos (Grid / List + Tag Filter + Markdown + OCR)

struct MemosGalleryView: View {
    @EnvironmentObject var store: TodoStore

    @State private var editingNoteId: String = ""
    @State private var title: String = ""
    @State private var tagsInput: String = ""
    @State private var content: String = ""
    @State private var pinned: Bool = false
    @State private var extractMemory: Bool = false
    @State private var pendingMedia: [MediaAttachmentModel] = []

    @State private var searchQuery: String = ""
    @State private var selectedTag: String = ""
    @State private var pinnedOnly: Bool = false
    @State private var useGridLayout: Bool = true
    @State private var memoResp: MemoListResponse?
    @State private var isLoading: Bool = false

    private let gridColumns = [
        GridItem(.adaptive(minimum: 260, maximum: 440), spacing: 12)
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            composeCard
            filterToolbar
            memosCollection
        }
        .task {
            await reloadMemos()
        }
    }

    private var composeCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                TextField("备忘标题（可选）...", text: $title)
                    .textFieldStyle(.roundedBorder)
                    .font(.subheadline.bold())
                TextField("#标签（空格分隔）", text: $tagsInput)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 165)
            }

            TextField("支持 Markdown（**加粗**、- 列表、#标签）与附图 OCR...", text: $content, axis: .vertical)
                .lineLimit(3...7)
                .textFieldStyle(.roundedBorder)

            if !pendingMedia.isEmpty {
                MediaThumbnailGridView(media: pendingMedia) { item in
                    pendingMedia.removeAll { $0.mediaId == item.mediaId }
                    Task { try? await store.client.deleteMedia(mediaId: item.mediaId) }
                }
            }

            HStack(spacing: 12) {
                Toggle("📌 置顶", isOn: $pinned)
                    .toggleStyle(.switch)
                    .controlSize(.small)
                Toggle("🧠 静默提炼长期偏好/事实", isOn: $extractMemory)
                    .toggleStyle(.switch)
                    .controlSize(.small)
                Spacer()
            }
            .font(.caption)

            HStack(spacing: 8) {
                if !editingNoteId.isEmpty {
                    Button("取消编辑") {
                        resetCompose()
                    }
                    .buttonStyle(.bordered)
                }
                Spacer()
                ImageAttachPickerButton(pendingMedia: $pendingMedia)
                VoiceMicButton(text: $content, compact: false)
                Button(editingNoteId.isEmpty ? "✍️ 保存备忘" : "💾 保存修改") {
                    submitMemo()
                }
                .buttonStyle(.borderedProminent)
                .tint(store.activeTheme.primaryColor)
                .disabled(
                    content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    && title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    && pendingMedia.isEmpty
                )
            }
        }
        .padding(14)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(Color.primary.opacity(0.08), lineWidth: 1)
        )
    }

    private var filterToolbar: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                TextField("检索备忘标题、正文或图片 OCR...", text: $searchQuery)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { Task { await reloadMemos() } }
                Button("搜索") {
                    Task { await reloadMemos() }
                }
                .buttonStyle(.bordered)

                Button {
                    useGridLayout.toggle()
                } label: {
                    Image(systemName: useGridLayout ? "square.grid.2x2.fill" : "list.bullet")
                }
                .buttonStyle(.bordered)
            }

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    tagChip(label: "全部 (\(memoResp?.total ?? 0))", active: selectedTag.isEmpty && !pinnedOnly) {
                        selectedTag = ""
                        pinnedOnly = false
                        Task { await reloadMemos() }
                    }
                    tagChip(label: "📌 置顶 (\(memoResp?.pinnedCount ?? 0))", active: pinnedOnly) {
                        pinnedOnly.toggle()
                        Task { await reloadMemos() }
                    }
                    ForEach(memoResp?.tagsCatalog ?? []) { tc in
                        tagChip(label: "#\(tc.tag) (\(tc.count))", active: selectedTag == tc.tag) {
                            selectedTag = (selectedTag == tc.tag) ? "" : tc.tag
                            Task { await reloadMemos() }
                        }
                    }
                }
            }
        }
    }

    private func tagChip(label: String, active: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(.caption.bold())
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(active ? store.activeTheme.primaryColor : Color.primary.opacity(0.05))
                .foregroundStyle(active ? Color.white : .primary)
                .clipShape(Capsule())
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private var memosCollection: some View {
        let items = memoResp?.memos ?? []
        if isLoading && items.isEmpty {
            ProgressView("正在加载备忘录...")
                .frame(maxWidth: .infinity, minHeight: 160)
        } else if items.isEmpty {
            ContentUnavailableView("暂无备忘录", systemImage: "pin.slash", description: Text("支持置顶、#标签筛选、Markdown 与附图 OCR"))
                .padding(.vertical, 24)
        } else if useGridLayout {
            LazyVGrid(columns: gridColumns, alignment: .leading, spacing: 12) {
                ForEach(items) { item in
                    memoCard(item)
                }
            }
        } else {
            VStack(spacing: 12) {
                ForEach(items) { item in
                    memoCard(item)
                }
            }
        }
    }

    private func memoCard(_ item: RichNoteItemModel) -> some View {
        let isPin = item.pinned ?? false
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                if isPin {
                    Text("📌 置顶")
                        .font(.caption2.bold())
                        .padding(.horizontal, 7)
                        .padding(.vertical, 2)
                        .background(store.activeTheme.primaryColor)
                        .foregroundStyle(.white)
                        .clipShape(Capsule())
                }
                ForEach(item.tags ?? [], id: \.self) { t in
                    Text("#\(t)")
                        .font(.caption2.weight(.semibold))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(store.activeTheme.primaryColor.opacity(0.12))
                        .foregroundStyle(store.activeTheme.primaryColor)
                        .clipShape(Capsule())
                }
                Spacer(minLength: 4)
                Text(String(item.updatedAt.prefix(16)).replacingOccurrences(of: "T", with: " "))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            if let t = item.title, !t.isEmpty {
                Text(t)
                    .font(.headline)
            }

            MarkdownAnswerView(text: item.content)

            if let mList = item.media, !mList.isEmpty {
                MediaThumbnailGridView(media: mList)
            }

            HStack(spacing: 10) {
                Spacer()
                Button(isPin ? "取消置顶" : "📌 置顶") {
                    Task {
                        try? await store.client.toggleMemoPin(noteId: item.noteId, pinned: !isPin)
                        await reloadMemos()
                    }
                }
                .font(.caption)
                .buttonStyle(.borderless)

                Button("✏️ 编辑") {
                    editingNoteId = item.noteId
                    title = item.title ?? ""
                    tagsInput = (item.tags ?? []).joined(separator: " ")
                    content = item.content
                    pinned = item.pinned ?? false
                    extractMemory = item.extractMemory ?? false
                }
                .font(.caption)
                .buttonStyle(.borderless)

                Button(role: .destructive) {
                    Task {
                        try? await store.client.forgetNote(noteId: item.noteId)
                        await reloadMemos()
                        await store.refresh()
                    }
                } label: {
                    Text("🗑️ 遗忘")
                        .font(.caption)
                }
                .buttonStyle(.borderless)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(isPin ? store.activeTheme.primaryColor : Color.primary.opacity(0.08), lineWidth: isPin ? 1.5 : 1)
        )
    }

    private func resetCompose() {
        editingNoteId = ""
        title = ""
        tagsInput = ""
        content = ""
        pinned = false
        extractMemory = false
        pendingMedia = []
    }

    private func submitMemo() {
        let body: [String: Any] = [
            "note_id": editingNoteId,
            "title": title.trimmingCharacters(in: .whitespacesAndNewlines),
            "content": content.trimmingCharacters(in: .whitespacesAndNewlines),
            "tags": tagsInput,
            "pinned": pinned,
            "extract_memory": extractMemory,
            "media_ids": pendingMedia.map(\.mediaId)
        ]
        dismissAppKeyboard()
        Task {
            _ = try? await store.client.saveMemo(body: body)
            resetCompose()
            await reloadMemos()
        }
    }

    private func reloadMemos() async {
        isLoading = true
        defer { isLoading = false }
        if let res = try? await store.client.fetchMemos(query: searchQuery, tag: selectedTag, pinnedOnly: pinnedOnly) {
            memoResp = res
        }
    }
}

// MARK: - Subview 2: One-Page-Per-Day Mood Journal + AI Daily Summary

struct JournalDailyStudioView: View {
    @EnvironmentObject var store: TodoStore

    @State private var selectedDate: String = CalendarTimelineView.todayYMD()
    @State private var selectedMood: String = "calm"
    @State private var draftContent: String = ""
    @State private var extractMemory: Bool = true
    @State private var pendingMedia: [MediaAttachmentModel] = []
    @State private var overview: JournalOverviewResponse?
    @State private var isBusy: Bool = false

    private let fallbackMoods: [MoodCatalogItem] = [
        MoodCatalogItem(id: "joy", icon: "😊", label: "愉悦", color: "#f59e0b", group: "positive"),
        MoodCatalogItem(id: "focused", icon: "🔥", label: "充实", color: "#ef4444", group: "positive"),
        MoodCatalogItem(id: "celebrate", icon: "🎉", label: "惊喜", color: "#ec4899", group: "positive"),
        MoodCatalogItem(id: "grateful", icon: "💖", label: "感恩", color: "#f43f5e", group: "positive"),
        MoodCatalogItem(id: "calm", icon: "🌿", label: "平静", color: "#10b981", group: "neutral"),
        MoodCatalogItem(id: "thinking", icon: "🤔", label: "思考", color: "#6366f1", group: "neutral"),
        MoodCatalogItem(id: "meh", icon: "😶", label: "麻木", color: "#94a3b8", group: "neutral"),
        MoodCatalogItem(id: "tired", icon: "😴", label: "疲惫", color: "#64748b", group: "neutral"),
        MoodCatalogItem(id: "sad", icon: "😔", label: "难过", color: "#3b82f6", group: "negative"),
        MoodCatalogItem(id: "anxious", icon: "😰", label: "焦虑", color: "#8b5cf6", group: "negative"),
        MoodCatalogItem(id: "angry", icon: "😠", label: "烦躁", color: "#dc2626", group: "negative"),
        MoodCatalogItem(id: "low", icon: "🌧️", label: "低落", color: "#475569", group: "negative")
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            journalEditorCard
            historySection
        }
        .task {
            await reloadJournal()
        }
    }

    private var journalEditorCard: some View {
        let moods = overview?.moodCatalog ?? fallbackMoods
        return VStack(alignment: .leading, spacing: 12) {
            // Date navigator + AI Summary triggers
            HStack(spacing: 8) {
                Button { shiftDay(-1) } label: { Image(systemName: "chevron.left") }
                    .buttonStyle(.bordered)
                Text(selectedDate)
                    .font(.subheadline.bold().monospacedDigit())
                Button("今天") {
                    selectedDate = CalendarTimelineView.todayYMD()
                    Task { await reloadJournal() }
                }
                .buttonStyle(.bordered)
                Button { shiftDay(1) } label: { Image(systemName: "chevron.right") }
                    .buttonStyle(.bordered)

                Spacer()

                Button {
                    triggerAISummary(withDream: false)
                } label: {
                    Text(isBusy ? "汇总中..." : "✨ AI 今日小结")
                        .font(.caption.bold())
                }
                .buttonStyle(.borderedProminent)
                .tint(store.activeTheme.primaryColor)
                .disabled(isBusy)

                Button {
                    triggerAISummary(withDream: true)
                } label: {
                    Text("🌙 小结+Dream")
                        .font(.caption.bold())
                }
                .buttonStyle(.bordered)
                .disabled(isBusy)
            }

            if let ov = overview {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        Text("\(ov.lunarLabel) · \(ov.weekdayLabel)")
                            .font(.caption2.bold())
                            .foregroundStyle(.secondary)
                        footprintBadge("✅ 完成待办 \(ov.dayFootprint.completedCount) 项")
                        footprintBadge("🍅 专注 \(ov.dayFootprint.focusMinutes)m (\(ov.dayFootprint.focusPomodoros) 番茄)")
                        footprintBadge("📌 随记 \(ov.dayFootprint.memoCount) 条")
                    }
                }
            }

            // Mood Selector
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(moods) { m in
                        Button {
                            selectedMood = m.id
                        } label: {
                            Text("\(m.icon) \(m.label)")
                                .font(.caption.bold())
                                .padding(.horizontal, 11)
                                .padding(.vertical, 6)
                                .background(selectedMood == m.id ? store.activeTheme.primaryColor.opacity(0.16) : Color.primary.opacity(0.04))
                                .foregroundStyle(selectedMood == m.id ? store.activeTheme.primaryColor : .primary)
                                .clipShape(Capsule())
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            TextField("写下今天的经历、感悟或点击右上角「✨ AI 今日小结」自动汇总...", text: $draftContent, axis: .vertical)
                .lineLimit(6...14)
                .textFieldStyle(.roundedBorder)

            if !pendingMedia.isEmpty {
                MediaThumbnailGridView(media: pendingMedia) { item in
                    pendingMedia.removeAll { $0.mediaId == item.mediaId }
                    Task { try? await store.client.deleteMedia(mediaId: item.mediaId) }
                }
            }

            HStack {
                Toggle("🧠 静默沉淀长期经历/偏好", isOn: $extractMemory)
                    .toggleStyle(.switch)
                    .controlSize(.small)
                    .font(.caption)
                Spacer()
                ImageAttachPickerButton(pendingMedia: $pendingMedia)
                VoiceMicButton(text: $draftContent, compact: false)
                Button("💾 保存当日日记") {
                    saveJournal()
                }
                .buttonStyle(.borderedProminent)
                .tint(store.activeTheme.primaryColor)
                .disabled(isBusy || (draftContent.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && pendingMedia.isEmpty))
            }
        }
        .padding(14)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(Color.primary.opacity(0.08), lineWidth: 1)
        )
    }

    private func footprintBadge(_ text: String) -> some View {
        Text(text)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(store.activeTheme.primaryColor.opacity(0.10))
            .foregroundStyle(store.activeTheme.primaryColor)
            .clipShape(Capsule())
    }

    private var historySection: some View {
        let list = overview?.timeline ?? []
        return VStack(alignment: .leading, spacing: 10) {
            Text("🕰️ 历史日记流（一日一页 · 共 \(list.count) 篇）")
                .font(.subheadline.bold())

            if list.isEmpty {
                Text("还没有历史日记，点击上方「✨ AI 今日小结」开启第一篇每日复盘吧。")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 8)
            } else {
                ForEach(list) { entry in
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Text("📅 \(entry.journalDate ?? String(entry.eventTime.prefix(10)))")
                                .font(.subheadline.bold())
                            if let mm = entry.moodMeta {
                                Text("\(mm.icon) \(mm.label)")
                                    .font(.caption2.bold())
                                    .padding(.horizontal, 7)
                                    .padding(.vertical, 2)
                                    .background(store.activeTheme.primaryColor.opacity(0.12))
                                    .clipShape(Capsule())
                            }
                            if let lunar = entry.lunarLabel, !lunar.isEmpty {
                                Text(lunar)
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("切换到此日") {
                                if let jd = entry.journalDate, !jd.isEmpty {
                                    selectedDate = jd
                                    Task { await reloadJournal() }
                                }
                            }
                            .font(.caption)
                            .buttonStyle(.borderless)
                        }

                        MarkdownAnswerView(text: entry.content)

                        if let m = entry.media, !m.isEmpty {
                            MediaThumbnailGridView(media: m)
                        }
                    }
                    .padding(12)
                    .background(.background)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .stroke(Color.primary.opacity(0.07), lineWidth: 1)
                    )
                }
            }
        }
    }

    private func shiftDay(_ delta: Int) {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "Asia/Shanghai") ?? .current
        let cur = f.date(from: selectedDate) ?? Date()
        if let next = Calendar(identifier: .gregorian).date(byAdding: .day, value: delta, to: cur) {
            selectedDate = f.string(from: next)
            Task { await reloadJournal() }
        }
    }

    private func triggerAISummary(withDream: Bool) {
        isBusy = true
        Task {
            defer { isBusy = false }
            if let res = try? await store.client.generateJournalAISummary(
                date: selectedDate,
                draftContent: draftContent,
                triggerDream: withDream
            ) {
                let clean = draftContent.trimmingCharacters(in: .whitespacesAndNewlines)
                draftContent = clean.isEmpty ? res.summaryMarkdown : "\(clean)\n\n\(res.summaryMarkdown)"
                store.showBanner(withDream ? "🌙 已生成今日小结并完成 Dream 归纳" : "✨ 已汇总今日待办与专注时长")
            }
        }
    }

    private func saveJournal() {
        isBusy = true
        dismissAppKeyboard()
        let body: [String: Any] = [
            "journal_date": selectedDate,
            "mood": selectedMood,
            "content": draftContent.trimmingCharacters(in: .whitespacesAndNewlines),
            "extract_memory": extractMemory,
            "media_ids": pendingMedia.map(\.mediaId)
        ]
        Task {
            defer { isBusy = false }
            _ = try? await store.client.saveJournal(body: body)
            pendingMedia = []
            store.showBanner("📖 \(selectedDate) 日记已保存")
            await reloadJournal()
        }
    }

    private func reloadJournal() async {
        if let ov = try? await store.client.fetchJournalOverview(date: selectedDate, limit: 30) {
            overview = ov
            if let cur = ov.currentEntry {
                draftContent = cur.content
                selectedMood = cur.mood ?? "calm"
                extractMemory = cur.extractMemory ?? true
            } else {
                draftContent = ""
                selectedMood = "calm"
                extractMemory = true
            }
        }
    }
}
