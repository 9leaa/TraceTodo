import SwiftUI

// MARK: - Countdown & Anniversary Models

struct CountdownItemModel: Codable, Identifiable, Hashable {
    let countdownId: String
    let spaceId: String
    let title: String
    let kind: String
    let calendarType: String
    let targetDate: String
    let effectiveSolarDate: String
    let displaySolarDate: String
    let weekdayLabel: String
    let lunarYear: Int
    let lunarMonth: Int
    let lunarDay: Int
    let isLeapMonth: Bool
    let lunarLabel: String
    let repeatRule: String
    let icon: String
    let colorTheme: String
    let pinned: Bool
    let note: String
    let isToday: Bool
    let badgeLabel: String
    let daysValue: Int
    let displayNumber: String
    let unitLabel: String
    let nextAnniversaryDays: Int?
    let capsuleText: String
    let headerBanner: String

    var id: String { countdownId }

    var bannerGradient: LinearGradient {
        let colors: [Color]
        switch colorTheme {
        case "crimson":
            colors = [Color(red: 0.86, green: 0.15, blue: 0.15), Color(red: 0.96, green: 0.25, blue: 0.37)]
        case "lavender":
            colors = [Color(red: 0.49, green: 0.23, blue: 0.93), Color(red: 0.65, green: 0.55, blue: 0.98)]
        case "pink":
            colors = [Color(red: 0.86, green: 0.15, blue: 0.47), Color(red: 0.96, green: 0.45, blue: 0.71)]
        case "mint":
            colors = [Color(red: 0.02, green: 0.59, blue: 0.41), Color(red: 0.20, green: 0.83, blue: 0.60)]
        case "peach":
            colors = [Color(red: 0.92, green: 0.35, blue: 0.05), Color(red: 0.98, green: 0.57, blue: 0.24)]
        case "butter":
            colors = [Color(red: 0.79, green: 0.54, blue: 0.02), Color(red: 0.95, green: 0.76, blue: 0.12)]
        default:
            colors = [Color(red: 0.01, green: 0.52, blue: 0.78), Color(red: 0.22, green: 0.74, blue: 0.97)]
        }
        return LinearGradient(colors: colors, startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    enum CodingKeys: String, CodingKey {
        case countdownId = "countdown_id"
        case spaceId = "space_id"
        case title, kind, icon, pinned, note
        case calendarType = "calendar_type"
        case targetDate = "target_date"
        case effectiveSolarDate = "effective_solar_date"
        case displaySolarDate = "display_solar_date"
        case weekdayLabel = "weekday_label"
        case lunarYear = "lunar_year"
        case lunarMonth = "lunar_month"
        case lunarDay = "lunar_day"
        case isLeapMonth = "is_leap_month"
        case lunarLabel = "lunar_label"
        case repeatRule = "repeat_rule"
        case colorTheme = "color_theme"
        case isToday = "is_today"
        case badgeLabel = "badge_label"
        case daysValue = "days_value"
        case displayNumber = "display_number"
        case unitLabel = "unit_label"
        case nextAnniversaryDays = "next_anniversary_days"
        case capsuleText = "capsule_text"
        case headerBanner = "header_banner"
    }
}

struct CountdownListResponse: Codable {
    let spaceId: String
    let todayDate: String
    let todayLunar: String
    let items: [CountdownItemModel]
    let topCapsules: [CountdownItemModel]
    let total: Int

    enum CodingKeys: String, CodingKey {
        case spaceId = "space_id"
        case todayDate = "today_date"
        case todayLunar = "today_lunar"
        case items
        case topCapsules = "top_capsules"
        case total
    }
}

// MARK: - Top Capsule Strip for Todo View

struct TodoCountdownCapsuleStripView: View {
    @EnvironmentObject var store: TodoStore
    @State private var showWallSheet: Bool = false
    @State private var showNewSheet: Bool = false

    var body: some View {
        let capsules = store.countdownList?.topCapsules ?? []
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                if capsules.isEmpty {
                    Button {
                        showNewSheet = true
                    } label: {
                        Label("添加倒数日/纪念日", systemImage: "hourglass.badge.plus")
                            .font(.caption.bold())
                            .padding(.horizontal, 10)
                            .padding(.vertical, 4)
                            .background(store.activeTheme.primaryColor.opacity(0.12))
                            .foregroundStyle(store.activeTheme.primaryColor)
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                } else {
                    ForEach(capsules) { item in
                        Button {
                            showWallSheet = true
                        } label: {
                            Text(item.capsuleText)
                                .font(.caption.bold())
                                .padding(.horizontal, 10)
                                .padding(.vertical, 4)
                                .background(store.activeTheme.primaryColor.opacity(0.13))
                                .foregroundStyle(store.activeTheme.primaryColor)
                                .clipShape(Capsule())
                        }
                        .buttonStyle(.plain)
                    }

                    Button {
                        showWallSheet = true
                    } label: {
                        Text("卡片墙 ▶")
                            .font(.caption2.bold())
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .sheet(isPresented: $showWallSheet) {
            NavigationStack {
                ScrollView {
                    CountdownWallView()
                        .padding(16)
                }
                .navigationTitle("⏳ 倒数纪念日")
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("完成") { showWallSheet = false }
                    }
                }
            }
            .environmentObject(store)
            #if os(macOS)
            .frame(minWidth: 620, minHeight: 480)
            #endif
        }
        .sheet(isPresented: $showNewSheet) {
            CountdownEditorSheet(existing: nil)
                .environmentObject(store)
        }
    }
}

// MARK: - Countdown & Anniversary Big-Number Card Wall

struct CountdownWallView: View {
    @EnvironmentObject var store: TodoStore
    @State private var editingItem: CountdownItemModel?
    @State private var isCreating: Bool = false

    private let gridColumns = [
        GridItem(.adaptive(minimum: 220, maximum: 320), spacing: 14)
    ]

    var body: some View {
        let items = store.countdownList?.items ?? []
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("⏳ 倒数纪念日卡片墙")
                        .font(.headline.bold())
                    Text(store.countdownList?.todayLunar ?? "支持公历/农历生日与节日自动换算")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button {
                    isCreating = true
                } label: {
                    Label("新建纪念日", systemImage: "plus")
                }
                .buttonStyle(.borderedProminent)
                .tint(store.activeTheme.primaryColor)
            }

            if items.isEmpty {
                ContentUnavailableView(
                    "暂无倒数纪念日",
                    systemImage: "hourglass",
                    description: Text("记录春节、公农历生日、考试倒计时或恋爱/入职正数纪念日，自动同步至 iOS 桌面与锁屏小组件。")
                )
                .padding(.vertical, 28)
            } else {
                LazyVGrid(columns: gridColumns, spacing: 14) {
                    ForEach(items) { item in
                        countdownCard(item)
                            .onTapGesture {
                                editingItem = item
                            }
                    }
                }
            }
        }
        .task {
            await store.refreshCountdowns()
        }
        .sheet(isPresented: $isCreating) {
            CountdownEditorSheet(existing: nil)
                .environmentObject(store)
        }
        .sheet(item: $editingItem) { item in
            CountdownEditorSheet(existing: item)
                .environmentObject(store)
        }
    }

    private func countdownCard(_ item: CountdownItemModel) -> some View {
        let repeatText: String = {
            switch item.repeatRule {
            case "yearly": return "每年重复"
            case "monthly": return "每月重复"
            default: return "单次"
            }
        }()

        return VStack(spacing: 0) {
            HStack {
                Text("\(item.icon) \(item.headerBanner)")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(.white)
                    .lineLimit(1)
                Spacer(minLength: 4)
                if item.pinned {
                    Image(systemName: "pin.fill")
                        .font(.caption2)
                        .foregroundStyle(.white.opacity(0.92))
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(item.bannerGradient)

            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(item.displayNumber)
                    .font(.system(size: 44, weight: .heavy, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(.primary)
                if !item.unitLabel.isEmpty {
                    Text(item.unitLabel)
                        .font(.subheadline.bold())
                        .foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 20)
            .background(.background)

            Divider()

            HStack {
                Text("\(item.displaySolarDate) (\(item.weekdayLabel))")
                    .lineLimit(1)
                Spacer(minLength: 4)
                Text("\(item.lunarLabel) · \(repeatText)")
                    .lineLimit(1)
            }
            .font(.system(size: 11))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(Color.primary.opacity(0.025))
        }
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .stroke(Color.primary.opacity(0.08), lineWidth: 1)
        )
    }
}

// MARK: - Countdown & Anniversary Editor Sheet

struct CountdownEditorSheet: View {
    @EnvironmentObject var store: TodoStore
    @Environment(\.dismiss) private var dismiss

    let existing: CountdownItemModel?

    @State private var title: String = ""
    @State private var icon: String = "🧧"
    @State private var kind: String = "countdown"
    @State private var calendarType: String = "solar"
    @State private var solarDate: Date = Date()
    @State private var lunarYear: Int = Calendar.current.component(.year, from: Date())
    @State private var lunarMonth: Int = 1
    @State private var lunarDay: Int = 1
    @State private var repeatRule: String = "yearly"
    @State private var pinned: Bool = true
    @State private var colorTheme: String = "crimson"
    @State private var isSaving: Bool = false

    private let icons = ["🎯", "🧧", "🎂", "💍", "🚀", "🎓", "✈️", "🌱"]
    private let colors: [(id: String, name: String, color: Color)] = [
        ("crimson", "中国红", .red),
        ("lavender", "薰衣草", .purple),
        ("sky", "晴空蓝", .blue),
        ("mint", "薄荷绿", .mint),
        ("peach", "蜜桃橙", .orange),
        ("pink", "樱花粉", .pink),
        ("butter", "暖阳黄", .yellow)
    ]
    private let lunarMonths = ["正月","二月","三月","四月","五月","六月","七月","八月","九月","十月","冬月","腊月"]
    private let lunarDays = ["初一","初二","初三","初四","初五","初六","初七","初八","初九","初十","十一","十二","十三","十四","十五","十六","十七","十八","十九","二十","廿一","廿二","廿三","廿四","廿五","廿六","廿七","廿八","廿九","三十"]

    var body: some View {
        NavigationStack {
            Form {
                Section("基本名称与图标") {
                    HStack {
                        Picker("图标", selection: $icon) {
                            ForEach(icons, id: \.self) { ic in
                                Text(ic).tag(ic)
                            }
                        }
                        .labelsHidden()
                        .frame(width: 64)

                        TextField("例如：春节团圆 / 妈妈生日 / 恋爱纪念日", text: $title)
                        VoiceMicButton(text: $title, compact: true)
                    }

                    Picker("计时类型", selection: $kind) {
                        Text("⏳ 倒数日（未来目标）").tag("countdown")
                        Text("💖 正数纪念日（累计已过）").tag("anniversary")
                    }
                }

                Section("公历 / 农历日期") {
                    Picker("历法", selection: $calendarType) {
                        Text("☀️ 公历").tag("solar")
                        Text("🌙 农历").tag("lunar")
                    }
                    .pickerStyle(.segmented)

                    if calendarType == "solar" {
                        DatePicker("选择公历日期", selection: $solarDate, displayedComponents: [.date])
                    } else {
                        Stepper("农历年份：\(lunarYear) 年", value: $lunarYear, in: 1900...2100)
                        Picker("农历月份", selection: $lunarMonth) {
                            ForEach(1...12, id: \.self) { m in
                                Text(lunarMonths[m - 1]).tag(m)
                            }
                        }
                        Picker("农历日期", selection: $lunarDay) {
                            ForEach(1...30, id: \.self) { d in
                                Text(lunarDays[d - 1]).tag(d)
                            }
                        }
                    }

                    Picker("重复周期", selection: $repeatRule) {
                        Text("不重复").tag("none")
                        Text("每年重复（生日/节日/周年）").tag("yearly")
                        Text("每月重复").tag("monthly")
                    }

                    Toggle("📌 置顶展示在待办顶栏胶囊", isOn: $pinned)
                }

                Section("卡片色彩") {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 10) {
                            ForEach(colors, id: \.id) { c in
                                Button {
                                    colorTheme = c.id
                                } label: {
                                    HStack(spacing: 5) {
                                        Circle().fill(c.color).frame(width: 12, height: 12)
                                        Text(c.name).font(.caption)
                                    }
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 5)
                                    .background(colorTheme == c.id ? c.color.opacity(0.18) : Color.primary.opacity(0.05))
                                    .clipShape(Capsule())
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                }

                if let ex = existing {
                    Section {
                        Button(role: .destructive) {
                            Task {
                                try? await store.client.deleteCountdown(countdownId: ex.countdownId)
                                await store.refreshCountdowns()
                                dismiss()
                            }
                        } label: {
                            Label("删除该倒数纪念日", systemImage: "trash")
                        }
                    }
                }
            }
            .navigationTitle(existing == nil ? "新建倒数纪念日" : "编辑倒数纪念日")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isSaving ? "保存中..." : "保存") {
                        save()
                    }
                    .disabled(isSaving || title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .onAppear {
                guard let ex = existing else { return }
                title = ex.title
                icon = ex.icon
                kind = ex.kind
                calendarType = ex.calendarType
                lunarYear = ex.lunarYear
                lunarMonth = max(1, min(12, ex.lunarMonth))
                lunarDay = max(1, min(30, ex.lunarDay))
                repeatRule = ex.repeatRule
                pinned = ex.pinned
                colorTheme = ex.colorTheme
                let f = DateFormatter()
                f.dateFormat = "yyyy-MM-dd"
                f.timeZone = TimeZone(identifier: "Asia/Shanghai") ?? .current
                if let d = f.date(from: ex.targetDate) {
                    solarDate = d
                }
            }
        }
        #if os(macOS)
        .frame(minWidth: 460, minHeight: 460)
        #endif
    }

    private func save() {
        let clean = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        isSaving = true
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "Asia/Shanghai") ?? .current

        var body: [String: Any] = [
            "countdown_id": existing?.countdownId ?? "",
            "title": clean,
            "icon": icon,
            "kind": kind,
            "calendar_type": calendarType,
            "repeat_rule": repeatRule,
            "pinned": pinned,
            "color_theme": colorTheme
        ]
        if calendarType == "lunar" {
            body["lunar_year"] = lunarYear
            body["lunar_month"] = lunarMonth
            body["lunar_day"] = lunarDay
        } else {
            body["target_date"] = f.string(from: solarDate)
        }

        Task {
            defer { isSaving = false }
            _ = try? await store.client.saveCountdown(body: body)
            await store.refreshCountdowns()
            dismiss()
        }
    }
}
