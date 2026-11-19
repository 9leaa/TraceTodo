import SwiftUI

struct DashboardOverviewView: View {
    @EnvironmentObject var store: TodoStore
    @State private var showMatrixBelow: Bool = true

    private var counts: MatrixSummaryCounts {
        store.matrix?.summaryCounts ?? MatrixSummaryCounts(
            totalTodo: 0, totalDone: 0, overdue: 0, q1: 0, q2: 0, q3: 0, q4: 0
        )
    }

    private var completionPct: Int {
        let total = counts.totalTodo + counts.totalDone
        guard total > 0 else { return 0 }
        return Int(round(Double(counts.totalDone) / Double(total) * 100.0))
    }

    private var focusSessionsTotal: Int {
        (store.matrix?.sortedTasks ?? []).reduce(0) { $0 + ($1.payload.focusSessionsCompleted ?? 0) }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if let banner = store.statusBanner {
                    HStack(spacing: 8) {
                        Image(systemName: "sparkles")
                            .foregroundStyle(.purple)
                        Text(banner)
                            .font(.footnote.weight(.medium))
                            .foregroundStyle(Color(red: 0.12, green: 0.16, blue: 0.24))
                        Spacer()
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(Color.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .shadow(color: Color.black.opacity(0.04), radius: 8, y: 3)
                }

                // SUMMARY Header
                HStack(alignment: .center) {
                    Text("SUMMARY")
                        .font(.system(size: 16, weight: .medium, design: .rounded))
                        .tracking(2.2)
                        .foregroundStyle(Color(red: 0.35, green: 0.40, blue: 0.49))

                    Spacer()

                    HStack(spacing: 14) {
                        filterLink("待办中", isSelected: !store.includeDone) {
                            store.includeDone = false
                            Task { await store.refresh() }
                        }
                        filterLink("含已完成", isSelected: store.includeDone) {
                            store.includeDone = true
                            Task { await store.refresh() }
                        }
                        filterLink(showMatrixBelow ? "收起四象限" : "展开四象限", isSelected: showMatrixBelow) {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                showMatrixBelow.toggle()
                            }
                        }
                    }
                }

                // 2x2 Vibrant Gradient Hero KPI Cards
                LazyVGrid(
                    columns: [GridItem(.flexible(), spacing: 16), GridItem(.flexible(), spacing: 16)],
                    spacing: 16
                ) {
                    GradientHeroKpiCard(
                        title: "Q1 重要且紧急",
                        mainValue: "\(counts.q1) 项",
                        subLeft: counts.overdue > 0 ? "⚠️ \(counts.overdue) 项已逾期 ↑" : "✓ 无逾期急事",
                        subRight: "马上做",
                        iconName: "flame.fill",
                        colors: [Color(red: 1.00, green: 0.49, blue: 0.39), Color(red: 1.00, green: 0.29, blue: 0.56)],
                        shadowColor: Color(red: 1.00, green: 0.34, blue: 0.50)
                    )
                    GradientHeroKpiCard(
                        title: "Q2 重要不紧急",
                        mainValue: "\(counts.q2) 项",
                        subLeft: "长期规划与成长 ↑",
                        subRight: "建议预留专注时段",
                        iconName: "calendar.badge.clock",
                        colors: [Color(red: 0.38, green: 0.31, blue: 0.96), Color(red: 0.14, green: 0.57, blue: 0.91)],
                        shadowColor: Color(red: 0.25, green: 0.43, blue: 0.94)
                    )
                    GradientHeroKpiCard(
                        title: "待办总览 (TODO)",
                        mainValue: "\(counts.totalTodo)",
                        subLeft: "Q3 速办 \(counts.q3) ↑",
                        subRight: "Q4 精简 \(counts.q4)",
                        iconName: "checklist",
                        colors: [Color(red: 0.04, green: 0.81, blue: 0.65), Color(red: 0.09, green: 0.65, blue: 0.87)],
                        shadowColor: Color(red: 0.06, green: 0.72, blue: 0.76)
                    )
                    GradientHeroKpiCard(
                        title: "完成率 & 专注",
                        mainValue: "\(completionPct)% / \(counts.totalDone)",
                        subLeft: "专注 \(focusSessionsTotal) 轮 ↑",
                        subRight: "累计已完结 ↑",
                        iconName: "star.circle.fill",
                        colors: [Color(red: 0.93, green: 0.27, blue: 0.77), Color(red: 0.48, green: 0.35, blue: 0.97)],
                        shadowColor: Color(red: 0.68, green: 0.31, blue: 0.88)
                    )
                }

                // Middle Row: TOP FOCUS + TASK INSIGHTS Donut Chart
                #if os(iOS)
                VStack(spacing: 16) {
                    TopFocusCardView(tasks: Array((store.matrix?.sortedTasks ?? []).filter { !$0.payload.isDone }.prefix(4)))
                    TaskInsightsDonutView(counts: counts)
                }
                #else
                HStack(alignment: .top, spacing: 16) {
                    TopFocusCardView(tasks: Array((store.matrix?.sortedTasks ?? []).filter { !$0.payload.isDone }.prefix(4)))
                        .frame(maxWidth: .infinity)
                    TaskInsightsDonutView(counts: counts)
                        .frame(maxWidth: .infinity)
                }
                #endif

                // Eisenhower 2x2 Interactive Quadrant Matrix
                if showMatrixBelow {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text("EISENHOWER MATRIX · 四象限工作台")
                                .font(.system(size: 13, weight: .semibold, design: .rounded))
                                .tracking(1.5)
                                .foregroundStyle(Color(red: 0.35, green: 0.40, blue: 0.49))
                            Spacer()
                            Button {
                                store.isPresentingNewTaskSheet = true
                            } label: {
                                Label("新建彩色卡片", systemImage: "plus")
                                    .font(.caption.bold())
                            }
                            .buttonStyle(.bordered)
                        }

                        let cols = matrixColumns
                        LazyVGrid(columns: cols, spacing: 14) {
                            ForEach(QuadrantKind.allCases) { q in
                                DashboardQuadrantBox(quadrant: q, tasks: store.tasks(for: q))
                            }
                        }
                    }
                    .padding(.top, 4)
                }
            }
            .padding(22)
        }
        .background(Color(red: 0.96, green: 0.97, blue: 0.99))
        .iosScrollDismissesKeyboard()
        .refreshable {
            await store.refresh()
        }
    }

    private var matrixColumns: [GridItem] {
        #if os(iOS)
        return [GridItem(.adaptive(minimum: 310), spacing: 14)]
        #else
        return [GridItem(.flexible(), spacing: 14), GridItem(.flexible(), spacing: 14)]
        #endif
    }

    private func filterLink(_ label: String, isSelected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(.system(size: 12.5, weight: isSelected ? .bold : .medium))
                .foregroundStyle(isSelected ? Color(red: 0.14, green: 0.39, blue: 0.92) : Color(red: 0.55, green: 0.60, blue: 0.68))
                .padding(.vertical, 4)
                .padding(.horizontal, 4)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

struct GradientHeroKpiCard: View {
    let title: String
    let mainValue: String
    let subLeft: String
    let subRight: String
    let iconName: String
    let colors: [Color]
    let shadowColor: Color

    var body: some View {
        ZStack(alignment: .trailing) {
            // Watermark icon on the right
            Image(systemName: iconName)
                .font(.system(size: 74, weight: .bold))
                .foregroundStyle(Color.white.opacity(0.16))
                .offset(x: -10, y: 12)

            VStack(alignment: .leading, spacing: 8) {
                Text(title)
                    .font(.system(size: 11.5, weight: .semibold, design: .rounded))
                    .tracking(1.0)
                    .foregroundStyle(Color.white.opacity(0.90))

                Text(mainValue)
                    .font(.system(size: 28, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)
                    .minimumScaleFactor(0.7)
                    .lineLimit(1)

                HStack(spacing: 12) {
                    Text(subLeft)
                    Text(subRight)
                }
                .font(.system(size: 11.5, weight: .medium))
                .foregroundStyle(Color.white.opacity(0.88))
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 18)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(minHeight: 112)
        .background(
            LinearGradient(colors: colors, startPoint: .topLeading, endPoint: .bottomTrailing)
        )
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .shadow(color: shadowColor.opacity(0.28), radius: 14, x: 0, y: 8)
    }
}

struct TopFocusCardView: View {
    @EnvironmentObject var store: TodoStore
    let tasks: [TaskItemModel]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("TOP FOCUS · 优先聚焦")
                    .font(.system(size: 12, weight: .semibold, design: .rounded))
                    .tracking(1.4)
                    .foregroundStyle(Color(red: 0.42, green: 0.47, blue: 0.56))
                Spacer()
                Text("Top \(tasks.count)")
                    .font(.caption2.bold())
                    .foregroundStyle(Color(red: 0.55, green: 0.60, blue: 0.68))
            }

            if tasks.isEmpty {
                Text("当前无高优待办，点击右上角「+ 新建任务」开启今日计划。")
                    .font(.caption)
                    .foregroundStyle(Color(red: 0.55, green: 0.60, blue: 0.68))
                    .padding(.vertical, 28)
                    .frame(maxWidth: .infinity, alignment: .center)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(tasks.enumerated()), id: \.element.id) { idx, task in
                        let subs = task.payload.subtaskList
                        let progress: Double = {
                            if !subs.isEmpty {
                                return Double(subs.filter(\.done).count) / Double(subs.count)
                            }
                            return min(1.0, max(0.25, task.payload.priorityScore / 120.0))
                        }()

                        HStack(spacing: 12) {
                            Text("\(idx + 1). \(task.title)")
                                .font(.system(size: 13.5, weight: .medium))
                                .foregroundStyle(Color(red: 0.22, green: 0.27, blue: 0.36))
                                .lineLimit(1)

                            Spacer()

                            if !subs.isEmpty {
                                Text("\(subs.filter(\.done).count)/\(subs.count)")
                                    .font(.caption2.bold())
                                    .foregroundStyle(Color(red: 0.52, green: 0.56, blue: 0.65))
                            }

                            Button {
                                store.activeFocusTask = task
                            } label: {
                                SegmentedRadialRing(progress: progress, tint: task.payload.quadrantKind.tintColor)
                            }
                            .buttonStyle(.plain)
                            .help("点击进入专注模式")
                        }
                        .padding(.vertical, 11)
                        .contentShape(Rectangle())
                        .onTapGesture {
                            store.editingTaskTarget = task
                        }

                        if idx < tasks.count - 1 {
                            Divider().opacity(0.5)
                        }
                    }
                }
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, minHeight: 228, alignment: .topLeading)
        .background(Color.white)
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .shadow(color: Color.black.opacity(0.05), radius: 14, x: 0, y: 6)
    }
}

private struct SegmentedRadialRing: View {
    let progress: Double
    let tint: Color

    var body: some View {
        let filled = max(2, Int(round(progress * 10)))
        ZStack {
            ForEach(0..<10, id: \.self) { i in
                RoundedRectangle(cornerRadius: 1)
                    .fill(i < filled ? tint : Color(red: 0.88, green: 0.90, blue: 0.94))
                    .frame(width: 2.2, height: 5.5)
                    .offset(y: -8.5)
                    .rotationEffect(.degrees(Double(i) * 36.0))
            }
        }
        .frame(width: 24, height: 24)
    }
}

struct TaskInsightsDonutView: View {
    let counts: MatrixSummaryCounts

    private var totalAll: Int {
        counts.totalTodo + counts.totalDone
    }

    private var segments: [(label: String, value: Int, color: Color)] {
        [
            ("已完成", counts.totalDone, Color(red: 0.51, green: 0.38, blue: 0.96)),
            ("Q1 急事", counts.q1, Color(red: 0.98, green: 0.36, blue: 0.48)),
            ("Q2 规划", counts.q2, Color(red: 0.16, green: 0.58, blue: 0.94)),
            ("Q3/Q4", counts.q3 + counts.q4, Color(red: 0.08, green: 0.78, blue: 0.68)),
        ]
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("TASK INSIGHTS · 分布洞察")
                .font(.system(size: 12, weight: .semibold, design: .rounded))
                .tracking(1.4)
                .foregroundStyle(Color(red: 0.42, green: 0.47, blue: 0.56))

            HStack(spacing: 22) {
                ZStack {
                    Circle()
                        .stroke(Color(red: 0.92, green: 0.94, blue: 0.97), lineWidth: 13)

                    ForEach(Array(arcSlices().enumerated()), id: \.offset) { _, slice in
                        Circle()
                            .trim(from: slice.start, to: slice.end)
                            .stroke(slice.color, style: StrokeStyle(lineWidth: 13, lineCap: .round))
                            .rotationEffect(.degrees(-90))
                    }

                    VStack(spacing: 2) {
                        Text("\(totalAll)")
                            .font(.system(size: 26, weight: .bold, design: .rounded))
                            .foregroundStyle(Color(red: 0.14, green: 0.18, blue: 0.26))
                        Text("总记录事项")
                            .font(.system(size: 10.5))
                            .foregroundStyle(Color(red: 0.55, green: 0.60, blue: 0.68))
                    }
                }
                .frame(width: 132, height: 132)

                VStack(alignment: .leading, spacing: 12) {
                    ForEach(Array(segments.enumerated()), id: \.offset) { _, seg in
                        HStack(spacing: 8) {
                            Circle()
                                .fill(seg.color)
                                .frame(width: 8, height: 8)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(seg.label)
                                    .font(.system(size: 12))
                                    .foregroundStyle(Color(red: 0.45, green: 0.50, blue: 0.58))
                                Text("\(seg.value)")
                                    .font(.system(size: 16, weight: .bold, design: .rounded))
                                    .foregroundStyle(Color(red: 0.14, green: 0.18, blue: 0.26))
                            }
                        }
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(.top, 4)
        }
        .padding(18)
        .frame(maxWidth: .infinity, minHeight: 228, alignment: .topLeading)
        .background(Color.white)
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .shadow(color: Color.black.opacity(0.05), radius: 14, x: 0, y: 6)
    }

    private func arcSlices() -> [(start: CGFloat, end: CGFloat, color: Color)] {
        let sum = max(1, segments.reduce(0) { $0 + $1.value })
        var cursor: CGFloat = 0
        var out: [(start: CGFloat, end: CGFloat, color: Color)] = []
        for seg in segments where seg.value > 0 {
            let frac = CGFloat(seg.value) / CGFloat(sum)
            let start = cursor
            let end = min(1.0, cursor + frac)
            out.append((start: start, end: max(start + 0.01, end - 0.015), color: seg.color))
            cursor = end
        }
        return out
    }
}

private struct DashboardQuadrantBox: View {
    let quadrant: QuadrantKind
    let tasks: [TaskItemModel]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Image(systemName: quadrant.iconName)
                    .foregroundStyle(quadrant.tintColor)
                VStack(alignment: .leading, spacing: 1) {
                    Text(quadrant.title)
                        .font(.system(size: 14, weight: .bold))
                        .foregroundStyle(Color(red: 0.12, green: 0.16, blue: 0.24))
                    Text(quadrant.subtitle)
                        .font(.system(size: 11))
                        .foregroundStyle(Color(red: 0.45, green: 0.50, blue: 0.58))
                }
                Spacer()
                Text("\(tasks.filter { !$0.payload.isDone }.count)")
                    .font(.system(size: 11.5, weight: .bold))
                    .padding(.horizontal, 9)
                    .padding(.vertical, 3)
                    .background(quadrant.tintColor)
                    .foregroundStyle(.white)
                    .clipShape(Capsule())
            }

            if tasks.isEmpty {
                Text("当前象限暂无待办")
                    .font(.caption)
                    .foregroundStyle(Color(red: 0.55, green: 0.60, blue: 0.68))
                    .frame(maxWidth: .infinity, minHeight: 68, alignment: .center)
            } else {
                VStack(spacing: 8) {
                    ForEach(tasks) { item in
                        TaskCardView(task: item)
                    }
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, minHeight: 160, alignment: .topLeading)
        .background(Color.white)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(quadrant.tintColor.opacity(0.24), lineWidth: 1)
        )
        .shadow(color: Color.black.opacity(0.03), radius: 8, y: 3)
    }
}

struct RightTimelineRailView: View {
    @EnvironmentObject var store: TodoStore
    @State private var feedTab: Int = 0 // 0: 待办, 1: 监督, 2: 已完成

    private var todoTasks: [TaskItemModel] {
        (store.matrix?.sortedTasks ?? []).filter { !$0.payload.isDone }
    }

    private var supervisedTasks: [TaskItemModel] {
        (store.matrix?.sortedTasks ?? []).filter {
            !$0.payload.isDone && (($0.payload.supervisionEnabled ?? false) || $0.payload.taskMode == "multiplayer")
        }
    }

    private var doneTasks: [TaskItemModel] {
        (store.matrix?.sortedTasks ?? []).filter { $0.payload.isDone }
    }

    private var displayedTasks: [TaskItemModel] {
        switch feedTab {
        case 1: return supervisedTasks
        case 2: return doneTasks
        default: return todoTasks
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            // Royal Blue Header matching ORDERS panel in reference image
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text("TASKS · 待办流")
                        .font(.system(size: 16, weight: .medium, design: .rounded))
                        .tracking(2.0)
                        .foregroundStyle(.white)
                    Spacer()
                    Button {
                        store.isPresentingNewTaskSheet = true
                    } label: {
                        Image(systemName: "plus.circle.fill")
                            .font(.system(size: 18))
                            .foregroundStyle(.white.opacity(0.9))
                    }
                    .buttonStyle(.plain)
                }

                HStack(spacing: 16) {
                    railTab("待办 (\(store.totalTodoCount))", index: 0)
                    railTab("监督 (\(supervisedTasks.count))", index: 1)
                    railTab("已完 (\(store.matrix?.summaryCounts.totalDone ?? 0))", index: 2)
                }
            }
            .padding(.horizontal, 18)
            .padding(.top, 18)
            .padding(.bottom, 12)
            .background(
                LinearGradient(
                    colors: [Color(red: 0.29, green: 0.56, blue: 0.96), Color(red: 0.16, green: 0.45, blue: 0.90)],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            )

            // Timeline Feed
            ScrollView {
                if displayedTasks.isEmpty {
                    VStack(spacing: 8) {
                        Image(systemName: "checkmark.seal")
                            .font(.title2)
                            .foregroundStyle(Color(red: 0.55, green: 0.60, blue: 0.68))
                        Text(feedTab == 2 ? "在看板开启「含已完成」后展示已完结记录" : "暂无该分组任务")
                            .font(.caption)
                            .foregroundStyle(Color(red: 0.55, green: 0.60, blue: 0.68))
                            .multilineTextAlignment(.center)
                    }
                    .padding(32)
                    .frame(maxWidth: .infinity)
                } else {
                    VStack(alignment: .leading, spacing: 16) {
                        ForEach(displayedTasks) { task in
                            timelineItemRow(task)
                        }
                    }
                    .padding(16)
                }
            }
            .background(Color(red: 0.96, green: 0.97, blue: 0.99))
        }
        .frame(width: 295)
        .overlay(
            Rectangle()
                .fill(Color.black.opacity(0.06))
                .frame(width: 1),
            alignment: .leading
        )
    }

    private func railTab(_ title: String, index: Int) -> some View {
        Button {
            feedTab = index
            if index == 2 && !store.includeDone {
                store.includeDone = true
                Task { await store.refresh() }
            }
        } label: {
            VStack(spacing: 6) {
                Text(title)
                    .font(.system(size: 12, weight: feedTab == index ? .bold : .medium))
                    .foregroundStyle(feedTab == index ? .white : Color.white.opacity(0.75))
                Rectangle()
                    .fill(feedTab == index ? Color.white : Color.clear)
                    .frame(height: 2.5)
                    .clipShape(Capsule())
            }
            .padding(.top, 4)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func timelineItemRow(_ task: TaskItemModel) -> some View {
        let p = task.payload
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Circle()
                    .stroke(p.quadrantKind.tintColor, lineWidth: 2)
                    .background(Circle().fill(Color.white))
                    .frame(width: 12, height: 12)

                Text(p.quadrantKind.shortCode)
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(p.quadrantKind.tintColor)

                Spacer()

                Text(timelineTimeLabel(task))
                    .font(.system(size: 11))
                    .foregroundStyle(p.isOverdue == true ? .red : Color(red: 0.55, green: 0.60, blue: 0.68))
            }

            VStack(alignment: .leading, spacing: 8) {
                Text(task.title)
                    .font(.system(size: 13.5, weight: .semibold))
                    .foregroundStyle(Color(red: 0.14, green: 0.18, blue: 0.26))
                    .strikethrough(p.isDone)
                    .onTapGesture { store.editingTaskTarget = task }

                if !p.subtaskList.isEmpty {
                    VStack(alignment: .leading, spacing: 3) {
                        ForEach(p.subtaskList.prefix(3)) { s in
                            Text("\(s.done ? "✓" : "•") \(s.title)")
                                .font(.system(size: 11.5))
                                .foregroundStyle(Color(red: 0.42, green: 0.47, blue: 0.56))
                                .lineLimit(1)
                        }
                    }
                } else if let reason = p.priorityReason, !reason.isEmpty {
                    Text(reason)
                        .font(.system(size: 11.5))
                        .foregroundStyle(Color(red: 0.45, green: 0.50, blue: 0.58))
                        .lineLimit(2)
                }

                Divider().opacity(0.45)

                HStack {
                    Text("重要度 \(p.importance)★")
                        .font(.system(size: 11))
                        .foregroundStyle(Color(red: 0.50, green: 0.55, blue: 0.64))
                    Spacer()
                    Text("\(Int(p.priorityScore)) 分")
                        .font(.system(size: 12, weight: .bold, design: .rounded))
                        .foregroundStyle(Color(red: 0.14, green: 0.18, blue: 0.26))
                }

                // Bottom 3-button action strip matching ORDERS card actions in reference image
                HStack(spacing: 8) {
                    if !p.isDone {
                        Button {
                            Task { await store.transitionTask(task, action: "complete_task") }
                        } label: {
                            Image(systemName: "checkmark")
                                .font(.system(size: 12, weight: .bold))
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 6)
                                .background(Color(red: 0.20, green: 0.54, blue: 0.96))
                                .foregroundStyle(.white)
                                .clipShape(RoundedRectangle(cornerRadius: 6))
                        }
                        .buttonStyle(.plain)

                        Button {
                            Task { await store.transitionTask(task, action: "cancel_task") }
                        } label: {
                            Image(systemName: "xmark")
                                .font(.system(size: 11, weight: .semibold))
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 6)
                                .background(Color.white)
                                .foregroundStyle(Color(red: 0.42, green: 0.47, blue: 0.56))
                                .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.black.opacity(0.12), lineWidth: 1))
                        }
                        .buttonStyle(.plain)

                        Button {
                            store.activeFocusTask = task
                        } label: {
                            Image(systemName: "timer")
                                .font(.system(size: 12, weight: .semibold))
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 6)
                                .background(Color.white)
                                .foregroundStyle(Color.purple)
                                .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.black.opacity(0.12), lineWidth: 1))
                        }
                        .buttonStyle(.plain)
                    } else {
                        Button {
                            Task { await store.transitionTask(task, action: "reopen_task") }
                        } label: {
                            Text("↩️ 重新开启")
                                .font(.system(size: 11.5, weight: .semibold))
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 6)
                                .background(Color.primary.opacity(0.05))
                                .clipShape(RoundedRectangle(cornerRadius: 6))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .padding(13)
            .background(Color.white)
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            .shadow(color: Color.black.opacity(0.05), radius: 8, y: 3)
            .padding(.leading, 14)
        }
    }

    private func timelineTimeLabel(_ task: TaskItemModel) -> String {
        if let rc = task.payload.rolloverCount, rc > 0 {
            return "顺延\(rc)天"
        }
        if let due = task.payload.dueAt, !due.isEmpty {
            return String(due.prefix(10))
        }
        return String(task.updatedAt.prefix(10))
    }
}
