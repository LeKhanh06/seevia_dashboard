package com.youreyes.app.calls

/** A parsed FCM data message of protocol section 7.6. */
sealed interface CallPush {
    data class IncomingCall(val callId: String, val fromName: String, val emergency: Boolean) : CallPush

    data class Answered(val callId: String, val mediaUrl: String, val mediaToken: String) : CallPush {
        override fun toString(): String = "Answered(callId=$callId)"
    }

    data class Ended(val callId: String, val reason: String?) : CallPush

    data class MessageIn(val messageId: String, val fromName: String, val text: String) : CallPush {
        override fun toString(): String = "MessageIn(messageId=$messageId)"
    }
}

/**
 * Routes FCM data to the call/message path. A payload without a known `type` (including every
 * old command push, which has `request_id` instead) or with a required key missing returns null.
 */
object FcmCallRouter {

    fun parse(data: Map<String, String>): CallPush? {
        fun value(key: String): String? = data[key]?.takeIf { it.isNotBlank() }

        return when (data["type"]) {
            "call_incoming" -> {
                val callId = value("call_id") ?: return null
                CallPush.IncomingCall(callId, data["from_display_name"].orEmpty(), data["emergency"] == "true")
            }
            "call_answered" -> {
                val callId = value("call_id") ?: return null
                CallPush.Answered(
                    callId,
                    mediaUrl = value("media_url") ?: return null,
                    mediaToken = value("media_token") ?: return null,
                )
            }
            "call_ended" -> CallPush.Ended(value("call_id") ?: return null, value("reason"))
            "message_in" -> CallPush.MessageIn(
                messageId = value("message_id") ?: return null,
                fromName = data["from_display_name"].orEmpty(),
                text = data["text"] ?: return null,
            )
            else -> null
        }
    }
}
