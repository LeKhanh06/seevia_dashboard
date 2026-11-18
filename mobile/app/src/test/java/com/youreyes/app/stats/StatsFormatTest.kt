package com.youreyes.app.stats

import com.youreyes.app.network.ApiResult
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class StatsFormatTest {

    // Đúng hình phản hồi của AB `owner_stats` (không có envelope `data`).
    private val sample = """
        {"range":{"from":"2026-11-12","to":"2026-11-18"},
         "totals":{"uses":17,"completion_rate":87.5,"first_try_rate":70.0,"avg_retries":0.3,
                   "usage_seconds":3900,"connection_seconds":7260,"alert_mode_seconds":45,"active_days":5,
                   "internet_calls":4,"internet_calls_answered":3,"sos":1},
         "features":[{"key":"money","uses":3,"completion_rate":100.0},
                     {"key":"ocr","uses":9,"completion_rate":77.8},
                     {"key":"alert_mode","uses":2,"completion_rate":null}],
         "by_day":[{"date":"2026-11-12","uses":0},{"date":"2026-11-13","uses":6},{"date":"2026-11-14","uses":2},
                   {"date":"2026-11-15","uses":3},{"date":"2026-11-16","uses":4},{"date":"2026-11-17","uses":2},
                   {"date":"2026-11-18","uses":null}]}
    """.trimIndent()

    @Test
    fun `parses the bare me-stats object including nulls`() {
        val stats = parseMyStats(JSONObject(sample))
        assertEquals("2026-11-12", stats.from)
        assertEquals(17, stats.totals.uses)
        assertEquals(87.5, stats.totals.completionRate!!, 0.0)
        assertEquals(3, stats.totals.internetCallsAnswered)
        assertNull(stats.features.single { it.key == "alert_mode" }.completionRate)
        assertNull(stats.byDay.last().uses)
    }

    @Test
    fun `empty account parses to zeros and nulls, not a crash`() {
        val stats = parseMyStats(JSONObject("""{"range":{"from":"a","to":"b"},"totals":{"uses":0,"completion_rate":null},"features":[],"by_day":[]}"""))
        assertEquals(0, stats.totals.uses)
        assertNull(stats.totals.completionRate)
        assertEquals(0L, stats.totals.usageSeconds)
        val content = buildStatsContent(stats, StatsPeriod.WEEK)
        assertTrue(content.isEmpty)
        assertEquals("Trong 7 ngày qua chưa có lượt dùng kính nào được ghi lại.", content.summary)
    }

    @Test
    fun `range is inclusive and uses Vietnam days`() {
        // 2026-11-18 17:30 UTC = 2026-11-19 00:30 giờ Việt Nam.
        val now = 1_795_023_000_000L
        assertEquals("2026-11-13" to "2026-11-19", statsRange(StatsPeriod.WEEK, now))
        assertEquals("2026-10-21" to "2026-11-19", statsRange(StatsPeriod.MONTH, now))
    }

    @Test
    fun `percent and duration formats are Vietnamese`() {
        assertEquals("87,5%", formatPercent(87.5))
        assertEquals("100%", formatPercent(100.0))
        assertEquals("—", formatPercent(null))
        assertEquals("chưa có số liệu", spokenPercent(null))
        assertEquals("87,5 phần trăm", spokenPercent(87.5))
        assertEquals("0 phút", formatDuration(0))
        assertEquals("dưới 1 phút", formatDuration(45))
        assertEquals("45 phút", formatDuration(2700))
        assertEquals("2 giờ", formatDuration(7200))
        assertEquals("2 giờ 1 phút", formatDuration(7260))
    }

    @Test
    fun `content gives spoken summary, chart summary and sorted features`() {
        val content = buildStatsContent(parseMyStats(JSONObject(sample)), StatsPeriod.WEEK)
        assertFalse(content.isEmpty)
        assertEquals(
            "Trong 7 ngày qua bạn dùng kính 17 lượt, có dùng 5 ngày, hoàn thành 87,5 phần trăm.",
            content.summary,
        )
        assertEquals(
            "Biểu đồ theo ngày: nhiều nhất ngày 13 tháng 11 với 6 lượt; trung bình 2,8 lượt mỗi ngày.",
            content.chartSummary,
        )
        assertEquals(listOf("Đọc chữ", "Nhận diện tiền", "Cảnh báo vật cản liên tục"), content.features.map { it.label })
        assertEquals("Đọc chữ: 9 lượt, hoàn thành 77,8 phần trăm", content.features.first().spoken)
        assertNull(content.features.last().rateText)
        assertEquals(
            "4 cuộc gọi người thân, 3 cuộc có người nghe",
            content.tiles.single { it.label == "Gọi người thân (có người nghe)" }.spoken,
        )
        assertEquals("0,3 lần", content.tiles.single { it.label == "Thử lại trung bình" }.value)
        // Tài liệu nghiệp vụ (/me/stats): kết nối, lượt, kết quả, thử lại, cảnh báo, gọi người thân, theo ngày + chức năng.
        val labels = content.tiles.map { it.label }
        listOf("Lượt dùng", "Hoàn thành", "Thử lại trung bình", "Kính kết nối", "Cảnh báo vật cản",
            "Gọi người thân (có người nghe)").forEach { assertTrue(it, it in labels) }
        assertEquals("ngày 18 tháng 11: chưa tới", content.bars.last().spoken)
        assertEquals("13/11", content.bars[1].label)
    }

    @Test
    fun `every server feature key has a Vietnamese label`() {
        // Khoá nhóm của AB services/admin_stats.py GROUPS.
        val keys = listOf("ocr", "ocr_live", "money", "ask", "find", "space", "hazard", "chat", "family",
            "internet_call", "music", "directions", "ride", "alert_mode")
        keys.forEach { assertTrue(it, FEATURE_LABELS.containsKey(it)) }
    }

    @Test
    fun `statistics switched off on the server gets its own message`() {
        assertEquals(
            "Máy chủ chưa bật thống kê sử dụng. Hãy báo đội ngũ SeeVia.",
            statsErrorVi(ApiResult.Failed(503, "USAGE_NOT_CONFIGURED", "")),
        )
        assertEquals("Không kết nối được máy chủ. Kiểm tra mạng rồi thử lại.", statsErrorVi(ApiResult.Network(RuntimeException())))
    }
}
