package top.cnsc.uavresolver

import android.content.Intent
import android.graphics.Color as AndroidColor
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.core.view.WindowCompat
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        window.statusBarColor = AndroidColor.TRANSPARENT
        WindowCompat.getInsetsController(window, window.decorView).isAppearanceLightStatusBars = true
        val prefs = Prefs(this)
        val history = HistoryStore(this)
        val categoryCache = CategoryCache(this)
        val api = ResolverApi()
        val initialUrl = extractSharedUrl(intent)

        setContent {
            AppRoot(prefs, history, categoryCache, api, initialUrl)
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        recreate()
    }

    private fun extractSharedUrl(intent: Intent?): String {
        if (intent?.action != Intent.ACTION_SEND) return ""
        val text = intent.getStringExtra(Intent.EXTRA_TEXT) ?: return ""
        val match = Regex("""https?://\S+""").find(text)
        return (match?.value ?: text).trim()
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppRoot(
    prefs: Prefs,
    history: HistoryStore,
    categoryCache: CategoryCache,
    api: ResolverApi,
    initialUrl: String,
) {
    MaterialTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            var showHistory by remember { mutableStateOf(initialUrl.isNotEmpty()) }
            var baseUrl by remember { mutableStateOf(prefs.baseUrl) }
            var apiKey by remember { mutableStateOf(prefs.apiKey) }
            var showSettings by remember { mutableStateOf(baseUrl.isEmpty()) }
            var refreshingCategories by remember { mutableStateOf(false) }
            var fullscreenVideo by remember { mutableStateOf<VideoDetail?>(null) }
            val snackbarHostState = remember { SnackbarHostState() }
            val scope = rememberCoroutineScope()

            fun refreshCategories() {
                if (baseUrl.isEmpty() || apiKey.isEmpty()) {
                    scope.launch { snackbarHostState.showSnackbar("请先填写解析服务地址和 API Key") }
                    return
                }
                scope.launch {
                    refreshingCategories = true
                    var ok = 0
                    var fail = 0
                    for (site in listOf("jabletv", "missav")) {
                        try {
                            val fresh = api.listCategories(baseUrl, apiKey, site, refresh = true)
                            categoryCache.put(site, fresh)
                            ok++
                        } catch (e: Exception) {
                            fail++
                        }
                    }
                    refreshingCategories = false
                    val msg = if (fail == 0) "分类缓存已刷新" else "刷新完成，$fail 个站点失败"
                    snackbarHostState.showSnackbar(msg)
                }
            }

            Box(modifier = Modifier.fillMaxSize()) {
            Scaffold(
                snackbarHost = { SnackbarHost(snackbarHostState) },
            ) { padding ->
                Column(
                    modifier = Modifier
                        .padding(padding)
                        .fillMaxSize(),
                ) {
                    Box(modifier = Modifier.weight(1f).fillMaxWidth()) {
                        BrowseScreen(
                            baseUrl = baseUrl,
                            apiKey = apiKey,
                            api = api,
                            history = history,
                            categoryCache = categoryCache,
                            onToggleSettings = { showSettings = true },
                            onOpenHistory = { showHistory = true },
                            onSubmitResult = { msg, _ ->
                                scope.launch { snackbarHostState.showSnackbar(msg) }
                            },
                            onPlayVideo = { fullscreenVideo = it },
                            isFullscreenActive = fullscreenVideo != null,
                        )

                        // These stay composed permanently (just resized to nothing
                        // when hidden) instead of being torn down via `if`, so
                        // opening one again keeps whatever was typed/scrolled
                        // exactly as it was. Each needs its own opaque background
                        // since it overlays Browse instead of swapping places with it.
                        Surface(
                            modifier = if (showHistory) Modifier.fillMaxSize() else Modifier.size(0.dp),
                            color = MaterialTheme.colorScheme.background,
                        ) {
                            HistoryScreen(
                                visible = showHistory,
                                prefs = prefs,
                                history = history,
                                api = api,
                                initialUrl = initialUrl,
                                baseUrl = baseUrl,
                                apiKey = apiKey,
                                onDismiss = { showHistory = false },
                                onNeedSettings = { showSettings = true },
                            )
                        }

                        Surface(
                            modifier = if (showSettings) Modifier.fillMaxSize() else Modifier.size(0.dp),
                            color = MaterialTheme.colorScheme.background,
                        ) {
                            SettingsScreen(
                                visible = showSettings,
                                baseUrl = baseUrl,
                                apiKey = apiKey,
                                onBaseUrlChange = { baseUrl = it },
                                onApiKeyChange = { apiKey = it },
                                onSave = {
                                    prefs.baseUrl = baseUrl
                                    prefs.apiKey = apiKey
                                    showSettings = false
                                },
                                onDismiss = { showSettings = false },
                                refreshing = refreshingCategories,
                                onRefreshCategories = { refreshCategories() },
                            )
                        }
                    }
                }
            }

            fullscreenVideo?.let { video ->
                FullscreenPlayer(video = video, onClose = { fullscreenVideo = null })
            }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    visible: Boolean,
    baseUrl: String,
    apiKey: String,
    onBaseUrlChange: (String) -> Unit,
    onApiKeyChange: (String) -> Unit,
    onSave: () -> Unit,
    onDismiss: () -> Unit,
    refreshing: Boolean,
    onRefreshCategories: () -> Unit,
) {
    BackHandler(enabled = visible, onBack = onDismiss)

    Column(
        modifier = Modifier
            .padding(16.dp)
            .fillMaxSize(),
    ) {
        Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onDismiss, modifier = Modifier.size(32.dp)) {
                Icon(Icons.Default.ArrowBack, contentDescription = "返回", modifier = Modifier.size(20.dp))
            }
            Text("解析服务设置", fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f).padding(start = 8.dp))
        }
        Spacer(Modifier.height(16.dp))
        OutlinedTextField(
            value = baseUrl,
            onValueChange = onBaseUrlChange,
            label = { Text("解析服务地址，如 http://s.cnsc.top:38060") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = apiKey,
            onValueChange = onApiKeyChange,
            label = { Text("API Key") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Ascii),
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(16.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Button(onClick = onSave, shape = RoundedCornerShape(8.dp)) { Text("保存") }
            Spacer(Modifier.width(8.dp))
            OutlinedButton(
                onClick = onRefreshCategories,
                enabled = !refreshing,
                shape = RoundedCornerShape(8.dp),
            ) {
                Text(if (refreshing) "刷新中..." else "刷新分类")
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HistoryScreen(
    visible: Boolean,
    prefs: Prefs,
    history: HistoryStore,
    api: ResolverApi,
    initialUrl: String,
    baseUrl: String,
    apiKey: String,
    onDismiss: () -> Unit,
    onNeedSettings: () -> Unit,
) {
    BackHandler(enabled = visible, onBack = onDismiss)

    var urlText by remember { mutableStateOf(initialUrl) }
    var statusText by remember { mutableStateOf<String?>(null) }
    var statusIsError by remember { mutableStateOf(false) }
    var submitting by remember { mutableStateOf(false) }
    var historyList by remember { mutableStateOf(history.all()) }
    val scope = rememberCoroutineScope()

    fun submit() {
        val u = urlText.trim()
        if (u.isEmpty()) {
            statusText = "请输入或分享一个视频网址"
            statusIsError = true
            return
        }
        if (baseUrl.isEmpty() || apiKey.isEmpty()) {
            statusText = "请先在设置里填写解析服务地址和 API Key"
            statusIsError = true
            onNeedSettings()
            return
        }
        submitting = true
        statusText = null
        scope.launch {
            val result = api.resolve(baseUrl, apiKey, u)
            submitting = false
            when (result) {
                is ResolveResult.Success -> {
                    statusIsError = false
                    statusText = "已提交远端下载：${result.outputName}"
                    history.add(
                        HistoryEntry(u, result.outputName, true, "已提交", System.currentTimeMillis()),
                    )
                }
                is ResolveResult.Failure -> {
                    statusIsError = true
                    statusText = "失败：${result.message}"
                    history.add(
                        HistoryEntry(u, null, false, result.message, System.currentTimeMillis()),
                    )
                }
            }
            historyList = history.all()
            urlText = ""
        }
    }

    Column(
        modifier = Modifier
            .padding(16.dp)
            .fillMaxSize(),
    ) {
        Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onDismiss, modifier = Modifier.size(32.dp)) {
                Icon(Icons.Default.ArrowBack, contentDescription = "返回", modifier = Modifier.size(20.dp))
            }
            Text("历史记录", fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f).padding(start = 8.dp))
        }
        OutlinedTextField(
            value = urlText,
            onValueChange = { urlText = it },
            label = { Text("视频网址") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Done),
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        Button(
            onClick = { submit() },
            enabled = !submitting,
            shape = RoundedCornerShape(8.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(if (submitting) "提交中..." else "解析并提交下载")
        }

        statusText?.let {
            Spacer(Modifier.height(8.dp))
            Text(
                it,
                color = if (statusIsError) MaterialTheme.colorScheme.error
                        else MaterialTheme.colorScheme.primary,
            )
        }

        Spacer(Modifier.height(16.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.End,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (historyList.isNotEmpty()) {
                IconButton(
                    onClick = {
                        history.clear()
                        historyList = history.all()
                    },
                    modifier = Modifier.size(32.dp),
                ) {
                    Icon(
                        Icons.Default.Delete,
                        contentDescription = "清除历史记录",
                        modifier = Modifier.size(20.dp),
                    )
                }
            }
        }
        Spacer(Modifier.height(4.dp))
        val fmt = remember { SimpleDateFormat("MM-dd HH:mm", Locale.getDefault()) }
        LazyColumn(modifier = Modifier.weight(1f)) {
            items(historyList) { entry ->
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 4.dp),
                ) {
                    Column(Modifier.padding(10.dp)) {
                        Text(entry.outputName ?: entry.url, fontWeight = FontWeight.Medium)
                        Text(entry.url, style = MaterialTheme.typography.bodySmall)
                        Text(
                            "${fmt.format(Date(entry.timestamp))} · " +
                                if (entry.success) "成功" else "失败：${entry.message}",
                            color = if (entry.success) MaterialTheme.colorScheme.primary
                                    else MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                }
            }
        }
    }
}
