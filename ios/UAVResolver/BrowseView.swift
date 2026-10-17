import SwiftUI

enum PickerMode: String, Identifiable {
    // 导航 = MissAV's categories; 菜单 / 标签 = JableTV's categories / grouped tags.
    case nav, menu, tags
    var id: String { rawValue }
    var site: String { self == .nav ? "missav" : "jabletv" }
    var title: String {
        switch self {
        case .nav: return "导航"
        case .menu: return "菜单"
        case .tags: return "标签"
        }
    }
}

// Pseudo-sites for the resolver's own lists: stored videos (/api/videos)
// and the favourites (/api/videos?favourite=true).
private let storedSite = "stored"
private let favouriteSite = "favourite"

/// Everything that decides what the grid shows; a change reloads it.
private struct ListKey: Equatable {
    var site: String
    var categoryURL: String?
    var search: String
    var page: Int
    var reload: Int
    var server: String
}

struct BrowseView: View {
    @EnvironmentObject private var model: AppModel

    @State private var site = ""
    @State private var selectedCategory: BrowseCategory?
    @State private var searchQuery = ""
    @State private var activeSearch = ""
    @State private var page = 1
    @State private var videos: [BrowseVideo] = []
    /// Page count of the stored lists; nil for site listings, which don't say.
    @State private var lastPage: Int?
    /// Bumped to reload the current list when its other keys haven't changed.
    @State private var reloadKey = 0
    @State private var scrollToken = 0
    @State private var selected = Set<String>()
    @State private var loading = false
    @State private var error: String?
    @State private var submitting = false
    @State private var removing = false
    @State private var picker: PickerMode?
    @State private var started = false
    @FocusState private var searchFocused: Bool

    private var listKey: ListKey {
        ListKey(site: site, categoryURL: selectedCategory?.url, search: activeSearch, page: page,
                reload: reloadKey, server: model.baseURL + "|" + model.apiKey)
    }

    var body: some View {
        VStack(spacing: 0) {
            if model.configured {
                topBar
                if let error {
                    Text(error)
                        .font(.footnote)
                        .foregroundColor(.red)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 4)
                }
                grid
                pager
                if !selected.isEmpty {
                    SelectionBar(count: selected.count, submitting: submitting, removing: removing,
                                 onSubmit: submitSelected, onRemove: removeSelected)
                }
            } else {
                notConfigured
            }
        }
        .toolbar(.hidden, for: .navigationBar)
        .task(id: listKey) { await loadVideos() }
        .onAppear {
            guard !started else { return }
            started = true
            if model.configured { openStored() } else { model.path.append(.settings) }
        }
        .onChange(of: model.configured) { configured in
            // First launch: back from 设置 with the server filled in.
            if configured && site.isEmpty { openStored() }
        }
        .sheet(item: $picker) { mode in
            CategoryPickerView(mode: mode) { category in
                site = mode.site
                selectedCategory = category
                activeSearch = ""
                searchQuery = ""
                page = 1
                reloadKey += 1
            }
            .environmentObject(model)
        }
        .onReceive(model.favouriteChanged) { change in
            applyFavourite(change.url, change.favourite)
        }
        .onReceive(model.videoRemoved) { url in
            videos.removeAll { $0.url == url }
            selected.remove(url)
        }
    }

    // MARK: Parts

    private var notConfigured: some View {
        VStack(spacing: 16) {
            Spacer()
            Text("请先在设置里填写解析服务地址和 API Key")
                .foregroundColor(.red)
            Button("设置") { model.path.append(.settings) }
                .buttonStyle(.borderedProminent)
            Spacer()
        }
        .padding(16)
    }

    private var topBar: some View {
        HStack(spacing: 8) {
            HStack(spacing: 6) {
                TextField("关键字", text: $searchQuery)
                    .font(.subheadline)
                    // Video codes are Latin/digits.
                    .keyboardType(.asciiCapable)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .submitLabel(.search)
                    .focused($searchFocused)
                    .onSubmit(submitSearch)
                    .padding(.horizontal, 12)
                // Jable's button is left out to make room for the stored list;
                // its categories are still under the menu (菜单/标签).
                barButton("Manko") { openStored() }
                barButton("Miss") {
                    if searchQuery.trimmed.isEmpty { openSiteHome("missav") } else { searchSite("missav") }
                }
            }
            .padding(5)
            .frame(height: 44)
            .background(Color(.systemGray6), in: RoundedRectangle(cornerRadius: 8))

            Menu {
                Button("导航") { picker = .nav }
                Button("菜单") { picker = .menu }
                Button("标签") { picker = .tags }
                Button("我的收藏") { openFavourites() }
                Button("解析下载") { model.path.append(.parse) }
                Button("设置") { model.path.append(.settings) }
            } label: {
                Image(systemName: "line.3.horizontal")
                    .font(.title3)
                    .foregroundColor(.primary)
                    .frame(width: 32, height: 44)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 4)
    }

    private func barButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button {
            searchFocused = false
            action()
        } label: {
            Text(title)
                .font(.subheadline)
                .foregroundColor(.white)
                .padding(.horizontal, 10)
                .frame(maxHeight: .infinity)
                .background(Color(white: 0.38), in: RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
    }

    private var grid: some View {
        ScrollViewReader { proxy in
            ScrollView {
                Color.clear.frame(height: 0).id("top")
                LazyVGrid(columns: videoGridColumns, spacing: 2) {
                    ForEach(videos) { video in
                        VideoCard(
                            video: video,
                            thumbURL: model.api.thumbURL(video.thumbnail),
                            isSelected: selected.contains(video.url),
                            onToggle: { toggleSelection(video.url) },
                            onOpen: { model.path.append(.detail(video)) },
                            onToggleFavourite: {
                                Task { await model.toggleFavourite(video.url, current: video.favourite) }
                            })
                    }
                }
                .padding(1)
            }
            .overlay {
                if loading && videos.isEmpty { ProgressView() }
            }
            .scrollDismissesKeyboard(.immediately)
            .onChange(of: scrollToken) { _ in proxy.scrollTo("top", anchor: .top) }
        }
    }

    private var pager: some View {
        HStack(spacing: 12) {
            Button { page -= 1 } label: { Image(systemName: "chevron.left").frame(width: 32, height: 32) }
                .disabled(page <= 1 || loading)
            HStack(spacing: 6) {
                Text("第 \(page) 页").font(.footnote)
                if loading { ProgressView().scaleEffect(0.7) }
            }
            Button { page += 1 } label: { Image(systemName: "chevron.right").frame(width: 32, height: 32) }
                .disabled(loading || videos.isEmpty || lastPage.map { page >= $0 } ?? false)
        }
        .padding(.vertical, 2)
    }

    // MARK: Actions

    private func loadVideos() async {
        let isStored = site == storedSite || site == favouriteSite
        guard model.configured, !site.isEmpty else { return }
        if !isStored && activeSearch.isEmpty && selectedCategory == nil { return }
        loading = true
        error = nil
        selected = []
        lastPage = nil
        do {
            let result: [BrowseVideo]
            var pages: Int?
            if isStored {
                let stored = try await model.api.stored(page: page, search: activeSearch,
                                                        favourite: site == favouriteSite)
                result = stored.videos
                pages = stored.pages
            } else if !activeSearch.isEmpty {
                result = try await model.api.search(site: site, query: activeSearch, page: page)
            } else {
                result = try await model.api.videos(site: site, categoryURL: selectedCategory?.url ?? "",
                                                    page: page)
            }
            // A newer list was asked for meanwhile; that one's task shows it.
            if Task.isCancelled { return }
            videos = result
            lastPage = pages
            scrollToken += 1
        } catch is CancellationError {
            return
        } catch {
            if Task.isCancelled { return }
            self.error = error.localizedDescription
            videos = []
        }
        loading = false
    }

    private func openSiteHome(_ target: String) {
        Task {
            loading = true
            error = nil
            do {
                let categories = try await model.categories(for: target)
                site = target
                selectedCategory = categories.first
                activeSearch = ""
                searchQuery = ""
                page = 1
                reloadKey += 1
            } catch {
                self.error = error.localizedDescription
                loading = false
            }
        }
    }

    /// With a keyword in the bar, searches the stored list instead.
    private func openStored() {
        site = storedSite
        selectedCategory = nil
        activeSearch = searchQuery.trimmed
        page = 1
        reloadKey += 1
    }

    private func openFavourites() {
        site = favouriteSite
        selectedCategory = nil
        activeSearch = searchQuery.trimmed
        page = 1
        reloadKey += 1
    }

    private func searchSite(_ target: String) {
        site = target
        selectedCategory = nil
        activeSearch = searchQuery.trimmed
        page = 1
        reloadKey += 1
    }

    /// The keyboard's search key: searches whatever list is showing.
    private func submitSearch() {
        if site == storedSite || site == favouriteSite {
            activeSearch = searchQuery.trimmed
            page = 1
            reloadKey += 1
        } else if searchQuery.trimmed.isEmpty {
            openSiteHome("missav")
        } else {
            searchSite("missav")
        }
    }

    private func toggleSelection(_ url: String) {
        if selected.contains(url) { selected.remove(url) } else { selected.insert(url) }
    }

    /// A video taken out of the favourites leaves the favourites list.
    private func applyFavourite(_ url: String, _ favourite: Bool) {
        if site == favouriteSite && !favourite {
            videos.removeAll { $0.url == url }
            selected.remove(url)
        } else if let index = videos.firstIndex(where: { $0.url == url }) {
            videos[index].favourite = favourite
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
            removing = true
            let result = await model.remove(urls)
            removing = false
            model.show(result.message, error: result.removed.count != urls.count)
        }
    }
}

struct CategoryPickerView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let mode: PickerMode
    let onSelect: (BrowseCategory) -> Void

    @State private var categories: [BrowseCategory]?
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Group {
                if let error {
                    Text(error).foregroundColor(.red).padding()
                } else if let categories {
                    list(categories)
                } else {
                    ProgressView()
                }
            }
            .navigationTitle(mode.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
            }
        }
        .task {
            do {
                categories = try await model.categories(for: mode.site)
            } catch is CancellationError {
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    @ViewBuilder
    private func list(_ all: [BrowseCategory]) -> some View {
        switch mode {
        case .nav:
            List { rows(all) }
        case .menu:
            List { rows(all.filter { $0.group.isEmpty && !$0.section }) }
        case .tags:
            List {
                ForEach(Array(groups(all).enumerated()), id: \.offset) { _, group in
                    Section(group.name) { rows(group.items) }
                }
            }
        }
    }

    private func rows(_ items: [BrowseCategory]) -> some View {
        ForEach(Array(items.enumerated()), id: \.offset) { _, category in
            Button {
                onSelect(category)
                dismiss()
            } label: {
                HStack {
                    Text(category.name).foregroundColor(.primary)
                    Spacer()
                    if category.count > 0 {
                        Text("\(category.count)").font(.footnote).foregroundColor(.secondary)
                    }
                }
            }
        }
    }

    /// Tags in page order, grouped as JableTV's sidebar groups them.
    private func groups(_ all: [BrowseCategory]) -> [(name: String, items: [BrowseCategory])] {
        var result: [(name: String, items: [BrowseCategory])] = []
        for category in all where !category.group.isEmpty {
            if result.last?.name == category.group {
                result[result.count - 1].items.append(category)
            } else {
                result.append((category.group, [category]))
            }
        }
        return result
    }
}
