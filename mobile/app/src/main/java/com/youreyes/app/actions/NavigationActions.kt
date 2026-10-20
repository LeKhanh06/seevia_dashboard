package com.youreyes.app.actions

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.os.Build
import android.net.Uri
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import org.json.JSONObject
import java.util.UUID

private const val MAPS_PACKAGE = "com.google.android.apps.maps"

/**
 * `navigation_start`: notify the user and open Google Maps when tapped.
 *
 * An address without coordinates is geocoded so the result can report real
 * coordinates; if that fails Maps still navigates by address and lat/lng are null.
 */
class NavigationStartHandler : ActionHandler {
    @SuppressLint("MissingPermission") // notifications are enabled first; notify failures are converted to action errors
    override fun execute(context: Context?, paramsJson: String): ActionExecutionResult {
        if (context == null) return failure("NAVIGATION_START_FAILED", "No context available")
        val json = runCatching { JSONObject(paramsJson) }.getOrElse {
            return failure("NAVIGATION_START_FAILED", "Invalid navigation_start params: ${it.message}")
        }
        val destination = json.optJSONObject("destination")
        val target = NavigationUrls.target(destination)
            ?: return failure("INVALID_DESTINATION", "destination needs an address or lat/lng")
        val place = ResolvedPlace.from(context, destination)
            ?: return failure("INVALID_DESTINATION", "destination needs an address or lat/lng")

        val mapsUrl = json.optString("maps_url").takeIf { it.isNotBlank() } ?: NavigationUrls.webUrl(target)
        val mapsInstalled = context.packageManager.getLaunchIntentForPackage(MAPS_PACKAGE) != null
        val openIntent = if (mapsInstalled) {
            Intent(Intent.ACTION_VIEW, Uri.parse(NavigationUrls.appUri(target))).setPackage(MAPS_PACKAGE)
        } else {
            Intent(Intent.ACTION_VIEW, Uri.parse(mapsUrl))
        }
        val navigationId = "nav-${UUID.randomUUID().toString().take(8)}"
        val address = place.address ?: target
        val notified = runCatching {
            if (!NotificationManagerCompat.from(context).areNotificationsEnabled()) return@runCatching false
            ensureNavigationChannel(context)
            if (!canOpen(context, openIntent)) return@runCatching false
            val pendingIntent = PendingIntent.getActivity(
                context,
                navigationId.hashCode(),
                openIntent,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
            val notification = NotificationCompat.Builder(context, COMMAND_CHANNEL_ID)
                .setSmallIcon(android.R.drawable.ic_dialog_map)
                .setContentTitle("Chỉ đường tới $address")
                .setContentText("chạm để mở Google Maps")
                .setContentIntent(pendingIntent)
                .setAutoCancel(true)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .build()
            NotificationManagerCompat.from(context).notify(navigationId.hashCode(), notification)
            true
        }.getOrDefault(false)
        if (!notified) return failure("NAVIGATION_PROVIDER_ERROR", "Could not show navigation notification")

        ActionSessions(context).put(
            ActionSessions.NAVIGATION, navigationId, JSONObject().put("state", "navigating")
        )
        return success(
            "navigation_id" to navigationId,
            "navigation_state" to "navigating",
            "travel_mode" to "walking",
            "destination" to place.toMap(),
        )
    }
}

private const val COMMAND_CHANNEL_ID = "appdemo_commands"

private fun ensureNavigationChannel(context: Context) {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
    val manager = context.getSystemService(NotificationManager::class.java) ?: return
    if (manager.getNotificationChannel(COMMAND_CHANNEL_ID) == null) {
        manager.createNotificationChannel(
            NotificationChannel(COMMAND_CHANNEL_ID, "Lệnh từ server", NotificationManager.IMPORTANCE_HIGH),
        )
    }
}

/**
 * `navigation_stop`: end a session started by [NavigationStartHandler].
 *
 * Maps runs as a separate app, so guidance on screen is not force-closed; the
 * session is marked stopped and later stops for it are refused.
 */
class NavigationStopHandler : ActionHandler {
    override fun execute(context: Context?, paramsJson: String): ActionExecutionResult {
        if (context == null) return failure("NAVIGATION_STOP_FAILED", "No context available")
        val navigationId = runCatching { JSONObject(paramsJson).optString("navigation_id") }
            .getOrDefault("").trim()
        val sessions = ActionSessions(context)
        val session = sessions.get(ActionSessions.NAVIGATION, navigationId)
            ?: return failure("NAVIGATION_NOT_FOUND", "No navigation '$navigationId' on this phone")
        if (session.optString("state") == "stopped") {
            return failure("NAVIGATION_ALREADY_STOPPED", "Navigation '$navigationId' is already stopped")
        }
        sessions.put(ActionSessions.NAVIGATION, navigationId, session.put("state", "stopped"))
        return success("navigation_id" to navigationId, "navigation_state" to "stopped")
    }
}
