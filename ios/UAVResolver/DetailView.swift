import SwiftUI

struct DetailView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.openURL) private var openURL
    @Environment(\.dismiss) private var dismiss
    let video: BrowseVideo

    @State private var detail: VideoDetail?
    @State private var loading = true
    @State private var error: String?
    /// Bumped by 重试 to fetch the detail again.
    @State private var attempt = 0
    @State private var loadedAttempt: Int?
    @State private var downloading = false
    @State private var related: [BrowseVideo] = []
    @State private var relatedLoading = false
    @State private var relatedLoaded = false
    @State private var selected = Set<String>()
    @State private var submitting = false
    @State private var removingSelected = false
    @State private var confirmRemove = false
    @State private var removing = false
    @State private var favourite: Bool
    @State private var favouriting = false

    init(video: BrowseVideo) {
        self.video = video
        _favourite = State(initialValue: video.favourite)
    }

    // Browse only lists JableTV and MissAV, and both have recommendations
    // (MissAV via its recommender, Jable's "猜你喜歡" block on the video page).
    private var relatedSite: String {
        video.url.range(of: "missav", options: .caseInsensitive) != nil ? "missav" : "jabletv"
    }

    // The listing gives the id; failing that its title usually leads with the
    // code ("IPZZ-891 ..."), so the title needn't wait for /api/detail.
    private var code: String {
        (detail?.slug.nonEmpty ?? video.slug.nonEmpty)?.uppercased()
            ?? String(video.title.split(separator: " ").first ?? "")
    }

    /// /api/detail may swap an untranslated Japanese title for a Chinese one.
    private var title: String { detail?.title.nonEmpty ?? video.title }
    private var resolved: Bool { !(detail?.resolvedUrl.isEmpty ?? true) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                cover
                info
                if relatedLoading || !related.isEmpty {
                    HStack(spacing: 8) {
                        Text("相关推荐").font(.headline)
                        if relatedLoading { ProgressView() }
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 16)
                    .padding(.bottom, 8)
                }
                LazyVGrid(columns: videoGridColumns, spacing: 2) {
                    ForEach(related) { item in
                        VideoCard(
                            video: item,
                            thumbURL: model.api.thumbURL(item.thumbnail),
                            isSelected: selected.contains(item.url),
                            onToggle: {
                                if selected.contains(item.url) { selected.remove(item.url) } else { selected.insert(item.url) }
                            },
                            // Opens another detail page on top.
                            onOpen: { model.path.append(.detail(item)) },
                            onToggleFavourite: {
                                Task { await model.toggleFavourite(item.url, current: item.favourite) }
                            })
                    }
                }
                .padding(1)
            }
        }
        .safeAreaInset(edge: .bottom) {
            if !selected.isEmpty {
                SelectionBar(count: selected.count, submitting: submitting, removing: removingSelected,
                             onSubmit: submitSelected, onRemove: removeSelected)
            }
        }
        .navigationTitle(code)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Color(white: 0.13), for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        // Both run again when the page reappears (back from a page stacked
        // on top); the flags keep that from fetching everything again.
        .task(id: attempt) { await loadDetail() }
        .task { await loadRelated() }
        .onReceive(model.favouriteChanged) { change in
            if change.url == video.url { favourite = change.favourite }
            if let index = related.firstIndex(where: { $0.url == change.url }) {
                related[index].favourite = change.favourite
            }
        }
        .onReceive(model.videoRemoved) { url in
            related.removeAll { $0.url == url }
            selected.remove(url)
        }
        .alert("删除视频", isPresented: $confirmRemove) {
            Button("删除", role: .destructive, action: removeVideo)
            Button("取消", role: .cancel) {}
        } message: {
            Text("删除后 \(code) 将不再出现在任何列表中。")
        }
    }

    // MARK: Parts

    private var cover: some View {
        Color.clear
            .aspectRatio(16 / 9, contentMode: .fit)
            // The large cover once /api/detail gave it, the listing's until then.
            .overlay(RemoteImage(url: model.api.thumbURL(detail?.thumbnail.nonEmpty ?? video.thumbnail)))
            .clipped()
            .overlay(alignment: .bottomTrailing) {
                Button(action: toggleFavourite) {
                    ZStack {
                        if favouriting {
                            ProgressView().tint(.white)
                        } else {
                            Image(systemName: favourite ? "heart.fill" : "heart")
                                .font(.title3)
                                .foregroundColor(favourite ? .favourite : .white)
                        }
                    }
                    .frame(width: 40, height: 40)
                    .background(Color.black.opacity(0.4), in: Circle())
                }
                .buttonStyle(.plain)
                .disabled(favouriting)
                .padding(8)
                .accessibilityLabel(favourite ? "取消收藏" : "收藏")
            }
    }

    private var info: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(code)
                .font(.title2)
                .fontWeight(.medium)
            // On a line of their own: next to a long code they'd be squeezed.
            VersionBadges(hasChineseSubtitle: detail?.hasChineseSubtitle ?? video.hasChineseSubtitle,
                          isUncensoredLeak: detail?.isUncensoredLeak ?? video.isUncensoredLeak)
                .padding(.top, 6)
            Text(title)
                .foregroundColor(.secondary)
                .lineLimit(4)
                .padding(.top, 8)
            if loading {
                HStack(spacing: 6) {
                    ProgressView().scaleEffect(0.7)
                    Text("正在获取播放地址...").font(.footnote).foregroundColor(.secondary)
                }
                .padding(.top, 8)
            }
            if let error {
                HStack {
                    Text(error).font(.footnote).foregroundColor(.red)
                    Button("重试") { attempt += 1 }
                }
                .padding(.top, 8)
            }
            HStack(spacing: 8) {
                DetailButton(text: "播放", icon: "play.fill", enabled: resolved) { play() }
                DetailButton(text: "下载", icon: "icloud.and.arrow.down", enabled: resolved && !downloading,
                             busy: downloading) { submitRemoteDownload() }
                DetailButton(text: "浏览器", icon: "safari") {
                    if let url = URL(string: video.url) { openURL(url) }
                }
                DetailButton(text: "删除", icon: "trash", enabled: !removing, busy: removing) {
                    confirmRemove = true
                }
            }
            .padding(.top, 16)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }

    // MARK: Actions

    /// The listing already gave title/cover, so the page shows those at once;
    /// this only fetches resolved_url, which play/download wait for.
    private func loadDetail() async {
        guard loadedAttempt != attempt else { return }
        loading = true
        error = nil
        do {
            let result = try await model.api.detail(video.url)
            if Task.isCancelled { return }
            detail = result
        } catch is CancellationError {
            return
        } catch {
            if Task.isCancelled { return }
            self.error = error.localizedDescription
        }
        loadedAttempt = attempt
        loading = false
    }

    private func loadRelated() async {
        guard !relatedLoaded else { return }
        relatedLoading = true
        var result: [BrowseVideo] = []
        do {
            var seen: Set<String> = [video.url]
            result = try await model.api.related(site: relatedSite, pageURL: video.url)
                .filter { seen.insert($0.url).inserted }
        } catch is CancellationError {
            return
        } catch {
            // No recommendations is not worth an error.
        }
        if Task.isCancelled { return }
        related = result
        relatedLoaded = true
        relatedLoading = false
    }

    private func play() {
        guard let detail, let url = URL(string: detail.resolvedUrl) else { return }
        model.playing = PlayItem(url: url, headers: detail.headers, title: title)
    }

    private func submitRemoteDownload() {
        guard let detail else { return }
        Task {
            downloading = true
            // Reuses the resolved_url from /api/detail instead of having the
            // server scrape the page again. Named by id, not title: some
            // titles are too long for a file name.
            let outputName = "\(detail.slug.nonEmpty ?? video.slug.nonEmpty ?? "video").mp4"
            do {
                let result = try await model.api.download(resolvedURL: detail.resolvedUrl, outputName: outputName)
                model.history.add(HistoryEntry(url: video.url, outputName: result.outputName, success: true,
                                               message: "已提交"))
                model.show("已提交远程下载")
            } catch {
                model.history.add(HistoryEntry(url: video.url, outputName: nil, success: false,
                                               message: error.localizedDescription))
                model.show(error.localizedDescription, error: true)
            }
            downloading = false
        }
    }

    private func toggleFavourite() {
        Task {
            favouriting = true
            await model.toggleFavourite(video.url, current: favourite)
            favouriting = false
        }
    }

    private func removeVideo() {
        Task {
            removing = true
            do {
                try await model.api.remove(video.url)
                model.show("已删除")
                model.videoRemoved.send(video.url)
                dismiss()
            } catch {
                model.show(error.localizedDescription, error: true)
            }
            removing = false
        }
    }

    private func submitSelected() {
        let urls = Array(selected)
        Task {
            submitting = true
            let result = await model.submit(urls)
            submitting = false
            selected = []
            model.show(result.message, error: !result.ok)
        }
    }

    private func removeSelected() {
        let urls = Array(selected)
        Task {
            removingSelected = true
            let result = await model.remove(urls)
            removingSelected = false
            model.show(result.message, error: result.removed.count != urls.count)
        }
    }
}

/// Square icon button; *text* is what VoiceOver reads out.
private struct DetailButton: View {
    let text: String
    let icon: String
    var enabled = true
    /// A spinner in place of the icon while its action runs.
    var busy = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack {
                if busy {
                    ProgressView()
                } else {
                    Image(systemName: icon).font(.system(size: 22))
                }
            }
            .frame(width: 48, height: 48)
            .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(Color(.separator)))
        }
        .disabled(!enabled)
        .accessibilityLabel(text)
    }
}
