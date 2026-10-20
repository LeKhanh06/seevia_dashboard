package com.youreyes.app.network

import com.youreyes.app.calls.CallAnswer
import com.youreyes.app.calls.CallInfo
import com.youreyes.app.calls.CareLink
import com.youreyes.app.calls.ChatMessage
import com.youreyes.app.calls.maskPhone
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException

/** Outcome of one call to the session API; never throws. */
sealed interface ApiResult<out T> {
    data class Ok<T>(val data: T) : ApiResult<T>

    /** A non-2xx reply (other than 401) carrying the server's error envelope. */
    data class Failed(val httpStatus: Int, val code: String, val message: String) : ApiResult<Nothing>

    /** 401: the session token is gone or expired; the caller clears it and asks for login. */
    data object Unauthorized : ApiResult<Nothing>

    data class Network(val cause: Throwable) : ApiResult<Nothing>
}

/**
 * Blocking client for the relative-facing API (`/api/v1/care`, `/calls`, `/messages`; protocol
 * section 7). Auth is the login session token, never the Device token. Call it off the main thread.
 *
 * Nothing here logs: requests and responses carry the access token, `media_token` and message text.
 */
class SessionApiClient(
    baseUrl: String,
    private val accessToken: String,
    private val client: OkHttpClient,
) {
    private val base = baseUrl.trimEnd('/')

    // ---- care links ----

    fun careLinks(): ApiResult<List<CareLink>> =
        call("GET", "/api/v1/care/links") { data -> data.getJSONArray("links").mapObjects(::parseCareLink) }

    fun invite(
        phone: String,
        displayName: String,
        aliases: List<String>,
        emergency: Boolean,
        priority: Int,
    ): ApiResult<CareLink> {
        val body = JSONObject()
            .put("phone_number", phone)
            .put("display_name", displayName)
            .put("aliases", org.json.JSONArray(aliases))
            .put("emergency", emergency)
            .put("priority", priority)
        return call("POST", "/api/v1/care/invite", body, parse = ::parseCareLink)
    }

    fun accept(linkId: String): ApiResult<CareLink> = call("POST", "/api/v1/care/$linkId/accept", parse = ::parseCareLink)

    fun revoke(linkId: String): ApiResult<CareLink> = call("DELETE", "/api/v1/care/$linkId", parse = ::parseCareLink)

    // ---- calls ----

    /** Returns the new `call_id`. */
    fun startCall(ownerUserId: String): ApiResult<String> =
        call("POST", "/api/v1/calls", JSONObject().put("owner_user_id", ownerUserId)) { it.getString("call_id") }

    fun answer(callId: String): ApiResult<CallAnswer> =
        call("POST", "/api/v1/calls/$callId/answer", parse = ::parseCallAnswer)

    fun reject(callId: String): ApiResult<CallInfo> =
        call("POST", "/api/v1/calls/$callId/reject", parse = ::parseCallInfo)

    fun hangup(callId: String): ApiResult<CallInfo> =
        call("POST", "/api/v1/calls/$callId/hangup", parse = ::parseCallInfo)

    fun getCall(callId: String): ApiResult<CallInfo> = call("GET", "/api/v1/calls/$callId", parse = ::parseCallInfo)

    // ---- messages ----

    /** Returns the new `message_id`. Resending with the same [requestId] never duplicates the message. */
    fun sendMessage(ownerUserId: String, text: String, requestId: String): ApiResult<String> {
        val body = JSONObject().put("owner_user_id", ownerUserId).put("text", text).put("request_id", requestId)
        return call("POST", "/api/v1/messages", body) { it.getString("message_id") }
    }

    /** Newest first, as the server returns it. */
    fun messages(withUserId: String, before: String? = null, limit: Int = 50): ApiResult<List<ChatMessage>> {
        val query = buildList {
            add("with" to withUserId)
            before?.let { add("before" to it) }
            add("limit" to limit.toString())
        }
        return call("GET", "/api/v1/messages", query = query) { data ->
            data.getJSONArray("messages").mapObjects(::parseMessage)
        }
    }

    // ---- plumbing ----

    private fun <T> call(
        method: String,
        path: String,
        body: JSONObject? = null,
        query: List<Pair<String, String>> = emptyList(),
        parse: (JSONObject) -> T,
    ): ApiResult<T> {
        val url = (base + path).toHttpUrlOrNull()?.newBuilder()?.apply {
            query.forEach { (key, value) -> addQueryParameter(key, value) }
        }?.build() ?: return ApiResult.Network(IllegalArgumentException("Bad server URL"))
        val requestBody = when {
            body != null -> body.toString().toRequestBody(JSON)
            method == "GET" -> null
            else -> ByteArray(0).toRequestBody(null)
        }
        val request = Request.Builder()
            .url(url)
            .method(method, requestBody)
            .header("Authorization", "Bearer $accessToken")
            .header("Accept", "application/json")
            .build()
        return try {
            client.newCall(request).execute().use { response ->
                val text = response.body?.string().orEmpty()
                when {
                    response.code == 401 -> ApiResult.Unauthorized
                    response.isSuccessful -> parseSuccess(text, parse)
                    else -> parseError(response.code, text)
                }
            }
        } catch (e: IOException) {
            ApiResult.Network(e)
        }
    }

    private fun <T> parseSuccess(text: String, parse: (JSONObject) -> T): ApiResult<T> =
        try {
            ApiResult.Ok(parse(JSONObject(text).getJSONObject("data")))
        } catch (e: JSONException) {
            ApiResult.Failed(200, "BAD_RESPONSE", "")
        }

    private companion object {
        val JSON = "application/json; charset=utf-8".toMediaType()
    }
}

// ---- parsing (internal for tests; they work on JSON text, no network) ----

private fun JSONObject.stringOrNull(key: String): String? = if (isNull(key)) null else getString(key)

private fun JSONObject.stringOrEmpty(key: String): String = stringOrNull(key).orEmpty()

private fun <T> org.json.JSONArray.mapObjects(transform: (JSONObject) -> T): List<T> =
    (0 until length()).map { transform(getJSONObject(it)) }

internal fun parseCareLink(json: JSONObject): CareLink {
    val aliases = json.optJSONArray("aliases")
    return CareLink(
        id = json.getString("id"),
        role = json.getString("role"),
        status = json.getString("status"),
        displayName = json.stringOrEmpty("display_name"),
        aliases = if (aliases == null) emptyList() else (0 until aliases.length()).map { aliases.getString(it) },
        emergency = json.optBoolean("emergency", false),
        priority = json.optInt("priority", 0),
        ownerUserId = json.getString("owner_user_id"),
        relativeUserId = json.getString("relative_user_id"),
        peerDisplayName = json.stringOrEmpty("peer_display_name"),
        peerPhoneMasked = maskPhone(json.stringOrNull("peer_phone_number")),
    )
}

internal fun parseCallInfo(json: JSONObject) = CallInfo(
    callId = json.getString("call_id"),
    direction = json.stringOrEmpty("direction"),
    state = json.getString("state"),
    endReason = json.stringOrNull("end_reason"),
    emergency = json.optBoolean("emergency", false),
    peerDisplayName = json.stringOrEmpty("peer_display_name"),
    mediaUrl = json.stringOrNull("media_url"),
    mediaToken = json.stringOrNull("media_token"),
)

internal fun parseCallAnswer(json: JSONObject) = CallAnswer(
    callId = json.getString("call_id"),
    mediaUrl = json.getString("media_url"),
    mediaToken = json.stringOrNull("media_token"),
)

internal fun parseMessage(json: JSONObject) = ChatMessage(
    id = json.getString("id"),
    direction = json.stringOrEmpty("direction"),
    fromMe = json.optBoolean("from_me", false),
    text = json.stringOrEmpty("text"),
    createdAt = json.stringOrEmpty("created_at"),
    readAt = json.stringOrNull("read_at"),
)

/** Reads the `{"status":"error","error":{code,message}}` envelope; tolerant of non-JSON bodies. */
internal fun parseError(status: Int, body: String): ApiResult.Failed {
    val error = try {
        JSONObject(body).optJSONObject("error")
    } catch (e: JSONException) {
        null
    }
    return ApiResult.Failed(
        httpStatus = status,
        code = error?.optString("code").orEmpty().ifBlank { "HTTP_$status" },
        message = error?.optString("message").orEmpty(),
    )
}
