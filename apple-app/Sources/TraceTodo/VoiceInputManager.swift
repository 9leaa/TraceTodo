import Foundation
import SwiftUI
import AVFoundation
import Speech

@MainActor
final class SpeechDictationController: ObservableObject {
    @Published private(set) var isRecording: Bool = false
    @Published var errorMessage: String?

    private var audioEngine: AVAudioEngine?
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private let speechRecognizer = SFSpeechRecognizer(locale: Locale(identifier: "zh-CN"))

    func toggleDictation(
        currentText: String,
        onTranscriptUpdate: @escaping (String) -> Void,
        onStopped: (() -> Void)? = nil
    ) {
        if isRecording {
            stopDictation()
            onStopped?()
        } else {
            Task {
                await startDictation(
                    baseText: currentText,
                    onTranscriptUpdate: onTranscriptUpdate,
                    onStopped: onStopped
                )
            }
        }
    }

    func startDictation(
        baseText: String,
        onTranscriptUpdate: @escaping (String) -> Void,
        onStopped: (() -> Void)? = nil
    ) async {
        if isRecording { return }
        errorMessage = nil

        let speechAllowed = await Self.requestSpeechPermission()
        guard speechAllowed else {
            errorMessage = "请在系统设置中允许「语音识别」权限"
            return
        }

        let micAllowed = await Self.requestMicrophonePermission()
        guard micAllowed else {
            errorMessage = "请在系统设置中允许「麦克风」权限"
            return
        }

        guard let recognizer = speechRecognizer, recognizer.isAvailable else {
            errorMessage = "当前设备原生语音识别暂不可用"
            return
        }

        stopDictation()

        #if os(iOS)
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.record, mode: .measurement, options: .duckOthers)
            try session.setActive(true, options: .notifyOthersOnDeactivation)
        } catch {
            errorMessage = "音频会话初始化失败：\(error.localizedDescription)"
            return
        }
        #endif

        let engine = AVAudioEngine()
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true

        let prefix = baseText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ? ""
            : baseText.trimmingCharacters(in: .whitespacesAndNewlines) + " "

        let inputNode = engine.inputNode
        let recordingFormat = inputNode.outputFormat(forBus: 0)
        guard recordingFormat.channelCount > 0 else {
            errorMessage = "未检测到可用麦克风输入通道"
            return
        }

        inputNode.installTap(onBus: 0, bufferSize: 1024, format: recordingFormat) { buffer, _ in
            req.append(buffer)
        }

        do {
            engine.prepare()
            try engine.start()
        } catch {
            inputNode.removeTap(onBus: 0)
            errorMessage = "启动麦克风失败：\(error.localizedDescription)"
            return
        }

        self.audioEngine = engine
        self.recognitionRequest = req
        self.isRecording = true

        self.recognitionTask = recognizer.recognitionTask(with: req) { [weak self] result, error in
            Task { @MainActor in
                guard let self else { return }
                if let result {
                    let spoken = result.bestTranscription.formattedString
                    onTranscriptUpdate(prefix + spoken)
                }
                if error != nil || (result?.isFinal == true) {
                    let wasRec = self.isRecording
                    self.stopDictation()
                    if wasRec {
                        onStopped?()
                    }
                }
            }
        }
    }

    func stopDictation() {
        if let engine = audioEngine {
            if engine.isRunning {
                engine.stop()
            }
            engine.inputNode.removeTap(onBus: 0)
        }
        recognitionRequest?.endAudio()
        recognitionTask?.cancel()
        audioEngine = nil
        recognitionRequest = nil
        recognitionTask = nil
        isRecording = false
        #if os(iOS)
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        #endif
    }

    private static func requestSpeechPermission() async -> Bool {
        await withCheckedContinuation { cont in
            SFSpeechRecognizer.requestAuthorization { status in
                cont.resume(returning: status == .authorized)
            }
        }
    }

    private static func requestMicrophonePermission() async -> Bool {
        #if os(iOS)
        if #available(iOS 17.0, *) {
            return await AVAudioApplication.requestRecordPermission()
        } else {
            return await withCheckedContinuation { cont in
                AVAudioSession.sharedInstance().requestRecordPermission { ok in
                    cont.resume(returning: ok)
                }
            }
        }
        #else
        return await withCheckedContinuation { cont in
            AVCaptureDevice.requestAccess(for: .audio) { ok in
                cont.resume(returning: ok)
            }
        }
        #endif
    }
}

/// Minimalist native streaming voice dictation button (tap to dictate, zero navigation bloat).
struct VoiceMicButton: View {
    @EnvironmentObject var store: TodoStore
    @Binding var text: String
    var compact: Bool = true
    var onFinishedAutoSubmit: (() -> Void)? = nil

    @StateObject private var speech = SpeechDictationController()

    var body: some View {
        Button {
            speech.toggleDictation(
                currentText: text,
                onTranscriptUpdate: { updated in
                    text = updated
                },
                onStopped: {
                    if let autoSubmit = onFinishedAutoSubmit, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        autoSubmit()
                    }
                }
            )
        } label: {
            HStack(spacing: 4) {
                Image(systemName: speech.isRecording ? "waveform.circle.fill" : "mic.fill")
                    .font(.system(size: 13.5, weight: .semibold))
                    .foregroundStyle(speech.isRecording ? .red : .secondary)
                if !compact || speech.isRecording {
                    Text(speech.isRecording ? "听写中" : "语音")
                        .font(.caption.bold())
                        .foregroundStyle(speech.isRecording ? .red : .secondary)
                }
            }
            .padding(.horizontal, compact && !speech.isRecording ? 7 : 10)
            .padding(.vertical, 5)
            .background(speech.isRecording ? Color.red.opacity(0.14) : Color.primary.opacity(0.05))
            .clipShape(Capsule())
        }
        .buttonStyle(.plain)
        .help("Apple 原生 SFSpeechRecognizer 实时听写（0 延迟 0 Token）")
        .onChange(of: speech.errorMessage) { _, err in
            if let err, !err.isEmpty {
                store.showBanner("🎤 \(err)")
            }
        }
    }
}
