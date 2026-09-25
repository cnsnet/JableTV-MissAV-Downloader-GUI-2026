package top.cnsc.uavresolver

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import kotlinx.coroutines.launch

@Composable
fun VideoDetailDialog(
    video: BrowseVideo,
    baseUrl: String,
    apiKey: String,
    api: ResolverApi,
    history: HistoryStore,
    onSubmitResult: (String, Boolean) -> Unit,
    onPlayVideo: (VideoDetail) -> Unit,
    isFullscreenActive: Boolean,
    onDismiss: () -> Unit,
) {
    var detail by remember { mutableStateOf<VideoDetail?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var downloading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(video.url) {
        loading = true
        error = null
        when (val result = api.detail(baseUrl, apiKey, video.url)) {
            is DetailResult.Success -> detail = result.detail
            is DetailResult.Failure -> error = result.message
        }
        loading = false
    }

    fun submitRemoteDownload() {
        scope.launch {
            downloading = true
            when (val result = api.resolve(baseUrl, apiKey, video.url)) {
                is ResolveResult.Success -> {
                    history.add(HistoryEntry(video.url, result.outputName, true, "已提交", System.currentTimeMillis()))
                    onSubmitResult("已提交远程下载", true)
                }
                is ResolveResult.Failure -> {
                    history.add(HistoryEntry(video.url, null, false, result.message, System.currentTimeMillis()))
                    onSubmitResult(result.message, false)
                }
            }
            downloading = false
        }
    }

    if (!isFullscreenActive) {
        BackHandler(onBack = onDismiss)
        Surface(modifier = Modifier.fillMaxSize(), color = Color.Black) {
            Column(modifier = Modifier.fillMaxSize()) {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(4.dp),
                    horizontalArrangement = Arrangement.End,
                ) {
                    IconButton(onClick = onDismiss) {
                        Icon(Icons.Default.Close, contentDescription = "关闭", tint = Color.White)
                    }
                }

                Box(modifier = Modifier.fillMaxWidth().aspectRatio(16f / 9f)) {
                    AsyncImage(
                        model = api.thumbUrl(baseUrl, apiKey, video.thumbnail),
                        contentDescription = video.title,
                        contentScale = ContentScale.Fit,
                        modifier = Modifier.fillMaxSize(),
                    )
                    if (loading) {
                        CircularProgressIndicator(
                            modifier = Modifier.align(Alignment.Center),
                            color = Color.White,
                        )
                    }
                }

                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        video.title,
                        color = Color.White,
                        style = MaterialTheme.typography.titleMedium,
                        maxLines = 3,
                        overflow = TextOverflow.Ellipsis,
                    )
                    error?.let {
                        Text(
                            it,
                            color = MaterialTheme.colorScheme.error,
                            modifier = Modifier.padding(top = 8.dp),
                        )
                    }
                    Spacer(Modifier.height(16.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Button(
                            onClick = {
                                detail?.let { onPlayVideo(it) }
                            },
                            enabled = detail != null,
                        ) {
                            Icon(Icons.Default.PlayArrow, contentDescription = null)
                            Spacer(Modifier.width(4.dp))
                            Text("播放")
                        }
                        Button(
                            onClick = { submitRemoteDownload() },
                            enabled = !downloading,
                        ) {
                            Icon(Icons.Default.CloudDownload, contentDescription = null)
                            Spacer(Modifier.width(4.dp))
                            Text(if (downloading) "提交中..." else "远程下载")
                        }
                    }
                }
            }
        }
    }
}
