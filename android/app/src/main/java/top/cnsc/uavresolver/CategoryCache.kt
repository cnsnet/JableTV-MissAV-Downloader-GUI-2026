package top.cnsc.uavresolver

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/** Category/tag lists barely change, so cache them locally and skip the
 * network entirely for a month instead of re-fetching on every Browse-tab
 * open. Mirrors the resolver's own server-side cache (see resolver/app.py);
 * this one just means the app doesn't even need a network round-trip. */
class CategoryCache(context: Context) {
    private val sp = context.getSharedPreferences("uav_category_cache", Context.MODE_PRIVATE)
    private val ttlMillis = 30L * 24 * 60 * 60 * 1000

    fun get(site: String): List<BrowseCategory>? {
        val ts = sp.getLong("ts_$site", 0L)
        if (ts == 0L || System.currentTimeMillis() - ts > ttlMillis) return null
        val json = sp.getString("data_$site", null) ?: return null
        return try {
            val arr = JSONArray(json)
            (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                BrowseCategory(
                    name = o.optString("name"),
                    url = o.optString("url"),
                    count = o.optInt("count", 0),
                    section = o.optBoolean("section", false),
                    group = o.optString("group"),
                )
            }
        } catch (e: Exception) {
            null
        }
    }

    fun put(site: String, categories: List<BrowseCategory>) {
        val arr = JSONArray()
        categories.forEach { c ->
            arr.put(
                JSONObject().apply {
                    put("name", c.name)
                    put("url", c.url)
                    put("count", c.count)
                    put("section", c.section)
                    put("group", c.group)
                },
            )
        }
        sp.edit()
            .putString("data_$site", arr.toString())
            .putLong("ts_$site", System.currentTimeMillis())
            .apply()
    }

    fun clear() {
        sp.edit().clear().apply()
    }
}
