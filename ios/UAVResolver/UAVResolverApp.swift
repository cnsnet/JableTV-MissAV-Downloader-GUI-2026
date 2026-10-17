import AVFoundation
import SwiftUI

@main
struct UAVResolverApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel()

    init() {
        // Covers come from the resolver's thumbnail proxy; keep plenty on disk.
        URLCache.shared = URLCache(memoryCapacity: 32 << 20, diskCapacity: 256 << 20)
        // Plays with the silent switch on, like any video app.
        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .moviePlayback)
    }

    var body: some Scene {
        WindowGroup {
            RootView().environmentObject(model)
        }
    }
}

final class AppDelegate: NSObject, UIApplicationDelegate {
    /// Portrait everywhere except the player, which turns it to landscape.
    static var orientationMask: UIInterfaceOrientationMask = .portrait

    func application(_ application: UIApplication,
                     supportedInterfaceOrientationsFor window: UIWindow?) -> UIInterfaceOrientationMask {
        Self.orientationMask
    }
}

enum OrientationLock {
    @MainActor
    static func set(_ mask: UIInterfaceOrientationMask) {
        AppDelegate.orientationMask = mask
        for scene in UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }) {
            var controller = scene.keyWindow?.rootViewController
            while let current = controller {
                current.setNeedsUpdateOfSupportedInterfaceOrientations()
                controller = current.presentedViewController
            }
            scene.requestGeometryUpdate(.iOS(interfaceOrientations: mask)) { _ in }
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        NavigationStack(path: $model.path) {
            BrowseView()
                .navigationDestination(for: Route.self) { route in
                    switch route {
                    case .detail(let video): DetailView(video: video)
                    case .parse: ParseView()
                    case .settings: SettingsView()
                    }
                }
        }
        .overlay(alignment: .bottom) {
            if let toast = model.toast {
                Text(toast.message)
                    .font(.subheadline)
                    .foregroundColor(.white)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 12)
                    .background(toast.isError ? Color(red: 0.55, green: 0.11, blue: 0.09) : Color(white: 0.2),
                                in: RoundedRectangle(cornerRadius: 8))
                    .padding(.horizontal, 16)
                    .padding(.bottom, 72)
                    .transition(.opacity)
                    .allowsHitTesting(false)
            }
        }
        .task(id: model.toast?.id) {
            guard model.toast != nil else { return }
            try? await Task.sleep(nanoseconds: 2_600_000_000)
            if !Task.isCancelled { withAnimation { model.toast = nil } }
        }
        .fullScreenCover(item: $model.playing) { item in
            PlayerScreen(item: item)
        }
    }
}
