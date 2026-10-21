package com.youreyes.app.ui.readiness

import android.Manifest
import android.content.Context
import android.content.Intent
import android.location.LocationManager
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import androidx.core.location.LocationManagerCompat
import com.youreyes.app.actions.hasNotificationAccess
import com.youreyes.app.actions.hasPermission
import com.youreyes.app.core.AppConfig

/**
 * Reads the same facts `capabilities_get` reports, plus the two the server cannot see:
 * the system Location switch and Bluetooth.
 */
object ReadinessChecker {

    fun snapshot(context: Context): ReadinessSnapshot {
        val power = context.getSystemService(Context.POWER_SERVICE) as? PowerManager
        val location = context.getSystemService(Context.LOCATION_SERVICE) as? LocationManager
        return ReadinessSnapshot(
            notificationAccess = hasNotificationAccess(context),
            batteryOptimizationOff = power?.isIgnoringBatteryOptimizations(context.packageName) == true,
            overlay = Settings.canDrawOverlays(context),
            locationSwitchOn = location != null && LocationManagerCompat.isLocationEnabled(location),
            backgroundLocation = Build.VERSION.SDK_INT < Build.VERSION_CODES.Q ||
                hasPermission(context, Manifest.permission.ACCESS_BACKGROUND_LOCATION),
            contacts = hasPermission(context, Manifest.permission.READ_CONTACTS),
            sms = hasPermission(context, Manifest.permission.SEND_SMS),
            call = hasPermission(context, Manifest.permission.CALL_PHONE),
            phoneState = hasPermission(context, Manifest.permission.READ_PHONE_STATE),
            emergencyContactSet = !AppConfig.prefs(context)
                .getString(AppConfig.KEY_EMERGENCY_CONTACT, null).isNullOrBlank(),
            bluetooth = Build.VERSION.SDK_INT < Build.VERSION_CODES.S ||
                (hasPermission(context, Manifest.permission.BLUETOOTH_CONNECT) &&
                    hasPermission(context, Manifest.permission.BLUETOOTH_SCAN)),
        )
    }

    /**
     * The system screen that fixes [key]. Null for the emergency contact, which is typed in
     * the app itself. Runtime permissions go to the app's own settings page: once Android
     * has been told "no" it no longer shows the permission dialog.
     */
    fun fixIntent(context: Context, key: ReadinessKey): Intent? {
        val packageUri = Uri.parse("package:${context.packageName}")
        val intent = when (key) {
            ReadinessKey.NOTIFICATION_ACCESS -> Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
            ReadinessKey.LOCATION_SWITCH -> Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS)
            ReadinessKey.BATTERY_OPTIMIZATION ->
                Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, packageUri)
            ReadinessKey.OVERLAY -> Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, packageUri)
            ReadinessKey.EMERGENCY_CONTACT -> return null
            ReadinessKey.BACKGROUND_LOCATION,
            ReadinessKey.CONTACTS,
            ReadinessKey.SMS,
            ReadinessKey.CALL,
            ReadinessKey.PHONE_STATE,
            ReadinessKey.BLUETOOTH,
            -> Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, packageUri)
        }
        return intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }

    /** Opens [key]'s settings; falls back to the app's page when a vendor build lacks the screen. */
    fun openFix(context: Context, key: ReadinessKey): Boolean {
        val intent = fixIntent(context, key) ?: return false
        if (runCatching { context.startActivity(intent) }.isSuccess) return true
        val fallback = Intent(
            Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            Uri.parse("package:${context.packageName}"),
        ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return runCatching { context.startActivity(fallback) }.isSuccess
    }
}
