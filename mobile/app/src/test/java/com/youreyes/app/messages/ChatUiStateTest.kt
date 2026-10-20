package com.youreyes.app.messages

import com.youreyes.app.calls.ChatMessage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.TimeZone

class ChatUiStateTest {

    private fun msg(id: String, at: String, fromMe: Boolean = true, text: String = "x", read: String? = null) =
        ChatMessage(id = id, direction = if (fromMe) "to_glasses" else "to_relative", fromMe = fromMe, text = text, createdAt = at, readAt = read)

    @Test
    fun merge_dedupes_by_id_and_prefers_the_newer_copy() {
        val server = listOf(msg("1", "2026-10-20T07:00:01Z"))
        val incoming = listOf(msg("1", "2026-10-20T07:00:01Z", read = "2026-10-20T07:00:09Z"))
        val items = ChatUiState.merge(server, incoming)
        assertEquals(1, items.size)
        assertEquals("2026-10-20T07:00:09Z", items[0].readAt)
    }

    @Test
    fun merge_orders_oldest_first_from_a_newest_first_history() {
        val server = listOf(msg("3", "2026-10-20T07:00:03Z"), msg("2", "2026-10-20T07:00:02Z"), msg("1", "2026-10-20T07:00:01Z"))
        val items = ChatUiState.merge(server, listOf(msg("4", "2026-10-20T07:00:04Z")))
        assertEquals(listOf("1", "2", "3", "4"), items.map { it.id })
    }

    @Test
    fun fractions_of_a_second_sort_correctly() {
        val items = ChatUiState.merge(
            listOf(msg("b", "2026-10-20T07:00:01.500Z"), msg("a", "2026-10-20T07:00:01Z"), msg("c", "2026-10-20T07:00:02Z")),
            emptyList(),
        )
        assertEquals(listOf("a", "b", "c"), items.map { it.id })
    }

    @Test
    fun status_label_follows_read_at_and_delivery() {
        val sent = ChatUiState.merge(listOf(msg("1", "2026-10-20T07:00:01Z")), emptyList())[0]
        assertEquals("Đã gửi", sent.statusLabel)
        val read = sent.copy(readAt = "2026-10-20T07:01:00Z")
        assertEquals("Đã nghe", read.statusLabel)
        assertEquals("Đang gửi", sent.copy(delivery = Delivery.SENDING).statusLabel)
        assertEquals("Gửi lỗi — chạm để gửi lại", sent.copy(delivery = Delivery.FAILED).statusLabel)
        assertTrue(sent.copy(delivery = Delivery.FAILED).canRetry)
        assertFalse(sent.canRetry)
    }

    @Test
    fun the_other_sides_messages_have_no_status_label() {
        val incoming = ChatUiState.merge(emptyList(), listOf(msg("9", "2026-10-20T07:00:01Z", fromMe = false)))[0]
        assertNull(incoming.statusLabel)
    }

    @Test
    fun emergency_is_only_a_message_from_the_other_side_starting_with_the_marker() {
        val items = ChatUiState.merge(
            emptyList(),
            listOf(
                msg("1", "2026-10-20T07:00:01Z", fromMe = false, text = "KHẨN CẤP: Mẹ cần giúp. Vị trí: https://maps"),
                msg("2", "2026-10-20T07:00:02Z", fromMe = false, text = "Con về muộn"),
                msg("3", "2026-10-20T07:00:03Z", fromMe = true, text = "KHẨN CẤP tôi tự gõ"),
            ),
        )
        assertEquals(listOf(true, false, false), items.map { it.isEmergency })
    }

    @Test
    fun a_failed_send_is_retried_with_the_same_request_id() {
        val pending = PendingSend("req-1", "Mẹ ơi", "2026-10-20T07:00:05Z")
        val failed = pending.failed()
        assertTrue(failed.failed)
        val retried = failed.retrying()
        assertEquals("req-1", retried.requestId)
        assertFalse(retried.failed)

        val shown = ChatUiState.withPending(emptyList(), listOf(failed, PendingSend("req-2", "b", "t")))
        assertEquals(Delivery.FAILED, shown[0].delivery)
        assertEquals("req-1", shown[0].requestId)
        assertEquals(Delivery.SENDING, shown[1].delivery)
    }

    @Test
    fun pending_messages_come_after_the_history() {
        val history = ChatUiState.merge(listOf(msg("1", "2026-10-20T07:00:01Z")), emptyList())
        val shown = ChatUiState.withPending(history, listOf(PendingSend("r", "t", "2026-10-20T07:00:00Z")))
        assertEquals(listOf("1", "pending-r"), shown.map { it.id })
    }

    @Test
    fun time_label_uses_the_given_zone() {
        val plus7 = TimeZone.getTimeZone("Asia/Ho_Chi_Minh")
        assertEquals("14:05", ChatUiState.timeLabel("2026-10-20T07:05:09Z", plus7))
        assertEquals("14:05", ChatUiState.timeLabel("2026-10-20T07:05:09.123Z", plus7))
        assertEquals("", ChatUiState.timeLabel("not a time", plus7))
    }
}
