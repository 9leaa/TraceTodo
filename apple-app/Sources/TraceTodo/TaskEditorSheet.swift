import SwiftUI

struct TaskEditorSheet: View {
    @EnvironmentObject var store: TodoStore
    @Environment(\.dismiss) private var dismiss

    let existingTask: TaskItemModel?

    @State private var title: String = ""
    @State private var summary: String = ""
    @State private var colorTheme: MacaronTheme = .sky
    @State private var taskMode: TaskModeOption = .personal
    @State private var assignee: String = "self"
    @State private var hasDueDate: Bool = true
    @State private var dueDate: Date = Date()
    @State private var isAllDay: Bool = true
    @State private var repeatRule: RepeatRuleOption = .never
    @State private var reminderOption: ReminderOffsetOption = .none
    @State private var subtasks: [SubtaskItem] = []
    @State private var focusLockEnabled: Bool = false
    @State private var focusMinutes: Int = 25
    @State private var supervisionEnabled: Bool = false
    @State private var autoRollover: Bool = true
    @State private var importance: Int = 4
    @State private var urgency: Int = 3
    @State private var manualQuadrant: String = ""
    @State private var showAdvancedQuadrant: Bool = false

    @State private var isDecomposing: Bool = false
    @State private var memoryContextHints: [String] = []
    @State private var localError: String?

    init(existingTask: TaskItemModel?) {
        self.existingTask = existingTask
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 14) {
                    // 1. Top Macaron Theme Header Card
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(spacing: 8) {
                            TextField("请输入任务内容 ✍️", text: $title)
                                .font(.system(size: 19, weight: .bold))
                                .textFieldStyle(.plain)
                                .foregroundStyle(Color(red: 0.08, green: 0.11, blue: 0.18))
                            VoiceMicButton(text: $title, compact: true)
                        }

                        TextField("可选补充备注 / 验收标准...", text: $summary)
                            .font(.system(size: 13))
                            .textFieldStyle(.plain)
                            .foregroundStyle(Color(red: 0.25, green: 0.30, blue: 0.38))

                        HStack(spacing: 12) {
                            ForEach(MacaronTheme.allCases) { theme in
                                Button {
                                    withAnimation(.easeInOut(duration: 0.18)) {
                                        colorTheme = theme
                                    }
                                } label: {
                                    ZStack {
                                        Circle()
                                            .fill(theme.dotColor)
                                            .frame(width: 26, height: 26)
                                            .overlay(
                                                Circle().stroke(Color.white.opacity(0.9), lineWidth: colorTheme == theme ? 3 : 1)
                                            )
                                        if colorTheme == theme {
                                            Image(systemName: "checkmark")
                                                .font(.system(size: 11, weight: .bold))
                                                .foregroundStyle(Color(red: 0.08, green: 0.11, blue: 0.18))
                                        }
                                    }
                                    .scaleEffect(colorTheme == theme ? 1.12 : 1.0)
                                }
                                .buttonStyle(.plain)
                            }
                            Spacer()
                            Text(colorTheme.displayName)
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(Color(red: 0.25, green: 0.30, blue: 0.38))
                        }
                        .padding(.top, 2)
                    }
                    .padding(16)
                    .background(colorTheme.cardBackground)
                    .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))

                    // 2. Mode Segmented Tabs & Schedule Card
                    VStack(spacing: 12) {
                        Picker("任务模式", selection: $taskMode) {
                            ForEach(TaskModeOption.allCases) { m in
                                Text(m.label).tag(m)
                            }
                        }
                        .pickerStyle(.segmented)
                        .onChange(of: taskMode) { _, newVal in
                            if newVal == .multiplayer {
                                supervisionEnabled = true
                            }
                        }

                        Text(privacyBannerText)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 6)
                            .padding(.horizontal, 10)
                            .background(Color.primary.opacity(0.05))
                            .clipShape(Capsule())

                        if taskMode == .multiplayer {
                            HStack {
                                Label("负责人 / 协作人", systemImage: "person.2.fill")
                                    .font(.subheadline)
                                Spacer()
                                TextField("self / 小王", text: $assignee)
                                    .textFieldStyle(.roundedBorder)
                                    .frame(width: 150)
                            }
                            Divider()
                        }

                        // Date row
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Label("日期", systemImage: "calendar")
                                    .font(.subheadline.weight(.medium))
                                Spacer()
                                Toggle("设定日期", isOn: $hasDueDate)
                                    .labelsHidden()
                                    .toggleStyle(.switch)
                                    .controlSize(.small)
                            }
                            if hasDueDate {
                                HStack(spacing: 6) {
                                    quickDatePill("今天", target: Date())
                                    quickDatePill("明天", target: Calendar.current.date(byAdding: .day, value: 1, to: Date()) ?? Date())
                                    quickDatePill("本周六", target: nextSaturday())
                                    Spacer()
                                    DatePicker("", selection: $dueDate, displayedComponents: .date)
                                        .labelsHidden()
                                        .datePickerStyle(.compact)
                                }
                            }
                        }

                        Divider()

                        // Time row
                        if hasDueDate {
                            HStack {
                                Label("时间", systemImage: "clock")
                                    .font(.subheadline.weight(.medium))
                                Spacer()
                                Toggle("全天", isOn: $isAllDay)
                                    .toggleStyle(.switch)
                                    .controlSize(.small)
                                    .fixedSize()
                                if !isAllDay {
                                    DatePicker("", selection: $dueDate, displayedComponents: .hourAndMinute)
                                        .labelsHidden()
                                        .datePickerStyle(.compact)
                                }
                            }
                            Divider()
                        }

                        // Repeat row
                        HStack {
                            Label("重复", systemImage: "repeat")
                                .font(.subheadline.weight(.medium))
                            Spacer()
                            Picker("重复", selection: $repeatRule) {
                                ForEach(RepeatRuleOption.allCases) { r in
                                    Text(r.label).tag(r)
                                }
                            }
                            .labelsHidden()
                        }

                        Divider()

                        // Reminder row
                        HStack {
                            Label("提醒", systemImage: "bell.badge")
                                .font(.subheadline.weight(.medium))
                            Spacer()
                            Picker("提醒", selection: $reminderOption) {
                                ForEach(ReminderOffsetOption.allCases) { opt in
                                    Text(opt.label).tag(opt)
                                }
                            }
                            .labelsHidden()
                            .onChange(of: reminderOption) { _, newVal in
                                if newVal != .none {
                                    NotificationManager.shared.requestPermissionIfNeeded()
                                }
                            }
                        }
                    }
                    .padding(14)
                    .background(.background)
                    .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 18, style: .continuous)
                            .stroke(Color.primary.opacity(0.06), lineWidth: 1)
                    )

                    // 3. Checklist + AI Decompose Card
                    VStack(alignment: .leading, spacing: 10) {
                        HStack {
                            Label("清单", systemImage: "checklist")
                                .font(.subheadline.bold())
                            if !subtasks.isEmpty {
                                Text("(\(subtasks.count) 步)")
                                    .font(.caption.bold())
                                    .foregroundStyle(.purple)
                            }
                            Spacer()
                            Button {
                                Task { await runAiDecompose() }
                            } label: {
                                HStack(spacing: 4) {
                                    if isDecomposing {
                                        ProgressView().controlSize(.mini)
                                    } else {
                                        Image(systemName: "sparkles")
                                    }
                                    Text(isDecomposing ? "拆解中..." : "AI 拆解")
                                }
                                .font(.caption.bold())
                                .padding(.horizontal, 12)
                                .padding(.vertical, 6)
                                .background(
                                    LinearGradient(
                                        colors: [Color.purple, Color.pink],
                                        startPoint: .leading,
                                        endPoint: .trailing
                                    )
                                )
                                .foregroundStyle(.white)
                                .clipShape(Capsule())
                            }
                            .buttonStyle(.plain)
                            .disabled(isDecomposing)
                        }

                        if !memoryContextHints.isEmpty {
                            Text("🧠 已结合个人记忆：\(memoryContextHints.joined(separator: " · "))")
                                .font(.caption2)
                                .foregroundStyle(.purple)
                                .padding(8)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(Color.purple.opacity(0.08))
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                        }

                        ForEach($subtasks) { $step in
                            HStack(spacing: 8) {
                                Button {
                                    step.done.toggle()
                                } label: {
                                    Image(systemName: step.done ? "checkmark.circle.fill" : "circle")
                                        .foregroundStyle(step.done ? .green : .purple)
                                }
                                .buttonStyle(.plain)

                                TextField("输入子步骤...", text: $step.title)
                                    .textFieldStyle(.plain)
                                    .font(.subheadline)
                                    .strikethrough(step.done)

                                Button {
                                    subtasks.removeAll { $0.id == step.id }
                                } label: {
                                    Image(systemName: "xmark.circle.fill")
                                        .foregroundStyle(.secondary)
                                }
                                .buttonStyle(.plain)
                            }
                            .padding(.horizontal, 10)
                            .padding(.vertical, 7)
                            .background(Color.primary.opacity(0.04))
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                        }

                        Button {
                            subtasks.append(SubtaskItem(id: "sub_\(UUID().uuidString.prefix(8))", title: "", done: false))
                        } label: {
                            Label("添加子步骤", systemImage: "plus.circle.fill")
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(.purple)
                        }
                        .buttonStyle(.plain)
                    }
                    .padding(14)
                    .background(.background)
                    .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 18, style: .continuous)
                            .stroke(Color.primary.opacity(0.06), lineWidth: 1)
                    )

                    // 4. Focus Lock, Friend Supervision & Auto-Rollover Card
                    VStack(spacing: 12) {
                        Toggle(isOn: $focusLockEnabled) {
                            VStack(alignment: .leading, spacing: 2) {
                                Label("超时专注 / 锁屏模式", systemImage: "nosign")
                                    .font(.subheadline.weight(.medium))
                                Text("开启后支持全屏倒计时专注，未完成退出需填写中断原因")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }

                        if focusLockEnabled {
                            HStack {
                                Text("专注时长")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Spacer()
                                Picker("专注时长", selection: $focusMinutes) {
                                    Text("15 分钟").tag(15)
                                    Text("25 分钟").tag(25)
                                    Text("45 分钟").tag(45)
                                    Text("60 分钟").tag(60)
                                }
                                .pickerStyle(.segmented)
                                .frame(maxWidth: 260)
                            }
                        }

                        Divider()

                        Toggle(isOn: $supervisionEnabled) {
                            VStack(alignment: .leading, spacing: 2) {
                                Label("好友监督", systemImage: "person.wave.2")
                                    .font(.subheadline.weight(.medium))
                                Text("开启后好友可在「监工小队」页查看并催办/鼓励")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }

                        Divider()

                        Toggle(isOn: $autoRollover) {
                            VStack(alignment: .leading, spacing: 2) {
                                Label("顺延任务", systemImage: "arrow.trianglehead.2.clockwise")
                                    .font(.subheadline.weight(.medium))
                                Text("跨日未完成自动顺延至今天（连续 ≥3 天触发 AI 提醒）")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    .padding(14)
                    .background(.background)
                    .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 18, style: .continuous)
                            .stroke(Color.primary.opacity(0.06), lineWidth: 1)
                    )

                    // 5. Collapsible Quadrant Tuning
                    DisclosureGroup("🎯 四象限重要/紧急度微调", isExpanded: $showAdvancedQuadrant) {
                        VStack(spacing: 10) {
                            Stepper("重要度：\(importance) 星", value: $importance, in: 1...5)
                            Stepper("基础紧急度：\(urgency) 级", value: $urgency, in: 1...5)
                            Picker("象限锁定", selection: $manualQuadrant) {
                                Text("自动计算（随截止时间提权）").tag("")
                                ForEach(QuadrantKind.allCases) { q in
                                    Text("锁定至 \(q.title)").tag(q.rawValue)
                                }
                            }
                        }
                        .padding(.top, 8)
                    }
                    .padding(14)
                    .background(.background)
                    .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 18, style: .continuous)
                            .stroke(Color.primary.opacity(0.06), lineWidth: 1)
                    )

                    if let err = localError {
                        Text(err)
                            .font(.caption)
                            .foregroundStyle(.red)
                    }

                    // Bottom Gradient Purple Button
                    Button {
                        dismissAppKeyboard()
                        Task { await save() }
                    } label: {
                        Text(existingTask == nil ? "创建新任务" : "保存修改")
                            .font(.headline)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                            .background(
                                LinearGradient(
                                    colors: [Color(red: 0.66, green: 0.33, blue: 0.97), Color(red: 0.49, green: 0.23, blue: 0.93)],
                                    startPoint: .leading,
                                    endPoint: .trailing
                                )
                            )
                            .foregroundStyle(.white)
                            .clipShape(Capsule())
                            .shadow(color: Color.purple.opacity(0.28), radius: 8, y: 4)
                    }
                    .buttonStyle(.plain)
                    .disabled(store.isBusy)
                }
                .padding(16)
            }
            .background(Color.primary.opacity(0.04))
            .iosScrollDismissesKeyboard()
            .iosKeyboardDoneToolbar()
            .navigationTitle(existingTask == nil ? "新建任务卡片" : "编辑任务 (v\(existingTask!.version))")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
            }
        }
        #if os(macOS)
        .frame(minWidth: 500, minHeight: 650)
        #endif
        .onAppear { populateFromExisting() }
    }

    private var privacyBannerText: String {
        if taskMode == .multiplayer {
            return "👥 多人任务将同步展示在「监工小队」协作看板中"
        }
        if supervisionEnabled {
            return "👀 已开启好友监督，好友可在「监工小队」看到并督促此任务"
        }
        return "ⓘ 若未开启监督，个人任务仅自己可见"
    }

    private func quickDatePill(_ label: String, target: Date) -> some View {
        let sameDay = Calendar.current.isDate(dueDate, inSameDayAs: target)
        return Button(label) {
            dueDate = target
        }
        .font(.caption.weight(sameDay ? .bold : .regular))
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(sameDay ? Color.purple.opacity(0.16) : Color.primary.opacity(0.06))
        .foregroundStyle(sameDay ? .purple : .primary)
        .clipShape(Capsule())
        .buttonStyle(.plain)
    }

    private func nextSaturday() -> Date {
        let cal = Calendar.current
        let today = Date()
        let wd = cal.component(.weekday, from: today) // 7 is Saturday
        let diff = (7 - wd + 7) % 7
        return cal.date(byAdding: .day, value: diff == 0 ? 7 : diff, to: today) ?? today
    }

    private func populateFromExisting() {
        guard let t = existingTask else { return }
        let p = t.payload
        title = t.title
        summary = t.summary == t.title ? "" : t.summary
        colorTheme = p.themeKind
        taskMode = TaskModeOption(rawValue: p.taskMode ?? "personal") ?? .personal
        assignee = p.assignee ?? "self"
        supervisionEnabled = p.supervisionEnabled ?? false
        isAllDay = p.isAllDay ?? true
        repeatRule = RepeatRuleOption(rawValue: p.repeatRule ?? "never") ?? .never
        reminderOption = ReminderOffsetOption.from(apiValue: p.reminderOffsetMinutes)
        subtasks = p.subtaskList
        focusLockEnabled = p.focusLockEnabled ?? false
        focusMinutes = p.focusMinutes ?? 25
        autoRollover = p.autoRollover ?? false
        importance = p.importance
        urgency = p.urgency
        manualQuadrant = t.userLocked ? p.quadrant : ""

        if let dueStr = p.dueAt, !dueStr.isEmpty {
            if let parsed = TraceTodoDateParser.parseBeijingDate(dueStr) {
                hasDueDate = true
                dueDate = parsed
            }
        } else {
            hasDueDate = false
        }
    }

    private func runAiDecompose() async {
        let cleanTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanTitle.isEmpty else {
            localError = "请先在顶部输入任务内容再使用 AI 拆解"
            return
        }
        localError = nil
        isDecomposing = true
        defer { isDecomposing = false }
        do {
            let res = try await store.client.decomposeTask(
                title: cleanTitle,
                summary: summary,
                memoryId: existingTask?.memoryId ?? ""
            )
            let nonEmptyExisting = subtasks.filter { !$0.title.trimmingCharacters(in: .whitespaces).isEmpty }
            subtasks = nonEmptyExisting + res.subtasks
            memoryContextHints = res.memoryContextUsed
        } catch {
            localError = "AI 拆解失败：\(error.localizedDescription)"
        }
    }

    private func save() async {
        let cleanTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanTitle.isEmpty else {
            localError = "请输入任务内容"
            return
        }
        localError = nil

        var dueIso = ""
        if hasDueDate {
            let cal = Calendar.current
            var targetDate = dueDate
            if isAllDay {
                targetDate = cal.date(bySettingHour: 23, minute: 59, second: 59, of: dueDate) ?? dueDate
            }
            let fmt = ISO8601DateFormatter()
            fmt.timeZone = TimeZone.current
            fmt.formatOptions = [.withInternetDateTime]
            dueIso = fmt.string(from: targetDate)
        }

        let cleanedSubs: [[String: Any]] = subtasks
            .filter { !$0.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
            .map { ["id": $0.id, "title": $0.title.trimmingCharacters(in: .whitespacesAndNewlines), "done": $0.done] }

        var body: [String: Any] = [
            "target_memory_id": existingTask?.memoryId ?? "",
            "title": cleanTitle,
            "summary": summary.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? cleanTitle : summary.trimmingCharacters(in: .whitespacesAndNewlines),
            "color_theme": colorTheme.rawValue,
            "task_mode": taskMode.rawValue,
            "supervision_enabled": supervisionEnabled || taskMode == .multiplayer,
            "assignee": taskMode == .multiplayer ? (assignee.trimmingCharacters(in: .whitespaces).isEmpty ? "self" : assignee) : "self",
            "due_at": dueIso,
            "is_all_day": isAllDay,
            "repeat_rule": repeatRule.rawValue,
            "auto_rollover": autoRollover,
            "focus_lock_enabled": focusLockEnabled,
            "focus_minutes": focusMinutes,
            "importance": importance,
            "urgency": urgency,
            "quadrant": manualQuadrant,
            "lock_for_user": !manualQuadrant.isEmpty,
            "subtasks": cleanedSubs
        ]
        if let ver = existingTask?.version {
            body["expected_version"] = ver
        }
        if let rem = reminderOption.apiValue {
            body["reminder_offset_minutes"] = rem
        } else {
            body["reminder_offset_minutes"] = NSNull()
        }

        let ok = await store.saveTaskCard(payloadBody: body, isEdit: existingTask != nil)
        if ok {
            dismiss()
        }
    }
}
