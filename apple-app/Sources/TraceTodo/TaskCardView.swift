import SwiftUI

struct TaskCardView: View {
    @EnvironmentObject var store: TodoStore
    let task: TaskItemModel
    @State private var showDetailSheet = false

    private var qKind: QuadrantKind {
        task.payload.quadrantKind
    }

    private var theme: MacaronTheme {
        task.payload.themeKind
    }

    var body: some View {
        HStack(spacing: 0) {
            Rectangle()
                .fill(theme.accentColor)
                .frame(width: 4)

            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top, spacing: 10) {
                    Button {
                        Task {
                            let action = task.payload.isDone ? "reopen_task" : "complete_task"
                            await store.transitionTask(task, action: action)
                        }
                    } label: {
                        Image(systemName: task.payload.isDone ? "checkmark.circle.fill" : "circle")
                            .font(.system(size: 19, weight: .semibold))
                            .foregroundStyle(task.payload.isDone ? .green : theme.accentColor)
                    }
                    .buttonStyle(.plain)

                    VStack(alignment: .leading, spacing: 4) {
                        Text(task.title)
                            .font(.system(size: 14.5, weight: .semibold))
                            .strikethrough(task.payload.isDone)
                            .foregroundStyle(task.payload.isDone ? .secondary : .primary)
                            .onTapGesture {
                                store.editingTaskTarget = task
                            }

                        if let reason = task.payload.priorityReason, !reason.isEmpty {
                            Text(reason)
                                .font(.system(size: 12))
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                    }

                    Spacer(minLength: 4)

                    Text("\(qKind.shortCode) · \(Int(task.payload.priorityScore))")
                        .font(.system(size: 11, weight: .bold))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(qKind.tintColor.opacity(0.14))
                        .foregroundStyle(qKind.tintColor)
                        .clipShape(Capsule())
                }

                // Checklist Progress & Interactive Subtasks
                if !task.payload.subtaskList.isEmpty {
                    let subs = task.payload.subtaskList
                    let doneCount = subs.filter(\.done).count
                    let ratio = Double(doneCount) / Double(max(1, subs.count))

                    VStack(alignment: .leading, spacing: 5) {
                        HStack {
                            Label("清单 \(doneCount)/\(subs.count)", systemImage: "checklist")
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(.secondary)
                            Spacer()
                        }

                        GeometryReader { geo in
                            ZStack(alignment: .leading) {
                                Capsule().fill(Color.primary.opacity(0.10))
                                Capsule()
                                    .fill(LinearGradient(colors: [.purple, .pink], startPoint: .leading, endPoint: .trailing))
                                    .frame(width: geo.size.width * ratio)
                            }
                        }
                        .frame(height: 5)

                        ForEach(subs) { sub in
                            Button {
                                Task { await store.toggleSubtask(task, subtask: sub) }
                            } label: {
                                HStack(spacing: 6) {
                                    Image(systemName: sub.done ? "checkmark.square.fill" : "square")
                                        .font(.system(size: 12))
                                        .foregroundStyle(sub.done ? .green : .purple)
                                    Text(sub.title)
                                        .font(.system(size: 12))
                                        .strikethrough(sub.done)
                                        .foregroundStyle(sub.done ? .secondary : .primary)
                                        .lineLimit(1)
                                }
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(8)
                    .background(Color.primary.opacity(0.035))
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                }

                // Meta Tags Row
                HStack(spacing: 6) {
                    Label("\(task.payload.importance)", systemImage: "star.fill")
                        .font(.system(size: 10.5))
                        .foregroundStyle(.secondary)

                    Label("\(task.payload.effectiveUrgency ?? task.payload.urgency)", systemImage: "bolt.fill")
                        .font(.system(size: 10.5))
                        .foregroundStyle(.secondary)

                    if let due = task.payload.dueAt, !due.isEmpty {
                        let isOverdue = task.payload.isOverdue ?? false
                        let allDay = task.payload.isAllDay ?? true
                        let label = allDay ? String(due.prefix(10)) : String(due.prefix(16)).replacingOccurrences(of: "T", with: " ")
                        Text("⏰ \(label)\(isOverdue ? " 逾期" : "")")
                            .font(.system(size: 10.5, weight: isOverdue ? .semibold : .regular))
                            .foregroundStyle(isOverdue ? .red : .secondary)
                    }

                    if let rep = task.payload.repeatRule, rep != "never" {
                        Text("🔁 \(RepeatRuleOption(rawValue: rep)?.label ?? rep)")
                            .font(.system(size: 10.5))
                            .foregroundStyle(.secondary)
                    }

                    if let rc = task.payload.rolloverCount, rc > 0 {
                        Text("↻ 顺延\(rc)天")
                            .font(.system(size: 10.5, weight: .semibold))
                            .foregroundStyle(rc >= 3 ? .red : .orange)
                    }

                    if (task.payload.supervisionEnabled ?? false) || task.payload.taskMode == "multiplayer" {
                        let who = (task.payload.assignee ?? "self")
                        Label(who == "self" ? "监督" : who, systemImage: "person.2.fill")
                            .font(.system(size: 10.5, weight: .medium))
                            .foregroundStyle(.purple)
                    }

                    if !task.payload.nudgeList.isEmpty {
                        Text("⚡\(task.payload.nudgeList.count)")
                            .font(.system(size: 10.5, weight: .semibold))
                            .foregroundStyle(.orange)
                    }

                    if task.userLocked {
                        Image(systemName: "lock.fill")
                            .font(.system(size: 10))
                            .foregroundStyle(.orange)
                    }

                    Spacer()

                    if !task.payload.isDone {
                        Button {
                            store.activeFocusTask = task
                        } label: {
                            Image(systemName: "timer")
                                .font(.system(size: 13))
                                .foregroundStyle(.purple)
                        }
                        .buttonStyle(.plain)
                        .help("进入沉浸专注锁屏")
                    }

                    Menu {
                        Button {
                            store.editingTaskTarget = task
                        } label: {
                            Label("编辑任务卡片 / AI 拆解", systemImage: "pencil")
                        }
                        if !task.payload.isDone {
                            Button {
                                store.activeFocusTask = task
                            } label: {
                                Label("开启专注锁屏 (\(task.payload.focusMinutes ?? 25)m)", systemImage: "timer")
                            }
                        }
                        Divider()
                        Section("调整四象限（自动启用防覆盖锁）") {
                            ForEach(QuadrantKind.allCases) { targetQ in
                                Button {
                                    Task { await store.moveTaskQuadrant(task, to: targetQ) }
                                } label: {
                                    Label(targetQ.title, systemImage: targetQ.iconName)
                                }
                            }
                        }
                        Divider()
                        if !task.payload.isDone {
                            Button(role: .destructive) {
                                Task { await store.transitionTask(task, action: "cancel_task") }
                            } label: {
                                Label("取消此待办", systemImage: "xmark.circle")
                            }
                        }
                        Button {
                            showDetailSheet = true
                        } label: {
                            Label("查看原话证据与版本 (v\(task.version))", systemImage: "doc.text.magnifyingglass")
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                            .font(.system(size: 15))
                            .foregroundStyle(.secondary)
                    }
                    .menuStyle(.borderlessButton)
                    .fixedSize()
                }
            }
            .padding(11)
        }
        .background(
            ZStack {
                Color.clear.background(.background)
                theme.cardBackground.opacity(0.22)
            }
        )
        .clipShape(RoundedRectangle(cornerRadius: 13, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 13, style: .continuous)
                .stroke(Color.primary.opacity(0.08), lineWidth: 1)
        )
        .sheet(isPresented: $showDetailSheet) {
            TaskProvenanceSheet(task: task)
                .environmentObject(store)
        }
    }
}

struct TaskProvenanceSheet: View {
    @EnvironmentObject var store: TodoStore
    @Environment(\.dismiss) private var dismiss
    let task: TaskItemModel
    @State private var detail: MemoryDetailResponse?
    @State private var loading = true

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(task.title)
                            .font(.headline)
                        Text(task.summary)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        Text("\(task.uri) · 当前版本 v\(task.version)")
                            .font(.caption)
                            .monospaced()
                            .foregroundStyle(.secondary)
                    }

                    Divider()

                    Text("📝 支撑原话记录 (RawNote 证据溯源)")
                        .font(.subheadline.bold())

                    if loading {
                        ProgressView()
                    } else if let sources = detail?.sources, !sources.isEmpty {
                        ForEach(sources) { src in
                            VStack(alignment: .leading, spacing: 4) {
                                Text("“\(src.visibleContent)”")
                                    .font(.callout)
                                Text("\(src.noteId) (rev \(src.citedRevision)) · \(String(src.eventTime.prefix(19)))")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                            .padding(10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color.primary.opacity(0.04))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                    } else {
                        Text("暂无离线缓存的原话详情")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    if let history = detail?.history, !history.isEmpty {
                        Divider()
                        Text("📜 版本演进链")
                            .font(.subheadline.bold())
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
                .padding()
            }
            .navigationTitle("记忆证据与版本溯源")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { dismiss() }
                }
            }
        }
        #if os(macOS)
        .frame(minWidth: 500, minHeight: 400)
        #endif
        .task {
            detail = try? await store.client.fetchMemoryDetail(memoryId: task.memoryId)
            loading = false
        }
    }
}
