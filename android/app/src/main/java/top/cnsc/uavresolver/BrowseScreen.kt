package top.cnsc.uavresolver

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.ArrowForward
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

private enum class PickerMode { NAV, MENU, TAGS }

// Pseudo-site for the resolver's own stored-video list (/api/videos).
private const val STORED_SITE = "stored"

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BrowseScreen(
    baseUrl: String,
    apiKey: String,
    api: ResolverApi,
    history: HistoryStore,
    categoryCache: CategoryCache,
    onToggleSettings: () -> Unit,
    onOpenHistory: () -> Unit,
    onSubmitResult: (String, Boolean) -> Unit,
    onPlayVideo: (VideoDetail) -> Unit,
    isFullscreenActive: Boolean,
) {
    var site by remember { mutableStateOf("") }
    var jabletvCategories by remember { mutableStateOf<List<BrowseCategory>>(emptyList()) }
    var missavCategories by remember { mutableStateOf<List<BrowseCategory>>(emptyList()) }
    var selectedCategory by remember { mutableStateOf<BrowseCategory?>(null) }
    var searchQuery by remember { mutableStateOf("") }
    var activeSearch by remember { mutableStateOf("") }
    var page by remember { mutableStateOf(1) }
    var videos by remember { mutableStateOf<List<BrowseVideo>>(emptyList()) }
    // Page count of the stored list; null for site listings, which don't say.
    var lastPage by remember { mutableStateOf<Int?>(null) }
    // Bumped to reload the current list when its other keys haven't changed.
    var reloadKey by remember { mutableStateOf(0) }
    var selected by remember { mutableStateOf(setOf<String>()) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var submitting by remember { mutableStateOf(false) }
    var removing by remember { mutableStateOf(false) }
    var detailTarget by remember { mutableStateOf<BrowseVideo?>(null) }
    var menuExpanded by remember { mutableStateOf(false) }
    var pickerMode by remember { mutableStateOf<PickerMode?>(null) }
    var pickerLoading by remember { mutableStateOf(false) }
    var pickerError by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val gridState = rememberLazyGridState()

    suspend fun ensureCategories(targetSite: String): List<BrowseCategory> {
        val existing = if (targetSite == "jabletv") jabletvCategories else missavCategories
        if (existing.isNotEmpty()) return existing
        val cached = categoryCache.get(targetSite)
        val cats = if (cached != null) {
            cached
        } else {
            val fresh = api.listCategories(baseUrl, apiKey, targetSite)
            if (fresh.isNotEmpty()) categoryCache.put(targetSite, fresh)
            fresh
        }
        if (targetSite == "jabletv") jabletvCategories = cats else missavCategories = cats
        return cats
    }

    suspend fun loadVideos(cat: BrowseCategory?, query: String, pageNum: Int) {
        if (site != STORED_SITE && query.isEmpty() && cat == null) return
        loading = true
        error = null
        selected = emptySet()
        lastPage = null
        try {
            videos = if (site == STORED_SITE) {
                val stored = api.storedVideos(baseUrl, apiKey, pageNum, query)
                lastPage = stored.pages
                stored.videos
            } else if (query.isNotEmpty()) {
                api.search(baseUrl, apiKey, site, query, pageNum)
            } else {
                api.listVideos(baseUrl, apiKey, site, cat!!.url, pageNum)
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            error = e.message ?: "列表加载失败"
            videos = emptyList()
        }
        loading = false
    }

    fun openSiteHome(targetSite: String) {
        scope.launch {
            loading = true
            error = null
            try {
                val cats = ensureCategories(targetSite)
                site = targetSite
                selectedCategory = cats.firstOrNull()
                activeSearch = ""
                searchQuery = ""
                page = 1
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                error = e.message ?: "分类加载失败"
            }
            loading = false
        }
    }

    // With a keyword in the bar, searches the stored list instead.
    fun openStored() {
        site = STORED_SITE
        selectedCategory = null
        activeSearch = searchQuery.trim()
        page = 1
        reloadKey++
    }

    fun searchSite(targetSite: String) {
        val q = searchQuery.trim()
        if (q.isEmpty()) {
            openSiteHome(targetSite)
            return
        }
        site = targetSite
        selectedCategory = null
        activeSearch = q
        page = 1
    }

    LaunchedEffect(site, selectedCategory, activeSearch, page, reloadKey) {
        if (baseUrl.isNotEmpty() && apiKey.isNotEmpty() && site.isNotEmpty()) {
            loadVideos(selectedCategory, activeSearch, page)
            gridState.scrollToItem(0)
        }
    }

    LaunchedEffect(pickerMode) {
        val mode = pickerMode ?: return@LaunchedEffect
        val needsSite = if (mode == PickerMode.NAV) "missav" else "jabletv"
        val already = if (needsSite == "jabletv") jabletvCategories else missavCategories
        if (already.isNotEmpty()) return@LaunchedEffect
        pickerLoading = true
        pickerError = null
        try {
            ensureCategories(needsSite)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            pickerError = e.message ?: "分类加载失败"
        }
        pickerLoading = false
    }

    fun submitSelected() {
        val urls = selected.toList()
        if (urls.isEmpty()) return
        scope.launch {
            submitting = true
            val msg = submitUrls(api, baseUrl, apiKey, history, urls)
            submitting = false
            selected = emptySet()
            onSubmitResult(msg.first, msg.second)
        }
    }

    fun removeSelected() {
        val urls = selected.toList()
        if (urls.isEmpty()) return
        scope.launch {
            removing = true
            val (removed, msg) = removeUrls(api, baseUrl, apiKey, urls)
            removing = false
            videos = videos.filter { it.url !in removed }
            selected = selected - removed
            onSubmitResult(msg, removed.size == urls.size)
        }
    }

    Column(modifier = Modifier.fillMaxSize()) {
        if (baseUrl.isEmpty() || apiKey.isEmpty()) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "请先在设置里填写解析服务地址和 API Key",
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier
                        .weight(1f)
                        .padding(16.dp),
                )
                IconButton(onClick = onToggleSettings, modifier = Modifier.size(32.dp)) {
                    Icon(Icons.Default.Settings, contentDescription = "设置", modifier = Modifier.size(20.dp))
                }
            }
            return@Column
        }

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // One soft-grey pill bar holding the input and the two action
            // buttons, which are their own small inset rounded-rect pills with
            // breathing room around them - matching the reference look instead
            // of full-height segments touching the bar's edges.
            Surface(
                modifier = Modifier
                    .weight(1f)
                    .height(44.dp),
                shape = RoundedCornerShape(8.dp),
                color = Color(0xFFEFEFEF),
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(horizontal = 5.dp, vertical = 5.dp),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxHeight()
                            .padding(horizontal = 12.dp),
                        contentAlignment = Alignment.CenterStart,
                    ) {
                        if (searchQuery.isEmpty()) {
                            Text(
                                "关键字",
                                style = MaterialTheme.typography.bodySmall,
                                color = Color(0xFF9E9E9E),
                            )
                        }
                        BasicTextField(
                            value = searchQuery,
                            onValueChange = { searchQuery = it },
                            singleLine = true,
                            // Video codes are Latin/digits. KeyboardType.Ascii isn't
                            // enough - some Chinese IMEs still pinyin-compose and never
                            // commit unless a candidate is tapped. Email reliably forces
                            // raw ASCII input on those IMEs too, without Password's
                            // side effect of the system treating the field as sensitive
                            // (which blocks screenshots/some accessibility features).
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
                            textStyle = MaterialTheme.typography.bodySmall.copy(
                                color = Color(0xFF212121),
                            ),
                            cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                    // Jable's button is hidden to make room for the stored list;
                    // its categories are still under the menu (菜单/标签).
                    SearchBarButton(text = "Manko", onClick = { openStored() })
                    SearchBarButton(
                        text = "Miss",
                        onClick = { if (searchQuery.isBlank()) openSiteHome("missav") else searchSite("missav") },
                    )
                }
            }
            Box {
                IconButton(onClick = { menuExpanded = true }, modifier = Modifier.size(32.dp)) {
                    Icon(Icons.Default.Menu, contentDescription = "菜单", modifier = Modifier.size(22.dp))
                }
                DropdownMenu(expanded = menuExpanded, onDismissRequest = { menuExpanded = false }) {
                    DropdownMenuItem(
                        text = { Text("导航") },
                        onClick = { menuExpanded = false; pickerMode = PickerMode.NAV },
                    )
                    DropdownMenuItem(
                        text = { Text("菜单") },
                        onClick = { menuExpanded = false; pickerMode = PickerMode.MENU },
                    )
                    DropdownMenuItem(
                        text = { Text("标签") },
                        onClick = { menuExpanded = false; pickerMode = PickerMode.TAGS },
                    )
                    DropdownMenuItem(
                        text = { Text("解析下载") },
                        onClick = { menuExpanded = false; onOpenHistory() },
                    )
                    DropdownMenuItem(
                        text = { Text("设置") },
                        onClick = { menuExpanded = false; onToggleSettings() },
                    )
                }
            }
        }

        if (loading) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
        }
        error?.let {
            Text(
                it,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp),
            )
        }

        LazyVerticalGrid(
            columns = GridCells.Fixed(2),
            state = gridState,
            // Edge-to-edge like the reference app: no side gutter, only a hairline
            // gap between cards.
            modifier = Modifier.weight(1f),
            contentPadding = PaddingValues(1.dp),
        ) {
            items(videos, key = { it.url }) { video ->
                VideoCard(
                    video = video,
                    thumbUrl = api.thumbUrl(baseUrl, apiKey, video.thumbnail),
                    isSelected = selected.contains(video.url),
                    onToggle = {
                        selected = if (selected.contains(video.url)) {
                            selected - video.url
                        } else {
                            selected + video.url
                        }
                    },
                    onLongPress = { detailTarget = video },
                )
            }
        }

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 2.dp),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(
                onClick = { if (page > 1) page-- },
                enabled = page > 1,
                modifier = Modifier.size(32.dp),
            ) {
                Icon(Icons.Default.ArrowBack, contentDescription = "上一页", modifier = Modifier.size(18.dp))
            }
            Text(
                "第 $page 页",
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(horizontal = 12.dp),
            )
            IconButton(
                onClick = { page++ },
                enabled = videos.isNotEmpty() && lastPage.let { it == null || page < it },
                modifier = Modifier.size(32.dp),
            ) {
                Icon(Icons.Default.ArrowForward, contentDescription = "下一页", modifier = Modifier.size(18.dp))
            }
        }

        if (selected.isNotEmpty()) {
            SelectionBar(
                count = selected.size, submitting = submitting, onSubmit = { submitSelected() },
                removing = removing, onRemove = { removeSelected() },
            )
        }
    }

    detailTarget?.let { video ->
        VideoDetailDialog(
            video = video,
            baseUrl = baseUrl,
            apiKey = apiKey,
            api = api,
            history = history,
            onSubmitResult = onSubmitResult,
            onPlayVideo = onPlayVideo,
            isFullscreenActive = isFullscreenActive,
            onDismiss = { detailTarget = null },
            onRemoved = { url ->
                videos = videos.filter { it.url != url }
                selected = selected - url
            },
        )
    }

    if (pickerMode != null) {
        val entries: List<PickerEntry> = when (pickerMode) {
            PickerMode.NAV -> missavCategories.map { PickerEntry.Item(it) }
            PickerMode.MENU -> jabletvCategories
                .filter { it.group.isEmpty() && !it.section }
                .map { PickerEntry.Item(it) }
            PickerMode.TAGS -> {
                val result = mutableListOf<PickerEntry>()
                var lastGroup = ""
                for (cat in jabletvCategories.filter { it.group.isNotEmpty() }) {
                    if (cat.group != lastGroup) {
                        lastGroup = cat.group
                        result.add(PickerEntry.Header(cat.group))
                    }
                    result.add(PickerEntry.Item(cat))
                }
                result
            }
            null -> emptyList()
        }
        CategoryPickerDialog(
            entries = entries,
            loading = pickerLoading,
            error = pickerError,
            onSelect = { cat ->
                val targetSite = if (pickerMode == PickerMode.NAV) "missav" else "jabletv"
                site = targetSite
                selectedCategory = cat
                activeSearch = ""
                searchQuery = ""
                page = 1
                pickerMode = null
            },
            onDismiss = { pickerMode = null },
        )
    }
}

// The search bar's buttons: 44dp bar minus its 5dp inset, 10dp side padding.
private val barButtonShape = RoundedCornerShape(8.dp)
private val barButtonPadding = PaddingValues(horizontal = 10.dp)
private val barButtonHeight = 34.dp

@Composable
fun SelectionBar(
    count: Int, submitting: Boolean, onSubmit: () -> Unit,
    removing: Boolean, onRemove: () -> Unit,
) {
    var confirmRemove by remember { mutableStateOf(false) }
    Surface(
        tonalElevation = 4.dp,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("已选 $count 个", modifier = Modifier.weight(1f))
            // Same shape/size as the search bar's Manko/Miss buttons.
            OutlinedButton(
                onClick = { confirmRemove = true },
                enabled = !removing && !submitting,
                shape = barButtonShape,
                contentPadding = barButtonPadding,
                modifier = Modifier.height(barButtonHeight),
            ) {
                Text(if (removing) "删除中..." else "删除", style = MaterialTheme.typography.bodyMedium)
            }
            Spacer(Modifier.width(8.dp))
            Button(
                onClick = onSubmit,
                enabled = !submitting && !removing,
                shape = barButtonShape,
                contentPadding = barButtonPadding,
                modifier = Modifier.height(barButtonHeight),
            ) {
                Text(if (submitting) "提交中..." else "下载", style = MaterialTheme.typography.bodyMedium)
            }
        }
    }
    if (confirmRemove) {
        AlertDialog(
            onDismissRequest = { confirmRemove = false },
            title = { Text("删除视频") },
            text = { Text("删除选中的 $count 个视频？删除后它们将不再出现在任何列表中。") },
            confirmButton = {
                TextButton(onClick = { confirmRemove = false; onRemove() }) { Text("删除") }
            },
            dismissButton = {
                TextButton(onClick = { confirmRemove = false }) { Text("取消") }
            },
        )
    }
}

/** Deletes each url on the server; returns the ones deleted and the summary message. */
suspend fun removeUrls(
    api: ResolverApi, baseUrl: String, apiKey: String, urls: List<String>,
): Pair<Set<String>, String> {
    val removed = mutableSetOf<String>()
    var lastError: String? = null
    for (url in urls) {
        try {
            api.removeVideo(baseUrl, apiKey, url)
            removed += url
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            lastError = e.message ?: "删除失败"
        }
    }
    val fail = urls.size - removed.size
    val msg = if (fail == 0) "已删除 ${removed.size} 个" else "已删除 ${removed.size} 个，失败 $fail 个：$lastError"
    return removed to msg
}

/** Submits each url for remote download; returns the summary message and whether all succeeded. */
suspend fun submitUrls(
    api: ResolverApi, baseUrl: String, apiKey: String, history: HistoryStore, urls: List<String>,
): Pair<String, Boolean> {
    var ok = 0
    var fail = 0
    for (url in urls) {
        when (val result = api.resolve(baseUrl, apiKey, url)) {
            is ResolveResult.Success -> {
                ok++
                history.add(HistoryEntry(url, result.outputName, true, "已提交", System.currentTimeMillis()))
            }
            is ResolveResult.Failure -> {
                fail++
                history.add(HistoryEntry(url, null, false, result.message, System.currentTimeMillis()))
            }
        }
    }
    val msg = if (fail == 0) "已提交 $ok 个" else "已提交 $ok 个，失败 $fail 个"
    return msg to (fail == 0)
}

@Composable
private fun SearchBarButton(text: String, onClick: () -> Unit) {
    Surface(
        modifier = Modifier
            .fillMaxHeight()
            .clickable(onClick = onClick),
        shape = barButtonShape,
        color = Color(0xFF616161),
    ) {
        Box(
            modifier = Modifier.padding(horizontal = 10.dp),
            contentAlignment = Alignment.Center,
        ) {
            Text(text, color = Color.White, style = MaterialTheme.typography.bodyMedium)
        }
    }
}

// Same labels and colours as MissAV's own cards, which is how it tells a
// code's pages (ipx-771 / ipx-771-uncensored-leak ...) apart.
private val chineseSubtitleColor = Color(0xFF991B1B)
private val uncensoredLeakColor = Color(0xFF1E40AF)

@Composable
fun VersionBadges(
    hasChineseSubtitle: Boolean?,
    isUncensoredLeak: Boolean?,
    modifier: Modifier = Modifier,
) {
    if (hasChineseSubtitle != true && isUncensoredLeak != true) return
    Row(modifier = modifier, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        if (hasChineseSubtitle == true) VersionBadge("中文字幕", chineseSubtitleColor)
        if (isUncensoredLeak == true) VersionBadge("无码影片", uncensoredLeakColor)
    }
}

@Composable
private fun VersionBadge(text: String, color: Color) {
    Text(
        text,
        color = Color.White,
        style = MaterialTheme.typography.labelSmall,
        modifier = Modifier
            .background(color.copy(alpha = 0.75f), RoundedCornerShape(4.dp))
            .padding(horizontal = 4.dp, vertical = 1.dp),
    )
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun VideoCard(
    video: BrowseVideo,
    thumbUrl: String,
    isSelected: Boolean,
    onToggle: () -> Unit,
    onLongPress: () -> Unit,
) {
    Card(
        shape = RectangleShape,
        modifier = Modifier
            .padding(1.dp)
            .then(
                if (isSelected) {
                    Modifier.border(2.dp, MaterialTheme.colorScheme.primary, RectangleShape)
                } else {
                    Modifier
                },
            )
            .combinedClickable(onClick = onToggle, onLongClick = onLongPress),
    ) {
        Column {
            Box {
                AsyncImage(
                    model = thumbUrl,
                    contentDescription = video.title,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier
                        .fillMaxWidth()
                        .aspectRatio(16f / 9f),
                )
                VersionBadges(
                    hasChineseSubtitle = video.hasChineseSubtitle,
                    isUncensoredLeak = video.isUncensoredLeak,
                    modifier = Modifier.align(Alignment.BottomStart).padding(4.dp),
                )
                if (video.duration.isNotEmpty()) {
                    Text(
                        video.duration,
                        color = androidx.compose.ui.graphics.Color.White,
                        style = MaterialTheme.typography.labelSmall,
                        modifier = Modifier
                            .align(Alignment.BottomEnd)
                            .padding(4.dp)
                            .background(
                                androidx.compose.ui.graphics.Color.Black.copy(alpha = 0.6f),
                                RoundedCornerShape(4.dp),
                            )
                            .padding(horizontal = 4.dp, vertical = 1.dp),
                    )
                }
                if (isSelected) {
                    Box(
                        modifier = Modifier
                            .align(Alignment.TopStart)
                            .padding(4.dp)
                            .background(MaterialTheme.colorScheme.primary, RoundedCornerShape(50)),
                    ) {
                        Icon(
                            Icons.Default.Check,
                            contentDescription = "已选",
                            tint = androidx.compose.ui.graphics.Color.White,
                            modifier = Modifier.padding(4.dp).size(14.dp),
                        )
                    }
                }
            }
            Text(
                video.title,
                fontWeight = FontWeight.Medium,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(8.dp),
            )
        }
    }
}
