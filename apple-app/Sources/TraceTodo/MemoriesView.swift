import SwiftUI

enum MemorySheetTab: String, CaseIterable, Identifiable {
    case edit = "✏️ 编辑"
    case markdown = "📝 Markdown"
    case provenance = "📜 溯源"

    var id: String { rawValue }
}

struct MemoriesAndAskHostView: View {
    @EnvironmentObject var store: TodoStore
    @State private var mode: Int = 1  // Default to 1 = 🕸️ 双链星图 (matches Web optimization)

    var body: some View {
        VStack(spacing: 0) {
            Picker("模式", selection: $mode) {
                Text("🕸️ 双链星图").tag(1)
                Text("🗂️ 记忆库").tag(0)
                Text("💬 证据问答").tag(2)
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(store.cardSurfaceBackground.opacity(0.75))

            Divider()

            if mode == 0 {
                MemoriesView()
            } else if mode == 1 {
                KnowledgeGraphCanvasView()
            } else {
                AskMemoryView()
            }
        }
    }
}

struct MemoriesView: View {
    @EnvironmentObject var store: TodoStore
    @State private var query: String = ""
    @State private var selectedType: String = ""
    @State private var hits: [MemorySearchHitModel] = []
    @State private var isLoading: Bool = false
    @State private var errorMsg: String?
    @State private var selectedMemoryTarget: MemoryItemModel?
    @State private var isPresentingNewMemory: Bool = false

    private let typeOptions: [(code: String, label: String)] = [
        ("", "全部"),
        ("semantic", "语义事实"),
        ("preference", "个人偏好"),
        ("episodic", "情景经历"),
        ("task", "任务")
    ]

    var body: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 10) {
                    HStack(spacing: 8) {
                        Image(systemName: "magnifyingglass")
                            .foregroundStyle(.purple)
                        TextField("搜索记忆（支持语义召回与关键词）...", text: $query)
                            .textFieldStyle(.plain)
                            .submitLabel(.search)
                            #if os(iOS)
                            .textInputAutocapitalization(.never)
                            #endif
                            .onSubmit {
                                dismissAppKeyboard()
                                Task { await loadMemories() }
                            }

                        if !query.isEmpty {
                            Button {
                                query = ""
                                Task { await loadMemories() }
                            } label: {
                                Image(systemName: "xmark.circle.fill")
                                    .foregroundStyle(.secondary)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(.background)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .stroke(Color.purple.opacity(0.28), lineWidth: 1)
                    )

                    Button {
                        Task { await loadMemories() }
                    } label: {
                        Text(isLoading ? "检索中..." : "检索")
                            .font(.subheadline.bold())
                            .padding(.horizontal, 12)
                            .padding(.vertical, 8)
                            .background(Color.blue.opacity(0.14))
                            .foregroundStyle(.blue)
                            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    }
                    .buttonStyle(.plain)
                    .contentShape(Rectangle())

                    Button {
                        isPresentingNewMemory = true
                    } label: {
                        Label("新建记忆", systemImage: "plus")
                            .font(.subheadline.bold())
                            .padding(.horizontal, 12)
                            .padding(.vertical, 8)
                            .background(
                                LinearGradient(
                                    colors: [Color(red: 0.20, green: 0.54, blue: 0.96), Color(red: 0.48, green: 0.35, blue: 0.97)],
                                    startPoint: .leading,
                                    endPoint: .trailing
                                )
                            )
                            .foregroundStyle(.white)
                            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    }
                    .buttonStyle(.plain)
                    .contentShape(Rectangle())
                }

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(typeOptions, id: \.code) { opt in
                            let isSel = selectedType == opt.code
                            Button {
                                selectedType = opt.code
                                Task { await loadMemories() }
                            } label: {
                                Text(opt.label)
                                    .font(.caption.weight(isSel ? .bold : .medium))
                                    .padding(.horizontal, 12)
                                    .padding(.vertical, 6)
                                    .background(isSel ? Color(red: 0.18, green: 0.22, blue: 0.31) : Color.primary.opacity(0.06))
                                    .foregroundStyle(isSel ? .white : .primary)
                                    .clipShape(Capsule())
                                    .contentShape(Capsule())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            .padding(14)

            Divider()

            if let err = errorMsg {
                Text(err)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .padding(10)
            }

            if isLoading && hits.isEmpty {
                Spacer()
                ProgressView("正在加载记忆库...")
                Spacer()
            } else if hits.isEmpty {
                ContentUnavailableView(
                    "暂无匹配的记忆",
                    systemImage: "brain.head.profile",
                    description: Text("点击右上角「新建记忆」手动录入，或在顶部输入一句话由 AI 自动抽取。")
                )
            } else {
                ScrollView {
                    LazyVStack(spacing: 10) {
                        ForEach(hits) { hit in
                            memoryRowCard(hit)
                        }
                    }
                    .padding(14)
                }
                .refreshable {
                    await loadMemories()
                }
            }
        }
        .iosScrollDismissesKeyboard()
        .task(id: "\(store.serverURL)|\(store.spaceId)") {
            await loadMemories()
        }
        .sheet(item: $selectedMemoryTarget) { target in
            MemoryEditorAndDetailSheet(
                memoryId: target.memoryId,
                initialTab: .edit,
                onChanged: {
                    Task {
                        await loadMemories()
                        await store.refresh()
                    }
                }
            )
            .environmentObject(store)
        }
        .sheet(isPresented: $isPresentingNewMemory) {
            NewMemorySheet(onCreated: {
                Task {
                    await loadMemories()
                    await store.refresh()
                }
            })
            .environmentObject(store)
        }
    }

    @ViewBuilder
    private func memoryRowCard(_ hit: MemorySearchHitModel) -> some View {
        let m = hit.memory
        Button {
            selectedMemoryTarget = m
        } label: {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Text(m.typeDisplayName)
                        .font(.caption2.bold())
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(m.typeTintColor.opacity(0.14))
                        .foregroundStyle(m.typeTintColor)
                        .clipShape(Capsule())

                    Text(m.title)
                        .font(.system(size: 14.5, weight: .semibold))
                        .foregroundStyle(.primary)
                        .lineLimit(1)

                    Spacer()

                    if m.userLocked {
                        Text("🔒 锁定")
                            .font(.caption2.bold())
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.orange.opacity(0.15))
                            .foregroundStyle(.orange)
                            .clipShape(Capsule())
                    }

                    Text("v\(m.version)")
                        .font(.caption2.bold())
                        .foregroundStyle(.secondary)
                }

                Text(m.summary)
                    .font(.system(size: 13))
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
                    .multilineTextAlignment(.leading)

                HStack(spacing: 8) {
                    Text(m.uri)
                        .font(.caption2)
                        .monospaced()
                        .foregroundStyle(.tertiary)
                        .lineLimit(1)

                    Spacer()

                    Text("✏️ 编辑 · 📝 MD")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.purple)
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.background)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(Color.primary.opacity(0.08), lineWidth: 1)
            )
            .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
        .buttonStyle(.plain)
    }

    func loadMemories() async {
        isLoading = true
        errorMsg = nil
        defer { isLoading = false }
        do {
            let res = try await store.client.searchMemories(query: query, memoryType: selectedType, topK: 50)
            self.hits = res.memories
        } catch {
            self.errorMsg = error.localizedDescription
        }
    }
}

struct MemoryEditorAndDetailSheet: View {
    @EnvironmentObject var store: TodoStore
    @Environment(\.dismiss) private var dismiss

    let memoryId: String
    @State var selectedTab: MemorySheetTab
    var onChanged: (() -> Void)? = nil

    @State private var detail: MemoryDetailResponse?
    @State private var loading: Bool = true
    @State private var saving: Bool = false
    @State private var statusMsg: String?
    @State private var confirmForget: Bool = false

    // Editable fields
    @State private var title: String = ""
    @State private var summary: String = ""
    @State private var tagsText: String = ""
    @State private var lockForUser: Bool = true

    // Type-specific fields
    @State private var semSubject: String = "user"
    @State private var semPredicate: String = ""
    @State private var semValue: String = ""
    @State private var prefTarget: String = ""
    @State private var prefPolarity: String = "positive"
    @State private var prefConditions: String = ""
    @State private var prefExceptions: String = ""
    @State private var epiSummary: String = ""
    @State private var epiOccurredAt: String = ""

    // Raw Markdown editor
    @State private var markdownText: String = ""

    init(memoryId: String, initialTab: MemorySheetTab = .edit, onChanged: (() -> Void)? = nil) {
        self.memoryId = memoryId
        _selectedTab = State(initialValue: initialTab)
        self.onChanged = onChanged
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                Picker("视图", selection: $selectedTab) {
                    ForEach(MemorySheetTab.allCases) { tab in
                        Text(tab.rawValue).tag(tab)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)

                if let msg = statusMsg {
                    Text(msg)
                        .font(.caption.weight(.medium))
                        .foregroundStyle(msg.contains("失败") ? .red : .green)
                        .padding(.horizontal, 16)
                        .padding(.bottom, 6)
                }

                Divider()

                if loading {
                    Spacer()
                    ProgressView("正在读取记忆详情...")
                    Spacer()
                } else if let mem = detail?.memory {
                    Group {
                        switch selectedTab {
                        case .edit:
                            editFormView(mem)
                        case .markdown:
                            markdownEditorView(mem)
                        case .provenance:
                            provenanceView(mem)
                        }
                    }
                } else {
                    ContentUnavailableView("无法读取记忆", systemImage: "exclamationmark.triangle")
                }
            }
            .navigationTitle(detail?.memory.map { "\($0.title) (v\($0.version))" } ?? "记忆详情")
            .iosKeyboardDoneToolbar()
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
            }
            .confirmationDialog("确认彻底遗忘此记忆？", isPresented: $confirmForget, titleVisibility: .visible) {
                Button("确认彻底遗忘（同步清理关联证据与索引）", role: .destructive) {
                    Task { await performForget() }
                }
                Button("取消", role: .cancel) {}
            } message: {
                Text("将同步擦除该记忆的历史明文、仅关联此记忆的原始证据、向量/全文索引及衍生洞察，且不可恢复。")
            }
        }
        #if os(macOS)
        .frame(minWidth: 580, minHeight: 520)
        #endif
        .task {
            await loadDetail()
        }
    }

    @ViewBuilder
    private func editFormView(_ mem: MemoryItemModel) -> some View {
        Form {
            Section("基本信息 · \(mem.typeDisplayName) (v\(mem.version))") {
                TextField("标题", text: $title)
                VStack(alignment: .leading, spacing: 4) {
                    Text("摘要 Summary").font(.caption).foregroundStyle(.secondary)
                    TextEditor(text: $summary)
                        .frame(minHeight: 72)
                }
                TextField("标签（逗号分隔）", text: $tagsText)
            }

            if mem.memoryType == "semantic" {
                Section("语义事实字段") {
                    TextField("主体 Subject", text: $semSubject)
                    TextField("谓词 Predicate", text: $semPredicate)
                    TextField("事实值 Value", text: $semValue)
                }
            } else if mem.memoryType == "preference" {
                Section("偏好字段") {
                    TextField("偏好对象 Target", text: $prefTarget)
                    Picker("极性", selection: $prefPolarity) {
                        Text("正面偏好 (positive)").tag("positive")
                        Text("负面回避 (negative)").tag("negative")
                        Text("视条件而定 (conditional)").tag("conditional")
                    }
                    TextField("适用条件（分号分隔）", text: $prefConditions)
                    TextField("例外限制（分号分隔）", text: $prefExceptions)
                }
            } else if mem.memoryType == "episodic" {
                Section("情景经历字段") {
                    TextField("事件摘要", text: $epiSummary)
                    TextField("发生时间", text: $epiOccurredAt)
                }
            }

            Section("保护与操作") {
                Toggle("🔒 用户锁定保护（防 AI 自动覆盖）", isOn: $lockForUser)

                Button {
                    Task { await saveFormChanges(mem) }
                } label: {
                    HStack {
                        Spacer()
                        Text(saving ? "保存中..." : "💾 保存修改 (CAS v\(mem.version))")
                            .fontWeight(.semibold)
                        Spacer()
                    }
                }
                .disabled(
                    saving || (
                        title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        && summary.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    )
                )

                Button(role: .destructive) {
                    confirmForget = true
                } label: {
                    HStack {
                        Spacer()
                        Text("🗑️ 遗忘此记忆")
                        Spacer()
                    }
                }
                .disabled(
                    saving || (
                        title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        && summary.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    )
                )
            }
        }
        .formStyle(.grouped)
        .iosScrollDismissesKeyboard()
    }

    @ViewBuilder
    private func markdownEditorView(_ mem: MemoryItemModel) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("直接查看 / 编辑 Obsidian 格式 Markdown（保存后自动 CAS 回流）")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Text("\(mem.permalink).md")
                    .font(.caption2)
                    .monospaced()
                    .foregroundStyle(.secondary)
            }

            TextEditor(text: $markdownText)
                .font(.system(size: 12.5, design: .monospaced))
                .padding(8)
                .background(Color.primary.opacity(0.04))
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .stroke(Color.primary.opacity(0.1), lineWidth: 1)
                )

            HStack {
                Spacer()
                Button {
                    Task { await saveMarkdown(mem) }
                } label: {
                    Text(saving ? "同步中..." : "💾 保存 Markdown 并回流")
                        .font(.subheadline.bold())
                        .padding(.horizontal, 16)
                        .padding(.vertical, 9)
                        .background(Color.purple)
                        .foregroundStyle(.white)
                        .clipShape(Capsule())
                }
                .buttonStyle(.plain)
                .disabled(saving || markdownText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(16)
    }

    @ViewBuilder
    private func provenanceView(_ mem: MemoryItemModel) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(mem.uri).font(.caption).monospaced().foregroundStyle(.secondary)
                    Text(mem.summary).font(.subheadline)
                }

                Divider()
                Text("📝 支撑原话证据 (RawNotes)").font(.subheadline.bold())

                let sources = detail?.sources ?? []
                if sources.isEmpty {
                    Text("暂无关联原话").font(.caption).foregroundStyle(.secondary)
                } else {
                    ForEach(sources) { src in
                        VStack(alignment: .leading, spacing: 4) {
                            Text("“\(src.visibleContent)”").font(.callout)
                            Text("\(src.noteId) (rev \(src.citedRevision)) · \(String(src.eventTime.prefix(19)))")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.primary.opacity(0.04))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                }

                let history = detail?.history ?? []
                if !history.isEmpty {
                    Divider()
                    Text("📜 版本演进链").font(.subheadline.bold())
                    ForEach(history) { item in
                        HStack(alignment: .top) {
                            Text("v\(item.version)")
                                .font(.caption.bold())
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(Color.accentColor.opacity(0.15))
                                .clipShape(Capsule())
                            VStack(alignment: .leading, spacing: 2) {
                                Text(item.summary).font(.caption)
                                Text("\(item.origin) · \(item.changeReason)")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            .padding(16)
        }
    }

    private func loadDetail() async {
        loading = true
        defer { loading = false }
        do {
            let d = try await store.client.fetchMemoryDetail(memoryId: memoryId)
            self.detail = d
            if let m = d.memory {
                self.title = m.title
                self.summary = m.summary
                self.tagsText = (m.tags ?? []).joined(separator: ", ")
                self.semSubject = m.stringField("subject", default: "user")
                self.semPredicate = m.stringField("predicate")
                self.semValue = m.stringField("value")
                self.prefTarget = m.stringField("target")
                self.prefPolarity = m.stringField("polarity", default: "positive")
                self.prefConditions = m.stringListField("conditions").joined(separator: "；")
                self.prefExceptions = m.stringListField("exceptions").joined(separator: "；")
                self.epiSummary = m.stringField("event_summary", default: m.summary)
                self.epiOccurredAt = m.stringField("occurred_at", default: m.eventTime ?? "")
                self.lockForUser = m.userLocked
            }
            self.markdownText = d.markdown ?? ""
        } catch {
            self.statusMsg = "读取失败：\(error.localizedDescription)"
        }
    }

    private func saveFormChanges(_ mem: MemoryItemModel) async {
        let cleanTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanSummary = summary.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanTitle.isEmpty || !cleanSummary.isEmpty else {
            statusMsg = "标题和摘要不能同时为空"
            return
        }
        saving = true
        defer { saving = false }
        var payload = mem.rawPayloadDict
        if mem.memoryType == "semantic" {
            payload["subject"] = semSubject.isEmpty ? "user" : semSubject
            payload["predicate"] = semPredicate
            payload["value"] = semValue
        } else if mem.memoryType == "preference" {
            payload["target"] = prefTarget
            payload["polarity"] = prefPolarity
            payload["conditions"] = prefConditions
                .components(separatedBy: CharacterSet(charactersIn: ";；"))
                .map { $0.trimmingCharacters(in: .whitespaces) }
                .filter { !$0.isEmpty }
            payload["exceptions"] = prefExceptions
                .components(separatedBy: CharacterSet(charactersIn: ";；"))
                .map { $0.trimmingCharacters(in: .whitespaces) }
                .filter { !$0.isEmpty }
        } else if mem.memoryType == "episodic" {
            payload["event_summary"] = epiSummary
            payload["occurred_at"] = epiOccurredAt
        }
        let tags = tagsText
            .components(separatedBy: CharacterSet(charactersIn: ",，"))
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }

        do {
            let res = try await store.client.proposeMemoryChange(body: [
                "action": "update",
                "memory_type": mem.memoryType,
                "target_memory_id": mem.memoryId,
                "expected_version": mem.version,
                "title": title.trimmingCharacters(in: .whitespacesAndNewlines),
                "summary": summary.trimmingCharacters(in: .whitespacesAndNewlines),
                "payload": payload,
                "tags": tags,
                "lock_for_user": lockForUser,
                "reason": "App 手动编辑记忆"
            ])
            statusMsg = "✅ 已保存至 v\(res.memory?.version ?? mem.version + 1)"
            await loadDetail()
            onChanged?()
        } catch {
            statusMsg = "保存失败：\(error.localizedDescription)"
        }
    }

    private func saveMarkdown(_ mem: MemoryItemModel) async {
        saving = true
        defer { saving = false }
        do {
            let res = try await store.client.saveMemoryMarkdown(
                memoryId: mem.memoryId,
                expectedVersion: mem.version,
                markdown: markdownText
            )
            statusMsg = "✅ Markdown 回流成功，已升级至 v\(res.memory?.version ?? mem.version + 1)"
            await loadDetail()
            onChanged?()
        } catch {
            statusMsg = "保存失败：\(error.localizedDescription)"
        }
    }

    private func performForget() async {
        guard let mem = detail?.memory else { return }
        saving = true
        defer { saving = false }
        do {
            _ = try await store.client.proposeMemoryChange(body: [
                "action": "forget",
                "target_memory_id": mem.memoryId,
                "expected_version": mem.version,
                "reason": "App 手动遗忘记忆"
            ])
            onChanged?()
            dismiss()
        } catch {
            statusMsg = "遗忘失败：\(error.localizedDescription)"
        }
    }
}

struct NewMemorySheet: View {
    @EnvironmentObject var store: TodoStore
    @Environment(\.dismiss) private var dismiss
    var initialTitle: String = ""
    var onCreated: (() -> Void)? = nil

    @State private var memoryType: String = "semantic"
    @State private var title: String = ""
    @State private var summary: String = ""
    @State private var extraKey: String = ""
    @State private var extraVal: String = ""
    @State private var tagsText: String = ""
    @State private var saving: Bool = false
    @State private var errorMsg: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("新建记忆类型") {
                    Picker("类型", selection: $memoryType) {
                        Text("语义事实 (semantic)").tag("semantic")
                        Text("个人偏好 (preference)").tag("preference")
                        Text("情景经历 (episodic)").tag("episodic")
                    }
                    TextField("标题（如：常驻城市 / 咖啡习惯）", text: $title)
                    TextField("摘要 Summary（支持 [[双链]]）", text: $summary, axis: .vertical)
                        .lineLimit(2...5)
                }

                Section("补充属性") {
                    if memoryType == "semantic" {
                        TextField("属性键 Predicate（如 residence）", text: $extraKey)
                        TextField("属性值 Value（如 上海）", text: $extraVal)
                    } else if memoryType == "preference" {
                        TextField("偏好对象 Target（如 美式咖啡）", text: $extraKey)
                        TextField("例外限制（分号分隔）", text: $extraVal)
                    } else {
                        TextField("事件经过 Event Summary", text: $extraVal)
                    }
                    TextField("标签（逗号分隔）", text: $tagsText)
                }

                if let err = errorMsg {
                    Text(err).font(.caption).foregroundStyle(.red)
                }
            }
            .formStyle(.grouped)
            .iosScrollDismissesKeyboard()
            .iosKeyboardDoneToolbar()
            .navigationTitle("✨ 新建长期记忆")
            .onAppear {
                if title.isEmpty && !initialTitle.isEmpty {
                    title = initialTitle
                }
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(saving ? "创建中..." : "创建") {
                        Task { await createMemory() }
                    }
                    .disabled(saving || title.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
        #if os(macOS)
        .frame(minWidth: 480, minHeight: 420)
        #endif
    }

    private func createMemory() async {
        saving = true
        defer { saving = false }
        let t = title.trimmingCharacters(in: .whitespacesAndNewlines)
        let s = summary.trimmingCharacters(in: .whitespacesAndNewlines)
        let finalSummary = s.isEmpty ? t : s

        var payload: [String: Any] = [:]
        if memoryType == "semantic" {
            payload = [
                "subject": "user",
                "predicate": extraKey.isEmpty ? "fact" : extraKey,
                "value": extraVal.isEmpty ? finalSummary : extraVal
            ]
        } else if memoryType == "preference" {
            payload = [
                "target": extraKey.isEmpty ? t : extraKey,
                "polarity": "positive",
                "conditions": [],
                "exceptions": extraVal
                    .components(separatedBy: CharacterSet(charactersIn: ";；"))
                    .map { $0.trimmingCharacters(in: .whitespaces) }
                    .filter { !$0.isEmpty }
            ]
        } else {
            payload = ["event_summary": extraVal.isEmpty ? finalSummary : extraVal]
        }

        let tags = tagsText
            .components(separatedBy: CharacterSet(charactersIn: ",，"))
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }

        do {
            _ = try await store.client.proposeMemoryChange(body: [
                "action": "create",
                "memory_type": memoryType,
                "title": t,
                "summary": finalSummary,
                "payload": payload,
                "tags": tags,
                "lock_for_user": true,
                "reason": "App 手动新建记忆"
            ])
            onCreated?()
            dismiss()
        } catch {
            errorMsg = error.localizedDescription
        }
    }
}

// MARK: - Obsidian-Style Force-Directed Knowledge Graph Canvas (SwiftUI Native)

private struct IdentifiableMemoryId: Identifiable {
    let id: String
}

private struct GhostCreationTarget: Identifiable {
    let id = UUID()
    let title: String
}

struct KnowledgeGraphCanvasView: View {
    @EnvironmentObject var store: TodoStore
    @State private var seedText: String = ""
    @State private var selectedType: String = ""
    @State private var selectedSubject: String = ""
    @State private var includeSemantic: Bool = true
    @State private var isLightCanvas: Bool = false
    @State private var isLoading: Bool = false
    @State private var errorMsg: String?

    @State private var nodes: [GraphCanvasNodeModel] = []
    @State private var edges: [GraphCanvasEdgeModel] = []
    @State private var stats: GraphCanvasStatsModel?

    @State private var positions: [String: CGPoint] = [:]
    @State private var velocities: [String: CGPoint] = [:]
    @State private var alpha: Double = 0.0
    @State private var camOffset: CGSize = .zero
    @State private var zoomScale: CGFloat = 1.0
    @State private var baseZoomScale: CGFloat = 1.0
    @State private var canvasSize: CGSize = CGSize(width: 600, height: 480)

    @State private var selectedNodeId: String?
    @State private var draggingNodeId: String?
    @State private var dragStartCam: CGSize = .zero
    @State private var isGestureActive: Bool = false

    @State private var detailSheetTarget: IdentifiableMemoryId?
    @State private var ghostCreateTarget: GhostCreationTarget?

    private let physicsTimer = Timer.publish(every: 1.0 / 60.0, on: .main, in: .common).autoconnect()

    private let typeChips: [(code: String, label: String)] = [
        ("", "全部"),
        ("task", "任务"),
        ("semantic", "事实"),
        ("preference", "偏好"),
        ("episodic", "情景"),
        ("dream", "Dream✦")
    ]

    var body: some View {
        VStack(spacing: 0) {
            filterHeader
            Divider()
            ZStack(alignment: .bottom) {
                graphStage
                if let selNode = nodes.first(where: { $0.id == selectedNodeId }) {
                    inspectorCard(for: selNode)
                        .padding(12)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }
            }
        }
        .task(id: "\(store.serverURL)|\(store.spaceId)") {
            await reloadGraph()
        }
        .onReceive(physicsTimer) { _ in
            guard alpha > 0.005 || draggingNodeId != nil else { return }
            stepPhysics()
        }
        .sheet(item: $detailSheetTarget) { item in
            MemoryEditorAndDetailSheet(
                memoryId: item.id,
                initialTab: .provenance,
                onChanged: {
                    Task {
                        await reloadGraph()
                        await store.refresh()
                    }
                }
            )
            .environmentObject(store)
        }
        .sheet(item: $ghostCreateTarget) { ghost in
            NewMemorySheet(initialTitle: ghost.title) {
                Task {
                    await reloadGraph()
                    await store.refresh()
                }
            }
            .environmentObject(store)
        }
    }

    private var filterHeader: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                HStack(spacing: 6) {
                    Image(systemName: "point.3.connected.trianglepath.dotted")
                        .foregroundStyle(.purple)
                    TextField("输入种子 ID / 链接 / 关键词聚焦子图（留空全局）...", text: $seedText)
                        .textFieldStyle(.plain)
                        .onSubmit { Task { await reloadGraph() } }
                    if !seedText.isEmpty {
                        Button {
                            seedText = ""
                            Task { await reloadGraph() }
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .foregroundStyle(.secondary)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 7)
                .background(.background)
                .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 9, style: .continuous)
                        .stroke(Color.purple.opacity(0.25), lineWidth: 1)
                )

                Button {
                    Task { await reloadGraph() }
                } label: {
                    Text(isLoading ? "加载..." : "刷新")
                        .font(.caption.bold())
                        .padding(.horizontal, 11)
                        .padding(.vertical, 8)
                        .background(Color.blue.opacity(0.14))
                        .foregroundStyle(.blue)
                        .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
                }
                .buttonStyle(.plain)
            }

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 7) {
                    ForEach(typeChips, id: \.code) { opt in
                        let isSel = selectedType == opt.code
                        Button {
                            selectedType = opt.code
                            Task { await reloadGraph() }
                        } label: {
                            Text(opt.label)
                                .font(.caption.weight(isSel ? .bold : .medium))
                                .padding(.horizontal, 10)
                                .padding(.vertical, 5)
                                .background(isSel ? Color(red: 0.16, green: 0.20, blue: 0.30) : Color.primary.opacity(0.06))
                                .foregroundStyle(isSel ? .white : .primary)
                                .clipShape(Capsule())
                        }
                        .buttonStyle(.plain)
                    }

                    if let subjects = stats?.subjects, subjects.count > 1 {
                        Menu {
                            Button("全部主体") {
                                selectedSubject = ""
                                Task { await reloadGraph() }
                            }
                            ForEach(subjects, id: \.self) { subj in
                                Button(subj == "self" ? "本人 (self)" : subj) {
                                    selectedSubject = subj
                                    Task { await reloadGraph() }
                                }
                            }
                        } label: {
                            Label(selectedSubject.isEmpty ? "主体" : (selectedSubject == "self" ? "本人" : selectedSubject), systemImage: "person.crop.circle")
                                .font(.caption.weight(.medium))
                                .padding(.horizontal, 10)
                                .padding(.vertical, 5)
                                .background(Color.primary.opacity(0.06))
                                .clipShape(Capsule())
                        }
                    }

                    Toggle(isOn: $includeSemantic) {
                        Text("语义虚线")
                            .font(.caption)
                    }
                    .toggleStyle(.button)
                    .controlSize(.small)
                    .onChange(of: includeSemantic) { _, _ in
                        Task { await reloadGraph() }
                    }
                }
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    private var graphStage: some View {
        GeometryReader { geo in
            ZStack {
                LinearGradient(
                    colors: isLightCanvas
                        ? [Color(red: 0.98, green: 0.99, blue: 1.0), Color(red: 0.94, green: 0.96, blue: 0.98)]
                        : [Color(red: 0.07, green: 0.10, blue: 0.18), Color(red: 0.04, green: 0.06, blue: 0.10)],
                    startPoint: .top,
                    endPoint: .bottom
                )

                Canvas { context, size in
                    drawGraph(in: &context, size: size)
                }
                .gesture(canvasDragGesture(in: geo.size))
                .simultaneousGesture(
                    MagnifyGesture()
                        .onChanged { val in
                            zoomScale = min(3.2, max(0.32, baseZoomScale * val.magnification))
                        }
                        .onEnded { _ in
                            baseZoomScale = zoomScale
                        }
                )

                if nodes.isEmpty && !isLoading {
                    VStack(spacing: 8) {
                        Image(systemName: "point.3.connected.trianglepath.dotted")
                            .font(.system(size: 30))
                            .foregroundStyle(.secondary)
                        Text(errorMsg ?? "当前空间暂无可展示的图谱节点")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }

                // Top-left HUD pill
                VStack {
                    HStack {
                        if let st = stats {
                            Text("节点 \(st.totalNodes) · 双链 \(st.wikilinkEdges) · Dream \(st.derivedEdges) · 语义 \(st.semanticEdges)")
                                .font(.system(size: 11, weight: .semibold))
                                .padding(.horizontal, 10)
                                .padding(.vertical, 5)
                                .background(isLightCanvas ? Color.white.opacity(0.88) : Color.black.opacity(0.55))
                                .foregroundStyle(isLightCanvas ? Color.primary : Color.white)
                                .clipShape(Capsule())
                        }
                        Spacer()
                    }
                    Spacer()
                    HStack(spacing: 6) {
                        hudButton("+") {
                            zoomScale = min(3.2, zoomScale * 1.25)
                            baseZoomScale = zoomScale
                        }
                        hudButton("−") {
                            zoomScale = max(0.32, zoomScale * 0.8)
                            baseZoomScale = zoomScale
                        }
                        hudButton("⟲") {
                            camOffset = .zero
                            zoomScale = 1.0
                            baseZoomScale = 1.0
                        }
                        hudButton("⚡") {
                            alpha = 0.85
                        }
                        hudButton(isLightCanvas ? "🌙" : "☀️") {
                            isLightCanvas.toggle()
                        }
                        Spacer()
                    }
                }
                .padding(12)
            }
            .onAppear { canvasSize = geo.size }
            .onChange(of: geo.size) { _, newSize in canvasSize = newSize }
        }
    }

    private func hudButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 13, weight: .bold))
                .frame(width: 32, height: 32)
                .background(isLightCanvas ? Color.white.opacity(0.92) : Color(red: 0.10, green: 0.14, blue: 0.24).opacity(0.9))
                .foregroundStyle(isLightCanvas ? Color.primary : Color.white)
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .stroke(Color.gray.opacity(0.28), lineWidth: 1)
                )
        }
        .buttonStyle(.plain)
    }

    private func inspectorCard(for node: GraphCanvasNodeModel) -> some View {
        let neighborEdges = edges.filter { $0.source == node.id || $0.target == node.id }
        return VStack(alignment: .leading, spacing: 8) {
            HStack {
                Circle()
                    .fill(colorForNode(node))
                    .frame(width: 9, height: 9)
                Text(node.title)
                    .font(.subheadline.bold())
                    .lineLimit(1)
                Spacer()
                Text(node.isGhost ? "悬空引用" : "\(node.memoryType) · v\(node.version)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Button {
                    withAnimation(.easeOut(duration: 0.15)) { selectedNodeId = nil }
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
            }

            Text(node.summary)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(2)

            if !neighborEdges.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(neighborEdges) { edge in
                            let otherId = edge.source == node.id ? edge.target : edge.source
                            if let other = nodes.first(where: { $0.id == otherId }) {
                                Button {
                                    selectedNodeId = other.id
                                    if let p = positions[other.id] {
                                        camOffset = CGSize(width: -p.x * zoomScale, height: -p.y * zoomScale)
                                    }
                                } label: {
                                    Text("↔ \(other.title)")
                                        .font(.caption2.weight(.medium))
                                        .padding(.horizontal, 8)
                                        .padding(.vertical, 4)
                                        .background(Color.blue.opacity(0.14))
                                        .foregroundStyle(.blue)
                                        .clipShape(Capsule())
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                }
            }

            HStack(spacing: 8) {
                if node.isGhost {
                    Button {
                        ghostCreateTarget = GhostCreationTarget(title: node.title)
                    } label: {
                        Label("立即创建此节点并自动连线", systemImage: "sparkles")
                            .font(.caption.bold())
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                } else {
                    Button {
                        seedText = node.memoryId
                        Task { await reloadGraph() }
                    } label: {
                        Label("聚焦子图", systemImage: "scope")
                            .font(.caption.bold())
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)

                    Button {
                        detailSheetTarget = IdentifiableMemoryId(id: node.memoryId)
                    } label: {
                        Label("原文溯源与编辑", systemImage: "doc.text.magnifyingglass")
                            .font(.caption.bold())
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                }
            }
        }
        .padding(12)
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .shadow(color: .black.opacity(0.18), radius: 12, y: 4)
    }

    private func colorForNode(_ n: GraphCanvasNodeModel) -> Color {
        if n.isGhost { return Color.gray }
        if n.assertionKind == "inferred" { return Color(red: 0.93, green: 0.28, blue: 0.60) }
        switch n.memoryType {
        case "task":
            return n.taskStatus == "done"
                ? Color(red: 0.06, green: 0.73, blue: 0.51)
                : Color(red: 0.23, green: 0.51, blue: 0.96)
        case "semantic":
            return Color(red: 0.55, green: 0.36, blue: 0.96)
        case "preference":
            return Color(red: 0.96, green: 0.62, blue: 0.04)
        case "episodic":
            return Color(red: 0.08, green: 0.72, blue: 0.65)
        default:
            return Color.indigo
        }
    }

    private func radiusForNode(_ n: GraphCanvasNodeModel) -> CGFloat {
        if n.isGhost { return 6.5 }
        return min(21.0, 7.2 + CGFloat(sqrt(Double(max(0, n.degree)))) * 3.0)
    }

    private func worldToScreen(_ pt: CGPoint, in size: CGSize) -> CGPoint {
        CGPoint(
            x: size.width * 0.5 + camOffset.width + pt.x * zoomScale,
            y: size.height * 0.5 + camOffset.height + pt.y * zoomScale
        )
    }

    private func screenToWorld(_ pt: CGPoint, in size: CGSize) -> CGPoint {
        CGPoint(
            x: (pt.x - size.width * 0.5 - camOffset.width) / max(0.25, zoomScale),
            y: (pt.y - size.height * 0.5 - camOffset.height) / max(0.25, zoomScale)
        )
    }

    private func hitTestNode(at screenPt: CGPoint, in size: CGSize) -> GraphCanvasNodeModel? {
        let worldPt = screenToWorld(screenPt, in: size)
        let pad: CGFloat = 6.0 / max(0.4, zoomScale)
        for node in nodes.reversed() {
            guard let pos = positions[node.id] else { continue }
            let r = radiusForNode(node) + pad
            if hypot(worldPt.x - pos.x, worldPt.y - pos.y) <= r {
                return node
            }
        }
        return nil
    }

    private func canvasDragGesture(in size: CGSize) -> some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                if !isGestureActive {
                    isGestureActive = true
                    dragStartCam = camOffset
                    if let hit = hitTestNode(at: value.startLocation, in: size) {
                        draggingNodeId = hit.id
                        alpha = max(alpha, 0.32)
                    } else {
                        draggingNodeId = nil
                    }
                }
                if let dragId = draggingNodeId {
                    positions[dragId] = screenToWorld(value.location, in: size)
                    velocities[dragId] = .zero
                    alpha = max(alpha, 0.25)
                } else {
                    camOffset = CGSize(
                        width: dragStartCam.width + value.translation.width,
                        height: dragStartCam.height + value.translation.height
                    )
                }
            }
            .onEnded { value in
                let moved = hypot(value.translation.width, value.translation.height)
                if moved < 5 {
                    if let hit = hitTestNode(at: value.location, in: size) {
                        withAnimation(.easeOut(duration: 0.16)) {
                            selectedNodeId = hit.id
                        }
                    } else {
                        withAnimation(.easeOut(duration: 0.16)) {
                            selectedNodeId = nil
                        }
                    }
                }
                draggingNodeId = nil
                isGestureActive = false
            }
    }

    private func drawGraph(in context: inout GraphicsContext, size: CGSize) {
        let focusId = selectedNodeId
        var neighborSet = Set<String>()
        if let fid = focusId {
            for e in edges {
                if e.source == fid { neighborSet.insert(e.target) }
                if e.target == fid { neighborSet.insert(e.source) }
            }
        }

        // 1. Edges
        for edge in edges {
            guard let p1 = positions[edge.source], let p2 = positions[edge.target] else { continue }
            let s1 = worldToScreen(p1, in: size)
            let s2 = worldToScreen(p2, in: size)
            let isHighlighted = focusId != nil && (edge.source == focusId || edge.target == focusId)
            let isDimmed = focusId != nil && !isHighlighted

            var path = Path()
            path.move(to: s1)
            path.addLine(to: s2)

            let baseColor: Color = {
                switch edge.edgeKind {
                case "derived_from": return Color.pink
                case "wikilink": return Color.blue
                case "co_evidence": return Color.teal
                case "semantic": return Color.purple
                default: return Color.gray
                }
            }()
            let opacity = isDimmed ? 0.12 : (isHighlighted ? 0.92 : (edge.dashed ? 0.36 : 0.62))
            let style = StrokeStyle(
                lineWidth: isHighlighted ? 2.0 : 1.2,
                dash: edge.dashed ? [4, 4] : []
            )
            context.stroke(path, with: .color(baseColor.opacity(opacity)), style: style)
        }

        // 2. Nodes & Labels
        for node in nodes {
            guard let worldPos = positions[node.id] else { continue }
            let center = worldToScreen(worldPos, in: size)
            let r = radiusForNode(node) * zoomScale
            let isFocus = node.id == focusId
            let isNb = neighborSet.contains(node.id)
            let isDimmed = focusId != nil && !isFocus && !isNb
            let nodeOpacity = isDimmed ? 0.18 : 1.0
            let col = colorForNode(node)

            if isFocus || isNb {
                let haloRect = CGRect(x: center.x - r - 5, y: center.y - r - 5, width: (r + 5) * 2, height: (r + 5) * 2)
                context.fill(Path(ellipseIn: haloRect), with: .color(col.opacity(0.24 * nodeOpacity)))
            }

            let rect = CGRect(x: center.x - r, y: center.y - r, width: r * 2, height: r * 2)
            if node.isGhost {
                context.stroke(
                    Path(ellipseIn: rect),
                    with: .color(Color.gray.opacity(0.75 * nodeOpacity)),
                    style: StrokeStyle(lineWidth: 1.5, dash: [3, 3])
                )
            } else {
                context.fill(Path(ellipseIn: rect), with: .color(col.opacity(nodeOpacity)))
                context.stroke(
                    Path(ellipseIn: rect),
                    with: .color((isFocus ? Color.white : Color.black.opacity(0.35)).opacity(nodeOpacity)),
                    lineWidth: isFocus ? 2.2 : 1.0
                )
            }

            if isFocus || isNb || node.degree >= 2 || zoomScale >= 0.78 || nodes.count <= 32 {
                let displayTitle: String
                if isFocus {
                    var chunks: [String] = []
                    var current = ""
                    for ch in node.title {
                        current.append(ch)
                        if current.count >= 16 {
                            chunks.append(current)
                            current = ""
                        }
                    }
                    if !current.isEmpty { chunks.append(current) }
                    displayTitle = chunks.joined(separator: "\n")
                } else {
                    displayTitle = node.title.count > 16 ? String(node.title.prefix(16)) + "…" : node.title
                }
                let text = Text(displayTitle)
                    .font(.system(size: isFocus ? 12 : 11, weight: isFocus ? .bold : .medium))
                    .foregroundColor((isLightCanvas ? Color.black : Color.white).opacity(nodeOpacity))
                context.draw(text, at: CGPoint(x: center.x, y: center.y + r + 7), anchor: .top)
            }
        }
    }

    private func reloadGraph() async {
        isLoading = true
        errorMsg = nil
        defer { isLoading = false }
        do {
            let res = try await store.client.fetchGraphCanvas(
                seed: seedText.trimmingCharacters(in: .whitespacesAndNewlines),
                memoryType: selectedType,
                subject: selectedSubject,
                includeSemantic: includeSemantic
            )
            nodes = res.nodes
            edges = res.edges
            stats = res.stats

            var nextPos: [String: CGPoint] = [:]
            var nextVel: [String: CGPoint] = [:]
            for (idx, node) in res.nodes.enumerated() {
                if let existing = positions[node.id] {
                    nextPos[node.id] = existing
                    nextVel[node.id] = .zero
                } else {
                    let angle = Double(idx) * 2.39996323
                    let dist = 28.0 * sqrt(Double(idx + 1))
                    nextPos[node.id] = CGPoint(x: cos(angle) * dist, y: sin(angle) * dist)
                    nextVel[node.id] = .zero
                }
            }
            positions = nextPos
            velocities = nextVel
            if let sel = selectedNodeId, !res.nodes.contains(where: { $0.id == sel }) {
                selectedNodeId = nil
            }
            alpha = 0.92
        } catch {
            errorMsg = error.localizedDescription
        }
    }

    private func stepPhysics() {
        let count = nodes.count
        guard count > 0 else { alpha = 0; return }

        var pos = positions
        var vel = velocities
        let curAlpha = CGFloat(alpha)

        for i in 0..<count {
            let idA = nodes[i].id
            guard let pA = pos[idA] else { continue }
            var vA = vel[idA] ?? .zero
            if idA != draggingNodeId {
                vA.x -= pA.x * 0.015 * curAlpha
                vA.y -= pA.y * 0.015 * curAlpha
            }
            for j in (i + 1)..<count {
                let idB = nodes[j].id
                guard let pB = pos[idB] else { continue }
                var vB = vel[idB] ?? .zero
                var dx = pB.x - pA.x
                var dy = pB.y - pA.y
                var d2 = dx * dx + dy * dy
                if d2 < 1 {
                    dx = CGFloat(i % 2 == 0 ? 1 : -1)
                    dy = CGFloat(j % 2 == 0 ? 1 : -1)
                    d2 = 2
                }
                let dist = sqrt(d2)
                let rep = (1450.0 * curAlpha) / (d2 + 120.0)
                let fx = (dx / dist) * rep
                let fy = (dy / dist) * rep
                if idA != draggingNodeId { vA.x -= fx; vA.y -= fy }
                if idB != draggingNodeId { vB.x += fx; vB.y += fy }
                vel[idB] = vB
            }
            vel[idA] = vA
        }

        for edge in edges {
            guard let pS = pos[edge.source], let pT = pos[edge.target] else { continue }
            let dx = pT.x - pS.x
            let dy = pT.y - pS.y
            let dist = max(1.0, hypot(dx, dy))
            let rest: CGFloat = edge.edgeKind == "semantic" ? 140.0 : 105.0
            let k: CGFloat = (edge.edgeKind == "semantic" ? 0.028 : 0.052) * CGFloat(edge.weight) * curAlpha
            let f = (dist - rest) * k
            let fx = (dx / dist) * f
            let fy = (dy / dist) * f
            if edge.source != draggingNodeId {
                var vs = vel[edge.source] ?? .zero
                vs.x += fx; vs.y += fy
                vel[edge.source] = vs
            }
            if edge.target != draggingNodeId {
                var vt = vel[edge.target] ?? .zero
                vt.x -= fx; vt.y -= fy
                vel[edge.target] = vt
            }
        }

        for node in nodes {
            let nid = node.id
            if nid == draggingNodeId {
                vel[nid] = .zero
                continue
            }
            var v = vel[nid] ?? .zero
            var p = pos[nid] ?? .zero
            v.x *= 0.80
            v.y *= 0.80
            let sp = hypot(v.x, v.y)
            if sp > 16 {
                v.x = (v.x / sp) * 16
                v.y = (v.y / sp) * 16
            }
            p.x += v.x
            p.y += v.y
            vel[nid] = v
            pos[nid] = p
        }

        positions = pos
        velocities = vel
        if draggingNodeId == nil {
            alpha *= 0.968
        }
    }
}
