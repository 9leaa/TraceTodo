import SwiftUI

struct QuickCaptureBar: View {
    @EnvironmentObject var store: TodoStore
    @State private var inputText: String = ""
    @State private var pendingMedia: [MediaAttachmentModel] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                HStack(spacing: 8) {
                    Image(systemName: "sparkles")
                        .foregroundStyle(store.activeTheme.primaryColor)
                    TextField("一句话极速录入（支持拍照/附图与语音）...", text: $inputText)
                        .textFieldStyle(.plain)
                        .onSubmit { submit() }

                    ImageAttachPickerButton(pendingMedia: $pendingMedia, compact: true)
                    VoiceMicButton(text: $inputText, compact: true)

                    if store.isBusy {
                        ProgressView()
                            .controlSize(.small)
                    } else {
                        Button("AI 录入") { submit() }
                            .buttonStyle(.borderedProminent)
                            .tint(store.activeTheme.primaryColor)
                            .disabled(
                                inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                && pendingMedia.isEmpty
                            )
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(.background)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .stroke(store.activeTheme.primaryColor.opacity(0.32), lineWidth: 1.2)
                )

                Button {
                    store.isPresentingNewTaskSheet = true
                } label: {
                    Label("新建", systemImage: "plus")
                        .font(.subheadline.bold())
                        .padding(.horizontal, 13)
                        .padding(.vertical, 10)
                        .background(
                            LinearGradient(
                                colors: [store.activeTheme.primaryFillColor, store.activeTheme.accentColor],
                                startPoint: .leading,
                                endPoint: .trailing
                            )
                        )
                        .foregroundStyle(.white)
                        .clipShape(Capsule())
                        .shadow(color: store.activeTheme.primaryColor.opacity(0.22), radius: 5, y: 2)
                }
                .buttonStyle(.plain)
            }

            if !pendingMedia.isEmpty {
                MediaThumbnailGridView(media: pendingMedia) { item in
                    pendingMedia.removeAll { $0.mediaId == item.mediaId }
                    Task { try? await store.client.deleteMedia(mediaId: item.mediaId) }
                }
            }
        }
    }

    private func submit() {
        let text = inputText
        let mediaIds = pendingMedia.map(\.mediaId)
        inputText = ""
        pendingMedia = []
        dismissAppKeyboard()
        Task { await store.submitQuickNote(text, mediaIds: mediaIds) }
    }
}

struct QuadrantMatrixView: View {
    @EnvironmentObject var store: TodoStore
    @State private var showDiagnostics: Bool = true

    private var gridColumns: [GridItem] {
        #if os(iOS)
        return [GridItem(.adaptive(minimum: 320), spacing: 14)]
        #else
        return [GridItem(.flexible(), spacing: 14), GridItem(.flexible(), spacing: 14)]
        #endif
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                QuickCaptureBar()

                if let banner = store.statusBanner {
                    HStack(spacing: 10) {
                        Image(systemName: "info.circle.fill")
                            .foregroundStyle(store.activeTheme.primaryColor)
                        Text(banner).font(.footnote.weight(.medium))
                        Spacer()
                        if let undoLbl = store.statusBannerUndoLabel {
                            Button(undoLbl) {
                                store.triggerBannerUndo()
                            }
                            .font(.caption.bold())
                            .padding(.horizontal, 10)
                            .padding(.vertical, 4)
                            .background(store.activeTheme.primaryFillColor)
                            .foregroundStyle(.white)
                            .clipShape(Capsule())
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(10)
                    .background(store.cardSurfaceBackground)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                    .overlay(
                        RoundedRectangle(cornerRadius: 10)
                            .stroke(store.activeTheme.primaryColor.opacity(0.25), lineWidth: 1)
                    )
                }

                // KPI Summary Strip
                if let counts = store.matrix?.summaryCounts {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 10) {
                            KpiPill(title: "待办总计", value: counts.totalTodo, color: .primary)
                            KpiPill(title: "Q1 马上做", value: counts.q1, color: QuadrantKind.q1.tintColor)
                            KpiPill(title: "Q2 计划做", value: counts.q2, color: QuadrantKind.q2.tintColor)
                            KpiPill(title: "Q3 速办", value: counts.q3, color: QuadrantKind.q3.tintColor)
                            Button {
                                store.setQuickScope(.done)
                            } label: {
                                KpiPill(title: "已完成（点此查看）", value: counts.totalDone, color: .green)
                            }
                            .buttonStyle(.plain)
                            Toggle("含已完成", isOn: $store.includeDone)
                                .toggleStyle(.switch)
                                .controlSize(.small)
                                .font(.caption)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 6)
                                .background(.background)
                                .clipShape(RoundedRectangle(cornerRadius: 10))
                                .onChange(of: store.includeDone) { _, _ in
                                    Task { await store.refreshTasksFast() }
                                }
                            if !store.pendingNotes.isEmpty {
                                KpiPill(title: "离线待同步", value: store.pendingNotes.count, color: .orange)
                            }
                        }
                    }
                }

                // AI Memory-Driven Diagnostics
                if let diags = store.matrix?.diagnostics, !diags.isEmpty {
                    DisclosureGroup(isExpanded: $showDiagnostics) {
                        VStack(alignment: .leading, spacing: 6) {
                            ForEach(diags, id: \.self) { line in
                                Text(line)
                                    .font(.system(size: 12.5))
                                    .padding(.top, 2)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.top, 6)
                    } label: {
                        Label("艾森豪威尔四象限 · 记忆驱动负载诊断", systemImage: "brain.head.profile")
                            .font(.system(size: 13.5, weight: .semibold))
                    }
                    .padding(12)
                    .background(Color.purple.opacity(0.06))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                }

                // 2x2 Eisenhower Matrix Grid
                LazyVGrid(columns: gridColumns, spacing: 14) {
                    ForEach(QuadrantKind.allCases) { q in
                        QuadrantColumnCard(quadrant: q, tasks: store.tasks(for: q))
                    }
                }
            }
            .padding(16)
        }
        .iosScrollDismissesKeyboard()
        .refreshable {
            await store.refresh()
        }
    }
}

private struct KpiPill: View {
    let title: String
    let value: Int
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.caption2)
                .foregroundStyle(.secondary)
            Text("\(value)")
                .font(.system(size: 18, weight: .bold, design: .rounded))
                .foregroundStyle(color)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(Color.primary.opacity(0.08), lineWidth: 1)
        )
    }
}

private struct QuadrantColumnCard: View {
    let quadrant: QuadrantKind
    let tasks: [TaskItemModel]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Image(systemName: quadrant.iconName)
                    .foregroundStyle(quadrant.tintColor)
                VStack(alignment: .leading, spacing: 1) {
                    Text(quadrant.title)
                        .font(.system(size: 14.5, weight: .bold))
                    Text(quadrant.subtitle)
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text("\(tasks.filter { !$0.payload.isDone }.count)")
                    .font(.system(size: 12, weight: .bold))
                    .padding(.horizontal, 9)
                    .padding(.vertical, 3)
                    .background(quadrant.tintColor)
                    .foregroundStyle(.white)
                    .clipShape(Capsule())
            }

            if tasks.isEmpty {
                Text("当前象限暂无待办")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 80, alignment: .center)
            } else {
                VStack(spacing: 8) {
                    ForEach(tasks) { item in
                        TaskCardView(task: item)
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(14)
        .frame(maxWidth: .infinity, minHeight: 210, alignment: .topLeading)
        .background(quadrant.softBackground)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(quadrant.tintColor.opacity(0.28), lineWidth: 1)
        )
    }
}
