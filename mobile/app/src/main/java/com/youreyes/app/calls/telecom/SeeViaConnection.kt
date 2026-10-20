package com.youreyes.app.calls.telecom

import android.content.Context
import android.net.Uri
import android.os.Build
import android.telecom.Connection
import android.telecom.DisconnectCause
import androidx.annotation.RequiresApi
import com.youreyes.app.calls.CallController
import com.youreyes.app.calls.CallNotifications

/**
 * One incoming call as Android's Telecom stack sees it (self-managed: the app draws its own UI
 * and plays its own ringtone; Telecom gives lock-screen priority, Bluetooth answer/hang-up and
 * proper handling next to a SIM call).
 */
@RequiresApi(Build.VERSION_CODES.O)
class SeeViaConnection(
    private val context: Context,
    val callId: String,
    private val callerName: String,
    private val emergency: Boolean,
) : Connection() {

    init {
        connectionProperties = connectionProperties or PROPERTY_SELF_MANAGED
        connectionCapabilities = connectionCapabilities or CAPABILITY_MUTE
        setAudioModeIsVoip(true)
        setAddress(Uri.fromParts("sip", "seevia", null), android.telecom.TelecomManager.PRESENTATION_ALLOWED)
        setCallerDisplayName(CallNotifications.displayName(callerName), android.telecom.TelecomManager.PRESENTATION_ALLOWED)
        setRinging()
    }

    override fun onShowIncomingCallUi() {
        CallController.showRingingUi(context, callerName, emergency)
    }

    override fun onAnswer() {
        CallController.answerFromSystem(context)
    }

    override fun onAnswer(videoState: Int) = onAnswer()

    override fun onReject() {
        CallController.reject()
    }

    override fun onDisconnect() {
        CallController.hangup()
        end(DisconnectCause.LOCAL)
    }

    override fun onAbort() {
        end(DisconnectCause.CANCELED)
    }

    /** Telecom must be told when the app ends the call itself, or the call lingers in the system. */
    fun end(cause: Int) {
        if (state == STATE_DISCONNECTED) return
        setDisconnected(DisconnectCause(cause))
        destroy()
        TelecomBridge.connectionEnded(this)
    }

    fun markActive() {
        if (state != STATE_DISCONNECTED) setActive()
    }
}
