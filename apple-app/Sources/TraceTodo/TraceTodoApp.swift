import SwiftUI
#if os(macOS)
import AppKit
#endif

enum AppSection: String, CaseIterable, Identifiable {
    case overview = "概览"
    case matrix = "四象限"
    case list = "列表"
    case supervision = "监工"
    case memories = "记忆"
    case graph = "图谱"
    case ask = "问答"
    case settings = "设置"

    var id: String { rawValue }

    var icon: String {
        switch self {
        case .overview: return "gauge.with.dots.needle.33percent"
        case .matrix: return "square.grid.2x2.fill"
        case .list: return "list.bullet.rectangle.portrait"
        case .supervision: return "person.2.fill"
        case .memories: return "brain.head.profile"
        case .graph: return "point.3.connected.trianglepath.dotted"
        case .ask: return "bubble.left.and.text.bubble.right.fill"
        case .settings: return "gearshape.fill"
        }
    }
}

struct MainContentView: View {
    @EnvironmentObject var store: TodoStore
    @Environment(\.scenePhase) private var scenePhase
    @State private var selectedSection: AppSection = .overview
    @State private var topCaptureText: String = ""

    var body: some View {
        ZStack {
            #if os(iOS)
            TabView(selection: $selectedSection) {
                NavigationStack {
                    DashboardOverviewView()
                        .navigationTitle("TraceTodo 概览")
                        .toolbar { refreshToolbarItem }
                        .iosKeyboardDoneToolbar()
                }
                .tabItem { Label("概览", systemImage: "gauge.with.dots.needle.33percent") }
                .tag(AppSection.overview)

                NavigationStack {
                    SortedTaskListView()
                        .navigationTitle("优先级列表")
                        .toolbar { refreshToolbarItem }
                        .iosKeyboardDoneToolbar()
                }
                .tabItem { Label("列表", systemImage: "list.bullet.rectangle.portrait") }
                .tag(AppSection.list)

                NavigationStack {
                    SupervisionBoardView()
                        .navigationTitle("监工小队")
                        .iosKeyboardDoneToolbar()
                }
                .tabItem { Label("监工", systemImage: "person.2.fill") }
                .tag(AppSection.supervision)

                NavigationStack {
                    MemoriesAndAskHostView()
                        .navigationTitle("记忆与问答")
                        .iosKeyboardDoneToolbar()
                }
                .tabItem { Label("记忆", systemImage: "brain.head.profile") }
                .tag(AppSection.ask)

                NavigationStack {
                    SettingsSyncView()
                        .navigationTitle("设置与同步")
                        .iosKeyboardDoneToolbar()
                }
                .tabItem { Label("设置", systemImage: "gearshape.fill") }
                .tag(AppSection.settings)
            }
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
                selectedSection = .overview
                Task { await store.refresh() }
            }
            .onOpenURL { url in
                handleDeepLink(url)
            }
            #else
            VStack(spacing: 0) {
                // Top Crisp White Header Bar matching reference UI
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
                                        colors: [Color(red: 0.24, green: 0.51, blue: 0.96), Color(red: 0.48, green: 0.35, blue: 0.97)],
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
                            .foregroundStyle(Color(red: 0.62, green: 0.67, blue: 0.75))
                        TextField("一句话极速录入待办或记忆（如：明天必须修复支付超时 bug）...", text: $topCaptureText)
                            .textFieldStyle(.plain)
                            .font(.system(size: 13.5))
                            .foregroundStyle(Color(red: 0.14, green: 0.18, blue: 0.26))
                            .onSubmit { submitTopCapture() }

                        if store.isBusy {
                            ProgressView().controlSize(.small)
                        } else if !topCaptureText.trimmingCharacters(in: .whitespaces).isEmpty {
                            Button("AI 录入") { submitTopCapture() }
                                .font(.caption.bold())
                                .buttonStyle(.borderedProminent)
                                .tint(Color(red: 0.20, green: 0.54, blue: 0.96))
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
                            .background(Color(red: 0.20, green: 0.54, blue: 0.96))
                            .foregroundStyle(.white)
                            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                    }
                    .buttonStyle(.plain)

                    // Overdue / Status Bell Indicator
                    ZStack(alignment: .topTrailing) {
                        Button {
                            selectedSection = .overview
                            Task { await store.refresh() }
                        } label: {
                            Image(systemName: "bell")
                                .font(.system(size: 16))
                                .foregroundStyle(Color(red: 0.42, green: 0.47, blue: 0.56))
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
                                .background(Color(red: 0.20, green: 0.54, blue: 0.96))
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
                                .foregroundStyle(Color(red: 0.28, green: 0.33, blue: 0.42))
                        }
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(Color(red: 0.95, green: 0.96, blue: 0.98))
                        .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)

                    Button {
                        Task { await store.refresh() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 14))
                            .foregroundStyle(Color(red: 0.42, green: 0.47, blue: 0.56))
                    }
                    .frame(width: 30, height: 30)
                    .contentShape(Rectangle())
                    .buttonStyle(.plain)
                }
                .padding(.horizontal, 18)
                .frame(height: 58)
                .background(Color.white)
                .overlay(Rectangle().fill(Color.black.opacity(0.06)).frame(height: 1), alignment: .bottom)

                // Main 3-Column Body: Floating White Left Rail + Center Canvas + Right Blue-Header Timeline
                HStack(spacing: 0) {
                    leftFloatingRail
                        .padding(.vertical, 16)
                        .padding(.leading, 16)

                    Group {
                        switch selectedSection {
                        case .overview:
                            DashboardOverviewView()
                        case .matrix:
                            QuadrantMatrixView()
                        case .list:
                            SortedTaskListView()
                        case .supervision:
                            SupervisionBoardView()
                        case .memories:
                            MemoriesView()
                        case .graph:
                            KnowledgeGraphCanvasView()
                        case .ask:
                            AskMemoryView()
                        case .settings:
                            SettingsSyncView()
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)

                    RightTimelineRailView()
                }
                .background(Color(red: 0.96, green: 0.97, blue: 0.99))
            }
            .preferredColorScheme(.light)
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
                selectedSection = .overview
                Task { await store.refresh() }
            }
            #endif

            if let focusTask = store.activeFocusTask {
                FocusLockView(task: focusTask)
                    .environmentObject(store)
                    .transition(.opacity)
                    .zIndex(100)
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
            ForEach(AppSection.allCases) { sec in
                let isSel = selectedSection == sec
                Button {
                    selectedSection = sec
                } label: {
                    VStack(spacing: 5) {
                        ZStack {
                            if isSel {
                                Circle()
                                    .fill(Color(red: 0.18, green: 0.22, blue: 0.31))
                                    .frame(width: 34, height: 34)
                            }
                            Image(systemName: sec.icon)
                                .font(.system(size: 14.5, weight: .semibold))
                                .foregroundStyle(isSel ? .white : Color(red: 0.56, green: 0.61, blue: 0.70))
                        }
                        .frame(height: 34)

                        Text(sec.rawValue)
                            .font(.system(size: 11, weight: isSel ? .bold : .medium))
                            .foregroundStyle(isSel ? Color(red: 0.14, green: 0.18, blue: 0.26) : Color(red: 0.52, green: 0.57, blue: 0.66))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 8)
                    .background(isSel ? Color(red: 0.95, green: 0.97, blue: 1.00) : Color.clear)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .contentShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                }
                .buttonStyle(.plain)
            }

            Spacer(minLength: 8)

            // Bottom TIP insight card matching left rail TIP widget in reference image
            VStack(spacing: 6) {
                Text("TIP")
                    .font(.system(size: 10, weight: .bold))
                    .tracking(1.2)
                    .foregroundStyle(Color(red: 0.48, green: 0.53, blue: 0.62))

                Image(systemName: "lightbulb.max.fill")
                    .font(.system(size: 16))
                    .foregroundStyle(Color(red: 0.95, green: 0.76, blue: 0.20))

                Text(railTipText)
                    .font(.system(size: 10.5))
                    .foregroundStyle(Color(red: 0.32, green: 0.37, blue: 0.46))
                    .multilineTextAlignment(.center)
                    .lineLimit(5)
                    .padding(8)
                    .frame(maxWidth: .infinity)
                    .background(Color(red: 0.91, green: 0.94, blue: 0.97))
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            }
            .padding(.horizontal, 6)
            .padding(.bottom, 10)
        }
        .padding(.vertical, 12)
        .padding(.horizontal, 6)
        .frame(width: 108)
        .background(Color.white)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .shadow(color: Color.black.opacity(0.05), radius: 14, x: 0, y: 6)
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
            selectedSection = .ask
        } else if host == "supervision" {
            selectedSection = .supervision
        } else if host == "list" {
            selectedSection = .list
        } else {
            selectedSection = .overview
        }
        Task { await store.refresh() }
    }

    private var refreshToolbarItem: some ToolbarContent {
        ToolbarItemGroup(placement: .automatic) {
            Button {
                store.isPresentingNewTaskSheet = true
            } label: {
                Image(systemName: "plus")
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
