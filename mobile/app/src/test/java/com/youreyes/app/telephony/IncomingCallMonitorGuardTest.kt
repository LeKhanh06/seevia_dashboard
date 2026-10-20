package com.youreyes.app.telephony

import com.youreyes.app.calls.ActiveVoipCall
import com.youreyes.app.calls.CallState
import com.youreyes.app.calls.isVoipActive
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class IncomingCallMonitorGuardTest {

    @Test
    fun voip_call_is_never_reported_as_sim_call() {
        assertFalse(shouldReportSimCall(voipActive = true))
    }

    @Test
    fun sim_call_is_reported_when_no_voip_call() {
        assertTrue(shouldReportSimCall(voipActive = false))
    }

    @Test
    fun voip_is_active_from_ringing_until_ended() {
        assertTrue(isVoipActive(CallState.Ringing("c", "Mẹ", false)))
        assertTrue(isVoipActive(CallState.Outgoing(CallState.PENDING_ID)))
        assertTrue(isVoipActive(CallState.Connecting("c")))
        assertTrue(isVoipActive(CallState.Active("c", 0L)))
        assertFalse(isVoipActive(CallState.Ended("c", "x")))
        assertFalse(isVoipActive(CallState.Idle))
    }

    @Test
    fun flag_defaults_to_inactive() {
        assertFalse(ActiveVoipCall.isActive)
    }
}
