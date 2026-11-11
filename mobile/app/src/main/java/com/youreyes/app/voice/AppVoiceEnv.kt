package com.youreyes.app.voice

import android.content.Context
import com.youreyes.app.BuildConfig
import com.youreyes.app.actions.EmergencyCall
import com.youreyes.app.calls.CallController
import com.youreyes.app.calls.SessionApi
import com.youreyes.app.calls.apiErrorVi
import com.youreyes.app.core.AppConfig
import com.youreyes.app.messages.ChatUiState
import com.youreyes.app.network.ApiResult
import com.youreyes.app.network.GlassesServerClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.UUID

/** [VoiceEnv] over the real app: care links, calls, messages, glasses status, emergency call. */
class AppVoiceEnv(
    context: Context,
    private val navigate: (String) -> Unit,
) : VoiceEnv {

    private val app: Context = context.applicationContext

    private var cached: List<VoiceContact> = emptyList()
    private var cachedAt = 0L

    override fun isLoggedIn(): Boolean = SessionApi.hasSession(app)

    /**
     * Everyone this account can message: the glasses owners I am a relative of (can also be called), and
     * the relatives I invited as a glasses owner (messages only: the server has no owner-to-relative call).
     */
    override suspend fun contacts(): List<VoiceContact> {
        val now = System.currentTimeMillis()
        if (cached.isNotEmpty() && now - cachedAt < CACHE_MS) return cached
        val result = withContext(Dispatchers.IO) { SessionApi.run(app) { it.careLinks() } }
        if (result is ApiResult.Ok) {
            cached = contactsFrom(result.data)
            cachedAt = now
        }
        return cached
    }

    override fun startCall(contact: VoiceContact) {
        CallController.call(app, contact.id, contact.name)
    }

    override suspend fun sendMessage(contact: VoiceContact, text: String): String? {
        val result = withContext(Dispatchers.IO) {
            SessionApi.run(app) { it.sendMessage(contact.id, text, UUID.randomUUID().toString(), toRelative = !contact.canCall) }
        }
        return if (result is ApiResult.Ok) null else apiErrorVi(result)
    }

    override suspend fun unreadMessages(contacts: List<VoiceContact>): List<SpokenMessage>? {
        if (contacts.isEmpty()) return emptyList()
        var anyOk = false
        val out = ArrayList<SpokenMessage>()
        for (contact in contacts) {
            val result = withContext(Dispatchers.IO) { SessionApi.run(app) { it.messages(contact.id, limit = 20) } }
            if (result !is ApiResult.Ok) continue
            anyOk = true
            VoiceMessages.unread(result.data, ReadMarkers.get(app, contact.id)).forEach { message ->
                val emergency = message.text.startsWith(ChatUiState.EMERGENCY_PREFIX)
                val body = if (emergency) message.text.removePrefix(ChatUiState.EMERGENCY_PREFIX).trimStart(':', ' ') else message.text
                out += SpokenMessage(message.id, contact.name, if (emergency) "Khẩn cấp. $body" else body, message.createdAt, contact.id)
            }
        }
        if (!anyOk) return null
        return out.sortedBy { VoiceMessages.sortKey(it.createdAt) }
    }

    override fun markRead(messages: List<SpokenMessage>) {
        messages.groupBy { it.peerId }.forEach { (peer, list) ->
            ReadMarkers.advance(app, peer, list.maxByOrNull { VoiceMessages.sortKey(it.createdAt) }?.createdAt)
        }
    }

    override suspend fun glassesSummary(): String = glassesBrief().spoken()

    /** Reads the glasses' state from Server Kính. Public so the home screen's tile can show the same words. */
    suspend fun glassesBrief(): GlassesBrief {
        val paired = AppConfig.prefs(app).getString(AppConfig.KEY_PAIRED_GLASSES_ID, "").orEmpty()
        if (paired.isBlank()) return GlassesBrief.from(paired, null, false)
        if (BuildConfig.GLASSES_SERVER_URL.isBlank() || BuildConfig.GLASSES_SERVER_TOKEN.isBlank()) {
            return GlassesBrief.from(paired, null, true)
        }
        val result = withContext(Dispatchers.IO) {
            GlassesServerClient().deviceStatus(BuildConfig.GLASSES_SERVER_URL, BuildConfig.GLASSES_SERVER_TOKEN, paired)
        }
        return GlassesBrief.from(paired, result.getOrNull(), result.isFailure)
    }

    override fun emergencyNumber(): String = EmergencyCall.dialable(EmergencyCall.savedNumber(app))

    override fun startEmergency(): String = EmergencyCall.describe(EmergencyCall.start(app))

    override fun open(screen: VoiceScreen) {
        navigate(VoiceRoutes.routeFor(screen))
    }

    companion object {
        private const val CACHE_MS = 60_000L

        /** Active links as contacts: owners I can call first (they win a tie), then my own relatives. */
        fun contactsFrom(links: List<com.youreyes.app.calls.CareLink>): List<VoiceContact> {
            val active = links.filter { it.status == "active" }
            val glassesOwners = active.filter { it.role == "relative" }.map { link ->
                VoiceContact(
                    id = link.ownerUserId,
                    name = link.peerDisplayName.ifBlank { link.displayName }.ifBlank { "Người thân" },
                    aliases = listOf(link.displayName).filter { it.isNotBlank() },
                )
            }
            val myRelatives = active.filter { it.role == "owner" }.map { link ->
                VoiceContact(
                    id = link.relativeUserId,
                    name = link.displayName.ifBlank { link.peerDisplayName }.ifBlank { "Người thân" },
                    aliases = (link.aliases + link.peerDisplayName).filter { it.isNotBlank() }.distinct(),
                    canCall = false,
                )
            }
            return (glassesOwners + myRelatives).distinctBy { it.id }
        }
    }
}
