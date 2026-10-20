package com.youreyes.app.calls

import android.Manifest
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat

/**
 * Keeps the process (and the microphone) alive while a call is calling, connecting or talking.
 * Type `phoneCall`, plus `microphone` when RECORD_AUDIO is granted (Android 14 refuses the
 * microphone type without it). [CallController] starts and stops it from the call state.
 */
class CallForegroundService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val name = intent?.getStringExtra(EXTRA_NAME).orEmpty()
        val notification = CallNotifications.ongoing(this, name)
        try {
            var type = 0
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                type = ServiceInfo.FOREGROUND_SERVICE_TYPE_PHONE_CALL
                if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                    type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
                }
            }
            ServiceCompat.startForeground(this, CallNotifications.ID_ONGOING, notification, type)
        } catch (e: Exception) {
            // Started from a state Android does not allow (background without exemption): the call
            // still works while the screen is on; it just may not survive being backgrounded.
            Log.w(TAG, "Foreground start refused: ${e.javaClass.simpleName}")
            stopSelf()
        }
        return START_NOT_STICKY
    }

    companion object {
        private const val TAG = "CallForegroundService"
        private const val EXTRA_NAME = "name"

        fun start(context: Context, name: String) {
            val intent = Intent(context, CallForegroundService::class.java).putExtra(EXTRA_NAME, name)
            runCatching { ContextCompat.startForegroundService(context, intent) }
                .onFailure { Log.w(TAG, "Cannot start call service: ${it.javaClass.simpleName}") }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, CallForegroundService::class.java))
        }
    }
}
