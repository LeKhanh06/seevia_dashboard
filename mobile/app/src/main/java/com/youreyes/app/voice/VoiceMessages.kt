package com.youreyes.app.voice

import android.content.Context
import com.youreyes.app.calls.ChatMessage
import com.youreyes.app.core.AppConfig

/** Which received messages count as "not read yet" for the voice command "đọc tin nhắn". */
object VoiceMessages {

    /** ISO UTC timestamps compare as text only once fractions are padded: "…:00Z" vs "…:00.5Z". */
    fun sortKey(createdAt: String): String {
        val trimmed = createdAt.removeSuffix("Z")
        val seconds = trimmed.substringBefore('.')
        val fraction = trimmed.substringAfter('.', "").padEnd(9, '0')
        return "$seconds.$fraction"
    }

    /**
     * Messages from the other person newer than [readUpTo] (the `created_at` of the last one the
     * person saw or heard), oldest first, at most [max] (the newest ones when there are more).
     * Never read anything before: the latest [max].
     */
    fun unread(messages: List<ChatMessage>, readUpTo: String?, max: Int = 5): List<ChatMessage> {
        val marker = readUpTo?.takeIf { it.isNotBlank() }?.let(::sortKey)
        return messages
            .filter { !it.fromMe && it.text.isNotBlank() }
            .filter { marker == null || sortKey(it.createdAt) > marker }
            .sortedBy { sortKey(it.createdAt) }
            .takeLast(max)
    }

    /** The newest `created_at` among received [messages], or null. */
    fun newestReceived(messages: List<ChatMessage>): String? =
        messages.filter { !it.fromMe }.maxByOrNull { sortKey(it.createdAt) }?.createdAt
}

/** Remembers, per conversation, how far received messages were read (opened on screen or read aloud). */
object ReadMarkers {
    private const val PREFIX = "read_upto_"

    fun get(context: Context, peerId: String): String? =
        AppConfig.prefs(context).getString(PREFIX + peerId, null)

    /** Moves the marker forward only; an older timestamp never un-reads messages. */
    fun advance(context: Context, peerId: String, createdAt: String?) {
        if (peerId.isBlank() || createdAt.isNullOrBlank()) return
        val prefs = AppConfig.prefs(context)
        val old = prefs.getString(PREFIX + peerId, null)
        if (old == null || VoiceMessages.sortKey(createdAt) > VoiceMessages.sortKey(old)) {
            prefs.edit().putString(PREFIX + peerId, createdAt).apply()
        }
    }
}
