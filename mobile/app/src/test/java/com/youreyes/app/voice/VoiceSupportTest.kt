package com.youreyes.app.voice

import com.youreyes.app.actions.EmergencyCall
import com.youreyes.app.calls.ChatMessage
import com.youreyes.app.network.GlassesStatus
import com.youreyes.app.ui.components.spokenText
import com.youreyes.app.ui.navigation.Routes
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceSupportTest {

    private fun msg(id: String, at: String, fromMe: Boolean = false, text: String = "tin $id") =
        ChatMessage(id, if (fromMe) "to_glasses" else "to_relative", fromMe, text, at, null)

    // ---- unread selection ----

    @Test
    fun unread_is_everything_received_after_the_marker_oldest_first() {
        val all = listOf(
            msg("3", "2026-10-21T03:00:00Z"),
            msg("2", "2026-10-21T02:00:00Z"),
            msg("1", "2026-10-21T01:00:00Z"),
        )
        assertEquals(listOf("2", "3"), VoiceMessages.unread(all, "2026-10-21T01:00:00Z").map { it.id })
    }

    @Test
    fun unread_ignores_my_own_messages_and_empty_ones() {
        val all = listOf(
            msg("1", "2026-10-21T01:00:00Z", fromMe = true),
            msg("2", "2026-10-21T02:00:00Z", text = "  "),
            msg("3", "2026-10-21T03:00:00Z"),
        )
        assertEquals(listOf("3"), VoiceMessages.unread(all, null).map { it.id })
    }

    @Test
    fun unread_without_a_marker_reads_only_the_latest_few() {
        val all = (1..8).map { msg("$it", "2026-10-21T0$it:00:00Z") }
        assertEquals(listOf("4", "5", "6", "7", "8"), VoiceMessages.unread(all, null, max = 5).map { it.id })
    }

    @Test
    fun unread_compares_fractional_seconds_correctly() {
        val all = listOf(msg("a", "2026-10-21T01:00:00.5Z"), msg("b", "2026-10-21T01:00:00Z"))
        // marker 00Z: only the later ".5Z" message is newer
        assertEquals(listOf("a"), VoiceMessages.unread(all, "2026-10-21T01:00:00Z").map { it.id })
    }

    @Test
    fun unread_is_empty_when_everything_was_read() {
        val all = listOf(msg("1", "2026-10-21T01:00:00Z"))
        assertTrue(VoiceMessages.unread(all, "2026-10-21T01:00:00Z").isEmpty())
        assertTrue(VoiceMessages.unread(emptyList(), null).isEmpty())
    }

    @Test
    fun newest_received_ignores_mine() {
        val all = listOf(msg("1", "2026-10-21T01:00:00Z"), msg("2", "2026-10-21T05:00:00Z", fromMe = true))
        assertEquals("2026-10-21T01:00:00Z", VoiceMessages.newestReceived(all))
        assertNull(VoiceMessages.newestReceived(emptyList()))
    }

    // ---- glasses wording ----

    private fun status(known: Boolean = true, online: Boolean = true, wifi: String? = "Nha Lan") = GlassesStatus(
        deviceId = "glasses-01", serial = "01", known = known, online = online, wifiSsid = wifi,
        firmwareVersion = "1.0", latestVersion = "1.0", updateAvailable = false, downlinkOpen = true, pairedTo = null,
    )

    @Test
    fun glasses_brief_online_names_the_wifi_and_admits_no_battery_data() {
        val brief = GlassesBrief.from("glasses-01", status(), fetchFailed = false)
        assertEquals(GlassesBrief.State.ONLINE, brief.state)
        assertEquals("Đang hoạt động", brief.title)
        assertTrue(brief.spoken().contains("Wi-Fi Nha Lan"))
        assertTrue(brief.spoken().contains("chưa nhận được thông tin pin"))
    }

    @Test
    fun glasses_brief_other_states() {
        assertEquals(GlassesBrief.State.NOT_PAIRED, GlassesBrief.from("", null, false).state)
        assertEquals(GlassesBrief.State.UNAVAILABLE, GlassesBrief.from("g", null, true).state)
        assertEquals(GlassesBrief.State.UNAVAILABLE, GlassesBrief.from("g", status(), true).state)
        assertEquals(GlassesBrief.State.UNKNOWN_SERIAL, GlassesBrief.from("g", status(known = false), false).state)
        assertEquals(GlassesBrief.State.OFFLINE, GlassesBrief.from("g", status(online = false), false).state)
        listOf(GlassesBrief.State.entries).flatten().forEach {
            val b = GlassesBrief(it)
            assertTrue(b.title.isNotBlank())
            assertTrue(b.spoken().isNotBlank())
        }
    }

    // ---- routes ----

    @Test
    fun every_voice_screen_has_its_own_visible_route() {
        val routes = VoiceScreen.entries.map { VoiceRoutes.routeFor(it) }
        assertEquals(routes.size, routes.toSet().size)
        val hidden = setOf(Routes.ALBUM, Routes.COMMUNITY, Routes.MEETING, Routes.TRANSLATION, Routes.DEV_TEST)
        assertTrue(routes.none { it in hidden })
    }

    // ---- shared text helpers ----

    @Test
    fun status_symbols_are_not_read_out() {
        assertEquals("Đã gửi lời mời.", spokenText("✅ Đã gửi lời mời."))
        assertEquals("Lỗi: mất mạng", spokenText("❌ Lỗi: mất mạng"))
        assertEquals("Bình thường", spokenText("Bình thường"))
        assertEquals("", spokenText("✅"))
    }

    @Test
    fun emergency_number_is_dialable_and_spoken_digit_by_digit() {
        assertEquals("0901234567", EmergencyCall.dialable(" 0901 234-567 "))
        assertEquals("+84901234567", EmergencyCall.dialable("+84 901 234 567"))
        assertEquals("", EmergencyCall.dialable("abc"))
        assertEquals("1 1 5", EmergencyCall.spokenNumber("115"))
        assertFalse(EmergencyCall.describe(EmergencyCall.Result.NoNumber).isBlank())
    }
}
