package top.cnsc.uavresolver

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

data class HistoryEntry(
    val url: String,
    val outputName: String?,
    val success: Boolean,
    val message: String,
    val timestamp: Long,
)

class HistoryStore(context: Context) {
    private val file = File(context.filesDir, "history.json")
    private val maxEntries = 100

    fun all(): List<HistoryEntry> {
        if (!file.exists()) return emptyList()
        return try {
            val arr = JSONArray(file.readText())
            (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                HistoryEntry(
                    url = o.getString("url"),
                    outputName = o.optString("outputName").ifEmpty { null },
                    success = o.getBoolean("success"),
                    message = o.getString("message"),
                    timestamp = o.getLong("timestamp"),
                )
            }.sortedByDescending { it.timestamp }
        } catch (e: Exception) {
            emptyList()
        }
    }

    fun add(entry: HistoryEntry) {
        val current = all().toMutableList()
        current.add(0, entry)
        val trimmed = current.take(maxEntries)
        val arr = JSONArray()
        trimmed.forEach { e ->
            arr.put(JSONObject().apply {
                put("url", e.url)
                put("outputName", e.outputName ?: "")
                put("success", e.success)
                put("message", e.message)
                put("timestamp", e.timestamp)
            })
        }
        file.writeText(arr.toString())
    }

    fun clear() {
        if (file.exists()) file.delete()
    }
}
