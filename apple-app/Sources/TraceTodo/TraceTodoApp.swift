import SwiftUI
#if os(macOS)
import AppKit
#endif

enum AppSection: String, CaseIterable, Identifiable {
    case tasks = "待办"
    case calendar = "日历"
    case focus = "专注"
    case notes = "随记"
    case memories = "记忆"

    var id: String { rawValue }

    var icon: String {
        switch self {
        case .tasks: return "checklist"
        case .calendar: return "calendar"
        case .focus: return "timer"
        case .notes: return "note.text"
        case .memories: return "brain.head.profile"
        }
    }

    var toggleKey: ModuleToggleKey? {
        switch self {
        case .tasks, .memories: return nil
        case .calendar: return .calendar
        case .focus: return .focus
        case .notes: return .notes
        }
    }
}

struct MainContentView: View {
    @EnvironmentObject var store: TodoStore
    @Environment(\.scenePhase) private var scenePhase
    @State private var selectedSection: AppSection = .tasks
    @State private var topCaptureText: String = ""

    private var visibleSections: [AppSection] {
        AppSection.allCases.filter { sec in
            guard let key = sec.toggleKey else { return true }
            return store.isModuleEnabled(key)
        }
    }

    var body: some View {
        ZStack {
            #if os(iOS)
            TabView(selection: $selectedSection) {
                NavigationStack {
                    UnifiedTodoHostView()
                        .navigationTitle("待办")
                        .navigationBarTitleDisplayMode(.inline)
                        .toolbar { iosTopToolbar }
                        .iosKeyboardDoneToolbar()
                }
                .tabItem { Label("待办", systemImage: AppSection.tasks.icon) }
                .tag(AppSection.tasks)

                if store.isModuleEnabled(.calendar) {
                    NavigationStack {
                        CalendarPreviewHostView()
                            .navigationTitle("日历")
                            .navigationBarTitleDisplayMode(.inline)
                            .toolbar { iosTopToolbar }
                            .iosKeyboardDoneToolbar()
                    }
                    .tabItem { Label("日历", systemImage: AppSection.calendar.icon) }
                    .tag(AppSection.calendar)
                }

                if store.isModuleEnabled(.focus) {
                    NavigationStack {
                        FocusCenterView()
                            .navigationTitle("番茄专注")
                            .navigationBarTitleDisplayMode(.inline)
                            .toolbar { iosTopToolbar }
                            .iosKeyboardDoneToolbar()
                    }
                    .tabItem { Label("专注", systemImage: AppSection.focus.icon) }
                    .tag(AppSection.focus)
                }

                if store.isModuleEnabled(.notes) {
                    NavigationStack {
                        QuickNotesStreamView()
                            .navigationTitle("随记")
                            .navigationBarTitleDisplayMode(.inline)
                            .toolbar { iosTopToolbar }
                            .iosKeyboardDoneToolbar()
                    }
                    .tabItem { Label("随记", systemImage: AppSection.notes.icon) }
                    .tag(AppSection.notes)
                }

                NavigationStack {
                    MemoriesAndAskHostView()
                        .navigationTitle("记忆与问答")
                        .navigationBarTitleDisplayMode(.inline)
                        .toolbar { iosTopToolbar }
                        .iosKeyboardDoneToolbar()
                }
                .tabItem { Label("记忆", systemImage: AppSection.memories.icon) }
                .tag(AppSection.memories)
            }
            .tint(store.activeTheme.primaryColor)
            .preferredColorScheme(store.activeTheme.isDark ? .dark : .light)
            .task {
                NotificationManager.shared.refreshAuthorizationStatus()
                await store.refresh()
                store.startAutoSyncLoop()
            }
            .onChange(of: scenePhase) { _, newPhase in
                if newPhase == .active {
                    NotificationManager.shared.refreshAuthorizationStatus()
                    store.startAutoSyncLoop()
                    Task { await store.syncIfChanged() }
                } else if newPhase == .background {
                    store.stopAutoSyncLoop()
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: .NSCalendarDayChanged)) { _ in
                Task { await store.refresh() }
            }
            .onReceive(NotificationCenter.default.publisher(for: .traceTodoOpenOverview)) { _ in
                selectedSection = .tasks
                Task { await store.refresh() }
            }
            .onOpenURL { url in
                handleDeepLink(url)
            }
            #else
            VStack(spacing: 0) {
                // Top Header Bar with Theme Vector Micro-Illustration
                ZStack(alignment: .trailing) {
                    ThemeHeaderArtworkView(theme: store.activeTheme)

                    HStack(spacing: 14) {
                        if let appIconImg = NSApp.applicationIconImage {
                            Image(nsImage: appIconImg)
                                .resizable()
                                .scaledToFit()
                                .frame(width: 36, height: 36)
                                .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
                        } else {
                            ZStack {
                                RoundedRectangle(cornerRadius: 10, style: .continuous)
                                    .fill(
                                        LinearGradient(
                                            colors: [store.activeTheme.primaryColor, store.activeTheme.accentColor],
                                            startPoint: .topLeading,
                                            endPoint: .bottomTrailing
                                        )
                                    )
                                    .frame(width: 34, height: 34)
                                Image(systemName: "square.grid.2x2.fill")
                                    .font(.system(size: 15, weight: .bold))
                                    .foregroundStyle(.white)
                            }
                        }

                        Divider().frame(height: 24)

                        HStack(spacing: 8) {
                            Image(systemName: "magnifyingglass")
                                .foregroundStyle(.secondary)
                            TextField("一句话极速录入待办或记忆（支持 🎤 语音听写）...", text: $topCaptureText)
                                .textFieldStyle(.plain)
                                .font(.system(size: 13.5))
                                .onSubmit { submitTopCapture() }

                            VoiceMicButton(text: $topCaptureText, compact: true)

                            if store.isBusy {
                                ProgressView().controlSize(.small)
                            } else if !topCaptureText.trimmingCharacters(in: .whitespaces).isEmpty {
                                Button("AI 录入") { submitTopCapture() }
                                    .font(.caption.bold())
                                    .buttonStyle(.borderedProminent)
                                    .tint(store.activeTheme.primaryColor)
                            }
                        }

                        Spacer()

                        Button {
                            store.isPresentingNewTaskSheet = true
                        } label: {
                            Label("新建任务", systemImage: "plus.rectangle.fill")
                                .font(.system(size: 12.5, weight: .semibold))
                                .padding(.horizontal, 14)
                                .padding(.vertical, 7)
                                .background(store.activeTheme.primaryColor)
                                .foregroundStyle(.white)
                                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                        }
                        .buttonStyle(.plain)

                        if store.isModuleEnabled(.supervision) {
                            Button {
                                store.isPresentingSupervisionSheet = true
                            } label: {
                                Label("监工", systemImage: "person.2.fill")
                                    .font(.system(size: 12, weight: .medium))
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 6)
                                    .background(Color.primary.opacity(0.06))
                                    .clipShape(Capsule())
                            }
                            .buttonStyle(.plain)
                        }

                        Button {
                            store.isPresentingSettingsSheet = true
                        } label: {
                            Label("外观/设置", systemImage: "paintpalette.fill")
                                .font(.system(size: 12, weight: .medium))
                                .padding(.horizontal, 10)
                                .padding(.vertical, 6)
                                .background(Color.primary.opacity(0.06))
                                .clipShape(Capsule())
                        }
                        .buttonStyle(.plain)

                        // Overdue / Status Bell Indicator
                        ZStack(alignment: .topTrailing) {
                            Button {
                                selectedSection = .tasks
                                Task { await store.refresh() }
                            } label: {
                                Image(systemName: "bell")
                                    .font(.system(size: 16))
                                    .foregroundStyle(.secondary)
                            }
                            .buttonStyle(.plain)
                            .contentShape(Rectangle())
                            .help("刷新与查看逾期/顺延提醒")

                            let alertCnt = (store.matrix?.summaryCounts.overdue ?? 0) + store.q1Count
                            if alertCnt > 0 {
                                Text("\(alertCnt)")
                                    .font(.system(size: 9, weight: .bold))
                                    .foregroundStyle(.white)
                                    .padding(.horizontal, 4)
                                    .padding(.vertical, 1)
                                    .background(store.activeTheme.primaryColor)
                                    .clipShape(Capsule())
                                    .offset(x: 6, y: -5)
                            }
                        }

                        Button {
                            store.isPresentingAuthSheet = true
                        } label: {
                            HStack(spacing: 6) {
                                Circle()
                                    .fill(store.isOnline ? Color.green : Color.orange)
                                    .frame(width: 7, height: 7)
                                Text(store.currentUser?.displayName ?? store.spaceId)
                                    .font(.system(size: 12.5, weight: .medium))
                            }
                            .padding(.horizontal, 10)
                            .padding(.vertical, 5)
                            .background(Color.primary.opacity(0.06))
                            .clipShape(Capsule())
                        }
                        .buttonStyle(.plain)

                        Button {
                            Task { await store.refresh() }
                        } label: {
                            Image(systemName: "arrow.clockwise")
                                .font(.system(size: 14))
                                .foregroundStyle(.secondary)
                        }
                        .frame(width: 30, height: 30)
                        .contentShape(Rectangle())
                        .buttonStyle(.plain)
                    }
                    .padding(.horizontal, 18)
                }
                .frame(height: 58)
                .background(store.activeTheme.surfaceBackground)
                .overlay(Rectangle().fill(Color.primary.opacity(0.08)).frame(height: 1), alignment: .bottom)

                // Main 3-Column Body: Converged 5-Tab Left Rail + Center Canvas + Right Timeline
                HStack(spacing: 0) {
                    leftFloatingRail
                        .padding(.vertical, 16)
                        .padding(.leading, 16)

                    Group {
                        switch selectedSection {
                        case .tasks:
                            UnifiedTodoHostView()
                        case .calendar:
                            CalendarPreviewHostView()
                        case .focus:
                            FocusCenterView()
                        case .notes:
                            QuickNotesStreamView()
                        case .memories:
                            MemoriesAndAskHostView()
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)

                    RightTimelineRailView()
                }
                .background(store.activeTheme.canvasBackground)
            }
            .preferredColorScheme(store.activeTheme.isDark ? .dark : .light)
            .frame(minWidth: 1120, minHeight: 700)
            .task {
                NotificationManager.shared.refreshAuthorizationStatus()
                await store.refresh()
                store.startAutoSyncLoop()
            }
            .onChange(of: scenePhase) { _, newPhase in
                if newPhase == .active {
                    NotificationManager.shared.refreshAuthorizationStatus()
                    store.startAutoSyncLoop()
                    Task { await store.syncIfChanged() }
                } else if newPhase == .background {
                    store.stopAutoSyncLoop()
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: .NSCalendarDayChanged)) { _ in
                Task { await store.refresh() }
            }
            .onReceive(NotificationCenter.default.publisher(for: .traceTodoOpenOverview)) { _ in
                selectedSection = .tasks
                Task { await store.refresh() }
            }
            #endif

            if let launch = store.activeFocusLaunch {
                FocusLockView(launch: launch)
                    .environmentObject(store)
                    .transition(.opacity)
                    .zIndex(100)
            } else if let focusTask = store.activeFocusTask {
                FocusLockView(task: focusTask)
                    .environmentObject(store)
                    .transition(.opacity)
                    .zIndex(100)
            }
        }
        .onChange(of: store.enabledModules) { _, _ in
            if let key = selectedSection.toggleKey, !store.isModuleEnabled(key) {
                selectedSection = .tasks
            }
        }
        .sheet(isPresented: $store.isPresentingNewTaskSheet) {
            TaskEditorSheet(existingTask: nil)
                .environmentObject(store)
        }
        .sheet(item: $store.editingTaskTarget) { target in
            TaskEditorSheet(existingTask: target)
                .environmentObject(store)
        }
        .sheet(isPresented: $store.isPresentingSupervisionSheet) {
            NavigationStack {
                SupervisionBoardView()
                    .environmentObject(store)
                    .navigationTitle("好友监工小队")
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) {
                            Button("关闭") { store.isPresentingSupervisionSheet = false }
                        }
                    }
            }
            #if os(macOS)
            .frame(minWidth: 680, minHeight: 540)
            #endif
        }
        .sheet(isPresented: $store.isPresentingSettingsSheet) {
            NavigationStack {
                SettingsSyncView()
                    .environmentObject(store)
                    .navigationTitle("外观主题与设置")
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) {
                            Button("完成") { store.isPresentingSettingsSheet = false }
                        }
                    }
            }
            #if os(macOS)
            .frame(minWidth: 620, minHeight: 580)
            #endif
        }
        .sheet(isPresented: Binding(
            get: { store.isPresentingAuthSheet || store.needsLoginGate },
            set: { newVal in
                if !store.needsLoginGate {
                    store.isPresentingAuthSheet = newVal
                }
            }
        )) {
            AuthGateSheet()
                .environmentObject(store)
                .interactiveDismissDisabled(store.needsLoginGate)
        }
    }

    #if os(macOS)
    private var leftFloatingRail: some View {
        VStack(spacing: 6) {
            ForEach(visibleSections) { sec in
                let isSel = selectedSection == sec
                Button {
                    selectedSection = sec
                } label: {
                    VStack(spacing: 5) {
                        ZStack {
                            if isSel {
                                Circle()
                                    .fill(store.activeTheme.primaryColor)
                                    .frame(width: 34, height: 34)
                            }
                            Image(systemName: sec.icon)
                                .font(.system(size: 14.5, weight: .semibold))
                                .foregroundStyle(isSel ? .white : .secondary)
                        }
                        .frame(height: 34)

                        Text(sec.rawValue)
                            .font(.system(size: 11, weight: isSel ? .bold : .medium))
                            .foregroundStyle(isSel ? .primary : .secondary)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 8)
                    .background(isSel ? store.activeTheme.railSelectedBg : Color.clear)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .contentShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                }
                .buttonStyle(.plain)
            }

            Spacer(minLength: 8)

            VStack(spacing: 6) {
                Text("TIP")
                    .font(.system(size: 10, weight: .bold))
                    .tracking(1.2)
                    .foregroundStyle(.secondary)

                Image(systemName: "lightbulb.max.fill")
                    .font(.system(size: 16))
                    .foregroundStyle(Color(red: 0.95, green: 0.76, blue: 0.20))

                Text(railTipText)
                    .font(.system(size: 10.5))
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .lineLimit(5)
                    .padding(8)
                    .frame(maxWidth: .infinity)
                    .background(Color.primary.opacity(0.05))
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            }
            .padding(.horizontal, 6)
            .padding(.bottom, 10)
        }
        .padding(.vertical, 12)
        .padding(.horizontal, 6)
        .frame(width: 102)
        .background(store.activeTheme.surfaceBackground)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .shadow(color: Color.black.opacity(0.06), radius: 14, x: 0, y: 6)
    }

    private var railTipText: String {
        if let first = store.matrix?.diagnostics.first {
            return first
                .replacingOccurrences(of: "🎯 ", with: "")
                .replacingOccurrences(of: "🔥 ", with: "")
                .replacingOccurrences(of: "⚠️ ", with: "")
        }
        return "记录日常想法，AI 自动归类四象限与长期记忆"
    }

    private func submitTopCapture() {
        let txt = topCaptureText
        topCaptureText = ""
        Task { await store.submitQuickNote(txt) }
    }
    #endif

    private func handleDeepLink(_ url: URL) {
        guard url.scheme == "tracetodo" else { return }
        let host = url.host ?? ""
        if host == "new" {
            store.isPresentingNewTaskSheet = true
        } else if host == "memories" {
            selectedSection = .memories
        } else if host == "supervision" {
            store.isPresentingSupervisionSheet = true
        } else if host == "focus" {
            selectedSection = .focus
        } else if host == "calendar" {
            selectedSection = .calendar
        } else {
            selectedSection = .tasks
        }
        Task { await store.refresh() }
    }

    private var iosTopToolbar: some ToolbarContent {
        ToolbarItemGroup(placement: .automatic) {
            Button {
                store.isPresentingNewTaskSheet = true
            } label: {
                Image(systemName: "plus")
            }
            if store.isModuleEnabled(.supervision) {
                Button {
                    store.isPresentingSupervisionSheet = true
                } label: {
                    Image(systemName: "person.2")
                }
            }
            Button {
                store.isPresentingSettingsSheet = true
            } label: {
                Image(systemName: "paintpalette")
            }
            Button {
                Task { await store.refresh() }
            } label: {
                Image(systemName: "arrow.clockwise")
            }
        }
    }
}

#if os(macOS)
struct MenuBarQuickView: View {
    @EnvironmentObject var store: TodoStore
    @Environment(\.openWindow) private var openWindow
    @State private var quickText: String = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("🧠 TraceTodo 快捷待办")
                    .font(.headline)
                Spacer()
                Text("Q1: \(store.q1Count) · 待办: \(store.totalTodoCount)")
                    .font(.caption.bold())
                    .foregroundStyle(.secondary)
            }

            HStack(spacing: 6) {
                TextField("一句话记待办，回车即抽...", text: $quickText)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit {
                        let txt = quickText
                        quickText = ""
                        Task { await store.submitQuickNote(txt) }
                    }
                Button("添加") {
                    let txt = quickText
                    quickText = ""
                    Task { await store.submitQuickNote(txt) }
                }
                .disabled(quickText.trimmingCharacters(in: .whitespaces).isEmpty)
            }

            Divider()

            let topTasks = Array((store.matrix?.sortedTasks ?? []).filter { !$0.payload.isDone }.prefix(5))
            if topTasks.isEmpty {
                Text("当前全部待办已清空 🎉")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 8)
            } else {
                ForEach(topTasks) { t in
                    HStack(spacing: 8) {
                        Button {
                            Task { await store.transitionTask(t, action: "complete_task") }
                        } label: {
                            Image(systemName: "circle")
                                .foregroundStyle(t.payload.themeKind.accentColor)
                        }
                        .buttonStyle(.plain)

                        Text(t.title)
                            .font(.system(size: 12.5))
                            .lineLimit(1)
                        Spacer()
                        Text(t.payload.quadrantKind.shortCode)
                            .font(.system(size: 10, weight: .bold))
                            .foregroundStyle(t.payload.quadrantKind.tintColor)
                    }
                }
            }

            Divider()

            HStack {
                Button("+ 新建彩色卡片") {
                    NSApp.activate(ignoringOtherApps: true)
                    openWindow(id: "main")
                    store.isPresentingNewTaskSheet = true
                }
                Button("打开主窗口") {
                    NSApp.activate(ignoringOtherApps: true)
                    openWindow(id: "main")
                }
                Spacer()
                Button("刷新") {
                    Task { await store.refresh() }
                }
                Button("退出") {
                    NSApp.terminate(nil)
                }
            }
            .font(.caption)
        }
        .padding(14)
        .frame(width: 360)
    }
}
#endif

@main
struct TraceTodoApp: App {
    @StateObject private var store = TodoStore()

    init() {
        NotificationManager.shared.configureDelegate()
        #if os(macOS)
        NSApplication.shared.setActivationPolicy(.regular)
        #endif
    }

    var body: some Scene {
        WindowGroup(id: "main") {
            MainContentView()
                .environmentObject(store)
        }

        #if os(macOS)
        MenuBarExtra {
            MenuBarQuickView()
                .environmentObject(store)
        } label: {
            HStack(spacing: 3) {
                Image(systemName: store.q1Count > 0 ? "flame.fill" : "checkmark.circle")
                Text("\(store.totalTodoCount)")
            }
        }
        .menuBarExtraStyle(.window)
        #endif
    }
}
