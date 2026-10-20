package com.youreyes.app.calls.audio

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.net.URLEncoder
import java.util.concurrent.atomic.AtomicBoolean

/**
 * `WS /v2/call?token=<media_token>` (protocol 7.3). Connects as soon as it is constructed.
 *
 * The URL carries the one-time `media_token`, so this class never logs the URL, the token, or the
 * failure messages. [Listener] callbacks run on OkHttp's threads. After [close] the listener is silent.
 */
class CallMediaSocket(
    client: OkHttpClient,
    url: String,
    token: String,
    private val listener: Listener,
) {
    interface Listener {
        /** One 640-byte PCM16 frame from the glasses (not yet buffered; see [JitterBuffer]). */
        fun onAudio(frame: ByteArray)

        /** `{"t":"state",...}` from the server: ("ringing" | "active" | "ended", reason). */
        fun onState(state: String, reason: String?)

        /** Socket gone. 4401 = token wrong / already used / expired; 1006 = connection failure. */
        fun onClosed(code: Int)
    }

    @Volatile private var localClose = false
    private val closedNotified = AtomicBoolean(false)
    private var webSocket: WebSocket? = null

    init {
        val request = try {
            val separator = if ('?' in url) '&' else '?'
            Request.Builder().url(url + separator + "token=" + URLEncoder.encode(token, "UTF-8")).build()
        } catch (e: IllegalArgumentException) {
            null
        }
        if (request == null) {
            notifyClosed(CLOSE_ABNORMAL)
        } else {
            webSocket = client.newWebSocket(request, SocketCallbacks())
        }
    }

    /** Sends one frame as-is; a stalled network drops frames instead of building up delay. */
    fun sendAudio(frame: ByteArray) {
        val ws = webSocket ?: return
        if (localClose || frame.size != MediaProtocol.FRAME_BYTES) return
        if (ws.queueSize() > MAX_QUEUED_BYTES) return
        ws.send(frame.toByteString())
    }

    fun mute(on: Boolean) {
        if (!localClose) webSocket?.send(MediaProtocol.mute(on))
    }

    /** Tells the server to end the call; follow with [close]. */
    fun hangup() {
        if (!localClose) webSocket?.send(MediaProtocol.hangup())
    }

    fun close() {
        if (localClose) return
        localClose = true
        webSocket?.close(CLOSE_NORMAL, null)
    }

    private fun notifyClosed(code: Int) {
        if (!localClose && closedNotified.compareAndSet(false, true)) listener.onClosed(code)
    }

    private inner class SocketCallbacks : WebSocketListener() {
        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            if (!localClose) listener.onAudio(bytes.toByteArray())
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (localClose) return
            MediaProtocol.parseState(text)?.let { (state, reason) -> listener.onState(state, reason) }
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            notifyClosed(code)
            webSocket.close(CLOSE_NORMAL, null)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = notifyClosed(code)

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = notifyClosed(CLOSE_ABNORMAL)
    }

    companion object {
        const val CLOSE_NORMAL = 1000
        const val CLOSE_ABNORMAL = 1006
        const val CLOSE_TOKEN_REJECTED = 4401

        /** About 2 s of audio; beyond that a frame is more harmful late than lost. */
        private const val MAX_QUEUED_BYTES = MediaProtocol.FRAME_BYTES * 100L
    }
}
