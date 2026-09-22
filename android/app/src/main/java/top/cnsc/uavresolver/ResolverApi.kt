package top.cnsc.uavresolver

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

sealed class ResolveResult {
    data class Success(val outputName: String, val resolvedUrl: String) : ResolveResult()
    data class Failure(val message: String) : ResolveResult()
}

class ResolverApi {
    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    suspend fun resolve(baseUrl: String, apiKey: String, url: String): ResolveResult =
        withContext(Dispatchers.IO) {
            try {
                val requestJson = JSONObject().put("url", url).toString()
                val body = requestJson.toRequestBody("application/json".toMediaType())
                val request = Request.Builder()
                    .url("${baseUrl.trimEnd('/')}/api/resolve")
                    .addHeader("X-API-Key", apiKey)
                    .post(body)
                    .build()
                client.newCall(request).execute().use { resp ->
                    val text = resp.body?.string().orEmpty()
                    if (resp.isSuccessful) {
                        val json = JSONObject(text)
                        ResolveResult.Success(
                            outputName = json.optString("output_name"),
                            resolvedUrl = json.optString("resolved_url"),
                        )
                    } else {
                        val detail = try {
                            JSONObject(text).optString("detail", text)
                        } catch (e: Exception) {
                            text.ifEmpty { "HTTP ${resp.code}" }
                        }
                        ResolveResult.Failure("HTTP ${resp.code}: $detail")
                    }
                }
            } catch (e: Exception) {
                ResolveResult.Failure(e.message ?: "network error")
            }
        }
}
