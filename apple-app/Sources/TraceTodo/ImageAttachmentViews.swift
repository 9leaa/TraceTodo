import Foundation
import SwiftUI
import PhotosUI
import ImageIO
import UniformTypeIdentifiers

struct MediaAttachmentModel: Codable, Identifiable, Hashable {
    let mediaId: String
    let noteId: String?
    let mimeType: String
    let width: Int
    let height: Int
    let sizeBytes: Int
    let caption: String
    let ocrText: String
    let visionStatus: String
    let url: String
    let thumbUrl: String

    var id: String { mediaId }

    enum CodingKeys: String, CodingKey {
        case mediaId = "media_id"
        case noteId = "note_id"
        case mimeType = "mime_type"
        case width
        case height
        case sizeBytes = "size_bytes"
        case caption
        case ocrText = "ocr_text"
        case visionStatus = "vision_status"
        case url
        case thumbUrl = "thumb_url"
    }
}

struct MediaUploadResponse: Codable {
    let media: MediaAttachmentModel
    let usedBytes: Int?

    enum CodingKeys: String, CodingKey {
        case media
        case usedBytes = "used_bytes"
    }
}

/// Cross-platform (macOS + iOS) ImageIO JPEG compressor targeting ~150-280 KB + 320px thumbnail.
enum ImageClientCompressor {
    struct CompressedPair {
        let mainBase64: String
        let thumbBase64: String
        let width: Int
        let height: Int
    }

    static func compress(rawData: Data) -> CompressedPair? {
        guard let src = CGImageSourceCreateWithData(rawData as CFData, nil) else {
            return nil
        }
        guard let (mainData, w, h) = makeJpegThumbnail(from: src, maxPixelSize: 1440, quality: 0.76),
              let (thumbData, _, _) = makeJpegThumbnail(from: src, maxPixelSize: 320, quality: 0.70) else {
            return nil
        }
        return CompressedPair(
            mainBase64: "data:image/jpeg;base64," + mainData.base64EncodedString(),
            thumbBase64: "data:image/jpeg;base64," + thumbData.base64EncodedString(),
            width: w,
            height: h
        )
    }

    private static func makeJpegThumbnail(
        from src: CGImageSource,
        maxPixelSize: Int,
        quality: CGFloat
    ) -> (Data, Int, Int)? {
        let opts: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true,
            kCGImageSourceThumbnailMaxPixelSize: maxPixelSize
        ]
        guard let cgImg = CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary) else {
            return nil
        }
        let outData = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(
            outData,
            UTType.jpeg.identifier as CFString,
            1,
            nil
        ) else {
            return nil
        }
        let destProps: [CFString: Any] = [
            kCGImageDestinationLossyCompressionQuality: quality
        ]
        CGImageDestinationAddImage(dest, cgImg, destProps as CFDictionary)
        guard CGImageDestinationFinalize(dest) else {
            return nil
        }
        return (outData as Data, cgImg.width, cgImg.height)
    }
}

/// PhotosPicker button that compresses selected images on-device before uploading to `/api/media/upload`.
struct ImageAttachPickerButton: View {
    @EnvironmentObject var store: TodoStore
    @Binding var pendingMedia: [MediaAttachmentModel]
    @State private var selectedItems: [PhotosPickerItem] = []
    @State private var isUploading: Bool = false

    var body: some View {
        PhotosPicker(
            selection: $selectedItems,
            maxSelectionCount: 6,
            matching: .images
        ) {
            HStack(spacing: 4) {
                if isUploading {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "photo.badge.plus")
                        .font(.system(size: 13.5, weight: .semibold))
                }
                Text(isUploading ? "压缩中" : "附图")
                    .font(.caption.bold())
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(Color.primary.opacity(0.06))
            .clipShape(Capsule())
        }
        .buttonStyle(.plain)
        .onChange(of: selectedItems) { _, newItems in
            guard !newItems.isEmpty else { return }
            Task {
                await uploadPickedItems(newItems)
                selectedItems = []
            }
        }
    }

    private func uploadPickedItems(_ items: [PhotosPickerItem]) async {
        isUploading = true
        defer { isUploading = false }
        for item in items {
            guard let rawData = try? await item.loadTransferable(type: Data.self),
                  let compressed = ImageClientCompressor.compress(rawData: rawData) else {
                continue
            }
            do {
                let uploaded = try await store.client.uploadMedia(
                    imageBase64: compressed.mainBase64,
                    thumbBase64: compressed.thumbBase64,
                    width: compressed.width,
                    height: compressed.height
                )
                pendingMedia.append(uploaded.media)
            } catch {
                store.showBanner("图片上传失败：\(error.localizedDescription)")
            }
        }
    }
}

/// Thumbnail grid with tap-to-inspect full image, VLM Caption, and OCR text.
struct MediaThumbnailGridView: View {
    @EnvironmentObject var store: TodoStore
    let media: [MediaAttachmentModel]
    var onRemoveDraft: ((MediaAttachmentModel) -> Void)? = nil

    @State private var inspectedItem: MediaAttachmentModel?

    private func fullURL(_ relPath: String) -> URL? {
        let base = store.serverURL
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        return URL(string: base + relPath)
    }

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(media) { m in
                    ZStack(alignment: .topTrailing) {
                        Button {
                            inspectedItem = m
                        } label: {
                            AsyncImage(url: fullURL(m.thumbUrl.isEmpty ? m.url : m.thumbUrl)) { phase in
                                switch phase {
                                case .success(let image):
                                    image
                                        .resizable()
                                        .scaledToFill()
                                case .failure:
                                    Image(systemName: "photo")
                                        .foregroundStyle(.secondary)
                                default:
                                    ProgressView().controlSize(.small)
                                }
                            }
                            .frame(width: 72, height: 72)
                            .background(Color.primary.opacity(0.05))
                            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                            .overlay(
                                RoundedRectangle(cornerRadius: 10, style: .continuous)
                                    .stroke(Color.primary.opacity(0.1), lineWidth: 1)
                            )
                        }
                        .buttonStyle(.plain)

                        if let onRemoveDraft {
                            Button {
                                onRemoveDraft(m)
                            } label: {
                                Image(systemName: "xmark.circle.fill")
                                    .font(.system(size: 16))
                                    .foregroundStyle(.white, .black.opacity(0.7))
                            }
                            .buttonStyle(.plain)
                            .offset(x: 4, y: -4)
                        }
                    }
                }
            }
            .padding(.vertical, 2)
        }
        .sheet(item: $inspectedItem) { item in
            NavigationStack {
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        if let url = fullURL(item.url) {
                            AsyncImage(url: url) { phase in
                                if case .success(let img) = phase {
                                    img
                                        .resizable()
                                        .scaledToFit()
                                        .frame(maxWidth: .infinity, maxHeight: 420)
                                        .clipShape(RoundedRectangle(cornerRadius: 12))
                                } else {
                                    ProgressView()
                                        .frame(maxWidth: .infinity, minHeight: 180)
                                }
                            }
                        }

                        HStack(spacing: 8) {
                            Text("\(item.width)×\(item.height)")
                                .font(.caption2)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 3)
                                .background(Color.primary.opacity(0.06))
                                .clipShape(Capsule())
                            Text("\(max(1, item.sizeBytes / 1024)) KB")
                                .font(.caption2)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 3)
                                .background(Color.primary.opacity(0.06))
                                .clipShape(Capsule())
                        }

                        if !item.caption.isEmpty {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("👁️ 视觉摘要 (Caption)")
                                    .font(.caption.bold())
                                    .foregroundStyle(.secondary)
                                Text(item.caption)
                                    .font(.subheadline)
                            }
                        }

                        if !item.ocrText.isEmpty {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("📄 图中文字 (OCR)")
                                    .font(.caption.bold())
                                    .foregroundStyle(.secondary)
                                Text(item.ocrText)
                                    .font(.footnote)
                                    .padding(10)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .background(Color.primary.opacity(0.04))
                                    .clipShape(RoundedRectangle(cornerRadius: 10))
                            }
                        }
                    }
                    .padding(16)
                }
                .navigationTitle("视觉记忆溯源")
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("关闭") { inspectedItem = nil }
                    }
                }
            }
            #if os(macOS)
            .frame(minWidth: 520, minHeight: 480)
            #endif
        }
    }
}
