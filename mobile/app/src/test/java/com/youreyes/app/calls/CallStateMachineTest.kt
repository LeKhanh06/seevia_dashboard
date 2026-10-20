package com.youreyes.app.calls

import com.youreyes.app.network.ApiResult
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CallStateMachineTest {

    private val machine = CallStateMachine(clock = { 5_000L })

    private fun incoming(id: String = "c1", name: String = "Cô Sáu", emergency: Boolean = false) =
        CallEvent.Push(CallPush.IncomingCall(id, name, emergency))

    private fun ended(id: String = "c1", reason: String? = "glasses_hangup") = CallEvent.Push(CallPush.Ended(id, reason))

    private fun answered(id: String = "c1") = CallEvent.Push(CallPush.Answered(id, "wss://media", "tok"))

    private fun callInfo(id: String = "c1", state: String = "active", reason: String? = null, token: String? = null) =
        CallInfo(id, "to_glasses", state, reason, false, "Cô Sáu", "wss://media", token)

    private fun ringing(id: String = "c1"): List<CallEffect> {
        val effects = machine.handle(incoming(id))
        assertEquals(CallState.Ringing(id, "Cô Sáu", false), machine.state)
        return effects
    }

    private fun connectingViaAnswer(id: String = "c1") {
        ringing(id)
        machine.handle(CallEvent.UserAnswer)
        machine.handle(CallEvent.AnswerReply(id, ApiResult.Ok(CallAnswer(id, "wss://media", "tok"))))
    }

    // --- Review focus 1: FCM late / duplicate / reordered ---

    @Test
    fun `incoming rings once for duplicate push`() {
        val first = machine.handle(incoming())
        val second = machine.handle(incoming())
        assertEquals(listOf<CallEffect>(CallEffect.StartRinging), first)
        assertTrue(second.isEmpty())
        assertEquals(CallState.Ringing("c1", "Cô Sáu", false), machine.state)
    }

    @Test
    fun `ended before incoming never rings`() {
        machine.handle(ended("c1", "missed"))
        val effects = machine.handle(incoming("c1"))
        assertEquals(CallState.Idle, machine.state)
        assertFalse(CallEffect.StartRinging in effects)
        assertTrue(effects.isEmpty())
    }

    @Test
    fun `ended after ringing stops ringing and shows reason`() {
        ringing()
        val effects = machine.handle(ended("c1", "missed"))
        assertEquals(CallState.Ended("c1", "Không ai nghe máy"), machine.state)
        assertTrue(CallEffect.StopRinging in effects)
        assertTrue(CallEffect.ShowEnded("Không ai nghe máy") in effects)
    }

    @Test
    fun `late incoming push after the call ended and the screen closed never rings`() {
        ringing("c1")
        machine.handle(ended("c1"))
        machine.handle(CallEvent.Dismiss)
        assertEquals(CallState.Idle, machine.state)
        assertTrue(machine.handle(incoming("c1")).isEmpty())
        assertEquals(CallState.Idle, machine.state)
    }

    @Test
    fun `ended push for an unrelated call is ignored`() {
        ringing("c1")
        val effects = machine.handle(ended("other"))
        assertTrue(effects.isEmpty())
        assertEquals(CallState.Ringing("c1", "Cô Sáu", false), machine.state)
    }

    @Test
    fun `new call after ended is allowed`() {
        ringing("c1")
        machine.handle(ended("c1"))
        assertEquals(listOf<CallEffect>(CallEffect.StartRinging), machine.handle(incoming("c2")))
    }

    @Test
    fun `ended ids memory is capped at 50`() {
        for (i in 1..60) machine.handle(ended("old-$i"))
        assertTrue(machine.endedIdCount() <= 50)
        // the newest is still remembered, the oldest was forgotten
        assertTrue(machine.handle(incoming("old-60")).isEmpty())
        assertEquals(listOf<CallEffect>(CallEffect.StartRinging), machine.handle(incoming("old-1")))
    }

    // --- Review focus 2: answer on a call that is gone / 401 ---

    @Test
    fun `answer 409 stops ringing and shows ended`() {
        ringing()
        machine.handle(CallEvent.UserAnswer)
        val effects = machine.handle(
            CallEvent.AnswerReply("c1", ApiResult.Failed(409, "CALL_STATE_CONFLICT", "Call already ended"))
        )
        assertEquals(CallState.Ended("c1", "Cuộc gọi đã kết thúc"), machine.state)
        assertTrue(CallEffect.StopRinging in effects)
        assertTrue(CallEffect.ShowEnded("Cuộc gọi đã kết thúc") in effects)
        assertTrue(effects.none { it is CallEffect.OpenMedia })
    }

    @Test
    fun `answer unauthorized shows relogin and never opens media`() {
        ringing()
        machine.handle(CallEvent.UserAnswer)
        val effects = machine.handle(CallEvent.AnswerReply("c1", ApiResult.Unauthorized))
        assertEquals(CallState.Ended("c1", "Phiên đăng nhập hết hạn"), machine.state)
        assertTrue(CallEffect.StopRinging in effects)
        assertTrue(CallEffect.ShowEnded("Phiên đăng nhập hết hạn") in effects)
        assertTrue(effects.none { it is CallEffect.OpenMedia })
    }

    @Test
    fun `answer network failure does not open media`() {
        ringing()
        machine.handle(CallEvent.UserAnswer)
        val effects = machine.handle(CallEvent.AnswerReply("c1", ApiResult.Network(java.io.IOException("down"))))
        assertTrue(machine.state is CallState.Ended)
        assertTrue(effects.none { it is CallEffect.OpenMedia })
    }

    @Test
    fun `user answer stops ringing and calls the answer api once`() {
        ringing()
        val effects = machine.handle(CallEvent.UserAnswer)
        assertEquals(listOf(CallEffect.StopRinging, CallEffect.CallApi(CallOp.ANSWER, "c1")), effects)
        assertEquals(CallState.Connecting("c1"), machine.state)
        assertTrue(machine.handle(CallEvent.UserAnswer).isEmpty())
    }

    @Test
    fun `answer ok opens media with token`() {
        ringing()
        machine.handle(CallEvent.UserAnswer)
        val effects = machine.handle(CallEvent.AnswerReply("c1", ApiResult.Ok(CallAnswer("c1", "wss://media", "tok"))))
        assertEquals(listOf<CallEffect>(CallEffect.OpenMedia("wss://media", "tok")), effects)
        assertEquals(CallState.Connecting("c1"), machine.state)
    }

    @Test
    fun `answer ok without token syncs and then opens media once`() {
        ringing()
        machine.handle(CallEvent.UserAnswer)
        val effects = machine.handle(CallEvent.AnswerReply("c1", ApiResult.Ok(CallAnswer("c1", "wss://media", null))))
        assertEquals(listOf<CallEffect>(CallEffect.CallApi(CallOp.GET, "c1")), effects)
        val sync = machine.handle(CallEvent.SyncResult("c1", ApiResult.Ok(callInfo(token = "tok"))))
        assertEquals(listOf<CallEffect>(CallEffect.OpenMedia("wss://media", "tok")), sync)
        assertTrue(machine.handle(CallEvent.SyncResult("c1", ApiResult.Ok(callInfo(token = "tok")))).isEmpty())
    }

    @Test
    fun `answer reply for another call is ignored`() {
        ringing("c1")
        machine.handle(CallEvent.UserAnswer)
        val effects = machine.handle(CallEvent.AnswerReply("zzz", ApiResult.Ok(CallAnswer("zzz", "wss://media", "tok"))))
        assertTrue(effects.isEmpty())
        assertEquals(CallState.Connecting("c1"), machine.state)
    }

    @Test
    fun `sync saying the call is over while ringing stops the ring`() {
        ringing()
        val effects = machine.handle(CallEvent.SyncResult("c1", ApiResult.Ok(callInfo(state = "missed"))))
        assertEquals(CallState.Ended("c1", "Không ai nghe máy"), machine.state)
        assertTrue(CallEffect.StopRinging in effects)
    }

    @Test
    fun `sync with no such call while ringing ends it`() {
        ringing()
        val effects = machine.handle(CallEvent.SyncResult("c1", ApiResult.Failed(404, "CALL_NOT_FOUND", "")))
        assertTrue(machine.state is CallState.Ended)
        assertTrue(CallEffect.StopRinging in effects)
    }

    // --- Outgoing ---

    @Test
    fun `user call starts an outgoing call and adopts the call id`() {
        val effects = machine.handle(CallEvent.UserCall("owner-1"))
        assertEquals(listOf<CallEffect>(CallEffect.StartCall("owner-1")), effects)
        assertEquals(CallState.Outgoing(CallState.PENDING_ID), machine.state)
        machine.handle(CallEvent.StartReply(ApiResult.Ok("c9")))
        assertEquals(CallState.Outgoing("c9"), machine.state)
    }

    @Test
    fun `start call failure ends with a reason`() {
        machine.handle(CallEvent.UserCall("owner-1"))
        val effects = machine.handle(CallEvent.StartReply(ApiResult.Failed(403, "CALL_NOT_ALLOWED", "")))
        assertTrue(machine.state is CallState.Ended)
        assertTrue(effects.any { it is CallEffect.ShowEnded })
    }

    @Test
    fun `start reply arriving after the user cancelled hangs up the orphan call`() {
        machine.handle(CallEvent.UserCall("owner-1"))
        machine.handle(CallEvent.UserHangup)
        val effects = machine.handle(CallEvent.StartReply(ApiResult.Ok("c9")))
        assertEquals(listOf<CallEffect>(CallEffect.CallApi(CallOp.HANGUP, "c9")), effects)
    }

    @Test
    fun `outgoing answered push opens media`() {
        machine.handle(CallEvent.UserCall("owner-1"))
        machine.handle(CallEvent.StartReply(ApiResult.Ok("c9")))
        val effects = machine.handle(answered("c9"))
        assertEquals(listOf<CallEffect>(CallEffect.OpenMedia("wss://media", "tok")), effects)
        assertEquals(CallState.Connecting("c9"), machine.state)
        assertTrue(machine.handle(answered("c9")).isEmpty())
    }

    @Test
    fun `answered push for a call that already ended is ignored`() {
        machine.handle(CallEvent.UserCall("owner-1"))
        machine.handle(CallEvent.StartReply(ApiResult.Ok("c9")))
        machine.handle(ended("c9", "busy"))
        assertTrue(machine.handle(answered("c9")).isEmpty())
    }

    @Test
    fun `outgoing busy ends with vi reason`() {
        machine.handle(CallEvent.UserCall("owner-1"))
        machine.handle(CallEvent.StartReply(ApiResult.Ok("c9")))
        val effects = machine.handle(ended("c9", "busy"))
        assertEquals(CallState.Ended("c9", "Kính đang bận"), machine.state)
        assertTrue(CallEffect.ShowEnded("Kính đang bận") in effects)
    }

    // --- Media / hangup ---

    @Test
    fun `socket active moves connecting to active with start time`() {
        connectingViaAnswer()
        machine.handle(CallEvent.SocketState("active", null))
        assertEquals(CallState.Active("c1", 5_000L), machine.state)
    }

    @Test
    fun `hangup sends api and closes media`() {
        connectingViaAnswer()
        machine.handle(CallEvent.SocketState("active", null))
        val effects = machine.handle(CallEvent.UserHangup)
        assertTrue(CallEffect.CloseMedia in effects)
        assertTrue(CallEffect.CallApi(CallOp.HANGUP, "c1") in effects)
        assertTrue(machine.state is CallState.Ended)
    }

    @Test
    fun `socket ended moves to ended`() {
        connectingViaAnswer()
        machine.handle(CallEvent.SocketState("active", null))
        val effects = machine.handle(CallEvent.SocketState("ended", "glasses_hangup"))
        assertEquals(CallState.Ended("c1", "Cuộc gọi đã kết thúc"), machine.state)
        assertTrue(CallEffect.CloseMedia in effects)
        assertTrue(CallEffect.ShowEnded("Cuộc gọi đã kết thúc") in effects)
    }

    @Test
    fun `socket media timeout reports lost audio`() {
        connectingViaAnswer()
        machine.handle(CallEvent.SocketState("ended", "media_timeout"))
        assertEquals(CallState.Ended("c1", "Mất kết nối âm thanh"), machine.state)
    }

    @Test
    fun `second incoming while active is rejected and the first call is kept`() {
        connectingViaAnswer("c1")
        machine.handle(CallEvent.SocketState("active", null))
        val effects = machine.handle(incoming("c2"))
        assertEquals(listOf<CallEffect>(CallEffect.CallApi(CallOp.REJECT, "c2")), effects)
        assertEquals(CallState.Active("c1", 5_000L), machine.state)
    }

    @Test
    fun `second incoming while ringing another call is rejected without a second ring`() {
        ringing("c1")
        val effects = machine.handle(incoming("c2"))
        assertEquals(listOf<CallEffect>(CallEffect.CallApi(CallOp.REJECT, "c2")), effects)
        assertEquals(CallState.Ringing("c1", "Cô Sáu", false), machine.state)
    }

    @Test
    fun `user reject stops ringing calls reject and returns to idle`() {
        ringing()
        val effects = machine.handle(CallEvent.UserReject)
        assertEquals(listOf(CallEffect.StopRinging, CallEffect.CallApi(CallOp.REJECT, "c1")), effects)
        assertEquals(CallState.Idle, machine.state)
        assertTrue(machine.handle(incoming("c1")).isEmpty())
    }

    @Test
    fun `end reasons map to vietnamese`() {
        assertEquals("Kính đang bận", endReasonVi("busy"))
        assertEquals("Kính không kết nối", endReasonVi("glasses_offline"))
        assertEquals("Không ai nghe máy", endReasonVi("missed"))
        assertEquals("Không ai nghe máy", endReasonVi("timeout"))
        assertEquals("Cuộc gọi bị từ chối", endReasonVi("rejected"))
        assertEquals("Đã huỷ liên kết người thân", endReasonVi("revoked"))
        assertEquals("Mất kết nối âm thanh", endReasonVi("media_timeout"))
        assertEquals("Mất kết nối âm thanh", endReasonVi("peer_disconnect"))
        assertEquals("Cuộc gọi quá 2 giờ", endReasonVi("max_duration"))
        assertEquals("Cuộc gọi đã kết thúc", endReasonVi("hangup"))
        assertEquals("Cuộc gọi đã kết thúc", endReasonVi(null))
    }
}
