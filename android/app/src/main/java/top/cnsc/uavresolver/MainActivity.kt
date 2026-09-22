package top.cnsc.uavresolver

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = Prefs(this)
        val history = HistoryStore(this)
        val api = ResolverApi()
        val initialUrl = extractSharedUrl(intent)

        setContent {
            AppRoot(prefs, history, api, initialUrl)
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
fun AppRoot(prefs: Prefs, history: HistoryStore, api: ResolverApi, initialUrl: String) {
    MaterialTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            var urlText by remember { mutableStateOf(initialUrl) }
            var baseUrl by remember { mutableStateOf(prefs.baseUrl) }
            var apiKey by remember { mutableStateOf(prefs.apiKey) }
            var showSettings by remember { mutableStateOf(baseUrl.isEmpty()) }
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
                    showSettings = true
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
                                HistoryEntry(u, result.outputName, true, "已提交", System.currentTimeMillis())
                            )
                        }
                        is ResolveResult.Failure -> {
                            statusIsError = true
                            statusText = "失败：${result.message}"
                            history.add(
                                HistoryEntry(u, null, false, result.message, System.currentTimeMillis())
                            )
                        }
                    }
                    historyList = history.all()
                    urlText = ""
                }
            }

            Scaffold(
                topBar = {
                    TopAppBar(
                        title = { Text("UAV 远端下载") },
                        actions = {
                            IconButton(onClick = { showSettings = !showSettings }) {
                                Icon(Icons.Default.Settings, contentDescription = "设置")
                            }
                        }
                    )
                }
            ) { padding ->
                Column(
                    modifier = Modifier
                        .padding(padding)
                        .padding(16.dp)
                        .fillMaxSize()
                ) {
                    if (showSettings) {
                        Card(modifier = Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(12.dp)) {
                                Text("解析服务设置", fontWeight = FontWeight.Bold)
                                Spacer(Modifier.height(8.dp))
                                OutlinedTextField(
                                    value = baseUrl,
                                    onValueChange = { baseUrl = it },
                                    label = { Text("解析服务地址，如 http://s.cnsc.top:38060") },
                                    singleLine = true,
                                    modifier = Modifier.fillMaxWidth()
                                )
                                Spacer(Modifier.height(8.dp))
                                OutlinedTextField(
                                    value = apiKey,
                                    onValueChange = { apiKey = it },
                                    label = { Text("API Key") },
                                    singleLine = true,
                                    modifier = Modifier.fillMaxWidth()
                                )
                                Spacer(Modifier.height(8.dp))
                                Button(onClick = {
                                    prefs.baseUrl = baseUrl
                                    prefs.apiKey = apiKey
                                    showSettings = false
                                }) { Text("保存") }
                            }
                        }
                        Spacer(Modifier.height(16.dp))
                    }

                    OutlinedTextField(
                        value = urlText,
                        onValueChange = { urlText = it },
                        label = { Text("视频网址") },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                        modifier = Modifier.fillMaxWidth()
                    )
                    Spacer(Modifier.height(8.dp))
                    Button(
                        onClick = { submit() },
                        enabled = !submitting,
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Text(if (submitting) "提交中..." else "解析并提交下载")
                    }

                    statusText?.let {
                        Spacer(Modifier.height(8.dp))
                        Text(
                            it,
                            color = if (statusIsError) MaterialTheme.colorScheme.error
                                    else MaterialTheme.colorScheme.primary
                        )
                    }

                    Spacer(Modifier.height(16.dp))
                    Text("历史记录", fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(4.dp))
                    val fmt = remember { SimpleDateFormat("MM-dd HH:mm", Locale.getDefault()) }
                    LazyColumn(modifier = Modifier.weight(1f)) {
                        items(historyList) { entry ->
                            Card(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(vertical = 4.dp)
                            ) {
                                Column(Modifier.padding(10.dp)) {
                                    Text(entry.outputName ?: entry.url, fontWeight = FontWeight.Medium)
                                    Text(entry.url, style = MaterialTheme.typography.bodySmall)
                                    Text(
                                        "${fmt.format(Date(entry.timestamp))} · " +
                                            if (entry.success) "成功" else "失败：${entry.message}",
                                        color = if (entry.success) MaterialTheme.colorScheme.primary
                                                else MaterialTheme.colorScheme.error,
                                        style = MaterialTheme.typography.bodySmall
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
