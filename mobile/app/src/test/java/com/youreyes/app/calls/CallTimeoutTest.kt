package com.youreyes.app.calls

import com.youreyes.app.network.ApiResult
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Outgoing and connecting calls cannot hang forever: timeouts, polling and cleanup (review fixes 2, 6). */
class CallTimeoutTest {

    private var now = 100_000L
    private val machine = CallStateMachine(clock = { now })

    private fun info(id: String, state: String, reason: String? = null, token: String? = null) =
        CallInfo(id, "to_glasses", state, reason, false, "Cô Sáu", "wss://media", token)

    private fun outgoing(id: String = "c9") {
        machine.handle(CallEvent.UserCall("owner-1"))
        machine.handle(CallEvent.StartReply(ApiResult.Ok(id)))
        assertEquals(CallState.Outgoing(id), machine.state)
    }

    private fun answerRinging(id: String = "c1", token: String? = "tok") {
        machine.handle(CallEvent.Push(CallPush.IncomingCall(id, "Cô Sáu", false)))
        machine.handle(CallEvent.UserAnswer)
        machine.handle(CallEvent.AnswerReply(id, ApiResult.Ok(CallAnswer(id, "wss://media", token))))
    }

    // ---- outgoing ----

    @Test
    fun `outgoing call nobody answers ends after 45 seconds and hangs up`() {
        outgoing("c9")
        now += 44_999
        assertFalse(machine.handle(CallEvent.Tick).any { it is CallEffect.ShowEnded })
        now += 1
        val effects = machine.handle(CallEvent.Tick)

        assertEquals(CallState.Ended("c9", "Không ai nghe máy"), machine.state)
        assertTrue(CallEffect.CallApi(CallOp.HANGUP, "c9") in effects)
        assertTrue(CallEffect.ShowEnded("Không ai nghe máy") in effects)
        // A late `call_answered` for the dead call opens nothing.
        assertTrue(machine.handle(CallEvent.Push(CallPush.Answered("c9", "wss://m", "t"))).isEmpty())
    }

    @Test
    fun `outgoing timeout before the server replied has nothing to hang up until the reply arrives`() {
        machine.handle(CallEvent.UserCall("owner-1"))
        now += 45_000
        val effects = machine.handle(CallEvent.Tick)
        assertEquals(CallState.Ended(CallState.PENDING_ID, "Không ai nghe máy"), machine.state)
        assertTrue(effects.none { it is CallEffect.CallApi })

        val late = machine.handle(CallEvent.StartReply(ApiResult.Ok("c9")))
        assertEquals(listOf<CallEffect>(CallEffect.CallApi(CallOp.HANGUP, "c9")), late)
    }

    @Test
    fun `outgoing call asks the server every 5 seconds in case call_answered or call_ended was lost`() {
        outgoing("c9")
        now += 4_999
        assertTrue(machine.handle(CallEvent.Tick).isEmpty())
        now += 1
        assertEquals(listOf<CallEffect>(CallEffect.CallApi(CallOp.GET, "c9")), machine.handle(CallEvent.Tick))
        now += 1
        assertTrue(machine.handle(CallEvent.Tick).isEmpty())
        now += 4_999
        assertEquals(listOf<CallEffect>(CallEffect.CallApi(CallOp.GET, "c9")), machine.handle(CallEvent.Tick))
    }

    @Test
    fun `no polling before the call id is known`() {
        machine.handle(CallEvent.UserCall("owner-1"))
        now += 10_000
        assertTrue(machine.handle(CallEvent.Tick).isEmpty())
    }

    @Test
    fun `poll finding the call active with a token opens the audio`() {
        outgoing("c9")
        val effects = machine.handle(CallEvent.SyncResult("c9", ApiResult.Ok(info("c9", "active", token = "tok"))))
        assertEquals(CallState.Connecting("c9"), machine.state)
        assertTrue(effects.any { it is CallEffect.OpenMedia })
    }

    @Test
    fun `poll finding the call over ends it with the reason`() {
        val cases = listOf(
            Triple("ended", "glasses_hangup", "Cuộc gọi đã kết thúc"),
            Triple("missed", null, "Không ai nghe máy"),
            Triple("rejected", null, "Cuộc gọi bị từ chối"),
        )
        for ((state, reason, text) in cases) {
            val m = CallStateMachine(clock = { now })
            m.handle(CallEvent.UserCall("owner-1"))
            m.handle(CallEvent.StartReply(ApiResult.Ok("c-$state")))
            val effects = m.handle(CallEvent.SyncResult("c-$state", ApiResult.Ok(info("c-$state", state, reason))))
            assertEquals(CallState.Ended("c-$state", text), m.state)
            assertTrue(CallEffect.ShowEnded(text) in effects)
        }
    }

    // ---- connecting ----

    @Test
    fun `connecting without an active state for 15 seconds ends and cleans up`() {
        answerRinging("c1")
        now += 14_999
        assertTrue(machine.handle(CallEvent.Tick).isEmpty())
        now += 1
        val effects = machine.handle(CallEvent.Tick)

        assertEquals(CallState.Ended("c1", "Mất kết nối âm thanh"), machine.state)
        assertTrue(CallEffect.CloseMedia in effects)
        assertTrue(CallEffect.CallApi(CallOp.HANGUP, "c1") in effects)
        assertTrue(CallEffect.ShowEnded("Mất kết nối âm thanh") in effects)
    }

    @Test
    fun `the 15 seconds count from opening the audio, not from the tap on Nghe`() {
        machine.handle(CallEvent.Push(CallPush.IncomingCall("c1", "Cô Sáu", false)))
        machine.handle(CallEvent.UserAnswer)
        now += 10_000 // slow answer request
        machine.handle(CallEvent.AnswerReply("c1", ApiResult.Ok(CallAnswer("c1", "wss://media", "tok"))))
        now += 14_999
        assertTrue(machine.handle(CallEvent.Tick).isEmpty())
        now += 1
        assertTrue(machine.handle(CallEvent.Tick).any { it is CallEffect.ShowEnded })
    }

    @Test
    fun `an answered outgoing call gets its own 15 seconds`() {
        outgoing("c9")
        now += 30_000
        machine.handle(CallEvent.Push(CallPush.Answered("c9", "wss://m", "t")))
        now += 14_999
        assertTrue(machine.handle(CallEvent.Tick).isEmpty())
        now += 1
        assertEquals(CallState.Ended("c9", "Mất kết nối âm thanh"), run { machine.handle(CallEvent.Tick); machine.state })
    }

    @Test
    fun `active server reply without a token forever still ends after 15 seconds`() {
        answerRinging("c1", token = null) // token already handed out: the machine asks again
        machine.handle(CallEvent.SyncResult("c1", ApiResult.Ok(info("c1", "active", token = null))))
        assertEquals(CallState.Connecting("c1"), machine.state)

        now += 15_000
        val effects = machine.handle(CallEvent.Tick)

        assertEquals(CallState.Ended("c1", "Mất kết nối âm thanh"), machine.state)
        assertTrue(CallEffect.CallApi(CallOp.HANGUP, "c1") in effects)
        assertFalse(CallEffect.CloseMedia in effects) // audio was never opened
    }

    @Test
    fun `a call that reached active is not timed out`() {
        answerRinging("c1")
        machine.handle(CallEvent.SocketState("active", null))
        now += 600_000
        assertTrue(machine.handle(CallEvent.Tick).isEmpty())
        assertTrue(machine.state is CallState.Active)
    }

    @Test
    fun `tick does nothing outside outgoing and connecting`() {
        assertTrue(machine.handle(CallEvent.Tick).isEmpty())
        machine.handle(CallEvent.Push(CallPush.IncomingCall("c1", "x", false)))
        now += 600_000
        assertTrue(machine.handle(CallEvent.Tick).isEmpty())
        assertTrue(machine.state is CallState.Ringing)
    }

    // ---- API errors while answering (fix 6) ----

    @Test
    fun `answer lost to the network still hangs the call up`() {
        machine.handle(CallEvent.Push(CallPush.IncomingCall("c1", "Cô Sáu", false)))
        machine.handle(CallEvent.UserAnswer)
        val effects = machine.handle(CallEvent.AnswerReply("c1", ApiResult.Network(java.io.IOException())))

        assertTrue(machine.state is CallState.Ended)
        assertTrue(CallEffect.CallApi(CallOp.HANGUP, "c1") in effects)
    }

    @Test
    fun `answer refused with 409 needs no hangup`() {
        machine.handle(CallEvent.Push(CallPush.IncomingCall("c1", "Cô Sáu", false)))
        machine.handle(CallEvent.UserAnswer)
        val effects = machine.handle(CallEvent.AnswerReply("c1", ApiResult.Failed(409, "CALL_STATE_CONFLICT", "")))

        assertTrue(machine.state is CallState.Ended)
        assertTrue(effects.none { it is CallEffect.CallApi })
    }

    @Test
    fun `start lost to the network has no call id to hang up`() {
        machine.handle(CallEvent.UserCall("owner-1"))
        val effects = machine.handle(CallEvent.StartReply(ApiResult.Network(java.io.IOException())))

        assertTrue(machine.state is CallState.Ended)
        assertTrue(effects.none { it is CallEffect.CallApi })
    }
}
