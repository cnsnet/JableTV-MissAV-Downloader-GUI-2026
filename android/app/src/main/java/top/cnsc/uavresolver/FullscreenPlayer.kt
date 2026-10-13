package top.cnsc.uavresolver

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.content.pm.ActivityInfo
import android.media.AudioManager
import android.net.Uri
import android.provider.Settings
import android.view.WindowManager
import androidx.activity.compose.BackHandler
import androidx.annotation.OptIn
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Brightness6
import androidx.compose.material.icons.filled.Forward10
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Replay10
import androidx.compose.material.icons.filled.VolumeOff
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.DialogWindowProvider
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.ui.PlayerView
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive

private fun Context.findActivity(): Activity? {
    var ctx = this
    while (ctx is ContextWrapper) {
        if (ctx is Activity) return ctx
        ctx = ctx.baseContext
    }
    return null
}

private const val FULL_SWIPE_SEEK_MS = 20 * 60 * 1000f

// What a vertical swipe adjusts: the left half the brightness, the right
// half the volume. A swipe over the full screen height covers the whole range.
private enum class SwipeLevel { VOLUME, BRIGHTNESS }

private fun formatDuration(ms: Long): String {
    if (ms < 0) return "0:00"
    val totalSeconds = ms / 1000
    val h = totalSeconds / 3600
    val m = (totalSeconds % 3600) / 60
    val s = totalSeconds % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, s) else "%d:%02d".format(m, s)
}

@OptIn(UnstableApi::class)
@Composable
fun FullscreenPlayer(video: VideoDetail, onClose: () -> Unit) {
    val context = LocalContext.current
    val activity = remember(context) { context.findActivity() }
    val view = LocalView.current
    val window = remember(view, activity) {
        (view.parent as? DialogWindowProvider)?.window ?: activity?.window
    }
    val audioManager = remember(context) { context.getSystemService(AudioManager::class.java) }

    DisposableEffect(Unit) {
        activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
        val controller = window?.let { WindowCompat.getInsetsController(it, it.decorView) }
        window?.let { WindowCompat.setDecorFitsSystemWindows(it, false) }
        controller?.hide(WindowInsetsCompat.Type.systemBars())
        controller?.systemBarsBehavior =
            WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        onDispose {
            window?.let { WindowCompat.setDecorFitsSystemWindows(it, true) }
            controller?.show(WindowInsetsCompat.Type.systemBars())
            activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
            // A brightness set by swiping only lasts while the player is open.
            window?.let {
                it.attributes = it.attributes.apply {
                    screenBrightness = WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE
                }
            }
        }
    }

    val player = remember(video.resolvedUrl, video.headers) {
        val dataSourceFactory = DefaultHttpDataSource.Factory()
            .setDefaultRequestProperties(video.headers)
        ExoPlayer.Builder(context)
            .setMediaSourceFactory(DefaultMediaSourceFactory(dataSourceFactory))
            .build()
            .apply {
                setMediaItem(MediaItem.fromUri(Uri.parse(video.resolvedUrl)))
                prepare()
                playWhenReady = true
            }
    }
    DisposableEffect(player) { onDispose { player.release() } }

    var isPlaying by remember { mutableStateOf(true) }
    // Screen stays on while playing or buffering to play; paused or ended,
    // it may time out as usual.
    var keepScreenOn by remember { mutableStateOf(true) }
    var positionMs by remember { mutableStateOf(0L) }
    var durationMs by remember { mutableStateOf(0L) }
    var controlsVisible by remember { mutableStateOf(true) }
    var lastInteraction by remember { mutableStateOf(0L) }
    var seekPreviewMs by remember { mutableStateOf<Long?>(null) }
    var sliderDragValue by remember { mutableStateOf<Float?>(null) }
    var dragAccumPx by remember { mutableStateOf(0f) }
    var swipeLevel by remember { mutableStateOf<SwipeLevel?>(null) }
    // 0..1 of the level being swiped, for its overlay.
    var swipeLevelValue by remember { mutableStateOf(0f) }

    DisposableEffect(player) {
        val listener = object : Player.Listener {
            override fun onIsPlayingChanged(playing: Boolean) { isPlaying = playing }
            override fun onEvents(player: Player, events: Player.Events) {
                keepScreenOn = player.playWhenReady && player.playbackState.let {
                    it == Player.STATE_BUFFERING || it == Player.STATE_READY
                }
            }
        }
        player.addListener(listener)
        onDispose { player.removeListener(listener) }
    }

    LaunchedEffect(player) {
        while (isActive) {
            positionMs = player.currentPosition.coerceAtLeast(0)
            durationMs = player.duration.coerceAtLeast(0)
            delay(500)
        }
    }

    LaunchedEffect(lastInteraction, isPlaying) {
        if (isPlaying) {
            delay(3000)
            controlsVisible = false
        }
    }

    fun wake() {
        controlsVisible = true
        lastInteraction = System.currentTimeMillis()
    }

    val density = LocalDensity.current
    val screenWidthDp = LocalConfiguration.current.screenWidthDp.toFloat()

    fun currentVolume(): Float {
        val max = audioManager?.getStreamMaxVolume(AudioManager.STREAM_MUSIC) ?: return 0f
        return audioManager.getStreamVolume(AudioManager.STREAM_MUSIC).toFloat() / max
    }

    fun setVolume(level: Float) {
        val am = audioManager ?: return
        val max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        am.setStreamVolume(AudioManager.STREAM_MUSIC, Math.round(level * max), 0)
    }

    // The window's own brightness once set, else the system's (0..255).
    fun currentBrightness(): Float {
        val own = window?.attributes?.screenBrightness ?: -1f
        if (own >= 0f) return own
        return try {
            Settings.System.getInt(context.contentResolver, Settings.System.SCREEN_BRIGHTNESS) / 255f
        } catch (e: Settings.SettingNotFoundException) {
            0.5f
        }
    }

    fun setBrightness(level: Float) {
        val w = window ?: return
        // Not quite 0: some devices turn the screen fully off at 0.
        w.attributes = w.attributes.apply { screenBrightness = level.coerceIn(0.01f, 1f) }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black)
            .pointerInput(durationMs, screenWidthDp) {
                // Full edge-to-edge swipe covers FULL_SWIPE_SEEK_MS regardless of screen size.
                val msPerDp = FULL_SWIPE_SEEK_MS / screenWidthDp
                detectHorizontalDragGestures(
                    onDragStart = {
                        wake()
                        dragAccumPx = 0f
                        seekPreviewMs = positionMs
                    },
                    onDragEnd = {
                        seekPreviewMs?.let { target ->
                            player.seekTo(target.coerceIn(0, durationMs.coerceAtLeast(0)))
                        }
                        seekPreviewMs = null
                        dragAccumPx = 0f
                    },
                    onDragCancel = {
                        seekPreviewMs = null
                        dragAccumPx = 0f
                    },
                    onHorizontalDrag = { change, dragAmount ->
                        change.consume()
                        dragAccumPx += dragAmount
                        val deltaMs = (dragAccumPx / density.density * msPerDp).toLong()
                        val cap = if (durationMs > 0) durationMs else Long.MAX_VALUE
                        seekPreviewMs = (positionMs + deltaMs).coerceIn(0, cap)
                    },
                )
            }
            .pointerInput(Unit) {
                var startLevel = 0f
                detectVerticalDragGestures(
                    onDragStart = { offset ->
                        val level = if (offset.x < size.width / 2) SwipeLevel.BRIGHTNESS else SwipeLevel.VOLUME
                        swipeLevel = level
                        startLevel = if (level == SwipeLevel.VOLUME) currentVolume() else currentBrightness()
                        swipeLevelValue = startLevel
                        dragAccumPx = 0f
                    },
                    onDragEnd = { swipeLevel = null; dragAccumPx = 0f },
                    onDragCancel = { swipeLevel = null; dragAccumPx = 0f },
                    onVerticalDrag = { change, dragAmount ->
                        change.consume()
                        dragAccumPx += dragAmount
                        // Swiping up (negative y) raises the level.
                        val value = (startLevel - dragAccumPx / size.height).coerceIn(0f, 1f)
                        swipeLevelValue = value
                        if (swipeLevel == SwipeLevel.VOLUME) setVolume(value) else setBrightness(value)
                    },
                )
            }
            .clickable(
                indication = null,
                interactionSource = remember { MutableInteractionSource() },
            ) { if (controlsVisible) controlsVisible = false else wake() },
    ) {
        AndroidView(
            factory = {
                PlayerView(it).apply {
                    this.player = player
                    useController = false
                }
            },
            update = { it.keepScreenOn = keepScreenOn },
            modifier = Modifier.fillMaxSize(),
        )

        seekPreviewMs?.let { preview ->
            val delta = preview - positionMs
            Box(
                modifier = Modifier
                    .align(Alignment.Center)
                    .background(Color.Black.copy(alpha = 0.6f), RoundedCornerShape(8.dp))
                    .padding(horizontal = 16.dp, vertical = 8.dp),
            ) {
                Text(
                    "${if (delta >= 0) "+" else ""}${formatDuration(delta)}  ${formatDuration(preview)}",
                    color = Color.White,
                    style = MaterialTheme.typography.titleMedium,
                )
            }
        }

        swipeLevel?.let { level ->
            Row(
                modifier = Modifier
                    .align(Alignment.Center)
                    .background(Color.Black.copy(alpha = 0.6f), RoundedCornerShape(8.dp))
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(
                    when {
                        level == SwipeLevel.BRIGHTNESS -> Icons.Default.Brightness6
                        swipeLevelValue <= 0f -> Icons.Default.VolumeOff
                        else -> Icons.Default.VolumeUp
                    },
                    contentDescription = if (level == SwipeLevel.VOLUME) "音量" else "亮度",
                    tint = Color.White,
                )
                Text(
                    "${Math.round(swipeLevelValue * 100)}%",
                    color = Color.White,
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.padding(start = 8.dp),
                )
            }
        }

        if (controlsVisible) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .align(Alignment.TopStart)
                    .background(Brush.verticalGradient(listOf(Color.Black.copy(alpha = 0.6f), Color.Transparent)))
                    .padding(horizontal = 8.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                IconButton(onClick = onClose) {
                    Icon(Icons.Default.ArrowBack, contentDescription = "返回", tint = Color.White)
                }
                Text(
                    video.title,
                    color = Color.White,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f).padding(start = 4.dp),
                )
            }

            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .align(Alignment.BottomStart)
                    .background(Brush.verticalGradient(listOf(Color.Transparent, Color.Black.copy(alpha = 0.7f))))
                    .padding(horizontal = 16.dp, vertical = 8.dp),
            ) {
                Slider(
                    value = sliderDragValue ?: positionMs.toFloat(),
                    onValueChange = { wake(); sliderDragValue = it },
                    onValueChangeFinished = {
                        sliderDragValue?.let { player.seekTo(it.toLong()) }
                        sliderDragValue = null
                    },
                    valueRange = 0f..durationMs.coerceAtLeast(1).toFloat(),
                    colors = SliderDefaults.colors(
                        thumbColor = Color.White,
                        activeTrackColor = Color.White,
                        inactiveTrackColor = Color.White.copy(alpha = 0.3f),
                    ),
                )
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(formatDuration(sliderDragValue?.toLong() ?: positionMs), color = Color.White)
                    Spacer(Modifier.weight(1f))
                    IconButton(onClick = {
                        wake()
                        player.seekTo((positionMs - 10000).coerceAtLeast(0))
                    }) {
                        Icon(Icons.Default.Replay10, contentDescription = "后退10秒", tint = Color.White)
                    }
                    IconButton(onClick = {
                        wake()
                        if (isPlaying) player.pause() else player.play()
                    }) {
                        Icon(
                            if (isPlaying) Icons.Default.Pause else Icons.Default.PlayArrow,
                            contentDescription = if (isPlaying) "暂停" else "播放",
                            tint = Color.White,
                        )
                    }
                    IconButton(onClick = {
                        wake()
                        player.seekTo((positionMs + 10000).coerceAtMost(durationMs.coerceAtLeast(0)))
                    }) {
                        Icon(Icons.Default.Forward10, contentDescription = "前进10秒", tint = Color.White)
                    }
                    Spacer(Modifier.weight(1f))
                    Text(formatDuration(durationMs), color = Color.White)
                }
            }
        }
    }

    BackHandler(onBack = onClose)
}
