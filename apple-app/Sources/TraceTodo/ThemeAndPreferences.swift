import Foundation
import SwiftUI

enum AppThemeKind: String, CaseIterable, Codable, Identifiable {
    case classic = "classic"
    case westlake = "westlake"
    case autumn = "autumn"
    case goldenGate = "golden_gate"
    case snowPeak = "snow_peak"
    case sakura = "sakura"
    case warmIvory = "warm_ivory"
    case celadonMist = "celadon_mist"
    case tideBlue = "tide_blue"
    case deepForest = "deep_forest"
    case nebula = "nebula"
    case cyber = "cyber"
    case polar = "polar"
    case velvetNoir = "velvet_noir"
    case obsidianGold = "obsidian_gold"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .classic: return "纯净晴空"
        case .westlake: return "西湖烟雨青"
        case .autumn: return "秋林枫叶橙"
        case .goldenGate: return "金门落日红"
        case .snowPeak: return "雪山冰川蓝"
        case .sakura: return "樱语粉霞"
        case .warmIvory: return "暖阳白衫"
        case .celadonMist: return "青竹烟雨"
        case .tideBlue: return "海盐潮蓝"
        case .deepForest: return "深林暗夜"
        case .nebula: return "星穹紫夜"
        case .cyber: return "赛博霓虹"
        case .polar: return "极光夜航"
        case .velvetNoir: return "丝绒夜宴"
        case .obsidianGold: return "黑曜流金"
        }
    }

    var subtitle: String {
        switch self {
        case .classic: return "清爽高对比白蓝 · 默认极简"
        case .westlake: return "江南青瓷碧水 · 远山断桥微插画"
        case .autumn: return "暖阳银杏琥珀 · 层林微插画"
        case .goldenGate: return "朱砂海湾霞光 · 长桥微插画"
        case .snowPeak: return "凛冽日照雪峰 · 冰川微插画"
        case .sakura: return "暮樱粉紫霞光 · 落瓣微插画"
        case .warmIvory: return "蜜拿铁亚麻米白 · 书院暖阳"
        case .celadonMist: return "墨竹天青烟雨 · 宋韵清雅"
        case .tideBlue: return "清透海盐橘落日 · 晴海潮风"
        case .deepForest: return "护眼松石暗色 · 星夜松影微插画"
        case .nebula: return "银河星云暗色 · 紫罗兰霓虹微光"
        case .cyber: return "夜城雨雾青品红 · 全息光轨"
        case .polar: return "冰原极光松石暗色 · 雪夜流萤"
        case .velvetNoir: return "微醺玫紫丝绒 · 都市夜风"
        case .obsidianGold: return "黑曜熔金松石影 · 暗夜电影"
        }
    }

    var isDark: Bool {
        switch self {
        case .deepForest, .nebula, .cyber, .polar, .velvetNoir, .obsidianGold:
            return true
        default:
            return false
        }
    }

    var primaryColor: Color {
        switch self {
        case .classic: return Color(red: 0.23, green: 0.51, blue: 0.96)
        case .westlake: return Color(red: 0.05, green: 0.58, blue: 0.53)
        case .autumn: return Color(red: 0.92, green: 0.35, blue: 0.05)
        case .goldenGate: return Color(red: 0.86, green: 0.15, blue: 0.15)
        case .snowPeak: return Color(red: 0.01, green: 0.52, blue: 0.78)
        case .sakura: return Color(red: 0.86, green: 0.15, blue: 0.47)
        case .warmIvory: return Color(red: 0.63, green: 0.38, blue: 0.03)
        case .celadonMist: return Color(red: 0.05, green: 0.45, blue: 0.56)
        case .tideBlue: return Color(red: 0.01, green: 0.41, blue: 0.63)
        case .deepForest: return Color(red: 0.06, green: 0.73, blue: 0.51)
        case .nebula: return Color(red: 0.55, green: 0.36, blue: 0.96)
        case .cyber: return Color(red: 0.02, green: 0.71, blue: 0.83)
        case .polar: return Color(red: 0.18, green: 0.83, blue: 0.75)
        case .velvetNoir: return Color(red: 0.91, green: 0.47, blue: 0.98)
        case .obsidianGold: return Color(red: 0.92, green: 0.70, blue: 0.03)
        }
    }

    var accentColor: Color {
        switch self {
        case .classic: return Color(red: 0.49, green: 0.23, blue: 0.93)
        case .westlake: return Color(red: 0.01, green: 0.52, blue: 0.78)
        case .autumn: return Color(red: 0.85, green: 0.47, blue: 0.02)
        case .goldenGate: return Color(red: 0.88, green: 0.11, blue: 0.28)
        case .snowPeak: return Color(red: 0.31, green: 0.27, blue: 0.90)
        case .sakura: return Color(red: 0.66, green: 0.33, blue: 0.97)
        case .warmIvory: return Color(red: 0.05, green: 0.58, blue: 0.53)
        case .celadonMist: return Color(red: 0.08, green: 0.50, blue: 0.24)
        case .tideBlue: return Color(red: 0.98, green: 0.45, blue: 0.09)
        case .deepForest: return Color(red: 0.22, green: 0.74, blue: 0.97)
        case .nebula: return Color(red: 0.93, green: 0.28, blue: 0.60)
        case .cyber: return Color(red: 0.85, green: 0.27, blue: 0.94)
        case .polar: return Color(red: 0.51, green: 0.55, blue: 0.97)
        case .velvetNoir: return Color(red: 0.49, green: 0.23, blue: 0.93)
        case .obsidianGold: return Color(red: 0.18, green: 0.83, blue: 0.75)
        }
    }

    var canvasBackground: Color {
        switch self {
        case .classic: return Color(red: 0.96, green: 0.97, blue: 0.99)
        case .westlake: return Color(red: 0.93, green: 0.97, blue: 0.96)
        case .autumn: return Color(red: 0.99, green: 0.96, blue: 0.94)
        case .goldenGate: return Color(red: 1.00, green: 0.96, blue: 0.96)
        case .snowPeak: return Color(red: 0.93, green: 0.96, blue: 1.00)
        case .sakura: return Color(red: 0.99, green: 0.95, blue: 0.97)
        case .warmIvory: return Color(red: 0.98, green: 0.96, blue: 0.93)
        case .celadonMist: return Color(red: 0.95, green: 0.97, blue: 0.96)
        case .tideBlue: return Color(red: 0.94, green: 0.97, blue: 0.99)
        case .deepForest: return Color(red: 0.04, green: 0.08, blue: 0.07)
        case .nebula: return Color(red: 0.05, green: 0.04, blue: 0.12)
        case .cyber: return Color(red: 0.02, green: 0.06, blue: 0.10)
        case .polar: return Color(red: 0.03, green: 0.07, blue: 0.09)
        case .velvetNoir: return Color(red: 0.08, green: 0.04, blue: 0.09)
        case .obsidianGold: return Color(red: 0.04, green: 0.04, blue: 0.06)
        }
    }

    var surfaceBackground: Color {
        switch self {
        case .deepForest: return Color(red: 0.08, green: 0.14, blue: 0.12)
        case .nebula: return Color(red: 0.09, green: 0.08, blue: 0.20)
        case .cyber: return Color(red: 0.05, green: 0.11, blue: 0.15)
        case .polar: return Color(red: 0.05, green: 0.13, blue: 0.15)
        case .velvetNoir: return Color(red: 0.13, green: 0.07, blue: 0.15)
        case .obsidianGold: return Color(red: 0.08, green: 0.09, blue: 0.11)
        default: return Color.white
        }
    }

    /// High-contrast fill color for white-text primary buttons and active duration pills (matches Web `--primary-fill`).
    var primaryFillColor: Color {
        switch self {
        case .westlake: return Color(red: 0.06, green: 0.46, blue: 0.43)
        case .autumn: return Color(red: 0.76, green: 0.25, blue: 0.05)
        case .warmIvory: return Color(red: 0.52, green: 0.30, blue: 0.05)
        case .tideBlue: return Color(red: 0.03, green: 0.35, blue: 0.52)
        case .deepForest: return Color(red: 0.02, green: 0.59, blue: 0.41)
        case .nebula: return Color(red: 0.49, green: 0.23, blue: 0.93)
        case .cyber: return Color(red: 0.05, green: 0.45, blue: 0.56)
        case .polar: return Color(red: 0.05, green: 0.58, blue: 0.53)
        case .velvetNoir: return Color(red: 0.64, green: 0.11, blue: 0.69)
        case .obsidianGold: return Color(red: 0.63, green: 0.38, blue: 0.03)
        default: return primaryColor
        }
    }

    var railSelectedBg: Color {
        primaryColor.opacity(isDark ? 0.22 : 0.12)
    }
}

// MARK: - Ambient Portrait & Nature Scene Catalog (Synced with Web SCENE_CATALOG)

enum AmbientSceneCategory: String, CaseIterable, Identifiable {
    case all = "all"
    case male = "male"
    case female = "female"
    case nature = "nature"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .all: return "全部"
        case .male: return "✨ 帅哥"
        case .female: return "🌸 美女"
        case .nature: return "🌌 自然科幻"
        }
    }
}

struct AmbientSceneItem: Identifiable, Equatable {
    let id: String
    let name: String
    let subtitle: String
    let category: AmbientSceneCategory
    let icon: String
    let accentColor: Color
    let pairedTheme: AppThemeKind?

    var isPortrait: Bool {
        category == .male || category == .female
    }

    static let catalog: [AmbientSceneItem] = [
        AmbientSceneItem(id: "none", name: "无场景", subtitle: "纯净无壁纸背景", category: .all, icon: "🪽", accentColor: .purple, pairedTheme: nil),
        // Male Series (4)
        AmbientSceneItem(id: "portrait_m_sunny", name: "阳光少年", subtitle: "暖阳白衫 · 清爽书院学长", category: .male, icon: "☀️", accentColor: Color(red: 0.96, green: 0.62, blue: 0.04), pairedTheme: .warmIvory),
        AmbientSceneItem(id: "portrait_m_ancient", name: "竹影公子", subtitle: "青竹烟雨 · 温润古风雅士", category: .male, icon: "🎋", accentColor: Color(red: 0.20, green: 0.83, blue: 0.60), pairedTheme: .celadonMist),
        AmbientSceneItem(id: "portrait_m_sport", name: "海风型男", subtitle: "晴海落日 · 清爽运动少年", category: .male, icon: "🌊", accentColor: Color(red: 0.22, green: 0.74, blue: 0.97), pairedTheme: .tideBlue),
        AmbientSceneItem(id: "portrait_m", name: "禁欲绅士", subtitle: "高定西装 · 都市暗夜电影", category: .male, icon: "🕴️", accentColor: Color(red: 0.98, green: 0.75, blue: 0.14), pairedTheme: .obsidianGold),
        // Female Series (8)
        AmbientSceneItem(id: "portrait_f_cool", name: "清冷御姐", subtitle: "暮色天际 · 都市高智晚风", category: .female, icon: "💎", accentColor: Color(red: 0.96, green: 0.45, blue: 0.71), pairedTheme: .velvetNoir),
        AmbientSceneItem(id: "portrait_f_hanfu", name: "江南仕女", subtitle: "烟雨水榭 · 宋韵汉服清雅", category: .female, icon: "🏮", accentColor: Color(red: 0.18, green: 0.83, blue: 0.75), pairedTheme: .celadonMist),
        AmbientSceneItem(id: "portrait_f_vitality", name: "元气甜妹", subtitle: "晴空海湾 · 明媚治愈少女", category: .female, icon: "🌻", accentColor: Color(red: 0.22, green: 0.74, blue: 0.97), pairedTheme: .warmIvory),
        AmbientSceneItem(id: "portrait_f", name: "樱花佳人", subtitle: "暮樱漫舞 · 温柔浪漫写真", category: .female, icon: "🌸", accentColor: Color(red: 0.98, green: 0.66, blue: 0.83), pairedTheme: .sakura),
        AmbientSceneItem(id: "portrait_f_campus", name: "奶糖学妹", subtitle: "双马尾学院风 · 银杏暖阳甜笑", category: .female, icon: "🎀", accentColor: Color(red: 0.98, green: 0.66, blue: 0.83), pairedTheme: .warmIvory),
        AmbientSceneItem(id: "portrait_f_snow", name: "雪域红梅", subtitle: "红氅白雪落梅 · 清冽冬日写真", category: .female, icon: "❄️", accentColor: Color(red: 0.88, green: 0.11, blue: 0.28), pairedTheme: .snowPeak),
        AmbientSceneItem(id: "portrait_f_lolita", name: "洋装甜心", subtitle: "洛丽塔蕾丝茶会 · 粉霞玫瑰", category: .female, icon: "🍰", accentColor: Color(red: 0.96, green: 0.45, blue: 0.71), pairedTheme: .sakura),
        AmbientSceneItem(id: "portrait_f_cyber", name: "赛博歌姬", subtitle: "全息霓虹舞台 · 电音偶像光轨", category: .female, icon: "🎤", accentColor: Color(red: 0.91, green: 0.47, blue: 0.98), pairedTheme: .cyber),
        // Nature & Sci-Fi Series (6)
        AmbientSceneItem(id: "starfield", name: "银河星空", subtitle: "璀璨星云 · 深空静谧冥想", category: .nature, icon: "🌌", accentColor: Color(red: 0.65, green: 0.55, blue: 0.98), pairedTheme: .nebula),
        AmbientSceneItem(id: "scifi", name: "科幻夜城", subtitle: "未来天际 · 全息流光夜雨", category: .nature, icon: "🏙️", accentColor: Color(red: 0.13, green: 0.83, blue: 0.93), pairedTheme: .cyber),
        AmbientSceneItem(id: "neon", name: "霓虹光轨", subtitle: "赛博街景 · 炫彩电音律动", category: .nature, icon: "⚡", accentColor: Color(red: 0.22, green: 0.74, blue: 0.97), pairedTheme: .cyber),
        AmbientSceneItem(id: "nature", name: "森林秘境", subtitle: "晨雾松林 · 萤火虫呼吸感", category: .nature, icon: "🌲", accentColor: Color(red: 0.20, green: 0.83, blue: 0.60), pairedTheme: .deepForest),
        AmbientSceneItem(id: "aurora", name: "极地极光", subtitle: "冰原雪峰 · 绿紫极光漫舞", category: .nature, icon: "🌠", accentColor: Color(red: 0.37, green: 0.92, blue: 0.83), pairedTheme: .polar),
        AmbientSceneItem(id: "ocean", name: "深海之梦", subtitle: "蔚蓝光束 · 静谧深潜气泡", category: .nature, icon: "🌊", accentColor: Color(red: 0.13, green: 0.83, blue: 0.93), pairedTheme: .snowPeak),
    ]

    static func byId(_ id: String) -> AmbientSceneItem {
        catalog.first(where: { $0.id == id }) ?? catalog[0]
    }
}

// MARK: - Disk-Cached Remote Scene Image Loader

@MainActor
final class SceneWallpaperCache: ObservableObject {
    static let shared = SceneWallpaperCache()
    @Published private var loadedData: [String: Data] = [:]
    private var inFlight: Set<String> = []

    private var cacheFolder: URL {
        let base = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first!
        let dir = base.appendingPathComponent("TraceTodoSceneWallpapers", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    func imageData(for sceneId: String, serverURL: String) -> Data? {
        guard !sceneId.isEmpty, sceneId != "none" else { return nil }
        if let mem = loadedData[sceneId] {
            return mem
        }
        let diskURL = cacheFolder.appendingPathComponent("\(sceneId).jpg")
        if let disk = try? Data(contentsOf: diskURL), disk.count > 1024 {
            loadedData[sceneId] = disk
            return disk
        }
        fetchIfNeeded(sceneId: sceneId, serverURL: serverURL)
        return nil
    }

    func fetchIfNeeded(sceneId: String, serverURL: String) {
        guard !sceneId.isEmpty, sceneId != "none", !inFlight.contains(sceneId) else { return }
        let cleanBase = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: "\(cleanBase)/scenes/\(sceneId).jpg") else { return }
        inFlight.insert(sceneId)
        let diskURL = cacheFolder.appendingPathComponent("\(sceneId).jpg")
        Task {
            defer { inFlight.remove(sceneId) }
            do {
                let (data, resp) = try await URLSession.shared.data(from: url)
                if let http = resp as? HTTPURLResponse, http.statusCode == 200, data.count > 1024 {
                    try? data.write(to: diskURL, options: .atomic)
                    self.loadedData[sceneId] = data
                }
            } catch {
                // Keep silent on offline; next render retries cleanly
            }
        }
    }
}

struct RemoteSceneWallpaperImage: View {
    let sceneId: String
    let serverURL: String
    @ObservedObject private var cache = SceneWallpaperCache.shared

    var body: some View {
        GeometryReader { geo in
            if let data = cache.imageData(for: sceneId, serverURL: serverURL) {
                #if os(macOS)
                if let nsImg = NSImage(data: data) {
                    Image(nsImage: nsImg)
                        .resizable()
                        .scaledToFill()
                        .frame(width: geo.size.width, height: geo.size.height, alignment: Alignment(horizontal: .trailing, vertical: .top))
                        .clipped()
                }
                #else
                if let uiImg = UIImage(data: data) {
                    Image(uiImage: uiImg)
                        .resizable()
                        .scaledToFill()
                        .frame(width: geo.size.width, height: geo.size.height, alignment: Alignment(horizontal: .trailing, vertical: .top))
                        .clipped()
                }
                #endif
            } else {
                let item = AmbientSceneItem.byId(sceneId)
                LinearGradient(
                    colors: [item.accentColor.opacity(0.28), Color.black.opacity(0.18)],
                    startPoint: .topTrailing,
                    endPoint: .bottomLeading
                )
            }
        }
    }
}

/// Full-window ambient scene wallpaper + scrim + subtle drifting bokeh/star particles (matches Web `#ambientSceneLayer`).
struct AmbientSceneBackgroundView: View {
    @EnvironmentObject var store: TodoStore

    private static let intensityOpacities: [Double] = [0.0, 0.26, 0.48, 0.72]

    var body: some View {
        ZStack {
            store.activeTheme.canvasBackground

            if store.hasActiveScene {
                let sc = store.activeSceneItem
                let idx = min(3, max(0, store.sceneIntensity))
                let imgOpacity = Self.intensityOpacities[idx]

                RemoteSceneWallpaperImage(sceneId: sc.id, serverURL: store.serverURL)
                    .opacity(imgOpacity)
                    .animation(.easeInOut(duration: 0.45), value: sc.id)

                LinearGradient(
                    colors: [
                        store.activeTheme.canvasBackground.opacity(0.36),
                        store.activeTheme.canvasBackground.opacity(0.58),
                        store.activeTheme.canvasBackground.opacity(0.76),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )

                if store.sceneMotion {
                    AmbientParticleCanvasView(accent: sc.accentColor, primary: store.activeTheme.primaryColor)
                        .opacity(0.65)
                }
            }
        }
        .ignoresSafeArea()
        .allowsHitTesting(false)
    }
}

struct AmbientParticleCanvasView: View {
    let accent: Color
    let primary: Color

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 20.0)) { timeline in
            let t = timeline.date.timeIntervalSinceReferenceDate
            Canvas { context, size in
                guard size.width > 10, size.height > 10 else { return }
                for i in 0..<18 {
                    let seed = Double(i) * 1.731
                    let xFrac = (sin(seed * 2.3 + t * 0.08) * 0.5 + 0.5)
                    let yRaw = (seed * 0.19 - t * (0.015 + Double(i % 4) * 0.004)).truncatingRemainder(dividingBy: 1.0)
                    let yFrac = yRaw < 0 ? yRaw + 1.0 : yRaw
                    let radius = CGFloat(2.2 + Double(i % 5) * 1.3)
                    let alpha = 0.16 + 0.20 * (0.5 + 0.5 * sin(t * 0.9 + seed))
                    let rect = CGRect(
                        x: CGFloat(xFrac) * size.width - radius,
                        y: CGFloat(yFrac) * size.height - radius,
                        width: radius * 2,
                        height: radius * 2
                    )
                    let col = (i % 2 == 0 ? accent : primary).opacity(alpha)
                    context.fill(Path(ellipseIn: rect), with: .color(col))
                }
            }
        }
        .allowsHitTesting(false)
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

struct SpacePreferencesExtra: Codable {
    let sceneId: String?
    let sceneIntensity: Int?
    let sceneMotion: Int?

    enum CodingKeys: String, CodingKey {
        case sceneId = "scene_id"
        case sceneIntensity = "scene_intensity"
        case sceneMotion = "scene_motion"
    }
}

struct SpacePreferencesResponse: Codable {
    let spaceId: String
    let themeId: String
    let enabledModules: [String: Bool]
    let pomodoroMinutes: Int?
    let extra: SpacePreferencesExtra?
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case spaceId = "space_id"
        case themeId = "theme_id"
        case enabledModules = "enabled_modules"
        case pomodoroMinutes = "pomodoro_minutes"
        case extra
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
            default:
                let orb1 = Path(ellipseIn: CGRect(x: w - 98, y: -8, width: 52, height: 52))
                let orb2 = Path(ellipseIn: CGRect(x: w - 52, y: 10, width: 38, height: 38))
                context.fill(orb1, with: .color(theme.primaryColor.opacity(theme.isDark ? 0.24 : 0.14)))
                context.fill(orb2, with: .color(theme.accentColor.opacity(theme.isDark ? 0.22 : 0.14)))
                var arc = Path()
                arc.move(to: CGPoint(x: w - 150, y: h - 6))
                arc.addQuadCurve(to: CGPoint(x: w - 8, y: h * 0.45), control: CGPoint(x: w - 80, y: 12))
                context.stroke(arc, with: .color(theme.primaryColor.opacity(0.38)), lineWidth: 2)
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

/// Converged "待办" primary tab combining Overview, 4-Quadrant Matrix, and Priority List, plus Quick Scope Filters.
struct UnifiedTodoHostView: View {
    @EnvironmentObject var store: TodoStore
    @State private var submode: UnifiedTodoSubmode = .overview

    var body: some View {
        VStack(spacing: 0) {
            VStack(spacing: 8) {
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
                    .frame(maxWidth: 220)
                }

                // Quick Scope Filter Pills matching Web `#todoQuickScopeBar`
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(TaskQuickScope.allCases) { scope in
                            let isSel = store.taskQuickScope == scope
                            let cnt = store.quickScopeCount(scope)
                            Button {
                                withAnimation(.easeInOut(duration: 0.16)) {
                                    store.taskQuickScope = scope
                                }
                            } label: {
                                HStack(spacing: 4) {
                                    Text(scope.label)
                                        .font(.system(size: 11.5, weight: isSel ? .bold : .medium))
                                    Text("\(cnt)")
                                        .font(.system(size: 10.5, weight: .bold, design: .rounded))
                                        .opacity(isSel ? 0.92 : 0.65)
                                }
                                .padding(.horizontal, 11)
                                .padding(.vertical, 5)
                                .background(isSel ? store.activeTheme.primaryFillColor : Color.primary.opacity(0.06))
                                .foregroundStyle(isSel ? Color.white : Color.primary)
                                .clipShape(Capsule())
                            }
                            .buttonStyle(.plain)
                        }
                        Spacer(minLength: 0)
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 9)
            .background(store.cardSurfaceBackground.opacity(0.75))

            Divider()

            OptimisticIngestBannerStackView()

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
}

/// Zero-duplication Calendar view projecting tasks, focus sessions, notes, and countdowns.
struct CalendarPreviewHostView: View {
    var body: some View {
        CalendarTimelineView()
    }
}

/// Instant 0ms optimistic pending task cards shown while background AI extraction is running.
struct OptimisticIngestBannerStackView: View {
    @EnvironmentObject var store: TodoStore

    var body: some View {
        if !store.optimisticIngestItems.isEmpty {
            VStack(spacing: 8) {
                ForEach(store.optimisticIngestItems) { item in
                    HStack(spacing: 10) {
                        ProgressView()
                            .controlSize(.small)
                            .tint(store.activeTheme.primaryColor)

                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.text)
                                .font(.system(size: 13.5, weight: .semibold))
                                .foregroundStyle(.primary)
                                .lineLimit(1)
                            Text("✨ 原话已秒存，AI 正在解析时间与四象限优先级…")
                                .font(.system(size: 11.5))
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }

                        Spacer()

                        Text("AI 解析中")
                            .font(.system(size: 11, weight: .bold))
                            .foregroundStyle(store.activeTheme.primaryColor)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(store.activeTheme.primaryColor.opacity(0.12))
                            .clipShape(Capsule())
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(store.activeTheme.surfaceBackground)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .strokeBorder(
                                store.activeTheme.primaryColor.opacity(0.45),
                                style: StrokeStyle(lineWidth: 1.1, dash: [5, 4])
                            )
                    )
                    .transition(.move(edge: .top).combined(with: .opacity))
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 10)
            .padding(.bottom, 4)
        }
    }
}

/// Unified "随记" view hosting Pinned Memos & One-Page-Per-Day Mood Journal on the RawNote kernel.
struct QuickNotesStreamView: View {
    var body: some View {
        MemoAndJournalStudioView()
    }
}

/// Reusable Appearance Theme Gallery (Light/Dark grouped), Portrait/Nature Scene Gallery, & Module Toggles inside SettingsSyncView.
struct ThemeAndModulesSettingsSections: View {
    @EnvironmentObject var store: TodoStore
    @State private var sceneCategory: AmbientSceneCategory = .all

    private var filteredScenes: [AmbientSceneItem] {
        if sceneCategory == .all {
            return AmbientSceneItem.catalog
        }
        return AmbientSceneItem.catalog.filter { $0.category == sceneCategory }
    }

    var body: some View {
        Section("🎨 精选矢量外观主题（亮/暗分组 · 跨端同步）") {
            VStack(alignment: .leading, spacing: 14) {
                themeGroupBlock(isDark: false, title: "☀️ 亮色主题")
                Divider()
                themeGroupBlock(isDark: true, title: "🌙 暗色主题")
            }
            .padding(.vertical, 4)
        }

        Section("✨ 人物写真与环境场景主题（帅哥 · 美女 · 自然科幻）") {
            VStack(alignment: .leading, spacing: 12) {
                Toggle("🎨 点击场景时自动联动契合主题配色", isOn: $store.syncSceneTheme)
                    .font(.caption.weight(.semibold))

                Picker("分类", selection: $sceneCategory) {
                    ForEach(AmbientSceneCategory.allCases) { cat in
                        Text(cat.label).tag(cat)
                    }
                }
                .pickerStyle(.segmented)

                LazyVGrid(columns: [GridItem(.adaptive(minimum: 138), spacing: 10)], spacing: 10) {
                    ForEach(filteredScenes) { sc in
                        let isSel = store.activeSceneId == sc.id
                        Button {
                            Task { await store.selectScene(sc) }
                        } label: {
                            VStack(alignment: .leading, spacing: 5) {
                                ZStack(alignment: .topTrailing) {
                                    if sc.id == "none" {
                                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                                            .fill(Color.primary.opacity(0.05))
                                            .frame(height: 72)
                                            .overlay(Text("🪽").font(.title3))
                                    } else {
                                        RemoteSceneWallpaperImage(sceneId: sc.id, serverURL: store.serverURL)
                                            .frame(height: 72)
                                            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                                    }
                                    if isSel {
                                        Image(systemName: "checkmark.circle.fill")
                                            .font(.system(size: 16, weight: .bold))
                                            .foregroundStyle(.white, store.activeTheme.primaryFillColor)
                                            .padding(5)
                                    }
                                }
                                Text("\(sc.icon) \(sc.name)")
                                    .font(.caption.bold())
                                    .foregroundStyle(.primary)
                                    .lineLimit(1)
                                Text(sc.subtitle)
                                    .font(.system(size: 10))
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                            .padding(7)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(store.activeTheme.primaryColor.opacity(isSel ? 0.12 : 0.03))
                            .clipShape(RoundedRectangle(cornerRadius: 11, style: .continuous))
                            .overlay(
                                RoundedRectangle(cornerRadius: 11, style: .continuous)
                                    .stroke(isSel ? store.activeTheme.primaryColor : Color.primary.opacity(0.10), lineWidth: isSel ? 2 : 1)
                            )
                        }
                        .buttonStyle(.plain)
                    }
                }

                HStack(spacing: 10) {
                    Text("💡 全站氛围光强度")
                        .font(.caption.bold())
                    Picker("强度", selection: Binding(
                        get: { store.sceneIntensity },
                        set: { val in Task { await store.setSceneIntensity(val) } }
                    )) {
                        Text("关闭").tag(0)
                        Text("微光").tag(1)
                        Text("标准").tag(2)
                        Text("沉浸").tag(3)
                    }
                    .pickerStyle(.segmented)
                }

                Toggle("🪽 开启氛围微光粒子飘动", isOn: Binding(
                    get: { store.sceneMotion },
                    set: { val in Task { await store.setSceneMotion(val) } }
                ))
                .font(.caption)
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

    @ViewBuilder
    private func themeGroupBlock(isDark: Bool, title: String) -> some View {
        let list = AppThemeKind.allCases.filter { $0.isDark == isDark }
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(title)
                    .font(.caption.bold())
                    .foregroundStyle(.secondary)
                Spacer()
                Text("\(list.count) 套")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 142), spacing: 10)], spacing: 10) {
                ForEach(list) { theme in
                    let isSel = store.activeTheme == theme
                    Button {
                        Task { await store.selectTheme(theme) }
                    } label: {
                        VStack(alignment: .leading, spacing: 5) {
                            ZStack(alignment: .topLeading) {
                                RoundedRectangle(cornerRadius: 7, style: .continuous)
                                    .fill(
                                        LinearGradient(
                                            colors: [theme.primaryColor, theme.accentColor],
                                            startPoint: .topLeading,
                                            endPoint: .bottomTrailing
                                        )
                                    )
                                    .frame(height: 32)
                                HStack {
                                    Text(theme.isDark ? "🌙 暗色" : "☀️ 亮色")
                                        .font(.system(size: 9.5, weight: .bold))
                                        .padding(.horizontal, 6)
                                        .padding(.vertical, 2)
                                        .background(Color.white.opacity(0.92))
                                        .foregroundStyle(Color.black.opacity(0.85))
                                        .clipShape(Capsule())
                                    Spacer()
                                    if isSel {
                                        Image(systemName: "checkmark.circle.fill")
                                            .font(.system(size: 14, weight: .bold))
                                            .foregroundStyle(.white)
                                    }
                                }
                                .padding(.horizontal, 6)
                                .padding(.top, 6)
                            }
                            Text(theme.title)
                                .font(.subheadline.bold())
                                .foregroundStyle(.primary)
                                .lineLimit(1)
                            Text(theme.subtitle)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(theme.primaryColor.opacity(isSel ? 0.12 : 0.04))
                        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .stroke(isSel ? theme.primaryColor : Color.primary.opacity(0.10), lineWidth: isSel ? 2 : 1)
                        )
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}
