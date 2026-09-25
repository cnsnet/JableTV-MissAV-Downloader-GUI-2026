package top.cnsc.uavresolver

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.content.pm.ActivityInfo
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.annotation.OptIn
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Forward10
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Replay10
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

private const val FULL_SWIPE_SEEK_MS = 10 * 60 * 1000f

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

    DisposableEffect(Unit) {
        activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
        val window = (view.parent as? DialogWindowProvider)?.window ?: activity?.window
        val controller = window?.let { WindowCompat.getInsetsController(it, it.decorView) }
        window?.let { WindowCompat.setDecorFitsSystemWindows(it, false) }
        controller?.hide(WindowInsetsCompat.Type.systemBars())
        controller?.systemBarsBehavior =
            WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        onDispose {
            window?.let { WindowCompat.setDecorFitsSystemWindows(it, true) }
            controller?.show(WindowInsetsCompat.Type.systemBars())
            activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
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
    var positionMs by remember { mutableStateOf(0L) }
    var durationMs by remember { mutableStateOf(0L) }
    var controlsVisible by remember { mutableStateOf(true) }
    var lastInteraction by remember { mutableStateOf(0L) }
    var seekPreviewMs by remember { mutableStateOf<Long?>(null) }
    var sliderDragValue by remember { mutableStateOf<Float?>(null) }
    var dragAccumPx by remember { mutableStateOf(0f) }

    DisposableEffect(player) {
        val listener = object : Player.Listener {
            override fun onIsPlayingChanged(playing: Boolean) { isPlaying = playing }
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
