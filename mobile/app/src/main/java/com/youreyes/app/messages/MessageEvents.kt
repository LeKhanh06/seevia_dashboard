package com.youreyes.app.messages

import android.content.Context
import com.youreyes.app.calls.CallPush
import com.youreyes.app.calls.SessionApi
import com.youreyes.app.network.ApiResult
import com.youreyes.app.ui.navigation.Routes
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import java.util.concurrent.Executors

/**
 * Where an FCM `message_in` goes: into the open conversation if there is one for that sender,
 * otherwise into a notification that opens it. Never logs the sender, the text or ids' content.
 */
object MessageEvents {
    /** The conversation on screen right now (set by the chat screen while it is started). */
    data class OpenChat(val peerUserId: String, val peerName: String)

    @Volatile
    var openChat: OpenChat? = null

    private val _incoming = MutableSharedFlow<CallPush.MessageIn>(extraBufferCapacity = 8, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val incoming: SharedFlow<CallPush.MessageIn> = _incoming.asSharedFlow()

    private val worker = Executors.newSingleThreadExecutor { Thread(it, "message-events").apply { isDaemon = true } }

    fun onMessageIn(context: Context, push: CallPush.MessageIn) {
        val app = context.applicationContext
        _incoming.tryEmit(push)
        val open = openChat
        if (open != null && sameName(open.peerName, push.fromName)) return // the screen shows it

        // Notify at once (an emergency must not wait for the network), then point the tap at the
        // right conversation once the sender is known.
        MessageNotifications.show(app, push, Routes.CARE)
        worker.execute {
            val peer = resolvePeer(app, push.fromName) ?: return@execute
            MessageNotifications.show(app, push, Routes.chatRoute(peer.first, peer.second))
        }
    }

    /** The push names the sender but not their id; match it against my active links as a relative. */
    private fun resolvePeer(context: Context, fromName: String): Pair<String, String>? {
        if (fromName.isBlank()) return null
        val result = SessionApi.run(context) { it.careLinks() }
        val links = (result as? ApiResult.Ok)?.data ?: return null
        val matches = links.filter { it.role == "relative" && it.status == "active" && sameName(it.peerDisplayName, fromName) }
        return matches.singleOrNull()?.let { it.ownerUserId to it.peerDisplayName }
    }

    fun sameName(a: String, b: String): Boolean = a.isNotBlank() && a.trim().equals(b.trim(), ignoreCase = true)
}
