package com.youreyes.app.calls

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.Ringtone
import android.media.RingtoneManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.Person
import androidx.core.content.ContextCompat
import com.youreyes.app.MainActivity
import com.youreyes.app.calls.ui.CallActivity

/**
 * Every notification of the call feature, in Vietnamese, for people using the phone (not the
 * glasses: the wearer hears everything through the glasses). Never puts a token, a URL, a phone
 * number or message text into a log line.
 */
object CallNotifications {
    private const val TAG = "CallNotifications"

    const val CHANNEL_CALLS = "seevia_calls"
    const val CHANNEL_EMERGENCY = "seevia_emergency_calls"
    const val CHANNEL_ONGOING = "seevia_call_ongoing"
    const val CHANNEL_ACCOUNT = "seevia_account"

    const val ID_INCOMING = 7001
    const val ID_ONGOING = 7002
    const val ID_SESSION = 7003
    const val ID_LOGIN_NEEDED = 7004

    private const val INCOMING_TIMEOUT_MS = 45_000L

    const val ACTION_REJECT = "com.youreyes.app.calls.REJECT"
    const val ACTION_HANGUP = "com.youreyes.app.calls.HANGUP"

    fun ensureChannels(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        // The app plays the ringtone itself (self-managed calls), so the channels stay silent.
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_CALLS, "Cuộc gọi đến", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Cuộc gọi từ kính của người thân"
                setSound(null, null)
                enableVibration(false)
                lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            },
        )
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_EMERGENCY, "Cuộc gọi khẩn cấp", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Cuộc gọi khẩn cấp từ kính; vẫn đổ chuông khi bật không làm phiền nếu bạn cho phép"
                setSound(null, null)
                enableVibration(false)
                setBypassDnd(true)
                lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            },
        )
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ONGOING, "Đang trong cuộc gọi", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Hiện suốt cuộc gọi để cuộc gọi không bị hệ thống tắt"
                setSound(null, null)
            },
        )
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ACCOUNT, "Tài khoản SeeVia", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Nhắc đăng nhập để nhận cuộc gọi và tin nhắn"
            },
        )
    }

    private fun immutable(extra: Int = 0) = PendingIntent.FLAG_IMMUTABLE or extra

    private fun activityIntent(context: Context, requestCode: Int, intent: Intent): PendingIntent =
        PendingIntent.getActivity(context, requestCode, intent, immutable(PendingIntent.FLAG_UPDATE_CURRENT))

    private fun receiverIntent(context: Context, requestCode: Int, action: String): PendingIntent =
        PendingIntent.getBroadcast(
            context,
            requestCode,
            Intent(context, CallActionReceiver::class.java).setAction(action),
            immutable(PendingIntent.FLAG_UPDATE_CURRENT),
        )

    private fun person(name: String): Person = Person.Builder().setName(name.ifBlank { "Người thân" }).setImportant(true).build()

    /** Heads-up + full-screen incoming call (the screen over a locked phone). */
    fun showIncoming(context: Context, name: String, emergency: Boolean) {
        ensureChannels(context)
        val title = displayName(name)
        val open = activityIntent(context, 1, CallActivity.intent(context))
        val answer = activityIntent(context, 2, CallActivity.intent(context, answer = true))
        val decline = receiverIntent(context, 3, ACTION_REJECT)
        val builder = NotificationCompat.Builder(context, if (emergency) CHANNEL_EMERGENCY else CHANNEL_CALLS)
            .setSmallIcon(android.R.drawable.sym_action_call)
            .setContentTitle(if (emergency) "KHẨN CẤP: $title đang gọi" else "$title đang gọi")
            .setContentText(if (emergency) "Cuộc gọi khẩn cấp từ kính SeeVia" else "Cuộc gọi từ kính SeeVia")
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setOngoing(true)
            .setAutoCancel(false)
            .setOnlyAlertOnce(true)
            // A call nobody answered must not stay on the lock screen if every FCM cleanup is lost.
            .setTimeoutAfter(INCOMING_TIMEOUT_MS)
            .setContentIntent(open)
            .setFullScreenIntent(open, true)
            .setStyle(NotificationCompat.CallStyle.forIncomingCall(person(title), decline, answer))
        post(context, ID_INCOMING, builder.build())
    }

    fun cancelIncoming(context: Context) = NotificationManagerCompat.from(context).cancel(ID_INCOMING)

    /** The notification the foreground service shows for the whole call. */
    fun ongoing(context: Context, name: String): Notification {
        ensureChannels(context)
        val title = displayName(name)
        val open = activityIntent(context, 4, CallActivity.intent(context))
        val hangup = receiverIntent(context, 5, ACTION_HANGUP)
        return NotificationCompat.Builder(context, CHANNEL_ONGOING)
            .setSmallIcon(android.R.drawable.sym_action_call)
            .setContentTitle("Đang trong cuộc gọi")
            .setContentText(title)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setOngoing(true)
            .setContentIntent(open)
            .setStyle(NotificationCompat.CallStyle.forOngoingCall(person(title), hangup))
            .build()
    }

    /** A 401 anywhere: the session token has been cleared; tell the user how to continue. */
    fun showSessionExpired(context: Context) {
        ensureChannels(context)
        val open = activityIntent(context, 6, MainActivity.routeIntent(context, MainActivity.ROUTE_AUTH))
        val notification = NotificationCompat.Builder(context, CHANNEL_ACCOUNT)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("Phiên đăng nhập hết hạn")
            .setContentText("Phiên đăng nhập hết hạn — mở app để đăng nhập lại")
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setContentIntent(open)
            .build()
        post(context, ID_SESSION, notification)
    }

    /** A call or message push arrived but nobody is signed in on this phone. */
    fun showLoginNeeded(context: Context) {
        ensureChannels(context)
        val open = activityIntent(context, 7, MainActivity.routeIntent(context, MainActivity.ROUTE_AUTH))
        val notification = NotificationCompat.Builder(context, CHANNEL_ACCOUNT)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("Chưa đăng nhập")
            .setContentText("Hãy đăng nhập SeeVia để nhận cuộc gọi")
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setContentIntent(open)
            .build()
        post(context, ID_LOGIN_NEEDED, notification)
    }

    private fun post(context: Context, id: Int, notification: Notification) {
        // POST_NOTIFICATIONS may be denied; a refused notification must not crash a push handler.
        if (!canNotify(context)) {
            Log.w(TAG, "Notification $id not shown: notifications are off")
            return
        }
        try {
            NotificationManagerCompat.from(context).notify(id, notification)
        } catch (e: SecurityException) {
            Log.w(TAG, "Notification $id not shown: ${e.javaClass.simpleName}")
        }
    }

    /** True when Android would show our notifications (permission granted on API 33+, and not switched off). */
    fun canNotify(context: Context): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            return false
        }
        return NotificationManagerCompat.from(context).areNotificationsEnabled()
    }

    fun displayName(name: String): String = name.ifBlank { "Người thân" }
}

/** Handles the Từ chối / Cúp buttons of call notifications. */
class CallActionReceiver : android.content.BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        // The process may have been restarted since the notification was posted.
        CallController.init(context)
        if (CallController.state.value == CallState.Idle) {
            CallNotifications.cancelIncoming(context)
            return
        }
        when (intent.action) {
            CallNotifications.ACTION_REJECT -> CallController.reject()
            CallNotifications.ACTION_HANGUP -> CallController.hangup()
        }
    }
}

/** The incoming-call ringtone and vibration, played by the app (self-managed calls get no system ring). */
class CallRinger(private val context: Context) {
    private var ringtone: Ringtone? = null
    private var vibrator: Vibrator? = null
    private val handler = Handler(Looper.getMainLooper())

    // Ringtone.isLooping exists from Android 9; older versions replay it while it still rings.
    private val replay = object : Runnable {
        override fun run() {
            val current = synchronized(this@CallRinger) { ringtone } ?: return
            if (!current.isPlaying) runCatching { current.play() }
            handler.postDelayed(this, REPLAY_CHECK_MS)
        }
    }

    @Synchronized
    fun start(emergency: Boolean) {
        if (ringtone != null) return
        val uri = RingtoneManager.getDefaultUri(
            if (emergency) RingtoneManager.TYPE_ALARM else RingtoneManager.TYPE_RINGTONE,
        )
        ringtone = runCatching {
            RingtoneManager.getRingtone(context, uri)?.apply {
                audioAttributes = AudioAttributes.Builder()
                    // An emergency rings like an alarm so a silenced phone still wakes the person up.
                    .setUsage(if (emergency) AudioAttributes.USAGE_ALARM else AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build()
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) isLooping = true
                play()
            }
        }.getOrNull()
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) handler.postDelayed(replay, REPLAY_CHECK_MS)
        val vib = context.getSystemService(Vibrator::class.java)
        vibrator = vib
        runCatching {
            val pattern = longArrayOf(0, 800, 800)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                vib?.vibrate(VibrationEffect.createWaveform(pattern, 0))
            } else {
                @Suppress("DEPRECATION")
                vib?.vibrate(pattern, 0)
            }
        }
    }

    @Synchronized
    fun stop() {
        handler.removeCallbacks(replay)
        runCatching { ringtone?.stop() }
        runCatching { vibrator?.cancel() }
        ringtone = null
        vibrator = null
    }

    private companion object {
        const val REPLAY_CHECK_MS = 1_000L
    }
}
