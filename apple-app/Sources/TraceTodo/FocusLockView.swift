import SwiftUI
import Combine
#if os(macOS)
import AppKit
#elseif canImport(AudioToolbox)
import AudioToolbox
#endif

enum FocusTimerMode: String, CaseIterable, Codable, Identifiable {
    case pomodoro = "pomodoro"
    case stopwatch = "stopwatch"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .pomodoro: return "🍅 番茄计时"
        case .stopwatch: return "⏱️ 正计时"
        }
    }
}

struct FocusStatsModel: Codable {
    let todayDate: String
    let todayPomodoros: Int
    let totalPomodoros: Int
    let todaySeconds: Int
    let totalSeconds: Int
    let todayMinutes: Double
    let totalMinutes: Double

    enum CodingKeys: String, CodingKey {
        case todayDate = "today_date"
        case todayPomodoros = "today_pomodoros"
        case totalPomodoros = "total_pomodoros"
        case todaySeconds = "today_seconds"
        case totalSeconds = "total_seconds"
        case todayMinutes = "today_minutes"
        case totalMinutes = "total_minutes"
    }
}

struct FocusSessionItemModel: Codable, Identifiable, Hashable {
    let sessionId: String
    let taskMemoryId: String
    let taskTitle: String
    let mode: String
    let plannedMinutes: Int
    let durationSeconds: Int
    let status: String
    let abortReason: String
    let startedAt: String
    let endedAt: String
    let focusDate: String

    var id: String { sessionId }

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case taskMemoryId = "task_memory_id"
        case taskTitle = "task_title"
        case mode
        case plannedMinutes = "planned_minutes"
        case durationSeconds = "duration_seconds"
        case status
        case abortReason = "abort_reason"
        case startedAt = "started_at"
        case endedAt = "ended_at"
        case focusDate = "focus_date"
    }
}

struct FocusOverviewResponse: Codable {
    let spaceId: String
    let stats: FocusStatsModel
    let sessions: [FocusSessionItemModel]

    enum CodingKeys: String, CodingKey {
        case spaceId = "space_id"
        case stats
        case sessions
    }
}

struct ActiveFocusLaunchConfig: Identifiable, Equatable {
    let id = UUID()
    let mode: FocusTimerMode
    let task: TaskItemModel?
    let customTitle: String
    let plannedMinutes: Int
}

func formatFocusDurationHuman(_ totalSec: Int) -> String {
    let mins = max(0, totalSec) / 60
    if mins < 60 {
        return "\(mins)m"
    }
    let h = mins / 60
    let m = mins % 60
    return m > 0 ? "\(h)h \(m)m" : "\(h)h"
}

func formatFocusClockString(_ seconds: Int) -> String {
    let s = max(0, seconds)
    let h = s / 3600
    let m = (s % 3600) / 60
    let rem = s % 60
    if h > 0 {
        return String(format: "%02d:%02d:%02d", h, m, rem)
    }
    return String(format: "%02d:%02d", m, rem)
}

/// Optional iOS FamilyControls / Screen Time extension hook.
enum ScreenTimeLockCapability {
    static var isSystemAppBlockConfigured: Bool { false }

    static var statusNote: String {
        #if os(iOS)
        return "当前为应用内高对比专注锁屏（如需禁用娱乐应用，可在 Xcode 添加 FamilyControls Entitlement）"
        #else
        return "macOS 全屏沉浸式专注锁屏 · 到点自动提示并计入专注时间轴"
        #endif
    }
}

struct FocusLockView: View {
    @EnvironmentObject var store: TodoStore
    let mode: FocusTimerMode
    let task: TaskItemModel?
    let customTitle: String
    let plannedMinutes: Int

    @State private var totalSeconds: Int = 25 * 60
    @State private var elapsedSeconds: Int = 0
    @State private var startedAtIso: String = ISO8601DateFormatter().string(from: Date())
    @State private var showAbortPrompt: Bool = false
    @State private var abortReason: String = ""
    @State private var isFinishing: Bool = false
    @State private var lockSceneOverrideId: String = ""
    @State private var dimOpacity: Double = 0.28

    private var effectiveLockScene: AmbientSceneItem {
        if !lockSceneOverrideId.isEmpty {
            return AmbientSceneItem.byId(lockSceneOverrideId)
        }
        if store.activeSceneId != "none" {
            return store.activeSceneItem
        }
        return AmbientSceneItem.byId("starfield")
    }

    private let ticker = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    init(task: TaskItemModel) {
        self.mode = .pomodoro
        self.task = task
        self.customTitle = task.title
        self.plannedMinutes = max(1, task.payload.focusMinutes ?? 25)
    }

    init(launch: ActiveFocusLaunchConfig) {
        self.mode = launch.mode
        self.task = launch.task
        self.customTitle = launch.task?.title ?? (launch.customTitle.isEmpty ? "自由专注" : launch.customTitle)
        self.plannedMinutes = max(1, launch.plannedMinutes)
    }

    private var remainingSeconds: Int {
        max(0, totalSeconds - elapsedSeconds)
    }

    private var progress: Double {
        if mode == .stopwatch {
            return Double(elapsedSeconds % 1500) / 1500.0
        }
        guard totalSeconds > 0 else { return 0 }
        return min(1.0, Double(elapsedSeconds) / Double(totalSeconds))
    }

    private var formattedTimer: String {
        formatFocusClockString(mode == .stopwatch ? elapsedSeconds : remainingSeconds)
    }

    var body: some View {
        let sc = effectiveLockScene
        ZStack {
            Color(red: 0.04, green: 0.05, blue: 0.10)
                .ignoresSafeArea()

            RemoteSceneWallpaperImage(sceneId: sc.id, serverURL: store.serverURL, verticalTargetRatio: 0.26)
                .ignoresSafeArea()

            LinearGradient(
                colors: [
                    Color.black.opacity(max(0.08, dimOpacity * 0.45)),
                    Color.black.opacity(dimOpacity),
                    Color.black.opacity(min(0.75, dimOpacity + 0.24)),
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()

            if store.sceneMotion {
                AmbientParticleCanvasView(accent: sc.accentColor, primary: store.activeTheme.primaryColor)
                    .ignoresSafeArea()
            }

            VStack(spacing: 14) {
                HStack {
                    Text(mode == .stopwatch ? "🔒 正计时专注" : "🔒 番茄专注")
                        .font(.caption.bold())
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(Color.black.opacity(0.38))
                        .foregroundStyle(Color.white.opacity(0.92))
                        .clipShape(Capsule())

                    Spacer()

                    Button {
                        if dimOpacity < 0.20 {
                            dimOpacity = 0.30
                        } else if dimOpacity < 0.40 {
                            dimOpacity = 0.52
                        } else {
                            dimOpacity = 0.14
                        }
                    } label: {
                        Text(dimOpacity < 0.20 ? "☀️ 高清壁纸" : (dimOpacity < 0.40 ? "🌤️ 标准暗度" : "🌙 深邃暗场"))
                            .font(.caption2.bold())
                            .padding(.horizontal, 10)
                            .padding(.vertical, 5)
                            .background(Color.black.opacity(0.42))
                            .foregroundStyle(.white)
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                }
                .padding(.horizontal, 18)
                .padding(.top, 8)

                Spacer(minLength: 24)

                Text(task?.title ?? customTitle)
                    .font(.title3.bold())
                    .foregroundStyle(.white)
                    .shadow(color: .black.opacity(0.55), radius: 6, x: 0, y: 2)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)

                ZStack {
                    Circle()
                        .fill(Color.black.opacity(0.24))
                    Circle()
                        .stroke(Color.white.opacity(0.20), lineWidth: 10)
                    Circle()
                        .trim(from: 0, to: progress)
                        .stroke(
                            LinearGradient(colors: [sc.accentColor, store.activeTheme.primaryColor], startPoint: .topLeading, endPoint: .bottomTrailing),
                            style: StrokeStyle(lineWidth: 10, lineCap: .round)
                        )
                        .rotationEffect(.degrees(-90))
                        .animation(.linear(duration: 0.4), value: progress)

                    VStack(spacing: 4) {
                        Text(formattedTimer)
                            .font(.system(size: 44, weight: .heavy, design: .rounded))
                            .monospacedDigit()
                            .foregroundStyle(.white)
                        Text(task != nil ? "已专注 \(task?.payload.focusSessionsCompleted ?? 0) 轮" : "🌱 \(sc.name)")
                            .font(.caption)
                            .foregroundStyle(Color.white.opacity(0.82))
                    }
                }
                #if os(iOS)
                .frame(width: 178, height: 178)
                #else
                .frame(width: 206, height: 206)
                #endif

                // Quick Scene Switcher Strip matching Web `#focusSceneStrip`
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(AmbientSceneItem.catalog.filter { $0.id != "none" }) { item in
                            let isSel = sc.id == item.id
                            Button {
                                lockSceneOverrideId = item.id
                                SceneWallpaperCache.shared.fetchIfNeeded(sceneId: item.id, serverURL: store.serverURL)
                            } label: {
                                Text("\(item.icon) \(item.name)")
                                    .font(.caption2.bold())
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 6)
                                    .background(isSel ? sc.accentColor.opacity(0.85) : Color.black.opacity(0.42))
                                    .foregroundStyle(.white)
                                    .clipShape(Capsule())
                                    .overlay(Capsule().stroke(Color.white.opacity(isSel ? 0.9 : 0.22), lineWidth: 1))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.horizontal, 18)
                }
                .frame(maxWidth: 560)

                if let curTask = task, !curTask.payload.subtaskList.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("📋 执行清单")
                            .font(.caption.bold())
                            .foregroundStyle(Color.white.opacity(0.7))

                        ScrollView {
                            VStack(alignment: .leading, spacing: 8) {
                                ForEach(curTask.payload.subtaskList) { sub in
                                    Button {
                                        Task { await store.toggleSubtask(curTask, subtask: sub) }
                                    } label: {
                                        HStack(spacing: 10) {
                                            Image(systemName: sub.done ? "checkmark.circle.fill" : "circle")
                                                .foregroundStyle(sub.done ? .green : .purple)
                                            Text(sub.title)
                                                .font(.subheadline)
                                                .strikethrough(sub.done)
                                                .foregroundStyle(sub.done ? Color.white.opacity(0.5) : .white)
                                            Spacer()
                                        }
                                    }
                                    .buttonStyle(.plain)
                                }
                            }
                        }
                        .frame(maxHeight: 150)
                    }
                    .padding(14)
                    .frame(maxWidth: 420)
                    .background(Color.white.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: 16))
                }

                Text(ScreenTimeLockCapability.statusNote)
                    .font(.caption2)
                    .foregroundStyle(Color.white.opacity(0.55))
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)

                if showAbortPrompt {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("⚠️ 专注尚未完成，请填写中断原因：")
                            .font(.caption.bold())
                            .foregroundStyle(Color(red: 0.99, green: 0.65, blue: 0.65))

                        HStack(spacing: 6) {
                            ForEach(["临时急事", "查资料", "休息一下"], id: \.self) { tag in
                                Button(tag) { abortReason = tag }
                                    .font(.caption2)
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 5)
                                    .background(Color.white.opacity(0.14))
                                    .foregroundStyle(.white)
                                    .clipShape(Capsule())
                                    .buttonStyle(.plain)
                            }
                        }

                        TextField("输入中断原因...", text: $abortReason)
                            .textFieldStyle(.roundedBorder)
                            .submitLabel(.done)
                            .onSubmit { dismissAppKeyboard() }

                        HStack {
                            Button("继续专注") {
                                dismissAppKeyboard()
                                showAbortPrompt = false
                            }
                            .foregroundStyle(.white)
                            Spacer()
                            Button("确认退出") {
                                dismissAppKeyboard()
                                let clean = abortReason.trimmingCharacters(in: .whitespacesAndNewlines)
                                guard !clean.isEmpty else { return }
                                isFinishing = true
                                Task {
                                    await store.recordUnifiedFocusSession(
                                        mode: mode,
                                        status: "aborted",
                                        plannedMinutes: plannedMinutes,
                                        durationSeconds: max(1, elapsedSeconds),
                                        task: task,
                                        customTitle: customTitle,
                                        abortReason: clean,
                                        startedAtIso: startedAtIso
                                    )
                                }
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(.red)
                            .disabled(abortReason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        }
                    }
                    .padding(14)
                    .frame(maxWidth: 420)
                    .background(Color.black.opacity(0.55))
                    .clipShape(RoundedRectangle(cornerRadius: 16))
                } else {
                    HStack(spacing: 14) {
                        Button {
                            guard !isFinishing else { return }
                            isFinishing = true
                            let effDur = mode == .stopwatch ? max(1, elapsedSeconds) : max(1, elapsedSeconds > 0 ? elapsedSeconds : totalSeconds)
                            Task {
                                await store.recordUnifiedFocusSession(
                                    mode: mode,
                                    status: "completed",
                                    plannedMinutes: plannedMinutes,
                                    durationSeconds: effDur,
                                    task: task,
                                    customTitle: customTitle,
                                    completeTask: task != nil,
                                    startedAtIso: startedAtIso
                                )
                            }
                        } label: {
                            Text(task != nil ? "✅ 完成任务并结束" : "✅ 结束并记录专注")
                                .font(.headline)
                                .padding(.horizontal, 20)
                                .padding(.vertical, 12)
                                .background(LinearGradient(colors: [.purple, .pink], startPoint: .leading, endPoint: .trailing))
                                .foregroundStyle(.white)
                                .clipShape(Capsule())
                        }
                        .buttonStyle(.plain)

                        Button("提前退出") {
                            showAbortPrompt = true
                        }
                        .padding(.horizontal, 16)
                        .padding(.vertical, 11)
                        .background(Color.white.opacity(0.14))
                        .foregroundStyle(.white)
                        .clipShape(Capsule())
                        .buttonStyle(.plain)
                    }
                }
            }
            .padding(24)
        }
        .onAppear {
            totalSeconds = plannedMinutes * 60
            elapsedSeconds = 0
            startedAtIso = ISO8601DateFormatter().string(from: Date())
        }
        .onReceive(ticker) { _ in
            guard !isFinishing else { return }
            elapsedSeconds += 1
            if mode == .pomodoro && elapsedSeconds >= totalSeconds {
                isFinishing = true
                playCompletionSound()
                Task {
                    await store.recordUnifiedFocusSession(
                        mode: .pomodoro,
                        status: "completed",
                        plannedMinutes: plannedMinutes,
                        durationSeconds: totalSeconds,
                        task: task,
                        customTitle: customTitle,
                        completeTask: false,
                        startedAtIso: startedAtIso
                    )
                }
            }
        }
    }

    private func playCompletionSound() {
        #if os(macOS)
        NSSound.beep()
        #elseif canImport(AudioToolbox)
        AudioServicesPlaySystemSound(1005)
        #endif
    }
}

/// Primary "专注" Tab: Pomodoro 25:00 / Stopwatch dual mode + 4-Grid Stats + History Timeline.
struct FocusCenterView: View {
    @EnvironmentObject var store: TodoStore
    @State private var mode: FocusTimerMode = .pomodoro
    @State private var plannedMinutes: Int = 25
    @State private var selectedTaskId: String = ""
    @State private var freeFocusTitle: String = ""

    // Inline active timer state (unified with lock overlay)
    @State private var isRunningInline: Bool = false
    @State private var isPausedInline: Bool = false
    @State private var elapsedSeconds: Int = 0
    @State private var startedAtIso: String = ""

    private let ticker = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    private var openTasks: [TaskItemModel] {
        (store.matrix?.sortedTasks ?? []).filter { !$0.payload.isDone }
    }

    private var boundTask: TaskItemModel? {
        openTasks.first(where: { $0.memoryId == selectedTaskId })
    }

    private var stats: FocusStatsModel {
        store.focusOverview?.stats ?? FocusStatsModel(
            todayDate: "",
            todayPomodoros: 0,
            totalPomodoros: 0,
            todaySeconds: 0,
            totalSeconds: 0,
            todayMinutes: 0,
            totalMinutes: 0
        )
    }

    private var inlineDisplaySeconds: Int {
        if mode == .stopwatch {
            return isRunningInline ? elapsedSeconds : 0
        }
        let total = plannedMinutes * 60
        return isRunningInline ? max(0, total - elapsedSeconds) : total
    }

    private var inlineProgress: Double {
        guard isRunningInline else { return 0 }
        if mode == .stopwatch {
            return Double(elapsedSeconds % 1500) / 1500.0
        }
        let total = max(1, plannedMinutes * 60)
        return min(1.0, Double(elapsedSeconds) / Double(total))
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("🍅 番茄专注中心")
                            .font(.title3.bold())
                        Text("番茄计时 / 正计时双模式 · 统一专注内核")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Picker("计时模式", selection: $mode) {
                        ForEach(FocusTimerMode.allCases) { m in
                            Text(m.label).tag(m)
                        }
                    }
                    .pickerStyle(.segmented)
                    .frame(maxWidth: 220)
                    .disabled(isRunningInline)
                }

                #if os(macOS)
                HStack(alignment: .top, spacing: 18) {
                    timerCardView
                        .frame(width: 390)
                    rightStatsAndTimelineColumn
                        .frame(maxWidth: .infinity)
                }
                #else
                VStack(spacing: 16) {
                    timerCardView
                    rightStatsAndTimelineColumn
                }
                #endif
            }
            .padding(18)
        }
        .task {
            plannedMinutes = store.defaultPomodoroMinutes
            await store.refreshFocusOverview()
        }
        .onReceive(ticker) { _ in
            guard isRunningInline && !isPausedInline else { return }
            elapsedSeconds += 1
            if mode == .pomodoro && elapsedSeconds >= plannedMinutes * 60 {
                let dur = plannedMinutes * 60
                stopInlineState()
                Task {
                    await store.recordUnifiedFocusSession(
                        mode: .pomodoro,
                        status: "completed",
                        plannedMinutes: plannedMinutes,
                        durationSeconds: dur,
                        task: boundTask,
                        customTitle: freeFocusTitle,
                        startedAtIso: startedAtIso
                    )
                }
            }
        }
    }

    private var timerCardView: some View {
        VStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 8) {
                Picker("关联事项", selection: $selectedTaskId) {
                    Text("🌱 自由专注（不绑定待办）").tag("")
                    ForEach(openTasks) { t in
                        Text("[\(t.payload.quadrantKind.shortCode)] \(t.title)").tag(t.memoryId)
                    }
                }
                .disabled(isRunningInline)

                if selectedTaskId.isEmpty {
                    TextField("可选：自由专注主题（如：深度阅读论文）...", text: $freeFocusTitle)
                        .textFieldStyle(.roundedBorder)
                        .disabled(isRunningInline)
                }
            }

            if mode == .pomodoro {
                HStack(spacing: 8) {
                    ForEach([15, 25, 45, 60], id: \.self) { mins in
                        let sel = plannedMinutes == mins
                        Button("\(mins) 分钟") {
                            if !isRunningInline { plannedMinutes = mins }
                        }
                        .font(.caption.bold())
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)
                        .background(sel ? store.activeTheme.primaryFillColor : Color.primary.opacity(0.07))
                        .foregroundStyle(sel ? Color.white : Color.primary)
                        .clipShape(Capsule())
                        .overlay(
                            Capsule()
                                .stroke(sel ? store.activeTheme.primaryFillColor : Color.primary.opacity(0.14), lineWidth: 1)
                        )
                        .buttonStyle(.plain)
                    }
                }
            }

            ZStack {
                Circle()
                    .stroke(Color.primary.opacity(0.09), lineWidth: 11)
                Circle()
                    .trim(from: 0, to: inlineProgress)
                    .stroke(
                        LinearGradient(
                            colors: [store.activeTheme.primaryColor, store.activeTheme.accentColor],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        ),
                        style: StrokeStyle(lineWidth: 11, lineCap: .round)
                    )
                    .rotationEffect(.degrees(-90))
                    .animation(.linear(duration: 0.35), value: inlineProgress)

                VStack(spacing: 4) {
                    Text(formatFocusClockString(inlineDisplaySeconds))
                        .font(.system(size: 44, weight: .heavy, design: .rounded))
                        .monospacedDigit()
                    Text(isRunningInline ? (isPausedInline ? "⏸ 已暂停" : (boundTask?.title ?? (freeFocusTitle.isEmpty ? "自由专注中" : freeFocusTitle))) : (mode == .stopwatch ? "正计时随时结算" : "准备 \(plannedMinutes) 分钟番茄"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .padding(.horizontal, 12)
                }
            }
            .frame(width: 205, height: 205)
            .padding(.vertical, 4)

            if !isRunningInline {
                HStack(spacing: 10) {
                    Button {
                        elapsedSeconds = 0
                        startedAtIso = ISO8601DateFormatter().string(from: Date())
                        isPausedInline = false
                        isRunningInline = true
                    } label: {
                        Label("开始专注", systemImage: "play.fill")
                            .font(.subheadline.bold())
                            .padding(.horizontal, 18)
                            .padding(.vertical, 10)
                            .background(store.activeTheme.primaryFillColor)
                            .foregroundStyle(.white)
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)

                    Button {
                        store.activeFocusLaunch = ActiveFocusLaunchConfig(
                            mode: mode,
                            task: boundTask,
                            customTitle: freeFocusTitle,
                            plannedMinutes: plannedMinutes
                        )
                    } label: {
                        Label("沉浸锁屏", systemImage: "lock.fill")
                            .font(.subheadline.weight(.medium))
                            .padding(.horizontal, 14)
                            .padding(.vertical, 10)
                            .background(Color.primary.opacity(0.07))
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                }
            } else {
                HStack(spacing: 8) {
                    Button(isPausedInline ? "▶ 继续" : "⏸ 暂停") {
                        isPausedInline.toggle()
                    }
                    .buttonStyle(.bordered)

                    Button("✅ 完成记录") {
                        let eff = max(1, elapsedSeconds)
                        let stIso = startedAtIso
                        stopInlineState()
                        Task {
                            await store.recordUnifiedFocusSession(
                                mode: mode,
                                status: "completed",
                                plannedMinutes: plannedMinutes,
                                durationSeconds: eff,
                                task: boundTask,
                                customTitle: freeFocusTitle,
                                startedAtIso: stIso
                            )
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(store.activeTheme.primaryColor)

                    Button("✕ 中断", role: .destructive) {
                        let eff = max(1, elapsedSeconds)
                        let stIso = startedAtIso
                        stopInlineState()
                        Task {
                            await store.recordUnifiedFocusSession(
                                mode: mode,
                                status: "aborted",
                                plannedMinutes: plannedMinutes,
                                durationSeconds: eff,
                                task: boundTask,
                                customTitle: freeFocusTitle,
                                abortReason: "手动提前结束",
                                startedAtIso: stIso
                            )
                        }
                    }
                    .buttonStyle(.bordered)
                }
            }
        }
        .padding(18)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .stroke(Color.primary.opacity(0.07), lineWidth: 1)
        )
    }

    private var rightStatsAndTimelineColumn: some View {
        VStack(alignment: .leading, spacing: 16) {
            LazyVGrid(columns: [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)], spacing: 12) {
                focusStatCard(title: "🍅 今日番茄", value: "\(stats.todayPomodoros)", subtitle: "今日完成轮次")
                focusStatCard(title: "🏆 总番茄", value: "\(stats.totalPomodoros)", subtitle: "累计完成番茄")
                focusStatCard(title: "⏱️ 今日专注时长", value: formatFocusDurationHuman(stats.todaySeconds), subtitle: "含番茄与正计时")
                focusStatCard(title: "⏳ 总专注时长", value: formatFocusDurationHuman(stats.totalSeconds), subtitle: "历史累计时长")
            }

            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("📜 专注历史时间轴")
                        .font(.headline)
                    Spacer()
                    Button("刷新") {
                        Task { await store.refreshFocusOverview() }
                    }
                    .font(.caption)
                    .buttonStyle(.borderless)
                }

                let sessions = store.focusOverview?.sessions ?? []
                if sessions.isEmpty {
                    Text("暂无专注历史记录，开启第一轮番茄或正计时后自动沉淀在此。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 16)
                } else {
                    ForEach(sessions) { sess in
                        HStack(spacing: 10) {
                            VStack(alignment: .leading, spacing: 4) {
                                HStack(spacing: 6) {
                                    Text(sess.status == "aborted" ? "⚠️ 中断" : (sess.mode == "stopwatch" ? "⏱️ 正计时" : "🍅 番茄"))
                                        .font(.caption2.bold())
                                        .padding(.horizontal, 7)
                                        .padding(.vertical, 2)
                                        .background((sess.status == "aborted" ? Color.orange : store.activeTheme.primaryColor).opacity(0.14))
                                        .foregroundStyle(sess.status == "aborted" ? .orange : store.activeTheme.primaryColor)
                                        .clipShape(Capsule())

                                    Text(sess.taskTitle)
                                        .font(.subheadline.weight(.semibold))
                                        .lineLimit(1)
                                }

                                let sTime = sess.startedAt.count >= 16 ? String(sess.startedAt.dropFirst(11).prefix(5)) : ""
                                let eTime = sess.endedAt.count >= 16 ? String(sess.endedAt.dropFirst(11).prefix(5)) : ""
                                Text("\(sess.focusDate) · \(sTime)–\(eTime) · \(formatFocusDurationHuman(sess.durationSeconds))")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button(role: .destructive) {
                                Task { await store.removeFocusSession(sess.sessionId) }
                            } label: {
                                Image(systemName: "trash")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            .buttonStyle(.plain)
                        }
                        .padding(11)
                        .background(Color.primary.opacity(0.03))
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }
                }
            }
            .padding(16)
            .background(.background)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(Color.primary.opacity(0.07), lineWidth: 1)
            )
        }
    }

    private func focusStatCard(title: String, value: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            Text(value)
                .font(.system(size: 25, weight: .heavy, design: .rounded))
                .monospacedDigit()
            Text(subtitle)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(Color.primary.opacity(0.07), lineWidth: 1)
        )
    }

    private func stopInlineState() {
        isRunningInline = false
        isPausedInline = false
        elapsedSeconds = 0
    }
}
