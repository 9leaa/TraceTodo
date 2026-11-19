import SwiftUI

enum MemorySheetTab: String, CaseIterable, Identifiable {
    case edit = "✏️ 编辑"
    case markdown = "📝 Markdown"
    case provenance = "📜 溯源"

    var id: String { rawValue }
}

struct MemoriesAndAskHostView: View {
    @EnvironmentObject var store: TodoStore
    @State private var mode: Int = 0  // 0 = 记忆库, 1 = 问答

    var body: some View {
        VStack(spacing: 0) {
            Picker("模式", selection: $mode) {
                Text("🗂️ 记忆库").tag(0)
                Text("💬 证据问答").tag(1)
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(Color.primary.opacity(0.03))

            Divider()

            if mode == 0 {
                MemoriesView()
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
                        Text("中性 (neutral)").tag("neutral")
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
                .disabled(saving)

                Button(role: .destructive) {
                    confirmForget = true
                } label: {
                    HStack {
                        Spacer()
                        Text("🗑️ 遗忘此记忆")
                        Spacer()
                    }
                }
                .disabled(saving)
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
            }
            self.markdownText = d.markdown ?? ""
        } catch {
            self.statusMsg = "读取失败：\(error.localizedDescription)"
        }
    }

    private func saveFormChanges(_ mem: MemoryItemModel) async {
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
