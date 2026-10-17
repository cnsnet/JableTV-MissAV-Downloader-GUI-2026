import SwiftUI

/// 解析下载: resolve a pasted page URL, or submit it for remote download.
struct ParseView: View {
    @EnvironmentObject private var model: AppModel

    @State private var urlText = ""
    @State private var status: String?
    @State private var statusIsError = false
    @State private var resolving = false
    @State private var downloading = false
    @State private var history: [HistoryEntry] = []
    // The last successful 解析 and its URL: 下载 of that same URL reuses it
    // instead of having the resolver scrape the page again.
    @State private var resolvedDetail: VideoDetail?
    @State private var resolvedFor = ""

    private static let dateFormat: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "MM-dd HH:mm"
        return formatter
    }()

    var body: some View {
        List {
            Section {
                HStack {
                    TextField("视频网址", text: $urlText)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    // Reads the clipboard without the system's paste prompt.
                    PasteButton(payloadType: String.self) { strings in
                        if let text = strings.first { urlText = Self.firstURL(in: text) }
                    }
                    .labelStyle(.iconOnly)
                    .buttonBorderShape(.capsule)
                }
                HStack(spacing: 8) {
                    Button(resolving ? "解析中..." : "解析", action: resolveOnly)
                        .buttonStyle(.bordered)
                        .frame(maxWidth: .infinity)
                    Button(downloading ? "下载中..." : "下载", action: download)
                        .buttonStyle(.borderedProminent)
                        .frame(maxWidth: .infinity)
                }
                .disabled(resolving || downloading)
                if let status {
                    Text(status)
                        .font(.footnote)
                        .foregroundColor(statusIsError ? .red : .accentColor)
                        .textSelection(.enabled)
                }
            }

            Section {
                ForEach(history) { entry in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(entry.outputName ?? entry.url).fontWeight(.medium)
                        Text(entry.url).font(.caption).foregroundColor(.secondary)
                        Text("\(Self.dateFormat.string(from: entry.timestamp)) · "
                             + (entry.success ? "成功" : "失败：\(entry.message)"))
                            .font(.caption)
                            .foregroundColor(entry.success ? .accentColor : .red)
                    }
                }
            } header: {
                HStack {
                    Text("历史记录")
                    Spacer()
                    if !history.isEmpty {
                        Button {
                            model.history.clear()
                            history = []
                        } label: {
                            Image(systemName: "trash")
                        }
                        .accessibilityLabel("清除历史记录")
                    }
                }
            }
        }
        .navigationTitle("解析下载")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { history = model.history.all() }
    }

    /// The URL in shared text like "title https://...", else the text.
    private static func firstURL(in text: String) -> String {
        let pattern = try? NSRegularExpression(pattern: #"https?://\S+"#)
        let range = NSRange(text.startIndex..., in: text)
        if let match = pattern?.firstMatch(in: text, range: range), let r = Range(match.range, in: text) {
            return String(text[r])
        }
        return text.trimmed
    }

    private func check(_ url: String) -> Bool {
        if url.isEmpty {
            status = "请输入或粘贴一个视频网址"
            statusIsError = true
            return false
        }
        return true
    }

    private func resolveOnly() {
        let url = urlText.trimmed
        guard check(url) else { return }
        resolving = true
        status = nil
        Task {
            do {
                let detail = try await model.api.detail(url)
                statusIsError = false
                status = "解析成功：\n\(detail.title)\n\(detail.resolvedUrl)"
                resolvedDetail = detail
                resolvedFor = url
            } catch {
                statusIsError = true
                status = "解析失败：\(error.localizedDescription)"
                resolvedDetail = nil
                resolvedFor = ""
            }
            resolving = false
        }
    }

    private func download() {
        let url = urlText.trimmed
        guard check(url) else { return }
        downloading = true
        status = nil
        Task {
            do {
                let result: ResolveResponse
                if let cached = resolvedDetail, resolvedFor == url {
                    result = try await model.api.download(
                        resolvedURL: cached.resolvedUrl, outputName: "\(cached.slug.nonEmpty ?? "video").mp4")
                } else {
                    result = try await model.api.resolve(url)
                }
                statusIsError = false
                status = "已提交远端下载：\(result.outputName)"
                model.history.add(HistoryEntry(url: url, outputName: result.outputName, success: true, message: "已提交"))
            } catch {
                statusIsError = true
                status = "失败：\(error.localizedDescription)"
                model.history.add(HistoryEntry(url: url, outputName: nil, success: false,
                                               message: error.localizedDescription))
            }
            downloading = false
            resolvedDetail = nil
            resolvedFor = ""
            urlText = ""
            history = model.history.all()
        }
    }
}
