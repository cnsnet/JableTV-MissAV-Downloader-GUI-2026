import SwiftUI
import UIKit

extension Color {
    static let favourite = Color(red: 0xEF / 255, green: 0x44 / 255, blue: 0x44 / 255)
    // Same labels and colours as MissAV's own cards, which is how it tells a
    // code's pages (ipx-771 / ipx-771-uncensored-leak ...) apart.
    static let chineseSubtitle = Color(red: 0x99 / 255, green: 0x1B / 255, blue: 0x1B / 255)
    static let uncensoredLeak = Color(red: 0x1E / 255, green: 0x40 / 255, blue: 0xAF / 255)
}

/// Two columns edge to edge with a hairline gap, like the Android grid.
let videoGridColumns = [GridItem(.flexible(), spacing: 2), GridItem(.flexible(), spacing: 2)]

struct VersionBadges: View {
    let hasChineseSubtitle: Bool?
    let isUncensoredLeak: Bool?

    var body: some View {
        if hasChineseSubtitle == true || isUncensoredLeak == true {
            HStack(spacing: 4) {
                if hasChineseSubtitle == true { badge("中文字幕", .chineseSubtitle) }
                if isUncensoredLeak == true { badge("无码影片", .uncensoredLeak) }
            }
        }
    }

    private func badge(_ text: String, _ color: Color) -> some View {
        Text(text)
            .font(.caption2)
            .foregroundColor(.white)
            .padding(.horizontal, 4)
            .padding(.vertical, 1)
            .background(color.opacity(0.75), in: RoundedRectangle(cornerRadius: 4))
    }
}

/// Covers kept in memory, so scrolling back doesn't flash empty cards.
final class ImageCache {
    static let shared = ImageCache()
    private let cache = NSCache<NSURL, UIImage>()

    init() { cache.countLimit = 400 }

    func image(for url: URL) -> UIImage? { cache.object(forKey: url as NSURL) }

    func load(_ url: URL) async -> UIImage? {
        if let image = image(for: url) { return image }
        guard let result = try? await URLSession.shared.data(from: url),
              (result.1 as? HTTPURLResponse)?.statusCode == 200,
              let image = UIImage(data: result.0) else { return nil }
        cache.setObject(image, forKey: url as NSURL)
        return image
    }
}

struct RemoteImage: View {
    let url: URL?
    @State private var image: UIImage?

    var body: some View {
        ZStack {
            Color(.tertiarySystemFill)
            if let shown = image ?? url.flatMap({ ImageCache.shared.image(for: $0) }) {
                Image(uiImage: shown).resizable().scaledToFill()
            }
        }
        .task(id: url) {
            guard let url else { return }
            // The old image stays until the new one is in (e.g. the detail's large cover).
            let loaded = await ImageCache.shared.load(url)
            if !Task.isCancelled, let loaded { image = loaded }
        }
    }
}

/// Tap selects, long-press opens the detail page, as on Android.
struct VideoCard: View {
    let video: BrowseVideo
    let thumbURL: URL?
    let isSelected: Bool
    let onToggle: () -> Void
    let onOpen: () -> Void
    let onToggleFavourite: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Color.clear
                .aspectRatio(16 / 9, contentMode: .fit)
                .overlay(RemoteImage(url: thumbURL))
                .clipped()
                .overlay(alignment: .bottomLeading) {
                    VersionBadges(hasChineseSubtitle: video.hasChineseSubtitle,
                                  isUncensoredLeak: video.isUncensoredLeak)
                        .padding(4)
                }
                .overlay(alignment: .bottomTrailing) {
                    if !video.duration.isEmpty {
                        Text(video.duration)
                            .font(.caption2)
                            .foregroundColor(.white)
                            .padding(.horizontal, 4)
                            .padding(.vertical, 1)
                            .background(Color.black.opacity(0.6), in: RoundedRectangle(cornerRadius: 4))
                            .padding(4)
                    }
                }
                .overlay(alignment: .topLeading) {
                    if isSelected {
                        Image(systemName: "checkmark")
                            .font(.system(size: 11, weight: .bold))
                            .foregroundColor(.white)
                            .padding(5)
                            .background(Color.accentColor, in: Circle())
                            .padding(4)
                    }
                }
                .overlay(alignment: .topTrailing) {
                    Button(action: onToggleFavourite) {
                        Image(systemName: video.favourite ? "heart.fill" : "heart")
                            .font(.system(size: 13))
                            .foregroundColor(video.favourite ? .favourite : .white)
                            .frame(width: 26, height: 26)
                            .background(Color.black.opacity(0.4), in: Circle())
                    }
                    .buttonStyle(.plain)
                    .padding(4)
                    .accessibilityLabel(video.favourite ? "取消收藏" : "收藏")
                }
            Text(video.title)
                .font(.caption)
                .fontWeight(.medium)
                .lineLimit(2, reservesSpace: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(8)
        }
        .background(Color(.secondarySystemBackground))
        .overlay(Rectangle().strokeBorder(Color.accentColor, lineWidth: isSelected ? 2 : 0))
        .contentShape(Rectangle())
        .onTapGesture(perform: onToggle)
        .onLongPressGesture(minimumDuration: 0.35) {
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            onOpen()
        }
    }
}

/// Bottom bar while cards are selected: delete (after a confirm) or download.
struct SelectionBar: View {
    let count: Int
    let submitting: Bool
    let removing: Bool
    let onSubmit: () -> Void
    let onRemove: () -> Void
    @State private var confirmRemove = false

    var body: some View {
        HStack(spacing: 8) {
            Text("已选 \(count) 个")
            Spacer()
            Button(removing ? "删除中..." : "删除") { confirmRemove = true }
                .buttonStyle(.bordered)
                .disabled(removing || submitting)
            Button(submitting ? "提交中..." : "下载", action: onSubmit)
                .buttonStyle(.borderedProminent)
                .disabled(removing || submitting)
        }
        .padding(12)
        .background(.bar)
        .alert("删除视频", isPresented: $confirmRemove) {
            Button("删除", role: .destructive, action: onRemove)
            Button("取消", role: .cancel) {}
        } message: {
            Text("删除选中的 \(count) 个视频？删除后它们将不再出现在任何列表中。")
        }
    }
}
