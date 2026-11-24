import SwiftUI
#if os(macOS)
import AppKit
#elseif os(iOS)
import UIKit
#endif

struct SortedTaskListView: View {
    @EnvironmentObject var store: TodoStore

    var body: some View {
        VStack(spacing: 0) {
            VStack(spacing: 10) {
                QuickCaptureBar()

                HStack {
                    Picker("排序", selection: $store.sortBy) {
                        ForEach(TaskSortOption.allCases) { opt in
                            Text(opt.label).tag(opt)
                        }
                    }
                    .pickerStyle(.segmented)
                    .onChange(of: store.sortBy) { _, _ in
                        Task { await store.refresh() }
                    }

                    Toggle("含已完成", isOn: $store.includeDone)
                        .toggleStyle(.switch)
                        .controlSize(.small)
                        .fixedSize()
                        .onChange(of: store.includeDone) { _, _ in
                            Task { await store.refresh() }
                        }
                }
            }
            .padding(14)

            Divider()

            let items = store.matrix?.sortedTasks ?? []
            if items.isEmpty {
                ContentUnavailableView(
                    "暂无待办事项",
                    systemImage: "checkmark.circle",
                    description: Text("在上方输入框输入任意一句话，或点击「新建任务」创建 DuoDo 彩色卡片。")
                )
            } else {
                List {
                    ForEach(items) { task in
                        TaskCardView(task: task)
                            .listRowSeparator(.hidden)
                            .listRowInsets(EdgeInsets(top: 5, leading: 14, bottom: 5, trailing: 14))
                            .swipeActions(edge: .leading, allowsFullSwipe: true) {
                                Button {
                                    Task {
                                        let act = task.payload.isDone ? "reopen_task" : "complete_task"
                                        await store.transitionTask(task, action: act)
                                    }
                                } label: {
                                    Label(task.payload.isDone ? "重开" : "完成", systemImage: "checkmark")
                                }
                                .tint(.green)
                            }
                            .swipeActions(edge: .trailing) {
                                if !task.payload.isDone {
                                    Button(role: .destructive) {
                                        Task { await store.transitionTask(task, action: "cancel_task") }
                                    } label: {
                                        Label("取消", systemImage: "trash")
                                    }
                                }
                            }
                    }
                }
                .listStyle(.plain)
                .iosScrollDismissesKeyboard()
                .refreshable {
                    await store.refresh()
                }
            }
        }
    }
}

struct SupervisionBoardView: View {
    @EnvironmentObject var store: TodoStore
    @State private var friendNickname: String = "监工好友"
    @State private var selectedAssignee: String = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("👥 好友监工小队 & 多人协作看板")
                                .font(.headline)
                            Text("仅展示开启「好友监督」或「多人任务」的事项；私人任务严格隔离不可见。")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("刷新看板") {
                            Task { await store.refreshSupervisionBoard(assignee: selectedAssignee) }
                        }
                    }

                    HStack(spacing: 10) {
                        TextField("我的昵称", text: $friendNickname)
                            .textFieldStyle(.roundedBorder)
                            .frame(maxWidth: 160)

                        if let assignees = store.supervisionBoard?.assignees, !assignees.isEmpty {
                            Picker("成员", selection: $selectedAssignee) {
                                Text("全部成员").tag("")
                                ForEach(assignees, id: \.self) { person in
                                    Text(person).tag(person)
                                }
                            }
                            .onChange(of: selectedAssignee) { _, newVal in
                                Task { await store.refreshSupervisionBoard(assignee: newVal) }
                            }
                        }
                    }

                    if let summary = store.supervisionBoard?.summary {
                        HStack(spacing: 16) {
                            VStack(alignment: .leading) {
                                Text("监督总数：\(summary.totalSupervised)")
                                    .font(.subheadline.bold())
                                Text("已完成 \(summary.doneSupervised) · 待推进 \(summary.todoSupervised)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text("\(Int(summary.completionRate))%")
                                .font(.title2.bold())
                                .foregroundStyle(.purple)
                        }
                        .padding(12)
                        .background(Color.purple.opacity(0.08))
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                    }
                }
                .padding(14)
                .background(.background)
                .clipShape(RoundedRectangle(cornerRadius: 14))
                .overlay(
                    RoundedRectangle(cornerRadius: 14)
                        .stroke(Color.primary.opacity(0.07), lineWidth: 1)
                )

                let tasks = store.supervisionBoard?.tasks ?? []
                if tasks.isEmpty {
                    ContentUnavailableView(
                        "暂无监督中的任务",
                        systemImage: "person.2.slash",
                        description: Text("在新建或编辑任务卡片中开启「好友监督」或切换为「多人任务」即可在此协作监督。")
                    )
                } else {
                    ForEach(tasks) { task in
                        VStack(alignment: .leading, spacing: 10) {
                            HStack(alignment: .top) {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(task.title)
                                        .font(.headline)
                                        .strikethrough(task.payload.isDone)
                                    HStack(spacing: 8) {
                                        Label(task.payload.assignee ?? "self", systemImage: "person.fill")
                                            .font(.caption)
                                            .foregroundStyle(.purple)
                                        if let subs = task.payload.subtasks, !subs.isEmpty {
                                            let doneC = subs.filter(\.done).count
                                            Text("清单 \(doneC)/\(subs.count)")
                                                .font(.caption)
                                                .foregroundStyle(.secondary)
                                        }
                                        if let rc = task.payload.rolloverCount, rc > 0 {
                                            Text("↻ 顺延 \(rc) 天")
                                                .font(.caption)
                                                .foregroundStyle(.orange)
                                        }
                                    }
                                }
                                Spacer()
                                Text(task.payload.isDone ? "已完成" : "进行中")
                                    .font(.caption.bold())
                                    .padding(.horizontal, 9)
                                    .padding(.vertical, 4)
                                    .background(task.payload.isDone ? Color.green.opacity(0.16) : Color.purple.opacity(0.16))
                                    .foregroundStyle(task.payload.isDone ? .green : .purple)
                                    .clipShape(Capsule())
                            }

                            if !task.payload.nudgeList.isEmpty {
                                VStack(alignment: .leading, spacing: 4) {
                                    ForEach(task.payload.nudgeList.suffix(3)) { n in
                                        Text("\(n.fromUser)：\(n.message)")
                                            .font(.caption)
                                            .padding(.horizontal, 8)
                                            .padding(.vertical, 4)
                                            .background(Color.primary.opacity(0.05))
                                            .clipShape(RoundedRectangle(cornerRadius: 8))
                                    }
                                }
                            }

                            HStack(spacing: 10) {
                                Button("⚡ 催一催") {
                                    Task { await store.sendNudge(to: task, kind: "nudge", fromUser: friendNickname) }
                                }
                                .buttonStyle(.bordered)

                                Button("👍 点赞鼓励") {
                                    Task { await store.sendNudge(to: task, kind: "cheer", fromUser: friendNickname) }
                                }
                                .buttonStyle(.borderedProminent)
                                .tint(.purple)

                                Spacer()

                                if !task.payload.isDone {
                                    Button("✅ 标记完成") {
                                        Task { await store.transitionTask(task, action: "complete_task") }
                                    }
                                    .buttonStyle(.bordered)
                                }
                            }
                        }
                        .padding(14)
                        .background(task.payload.themeKind.cardBackground.opacity(0.32))
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                        .overlay(
                            RoundedRectangle(cornerRadius: 14)
                                .stroke(Color.primary.opacity(0.08), lineWidth: 1)
                        )
                    }
                }
            }
            .padding(16)
        }
        .iosScrollDismissesKeyboard()
        .task {
            await store.refreshSupervisionBoard(assignee: selectedAssignee)
        }
        .refreshable {
            await store.refreshSupervisionBoard(assignee: selectedAssignee)
        }
    }
}

struct MarkdownAnswerView: View {
    let text: String

    private enum Block: Identifiable {
        case heading(id: Int, content: String)
        case numbered(id: Int, num: String, content: String)
        case bullet(id: Int, content: String)
        case paragraph(id: Int, content: String)

        var id: Int {
            switch self {
            case .heading(let id, _),
                 .numbered(let id, _, _),
                 .bullet(let id, _),
                 .paragraph(let id, _):
                return id
            }
        }
    }

    private var blocks: [Block] {
        let lines = text.components(separatedBy: .newlines)
        var parsed: [Block] = []
        for (idx, raw) in lines.enumerated() {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty { continue }

            if line.hasPrefix("### ") || line.hasPrefix("## ") || line.hasPrefix("# ") {
                let stripped = line.drop(while: { $0 == "#" || $0 == " " })
                parsed.append(.heading(id: idx, content: String(stripped)))
            } else if let numRange = line.range(of: #"^\d+[\.\、]\s+"#, options: .regularExpression) {
                let prefix = String(line[numRange])
                let digits = prefix.filter(\.isNumber)
                let body = String(line[numRange.upperBound...])
                parsed.append(.numbered(id: idx, num: digits, content: body))
            } else if line.hasPrefix("- ") || line.hasPrefix("* ") || line.hasPrefix("• ") {
                let body = String(line.dropFirst(2))
                parsed.append(.bullet(id: idx, content: body))
            } else {
                parsed.append(.paragraph(id: idx, content: line))
            }
        }
        return parsed
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(blocks) { block in
                switch block {
                case .heading(_, let content):
                    inlineMarkdownText(content)
                        .font(.system(size: 15, weight: .bold))
                        .padding(.top, 4)

                case .numbered(_, let num, let content):
                    HStack(alignment: .top, spacing: 10) {
                        Text(num)
                            .font(.system(size: 11.5, weight: .bold, design: .rounded))
                            .frame(width: 22, height: 22)
                            .background(Color.purple.opacity(0.16))
                            .foregroundStyle(.purple)
                            .clipShape(Circle())
                            .padding(.top, 1)

                        inlineMarkdownText(content)
                            .font(.system(size: 14.5))
                            .lineSpacing(5)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }

                case .bullet(_, let content):
                    HStack(alignment: .top, spacing: 9) {
                        Circle()
                            .fill(Color.purple.opacity(0.7))
                            .frame(width: 6, height: 6)
                            .padding(.top, 7)

                        inlineMarkdownText(content)
                            .font(.system(size: 14.5))
                            .lineSpacing(5)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }

                case .paragraph(_, let content):
                    inlineMarkdownText(content)
                        .font(.system(size: 14.5))
                        .lineSpacing(5)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .textSelection(.enabled)
    }

    private func inlineMarkdownText(_ raw: String) -> Text {
        if let attr = try? AttributedString(
            markdown: raw,
            options: AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        ) {
            return Text(attr)
        }
        return Text(raw)
    }
}

struct CitedMemoryDetailSheet: View {
    @EnvironmentObject var store: TodoStore
    let cited: CitedMemoryItem

    var body: some View {
        MemoryEditorAndDetailSheet(memoryId: cited.memoryId, initialTab: .provenance)
            .environmentObject(store)
    }
}

struct AskMemoryView: View {
    @EnvironmentObject var store: TodoStore
    @State private var question: String = ""
    @State private var qaHistory: [AskResponse] = []
    @State private var isAsking: Bool = false
    @State private var errorMsg: String?
    @State private var copiedTraceId: String?
    @State private var selectedMemory: CitedMemoryItem?

    private let quickPrompts = [
        "我这周都干了啥？",
        "我现在有哪些紧急待办？",
        "我有什么个人偏好？",
        "我以前的居住历史？"
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 10) {
                    Text("结合长期记忆（任务、偏好、事实、经历）与原始对话证据回答问题：")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)

                    HStack(spacing: 10) {
                        HStack(spacing: 8) {
                            Image(systemName: "magnifyingglass")
                                .foregroundStyle(.purple)
                            TextField("输入你的问题（支持当前状态追问或历史变迁追溯）...", text: $question)
                                .textFieldStyle(.plain)
                                .submitLabel(.search)
                                .onSubmit { ask() }
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 9)
                        .background(.background)
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .stroke(Color.purple.opacity(0.30), lineWidth: 1)
                        )

                        Button {
                            ask()
                        } label: {
                            Text(isAsking ? "检索中..." : "提问")
                                .font(.subheadline.bold())
                                .padding(.horizontal, 16)
                                .padding(.vertical, 9)
                                .background(
                                    LinearGradient(
                                        colors: [Color(red: 0.66, green: 0.33, blue: 0.97), Color(red: 0.49, green: 0.23, blue: 0.93)],
                                        startPoint: .leading,
                                        endPoint: .trailing
                                    )
                                )
                                .foregroundStyle(.white)
                                .clipShape(Capsule())
                        }
                        .buttonStyle(.plain)
                        .disabled(isAsking || question.trimmingCharacters(in: .whitespaces).isEmpty)
                    }

                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(quickPrompts, id: \.self) { prompt in
                                Button(prompt) {
                                    question = prompt
                                    ask()
                                }
                                .font(.caption)
                                .padding(.horizontal, 11)
                                .padding(.vertical, 5)
                                .background(Color.purple.opacity(0.09))
                                .foregroundStyle(.purple)
                                .clipShape(Capsule())
                                .buttonStyle(.plain)
                            }
                        }
                    }
                }

                if isAsking {
                    HStack(spacing: 10) {
                        ProgressView().controlSize(.small)
                        Text("正在联合检索向量索引、双链图谱与原文证据...")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                    .padding(14)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.background)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
                }

                if let err = errorMsg {
                    Text(err)
                        .foregroundStyle(.red)
                        .font(.footnote)
                        .padding(10)
                        .background(Color.red.opacity(0.08))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                }

                if qaHistory.isEmpty && !isAsking {
                    ContentUnavailableView(
                        "向记忆大脑提问",
                        systemImage: "brain.head.profile",
                        description: Text("点击上方快捷问题或输入任意追问，系统将只依据已记录的记忆卡与原话作答，并展示可点击的证据链。")
                    )
                    .padding(.top, 24)
                }

                ForEach(qaHistory) { res in
                    askResultCard(res)
                }
            }
            .padding(18)
        }
        .iosScrollDismissesKeyboard()
        .sheet(item: $selectedMemory) { mem in
            CitedMemoryDetailSheet(cited: mem)
                .environmentObject(store)
        }
    }

    @ViewBuilder
    private func askResultCard(_ res: AskResponse) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            // Question + Copy
            HStack(alignment: .top) {
                Label(res.question, systemImage: "bubble.left.fill")
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(.primary)
                Spacer()
                Button {
                    copyToClipboard(res.answer)
                    copiedTraceId = res.traceId
                } label: {
                    Label(copiedTraceId == res.traceId ? "已复制" : "复制", systemImage: copiedTraceId == res.traceId ? "checkmark" : "doc.on.doc")
                        .font(.caption)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Color.primary.opacity(0.06))
                        .clipShape(Capsule())
                }
                .buttonStyle(.plain)
            }

            // Status Badges
            HStack(spacing: 8) {
                Text(res.queryMode == "history" ? "🕰️ 历史演进追溯" : "📍 当前状态检索")
                    .font(.caption.bold())
                    .padding(.horizontal, 9)
                    .padding(.vertical, 3)
                    .background(Color.purple.opacity(0.14))
                    .foregroundStyle(.purple)
                    .clipShape(Capsule())

                Text(res.hasSufficientEvidence ? "✓ 证据充分" : "⚠️ 证据不足安全拒答")
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 9)
                    .padding(.vertical, 3)
                    .background(res.hasSufficientEvidence ? Color.green.opacity(0.14) : Color.orange.opacity(0.16))
                    .foregroundStyle(res.hasSufficientEvidence ? .green : .orange)
                    .clipShape(Capsule())

                Spacer()

                Text(res.traceId)
                    .font(.caption2)
                    .monospaced()
                    .foregroundStyle(.secondary)
            }

            // Rich Formatted Markdown Answer
            MarkdownAnswerView(text: res.answer)
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.primary.opacity(0.035))
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))

            // Citations: Memories, Source RawNotes & Visual Media
            let mems = res.citations?.memories ?? []
            let notes = res.citations?.notes ?? []
            let mediaItems = res.citations?.media ?? []

            if !mems.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("🧠 引用记忆卡（点击查看版本链与原话）")
                        .font(.caption.bold())
                        .foregroundStyle(.secondary)

                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(mems) { m in
                                Button {
                                    selectedMemory = m
                                } label: {
                                    HStack(spacing: 6) {
                                        Text(m.typeDisplayName)
                                            .font(.caption2.bold())
                                            .padding(.horizontal, 6)
                                            .padding(.vertical, 2)
                                            .background(Color.purple.opacity(0.15))
                                            .foregroundStyle(.purple)
                                            .clipShape(Capsule())
                                        Text(m.title)
                                            .font(.caption.weight(.medium))
                                            .lineLimit(1)
                                    }
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 6)
                                    .background(Color.primary.opacity(0.05))
                                    .clipShape(RoundedRectangle(cornerRadius: 10))
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                }
            }

            if !notes.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("📝 支撑原话证据")
                        .font(.caption.bold())
                        .foregroundStyle(.secondary)

                    ForEach(notes) { n in
                        HStack(alignment: .top, spacing: 8) {
                            Text(String(n.eventTime.prefix(10)))
                                .font(.caption2)
                                .monospaced()
                                .foregroundStyle(.secondary)
                                .padding(.top, 2)
                            Text("“\(n.content)”")
                                .font(.caption)
                                .foregroundStyle(.primary)
                                .lineLimit(2)
                        }
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.primary.opacity(0.03))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                }
            }

            if !mediaItems.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("🖼️ 关联视觉图片溯源（点击查看原图与 OCR）")
                        .font(.caption.bold())
                        .foregroundStyle(.secondary)
                    MediaThumbnailGridView(media: mediaItems)
                }
            }
        }
        .padding(16)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .stroke(Color.primary.opacity(0.08), lineWidth: 1)
        )
        .shadow(color: Color.black.opacity(0.03), radius: 6, y: 2)
    }

    private func ask() {
        let q = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else { return }
        dismissAppKeyboard()
        isAsking = true
        errorMsg = nil
        Task {
            do {
                let res = try await store.client.ask(question: q)
                qaHistory.insert(res, at: 0)
            } catch {
                errorMsg = error.localizedDescription
            }
            isAsking = false
        }
    }

    private func copyToClipboard(_ text: String) {
        #if os(macOS)
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        #elseif os(iOS)
        UIPasteboard.general.string = text
        #endif
    }
}

struct SettingsSyncView: View {
    @EnvironmentObject var store: TodoStore
    @State private var copiedTokenFeedback: Bool = false
    @State private var showAdminSheet: Bool = false

    var body: some View {
        Form {
            ThemeAndModulesSettingsSections()

            Section("账号登录与今日配额") {
                if let user = store.currentUser {
                    LabeledContent("当前账号", value: "\(user.displayName) (@\(user.username))")
                    LabeledContent("角色权限", value: user.isAdmin ? "管理员 admin" : "普通用户")
                    LabeledContent("专属空间", value: user.spaceId)
                    if let q = store.currentQuota {
                        LabeledContent("今日录入 / 配额", value: "\(q.notesCount) / \(user.dailyNoteLimit)")
                        LabeledContent("今日 AI 调用 / 上限", value: "\(q.llmCalls) / \(user.dailyLlmLimit)")
                        LabeledContent("🛡️ 防线拦截水话", value: "\(q.blockedNoiseCount) 次（0 Token 省费）")
                    }
                    Button {
                        #if os(macOS)
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(store.authToken, forType: .string)
                        #elseif os(iOS)
                        UIPasteboard.general.string = store.authToken
                        #endif
                        copiedTokenFeedback = true
                    } label: {
                        Text(copiedTokenFeedback ? "✅ 已复制 Bearer Token（可粘贴到桌面小组件）" : "📋 复制小组件 / 客户端 Token")
                    }
                    if user.isAdmin {
                        Button("🛡️ 打开管理员后台与防线设置") {
                            showAdminSheet = true
                        }
                        .fontWeight(.semibold)
                    }
                    Button("退出登录", role: .destructive) {
                        Task { await store.logout() }
                    }
                } else {
                    Text(store.authRequired ? "服务端已开启鉴权保护，请登录后同步待办与记忆。" : "当前处于免登录/本地开发模式，你也可以登录账号管理专属空间。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    Button("🔐 登录或注册账号") {
                        store.isPresentingAuthSheet = true
                    }
                    .fontWeight(.semibold)
                }
            }

            Section("Trace-Memory 服务端环境与连接") {
                HStack(spacing: 8) {
                    Button {
                        dismissAppKeyboard()
                        store.switchServerURL(TodoStore.testCloudServerURL)
                        Task { await store.refresh() }
                    } label: {
                        Text("🧪 云端测试服")
                            .font(.caption.bold())
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(store.serverURL == TodoStore.testCloudServerURL ? Color.teal : Color.teal.opacity(0.14))
                            .foregroundStyle(store.serverURL == TodoStore.testCloudServerURL ? .white : .teal)
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.borderless)

                    Button {
                        dismissAppKeyboard()
                        store.switchServerURL(TodoStore.prodCloudServerURL)
                        Task { await store.refresh() }
                    } label: {
                        Text("☁️ 线上正式服")
                            .font(.caption.bold())
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(store.serverURL == TodoStore.prodCloudServerURL ? Color.blue : Color.blue.opacity(0.12))
                            .foregroundStyle(store.serverURL == TodoStore.prodCloudServerURL ? .white : .blue)
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.borderless)

                    Button {
                        dismissAppKeyboard()
                        store.switchServerURL(TodoStore.localServerURL)
                        Task { await store.refresh() }
                    } label: {
                        Text("💻 本地环境")
                            .font(.caption.bold())
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(store.serverURL == TodoStore.localServerURL ? Color.indigo : Color.indigo.opacity(0.12))
                            .foregroundStyle(store.serverURL == TodoStore.localServerURL ? .white : .indigo)
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.borderless)

                    Spacer()
                }

                TextField("自定义服务端地址（默认线上，也支持本地或 HTTPS 域名）", text: $store.serverURL)
                    .autocorrectionDisabled()
                    .submitLabel(.done)
                    .onSubmit {
                        dismissAppKeyboard()
                        store.switchServerURL(store.serverURL)
                        Task { await store.refresh() }
                    }
                    #if os(iOS)
                    .textInputAutocapitalization(.never)
                    .keyboardType(.URL)
                    #endif
                TextField("记忆空间 Space ID (多空间隔离)", text: $store.spaceId)
                    .autocorrectionDisabled()
                    .submitLabel(.done)
                    .onSubmit {
                        dismissAppKeyboard()
                        Task { await store.refresh() }
                    }
                    #if os(iOS)
                    .textInputAutocapitalization(.never)
                    #endif

                HStack {
                    Circle()
                        .fill(store.isOnline ? Color.green : Color.orange)
                        .frame(width: 9, height: 9)
                    Text(store.engineBadge)
                        .font(.footnote)
                    Spacer()
                    Button("测试连接并刷新") {
                        dismissAppKeyboard()
                        Task { await store.refresh() }
                    }
                    .buttonStyle(.borderless)
                }

                if let err = store.lastConnectionError, !err.isEmpty {
                    Text("连接诊断：\(err)")
                        .font(.caption)
                        .foregroundStyle(.red)
                }
            }

            Section("任务消息通知与每日到期检查") {
                NotificationSettingsBlock()
                Button("🔄 立即执行到期状态检查（顺延 + 提权）") {
                    Task { await store.triggerDailyStatusCheck() }
                }
            }

            Section("离线队列与演示数据") {
                HStack {
                    Text("本地待同步离线记录")
                    Spacer()
                    Text("\(store.pendingNotes.count) 条")
                        .foregroundStyle(.secondary)
                }
                if !store.pendingNotes.isEmpty {
                    Button("立即重传离线队列") {
                        Task { await store.refresh() }
                    }
                }
                Button("✨ 一键向当前空间载入多象限演示数据") {
                    Task { await store.seedDemo() }
                }
            }
        }
        .formStyle(.grouped)
        .iosScrollDismissesKeyboard()
        .sheet(isPresented: $showAdminSheet) {
            NavigationStack {
                AdminConsoleView()
                    .environmentObject(store)
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) {
                            Button("完成") { showAdminSheet = false }
                        }
                    }
            }
            #if os(macOS)
            .frame(minWidth: 560, minHeight: 540)
            #endif
        }
    }
}

private struct NotificationSettingsBlock: View {
    @EnvironmentObject var store: TodoStore
    @ObservedObject private var notif = NotificationManager.shared
    @State private var feedbackText: String = ""

    var body: some View {
        Group {
            HStack {
                Text("系统通知权限状态")
                Spacer()
                Text(notif.statusLabel)
                    .font(.caption.bold())
                    .foregroundStyle(notif.isSystemDenied ? .red : .blue)
            }

            Toggle("允许任务消息通知", isOn: $notif.notificationsEnabled)
                .onChange(of: notif.notificationsEnabled) { _, newVal in
                    if newVal {
                        Task {
                            let ok = await notif.requestPermissionAndReport()
                            if ok {
                                notif.syncReminders(for: store.matrix?.sortedTasks ?? [])
                                feedbackText = "✅ 任务消息通知已开启并同步排程"
                            } else if notif.isSystemDenied {
                                feedbackText = "⚠️ 系统通知权限被关闭，请点击下方「前往系统设置」开启"
                            }
                        }
                    } else {
                        notif.syncReminders(for: store.matrix?.sortedTasks ?? [])
                        feedbackText = "已暂停本地任务通知"
                    }
                }

            Picker("默认提前提醒", selection: $notif.defaultReminderMinutes) {
                Text("仅手动设置提醒的任务").tag(-1)
                Text("截止准点提醒").tag(0)
                Text("提前 5 分钟").tag(5)
                Text("提前 15 分钟（推荐）").tag(15)
                Text("提前 30 分钟").tag(30)
                Text("提前 1 小时").tag(60)
            }
            .onChange(of: notif.defaultReminderMinutes) { _, _ in
                notif.syncReminders(for: store.matrix?.sortedTasks ?? [])
            }

            Toggle("每日 09:00 待办汇总早报", isOn: $notif.dailyDigestEnabled)
                .onChange(of: notif.dailyDigestEnabled) { _, _ in
                    notif.syncReminders(for: store.matrix?.sortedTasks ?? [])
                }

            Toggle("跨端新同步任务即时提醒", isOn: $notif.notifyNewRemoteTasks)

            HStack(spacing: 12) {
                Button("🔔 发送 3 秒测试通知") {
                    Task {
                        feedbackText = await notif.sendTestNotification()
                    }
                }
                .buttonStyle(.borderless)

                if notif.isSystemDenied {
                    Button("⚙️ 前往系统设置") {
                        notif.openSystemNotificationSettings()
                    }
                    .buttonStyle(.borderless)
                    .foregroundStyle(.orange)
                }
            }

            if !feedbackText.isEmpty {
                Text(feedbackText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .onAppear {
            notif.refreshAuthorizationStatus()
        }
    }
}
