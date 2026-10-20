package com.youreyes.app.messages

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.youreyes.app.MainActivity
import com.youreyes.app.calls.CallNotifications
import com.youreyes.app.calls.CallPush

/** Notifications for messages from the glasses. Content is shown to the user, never logged. */
object MessageNotifications {
    private const val TAG = "MessageNotifications"
    const val CHANNEL_MESSAGES = "seevia_messages"
    const val CHANNEL_EMERGENCY = "seevia_emergency_messages"
    private const val MAX_BODY = 300

    fun ensureChannels(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_MESSAGES, "Tin nhắn từ kính", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Tin nhắn người đeo kính gửi cho bạn"
            },
        )
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_EMERGENCY, "Tin nhắn khẩn cấp", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Tin KHẨN CẤP kèm vị trí khi người đeo kính cần giúp"
                setBypassDnd(true)
                enableVibration(true)
            },
        )
    }

    /** Posts (or, with the same message, replaces) the notification; tapping opens [route]. */
    fun show(context: Context, push: CallPush.MessageIn, route: String) {
        ensureChannels(context)
        val emergency = push.text.startsWith(ChatUiState.EMERGENCY_PREFIX)
        val tap = PendingIntent.getActivity(
            context,
            push.messageId.hashCode(),
            MainActivity.routeIntent(context, route),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val title = push.fromName.ifBlank { "Tin nhắn từ kính" }
        val body = push.text.take(MAX_BODY)
        val notification = NotificationCompat.Builder(context, if (emergency) CHANNEL_EMERGENCY else CHANNEL_MESSAGES)
            .setSmallIcon(android.R.drawable.ic_dialog_email)
            .setContentTitle(if (emergency) "KHẨN CẤP — $title" else title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setCategory(if (emergency) NotificationCompat.CATEGORY_ALARM else NotificationCompat.CATEGORY_MESSAGE)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setOnlyAlertOnce(true)
            .setAutoCancel(true)
            .setContentIntent(tap)
            .build()
        if (!CallNotifications.canNotify(context)) {
            Log.w(TAG, "Message notification not shown: notifications are off")
            return
        }
        try {
            NotificationManagerCompat.from(context).notify(push.messageId.hashCode(), notification)
        } catch (e: SecurityException) {
            Log.w(TAG, "Message notification not shown: ${e.javaClass.simpleName}")
        }
    }
}
