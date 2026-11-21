import Foundation
import SwiftUI

enum AppThemeKind: String, CaseIterable, Codable, Identifiable {
    case classic = "classic"
    case westlake = "westlake"
    case autumn = "autumn"
    case goldenGate = "golden_gate"
    case snowPeak = "snow_peak"
    case deepForest = "deep_forest"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .classic: return "纯净晴空"
        case .westlake: return "西湖烟雨青"
        case .autumn: return "秋林枫叶橙"
        case .goldenGate: return "金门落日红"
        case .snowPeak: return "雪山冰川蓝"
        case .deepForest: return "深林暗夜"
        }
    }

    var subtitle: String {
        switch self {
        case .classic: return "清爽高对比白蓝 · 默认极简"
        case .westlake: return "江南青瓷碧水 · 远山断桥微插画"
        case .autumn: return "暖阳银杏琥珀 · 层林微插画"
        case .goldenGate: return "朱砂海湾霞光 · 长桥微插画"
        case .snowPeak: return "凛冽日照雪峰 · 冰川微插画"
        case .deepForest: return "护眼松石暗色 · 星夜松影微插画"
        }
    }

    var isDark: Bool {
        self == .deepForest
    }

    var primaryColor: Color {
        switch self {
        case .classic: return Color(red: 0.23, green: 0.51, blue: 0.96)
        case .westlake: return Color(red: 0.05, green: 0.58, blue: 0.53)
        case .autumn: return Color(red: 0.92, green: 0.35, blue: 0.05)
        case .goldenGate: return Color(red: 0.86, green: 0.15, blue: 0.15)
        case .snowPeak: return Color(red: 0.01, green: 0.52, blue: 0.78)
        case .deepForest: return Color(red: 0.06, green: 0.73, blue: 0.51)
        }
    }

    var accentColor: Color {
        switch self {
        case .classic: return Color(red: 0.49, green: 0.23, blue: 0.93)
        case .westlake: return Color(red: 0.01, green: 0.52, blue: 0.78)
        case .autumn: return Color(red: 0.85, green: 0.47, blue: 0.02)
        case .goldenGate: return Color(red: 0.88, green: 0.11, blue: 0.28)
        case .snowPeak: return Color(red: 0.31, green: 0.27, blue: 0.90)
        case .deepForest: return Color(red: 0.22, green: 0.74, blue: 0.97)
        }
    }

    var canvasBackground: Color {
        switch self {
        case .classic: return Color(red: 0.96, green: 0.97, blue: 0.99)
        case .westlake: return Color(red: 0.93, green: 0.97, blue: 0.96)
        case .autumn: return Color(red: 0.99, green: 0.96, blue: 0.94)
        case .goldenGate: return Color(red: 1.00, green: 0.96, blue: 0.96)
        case .snowPeak: return Color(red: 0.93, green: 0.96, blue: 1.00)
        case .deepForest: return Color(red: 0.04, green: 0.08, blue: 0.07)
        }
    }

    var surfaceBackground: Color {
        switch self {
        case .deepForest: return Color(red: 0.08, green: 0.14, blue: 0.12)
        default: return Color.white
        }
    }

    var railSelectedBg: Color {
        primaryColor.opacity(isDark ? 0.22 : 0.12)
    }
}

enum ModuleToggleKey: String, CaseIterable, Identifiable {
    case calendar = "calendar"
    case focus = "focus"
    case notes = "notes"
    case journal = "journal"
    case countdown = "countdown"
    case supervision = "supervision"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .calendar: return "📅 日历视图"
        case .focus: return "🍅 番茄专注中心"
        case .notes: return "📝 随记（备忘 & 日记）"
        case .journal: return "📖 每日心情日记子模块"
        case .countdown: return "⏳ 倒数纪念日胶囊"
        case .supervision: return "👥 好友监工看板"
        }
    }

    var subtitle: String {
        switch self {
        case .calendar: return "关闭后从 5 栏主导航隐身"
        case .focus: return "关闭后从 5 栏主导航隐身"
        case .notes: return "关闭后从 5 栏主导航隐身"
        case .journal: return "随记页内一日一页心情记录"
        case .countdown: return "待办页顶部临期倒数日胶囊提醒"
        case .supervision: return "关闭后从顶栏与待办流隐身"
        }
    }
}

struct SpacePreferencesResponse: Codable {
    let spaceId: String
    let themeId: String
    let enabledModules: [String: Bool]
    let pomodoroMinutes: Int?
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case spaceId = "space_id"
        case themeId = "theme_id"
        case enabledModules = "enabled_modules"
        case pomodoroMinutes = "pomodoro_minutes"
        case updatedAt = "updated_at"
    }
}

struct RawNoteStreamItem: Codable, Identifiable, Hashable {
    let noteId: String
    let spaceId: String
    let content: String
    let sourceType: String
    let revision: Int
    let eventTime: String
    let createdAt: String
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
        case media
    }
}

struct RawNoteListResponse: Codable {
    let spaceId: String
    let count: Int
    let notes: [RawNoteStreamItem]

    enum CodingKeys: String, CodingKey {
        case spaceId = "space_id"
        case count
        case notes
    }
}

/// Pure SwiftUI vector micro-illustration rendered in the top bar (< 0 KB asset footprint).
struct ThemeHeaderArtworkView: View {
    let theme: AppThemeKind

    var body: some View {
        Canvas { context, size in
            let w = size.width
            let h = size.height
            switch theme {
            case .classic:
                let c1 = Path(ellipseIn: CGRect(x: w - 90, y: -12, width: 54, height: 54))
                let c2 = Path(ellipseIn: CGRect(x: w - 48, y: 12, width: 42, height: 42))
                context.fill(c1, with: .color(theme.primaryColor.opacity(0.10)))
                context.fill(c2, with: .color(theme.accentColor.opacity(0.10)))
            case .westlake:
                var hill = Path()
                hill.move(to: CGPoint(x: 0, y: h))
                hill.addQuadCurve(to: CGPoint(x: w * 0.55, y: h * 0.68), control: CGPoint(x: w * 0.25, y: h * 0.22))
                hill.addQuadCurve(to: CGPoint(x: w, y: h * 0.35), control: CGPoint(x: w * 0.80, y: h * 0.85))
                hill.addLine(to: CGPoint(x: w, y: h))
                hill.closeSubpath()
                context.fill(hill, with: .color(theme.primaryColor.opacity(0.16)))
            case .autumn:
                var wave = Path()
                wave.move(to: CGPoint(x: 10, y: h))
                wave.addQuadCurve(to: CGPoint(x: w, y: h * 0.32), control: CGPoint(x: w * 0.5, y: h * 0.20))
                wave.addLine(to: CGPoint(x: w, y: h))
                wave.closeSubpath()
                context.fill(wave, with: .color(theme.primaryColor.opacity(0.15)))
                let sun = Path(ellipseIn: CGRect(x: w - 68, y: 8, width: 22, height: 22))
                context.fill(sun, with: .color(theme.accentColor.opacity(0.28)))
            case .goldenGate:
                let sun = Path(ellipseIn: CGRect(x: w - 74, y: 8, width: 28, height: 28))
                context.fill(sun, with: .color(theme.accentColor.opacity(0.22)))
                var bridge = Path()
                bridge.move(to: CGPoint(x: w - 130, y: h - 4))
                bridge.addLine(to: CGPoint(x: w - 130, y: 14))
                bridge.move(to: CGPoint(x: w - 58, y: h - 4))
                bridge.addLine(to: CGPoint(x: w - 58, y: 14))
                bridge.move(to: CGPoint(x: w - 155, y: h - 12))
                bridge.addQuadCurve(to: CGPoint(x: w - 94, y: h - 14), control: CGPoint(x: w - 130, y: 18))
                bridge.addQuadCurve(to: CGPoint(x: w - 30, y: h - 12), control: CGPoint(x: w - 58, y: 18))
                context.stroke(bridge, with: .color(theme.primaryColor.opacity(0.35)), lineWidth: 2)
            case .snowPeak:
                var p1 = Path()
                p1.move(to: CGPoint(x: w - 165, y: h))
                p1.addLine(to: CGPoint(x: w - 115, y: 12))
                p1.addLine(to: CGPoint(x: w - 65, y: h))
                p1.closeSubpath()
                context.fill(p1, with: .color(theme.primaryColor.opacity(0.17)))
                var p2 = Path()
                p2.move(to: CGPoint(x: w - 95, y: h))
                p2.addLine(to: CGPoint(x: w - 50, y: 16))
                p2.addLine(to: CGPoint(x: w - 8, y: h))
                p2.closeSubpath()
                context.fill(p2, with: .color(theme.accentColor.opacity(0.17)))
            case .deepForest:
                for (dx, hTop) in [(w - 115, CGFloat(18)), (w - 82, CGFloat(12)), (w - 48, CGFloat(20))] {
                    var tree = Path()
                    tree.move(to: CGPoint(x: dx - 14, y: h))
                    tree.addLine(to: CGPoint(x: dx, y: hTop))
                    tree.addLine(to: CGPoint(x: dx + 14, y: h))
                    tree.closeSubpath()
                    context.fill(tree, with: .color(theme.primaryColor.opacity(0.28)))
                }
            }
        }
        .frame(width: 220, height: 54)
        .allowsHitTesting(false)
    }
}

enum UnifiedTodoSubmode: String, CaseIterable, Identifiable {
    case overview = "概览"
    case matrix = "四象限"
    case list = "列表"

    var id: String { rawValue }
}

/// Converged "待办" primary tab combining Overview, 4-Quadrant Matrix, and Priority List.
struct UnifiedTodoHostView: View {
    @EnvironmentObject var store: TodoStore
    @State private var submode: UnifiedTodoSubmode = .overview

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                if store.isModuleEnabled(.countdown) {
                    TodoCountdownCapsuleStripView()
                } else {
                    Text("📋 待办中心")
                        .font(.subheadline.bold())
                        .foregroundStyle(.secondary)
                }

                Spacer()

                Picker("视图", selection: $submode) {
                    ForEach(UnifiedTodoSubmode.allCases) { mode in
                        Text(mode.rawValue).tag(mode)
                    }
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 230)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 9)
            .background(Color.primary.opacity(0.025))

            Divider()

            switch submode {
            case .overview:
                DashboardOverviewView()
            case .matrix:
                QuadrantMatrixView()
            case .list:
                SortedTaskListView()
            }
        }
    }

    private var nearestCountdownInfo: (badge: String, title: String)? {
        let fmt = ISO8601DateFormatter()
        let todos = (store.matrix?.sortedTasks ?? []).filter { !$0.payload.isDone && ($0.payload.dueAt?.isEmpty == false) }
        var best: (Date, TaskItemModel)?
        for t in todos {
            guard let raw = t.payload.dueAt else { continue }
            let parsed = fmt.date(from: raw) ?? ISO8601DateFormatter().date(from: String(raw.prefix(19)) + "Z")
            if let d = parsed {
                if best == nil || d < best!.0 {
                    best = (d, t)
                }
            }
        }
        guard let (dueDate, task) = best else { return nil }
        let days = Int(ceil(dueDate.timeIntervalSinceNow / 86400.0))
        let badge = days < 0 ? "逾期 \(abs(days)) 天" : (days == 0 ? "就在今天" : "还有 \(days) 天")
        return (badge, task.title)
    }
}

/// Zero-duplication Calendar view projecting tasks, focus sessions, notes, and countdowns.
struct CalendarPreviewHostView: View {
    var body: some View {
        CalendarTimelineView()
    }
}

/// Unified "随记" view hosting Pinned Memos & One-Page-Per-Day Mood Journal on the RawNote kernel.
struct QuickNotesStreamView: View {
    var body: some View {
        MemoAndJournalStudioView()
    }
}

/// Reusable Appearance Theme Gallery & Module Toggles sections inside SettingsSyncView.
struct ThemeAndModulesSettingsSections: View {
    @EnvironmentObject var store: TodoStore

    var body: some View {
        Section("🎨 外观主题画廊（纯 Token 矢量 · 跨端同步）") {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 145), spacing: 10)], spacing: 10) {
                ForEach(AppThemeKind.allCases) { theme in
                    let isSel = store.activeTheme == theme
                    Button {
                        Task { await store.selectTheme(theme) }
                    } label: {
                        VStack(alignment: .leading, spacing: 5) {
                            HStack {
                                Circle().fill(theme.primaryColor).frame(width: 14, height: 14)
                                Circle().fill(theme.accentColor).frame(width: 14, height: 14)
                                Spacer()
                                if isSel {
                                    Image(systemName: "checkmark.circle.fill")
                                        .foregroundStyle(theme.primaryColor)
                                }
                            }
                            Text(theme.title)
                                .font(.subheadline.bold())
                                .foregroundStyle(.primary)
                            Text(theme.subtitle)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(theme.primaryColor.opacity(isSel ? 0.12 : 0.04))
                        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .stroke(isSel ? theme.primaryColor : Color.primary.opacity(0.1), lineWidth: isSel ? 2 : 1)
                        )
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.vertical, 4)
        }

        Section("🧩 可插拔模块开关（防臃肿 · 隐藏不常用入口）") {
            ForEach(ModuleToggleKey.allCases) { key in
                Toggle(isOn: Binding(
                    get: { store.isModuleEnabled(key) },
                    set: { newVal in
                        Task { await store.setModuleEnabled(key, enabled: newVal) }
                    }
                )) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(key.title)
                            .font(.subheadline.weight(.medium))
                        Text(key.subtitle)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }
}
