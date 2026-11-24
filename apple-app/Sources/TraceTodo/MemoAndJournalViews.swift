import SwiftUI
#if os(iOS)
import UIKit
#elseif os(macOS)
import AppKit
#endif

// MARK: - Live Markdown Editor (Real-Time Styled Typography + IME-Safe + Quick Format Toolbar)

private enum MarkdownLiveStyler {
    private static let headingRegex = try? NSRegularExpression(pattern: #"^(#{1,3})\s+(.*)$"#, options: [.anchorsMatchLines])
    private static let quoteRegex = try? NSRegularExpression(pattern: #"^(>\s+)(.*)$"#, options: [.anchorsMatchLines])
    private static let bulletRegex = try? NSRegularExpression(pattern: #"^([-*•]\s+|\d+[\.\、]\s+)(.*)$"#, options: [.anchorsMatchLines])
    private static let taskUncheckedRegex = try? NSRegularExpression(pattern: #"^([-*•]\s+\[\s\]\s*)(.*)$"#, options: [.anchorsMatchLines])
    private static let taskCheckedRegex = try? NSRegularExpression(pattern: #"^([-*•]\s+\[[xX]\]\s*)(.*)$"#, options: [.anchorsMatchLines])
    private static let boldRegex = try? NSRegularExpression(pattern: #"(\*\*)([^*\n]+)(\*\*)"#, options: [])
    private static let italicRegex = try? NSRegularExpression(pattern: #"(?<!\*)(\*)([^*\n]+)(\*)(?!\*)"#, options: [])
    private static let codeRegex = try? NSRegularExpression(pattern: #"(`)([^`\n]+)(`)"#, options: [])
    private static let tagRegex = try? NSRegularExpression(pattern: #"(?:^|\s)(#[\p{L}\p{N}_-]{1,24})"#, options: [.anchorsMatchLines])

    #if os(iOS)
    static func defaultTypingAttributes() -> [NSAttributedString.Key: Any] {
        let para = NSMutableParagraphStyle()
        para.lineSpacing = 4.5
        return [
            .font: UIFont.systemFont(ofSize: 15, weight: .regular),
            .foregroundColor: UIColor.label,
            .paragraphStyle: para
        ]
    }

    static func apply(to storage: NSTextStorage, accent: UIColor) {
        let fullText = storage.string as NSString
        let fullRange = NSRange(location: 0, length: fullText.length)
        guard fullRange.length > 0 else { return }

        let baseFont = UIFont.systemFont(ofSize: 15, weight: .regular)
        let boldFont = UIFont.systemFont(ofSize: 15, weight: .bold)
        let italicFont = UIFont.italicSystemFont(ofSize: 15)
        let codeFont = UIFont.monospacedSystemFont(ofSize: 13.5, weight: .medium)
        let markerFont = UIFont.monospacedSystemFont(ofSize: 11.5, weight: .regular)
        let markerColor = UIColor.tertiaryLabel.withAlphaComponent(0.55)

        storage.beginEditing()
        storage.setAttributes(defaultTypingAttributes(), range: fullRange)

        // Blockquotes (> ...)
        quoteRegex?.enumerateMatches(in: storage.string, options: [], range: fullRange) { m, _, _ in
            guard let m else { return }
            storage.addAttributes([.foregroundColor: accent, .font: UIFont.systemFont(ofSize: 15, weight: .bold)], range: m.range(at: 1))
            storage.addAttributes([.foregroundColor: UIColor.secondaryLabel, .font: italicFont], range: m.range(at: 2))
        }

        // Bullet / numbered lists
        bulletRegex?.enumerateMatches(in: storage.string, options: [], range: fullRange) { m, _, _ in
            guard let m else { return }
            storage.addAttributes([.foregroundColor: accent, .font: boldFont], range: m.range(at: 1))
        }

        // Unchecked / Checked tasks (- [ ] / - [x])
        taskUncheckedRegex?.enumerateMatches(in: storage.string, options: [], range: fullRange) { m, _, _ in
            guard let m else { return }
            storage.addAttributes([.foregroundColor: accent, .font: codeFont], range: m.range(at: 1))
        }
        taskCheckedRegex?.enumerateMatches(in: storage.string, options: [], range: fullRange) { m, _, _ in
            guard let m else { return }
            storage.addAttributes([.foregroundColor: accent, .font: codeFont], range: m.range(at: 1))
            storage.addAttributes([
                .foregroundColor: UIColor.secondaryLabel,
                .strikethroughStyle: NSUnderlineStyle.single.rawValue,
                .strikethroughColor: UIColor.secondaryLabel
            ], range: m.range(at: 2))
        }

        // Headings (# / ## / ###)
        headingRegex?.enumerateMatches(in: storage.string, options: [], range: fullRange) { m, _, _ in
            guard let m else { return }
            let hashes = fullText.substring(with: m.range(at: 1))
            let sz: CGFloat = hashes.count == 1 ? 19.0 : (hashes.count == 2 ? 17.0 : 15.6)
            storage.addAttributes([.font: markerFont, .foregroundColor: accent.withAlphaComponent(0.45)], range: m.range(at: 1))
            storage.addAttributes([.font: UIFont.systemFont(ofSize: sz, weight: .bold), .foregroundColor: UIColor.label], range: m.range(at: 2))
        }

        // Inline **bold**
        boldRegex?.enumerateMatches(in: storage.string, options: [], range: fullRange) { m, _, _ in
            guard let m else { return }
            storage.addAttributes([.font: markerFont, .foregroundColor: markerColor], range: m.range(at: 1))
            storage.addAttributes([.font: boldFont], range: m.range(at: 2))
            storage.addAttributes([.font: markerFont, .foregroundColor: markerColor], range: m.range(at: 3))
        }

        // Inline *italic*
        italicRegex?.enumerateMatches(in: storage.string, options: [], range: fullRange) { m, _, _ in
            guard let m else { return }
            storage.addAttributes([.font: markerFont, .foregroundColor: markerColor], range: m.range(at: 1))
            storage.addAttributes([.font: italicFont], range: m.range(at: 2))
            storage.addAttributes([.font: markerFont, .foregroundColor: markerColor], range: m.range(at: 3))
        }

        // Inline `code`
        codeRegex?.enumerateMatches(in: storage.string, options: [], range: fullRange) { m, _, _ in
            guard let m else { return }
            storage.addAttributes([.font: markerFont, .foregroundColor: markerColor], range: m.range(at: 1))
            storage.addAttributes([
                .font: codeFont,
                .foregroundColor: accent,
                .backgroundColor: accent.withAlphaComponent(0.10)
            ], range: m.range(at: 2))
            storage.addAttributes([.font: markerFont, .foregroundColor: markerColor], range: m.range(at: 3))
        }

        // #Hashtags
        tagRegex?.enumerateMatches(in: storage.string, options: [], range: fullRange) { m, _, _ in
            guard let m else { return }
            storage.addAttributes([
                .foregroundColor: accent,
                .font: UIFont.systemFont(ofSize: 13.5, weight: .semibold),
                .backgroundColor: accent.withAlphaComponent(0.09)
            ], range: m.range(at: 1))
        }

        _ = baseFont
        storage.endEditing()
    }
    #elseif os(macOS)
    static func defaultTypingAttributes() -> [NSAttributedString.Key: Any] {
        let para = NSMutableParagraphStyle()
        para.lineSpacing = 4.0
        return [
            .font: NSFont.systemFont(ofSize: 14.5, weight: .regular),
            .foregroundColor: NSColor.labelColor,
            .paragraphStyle: para
        ]
    }

    static func apply(to storage: NSTextStorage, accent: NSColor) {
        let fullText = storage.string as NSString
        let fullRange = NSRange(location: 0, length: fullText.length)
        guard fullRange.length > 0 else { return }

        let baseFont = NSFont.systemFont(ofSize: 14.5, weight: .regular)
        let boldFont = NSFont.systemFont(ofSize: 14.5, weight: .bold)
        let italicFont = NSFontManager.shared.convert(baseFont, toHaveTrait: .italicFontMask)
        let codeFont = NSFont.monospacedSystemFont(ofSize: 13.0, weight: .medium)
        let markerFont = NSFont.monospacedSystemFont(ofSize: 11.0, weight: .regular)
        let markerColor = NSColor.tertiaryLabelColor.withAlphaComponent(0.6)

        storage.beginEditing()
        storage.setAttributes(defaultTypingAttributes(), range: fullRange)

        quoteRegex?.enumerateMatches(in: storage.string, options: [], range: fullRange) { m, _, _ in
            guard let m else { return }
            storage.addAttributes([.foregroundColor: accent, .font: boldFont], range: m.range(at: 1))
            storage.addAttributes([.foregroundColor: NSColor.secondaryLabelColor, .font: italicFont], range: m.range(at: 2))
        }

        bulletRegex?.enumerateMatches(in: storage.string, options: [], range: fullRange) { m, _, _ in
            guard let m else { return }
            storage.addAttributes([.foregroundColor: accent, .font: boldFont], range: m.range(at: 1))
        }

        taskUncheckedRegex?.enumerateMatches(in: storage.string, options: [], range: fullRange) { m, _, _ in
            guard let m else { return }
            storage.addAttributes([.foregroundColor: accent, .font: codeFont], range: m.range(at: 1))
        }
        taskCheckedRegex?.enumerateMatches(in: storage.string, options: [], range: fullRange) { m, _, _ in
            guard let m else { return }
            storage.addAttributes([.foregroundColor: accent, .font: codeFont], range: m.range(at: 1))
            storage.addAttributes([
                .foregroundColor: NSColor.secondaryLabelColor,
                .strikethroughStyle: NSUnderlineStyle.single.rawValue,
                .strikethroughColor: NSColor.secondaryLabelColor
            ], range: m.range(at: 2))
        }

        headingRegex?.enumerateMatches(in: storage.string, options: [], range: fullRange) { m, _, _ in
            guard let m else { return }
            let hashes = fullText.substring(with: m.range(at: 1))
            let sz: CGFloat = hashes.count == 1 ? 18.5 : (hashes.count == 2 ? 16.5 : 15.2)
            storage.addAttributes([.font: markerFont, .foregroundColor: accent.withAlphaComponent(0.45)], range: m.range(at: 1))
            storage.addAttributes([.font: NSFont.systemFont(ofSize: sz, weight: .bold), .foregroundColor: NSColor.labelColor], range: m.range(at: 2))
        }

        boldRegex?.enumerateMatches(in: storage.string, options: [], range: fullRange) { m, _, _ in
            guard let m else { return }
            storage.addAttributes([.font: markerFont, .foregroundColor: markerColor], range: m.range(at: 1))
            storage.addAttributes([.font: boldFont], range: m.range(at: 2))
            storage.addAttributes([.font: markerFont, .foregroundColor: markerColor], range: m.range(at: 3))
        }

        italicRegex?.enumerateMatches(in: storage.string, options: [], range: fullRange) { m, _, _ in
            guard let m else { return }
            storage.addAttributes([.font: markerFont, .foregroundColor: markerColor], range: m.range(at: 1))
            storage.addAttributes([.font: italicFont], range: m.range(at: 2))
            storage.addAttributes([.font: markerFont, .foregroundColor: markerColor], range: m.range(at: 3))
        }

        codeRegex?.enumerateMatches(in: storage.string, options: [], range: fullRange) { m, _, _ in
            guard let m else { return }
            storage.addAttributes([.font: markerFont, .foregroundColor: markerColor], range: m.range(at: 1))
            storage.addAttributes([
                .font: codeFont,
                .foregroundColor: accent,
                .backgroundColor: accent.withAlphaComponent(0.11)
            ], range: m.range(at: 2))
            storage.addAttributes([.font: markerFont, .foregroundColor: markerColor], range: m.range(at: 3))
        }

        tagRegex?.enumerateMatches(in: storage.string, options: [], range: fullRange) { m, _, _ in
            guard let m else { return }
            storage.addAttributes([
                .foregroundColor: accent,
                .font: NSFont.systemFont(ofSize: 13.0, weight: .semibold),
                .backgroundColor: accent.withAlphaComponent(0.10)
            ], range: m.range(at: 1))
        }

        storage.endEditing()
    }
    #endif
}

#if os(iOS)
private struct PlatformLiveMarkdownTextView: UIViewRepresentable {
    @Binding var text: String
    @Binding var selectedRange: NSRange
    let accentColor: Color

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> UITextView {
        let tv = UITextView()
        tv.delegate = context.coordinator
        tv.backgroundColor = .clear
        tv.isScrollEnabled = true
        tv.alwaysBounceVertical = false
        tv.textContainerInset = UIEdgeInsets(top: 10, left: 8, bottom: 10, right: 8)
        tv.typingAttributes = MarkdownLiveStyler.defaultTypingAttributes()
        tv.text = text
        MarkdownLiveStyler.apply(to: tv.textStorage, accent: UIColor(accentColor))
        return tv
    }

    func updateUIView(_ uiView: UITextView, context: Context) {
        context.coordinator.parent = self
        // Never overwrite text or attributes while Chinese Pinyin IME is composing
        if uiView.markedTextRange != nil { return }
        if uiView.text != text {
            context.coordinator.isProgrammaticUpdate = true
            uiView.text = text
            MarkdownLiveStyler.apply(to: uiView.textStorage, accent: UIColor(accentColor))
            let safeLoc = min(selectedRange.location, (text as NSString).length)
            let safeLen = min(selectedRange.length, max(0, (text as NSString).length - safeLoc))
            uiView.selectedRange = NSRange(location: safeLoc, length: safeLen)
            uiView.typingAttributes = MarkdownLiveStyler.defaultTypingAttributes()
            context.coordinator.isProgrammaticUpdate = false
        }
    }

    final class Coordinator: NSObject, UITextViewDelegate {
        var parent: PlatformLiveMarkdownTextView
        var isProgrammaticUpdate = false

        init(_ parent: PlatformLiveMarkdownTextView) { self.parent = parent }

        func textViewDidChange(_ textView: UITextView) {
            guard !isProgrammaticUpdate else { return }
            let newText = textView.text ?? ""
            if parent.text != newText {
                parent.text = newText
            }
            // Wait until IME composition commits before styling
            guard textView.markedTextRange == nil else { return }
            isProgrammaticUpdate = true
            let savedSel = textView.selectedRange
            MarkdownLiveStyler.apply(to: textView.textStorage, accent: UIColor(parent.accentColor))
            if savedSel.location + savedSel.length <= (textView.text as NSString).length {
                textView.selectedRange = savedSel
            }
            textView.typingAttributes = MarkdownLiveStyler.defaultTypingAttributes()
            parent.selectedRange = textView.selectedRange
            isProgrammaticUpdate = false
        }

        func textViewDidChangeSelection(_ textView: UITextView) {
            guard !isProgrammaticUpdate, textView.markedTextRange == nil else { return }
            if parent.selectedRange != textView.selectedRange {
                DispatchQueue.main.async {
                    self.parent.selectedRange = textView.selectedRange
                }
            }
        }
    }
}
#elseif os(macOS)
private struct PlatformLiveMarkdownTextView: NSViewRepresentable {
    @Binding var text: String
    @Binding var selectedRange: NSRange
    let accentColor: Color

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSScrollView {
        let scrollView = NSTextView.scrollableTextView()
        scrollView.drawsBackground = false
        scrollView.hasVerticalScroller = true
        scrollView.autohidesScrollers = true
        scrollView.borderType = .noBorder

        if let tv = scrollView.documentView as? NSTextView {
            tv.delegate = context.coordinator
            tv.drawsBackground = false
            tv.isRichText = true
            tv.importsGraphics = false
            tv.allowsUndo = true
            tv.isAutomaticQuoteSubstitutionEnabled = false
            tv.isAutomaticDashSubstitutionEnabled = false
            tv.textContainerInset = NSSize(width: 6, height: 8)
            tv.typingAttributes = MarkdownLiveStyler.defaultTypingAttributes()
            tv.string = text
            if let storage = tv.textStorage {
                MarkdownLiveStyler.apply(to: storage, accent: NSColor(accentColor))
            }
        }
        return scrollView
    }

    func updateNSView(_ nsView: NSScrollView, context: Context) {
        context.coordinator.parent = self
        guard let tv = nsView.documentView as? NSTextView else { return }
        if tv.hasMarkedText() { return }
        if tv.string != text {
            context.coordinator.isProgrammaticUpdate = true
            tv.string = text
            if let storage = tv.textStorage {
                MarkdownLiveStyler.apply(to: storage, accent: NSColor(accentColor))
            }
            let maxLen = (text as NSString).length
            let safeLoc = min(selectedRange.location, maxLen)
            let safeLen = min(selectedRange.length, max(0, maxLen - safeLoc))
            tv.setSelectedRange(NSRange(location: safeLoc, length: safeLen))
            tv.typingAttributes = MarkdownLiveStyler.defaultTypingAttributes()
            context.coordinator.isProgrammaticUpdate = false
        }
    }

    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: PlatformLiveMarkdownTextView
        var isProgrammaticUpdate = false

        init(_ parent: PlatformLiveMarkdownTextView) { self.parent = parent }

        func textDidChange(_ notification: Notification) {
            guard !isProgrammaticUpdate, let tv = notification.object as? NSTextView else { return }
            let newText = tv.string
            if parent.text != newText {
                parent.text = newText
            }
            guard !tv.hasMarkedText(), let storage = tv.textStorage else { return }
            isProgrammaticUpdate = true
            let savedSel = tv.selectedRange()
            MarkdownLiveStyler.apply(to: storage, accent: NSColor(parent.accentColor))
            let maxLen = (tv.string as NSString).length
            if savedSel.location + savedSel.length <= maxLen {
                tv.setSelectedRange(savedSel)
            }
            tv.typingAttributes = MarkdownLiveStyler.defaultTypingAttributes()
            parent.selectedRange = tv.selectedRange()
            isProgrammaticUpdate = false
        }

        func textViewDidChangeSelection(_ notification: Notification) {
            guard !isProgrammaticUpdate, let tv = notification.object as? NSTextView, !tv.hasMarkedText() else { return }
            let sel = tv.selectedRange()
            if parent.selectedRange != sel {
                DispatchQueue.main.async {
                    self.parent.selectedRange = sel
                }
            }
        }
    }
}
#endif

struct LiveMarkdownEditor: View {
    @EnvironmentObject var store: TodoStore
    @Binding var text: String
    let placeholder: String
    var minHeight: CGFloat = 108
    var maxHeight: CGFloat = 240

    @State private var selectedRange: NSRange = NSRange(location: 0, length: 0)
    @State private var showPurePreview: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Formatting & Live Render Mode Bar
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 5) {
                    formatBtn("H1") { applyLinePrefix("# ") }
                    formatBtn("H2") { applyLinePrefix("## ") }
                    formatBtn("B", bold: true) { wrapSelection(prefix: "**", suffix: "**", fallback: "加粗") }
                    formatBtn("I", italic: true) { wrapSelection(prefix: "*", suffix: "*", fallback: "斜体") }
                    formatBtn("• 列表") { applyLinePrefix("- ") }
                    formatBtn("☑ 清单") { toggleOrInsertChecklist() }
                    formatBtn("❝ 引用") { applyLinePrefix("> ") }
                    formatBtn("</> 代码") { wrapSelection(prefix: "`", suffix: "`", fallback: "code") }

                    Divider().frame(height: 14).padding(.horizontal, 2)

                    Button {
                        withAnimation(.easeInOut(duration: 0.15)) {
                            showPurePreview.toggle()
                        }
                    } label: {
                        HStack(spacing: 3) {
                            Image(systemName: showPurePreview ? "pencil.and.outline" : "eye")
                                .font(.system(size: 10, weight: .bold))
                            Text(showPurePreview ? "返回编辑" : "纯净预览")
                                .font(.caption2.bold())
                        }
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3.5)
                        .background(showPurePreview ? store.activeTheme.primaryColor : store.activeTheme.primaryColor.opacity(0.12))
                        .foregroundStyle(showPurePreview ? Color.white : store.activeTheme.primaryColor)
                        .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 5)
            }
            .background(Color.primary.opacity(0.035))

            Divider().opacity(0.5)

            if showPurePreview {
                ScrollView {
                    if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        Text("（暂无内容，点击右上角「返回编辑」开始书写）")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(12)
                    } else {
                        MarkdownAnswerView(text: text)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(12)
                    }
                }
                .frame(minHeight: minHeight, maxHeight: maxHeight)
                .contentShape(Rectangle())
                .onTapGesture(count: 2) {
                    showPurePreview = false
                }
            } else {
                ZStack(alignment: .topLeading) {
                    if text.isEmpty {
                        Text(placeholder)
                            .font(.system(size: 14))
                            .foregroundStyle(.tertiary)
                            .padding(.horizontal, 13)
                            .padding(.vertical, 11)
                            .allowsHitTesting(false)
                    }
                    PlatformLiveMarkdownTextView(
                        text: $text,
                        selectedRange: $selectedRange,
                        accentColor: store.activeTheme.primaryColor
                    )
                    .frame(minHeight: minHeight, maxHeight: maxHeight)
                }
            }
        }
        .background(Color.primary.opacity(0.015))
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(Color.primary.opacity(0.12), lineWidth: 1)
        )
    }

    private func formatBtn(_ title: String, bold: Bool = false, italic: Bool = false, action: @escaping () -> Void) -> some View {
        Button(action: {
            if showPurePreview { showPurePreview = false }
            action()
        }) {
            Text(title)
                .font(.system(size: 11.5, weight: bold ? .heavy : .semibold))
                .italic(italic)
                .padding(.horizontal, 7)
                .padding(.vertical, 3.5)
                .background(Color.primary.opacity(0.05))
                .foregroundStyle(.primary)
                .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
        }
        .buttonStyle(.plain)
    }

    private func wrapSelection(prefix: String, suffix: String, fallback: String) {
        let ns = text as NSString
        let loc = min(max(0, selectedRange.location), ns.length)
        let len = min(max(0, selectedRange.length), ns.length - loc)
        let selected = len > 0 ? ns.substring(with: NSRange(location: loc, length: len)) : fallback
        let inserted = "\(prefix)\(selected)\(suffix)"
        text = ns.replacingCharacters(in: NSRange(location: loc, length: len), with: inserted)
        let pLen = (prefix as NSString).length
        let sLen = (selected as NSString).length
        selectedRange = len > 0
            ? NSRange(location: loc + (inserted as NSString).length, length: 0)
            : NSRange(location: loc + pLen, length: sLen)
    }

    private func applyLinePrefix(_ prefix: String) {
        let ns = text as NSString
        let loc = min(max(0, selectedRange.location), ns.length)
        let lineRange = ns.lineRange(for: NSRange(location: loc, length: 0))
        let lineStr = ns.substring(with: lineRange)
        if lineStr.hasPrefix(prefix) {
            let stripped = String(lineStr.dropFirst(prefix.count))
            text = ns.replacingCharacters(in: lineRange, with: stripped)
            selectedRange = NSRange(location: max(lineRange.location, loc - (prefix as NSString).length), length: 0)
        } else {
            let replaced = prefix + lineStr
            text = ns.replacingCharacters(in: lineRange, with: replaced)
            selectedRange = NSRange(location: loc + (prefix as NSString).length, length: 0)
        }
    }

    private func toggleOrInsertChecklist() {
        let ns = text as NSString
        let loc = min(max(0, selectedRange.location), ns.length)
        let lineRange = ns.lineRange(for: NSRange(location: loc, length: 0))
        let lineStr = ns.substring(with: lineRange)
        if lineStr.hasPrefix("- [ ] ") {
            let next = "- [x] " + String(lineStr.dropFirst(6))
            text = ns.replacingCharacters(in: lineRange, with: next)
        } else if lineStr.hasPrefix("- [x] ") || lineStr.hasPrefix("- [X] ") {
            let next = "- [ ] " + String(lineStr.dropFirst(6))
            text = ns.replacingCharacters(in: lineRange, with: next)
        } else {
            applyLinePrefix("- [ ] ")
        }
    }
}

// MARK: - Memo & Journal Models

struct MoodCatalogItem: Codable, Identifiable, Hashable {
    let id: String
    let icon: String
    let label: String
    let color: String
    let group: String?
}

struct MemoTagCountItem: Codable, Identifiable, Hashable {
    let tag: String
    let count: Int
    var id: String { tag }
}

struct RichNoteItemModel: Codable, Identifiable, Hashable {
    let noteId: String
    let spaceId: String
    let content: String
    let sourceType: String
    let revision: Int
    let eventTime: String
    let createdAt: String
    let updatedAt: String
    let noteKind: String?
    let title: String?
    let pinned: Bool?
    let tags: [String]?
    let mood: String?
    let moodMeta: MoodCatalogItem?
    let journalDate: String?
    let lunarLabel: String?
    let weekdayLabel: String?
    let extractMemory: Bool?
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
        case updatedAt = "updated_at"
        case noteKind = "note_kind"
        case title, pinned, tags, mood, media
        case moodMeta = "mood_meta"
        case journalDate = "journal_date"
        case lunarLabel = "lunar_label"
        case weekdayLabel = "weekday_label"
        case extractMemory = "extract_memory"
    }
}

struct MemoListResponse: Codable {
    let spaceId: String
    let memos: [RichNoteItemModel]
    let total: Int
    let pinnedCount: Int
    let tagsCatalog: [MemoTagCountItem]

    enum CodingKeys: String, CodingKey {
        case spaceId = "space_id"
        case memos, total
        case pinnedCount = "pinned_count"
        case tagsCatalog = "tags_catalog"
    }
}

struct MemoSaveResponse: Codable {
    let memo: RichNoteItemModel
    let jobId: String?

    enum CodingKeys: String, CodingKey {
        case memo
        case jobId = "job_id"
    }
}

struct JournalFootprintModel: Codable, Hashable {
    let date: String
    let completedCount: Int
    let openDueCount: Int
    let focusSessionsCount: Int
    let focusPomodoros: Int
    let focusMinutes: Int
    let memoCount: Int

    enum CodingKeys: String, CodingKey {
        case date
        case completedCount = "completed_count"
        case openDueCount = "open_due_count"
        case focusSessionsCount = "focus_sessions_count"
        case focusPomodoros = "focus_pomodoros"
        case focusMinutes = "focus_minutes"
        case memoCount = "memo_count"
    }
}

struct JournalOverviewResponse: Codable {
    let spaceId: String
    let date: String
    let weekdayLabel: String
    let lunarLabel: String
    let currentEntry: RichNoteItemModel?
    let dayFootprint: JournalFootprintModel
    let timeline: [RichNoteItemModel]
    let moodCatalog: [MoodCatalogItem]

    enum CodingKeys: String, CodingKey {
        case spaceId = "space_id"
        case date
        case weekdayLabel = "weekday_label"
        case lunarLabel = "lunar_label"
        case currentEntry = "current_entry"
        case dayFootprint = "day_footprint"
        case timeline
        case moodCatalog = "mood_catalog"
    }
}

struct JournalSaveResponse: Codable {
    let journal: RichNoteItemModel
    let jobId: String?

    enum CodingKeys: String, CodingKey {
        case journal
        case jobId = "job_id"
    }
}

struct JournalAISummaryResponse: Codable {
    let date: String
    let summaryMarkdown: String
    let provider: String

    enum CodingKeys: String, CodingKey {
        case date
        case summaryMarkdown = "summary_markdown"
        case provider
    }
}

let defaultMoodCatalogList: [MoodCatalogItem] = [
    MoodCatalogItem(id: "joy", icon: "😊", label: "愉悦", color: "#f59e0b", group: "positive"),
    MoodCatalogItem(id: "focused", icon: "🔥", label: "充实", color: "#ef4444", group: "positive"),
    MoodCatalogItem(id: "celebrate", icon: "🎉", label: "惊喜", color: "#ec4899", group: "positive"),
    MoodCatalogItem(id: "grateful", icon: "💖", label: "感恩", color: "#f43f5e", group: "positive"),
    MoodCatalogItem(id: "calm", icon: "🌿", label: "平静", color: "#10b981", group: "neutral"),
    MoodCatalogItem(id: "thinking", icon: "🤔", label: "思考", color: "#6366f1", group: "neutral"),
    MoodCatalogItem(id: "meh", icon: "😶", label: "麻木", color: "#94a3b8", group: "neutral"),
    MoodCatalogItem(id: "tired", icon: "😴", label: "疲惫", color: "#64748b", group: "neutral"),
    MoodCatalogItem(id: "sad", icon: "😔", label: "难过", color: "#3b82f6", group: "negative"),
    MoodCatalogItem(id: "anxious", icon: "😰", label: "焦虑", color: "#8b5cf6", group: "negative"),
    MoodCatalogItem(id: "angry", icon: "😠", label: "烦躁", color: "#dc2626", group: "negative"),
    MoodCatalogItem(id: "low", icon: "🌧️", label: "低落", color: "#475569", group: "negative")
]

// MARK: - Unified Notes Hub (Memos + Daily Journal)

enum NotesHubSubmode: String, Identifiable {
    case memo = "memo"
    case journal = "journal"
    var id: String { rawValue }
}

struct MemoAndJournalStudioView: View {
    @EnvironmentObject var store: TodoStore
    @State private var submode: NotesHubSubmode = .memo

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("📝 备忘录与每日日记")
                            .font(.headline.bold())
                        Text("轻点卡片查看完整详情，点击「原地修改」在当前位置直接编辑")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    if store.isModuleEnabled(.journal) {
                        Picker("随记模式", selection: $submode) {
                            Text("📌 备忘录").tag(NotesHubSubmode.memo)
                            Text("📖 每日日记").tag(NotesHubSubmode.journal)
                        }
                        .pickerStyle(.segmented)
                        .frame(maxWidth: 210)
                    }
                }

                if submode == .journal && store.isModuleEnabled(.journal) {
                    JournalDailyStudioView()
                } else {
                    MemosGalleryView()
                }
            }
            .padding(14)
        }
        .iosScrollDismissesKeyboard()
    }
}

// MARK: - Shared Detail & Edit Modal for Memos & Journals

struct NoteDetailPresentationTarget: Identifiable {
    let kind: NotesHubSubmode
    var item: RichNoteItemModel
    var id: String { "\(kind.rawValue)-\(item.noteId)" }
}

struct NoteAndJournalDetailSheet: View {
    @EnvironmentObject var store: TodoStore
    @Environment(\.dismiss) private var dismiss

    let kind: NotesHubSubmode
    @State var item: RichNoteItemModel
    let moodsCatalog: [MoodCatalogItem]
    let onSaved: (RichNoteItemModel?) -> Void

    @State private var isEditing: Bool = false
    @State private var draftTitle: String = ""
    @State private var draftTags: String = ""
    @State private var draftContent: String = ""
    @State private var draftPinned: Bool = false
    @State private var draftExtract: Bool = false
    @State private var draftMood: String = "calm"
    @State private var existingMedia: [MediaAttachmentModel] = []
    @State private var newMedia: [MediaAttachmentModel] = []
    @State private var isSaving: Bool = false
    @State private var confirmForget: Bool = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    metaHeader

                    Divider()

                    if isEditing {
                        editSection
                    } else {
                        readOnlySection
                    }
                }
                .padding(18)
            }
            .iosScrollDismissesKeyboard()
            .iosKeyboardDoneToolbar()
            .navigationTitle(sheetTitle)
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
                ToolbarItem(placement: .primaryAction) {
                    if isEditing {
                        Button(isSaving ? "保存中..." : "💾 保存") {
                            saveChanges()
                        }
                        .fontWeight(.bold)
                        .disabled(isSaving)
                    } else {
                        Button("✏️ 编辑") {
                            enterEditMode()
                        }
                        .fontWeight(.semibold)
                    }
                }
            }
            .confirmationDialog("确认彻底遗忘此条记录？", isPresented: $confirmForget, titleVisibility: .visible) {
                Button("确认彻底遗忘", role: .destructive) {
                    Task {
                        try? await store.client.forgetNote(noteId: item.noteId)
                        onSaved(nil)
                        await store.refresh()
                        dismiss()
                    }
                }
                Button("取消", role: .cancel) {}
            }
        }
        #if os(macOS)
        .frame(minWidth: 620, minHeight: 500)
        #endif
    }

    private var sheetTitle: String {
        if kind == .journal {
            let ymd = item.journalDate ?? String(item.eventTime.prefix(10))
            return "📖 \(ymd) 日记详情"
        }
        let t = (item.title ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? "📌 备忘录详情" : "📌 \(t)"
    }

    private var metaHeader: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                if isEditing {
                    badgePill("✏️ 编辑中", filled: true)
                }
                if kind == .journal {
                    let mm = item.moodMeta ?? moodsCatalog.first(where: { $0.id == item.mood }) ?? MoodCatalogItem(id: "calm", icon: "📖", label: "日记", color: "#6366f1", group: nil)
                    badgePill("\(mm.icon) \(mm.label)", filled: false)
                    if let wd = item.weekdayLabel, !wd.isEmpty {
                        badgePill(wd, filled: false)
                    }
                    if let lunar = item.lunarLabel, !lunar.isEmpty {
                        badgePill("🗓️ \(lunar)", filled: false)
                    }
                } else {
                    if item.pinned == true {
                        badgePill("📌 置顶", filled: true)
                    }
                    ForEach(item.tags ?? [], id: \.self) { t in
                        badgePill("#\(t)", filled: false)
                    }
                }
                badgePill((item.extractMemory ?? false) ? "🧠 记忆联动" : "📌 纯文本归档", filled: false)
                badgePill("v\(item.revision)", filled: false)
                Text("更新于 \(String(item.updatedAt.prefix(16)).replacingOccurrences(of: "T", with: " "))")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func badgePill(_ text: String, filled: Bool) -> some View {
        Text(text)
            .font(.caption2.bold())
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(filled ? store.activeTheme.primaryColor : store.activeTheme.primaryColor.opacity(0.12))
            .foregroundStyle(filled ? Color.white : store.activeTheme.primaryColor)
            .clipShape(Capsule())
    }

    private var readOnlySection: some View {
        VStack(alignment: .leading, spacing: 14) {
            if kind == .memo, let t = item.title, !t.isEmpty {
                Text(t)
                    .font(.title3.bold())
            }

            if item.content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Text("（无正文内容）")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } else {
                MarkdownAnswerView(text: item.content)
                    .padding(14)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.primary.opacity(0.035))
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            }

            if let mList = item.media, !mList.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("🖼️ 附件照片（轻点查看原图与 OCR）")
                        .font(.caption.bold())
                        .foregroundStyle(.secondary)
                    MediaThumbnailGridView(media: mList)
                }
            }

            HStack(spacing: 10) {
                if kind == .memo {
                    Button((item.pinned ?? false) ? "取消置顶" : "📌 置顶") {
                        Task {
                            let nextPin = !(item.pinned ?? false)
                            try? await store.client.toggleMemoPin(noteId: item.noteId, pinned: nextPin)
                            if let refreshed = try? await store.client.fetchMemos(limit: 120),
                               let updated = refreshed.memos.first(where: { $0.noteId == item.noteId }) {
                                item = updated
                                onSaved(updated)
                            } else {
                                onSaved(nil)
                            }
                        }
                    }
                    .buttonStyle(.bordered)
                }

                Button("✏️ 直接编辑") {
                    enterEditMode()
                }
                .buttonStyle(.borderedProminent)
                .tint(store.activeTheme.primaryColor)

                Spacer()

                Button(role: .destructive) {
                    confirmForget = true
                } label: {
                    Label("遗忘", systemImage: "trash")
                }
                .buttonStyle(.bordered)
            }
            .padding(.top, 6)
        }
    }

    private var editSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            if kind == .journal {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(moodsCatalog) { m in
                            Button {
                                draftMood = m.id
                            } label: {
                                Text("\(m.icon) \(m.label)")
                                    .font(.caption.bold())
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 6)
                                    .background(draftMood == m.id ? store.activeTheme.primaryColor.opacity(0.18) : Color.primary.opacity(0.05))
                                    .foregroundStyle(draftMood == m.id ? store.activeTheme.primaryColor : .primary)
                                    .clipShape(Capsule())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            } else {
                HStack(spacing: 8) {
                    TextField("备忘标题（可选）...", text: $draftTitle)
                        .textFieldStyle(.roundedBorder)
                        .font(.subheadline.bold())
                    TextField("#标签（空格分隔）", text: $draftTags)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 180)
                }
            }

            LiveMarkdownEditor(
                text: $draftContent,
                placeholder: "输入 Markdown 正文（输入 # 标题、**加粗**、- 列表、- [ ] 清单实时渲染排版）...",
                minHeight: 150,
                maxHeight: 320
            )

            let combinedMedia = existingMedia + newMedia
            if !combinedMedia.isEmpty {
                MediaThumbnailGridView(media: combinedMedia) { removed in
                    if existingMedia.contains(where: { $0.mediaId == removed.mediaId }) {
                        existingMedia.removeAll { $0.mediaId == removed.mediaId }
                    } else {
                        newMedia.removeAll { $0.mediaId == removed.mediaId }
                    }
                    Task { try? await store.client.deleteMedia(mediaId: removed.mediaId) }
                }
            }

            HStack(spacing: 12) {
                if kind == .memo {
                    Toggle("📌 置顶", isOn: $draftPinned)
                        .toggleStyle(.switch)
                        .controlSize(.small)
                }
                Toggle("🧠 提炼长期记忆", isOn: $draftExtract)
                    .toggleStyle(.switch)
                    .controlSize(.small)
                Spacer()
            }
            .font(.caption)

            HStack(spacing: 8) {
                ImageAttachPickerButton(pendingMedia: $newMedia)
                VoiceMicButton(text: $draftContent, compact: false)
                Spacer()
                Button("取消") {
                    for nm in newMedia {
                        Task { try? await store.client.deleteMedia(mediaId: nm.mediaId) }
                    }
                    newMedia = []
                    isEditing = false
                }
                .buttonStyle(.bordered)

                Button(isSaving ? "保存中..." : "💾 保存修改") {
                    saveChanges()
                }
                .buttonStyle(.borderedProminent)
                .tint(store.activeTheme.primaryColor)
                .disabled(isSaving)
            }
        }
    }

    private func enterEditMode() {
        draftTitle = item.title ?? ""
        draftTags = (item.tags ?? []).joined(separator: " ")
        draftContent = item.content
        draftPinned = item.pinned ?? false
        draftExtract = item.extractMemory ?? false
        draftMood = item.mood ?? "calm"
        existingMedia = item.media ?? []
        newMedia = []
        isEditing = true
    }

    private func saveChanges() {
        let cleanContent = draftContent.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanTitle = draftTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        if cleanContent.isEmpty && cleanTitle.isEmpty && existingMedia.isEmpty && newMedia.isEmpty {
            store.showBanner("请输入内容或附加图片后再保存")
            return
        }
        isSaving = true
        dismissAppKeyboard()
        Task {
            defer { isSaving = false }
            if kind == .memo {
                let body: [String: Any] = [
                    "note_id": item.noteId,
                    "title": cleanTitle,
                    "content": cleanContent,
                    "tags": draftTags,
                    "pinned": draftPinned,
                    "extract_memory": draftExtract,
                    "media_ids": newMedia.map(\.mediaId)
                ]
                if let res = try? await store.client.saveMemo(body: body) {
                    item = res.memo
                    newMedia = []
                    isEditing = false
                    onSaved(res.memo)
                    store.showBanner("✅ 备忘录修改已保存")
                }
            } else {
                let ymd = item.journalDate ?? String(item.eventTime.prefix(10))
                let body: [String: Any] = [
                    "note_id": item.noteId,
                    "journal_date": ymd,
                    "mood": draftMood,
                    "content": cleanContent,
                    "extract_memory": draftExtract,
                    "media_ids": newMedia.map(\.mediaId)
                ]
                if let res = try? await store.client.saveJournal(body: body) {
                    item = res.journal
                    newMedia = []
                    isEditing = false
                    onSaved(res.journal)
                    store.showBanner("📖 \(ymd) 日记修改已保存")
                }
            }
        }
    }
}

// MARK: - Subview 1: Pinned Memos (Grid / List + Click Detail + In-Place Card Edit)

struct MemosGalleryView: View {
    @EnvironmentObject var store: TodoStore

    // Top New Memo Compose State
    @State private var title: String = ""
    @State private var tagsInput: String = ""
    @State private var content: String = ""
    @State private var pinned: Bool = false
    @State private var extractMemory: Bool = false
    @State private var pendingMedia: [MediaAttachmentModel] = []

    // In-Place Card Editing State (`在原位置直接修改`)
    @State private var inlineNoteId: String = ""
    @State private var inlineTitle: String = ""
    @State private var inlineTags: String = ""
    @State private var inlineContent: String = ""
    @State private var inlinePinned: Bool = false
    @State private var inlineExtract: Bool = false
    @State private var inlineExistingMedia: [MediaAttachmentModel] = []
    @State private var inlineNewMedia: [MediaAttachmentModel] = []
    @State private var isSavingInline: Bool = false

    // Detail Modal State (`点击查看详情`)
    @State private var detailTarget: NoteDetailPresentationTarget?

    // Filters & List
    @State private var searchQuery: String = ""
    @State private var selectedTag: String = ""
    @State private var pinnedOnly: Bool = false
    @State private var useGridLayout: Bool = true
    @State private var memoResp: MemoListResponse?
    @State private var isLoading: Bool = false

    private let gridColumns = [
        GridItem(.adaptive(minimum: 270, maximum: 460), spacing: 12)
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            composeCard
            filterToolbar
            memosCollection
        }
        .task {
            await reloadMemos()
        }
        .sheet(item: $detailTarget) { target in
            NoteAndJournalDetailSheet(
                kind: .memo,
                item: target.item,
                moodsCatalog: defaultMoodCatalogList
            ) { _ in
                Task { await reloadMemos() }
            }
            .environmentObject(store)
        }
    }

    private var composeCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                TextField("新建备忘标题（可选）...", text: $title)
                    .textFieldStyle(.roundedBorder)
                    .font(.subheadline.bold())
                TextField("#标签（空格分隔）", text: $tagsInput)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 165)
            }

            LiveMarkdownEditor(
                text: $content,
                placeholder: "支持实时 Markdown 渲染（# 标题、**加粗**、- 列表、- [ ] 清单）与拍照附图 OCR...",
                minHeight: 92,
                maxHeight: 200
            )

            if !pendingMedia.isEmpty {
                MediaThumbnailGridView(media: pendingMedia) { item in
                    pendingMedia.removeAll { $0.mediaId == item.mediaId }
                    Task { try? await store.client.deleteMedia(mediaId: item.mediaId) }
                }
            }

            HStack(spacing: 12) {
                Toggle("📌 置顶", isOn: $pinned)
                    .toggleStyle(.switch)
                    .controlSize(.small)
                Toggle("🧠 静默提炼长期偏好/事实", isOn: $extractMemory)
                    .toggleStyle(.switch)
                    .controlSize(.small)
                Spacer()
            }
            .font(.caption)

            HStack(spacing: 8) {
                Spacer()
                ImageAttachPickerButton(pendingMedia: $pendingMedia)
                VoiceMicButton(text: $content, compact: false)
                Button("✍️ 保存备忘") {
                    submitNewMemo()
                }
                .buttonStyle(.borderedProminent)
                .tint(store.activeTheme.primaryColor)
                .disabled(
                    content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    && title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    && pendingMedia.isEmpty
                )
            }
        }
        .padding(14)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(Color.primary.opacity(0.08), lineWidth: 1)
        )
    }

    private var filterToolbar: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                TextField("检索备忘标题、正文或图片 OCR...", text: $searchQuery)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { Task { await reloadMemos() } }
                Button("搜索") {
                    Task { await reloadMemos() }
                }
                .buttonStyle(.bordered)

                Button {
                    useGridLayout.toggle()
                } label: {
                    Image(systemName: useGridLayout ? "square.grid.2x2.fill" : "list.bullet")
                }
                .buttonStyle(.bordered)
            }

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    tagChip(label: "全部 (\(memoResp?.total ?? 0))", active: selectedTag.isEmpty && !pinnedOnly) {
                        selectedTag = ""
                        pinnedOnly = false
                        Task { await reloadMemos() }
                    }
                    tagChip(label: "📌 置顶 (\(memoResp?.pinnedCount ?? 0))", active: pinnedOnly) {
                        pinnedOnly.toggle()
                        Task { await reloadMemos() }
                    }
                    ForEach(memoResp?.tagsCatalog ?? []) { tc in
                        tagChip(label: "#\(tc.tag) (\(tc.count))", active: selectedTag == tc.tag) {
                            selectedTag = (selectedTag == tc.tag) ? "" : tc.tag
                            Task { await reloadMemos() }
                        }
                    }
                }
            }
        }
    }

    private func tagChip(label: String, active: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(.caption.bold())
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(active ? store.activeTheme.primaryColor : Color.primary.opacity(0.05))
                .foregroundStyle(active ? Color.white : .primary)
                .clipShape(Capsule())
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private var memosCollection: some View {
        let items = memoResp?.memos ?? []
        if isLoading && items.isEmpty {
            ProgressView("正在加载备忘录...")
                .frame(maxWidth: .infinity, minHeight: 160)
        } else if items.isEmpty {
            ContentUnavailableView("暂无备忘录", systemImage: "pin.slash", description: Text("支持置顶、#标签筛选、点击查看详情、原地编辑与拍照附图"))
                .padding(.vertical, 24)
        } else if useGridLayout {
            LazyVGrid(columns: gridColumns, alignment: .leading, spacing: 12) {
                ForEach(items) { item in
                    memoOrInlineEditor(item)
                }
            }
        } else {
            VStack(spacing: 12) {
                ForEach(items) { item in
                    memoOrInlineEditor(item)
                }
            }
        }
    }

    @ViewBuilder
    private func memoOrInlineEditor(_ item: RichNoteItemModel) -> some View {
        if inlineNoteId == item.noteId {
            inlineMemoEditorCard(item)
        } else {
            memoCard(item)
        }
    }

    private func inlineMemoEditorCard(_ item: RichNoteItemModel) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("✏️ 卡片原地修改")
                    .font(.caption2.bold())
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(store.activeTheme.primaryColor)
                    .foregroundStyle(.white)
                    .clipShape(Capsule())
                Spacer()
                Toggle("📌 置顶", isOn: $inlinePinned)
                    .toggleStyle(.switch)
                    .controlSize(.mini)
                    .font(.caption2)
                Toggle("🧠 记忆", isOn: $inlineExtract)
                    .toggleStyle(.switch)
                    .controlSize(.mini)
                    .font(.caption2)
            }

            HStack(spacing: 6) {
                TextField("备忘标题（可选）...", text: $inlineTitle)
                    .textFieldStyle(.roundedBorder)
                    .font(.subheadline.bold())
                TextField("#标签", text: $inlineTags)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 130)
            }

            LiveMarkdownEditor(
                text: $inlineContent,
                placeholder: "直接在此修改备忘正文（实时渲染 Markdown 排版）...",
                minHeight: 108,
                maxHeight: 230
            )

            let combined = inlineExistingMedia + inlineNewMedia
            if !combined.isEmpty {
                MediaThumbnailGridView(media: combined) { removed in
                    if inlineExistingMedia.contains(where: { $0.mediaId == removed.mediaId }) {
                        inlineExistingMedia.removeAll { $0.mediaId == removed.mediaId }
                    } else {
                        inlineNewMedia.removeAll { $0.mediaId == removed.mediaId }
                    }
                    Task { try? await store.client.deleteMedia(mediaId: removed.mediaId) }
                }
            }

            HStack(spacing: 6) {
                ImageAttachPickerButton(pendingMedia: $inlineNewMedia, compact: true)
                VoiceMicButton(text: $inlineContent, compact: true)
                Spacer()
                Button("取消") {
                    cancelInlineEdit()
                }
                .font(.caption)
                .buttonStyle(.bordered)

                Button(isSavingInline ? "保存中..." : "💾 保存") {
                    saveInlineMemo(noteId: item.noteId)
                }
                .font(.caption.bold())
                .buttonStyle(.borderedProminent)
                .tint(store.activeTheme.primaryColor)
                .disabled(isSavingInline)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(store.activeTheme.primaryColor, lineWidth: 2)
        )
        .shadow(color: store.activeTheme.primaryColor.opacity(0.14), radius: 8, y: 3)
    }

    private func memoCard(_ item: RichNoteItemModel) -> some View {
        let isPin = item.pinned ?? false
        return VStack(alignment: .leading, spacing: 8) {
            // Clickable Body Area -> Opens Full Detail Modal
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    if isPin {
                        Text("📌 置顶")
                            .font(.caption2.bold())
                            .padding(.horizontal, 7)
                            .padding(.vertical, 2)
                            .background(store.activeTheme.primaryColor)
                            .foregroundStyle(.white)
                            .clipShape(Capsule())
                    }
                    ForEach(item.tags ?? [], id: \.self) { t in
                        Button {
                            selectedTag = (selectedTag == t) ? "" : t
                            Task { await reloadMemos() }
                        } label: {
                            Text("#\(t)")
                                .font(.caption2.weight(.semibold))
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(store.activeTheme.primaryColor.opacity(0.12))
                                .foregroundStyle(store.activeTheme.primaryColor)
                                .clipShape(Capsule())
                        }
                        .buttonStyle(.plain)
                    }
                    Spacer(minLength: 4)
                    Text(String(item.updatedAt.prefix(16)).replacingOccurrences(of: "T", with: " "))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }

                if let t = item.title, !t.isEmpty {
                    Text(t)
                        .font(.headline)
                        .foregroundStyle(.primary)
                }

                MarkdownAnswerView(text: item.content)
                    .lineLimit(6)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .onTapGesture {
                detailTarget = NoteDetailPresentationTarget(kind: .memo, item: item)
            }

            if let mList = item.media, !mList.isEmpty {
                MediaThumbnailGridView(media: mList)
            }

            Divider().opacity(0.45)

            HStack(spacing: 10) {
                Button {
                    detailTarget = NoteDetailPresentationTarget(kind: .memo, item: item)
                } label: {
                    Text("🔍 详情")
                        .font(.caption.weight(.medium))
                }
                .buttonStyle(.borderless)

                Spacer()

                Button(isPin ? "取消置顶" : "📌 置顶") {
                    Task {
                        try? await store.client.toggleMemoPin(noteId: item.noteId, pinned: !isPin)
                        await reloadMemos()
                    }
                }
                .font(.caption)
                .buttonStyle(.borderless)

                Button("✏️ 原地改") {
                    startInlineEdit(item)
                }
                .font(.caption.bold())
                .foregroundStyle(store.activeTheme.primaryColor)
                .buttonStyle(.borderless)

                Button(role: .destructive) {
                    Task {
                        try? await store.client.forgetNote(noteId: item.noteId)
                        await reloadMemos()
                        await store.refresh()
                    }
                } label: {
                    Text("🗑️ 遗忘")
                        .font(.caption)
                }
                .buttonStyle(.borderless)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(isPin ? store.activeTheme.primaryColor : Color.primary.opacity(0.08), lineWidth: isPin ? 1.5 : 1)
        )
    }

    private func startInlineEdit(_ item: RichNoteItemModel) {
        for nm in inlineNewMedia {
            Task { try? await store.client.deleteMedia(mediaId: nm.mediaId) }
        }
        inlineNoteId = item.noteId
        inlineTitle = item.title ?? ""
        inlineTags = (item.tags ?? []).joined(separator: " ")
        inlineContent = item.content
        inlinePinned = item.pinned ?? false
        inlineExtract = item.extractMemory ?? false
        inlineExistingMedia = item.media ?? []
        inlineNewMedia = []
    }

    private func cancelInlineEdit() {
        for nm in inlineNewMedia {
            Task { try? await store.client.deleteMedia(mediaId: nm.mediaId) }
        }
        inlineNewMedia = []
        inlineExistingMedia = []
        inlineNoteId = ""
    }

    private func saveInlineMemo(noteId: String) {
        let cleanTitle = inlineTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanContent = inlineContent.trimmingCharacters(in: .whitespacesAndNewlines)
        if cleanTitle.isEmpty && cleanContent.isEmpty && inlineExistingMedia.isEmpty && inlineNewMedia.isEmpty {
            store.showBanner("请输入备忘内容或附加图片")
            return
        }
        isSavingInline = true
        dismissAppKeyboard()
        let body: [String: Any] = [
            "note_id": noteId,
            "title": cleanTitle,
            "content": cleanContent,
            "tags": inlineTags,
            "pinned": inlinePinned,
            "extract_memory": inlineExtract,
            "media_ids": inlineNewMedia.map(\.mediaId)
        ]
        Task {
            defer { isSavingInline = false }
            if (try? await store.client.saveMemo(body: body)) != nil {
                inlineNewMedia = []
                inlineExistingMedia = []
                inlineNoteId = ""
                store.showBanner("✅ 已在原位置保存备忘修改")
                await reloadMemos()
            }
        }
    }

    private func submitNewMemo() {
        let body: [String: Any] = [
            "note_id": "",
            "title": title.trimmingCharacters(in: .whitespacesAndNewlines),
            "content": content.trimmingCharacters(in: .whitespacesAndNewlines),
            "tags": tagsInput,
            "pinned": pinned,
            "extract_memory": extractMemory,
            "media_ids": pendingMedia.map(\.mediaId)
        ]
        dismissAppKeyboard()
        Task {
            _ = try? await store.client.saveMemo(body: body)
            title = ""
            tagsInput = ""
            content = ""
            pinned = false
            extractMemory = false
            pendingMedia = []
            store.showBanner("✅ 新备忘录已保存")
            await reloadMemos()
        }
    }

    private func reloadMemos() async {
        isLoading = true
        defer { isLoading = false }
        if let res = try? await store.client.fetchMemos(query: searchQuery, tag: selectedTag, pinnedOnly: pinnedOnly) {
            memoResp = res
        }
    }
}

// MARK: - Subview 2: One-Page-Per-Day Mood Journal + Click Detail + In-Place Edit

struct JournalDailyStudioView: View {
    @EnvironmentObject var store: TodoStore

    @State private var selectedDate: String = CalendarTimelineView.todayYMD()
    @State private var selectedMood: String = "calm"
    @State private var draftContent: String = ""
    @State private var extractMemory: Bool = true
    @State private var pendingMedia: [MediaAttachmentModel] = []
    @State private var overview: JournalOverviewResponse?
    @State private var isBusy: Bool = false

    // In-Place History Journal Edit State (`在原位置直接修改`)
    @State private var inlineJournalId: String = ""
    @State private var inlineJournalMood: String = "calm"
    @State private var inlineJournalContent: String = ""
    @State private var inlineJournalExtract: Bool = true
    @State private var inlineExistingMedia: [MediaAttachmentModel] = []
    @State private var inlineNewMedia: [MediaAttachmentModel] = []
    @State private var isSavingInline: Bool = false

    // Detail Modal State (`点击查看详情`)
    @State private var detailTarget: NoteDetailPresentationTarget?

    var body: some View {
        let moods = overview?.moodCatalog ?? defaultMoodCatalogList
        VStack(alignment: .leading, spacing: 14) {
            journalEditorCard(moods: moods)
            historySection(moods: moods)
        }
        .task {
            await reloadJournal()
        }
        .sheet(item: $detailTarget) { target in
            NoteAndJournalDetailSheet(
                kind: .journal,
                item: target.item,
                moodsCatalog: moods
            ) { _ in
                Task { await reloadJournal() }
            }
            .environmentObject(store)
        }
    }

    private func journalEditorCard(moods: [MoodCatalogItem]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Button { shiftDay(-1) } label: { Image(systemName: "chevron.left") }
                    .buttonStyle(.bordered)
                Text(selectedDate)
                    .font(.subheadline.bold().monospacedDigit())
                Button("今天") {
                    selectedDate = CalendarTimelineView.todayYMD()
                    Task { await reloadJournal() }
                }
                .buttonStyle(.bordered)
                Button { shiftDay(1) } label: { Image(systemName: "chevron.right") }
                    .buttonStyle(.bordered)

                Spacer()

                Button {
                    triggerAISummary(withDream: false)
                } label: {
                    Text(isBusy ? "汇总中..." : "✨ AI 今日小结")
                        .font(.caption.bold())
                }
                .buttonStyle(.borderedProminent)
                .tint(store.activeTheme.primaryColor)
                .disabled(isBusy)

                Button {
                    triggerAISummary(withDream: true)
                } label: {
                    Text("🌙 小结+Dream")
                        .font(.caption.bold())
                }
                .buttonStyle(.bordered)
                .disabled(isBusy)
            }

            if let ov = overview {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        Text("\(ov.lunarLabel) · \(ov.weekdayLabel)")
                            .font(.caption2.bold())
                            .foregroundStyle(.secondary)
                        footprintBadge("✅ 完成待办 \(ov.dayFootprint.completedCount) 项")
                        footprintBadge("🍅 专注 \(ov.dayFootprint.focusMinutes)m (\(ov.dayFootprint.focusPomodoros) 番茄)")
                        footprintBadge("📌 随记 \(ov.dayFootprint.memoCount) 条")
                    }
                }
            }

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(moods) { m in
                        Button {
                            selectedMood = m.id
                        } label: {
                            Text("\(m.icon) \(m.label)")
                                .font(.caption.bold())
                                .padding(.horizontal, 11)
                                .padding(.vertical, 6)
                                .background(selectedMood == m.id ? store.activeTheme.primaryColor.opacity(0.16) : Color.primary.opacity(0.04))
                                .foregroundStyle(selectedMood == m.id ? store.activeTheme.primaryColor : .primary)
                                .clipShape(Capsule())
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            LiveMarkdownEditor(
                text: $draftContent,
                placeholder: "写下今天的经历、感悟（实时渲染 Markdown）或点击右上角「✨ AI 今日小结」自动汇总...",
                minHeight: 145,
                maxHeight: 300
            )

            if let curMedia = overview?.currentEntry?.media, !curMedia.isEmpty {
                MediaThumbnailGridView(media: curMedia) { removed in
                    Task {
                        try? await store.client.deleteMedia(mediaId: removed.mediaId)
                        await reloadJournal()
                    }
                }
            }

            if !pendingMedia.isEmpty {
                MediaThumbnailGridView(media: pendingMedia) { item in
                    pendingMedia.removeAll { $0.mediaId == item.mediaId }
                    Task { try? await store.client.deleteMedia(mediaId: item.mediaId) }
                }
            }

            HStack {
                Toggle("🧠 静默沉淀长期经历/偏好", isOn: $extractMemory)
                    .toggleStyle(.switch)
                    .controlSize(.small)
                    .font(.caption)
                Spacer()
                ImageAttachPickerButton(pendingMedia: $pendingMedia)
                VoiceMicButton(text: $draftContent, compact: false)
                Button("💾 保存当日日记") {
                    saveJournal()
                }
                .buttonStyle(.borderedProminent)
                .tint(store.activeTheme.primaryColor)
                .disabled(isBusy || (draftContent.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && pendingMedia.isEmpty))
            }
        }
        .padding(14)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(Color.primary.opacity(0.08), lineWidth: 1)
        )
    }

    private func footprintBadge(_ text: String) -> some View {
        Text(text)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(store.activeTheme.primaryColor.opacity(0.10))
            .foregroundStyle(store.activeTheme.primaryColor)
            .clipShape(Capsule())
    }

    private func historySection(moods: [MoodCatalogItem]) -> some View {
        let list = overview?.timeline ?? []
        return VStack(alignment: .leading, spacing: 10) {
            Text("🕰️ 历史日记流（一日一页 · 共 \(list.count) 篇 · 点击卡片看详情或原地修改）")
                .font(.subheadline.bold())

            if list.isEmpty {
                Text("还没有历史日记，点击上方「✨ AI 今日小结」开启第一篇每日复盘吧。")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 8)
            } else {
                ForEach(list) { entry in
                    if inlineJournalId == entry.noteId {
                        inlineJournalCard(entry: entry, moods: moods)
                    } else {
                        historyJournalCard(entry: entry)
                    }
                }
            }
        }
    }

    private func inlineJournalCard(entry: RichNoteItemModel, moods: [MoodCatalogItem]) -> some View {
        let ymd = entry.journalDate ?? String(entry.eventTime.prefix(10))
        return VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("📅 \(ymd) \(entry.weekdayLabel ?? "")")
                    .font(.subheadline.bold())
                if let lunar = entry.lunarLabel, !lunar.isEmpty {
                    Text(lunar)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text("✏️ 原地修改")
                    .font(.caption2.bold())
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(store.activeTheme.primaryColor)
                    .foregroundStyle(.white)
                    .clipShape(Capsule())
            }

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(moods) { m in
                        Button {
                            inlineJournalMood = m.id
                        } label: {
                            Text("\(m.icon) \(m.label)")
                                .font(.caption2.bold())
                                .padding(.horizontal, 9)
                                .padding(.vertical, 5)
                                .background(inlineJournalMood == m.id ? store.activeTheme.primaryColor.opacity(0.18) : Color.primary.opacity(0.05))
                                .foregroundStyle(inlineJournalMood == m.id ? store.activeTheme.primaryColor : .primary)
                                .clipShape(Capsule())
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            LiveMarkdownEditor(
                text: $inlineJournalContent,
                placeholder: "直接修改当日日记正文（实时渲染 Markdown 排版）...",
                minHeight: 118,
                maxHeight: 250
            )

            let combined = inlineExistingMedia + inlineNewMedia
            if !combined.isEmpty {
                MediaThumbnailGridView(media: combined) { removed in
                    if inlineExistingMedia.contains(where: { $0.mediaId == removed.mediaId }) {
                        inlineExistingMedia.removeAll { $0.mediaId == removed.mediaId }
                    } else {
                        inlineNewMedia.removeAll { $0.mediaId == removed.mediaId }
                    }
                    Task { try? await store.client.deleteMedia(mediaId: removed.mediaId) }
                }
            }

            HStack(spacing: 8) {
                Toggle("🧠 沉淀长期记忆", isOn: $inlineJournalExtract)
                    .toggleStyle(.switch)
                    .controlSize(.mini)
                    .font(.caption2)
                Spacer()
                ImageAttachPickerButton(pendingMedia: $inlineNewMedia, compact: true)
                VoiceMicButton(text: $inlineJournalContent, compact: true)
                Button("取消") {
                    cancelInlineJournalEdit()
                }
                .font(.caption)
                .buttonStyle(.bordered)
                Button(isSavingInline ? "保存中..." : "💾 保存") {
                    saveInlineJournal(entry: entry)
                }
                .font(.caption.bold())
                .buttonStyle(.borderedProminent)
                .tint(store.activeTheme.primaryColor)
                .disabled(isSavingInline)
            }
        }
        .padding(14)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(store.activeTheme.primaryColor, lineWidth: 2)
        )
    }

    private func historyJournalCard(entry: RichNoteItemModel) -> some View {
        let ymd = entry.journalDate ?? String(entry.eventTime.prefix(10))
        return VStack(alignment: .leading, spacing: 8) {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("📅 \(ymd) \(entry.weekdayLabel ?? "")")
                        .font(.subheadline.bold())
                    if let mm = entry.moodMeta {
                        Text("\(mm.icon) \(mm.label)")
                            .font(.caption2.bold())
                            .padding(.horizontal, 7)
                            .padding(.vertical, 2)
                            .background(store.activeTheme.primaryColor.opacity(0.12))
                            .clipShape(Capsule())
                    }
                    if let lunar = entry.lunarLabel, !lunar.isEmpty {
                        Text(lunar)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                }

                MarkdownAnswerView(text: entry.content)
                    .lineLimit(6)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .onTapGesture {
                detailTarget = NoteDetailPresentationTarget(kind: .journal, item: entry)
            }

            if let m = entry.media, !m.isEmpty {
                MediaThumbnailGridView(media: m)
            }

            Divider().opacity(0.4)

            HStack(spacing: 10) {
                Button("🔍 详情") {
                    detailTarget = NoteDetailPresentationTarget(kind: .journal, item: entry)
                }
                .font(.caption)
                .buttonStyle(.borderless)

                Spacer()

                Button("✏️ 原地改") {
                    startInlineJournalEdit(entry)
                }
                .font(.caption.bold())
                .foregroundStyle(store.activeTheme.primaryColor)
                .buttonStyle(.borderless)

                Button("切换到此日") {
                    selectedDate = ymd
                    Task { await reloadJournal() }
                }
                .font(.caption)
                .buttonStyle(.borderless)

                Button(role: .destructive) {
                    Task {
                        try? await store.client.forgetNote(noteId: entry.noteId)
                        await reloadJournal()
                    }
                } label: {
                    Text("🗑️ 遗忘")
                        .font(.caption)
                }
                .buttonStyle(.borderless)
            }
        }
        .padding(12)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(ymd == selectedDate ? store.activeTheme.primaryColor : Color.primary.opacity(0.07), lineWidth: ymd == selectedDate ? 1.5 : 1)
        )
    }

    private func startInlineJournalEdit(_ entry: RichNoteItemModel) {
        for nm in inlineNewMedia {
            Task { try? await store.client.deleteMedia(mediaId: nm.mediaId) }
        }
        inlineJournalId = entry.noteId
        inlineJournalMood = entry.mood ?? "calm"
        inlineJournalContent = entry.content
        inlineJournalExtract = entry.extractMemory ?? true
        inlineExistingMedia = entry.media ?? []
        inlineNewMedia = []
    }

    private func cancelInlineJournalEdit() {
        for nm in inlineNewMedia {
            Task { try? await store.client.deleteMedia(mediaId: nm.mediaId) }
        }
        inlineNewMedia = []
        inlineExistingMedia = []
        inlineJournalId = ""
    }

    private func saveInlineJournal(entry: RichNoteItemModel) {
        let ymd = entry.journalDate ?? String(entry.eventTime.prefix(10))
        let clean = inlineJournalContent.trimmingCharacters(in: .whitespacesAndNewlines)
        if clean.isEmpty && inlineExistingMedia.isEmpty && inlineNewMedia.isEmpty {
            store.showBanner("请输入日记内容或附加照片")
            return
        }
        isSavingInline = true
        dismissAppKeyboard()
        let body: [String: Any] = [
            "note_id": entry.noteId,
            "journal_date": ymd,
            "mood": inlineJournalMood,
            "content": clean,
            "extract_memory": inlineJournalExtract,
            "media_ids": inlineNewMedia.map(\.mediaId)
        ]
        Task {
            defer { isSavingInline = false }
            if (try? await store.client.saveJournal(body: body)) != nil {
                inlineNewMedia = []
                inlineExistingMedia = []
                inlineJournalId = ""
                store.showBanner("📖 已原地保存 \(ymd) 日记")
                await reloadJournal()
            }
        }
    }

    private func shiftDay(_ delta: Int) {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "Asia/Shanghai") ?? .current
        let cur = f.date(from: selectedDate) ?? Date()
        if let next = Calendar(identifier: .gregorian).date(byAdding: .day, value: delta, to: cur) {
            selectedDate = f.string(from: next)
            Task { await reloadJournal() }
        }
    }

    private func triggerAISummary(withDream: Bool) {
        isBusy = true
        Task {
            defer { isBusy = false }
            if let res = try? await store.client.generateJournalAISummary(
                date: selectedDate,
                draftContent: draftContent,
                triggerDream: withDream
            ) {
                let clean = draftContent.trimmingCharacters(in: .whitespacesAndNewlines)
                draftContent = clean.isEmpty ? res.summaryMarkdown : "\(clean)\n\n\(res.summaryMarkdown)"
                store.showBanner(withDream ? "🌙 已生成今日小结并完成 Dream 归纳" : "✨ 已汇总今日待办与专注时长")
            }
        }
    }

    private func saveJournal() {
        isBusy = true
        dismissAppKeyboard()
        let body: [String: Any] = [
            "journal_date": selectedDate,
            "mood": selectedMood,
            "content": draftContent.trimmingCharacters(in: .whitespacesAndNewlines),
            "extract_memory": extractMemory,
            "media_ids": pendingMedia.map(\.mediaId)
        ]
        Task {
            defer { isBusy = false }
            _ = try? await store.client.saveJournal(body: body)
            pendingMedia = []
            store.showBanner("📖 \(selectedDate) 日记已保存")
            await reloadJournal()
        }
    }

    private func reloadJournal() async {
        if let ov = try? await store.client.fetchJournalOverview(date: selectedDate, limit: 30) {
            overview = ov
            if let cur = ov.currentEntry {
                draftContent = cur.content
                selectedMood = cur.mood ?? "calm"
                extractMemory = cur.extractMemory ?? true
            } else {
                draftContent = ""
                selectedMood = "calm"
                extractMemory = true
            }
        }
    }
}
