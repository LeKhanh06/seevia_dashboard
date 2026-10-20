package com.youreyes.app.calls

import com.youreyes.app.network.ApiResult

/** Vietnamese text for a call's end reason (protocol 7.2 `end_reason` / 7.3 `state.reason`). */
fun endReasonVi(reason: String?): String = when (reason) {
    "busy" -> "Kính đang bận"
    "glasses_offline" -> "Kính không kết nối"
    "missed", "timeout" -> "Không ai nghe máy"
    "rejected" -> "Cuộc gọi bị từ chối"
    "revoked" -> "Đã huỷ liên kết người thân"
    "media_timeout", "peer_disconnect" -> "Mất kết nối âm thanh"
    "max_duration" -> "Cuộc gọi quá 2 giờ"
    else -> "Cuộc gọi đã kết thúc"
}

sealed interface CallState {
    data object Idle : CallState

    data class Ringing(val callId: String, val name: String, val emergency: Boolean) : CallState

    /** We are calling the glasses. [callId] is [PENDING_ID] until `POST /calls` replies. */
    data class Outgoing(val callId: String) : CallState

    /** The user pressed Nghe (answer in flight) or media is being opened. */
    data class Connecting(val callId: String) : CallState

    data class Active(val callId: String, val sinceMillis: Long) : CallState

    data class Ended(val callId: String, val reasonVi: String) : CallState

    companion object {
        const val PENDING_ID = ""
    }
}

enum class CallOp { START, ANSWER, REJECT, HANGUP, GET }

sealed interface CallEvent {
    data class Push(val push: CallPush) : CallEvent
    data object UserAnswer : CallEvent
    data object UserReject : CallEvent
    data object UserHangup : CallEvent
    data class UserCall(val ownerUserId: String) : CallEvent

    /** The controller finished `POST /calls`. */
    data class StartReply(val result: ApiResult<String>) : CallEvent

    /** The controller finished `POST /calls/{id}/answer`. */
    data class AnswerReply(val callId: String, val result: ApiResult<CallAnswer>) : CallEvent

    /** The controller finished `GET /calls/{id}` (also the pre-ring sync). */
    data class SyncResult(val callId: String, val result: ApiResult<CallInfo>) : CallEvent

    /**
     * `WS /v2/call` state frame ("ringing" / "active" / "ended"). A socket that closes without an
     * `ended` frame is reported by the controller as `SocketState("ended", "peer_disconnect")`.
     */
    data class SocketState(val state: String, val reason: String?) : CallEvent

    /** The call screen closed after showing [CallState.Ended]. */
    data object Dismiss : CallEvent
}

sealed interface CallEffect {
    data object StartRinging : CallEffect
    data object StopRinging : CallEffect
    data class StartCall(val ownerUserId: String) : CallEffect
    data class CallApi(val op: CallOp, val callId: String) : CallEffect
    data class OpenMedia(val url: String, val token: String) : CallEffect {
        override fun toString(): String = "OpenMedia"
    }
    data object CloseMedia : CallEffect
    data class ShowEnded(val reasonVi: String) : CallEffect
}

/**
 * Pure call lifecycle (protocol 7.7): events in, new state + effects out; no Android, no I/O.
 *
 * Besides [state] it remembers the last 50 ended call ids so a late or duplicated FCM
 * `call_incoming` for a finished call never rings (FCM has no ordering guarantee), and which call
 * already had its media opened so a token is never used twice. Thread-safe.
 */
class CallStateMachine(
    initial: CallState = CallState.Idle,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    var state: CallState = initial
        private set

    private val endedIds = LinkedHashSet<String>()
    private var mediaOpenedFor: String? = null

    internal fun endedIdCount(): Int = endedIds.size

    @Synchronized
    fun handle(event: CallEvent): List<CallEffect> {
        val (next, effects) = reduce(state, event)
        state = next
        return effects
    }

    /** Pure with respect to [state]; updates only the id memory described on the class. */
    @Synchronized
    fun reduce(state: CallState, event: CallEvent): Pair<CallState, List<CallEffect>> = when (event) {
        is CallEvent.Push -> onPush(state, event.push)
        CallEvent.UserAnswer -> onUserAnswer(state)
        CallEvent.UserReject -> onUserReject(state)
        CallEvent.UserHangup -> onUserHangup(state)
        is CallEvent.UserCall -> onUserCall(state, event.ownerUserId)
        is CallEvent.StartReply -> onStartReply(state, event.result)
        is CallEvent.AnswerReply -> onAnswerReply(state, event.callId, event.result)
        is CallEvent.SyncResult -> onSync(state, event.callId, event.result)
        is CallEvent.SocketState -> onSocket(state, event)
        CallEvent.Dismiss -> if (state is CallState.Ended) CallState.Idle to NONE else state to NONE
    }

    // ---- pushes ----

    private fun onPush(s: CallState, push: CallPush): Step = when (push) {
        is CallPush.IncomingCall -> onIncoming(s, push)
        is CallPush.Answered -> onAnsweredPush(s, push)
        is CallPush.Ended -> onEndedPush(s, push)
        is CallPush.MessageIn -> s to NONE
    }

    private fun onIncoming(s: CallState, push: CallPush.IncomingCall): Step {
        if (push.callId in endedIds) return s to NONE
        return when (s) {
            CallState.Idle, is CallState.Ended ->
                CallState.Ringing(push.callId, push.fromName, push.emergency) to listOf(CallEffect.StartRinging)
            else ->
                if (s.liveCallId() == push.callId) {
                    s to NONE // duplicate push of the call already shown
                } else {
                    remember(push.callId)
                    s to listOf(CallEffect.CallApi(CallOp.REJECT, push.callId)) // busy: keep the current call
                }
        }
    }

    private fun onAnsweredPush(s: CallState, push: CallPush.Answered): Step {
        if (push.callId in endedIds) return s to NONE
        if (s !is CallState.Outgoing || (s.callId != CallState.PENDING_ID && s.callId != push.callId)) return s to NONE
        return openMedia(push.callId, push.mediaUrl, push.mediaToken)
    }

    private fun onEndedPush(s: CallState, push: CallPush.Ended): Step {
        remember(push.callId)
        return if (s.liveCallId() == push.callId) finish(s, push.callId, endReasonVi(push.reason)) else s to NONE
    }

    // ---- user actions ----

    private fun onUserAnswer(s: CallState): Step =
        if (s is CallState.Ringing) {
            CallState.Connecting(s.callId) to listOf(CallEffect.StopRinging, CallEffect.CallApi(CallOp.ANSWER, s.callId))
        } else {
            s to NONE
        }

    private fun onUserReject(s: CallState): Step {
        if (s !is CallState.Ringing) return s to NONE
        remember(s.callId)
        return CallState.Idle to listOf(CallEffect.StopRinging, CallEffect.CallApi(CallOp.REJECT, s.callId))
    }

    private fun onUserHangup(s: CallState): Step = when (s) {
        is CallState.Ringing -> onUserReject(s)
        is CallState.Outgoing ->
            if (s.callId == CallState.PENDING_ID) {
                // Cancelled before the server replied; the late StartReply hangs the orphan up.
                CallState.Ended(s.callId, ENDED_GENERIC) to listOf(CallEffect.ShowEnded(ENDED_GENERIC))
            } else {
                remember(s.callId)
                CallState.Ended(s.callId, ENDED_GENERIC) to
                    listOf(CallEffect.CallApi(CallOp.HANGUP, s.callId), CallEffect.ShowEnded(ENDED_GENERIC))
            }
        is CallState.Connecting, is CallState.Active -> {
            val id = s.liveCallId()!!
            remember(id)
            CallState.Ended(id, ENDED_GENERIC) to listOf(
                CallEffect.CloseMedia,
                CallEffect.CallApi(CallOp.HANGUP, id),
                CallEffect.ShowEnded(ENDED_GENERIC),
            )
        }
        else -> s to NONE
    }

    private fun onUserCall(s: CallState, ownerUserId: String): Step =
        if (s == CallState.Idle || s is CallState.Ended) {
            CallState.Outgoing(CallState.PENDING_ID) to listOf(CallEffect.StartCall(ownerUserId))
        } else {
            s to NONE
        }

    // ---- API replies ----

    private fun onStartReply(s: CallState, result: ApiResult<String>): Step {
        if (result !is ApiResult.Ok) {
            return if (s is CallState.Outgoing && s.callId == CallState.PENDING_ID) {
                finish(s, s.callId, failureVi(result, START_FAILED))
            } else {
                s to NONE
            }
        }
        val id = result.data
        return when {
            s is CallState.Outgoing && s.callId == CallState.PENDING_ID ->
                if (id in endedIds) finish(s, id, ENDED_GENERIC) else CallState.Outgoing(id) to NONE
            s.liveCallId() == id -> s to NONE // adopted already through call_answered
            else -> s to listOf(CallEffect.CallApi(CallOp.HANGUP, id)) // user cancelled meanwhile: no orphan call
        }
    }

    private fun onAnswerReply(s: CallState, callId: String, result: ApiResult<CallAnswer>): Step {
        if (s !is CallState.Connecting || s.callId != callId || mediaOpenedFor == callId) return s to NONE
        return when (result) {
            is ApiResult.Ok ->
                result.data.mediaToken
                    ?.let { openMedia(callId, result.data.mediaUrl, it) }
                    ?: (s to listOf(CallEffect.CallApi(CallOp.GET, callId))) // token already handed out: ask again
            else -> finish(s, callId, failureVi(result, ENDED_GENERIC))
        }
    }

    private fun onSync(s: CallState, callId: String, result: ApiResult<CallInfo>): Step {
        if (s.liveCallId() != callId) return s to NONE
        return when (result) {
            is ApiResult.Ok -> {
                val info = result.data
                when {
                    info.state in TERMINAL_STATES -> finish(s, callId, endReasonVi(info.endReason ?: info.state))
                    info.state == "active" && s is CallState.Ringing -> finish(s, callId, ENDED_GENERIC) // answered elsewhere
                    info.state == "active" && info.mediaUrl != null && info.mediaToken != null &&
                        (s is CallState.Connecting || s is CallState.Outgoing) ->
                        openMedia(callId, info.mediaUrl, info.mediaToken)
                    else -> s to NONE
                }
            }
            is ApiResult.Unauthorized -> finish(s, callId, SESSION_EXPIRED)
            is ApiResult.Failed ->
                if (result.httpStatus >= 500) s to NONE else finish(s, callId, ENDED_GENERIC)
            is ApiResult.Network -> s to NONE // no news is not "ended"
        }
    }

    private fun onSocket(s: CallState, event: CallEvent.SocketState): Step = when (event.state) {
        "active" ->
            if (s is CallState.Connecting) CallState.Active(s.callId, clock()) to NONE else s to NONE
        "ended" ->
            if (s is CallState.Connecting || s is CallState.Active) {
                finish(s, s.liveCallId()!!, endReasonVi(event.reason))
            } else {
                s to NONE
            }
        else -> s to NONE
    }

    // ---- helpers ----

    private fun openMedia(callId: String, url: String, token: String): Step {
        if (mediaOpenedFor == callId) return CallState.Connecting(callId) to NONE
        mediaOpenedFor = callId
        return CallState.Connecting(callId) to listOf(CallEffect.OpenMedia(url, token))
    }

    /** Ends the live call [s] (id [callId]) with every cleanup effect its phase needs. */
    private fun finish(s: CallState, callId: String, reasonVi: String): Step {
        remember(callId)
        val effects = buildList {
            val mediaOpen = mediaOpenedFor == callId
            if (s is CallState.Ringing || (s is CallState.Connecting && !mediaOpen)) add(CallEffect.StopRinging)
            if (s is CallState.Active || (s is CallState.Connecting && mediaOpen)) add(CallEffect.CloseMedia)
            add(CallEffect.ShowEnded(reasonVi))
        }
        return CallState.Ended(callId, reasonVi) to effects
    }

    private fun remember(callId: String) {
        if (callId == CallState.PENDING_ID) return
        endedIds.remove(callId)
        endedIds.add(callId)
        while (endedIds.size > MAX_ENDED_IDS) endedIds.remove(endedIds.first())
    }

    private fun failureVi(result: ApiResult<*>, default: String): String = when (result) {
        is ApiResult.Unauthorized -> SESSION_EXPIRED
        is ApiResult.Network -> "Không kết nối được máy chủ"
        is ApiResult.Failed -> if (result.httpStatus >= 500) "Máy chủ đang gặp lỗi, thử lại sau" else default
        is ApiResult.Ok -> default
    }

    private fun CallState.liveCallId(): String? = when (this) {
        is CallState.Ringing -> callId
        is CallState.Outgoing -> callId
        is CallState.Connecting -> callId
        is CallState.Active -> callId
        else -> null
    }

    private companion object {
        const val MAX_ENDED_IDS = 50
        const val ENDED_GENERIC = "Cuộc gọi đã kết thúc"
        const val SESSION_EXPIRED = "Phiên đăng nhập hết hạn"
        const val START_FAILED = "Không gọi được người này"
        val TERMINAL_STATES = setOf("ended", "missed", "rejected", "failed")
        val NONE: List<CallEffect> = emptyList()
    }
}

private typealias Step = Pair<CallState, List<CallEffect>>
