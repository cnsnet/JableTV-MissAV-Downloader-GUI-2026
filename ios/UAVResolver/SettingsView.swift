import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel

    @State private var baseURL = ""
    @State private var apiKey = ""
    @State private var showKey = false
    @State private var refreshing = false

    var body: some View {
        Form {
            Section("解析服务") {
                TextField("地址，如 http://192.168.1.10:8060", text: $baseURL)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                HStack {
                    // Hidden by default: a secure field also stops iOS smart
                    // punctuation turning "-" in the key into "—".
                    Group {
                        if showKey {
                            TextField("API Key", text: $apiKey)
                        } else {
                            SecureField("API Key", text: $apiKey)
                        }
                    }
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    Button { showKey.toggle() } label: {
                        Image(systemName: showKey ? "eye.slash" : "eye")
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel(showKey ? "隐藏" : "显示")
                }
            }
            Section {
                Button("保存", action: save)
                    .disabled(baseURL.trimmed.isEmpty || apiKey.trimmed.isEmpty)
            }
            Section {
                Button(refreshing ? "刷新中..." : "刷新分类", action: refreshCategories)
                    .disabled(refreshing || !model.configured)
            } footer: {
                Text("分类列表会缓存在本机，站点分类有变化时再刷新。")
            }
        }
        .navigationTitle("设置")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear {
            baseURL = model.baseURL
            apiKey = model.apiKey
        }
    }

    private func save() {
        model.baseURL = baseURL.trimmed
        model.apiKey = apiKey.trimmed
        if !model.path.isEmpty { model.path.removeLast() }
    }

    private func refreshCategories() {
        Task {
            refreshing = true
            var fail = 0
            for site in ["jabletv", "missav"] {
                do {
                    _ = try await model.categories(for: site, refresh: true)
                } catch {
                    fail += 1
                }
            }
            refreshing = false
            model.show(fail == 0 ? "分类缓存已刷新" : "刷新完成，\(fail) 个站点失败", error: fail > 0)
        }
    }
}
