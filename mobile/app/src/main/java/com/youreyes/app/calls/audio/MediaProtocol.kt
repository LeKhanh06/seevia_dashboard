package com.youreyes.app.calls.audio

import org.json.JSONException
import org.json.JSONObject

/** Frame formats of `WS /v2/call` (protocol 7.3). */
object MediaProtocol {
    const val SAMPLE_RATE = 16_000
    const val FRAME_MS = 20

    /** PCM16 little-endian mono: 16 000 Hz x 20 ms x 2 bytes. */
    const val FRAME_BYTES = SAMPLE_RATE / 1000 * FRAME_MS * 2

    fun hangup(): String = """{"t":"hangup"}"""

    fun mute(on: Boolean): String = """{"t":"mute","on":$on}"""

    /** `{"t":"state","state":..,"reason"?:..}` -> (state, reason); anything else -> null. */
    fun parseState(text: String): Pair<String, String?>? {
        val json = try {
            JSONObject(text)
        } catch (e: JSONException) {
            return null
        }
        if (json.optString("t") != "state" || json.isNull("state")) return null
        val state = json.optString("state").ifEmpty { return null }
        val reason = if (json.isNull("reason")) null else json.optString("reason").ifEmpty { null }
        return state to reason
    }
}
