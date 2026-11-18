package com.youreyes.app.stats

import java.util.Calendar
import java.util.Locale
import java.util.TimeZone
import kotlin.math.roundToLong

/**
 * Pure formatting for the "Thống kê sử dụng" screen: period → date range, numbers → Vietnamese text and
 * one spoken sentence per block for TalkBack. No Android types, so it runs in plain JVM unit tests.
 */

/** Statistics days are Vietnam calendar days (server default `Asia/Ho_Chi_Minh`). */
val VIETNAM: TimeZone = TimeZone.getTimeZone("Asia/Ho_Chi_Minh")

enum class StatsPeriod(val days: Int, val label: String, val spokenRange: String) {
    WEEK(7, "7 ngày", "7 ngày qua"),
    MONTH(30, "30 ngày", "30 ngày qua"),
}

/** Inclusive `yyyy-MM-dd` range ending today (Vietnam) and covering [period] days. */
fun statsRange(period: StatsPeriod, nowMillis: Long): Pair<String, String> {
    val cal = Calendar.getInstance(VIETNAM, Locale.US).apply { timeInMillis = nowMillis }
    val to = isoDay(cal)
    cal.add(Calendar.DAY_OF_MONTH, -(period.days - 1))
    return isoDay(cal) to to
}

private fun isoDay(cal: Calendar): String = String.format(
    Locale.US, "%04d-%02d-%02d",
    cal.get(Calendar.YEAR), cal.get(Calendar.MONTH) + 1, cal.get(Calendar.DAY_OF_MONTH),
)

/** Dashboard feature keys (AB `services/admin_stats.py` GROUPS) → what the owner calls them. */
val FEATURE_LABELS: Map<String, String> = linkedMapOf(
    "ocr" to "Đọc chữ",
    "ocr_live" to "Đọc chữ có căn khung",
    "money" to "Nhận diện tiền",
    "ask" to "Hỏi về hình ảnh",
    "find" to "Tìm đồ vật",
    "space" to "Mô tả xung quanh",
    "hazard" to "Hỏi vật cản",
    "chat" to "Trò chuyện, tra cứu",
    "family" to "Gọi, nhắn người thân",
    "internet_call" to "Cuộc gọi đến kính",
    "music" to "Nghe nhạc",
    "directions" to "Dẫn đường",
    "ride" to "Đặt xe",
    "alert_mode" to "Cảnh báo vật cản liên tục",
)

fun featureLabel(key: String): String = FEATURE_LABELS[key] ?: key

/** `87.5` → "87,5%", `100.0` → "100%", null → "—" (shown) ; spoken form via [spokenPercent]. */
fun formatPercent(value: Double?): String {
    if (value == null) return "—"
    val tenths = (value * 10).roundToLong()
    return if (tenths % 10 == 0L) "${tenths / 10}%" else "${tenths / 10},${tenths % 10}%"
}

fun spokenPercent(value: Double?): String =
    if (value == null) "chưa có số liệu" else formatPercent(value).removeSuffix("%") + " phần trăm"

/** Seconds → "dưới 1 phút", "45 phút", "2 giờ", "2 giờ 5 phút"; 0 → "0 phút". */
fun formatDuration(seconds: Long): String {
    if (seconds <= 0L) return "0 phút"
    if (seconds < 60L) return "dưới 1 phút"
    val minutes = seconds / 60
    val hours = minutes / 60
    val rest = minutes % 60
    return when {
        hours == 0L -> "$minutes phút"
        rest == 0L -> "$hours giờ"
        else -> "$hours giờ $rest phút"
    }
}

/** `2026-11-18` → "18/11". */
fun shortDay(isoDate: String): String {
    val parts = isoDate.split("-")
    return if (parts.size == 3) "${parts[2].trimStart('0')}/${parts[1].trimStart('0')}" else isoDate
}

/** `2026-11-18` → "ngày 18 tháng 11". */
fun spokenDay(isoDate: String): String {
    val parts = isoDate.split("-")
    return if (parts.size == 3) "ngày ${parts[2].trimStart('0')} tháng ${parts[1].trimStart('0')}" else isoDate
}

data class StatTile(val label: String, val value: String, val spoken: String)

data class DayBar(val label: String, val uses: Int?, val spoken: String)

data class FeatureRow(val label: String, val usesText: String, val rateText: String?, val spoken: String)

data class StatsContent(
    /** One sentence read first: the whole period at a glance. */
    val summary: String,
    val tiles: List<StatTile>,
    val bars: List<DayBar>,
    /** What the chart shows, for TalkBack (the bars themselves are not separate focus stops). */
    val chartSummary: String,
    val features: List<FeatureRow>,
    val isEmpty: Boolean,
)

fun buildStatsContent(stats: MyStats, period: StatsPeriod): StatsContent {
    val t = stats.totals
    val calls = if (t.internetCalls == 0) "0" else "${t.internetCallsAnswered}/${t.internetCalls}"
    val tiles = listOf(
        StatTile("Lượt dùng", t.uses.toString(), "Lượt dùng: ${t.uses}"),
        StatTile("Hoàn thành", formatPercent(t.completionRate), "Hoàn thành: ${spokenPercent(t.completionRate)}"),
        StatTile(
            "Làm được ngay lần đầu", formatPercent(t.firstTryRate),
            "Làm được ngay lần đầu: ${spokenPercent(t.firstTryRate)}",
        ),
        StatTile(
            "Thử lại trung bình", t.avgRetries?.let { "${formatDecimal(it)} lần" } ?: "—",
            t.avgRetries?.let { "Trung bình mỗi việc phải thử lại ${formatDecimal(it)} lần" }
                ?: "Thử lại trung bình: chưa có số liệu",
        ),
        StatTile("Ngày có dùng", "${t.activeDays}/${period.days}", "Có dùng ${t.activeDays} trên ${period.days} ngày"),
        StatTile("Thời gian dùng", formatDuration(t.usageSeconds), "Thời gian dùng: ${formatDuration(t.usageSeconds)}"),
        StatTile(
            "Kính kết nối", formatDuration(t.connectionSeconds),
            "Kính kết nối tổng cộng ${formatDuration(t.connectionSeconds)}",
        ),
        StatTile(
            "Cảnh báo vật cản", formatDuration(t.alertModeSeconds),
            "Bật cảnh báo vật cản ${formatDuration(t.alertModeSeconds)}",
        ),
        StatTile(
            "Gọi người thân (có người nghe)", calls,
            if (t.internetCalls == 0) "Chưa có cuộc gọi người thân nào"
            else "${t.internetCalls} cuộc gọi người thân, ${t.internetCallsAnswered} cuộc có người nghe",
        ),
        StatTile("Khẩn cấp (SOS)", t.sos.toString(), "Khẩn cấp: ${t.sos} lần"),
    )

    val bars = stats.byDay.map { day ->
        DayBar(
            label = shortDay(day.date),
            uses = day.uses,
            spoken = if (day.uses == null) "${spokenDay(day.date)}: chưa tới" else "${spokenDay(day.date)}: ${day.uses} lượt",
        )
    }
    val counted = stats.byDay.filter { it.uses != null }
    val busiest = counted.maxByOrNull { it.uses ?: 0 }
    val chartSummary = when {
        busiest == null || (busiest.uses ?: 0) == 0 -> "Biểu đồ theo ngày: chưa có lượt nào trong ${period.spokenRange}."
        else -> "Biểu đồ theo ngày: nhiều nhất ${spokenDay(busiest.date)} với ${busiest.uses} lượt; " +
            "trung bình ${formatAverage(counted.sumOf { it.uses ?: 0 }, counted.size)} lượt mỗi ngày."
    }

    val features = stats.features.sortedByDescending { it.uses }.map { f ->
        val label = featureLabel(f.key)
        val rate = f.completionRate?.let { "Hoàn thành ${formatPercent(it)}" }
        FeatureRow(
            label = label,
            usesText = "${f.uses} lượt",
            rateText = rate,
            spoken = "$label: ${f.uses} lượt" + (f.completionRate?.let { ", hoàn thành ${spokenPercent(it)}" } ?: ""),
        )
    }

    val summary = if (t.uses == 0) {
        "Trong ${period.spokenRange} chưa có lượt dùng kính nào được ghi lại."
    } else {
        "Trong ${period.spokenRange} bạn dùng kính ${t.uses} lượt, có dùng ${t.activeDays} ngày" +
            (t.completionRate?.let { ", hoàn thành ${spokenPercent(it)}" } ?: "") + "."
    }
    return StatsContent(summary, tiles, bars, chartSummary, features, isEmpty = t.uses == 0)
}

/** 17 over 7 days → "2,4". */
private fun formatAverage(total: Int, days: Int): String = if (days == 0) "0" else formatDecimal(total.toDouble() / days)

/** One decimal, Vietnamese comma, no trailing ",0": 0.25 → "0,3", 2.0 → "2". */
fun formatDecimal(value: Double): String {
    val tenths = (value * 10).roundToLong()
    return if (tenths % 10 == 0L) "${tenths / 10}" else "${tenths / 10},${tenths % 10}"
}
