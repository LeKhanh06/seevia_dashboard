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
        // Every startForegroundService() call, even one meant to stop the service, must be
        // answered with startForeground() within seconds or Android kills the app. So always
        // go foreground first, and only then stop.
        startForegroundSafely(intent?.getStringExtra(EXTRA_NAME).orEmpty())
        if (intent?.action == ACTION_STOP) {
            ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
            stopSelf(startId) // no-op if a newer start (a new call) is already queued
        }
        return START_NOT_STICKY
    }

    private fun startForegroundSafely(name: String) {
        val notification = CallNotifications.ongoing(this, name)
        val withMic = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        val attempts = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val phone = ServiceInfo.FOREGROUND_SERVICE_TYPE_PHONE_CALL
            // Android 14 refuses the microphone type in some states; the call can still run as phoneCall.
            if (withMic) listOf(phone or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE, phone) else listOf(phone)
        } else {
            listOf(0)
        }
        for (type in attempts) {
            try {
                ServiceCompat.startForeground(this, CallNotifications.ID_ONGOING, notification, type)
                return
            } catch (e: Exception) {
                Log.w(TAG, "Foreground type $type refused: ${e.javaClass.simpleName}")
            }
        }
        stopSelf()
    }

    companion object {
        private const val TAG = "CallForegroundService"
        private const val EXTRA_NAME = "name"
        const val ACTION_STOP = "com.youreyes.app.calls.STOP_FOREGROUND"

        fun start(context: Context, name: String) {
            val intent = Intent(context, CallForegroundService::class.java).putExtra(EXTRA_NAME, name)
            runCatching { ContextCompat.startForegroundService(context, intent) }
                .onFailure { Log.w(TAG, "Cannot start call service: ${it.javaClass.simpleName}") }
        }

        /** Asks the service to stop itself (never `stopService`: see [onStartCommand]). */
        fun stop(context: Context) {
            val intent = Intent(context, CallForegroundService::class.java).setAction(ACTION_STOP)
            runCatching { ContextCompat.startForegroundService(context, intent) }
                .onFailure { Log.w(TAG, "Cannot stop call service: ${it.javaClass.simpleName}") }
        }
    }
}
