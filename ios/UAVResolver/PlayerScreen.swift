import AVFoundation
import Combine
import MediaPlayer
import SwiftUI

/// A swipe across the whole screen width seeks this far, as on Android.
private let fullSwipeSeekSeconds = 20.0 * 60

@MainActor
final class PlayerModel: ObservableObject {
    let player: AVPlayer
    /// A hidden system volume view: the only way to set the volume, and while
    /// it's on screen iOS doesn't show its own volume overlay.
    let volumeView = MPVolumeView(frame: CGRect(x: -1000, y: -1000, width: 1, height: 1))

    @Published var isPlaying = false
    /// Playing or waiting to play: the screen stays on.
    @Published var isActive = true
    @Published var position: Double = 0
    @Published var duration: Double = 0
    @Published var failure: String?

    private var timeObserver: Any?
    private var cancellables = Set<AnyCancellable>()

    init(item: PlayItem) {
        // The CDN wants the Referer/Origin /api/detail returned, on the
        // playlists and every segment.
        let asset = AVURLAsset(url: item.url, options: ["AVURLAssetHTTPHeaderFieldsKey": item.headers])
        let playerItem = AVPlayerItem(asset: asset)
        player = AVPlayer(playerItem: playerItem)

        timeObserver = player.addPeriodicTimeObserver(
            forInterval: CMTime(seconds: 0.5, preferredTimescale: 600), queue: .main
        ) { [weak self] time in
            Task { @MainActor in self?.tick(time) }
        }
        player.publisher(for: \.timeControlStatus)
            .receive(on: RunLoop.main)
            .sink { [weak self] status in
                self?.isPlaying = status == .playing
                self?.isActive = status != .paused
            }
            .store(in: &cancellables)
        playerItem.publisher(for: \.status)
            .receive(on: RunLoop.main)
            .sink { [weak self] status in
                if status == .failed {
                    self?.failure = "播放失败：" + (playerItem.error?.localizedDescription ?? "未知错误")
                }
            }
            .store(in: &cancellables)

        try? AVAudioSession.sharedInstance().setActive(true)
        player.play()
    }

    private func tick(_ time: CMTime) {
        if time.seconds.isFinite { position = time.seconds }
        let total = player.currentItem?.duration.seconds ?? 0
        duration = total.isFinite ? total : 0
    }

    func seek(to seconds: Double) {
        let upper = duration > 0 ? duration : seconds
        let target = max(0, min(seconds, upper))
        position = target
        player.seek(to: CMTime(seconds: target, preferredTimescale: 600),
                    toleranceBefore: .zero, toleranceAfter: .zero)
    }

    func togglePlay() {
        if isPlaying { player.pause() } else { player.play() }
    }

    func stop() {
        player.pause()
        if let timeObserver { player.removeTimeObserver(timeObserver) }
        timeObserver = nil
    }

    var volume: Float { AVAudioSession.sharedInstance().outputVolume }

    func setVolume(_ value: Float) {
        let slider = volumeView.subviews.compactMap { $0 as? UISlider }.first
        slider?.value = value
    }
}

@MainActor
private var currentScreen: UIScreen? {
    (UIApplication.shared.connectedScenes.first as? UIWindowScene)?.screen
}

private struct PlayerLayerView: UIViewRepresentable {
    let player: AVPlayer

    final class LayerView: UIView {
        override class var layerClass: AnyClass { AVPlayerLayer.self }
        var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }
    }

    func makeUIView(context: Context) -> LayerView {
        let view = LayerView()
        view.backgroundColor = .black
        view.playerLayer.player = player
        view.playerLayer.videoGravity = .resizeAspect
        return view
    }

    func updateUIView(_ uiView: LayerView, context: Context) {}
}

private struct HostedView: UIViewRepresentable {
    let view: UIView
    func makeUIView(context: Context) -> UIView { view }
    func updateUIView(_ uiView: UIView, context: Context) {}
}

/// Landscape, full screen, own controls: horizontal swipe seeks (the whole
/// width = 20 min), vertical swipe on the left half sets the brightness and
/// on the right half the volume, a tap shows/hides the controls.
struct PlayerScreen: View {
    let item: PlayItem
    @StateObject private var model: PlayerModel
    @Environment(\.dismiss) private var dismiss

    private enum DragMode { case seek, brightness, volume }

    @State private var controlsVisible = true
    /// Bumped on every interaction; the controls hide 3 s after the last one.
    @State private var interaction = 0
    @State private var dragMode: DragMode?
    @State private var dragStart: Double = 0
    @State private var seekPreview: Double?
    @State private var levelValue: Double = 0
    @State private var sliderValue: Double?
    @State private var originalBrightness: CGFloat?

    init(item: PlayItem) {
        self.item = item
        _model = StateObject(wrappedValue: PlayerModel(item: item))
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            PlayerLayerView(player: model.player).ignoresSafeArea()
            HostedView(view: model.volumeView).frame(width: 1, height: 1).opacity(0.01)
            gestureLayer
            overlays
            if controlsVisible { controls }
        }
        .statusBarHidden()
        .persistentSystemOverlays(.hidden)
        .onAppear {
            OrientationLock.set(.landscape)
            originalBrightness = currentScreen?.brightness
            UIApplication.shared.isIdleTimerDisabled = true
            wake()
        }
        .onDisappear {
            model.stop()
            UIApplication.shared.isIdleTimerDisabled = false
            // A brightness set by swiping only lasts while the player is open.
            if let originalBrightness { currentScreen?.brightness = originalBrightness }
            OrientationLock.set(.portrait)
        }
        .onChange(of: model.isActive) { active in
            // Paused or ended, the screen may time out as usual.
            UIApplication.shared.isIdleTimerDisabled = active
        }
        .task(id: "\(interaction)-\(model.isPlaying)") {
            guard model.isPlaying else { return }
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            if !Task.isCancelled && model.isPlaying && sliderValue == nil {
                withAnimation(.easeOut(duration: 0.2)) { controlsVisible = false }
            }
        }
    }

    private func wake() {
        withAnimation(.easeOut(duration: 0.2)) { controlsVisible = true }
        interaction += 1
    }

    private func close() {
        model.stop()
        dismiss()
    }

    // MARK: Gestures

    private var gestureLayer: some View {
        GeometryReader { geo in
            Color.clear
                .contentShape(Rectangle())
                .onTapGesture {
                    if controlsVisible {
                        withAnimation(.easeOut(duration: 0.2)) { controlsVisible = false }
                    } else {
                        wake()
                    }
                }
                .gesture(
                    DragGesture(minimumDistance: 12)
                        .onChanged { value in dragChanged(value, size: geo.size) }
                        .onEnded { _ in dragEnded() }
                )
        }
        .ignoresSafeArea()
    }

    private func dragChanged(_ value: DragGesture.Value, size: CGSize) {
        if dragMode == nil {
            let dx = value.translation.width, dy = value.translation.height
            if abs(dx) > abs(dy) {
                dragMode = .seek
                dragStart = model.position
                wake()
            } else if value.startLocation.x < size.width / 2 {
                dragMode = .brightness
                dragStart = Double(currentScreen?.brightness ?? 0.5)
            } else {
                dragMode = .volume
                dragStart = Double(model.volume)
            }
        }
        switch dragMode {
        case .seek:
            let delta = Double(value.translation.width / max(size.width, 1)) * fullSwipeSeekSeconds
            let upper = model.duration > 0 ? model.duration : .greatestFiniteMagnitude
            seekPreview = min(max(dragStart + delta, 0), upper)
        case .brightness, .volume:
            // Swiping up raises the level; the full screen height covers the whole range.
            let level = min(max(dragStart - Double(value.translation.height / max(size.height, 1)), 0), 1)
            levelValue = level
            if dragMode == .brightness {
                // Not quite 0: the screen would look switched off.
                currentScreen?.brightness = CGFloat(max(level, 0.01))
            } else {
                model.setVolume(Float(level))
            }
        case nil:
            break
        }
    }

    private func dragEnded() {
        if dragMode == .seek, let seekPreview { model.seek(to: seekPreview) }
        dragMode = nil
        seekPreview = nil
    }

    // MARK: Overlays

    @ViewBuilder
    private var overlays: some View {
        if let seekPreview {
            let delta = seekPreview - dragStart
            hud {
                Text("\(delta < 0 ? "-" : "+")\(formatTime(abs(delta)))  \(formatTime(seekPreview)) / \(formatTime(model.duration))")
                    .font(.title3.monospacedDigit())
            }
        } else if let dragMode, dragMode != .seek {
            hud {
                HStack(spacing: 8) {
                    Image(systemName: dragMode == .brightness ? "sun.max.fill"
                          : levelValue <= 0 ? "speaker.slash.fill" : "speaker.wave.2.fill")
                    Text("\(Int((levelValue * 100).rounded()))%").font(.title3.monospacedDigit())
                }
            }
        } else if let failure = model.failure {
            hud { Text(failure).multilineTextAlignment(.center) }
                .padding(32)
        } else if model.isActive && !model.isPlaying {
            ProgressView().tint(.white).scaleEffect(1.4)
        }
    }

    private func hud<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        content()
            .foregroundColor(.white)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .background(Color.black.opacity(0.6), in: RoundedRectangle(cornerRadius: 8))
            .allowsHitTesting(false)
    }

    private var controls: some View {
        VStack(spacing: 0) {
            HStack(spacing: 4) {
                Button(action: close) {
                    Image(systemName: "chevron.left")
                        .font(.title3.weight(.semibold))
                        .frame(width: 44, height: 44)
                }
                .accessibilityLabel("返回")
                Text(item.title).lineLimit(1)
                Spacer()
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(LinearGradient(colors: [.black.opacity(0.6), .clear], startPoint: .top, endPoint: .bottom)
                .ignoresSafeArea())

            Spacer(minLength: 0)

            VStack(spacing: 4) {
                Slider(
                    value: Binding(
                        get: { sliderValue ?? model.position },
                        set: { sliderValue = $0; interaction += 1 }),
                    in: 0...max(model.duration, 1),
                    onEditingChanged: { editing in
                        if !editing, let sliderValue {
                            model.seek(to: sliderValue)
                            self.sliderValue = nil
                        }
                    })
                    .tint(.white)
                HStack {
                    Text(formatTime(sliderValue ?? model.position)).font(.footnote.monospacedDigit())
                    Spacer()
                    HStack(spacing: 28) {
                        Button { wake(); model.seek(to: model.position - 10) } label: {
                            Image(systemName: "gobackward.10").font(.title2)
                        }
                        .accessibilityLabel("后退10秒")
                        Button { wake(); model.togglePlay() } label: {
                            Image(systemName: model.isPlaying ? "pause.fill" : "play.fill").font(.title)
                                .frame(width: 44, height: 44)
                        }
                        .accessibilityLabel(model.isPlaying ? "暂停" : "播放")
                        Button { wake(); model.seek(to: model.position + 10) } label: {
                            Image(systemName: "goforward.10").font(.title2)
                        }
                        .accessibilityLabel("前进10秒")
                    }
                    Spacer()
                    Text(formatTime(model.duration)).font(.footnote.monospacedDigit())
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .background(LinearGradient(colors: [.clear, .black.opacity(0.7)], startPoint: .top, endPoint: .bottom)
                .ignoresSafeArea())
        }
        .foregroundColor(.white)
        .transition(.opacity)
    }
}
