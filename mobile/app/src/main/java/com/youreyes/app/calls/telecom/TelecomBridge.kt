package com.youreyes.app.calls.telecom

import android.content.ComponentName
import android.content.Context
import android.os.Build
import android.os.Bundle
import android.telecom.DisconnectCause
import android.telecom.PhoneAccount
import android.telecom.PhoneAccountHandle
import android.telecom.TelecomManager
import android.util.Log

/**
 * Everything the app asks of Telecom, behind plain functions that never throw and degrade to
 * "false / no-op" below Android 8 (self-managed calls need API 26) or when the system refuses.
 */
object TelecomBridge {
    private const val TAG = "TelecomBridge"
    private const val ACCOUNT_ID = "seevia_voip"

    const val EXTRA_CALL_ID = "seevia_call_id"
    const val EXTRA_NAME = "seevia_name"
    const val EXTRA_EMERGENCY = "seevia_emergency"

    @Volatile
    private var current: SeeViaConnection? = null

    private fun handle(context: Context): PhoneAccountHandle =
        PhoneAccountHandle(ComponentName(context, SeeViaConnectionService::class.java), ACCOUNT_ID)

    /** Call once at app start. Safe to repeat. */
    fun registerAccount(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        runCatching {
            val telecom = context.getSystemService(TelecomManager::class.java) ?: return
            val account = PhoneAccount.builder(handle(context), "SeeVia")
                .setCapabilities(PhoneAccount.CAPABILITY_SELF_MANAGED)
                .build()
            telecom.registerPhoneAccount(account)
        }.onFailure { Log.w(TAG, "PhoneAccount not registered: ${it.javaClass.simpleName}") }
    }

    /**
     * Asks Telecom to ring. True means Telecom took the call and will call back into
     * [SeeViaConnection.onShowIncomingCallUi] (or [SeeViaConnectionService.onCreateIncomingConnectionFailed]);
     * false means the caller must ring on its own.
     */
    fun reportIncoming(context: Context, callId: String, name: String, emergency: Boolean): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return false
        return runCatching {
            val telecom = context.getSystemService(TelecomManager::class.java) ?: return false
            val extras = Bundle().apply {
                putBundle(
                    TelecomManager.EXTRA_INCOMING_CALL_EXTRAS,
                    Bundle().apply {
                        putString(EXTRA_CALL_ID, callId)
                        putString(EXTRA_NAME, name)
                        putBoolean(EXTRA_EMERGENCY, emergency)
                    },
                )
                putParcelable(TelecomManager.EXTRA_PHONE_ACCOUNT_HANDLE, handle(context))
            }
            telecom.addNewIncomingCall(handle(context), extras)
            true
        }.getOrElse {
            Log.w(TAG, "addNewIncomingCall refused: ${it.javaClass.simpleName}")
            false
        }
    }

    internal fun connectionCreated(connection: SeeViaConnection) {
        current = connection
    }

    internal fun connectionEnded(connection: SeeViaConnection) {
        if (current === connection) current = null
    }

    /** The user answered somewhere other than Telecom's own UI (our screen / notification). */
    fun markActive() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) current?.markActive()
    }

    /** The call is over; [cause] is a [DisconnectCause] code. */
    fun endCall(cause: Int = DisconnectCause.LOCAL) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) current?.end(cause)
    }
}
