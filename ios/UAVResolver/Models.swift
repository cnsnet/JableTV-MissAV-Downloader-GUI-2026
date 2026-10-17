import Foundation

/// A listing card: /api/browse/* and /api/videos.
struct BrowseVideo: Identifiable, Hashable, Decodable {
    var url: String
    var title: String
    var thumbnail: String
    var duration: String
    /// The page's id (URL slug) as the resolver stored it; may be empty.
    var slug: String
    /// MissAV's 中文字幕/无码影片 flags; nil when not known (e.g. JableTV).
    var hasChineseSubtitle: Bool?
    var isUncensoredLeak: Bool?
    var favourite: Bool

    var id: String { url }

    enum CodingKeys: String, CodingKey {
        case url, title, thumbnail, duration, favourite
        case slug = "id"
        case hasChineseSubtitle = "has_chinese_subtitle"
        case isUncensoredLeak = "is_uncensored_leak"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        url = (try? c.decodeIfPresent(String.self, forKey: .url)) ?? ""
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? ""
        thumbnail = (try? c.decodeIfPresent(String.self, forKey: .thumbnail)) ?? ""
        duration = (try? c.decodeIfPresent(String.self, forKey: .duration)) ?? ""
        slug = (try? c.decodeIfPresent(String.self, forKey: .slug)) ?? ""
        hasChineseSubtitle = try? c.decodeIfPresent(Bool.self, forKey: .hasChineseSubtitle)
        isUncensoredLeak = try? c.decodeIfPresent(Bool.self, forKey: .isUncensoredLeak)
        favourite = (try? c.decodeIfPresent(Bool.self, forKey: .favourite)) ?? false
    }
}

/// /api/detail.
struct VideoDetail: Decodable {
    var title: String
    var slug: String
    var description: String
    var thumbnail: String
    var resolvedUrl: String
    /// Referer/Origin the CDN wants.
    var headers: [String: String]
    var hasChineseSubtitle: Bool?
    var isUncensoredLeak: Bool?

    enum CodingKeys: String, CodingKey {
        case title, description, thumbnail, headers
        case slug = "id"
        case resolvedUrl = "resolved_url"
        case hasChineseSubtitle = "has_chinese_subtitle"
        case isUncensoredLeak = "is_uncensored_leak"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? ""
        slug = (try? c.decodeIfPresent(String.self, forKey: .slug)) ?? ""
        description = (try? c.decodeIfPresent(String.self, forKey: .description)) ?? ""
        thumbnail = (try? c.decodeIfPresent(String.self, forKey: .thumbnail)) ?? ""
        resolvedUrl = (try? c.decodeIfPresent(String.self, forKey: .resolvedUrl)) ?? ""
        headers = (try? c.decodeIfPresent([String: String].self, forKey: .headers)) ?? [:]
        hasChineseSubtitle = try? c.decodeIfPresent(Bool.self, forKey: .hasChineseSubtitle)
        isUncensoredLeak = try? c.decodeIfPresent(Bool.self, forKey: .isUncensoredLeak)
    }
}

struct BrowseCategory: Hashable, Codable {
    var name: String
    var url: String
    var count: Int
    var section: Bool
    var group: String

    enum CodingKeys: String, CodingKey { case name, url, count, section, group }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? ""
        url = (try? c.decodeIfPresent(String.self, forKey: .url)) ?? ""
        // Some scrapers give the count as a string.
        count = (try? c.decodeIfPresent(Int.self, forKey: .count))
            ?? Int((try? c.decodeIfPresent(String.self, forKey: .count)) ?? "") ?? 0
        section = (try? c.decodeIfPresent(Bool.self, forKey: .section)) ?? false
        group = (try? c.decodeIfPresent(String.self, forKey: .group)) ?? ""
    }
}

struct VideoListResponse: Decodable {
    var videos: [BrowseVideo]
    /// Page count; only /api/videos says.
    var pages: Int?
}

struct CategoryListResponse: Decodable {
    var categories: [BrowseCategory]
}

struct ResolveResponse: Decodable {
    var outputName: String
    var resolvedUrl: String

    enum CodingKeys: String, CodingKey {
        case outputName = "output_name"
        case resolvedUrl = "resolved_url"
    }
}

struct HistoryEntry: Codable, Identifiable {
    var id = UUID()
    var url: String
    var outputName: String?
    var success: Bool
    var message: String
    var timestamp = Date()
}

/// What the player opens.
struct PlayItem: Identifiable {
    let id = UUID()
    let url: URL
    let headers: [String: String]
    let title: String
}

enum Route: Hashable {
    case detail(BrowseVideo)
    case parse
    case settings
}

extension String {
    var trimmed: String { trimmingCharacters(in: .whitespacesAndNewlines) }
    var nonEmpty: String? { isEmpty ? nil : self }
}

/// "[h:]mm:ss" like the Android player.
func formatTime(_ seconds: Double) -> String {
    guard seconds.isFinite, seconds > 0 else { return "0:00" }
    let total = Int(seconds)
    let h = total / 3600, m = total % 3600 / 60, s = total % 60
    return h > 0 ? String(format: "%d:%02d:%02d", h, m, s) : String(format: "%d:%02d", m, s)
}
