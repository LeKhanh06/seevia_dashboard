package com.youreyes.app.stats

import org.json.JSONArray
import org.json.JSONObject

/**
 * `GET /api/v1/me/stats` (AB `api/me_stats.py`): the signed-in glasses owner's own usage for a range of
 * Vietnam days. Rates are percentages 0–100 (one decimal) or null when there is nothing to divide by.
 * No names, numbers or content: only counts and durations.
 */
data class MyStats(
    val from: String,
    val to: String,
    val totals: StatsTotals,
    val features: List<FeatureStat>,
    val byDay: List<DayUses>,
)

data class StatsTotals(
    val uses: Int,
    val completionRate: Double?,
    val firstTryRate: Double?,
    val avgRetries: Double?,
    val usageSeconds: Long,
    val connectionSeconds: Long,
    val alertModeSeconds: Long,
    val activeDays: Int,
    val internetCalls: Int,
    val internetCallsAnswered: Int,
    val sos: Int,
)

/** One dashboard feature key (`ocr`, `money`, `family`, `alert_mode`, ...); [completionRate] null for non-tasks. */
data class FeatureStat(val key: String, val uses: Int, val completionRate: Double?)

/** [date] is `yyyy-MM-dd`; [uses] is null for a day that has not happened yet. */
data class DayUses(val date: String, val uses: Int?)

private fun JSONObject.doubleOrNull(key: String): Double? =
    if (!has(key) || isNull(key)) null else optDouble(key).takeUnless { it.isNaN() }

private fun JSONObject.intOrNull(key: String): Int? = if (!has(key) || isNull(key)) null else optInt(key)

private fun JSONObject.count(key: String): Int = intOrNull(key) ?: 0

private fun JSONObject.seconds(key: String): Long = if (!has(key) || isNull(key)) 0L else optLong(key)

private fun <T> JSONArray?.mapObjects(transform: (JSONObject) -> T): List<T> =
    if (this == null) emptyList() else (0 until length()).map { transform(getJSONObject(it)) }

/** Tolerant reader: a missing total counts as 0 / null rather than failing the whole screen. */
fun parseMyStats(json: JSONObject): MyStats {
    val range = json.optJSONObject("range") ?: JSONObject()
    val t = json.optJSONObject("totals") ?: JSONObject()
    return MyStats(
        from = range.optString("from"),
        to = range.optString("to"),
        totals = StatsTotals(
            uses = t.count("uses"),
            completionRate = t.doubleOrNull("completion_rate"),
            firstTryRate = t.doubleOrNull("first_try_rate"),
            avgRetries = t.doubleOrNull("avg_retries"),
            usageSeconds = t.seconds("usage_seconds"),
            connectionSeconds = t.seconds("connection_seconds"),
            alertModeSeconds = t.seconds("alert_mode_seconds"),
            activeDays = t.count("active_days"),
            internetCalls = t.count("internet_calls"),
            internetCallsAnswered = t.count("internet_calls_answered"),
            sos = t.count("sos"),
        ),
        features = json.optJSONArray("features").mapObjects {
            FeatureStat(it.getString("key"), it.count("uses"), it.doubleOrNull("completion_rate"))
        },
        byDay = json.optJSONArray("by_day").mapObjects { DayUses(it.getString("date"), it.intOrNull("uses")) },
    )
}
