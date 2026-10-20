package com.youreyes.app.calls

/**
 * Data shapes of the relative-facing API (protocol section 7).
 *
 * Privacy: `toString()` of anything carrying a media token or message text omits it, so an
 * accidental `Log.d("...", value.toString())` cannot leak it. Phone numbers are stored masked.
 */

/** "***" + the last three digits; "" when there is no number. Never keep the full number. */
fun maskPhone(phone: String?): String {
    val digits = phone.orEmpty().filter { it.isDigit() }
    return if (digits.isEmpty()) "" else "***" + digits.takeLast(3)
}

/** One relationship between a glasses owner and a relative. `role` is the caller's role in it. */
data class CareLink(
    val id: String,
    val role: String,
    val status: String,
    val displayName: String,
    val aliases: List<String>,
    val emergency: Boolean,
    val priority: Int,
    val ownerUserId: String,
    val relativeUserId: String,
    val peerDisplayName: String,
    val peerPhoneMasked: String,
)

/** `GET /calls/{id}` and the reply of `reject` / `hangup`. */
data class CallInfo(
    val callId: String,
    val direction: String,
    val state: String,
    val endReason: String?,
    val emergency: Boolean,
    val peerDisplayName: String,
    val mediaUrl: String?,
    val mediaToken: String?,
) {
    override fun toString(): String =
        "CallInfo(callId=$callId, direction=$direction, state=$state, endReason=$endReason, hasToken=${mediaToken != null})"
}

/** Reply of `POST /calls/{id}/answer`; the token is handed out only once (later answers: null). */
data class CallAnswer(
    val callId: String,
    val mediaUrl: String,
    val mediaToken: String?,
) {
    override fun toString(): String = "CallAnswer(callId=$callId, hasToken=${mediaToken != null})"
}

data class ChatMessage(
    val id: String,
    val direction: String,
    val fromMe: Boolean,
    val text: String,
    val createdAt: String,
    val readAt: String?,
) {
    override fun toString(): String = "ChatMessage(id=$id, direction=$direction, fromMe=$fromMe, read=${readAt != null})"
}
