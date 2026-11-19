import SwiftUI
import Combine
#if os(macOS)
import AppKit
#elseif canImport(AudioToolbox)
import AudioToolbox
#endif

/// Optional iOS FamilyControls / Screen Time extension hook.
/// System-level app blocking requires the Apple FamilyControls entitlement in Xcode Signing & Capabilities.
enum ScreenTimeLockCapability {
    static var isSystemAppBlockConfigured: Bool {
        #if os(iOS) && canImport(FamilyControls)
        return false
        #else
        return false
        #endif
    }

    static var statusNote: String {
        #if os(iOS)
        return isSystemAppBlockConfigured
            ? "系统级屏幕使用时间 App 限制已就绪"
            : "当前为应用内高对比专注锁屏（如需禁用抖音/小红书，可在 Xcode 添加 FamilyControls Entitlement）"
        #else
        return "macOS 全屏沉浸式专注锁屏 · 超时自动提示"
        #endif
    }
}

struct FocusLockView: View {
    @EnvironmentObject var store: TodoStore
    let task: TaskItemModel

    @State private var totalSeconds: Int = 25 * 60
    @State private var remainingSeconds: Int = 25 * 60
    @State private var showAbortPrompt: Bool = false
    @State private var abortReason: String = ""
    @State private var isFinishing: Bool = false

    private let ticker = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    private var progress: Double {
        guard totalSeconds > 0 else { return 0 }
        return Double(totalSeconds - remainingSeconds) / Double(totalSeconds)
    }

    private var formattedTimer: String {
        let rem = max(0, remainingSeconds)
        let m = rem / 60
        let s = rem % 60
        return String(format: "%02d:%02d", m, s)
    }

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [Color(red: 0.11, green: 0.10, blue: 0.28), Color(red: 0.04, green: 0.05, blue: 0.10)],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()

            VStack(spacing: 22) {
                Text("🔒 沉浸专注锁屏模式 · DEEP WORK")
                    .font(.caption.bold())
                    .tracking(1.3)
                    .foregroundStyle(Color(red: 0.77, green: 0.71, blue: 0.99))

                Text(task.title)
                    .font(.title2.bold())
                    .foregroundStyle(.white)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)

                // Countdown Ring
                ZStack {
                    Circle()
                        .stroke(Color.white.opacity(0.14), lineWidth: 12)
                    Circle()
                        .trim(from: 0, to: progress)
                        .stroke(
                            LinearGradient(colors: [.purple, .pink], startPoint: .topLeading, endPoint: .bottomTrailing),
                            style: StrokeStyle(lineWidth: 12, lineCap: .round)
                        )
                        .rotationEffect(.degrees(-90))
                        .animation(.linear(duration: 0.4), value: progress)

                    VStack(spacing: 4) {
                        Text(formattedTimer)
                            .font(.system(size: 52, weight: .heavy, design: .rounded))
                            .monospacedDigit()
                            .foregroundStyle(.white)
                        Text("已专注 \(task.payload.focusSessionsCompleted ?? 0) 轮")
                            .font(.caption)
                            .foregroundStyle(Color.white.opacity(0.65))
                    }
                }
                .frame(width: 210, height: 210)

                // Interactive Checklist inside Focus Lock
                if !task.payload.subtaskList.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("📋 执行清单")
                            .font(.caption.bold())
                            .foregroundStyle(Color.white.opacity(0.7))

                        ScrollView {
                            VStack(alignment: .leading, spacing: 8) {
                                ForEach(task.payload.subtaskList) { sub in
                                    Button {
                                        Task { await store.toggleSubtask(task, subtask: sub) }
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
                            Button("收起键盘") { dismissAppKeyboard() }
                                .font(.caption)
                                .foregroundStyle(.white.opacity(0.75))
                            Spacer()
                            Button("确认退出") {
                                dismissAppKeyboard()
                                let clean = abortReason.trimmingCharacters(in: .whitespacesAndNewlines)
                                guard !clean.isEmpty else { return }
                                let elapsed = max(1, totalSeconds - remainingSeconds)
                                Task { await store.abortFocusSession(task, reason: clean, elapsedSeconds: elapsed) }
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
                            Task { await store.completeFocusSession(task, markTaskDone: true) }
                        } label: {
                            Text("✅ 完成任务并结束")
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
            let mins = max(1, task.payload.focusMinutes ?? 25)
            totalSeconds = mins * 60
            remainingSeconds = mins * 60
        }
        .onReceive(ticker) { _ in
            guard !isFinishing else { return }
            if remainingSeconds > 1 {
                remainingSeconds -= 1
            } else if remainingSeconds == 1 {
                remainingSeconds = 0
                isFinishing = true
                playCompletionSound()
                Task { await store.completeFocusSession(task, markTaskDone: false) }
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
