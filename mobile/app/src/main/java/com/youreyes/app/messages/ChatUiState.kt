package com.youreyes.app.messages

import com.youreyes.app.calls.ChatMessage
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

/** Where an outgoing message is on its way. */
enum class Delivery { SENT, SENDING, FAILED }

/** One line of the conversation as the screen draws it. */
data class ChatItem(
    val id: String,
    val text: String,
    val fromMe: Boolean,
    val createdAt: String,
    val readAt: String?,
    val isEmergency: Boolean,
    val delivery: Delivery,
    /** Present on messages this device is still sending (or failed to); the same id is reused to retry. */
    val requestId: String? = null,
) {
    /** Label under my own messages; null for the other side's. */
    val statusLabel: String?
        get() = when {
            !fromMe -> null
            delivery == Delivery.SENDING -> "Đang gửi"
            delivery == Delivery.FAILED -> "Gửi lỗi — chạm để gửi lại"
            readAt != null -> "Đã nghe"
            else -> "Đã gửi"
        }

    val canRetry: Boolean
        get() = fromMe && delivery == Delivery.FAILED
}

/** A message typed here and not yet confirmed by the server. */
data class PendingSend(
    val requestId: String,
    val text: String,
    val createdAt: String,
    val failed: Boolean = false,
) {
    fun failed(): PendingSend = copy(failed = true)

    /** Retrying reuses [requestId], so the server cannot deliver the message twice. */
    fun retrying(): PendingSend = copy(failed = false)
}

object ChatUiState {
    const val EMERGENCY_PREFIX = "KHẨN CẤP"

    fun isEmergency(message: ChatMessage): Boolean = !message.fromMe && message.text.startsWith(EMERGENCY_PREFIX)

    /**
     * Joins the server's history (newest first) with messages that arrived since, by `id`
     * (the later list wins, so a fresher `read_at` replaces an older copy), oldest first.
     */
    fun merge(server: List<ChatMessage>, incoming: List<ChatMessage>): List<ChatItem> {
        val byId = LinkedHashMap<String, ChatMessage>()
        server.forEach { byId[it.id] = it }
        incoming.forEach { byId[it.id] = it }
        return byId.values
            .withIndex()
            .sortedWith(compareBy({ sortKey(it.value.createdAt) }, { it.index }))
            .map { (_, m) ->
                ChatItem(
                    id = m.id,
                    text = m.text,
                    fromMe = m.fromMe,
                    createdAt = m.createdAt,
                    readAt = m.readAt,
                    isEmergency = isEmergency(m),
                    delivery = Delivery.SENT,
                )
            }
    }

    /** [items] plus the messages still being sent, after them (they are the newest). */
    fun withPending(items: List<ChatItem>, pending: List<PendingSend>): List<ChatItem> =
        items + pending.map {
            ChatItem(
                id = "pending-" + it.requestId,
                text = it.text,
                fromMe = true,
                createdAt = it.createdAt,
                readAt = null,
                isEmergency = false,
                delivery = if (it.failed) Delivery.FAILED else Delivery.SENDING,
                requestId = it.requestId,
            )
        }

    /** "14:05" in [zone] for an ISO UTC timestamp, or "" when it cannot be read. */
    fun timeLabel(createdAt: String, zone: TimeZone = TimeZone.getDefault()): String {
        val seconds = createdAt.substringBefore('.').removeSuffix("Z")
        return try {
            val parse = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }
            val date = parse.parse(seconds) ?: return ""
            SimpleDateFormat("HH:mm", Locale.US).apply { timeZone = zone }.format(date)
        } catch (e: java.text.ParseException) {
            ""
        }
    }

    /** ISO timestamps compare as text only once fractions are padded: "…:00Z" vs "…:00.5Z". */
    private fun sortKey(createdAt: String): String {
        val trimmed = createdAt.removeSuffix("Z")
        val seconds = trimmed.substringBefore('.')
        val fraction = trimmed.substringAfter('.', "").padEnd(9, '0')
        return "$seconds.$fraction"
    }
}
