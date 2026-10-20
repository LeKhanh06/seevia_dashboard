package com.youreyes.app.command

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.youreyes.app.actions.ActionRegistry
import com.youreyes.app.core.AppConfig
import com.youreyes.app.network.DeviceApiClient
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger

/**
 * Runs pushed commands one at a time in a foreground service.
 *
 * Handlers wait seconds for the phone to confirm an outcome; a foreground
 * service keeps the process alive for that long (a cached process was seen
 * being killed mid-command). Before each command, unsent reports are retried.
 */
class CommandExecutionService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startAsForeground()
        val command = intent?.let(::readCommand)
        if (command == null) {
            Log.w(TAG, "Started without a complete command")
            if (running.get() == 0) stopSelf(startId)
            return START_NOT_STICKY
        }

        running.incrementAndGet()
        latestStartId = startId
        val context = applicationContext
        lanes.getValue(CommandLanes.laneFor(command.action)).execute {
            try {
                val store = CommandStore(context)
                val sender = DeviceApiClient().asReportSender()
                val credentials = AppConfig.deviceCredentials(context)
                if (credentials != null) ReportFlusher(store, sender).flush(credentials)
                val result = CommandDispatcher(store, ActionRegistry(), sender, context)
                    .dispatch(command, credentials)
                Log.i(TAG, "${command.action} ${command.requestId}: $result")
                if (credentials != null && result is DispatchResult.Executed && !result.reportSent) {
                    ReportRetryWorker.enqueue(context)
                }
            } catch (exc: Exception) {
                Log.e(TAG, "Command ${command.action} ${command.requestId} failed", exc)
            } finally {
                // stopSelf(id) is a no-op if a newer command started meanwhile, so a command
                // arriving right now never loses its foreground service.
                if (running.decrementAndGet() == 0) this@CommandExecutionService.stopSelf(latestStartId)
            }
        }
        return START_NOT_STICKY
    }

    @Deprecated("The service is stopped after foreground service timeout")
    override fun onTimeout(startId: Int) {
        Log.w(TAG, "Foreground service timeout startId=$startId")
        stopSelf()
    }

    override fun onTimeout(startId: Int, fgsType: Int) {
        Log.w(TAG, "Foreground service timeout startId=$startId type=$fgsType")
        stopSelf()
    }

    private fun startAsForeground() {
        val manager = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && manager?.getNotificationChannel(CHANNEL_ID) == null) {
            manager?.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Thực hiện lệnh", NotificationManager.IMPORTANCE_LOW)
            )
        }
        val notification: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("Đang thực hiện lệnh")
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SHORT_SERVICE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun readCommand(intent: Intent): PushCommand? {
        val data = listOf("request_id", "user_id", "device_id", "action", "params_json", "maps_url")
            .mapNotNull { key -> intent.getStringExtra(key)?.let { key to it } }
            .toMap()
        val command = PushCommand.fromData(data) ?: return null
        return if (intent.hasExtra("expires_at_ms")) {
            command.copy(expiresAtMillis = intent.getLongExtra("expires_at_ms", 0L))
        } else command
    }

    companion object {
        private const val TAG = "CommandExecution"
        private const val CHANNEL_ID = "appdemo_execution"
        private const val NOTIFICATION_ID = 4001
        private val lanes = mapOf(Lane.FAST to Executors.newSingleThreadExecutor(), Lane.SLOW to Executors.newSingleThreadExecutor())
        private val running = AtomicInteger(0)
        @Volatile private var latestStartId = 0

        fun start(context: Context, command: PushCommand) {
            val intent = Intent(context, CommandExecutionService::class.java)
                .putExtra("request_id", command.requestId)
                .putExtra("user_id", command.userId)
                .putExtra("device_id", command.deviceId)
                .putExtra("action", command.action)
                .putExtra("params_json", command.paramsJson)
            command.expiresAtMillis?.let { intent.putExtra("expires_at_ms", it) }
            command.mapsUrl?.let { intent.putExtra("maps_url", it) }
            ContextCompat.startForegroundService(context, intent)
        }
    }
}
