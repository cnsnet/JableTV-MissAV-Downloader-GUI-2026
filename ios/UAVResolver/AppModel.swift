import Combine
import SwiftUI

struct Toast: Identifiable, Equatable {
    let id = UUID()
    let message: String
    let isError: Bool
}

/// App-wide state: settings, navigation, the toast, and the actions every
/// screen shares. A favourite or delete done anywhere is broadcast so every
/// list on the navigation stack updates (the Android app passes callbacks).
@MainActor
final class AppModel: ObservableObject {
    @Published var baseURL: String {
        didSet { UserDefaults.standard.set(baseURL, forKey: "baseUrl") }
    }
    @Published var apiKey: String {
        didSet { UserDefaults.standard.set(apiKey, forKey: "apiKey") }
    }
    @Published var path: [Route] = []
    @Published var playing: PlayItem?
    @Published var toast: Toast?

    let favouriteChanged = PassthroughSubject<(url: String, favourite: Bool), Never>()
    let videoRemoved = PassthroughSubject<String, Never>()

    let history = HistoryStore()
    private let categoryCache = CategoryCache()
    private var categoryMemory: [String: [BrowseCategory]] = [:]

    init() {
        baseURL = UserDefaults.standard.string(forKey: "baseUrl") ?? ""
        apiKey = UserDefaults.standard.string(forKey: "apiKey") ?? ""
    }

    var api: ResolverAPI { ResolverAPI(baseURL: baseURL, apiKey: apiKey) }
    var configured: Bool { !baseURL.isEmpty && !apiKey.isEmpty }

    func show(_ message: String, error: Bool = false) {
        withAnimation { toast = Toast(message: message, isError: error) }
    }

    /// Categories rarely change: memory, then disk, then the resolver.
    func categories(for site: String, refresh: Bool = false) async throws -> [BrowseCategory] {
        if !refresh {
            if let cached = categoryMemory[site], !cached.isEmpty { return cached }
            if let cached = categoryCache.get(site), !cached.isEmpty {
                categoryMemory[site] = cached
                return cached
            }
        }
        let fresh = try await api.categories(site: site, refresh: refresh)
        if !fresh.isEmpty {
            categoryCache.put(site, fresh)
            categoryMemory[site] = fresh
        }
        return fresh
    }

    @discardableResult
    func toggleFavourite(_ url: String, current: Bool) async -> Bool {
        do {
            try await api.setFavourite(url, !current)
            favouriteChanged.send((url, !current))
            show(current ? "已取消收藏" : "已收藏")
            return true
        } catch {
            show(error.localizedDescription, error: true)
            return false
        }
    }

    /// Deletes each url on the server; returns the ones deleted and the summary.
    func remove(_ urls: [String]) async -> (removed: Set<String>, message: String) {
        var removed = Set<String>()
        var lastError = ""
        for url in urls {
            do {
                try await api.remove(url)
                removed.insert(url)
            } catch {
                lastError = error.localizedDescription
            }
        }
        removed.forEach { videoRemoved.send($0) }
        let fail = urls.count - removed.count
        let message = fail == 0 ? "已删除 \(removed.count) 个"
            : "已删除 \(removed.count) 个，失败 \(fail) 个：\(lastError)"
        return (removed, message)
    }

    /// Submits each url for remote download; returns the summary and whether all succeeded.
    func submit(_ urls: [String]) async -> (message: String, ok: Bool) {
        var ok = 0, fail = 0
        for url in urls {
            do {
                let result = try await api.resolve(url)
                ok += 1
                history.add(HistoryEntry(url: url, outputName: result.outputName, success: true, message: "已提交"))
            } catch {
                fail += 1
                history.add(HistoryEntry(url: url, outputName: nil, success: false,
                                         message: error.localizedDescription))
            }
        }
        return (fail == 0 ? "已提交 \(ok) 个" : "已提交 \(ok) 个，失败 \(fail) 个", fail == 0)
    }
}

/// Download history of 解析下载 and the remote downloads, newest first.
final class HistoryStore {
    private let key = "history"
    private let maxEntries = 100

    func all() -> [HistoryEntry] {
        guard let data = UserDefaults.standard.data(forKey: key) else { return [] }
        return (try? JSONDecoder().decode([HistoryEntry].self, from: data)) ?? []
    }

    func add(_ entry: HistoryEntry) {
        save(Array(([entry] + all()).prefix(maxEntries)))
    }

    func clear() {
        UserDefaults.standard.removeObject(forKey: key)
    }

    private func save(_ entries: [HistoryEntry]) {
        if let data = try? JSONEncoder().encode(entries) {
            UserDefaults.standard.set(data, forKey: key)
        }
    }
}

/// Category lists per site, kept until 设置 → 刷新分类.
final class CategoryCache {
    private func key(_ site: String) -> String { "categories.\(site)" }

    func get(_ site: String) -> [BrowseCategory]? {
        guard let data = UserDefaults.standard.data(forKey: key(site)) else { return nil }
        return try? JSONDecoder().decode([BrowseCategory].self, from: data)
    }

    func put(_ site: String, _ categories: [BrowseCategory]) {
        if let data = try? JSONEncoder().encode(categories) {
            UserDefaults.standard.set(data, forKey: key(site))
        }
    }
}
