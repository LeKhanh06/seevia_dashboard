package com.youreyes.app.calls

/**
 * True while one of this app's own internet calls is live, from the moment it rings (or we start
 * calling) until it ends. [com.youreyes.app.telephony.IncomingCallMonitor] reads it so a VoIP call is
 * never reported to the glasses as a SIM call (protocol 6.1 vs 7).
 */
object ActiveVoipCall {
    @Volatile
    var isActive: Boolean = false
}

/** Ringing, calling, connecting and talking all count; Ended is only the closing screen. */
fun isVoipActive(state: CallState): Boolean = when (state) {
    is CallState.Ringing, is CallState.Outgoing, is CallState.Connecting, is CallState.Active -> true
    is CallState.Ended, CallState.Idle -> false
}
