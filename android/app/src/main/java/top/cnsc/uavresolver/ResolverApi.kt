package top.cnsc.uavresolver

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

sealed class ResolveResult {
    data class Success(val outputName: String, val resolvedUrl: String) : ResolveResult()
    data class Failure(val message: String) : ResolveResult()
}

class ResolverException(message: String) : Exception(message)

data class SiteInfo(val key: String, val name: String)

data class BrowseCategory(
    val name: String,
    val url: String,
    val count: Int,
    val section: Boolean,
    val group: String = "",
)

data class BrowseVideo(
    val url: String,
    val title: String,
    val thumbnail: String,
    val duration: String,
)

data class VideoDetail(
    val title: String,
    val id: String,
    val description: String,
    val thumbnail: String,
    val resolvedUrl: String,
    val headers: Map<String, String>,
)

sealed class DetailResult {
    data class Success(val detail: VideoDetail) : DetailResult()
    data class Failure(val message: String) : DetailResult()
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
                        ResolveResult.Failure("HTTP ${resp.code}: ${extractDetail(text, resp.code)}")
                    }
                }
            } catch (e: Exception) {
                ResolveResult.Failure(e.message ?: "network error")
            }
        }

    // Submits an already-resolved URL (e.g. from a prior detail() call)
    // straight to the download queue, skipping the resolver's scrape step.
    suspend fun download(baseUrl: String, apiKey: String, resolvedUrl: String, outputName: String): ResolveResult =
        withContext(Dispatchers.IO) {
            try {
                val requestJson = JSONObject()
                    .put("resolved_url", resolvedUrl)
                    .put("output_name", outputName)
                    .toString()
                val body = requestJson.toRequestBody("application/json".toMediaType())
                val request = Request.Builder()
                    .url("${baseUrl.trimEnd('/')}/api/download")
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
                        ResolveResult.Failure("HTTP ${resp.code}: ${extractDetail(text, resp.code)}")
                    }
                }
            } catch (e: Exception) {
                ResolveResult.Failure(e.message ?: "network error")
            }
        }

    suspend fun detail(baseUrl: String, apiKey: String, url: String): DetailResult =
        withContext(Dispatchers.IO) {
            try {
                val httpUrl = "${baseUrl.trimEnd('/')}/api/detail".toHttpUrl().newBuilder()
                    .addQueryParameter("url", url)
                    .build()
                val text = executeGet(httpUrl, apiKey)
                val json = JSONObject(text)
                val headersJson = json.optJSONObject("headers")
                val headers = mutableMapOf<String, String>()
                headersJson?.keys()?.forEach { key -> headers[key] = headersJson.optString(key) }
                DetailResult.Success(
                    VideoDetail(
                        title = json.optString("title"),
                        id = json.optString("id"),
                        description = json.optString("description"),
                        thumbnail = json.optString("thumbnail"),
                        resolvedUrl = json.optString("resolved_url"),
                        headers = headers,
                    )
                )
            } catch (e: Exception) {
                DetailResult.Failure(e.message ?: "network error")
            }
        }

    suspend fun listSites(baseUrl: String, apiKey: String): List<SiteInfo> =
        withContext(Dispatchers.IO) {
            val url = "${baseUrl.trimEnd('/')}/api/browse/sites".toHttpUrl()
            val arr = JSONObject(executeGet(url, apiKey)).getJSONArray("sites")
            (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                SiteInfo(o.optString("key"), o.optString("name"))
            }
        }

    suspend fun listCategories(
        baseUrl: String, apiKey: String, site: String, refresh: Boolean = false,
    ): List<BrowseCategory> = withContext(Dispatchers.IO) {
        val url = "${baseUrl.trimEnd('/')}/api/browse/$site/categories".toHttpUrl().newBuilder()
            .apply { if (refresh) addQueryParameter("refresh", "true") }
            .build()
        parseCategories(executeGet(url, apiKey))
    }

    suspend fun listVideos(
        baseUrl: String, apiKey: String, site: String,
        categoryUrl: String, page: Int,
    ): List<BrowseVideo> = withContext(Dispatchers.IO) {
        val url = "${baseUrl.trimEnd('/')}/api/browse/$site/videos".toHttpUrl().newBuilder()
            .addQueryParameter("category_url", categoryUrl)
            .addQueryParameter("page", page.toString())
            .build()
        parseVideos(executeGet(url, apiKey))
    }

    suspend fun search(
        baseUrl: String, apiKey: String, site: String,
        query: String, page: Int,
    ): List<BrowseVideo> = withContext(Dispatchers.IO) {
        val url = "${baseUrl.trimEnd('/')}/api/browse/$site/search".toHttpUrl().newBuilder()
            .addQueryParameter("q", query)
            .addQueryParameter("page", page.toString())
            .build()
        parseVideos(executeGet(url, apiKey))
    }

    fun thumbUrl(baseUrl: String, apiKey: String, originalUrl: String): String {
        val encodedUrl = URLEncoder.encode(originalUrl, "UTF-8")
        val encodedKey = URLEncoder.encode(apiKey, "UTF-8")
        return "${baseUrl.trimEnd('/')}/api/browse/thumb?url=$encodedUrl&api_key=$encodedKey"
    }

    private fun executeGet(url: okhttp3.HttpUrl, apiKey: String): String {
        val request = Request.Builder().url(url).addHeader("X-API-Key", apiKey).get().build()
        client.newCall(request).execute().use { resp ->
            val text = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) {
                throw ResolverException("HTTP ${resp.code}: ${extractDetail(text, resp.code)}")
            }
            return text
        }
    }

    private fun extractDetail(text: String, code: Int): String = try {
        JSONObject(text).optString("detail", text)
    } catch (e: Exception) {
        text.ifEmpty { "HTTP $code" }
    }

    private fun parseCategories(json: String): List<BrowseCategory> {
        val arr = JSONObject(json).getJSONArray("categories")
        return (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            BrowseCategory(
                name = o.optString("name"),
                url = o.optString("url"),
                count = o.optInt("count", 0),
                section = o.optBoolean("section", false),
                group = o.optString("group"),
            )
        }
    }

    private fun parseVideos(json: String): List<BrowseVideo> {
        val arr = JSONObject(json).getJSONArray("videos")
        return (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            BrowseVideo(
                url = o.optString("url"),
                title = o.optString("title"),
                thumbnail = o.optString("thumbnail"),
                duration = o.optString("duration"),
            )
        }
    }
}
