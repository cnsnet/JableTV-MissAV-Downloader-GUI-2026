package top.cnsc.uavresolver

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.FavoriteBorder
import androidx.compose.material.icons.filled.OpenInBrowser
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import kotlinx.coroutines.CancellationException
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
    // Called with the video's url once it was deleted on the server.
    onRemoved: (String) -> Unit,
    // Called with a video's url and new state once its favourite was set on
    // the server (this video or one of its recommendations).
    onFavouriteChanged: (String, Boolean) -> Unit,
) {
    var detail by remember { mutableStateOf<VideoDetail?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var downloading by remember { mutableStateOf(false) }
    var related by remember { mutableStateOf<List<BrowseVideo>>(emptyList()) }
    var relatedLoading by remember { mutableStateOf(false) }
    var selected by remember { mutableStateOf(setOf<String>()) }
    var submitting by remember { mutableStateOf(false) }
    var confirmRemove by remember { mutableStateOf(false) }
    var removing by remember { mutableStateOf(false) }
    var favourite by remember { mutableStateOf(video.favourite) }
    var favouriting by remember { mutableStateOf(false) }
    // Long-pressing a recommendation opens another detail page stacked on top.
    var detailTarget by remember { mutableStateOf<BrowseVideo?>(null) }
    val scope = rememberCoroutineScope()
    // Browse only lists JableTV and MissAV, and both have recommendations
    // (MissAV via its recommender, Jable's "猜你喜歡" block on the video page).
    val relatedSite = if (video.url.contains("missav", ignoreCase = true)) "missav" else "jabletv"

    // Bumped by the retry button to fetch the detail again.
    var detailAttempt by remember { mutableStateOf(0) }

    // The listing already gave title/cover, so the page shows those at once;
    // this only fetches resolved_url, which play/download wait for.
    LaunchedEffect(video.url, detailAttempt) {
        loading = true
        error = null
        when (val result = api.detail(baseUrl, apiKey, video.url)) {
            is DetailResult.Success -> detail = result.detail
            is DetailResult.Failure -> error = result.message
        }
        loading = false
    }

    // Loaded separately so the page shows as soon as the detail arrives.
    LaunchedEffect(video.url) {
        relatedLoading = true
        related = try {
            api.related(baseUrl, apiKey, relatedSite, video.url)
                .filter { it.url != video.url }
                .distinctBy { it.url }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            emptyList()
        }
        relatedLoading = false
    }

    fun submitSelected() {
        val urls = selected.toList()
        if (urls.isEmpty()) return
        scope.launch {
            submitting = true
            val (msg, ok) = submitUrls(api, baseUrl, apiKey, history, urls)
            submitting = false
            selected = emptySet()
            onSubmitResult(msg, ok)
        }
    }

    fun removeSelected() {
        val urls = selected.toList()
        if (urls.isEmpty()) return
        scope.launch {
            removing = true
            val (removedUrls, msg) = removeUrls(api, baseUrl, apiKey, urls)
            removing = false
            related = related.filter { it.url !in removedUrls }
            selected = selected - removedUrls
            removedUrls.forEach(onRemoved)
            onSubmitResult(msg, removedUrls.size == urls.size)
        }
    }

    fun submitRemoteDownload() {
        val d = detail ?: return
        scope.launch {
            downloading = true
            // Reuses the resolved_url from /api/detail instead of having the
            // server scrape the page again. Named by id, not title: some
            // titles are too long for a file name.
            val outputName = "${d.id.ifBlank { video.id }.ifBlank { "video" }}.mp4"
            when (val result = api.download(baseUrl, apiKey, d.resolvedUrl, outputName)) {
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

    // Keeps this page's own state and its recommendations in step with a
    // favourite set here or on a page stacked on top.
    fun applyFavourite(url: String, value: Boolean) {
        if (url == video.url) favourite = value
        related = related.map { if (it.url == url) it.copy(favourite = value) else it }
        onFavouriteChanged(url, value)
    }

    fun toggleFavourite(url: String, current: Boolean) {
        scope.launch {
            if (url == video.url) favouriting = true
            val failure = setFavourite(api, baseUrl, apiKey, url, !current)
            if (url == video.url) favouriting = false
            if (failure == null) {
                applyFavourite(url, !current)
                onSubmitResult(if (current) "已取消收藏" else "已收藏", true)
            } else {
                onSubmitResult(failure, false)
            }
        }
    }

    fun removeVideo() {
        scope.launch {
            removing = true
            try {
                api.removeVideo(baseUrl, apiKey, video.url)
                onSubmitResult("已删除", true)
                onRemoved(video.url)
                onDismiss()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                onSubmitResult(e.message ?: "删除失败", false)
            }
            removing = false
        }
    }

    // The listing gives the id; failing that its title usually leads with the
    // code ("IPZZ-891 ..."), so the top bar needn't wait for /api/detail.
    val code = detail?.id?.ifBlank { null }
        ?: video.id.ifBlank { null }?.uppercase()
        ?: video.title.substringBefore(' ')
    val resolved = detail?.resolvedUrl?.isNotBlank() == true
    val title = detail?.title?.ifBlank { null } ?: video.title
    val uriHandler = LocalUriHandler.current

    if (!isFullscreenActive) {
        BackHandler(onBack = onDismiss)
        Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            Column(modifier = Modifier.fillMaxSize()) {
                Surface(color = Color(0xFF212121), contentColor = Color.White) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(56.dp)
                            .padding(horizontal = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        IconButton(onClick = onDismiss) {
                            Icon(Icons.Default.ArrowBack, contentDescription = "返回")
                        }
                        Text(
                            code,
                            style = MaterialTheme.typography.titleLarge,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.padding(start = 16.dp),
                        )
                    }
                }

                LazyVerticalGrid(
                    columns = GridCells.Fixed(2),
                    modifier = Modifier.weight(1f),
                ) {
                    item(key = "header", span = { GridItemSpan(maxLineSpan) }) {
                        Column {
                            Box(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .aspectRatio(16f / 9f)
                                    .background(Color(0xFFEFEFEF)),
                            ) {
                                AsyncImage(
                                    model = api.thumbUrl(baseUrl, apiKey, video.thumbnail),
                                    contentDescription = title,
                                    contentScale = ContentScale.Crop,
                                    modifier = Modifier.fillMaxSize(),
                                )
                                // Same look as the heart on listing cards, a size up.
                                Box(
                                    modifier = Modifier
                                        .align(Alignment.BottomEnd)
                                        .padding(8.dp)
                                        .background(Color.Black.copy(alpha = 0.4f), RoundedCornerShape(50))
                                        .clickable(enabled = !favouriting) {
                                            toggleFavourite(video.url, favourite)
                                        }
                                        .padding(8.dp),
                                ) {
                                    if (favouriting) {
                                        CircularProgressIndicator(
                                            color = Color.White,
                                            strokeWidth = 2.dp,
                                            modifier = Modifier.size(24.dp),
                                        )
                                    } else {
                                        Icon(
                                            if (favourite) Icons.Default.Favorite else Icons.Default.FavoriteBorder,
                                            contentDescription = if (favourite) "取消收藏" else "收藏",
                                            tint = if (favourite) favouriteColor else Color.White,
                                            modifier = Modifier.size(24.dp),
                                        )
                                    }
                                }
                            }

                            Column(modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp)) {
                                Text(
                                    code,
                                    style = MaterialTheme.typography.titleLarge,
                                    fontWeight = FontWeight.Medium,
                                )
                                // On a line of their own: next to a long code they'd be
                                // squeezed into one character per line.
                                VersionBadges(
                                    hasChineseSubtitle = detail?.hasChineseSubtitle
                                        ?: video.hasChineseSubtitle,
                                    isUncensoredLeak = detail?.isUncensoredLeak
                                        ?: video.isUncensoredLeak,
                                    modifier = Modifier.padding(top = 6.dp),
                                )
                                // /api/detail may swap an untranslated Japanese title for a
                                // Chinese one, so prefer it once loaded.
                                Text(
                                    title,
                                    style = MaterialTheme.typography.bodyLarge,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    maxLines = 4,
                                    overflow = TextOverflow.Ellipsis,
                                    modifier = Modifier.padding(top = 8.dp),
                                )
                                if (loading) {
                                    Row(
                                        modifier = Modifier.padding(top = 8.dp),
                                        verticalAlignment = Alignment.CenterVertically,
                                    ) {
                                        CircularProgressIndicator(modifier = Modifier.size(14.dp), strokeWidth = 2.dp)
                                        Spacer(Modifier.width(6.dp))
                                        Text(
                                            "正在获取播放地址...",
                                            style = MaterialTheme.typography.bodySmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        )
                                    }
                                }
                                error?.let {
                                    Row(
                                        modifier = Modifier.padding(top = 8.dp),
                                        verticalAlignment = Alignment.CenterVertically,
                                    ) {
                                        Text(
                                            it,
                                            color = MaterialTheme.colorScheme.error,
                                            modifier = Modifier.weight(1f, fill = false),
                                        )
                                        TextButton(onClick = { detailAttempt++ }) { Text("重试") }
                                    }
                                }
                                Spacer(Modifier.height(16.dp))
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    DetailButton(
                                        text = "播放",
                                        icon = Icons.Default.PlayArrow,
                                        enabled = resolved,
                                        onClick = { detail?.let { onPlayVideo(it) } },
                                    )
                                    DetailButton(
                                        text = "下载",
                                        icon = Icons.Default.CloudDownload,
                                        enabled = resolved && !downloading,
                                        busy = downloading,
                                        onClick = { submitRemoteDownload() },
                                    )
                                    DetailButton(
                                        text = "浏览器",
                                        icon = Icons.Default.OpenInBrowser,
                                        onClick = {
                                            try {
                                                uriHandler.openUri(video.url)
                                            } catch (e: Exception) {
                                                onSubmitResult("无法打开浏览器", false)
                                            }
                                        },
                                    )
                                    DetailButton(
                                        text = "删除",
                                        icon = Icons.Default.Delete,
                                        enabled = !removing,
                                        busy = removing,
                                        onClick = { confirmRemove = true },
                                    )
                                }
                            }
                        }
                    }
                    if (relatedLoading || related.isNotEmpty()) {
                        item(key = "related-title", span = { GridItemSpan(maxLineSpan) }) {
                            Row(
                                modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 8.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Text(
                                    "相关推荐",
                                    style = MaterialTheme.typography.titleMedium,
                                    fontWeight = FontWeight.Bold,
                                )
                                if (relatedLoading) {
                                    Spacer(Modifier.width(8.dp))
                                    CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
                                }
                            }
                        }
                    }
                    items(related, key = { it.url }) { item ->
                        VideoCard(
                            video = item,
                            thumbUrl = api.thumbUrl(baseUrl, apiKey, item.thumbnail),
                            isSelected = selected.contains(item.url),
                            onToggle = {
                                selected = if (selected.contains(item.url)) {
                                    selected - item.url
                                } else {
                                    selected + item.url
                                }
                            },
                            onLongPress = { detailTarget = item },
                            onToggleFavourite = { toggleFavourite(item.url, item.favourite) },
                        )
                    }
                }

                if (selected.isNotEmpty()) {
                    SelectionBar(
                        count = selected.size, submitting = submitting, onSubmit = { submitSelected() },
                        removing = removing, onRemove = { removeSelected() },
                    )
                }
            }
        }
    }

    if (confirmRemove) {
        AlertDialog(
            onDismissRequest = { confirmRemove = false },
            title = { Text("删除视频") },
            text = { Text("删除后 $code 将不再出现在任何列表中。") },
            confirmButton = {
                TextButton(onClick = { confirmRemove = false; removeVideo() }) { Text("删除") }
            },
            dismissButton = {
                TextButton(onClick = { confirmRemove = false }) { Text("取消") }
            },
        )
    }

    detailTarget?.let { item ->
        VideoDetailDialog(
            video = item,
            baseUrl = baseUrl,
            apiKey = apiKey,
            api = api,
            history = history,
            onSubmitResult = onSubmitResult,
            onPlayVideo = onPlayVideo,
            isFullscreenActive = isFullscreenActive,
            onDismiss = { detailTarget = null },
            onRemoved = { url ->
                related = related.filter { it.url != url }
                selected = selected - url
                onRemoved(url)
            },
            onFavouriteChanged = { url, value -> applyFavourite(url, value) },
        )
    }
}

// Icon only; *text* is what accessibility services read out.
@Composable
private fun DetailButton(
    text: String,
    icon: ImageVector,
    onClick: () -> Unit,
    enabled: Boolean = true,
    // A spinner in place of the icon while its action runs.
    busy: Boolean = false,
) {
    OutlinedButton(
        onClick = onClick,
        enabled = enabled,
        shape = RoundedCornerShape(4.dp),
        // Square: the fixed size also overrides the button's 58dp minimum width.
        contentPadding = PaddingValues(0.dp),
        modifier = Modifier.size(48.dp),
    ) {
        if (busy) {
            CircularProgressIndicator(modifier = Modifier.size(26.dp), strokeWidth = 2.dp)
        } else {
            Icon(
                icon,
                contentDescription = text,
                modifier = Modifier.size(26.dp),
            )
        }
    }
}
