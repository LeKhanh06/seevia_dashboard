package com.youreyes.app.calls.telecom

import android.os.Build
import android.telecom.Connection
import android.telecom.ConnectionRequest
import android.telecom.ConnectionService
import android.telecom.DisconnectCause
import android.telecom.PhoneAccountHandle
import androidx.annotation.RequiresApi
import com.youreyes.app.calls.CallController
import com.youreyes.app.calls.CallState

/** Telecom entry point; the app only ever creates incoming connections here (outgoing calls bypass Telecom). */
@RequiresApi(Build.VERSION_CODES.O)
class SeeViaConnectionService : ConnectionService() {

    override fun onCreateIncomingConnection(
        connectionManagerPhoneAccount: PhoneAccountHandle?,
        request: ConnectionRequest?,
    ): Connection {
        val extras = request?.extras?.getBundle(android.telecom.TelecomManager.EXTRA_INCOMING_CALL_EXTRAS)
        val callId = extras?.getString(TelecomBridge.EXTRA_CALL_ID).orEmpty()
        // Telecom is asynchronous: by now the call may be over or replaced. Refuse instead of
        // handing back a connection that would ring for nothing.
        if (!isRingingFor(callId)) {
            return Connection.createFailedConnection(DisconnectCause(DisconnectCause.CANCELED))
        }
        val connection = SeeViaConnection(
            context = applicationContext,
            callId = callId,
            callerName = extras?.getString(TelecomBridge.EXTRA_NAME).orEmpty(),
            emergency = extras?.getBoolean(TelecomBridge.EXTRA_EMERGENCY) == true,
        )
        TelecomBridge.connectionCreated(connection)
        return connection
    }

    override fun onCreateIncomingConnectionFailed(
        connectionManagerPhoneAccount: PhoneAccountHandle?,
        request: ConnectionRequest?,
    ) {
        // Telecom refused (another self-managed call, or the account is off): ring with a plain
        // full-screen notification instead, but only for the call that is still ringing.
        val extras = request?.extras?.getBundle(android.telecom.TelecomManager.EXTRA_INCOMING_CALL_EXTRAS)
        if (!isRingingFor(extras?.getString(TelecomBridge.EXTRA_CALL_ID).orEmpty())) return
        CallController.showRingingUi(
            applicationContext,
            extras?.getString(TelecomBridge.EXTRA_NAME).orEmpty(),
            extras?.getBoolean(TelecomBridge.EXTRA_EMERGENCY) == true,
        )
    }

    private fun isRingingFor(callId: String): Boolean {
        CallController.init(applicationContext)
        val state = CallController.state.value
        return callId.isNotEmpty() && state is CallState.Ringing && state.callId == callId
    }

    override fun onCreateOutgoingConnectionFailed(
        connectionManagerPhoneAccount: PhoneAccountHandle?,
        request: ConnectionRequest?,
    ) = Unit
}
