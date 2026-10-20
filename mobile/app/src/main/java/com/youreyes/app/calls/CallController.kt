package com.youreyes.app.calls

import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.telecom.DisconnectCause
import android.util.Log
import com.youreyes.app.calls.audio.CallAudioEngine
import com.youreyes.app.calls.audio.CallMediaSocket
import com.youreyes.app.calls.telecom.TelecomBridge
import com.youreyes.app.calls.ui.CallActivity
import com.youreyes.app.messages.MessageEvents
import com.youreyes.app.network.ApiResult
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.concurrent.Executors

/** What the call screen shows besides [CallState]. */
data class CallExtras(
    val name: String = "",
    val emergency: Boolean = false,
    val muted: Boolean = false,
    val speaker: Boolean = false,
)

/**
 * Glue between the pure [CallStateMachine] and the real world: REST, `WS /v2/call`, audio,
 * ringtone, Telecom, the foreground service and notifications. One call at a time.
 *
 * Threading: every public entry point returns quickly. Events go through [dispatch]; the machine
 * decides, and its effects run in order on one worker thread, which hands every blocking network
 * call to another pool. Nothing here logs tokens, URLs, phone numbers or message text.
 */
object CallController {
    private const val TAG = "CallController"
    private const val RING_TIMEOUT_MS = 45_000L
    private const val ENDED_HOLD_MS = 3_000L

    private val machine = CallStateMachine()
    private val lock = Any()
    private val _state = MutableStateFlow<CallState>(CallState.Idle)
    private val _extras = MutableStateFlow(CallExtras())

    val state: StateFlow<CallState> = _state.asStateFlow()
    val extras: StateFlow<CallExtras> = _extras.asStateFlow()

    /** Set by [CallActivity] while it is on screen; decides whether an "ended" screen is worth showing. */
    @Volatile
    var uiVisible: Boolean = false

    /** The call screen was just asked for and may not be on screen yet. */
    @Volatile
    private var uiRequestedUntil = 0L

    @Volatile private var appContext: Context? = null
    @Volatile private var media: MediaSession? = null
    @Volatile private var ringShownFor: String? = null
    @Volatile private var incomingId: String? = null
    private var foregroundRunning = false
    private var ringer: CallRinger? = null

    private val effects = Executors.newSingleThreadExecutor { Thread(it, "call-effects").apply { isDaemon = true } }
    private val network = Executors.newCachedThreadPool { Thread(it, "call-net").apply { isDaemon = true } }
    private val main = Handler(Looper.getMainLooper())

    private class MediaSession(val callId: String) {
        @Volatile var socket: CallMediaSocket? = null
        @Volatile var engine: CallAudioEngine? = null
    }

    // ---- public API ----

    /** From the FCM receiver: returns at once. */
    fun onPush(context: Context, push: CallPush) {
        init(context)
        val needsLogin = push is CallPush.IncomingCall || push is CallPush.MessageIn
        if (needsLogin && !SessionApi.hasSession(context)) {
            CallNotifications.showLoginNeeded(context.applicationContext)
            return
        }
        if (push is CallPush.MessageIn) {
            MessageEvents.onMessageIn(context.applicationContext, push)
            return
        }
        dispatch(CallEvent.Push(push))
    }

    fun answer() = dispatch(CallEvent.UserAnswer)
    fun reject() = dispatch(CallEvent.UserReject)
    fun hangup() = dispatch(CallEvent.UserHangup)

    /** Starts calling the glasses of [ownerUserId] and opens the call screen. */
    fun call(context: Context, ownerUserId: String, name: String) {
        init(context)
        val idle = state.value.let { it == CallState.Idle || it is CallState.Ended }
        if (idle) {
            _extras.value = CallExtras(name = name)
            dispatch(CallEvent.UserCall(ownerUserId))
        }
        openCallScreen(context.applicationContext)
    }

    fun mute(on: Boolean) {
        _extras.value = _extras.value.copy(muted = on)
        media?.engine?.setMuted(on)
    }

    fun speaker(on: Boolean) {
        _extras.value = _extras.value.copy(speaker = on)
        media?.engine?.setSpeaker(on)
    }

    /** Telecom's own answer (Bluetooth button, system UI). */
    fun answerFromSystem(context: Context) {
        init(context)
        dispatch(CallEvent.UserAnswer)
        openCallScreen(context.applicationContext)
    }

    /** Rings: sound + full-screen notification. Telecom calls this once it accepted the call. */
    fun showRingingUi(context: Context, name: String, emergency: Boolean) {
        init(context)
        val ringing = state.value as? CallState.Ringing ?: return
        ringShownFor = ringing.callId
        ringer().start(emergency)
        CallNotifications.showIncoming(context.applicationContext, name, emergency)
    }

    /** The call screen was closed by the user after the call ended (or nothing to show). */
    fun dismiss() = dispatch(CallEvent.Dismiss)

    // ---- core ----

    private fun init(context: Context) {
        if (appContext != null) return
        synchronized(lock) {
            if (appContext == null) {
                appContext = context.applicationContext
                CallNotifications.ensureChannels(context.applicationContext)
            }
        }
    }

    private fun ringer(): CallRinger = synchronized(lock) {
        ringer ?: CallRinger(appContext!!).also { ringer = it }
    }

    private fun dispatch(event: CallEvent) {
        val ctx = appContext ?: return
        synchronized(lock) {
            val prev = machine.state
            val list = machine.handle(event)
            val next = machine.state
            publish(prev, next)
            effects.execute {
                transition(ctx, prev, next)
                list.forEach { runEffect(ctx, it) }
            }
        }
    }

    private fun publish(prev: CallState, next: CallState) {
        ActiveVoipCall.isActive = isVoipActive(next)
        if (next is CallState.Ringing && prev != next) {
            incomingId = next.callId
            _extras.value = CallExtras(name = next.name, emergency = next.emergency)
        }
        if (next == CallState.Idle) _extras.value = CallExtras()
        _state.value = next
    }

    /** Side effects that follow the state itself rather than one machine effect. */
    private fun transition(ctx: Context, prev: CallState, next: CallState) {
        val wantForeground = next is CallState.Outgoing || next is CallState.Connecting || next is CallState.Active
        if (wantForeground && !foregroundRunning) {
            foregroundRunning = true
            CallForegroundService.start(ctx, _extras.value.name)
        } else if (!wantForeground && foregroundRunning) {
            foregroundRunning = false
            CallForegroundService.stop(ctx)
        }
        if (prev is CallState.Ringing && (next is CallState.Connecting || next is CallState.Active)) {
            TelecomBridge.markActive()
        }
        if (isVoipActive(prev) && !isVoipActive(next)) {
            TelecomBridge.endCall(
                when {
                    prev is CallState.Ringing && next == CallState.Idle -> DisconnectCause.REJECTED
                    next is CallState.Ended -> DisconnectCause.REMOTE
                    else -> DisconnectCause.LOCAL
                },
            )
        }
    }

    private fun runEffect(ctx: Context, effect: CallEffect) {
        when (effect) {
            CallEffect.StartRinging -> startRinging(ctx)
            CallEffect.StopRinging -> {
                ringer().stop()
                CallNotifications.cancelIncoming(ctx)
            }
            is CallEffect.StartCall -> network.execute {
                dispatch(CallEvent.StartReply(SessionApi.run(ctx) { it.startCall(effect.ownerUserId) }))
            }
            is CallEffect.CallApi -> callApi(ctx, effect.op, effect.callId)
            is CallEffect.OpenMedia -> openMedia(ctx, effect)
            CallEffect.CloseMedia -> closeMedia()
            is CallEffect.ShowEnded -> showEnded(ctx)
        }
    }

    private fun callApi(ctx: Context, op: CallOp, callId: String) {
        network.execute {
            when (op) {
                CallOp.ANSWER -> dispatch(CallEvent.AnswerReply(callId, SessionApi.run(ctx) { it.answer(callId) }))
                CallOp.GET -> dispatch(CallEvent.SyncResult(callId, SessionApi.run(ctx) { it.getCall(callId) }))
                CallOp.REJECT -> SessionApi.run(ctx) { it.reject(callId) }
                CallOp.HANGUP -> SessionApi.run(ctx) { it.hangup(callId) }
                CallOp.START -> Unit // start has its own effect
            }
        }
    }

    // ---- ringing ----

    /** FCM can arrive late: ask the server whether the call still rings (3 s) before making noise. */
    private fun startRinging(ctx: Context) {
        val ringing = state.value as? CallState.Ringing ?: return
        network.execute {
            val result = SessionApi.run(ctx, quick = true) { it.getCall(ringing.callId) }
            dispatch(CallEvent.SyncResult(ringing.callId, result))
            val now = state.value
            if (now is CallState.Ringing && now.callId == ringing.callId) {
                if (!TelecomBridge.reportIncoming(ctx, now.callId, now.name, now.emergency)) {
                    showRingingUi(ctx, now.name, now.emergency)
                }
                main.postDelayed({
                    val s = state.value
                    // FCM `call_ended` can be lost; never ring forever.
                    if (s is CallState.Ringing && s.callId == ringing.callId) {
                        dispatch(CallEvent.Push(CallPush.Ended(s.callId, "missed")))
                    }
                }, RING_TIMEOUT_MS)
            }
        }
    }

    // ---- media ----

    private fun openMedia(ctx: Context, effect: CallEffect.OpenMedia) {
        val callId = when (val s = state.value) {
            is CallState.Connecting -> s.callId
            is CallState.Active -> s.callId
            else -> return
        }
        val session = MediaSession(callId)
        media = session
        val listener = object : CallMediaSocket.Listener {
            override fun onAudio(frame: ByteArray) {
                if (media === session) session.engine?.onRemoteAudio(frame)
            }

            override fun onState(state: String, reason: String?) {
                if (media === session) dispatch(CallEvent.SocketState(state, reason))
            }

            override fun onClosed(code: Int) {
                // A close without an `ended` frame is a dropped call.
                if (media === session) dispatch(CallEvent.SocketState("ended", "peer_disconnect"))
            }
        }
        val socket = CallMediaSocket(SessionApi.media, effect.url, effect.token, listener)
        session.socket = socket
        if (media !== session) {
            socket.close()
            return
        }
        val engine = CallAudioEngine(ctx, socket)
        session.engine = engine
        engine.setMuted(_extras.value.muted)
        if (!engine.start()) {
            Log.w(TAG, "Microphone or speaker could not be opened")
            network.execute { SessionApi.run(ctx) { it.hangup(callId) } }
            dispatch(CallEvent.SocketState("ended", "mic_unavailable"))
        } else if (_extras.value.speaker) {
            engine.setSpeaker(true)
        }
    }

    private fun closeMedia() {
        val session = media ?: return
        media = null
        session.socket?.hangup()
        session.socket?.close()
        session.engine?.stop()
    }

    // ---- ending ----

    private fun showEnded(ctx: Context) {
        val ended = state.value as? CallState.Ended ?: return
        ringer().stop()
        CallNotifications.cancelIncoming(ctx)
        val neverRang = ended.callId == incomingId && ringShownFor != ended.callId
        if (neverRang || !(uiVisible || System.currentTimeMillis() < uiRequestedUntil)) {
            dismiss()
        } else {
            main.postDelayed({ if (state.value == ended) dismiss() }, ENDED_HOLD_MS)
        }
    }

    private fun openCallScreen(ctx: Context) {
        uiRequestedUntil = System.currentTimeMillis() + 3_000L
        runCatching { ctx.startActivity(CallActivity.intent(ctx).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
            .onFailure { Log.w(TAG, "Call screen not opened: ${it.javaClass.simpleName}") }
    }
}
