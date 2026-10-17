import Foundation

struct APIError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

private let unreserved = CharacterSet(
    charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")

/// Query-string escaping strict enough for URLs passed as values (& = + ? /).
func percentEncode(_ s: String) -> String {
    s.addingPercentEncoding(withAllowedCharacters: unreserved) ?? s
}

/// The resolver's HTTP API (docs/resolver-api.md).
struct ResolverAPI {
    let baseURL: String
    let apiKey: String

    private static let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        return URLSession(configuration: config)
    }()

    func url(_ path: String, _ query: [(String, String)] = []) -> URL? {
        var s = baseURL.trimmed
        while s.hasSuffix("/") { s.removeLast() }
        s += path
        if !query.isEmpty {
            s += "?" + query.map { "\($0.0)=\(percentEncode($0.1))" }.joined(separator: "&")
        }
        guard let url = URL(string: s), url.scheme != nil, url.host != nil else { return nil }
        return url
    }

    /// Image loaders can't add headers, so the key goes in the URL here.
    func thumbURL(_ original: String) -> URL? {
        original.isEmpty ? nil : url("/api/browse/thumb", [("url", original), ("api_key", apiKey)])
    }

    private func send(_ method: String, _ path: String, query: [(String, String)] = [],
                      json: [String: Any]? = nil) async throws -> Data {
        guard let url = url(path, query) else { throw APIError(message: "解析服务地址无效") }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue(apiKey, forHTTPHeaderField: "X-API-Key")
        if let json {
            request.httpBody = try JSONSerialization.data(withJSONObject: json)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await Self.session.data(for: request)
        } catch let error as URLError where error.code == .cancelled {
            throw CancellationError()
        } catch {
            throw APIError(message: error.localizedDescription)
        }
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            throw APIError(message: "HTTP \(code): \(Self.detail(data, code))")
        }
        return data
    }

    private static func detail(_ data: Data, _ code: Int) -> String {
        if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let detail = obj["detail"] {
            return (detail as? String) ?? "\(detail)"
        }
        let text = String(data: data, encoding: .utf8) ?? ""
        return text.isEmpty ? "HTTP \(code)" : text
    }

    private func decode<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError(message: "响应解析失败：\(error.localizedDescription)")
        }
    }

    private func get<T: Decodable>(_ type: T.Type, _ path: String,
                                   _ query: [(String, String)] = []) async throws -> T {
        try decode(T.self, try await send("GET", path, query: query))
    }

    // MARK: Endpoints

    /// Scrape + submit to the remote download queue.
    func resolve(_ pageURL: String) async throws -> ResolveResponse {
        try decode(ResolveResponse.self, try await send("POST", "/api/resolve", json: ["url": pageURL]))
    }

    /// Submit an already-resolved URL, skipping the scrape.
    func download(resolvedURL: String, outputName: String) async throws -> ResolveResponse {
        try decode(ResolveResponse.self, try await send(
            "POST", "/api/download", json: ["resolved_url": resolvedURL, "output_name": outputName]))
    }

    func detail(_ pageURL: String) async throws -> VideoDetail {
        try await get(VideoDetail.self, "/api/detail", [("url", pageURL)])
    }

    func categories(site: String, refresh: Bool = false) async throws -> [BrowseCategory] {
        try await get(CategoryListResponse.self, "/api/browse/\(site)/categories",
                      refresh ? [("refresh", "true")] : []).categories
    }

    func videos(site: String, categoryURL: String, page: Int) async throws -> [BrowseVideo] {
        try await get(VideoListResponse.self, "/api/browse/\(site)/videos",
                      [("category_url", categoryURL), ("page", String(page))]).videos
    }

    func search(site: String, query: String, page: Int) async throws -> [BrowseVideo] {
        try await get(VideoListResponse.self, "/api/browse/\(site)/search",
                      [("q", query), ("page", String(page))]).videos
    }

    func related(site: String, pageURL: String) async throws -> [BrowseVideo] {
        try await get(VideoListResponse.self, "/api/browse/\(site)/related", [("url", pageURL)]).videos
    }

    /// The resolver's stored videos (or only the favourites), newest first;
    /// a search fuzzy-matches the code or title.
    func stored(page: Int, search: String, favourite: Bool) async throws -> (videos: [BrowseVideo], pages: Int) {
        var query = [("page", String(page))]
        if !search.isEmpty { query.append(("search", search)) }
        if favourite { query.append(("favourite", "true")) }
        let result = try await get(VideoListResponse.self, "/api/videos", query)
        return (result.videos, result.pages ?? 0)
    }

    /// Soft-delete: hidden from every listing afterwards.
    func remove(_ pageURL: String) async throws {
        _ = try await send("DELETE", "/api/videos", query: [("url", pageURL)])
    }

    func setFavourite(_ pageURL: String, _ favourite: Bool) async throws {
        _ = try await send(favourite ? "PUT" : "DELETE", "/api/favourites", query: [("url", pageURL)])
    }
}
