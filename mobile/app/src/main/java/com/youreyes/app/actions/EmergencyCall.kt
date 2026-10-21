package com.youreyes.app.actions

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import androidx.core.content.ContextCompat
import com.youreyes.app.core.AppConfig

/**
 * The phone-side emergency call: dial the one emergency number the person saved. Shared by the
 * SOS button, the "Khẩn cấp" button on the home screen and the voice command, so all three behave
 * the same. Callers ask for confirmation first; this class only places the call.
 */
object EmergencyCall {

    sealed interface Result {
        /** No number saved yet. */
        data object NoNumber : Result

        /** The call is being placed directly. */
        data class Calling(val number: String) : Result

        /** No call permission, so the dialler was opened with the number filled in. */
        data class Dialer(val number: String) : Result

        data object Failed : Result
    }

    /** The saved emergency number, or "". */
    fun savedNumber(context: Context): String =
        AppConfig.prefs(context).getString(AppConfig.KEY_EMERGENCY_CONTACT, "").orEmpty().trim()

    /** Digits and a leading plus only, which is all `tel:` needs. */
    fun dialable(number: String): String {
        val trimmed = number.trim()
        val plus = if (trimmed.startsWith("+")) "+" else ""
        return plus + trimmed.filter { it.isDigit() }
    }

    /** What is read out before the call is placed: digits grouped so a screen reader says them one by one. */
    fun spokenNumber(number: String): String = dialable(number).toList().joinToString(" ")

    fun start(context: Context): Result {
        val number = savedNumber(context)
        val dial = dialable(number)
        if (dial.isEmpty()) return Result.NoNumber
        val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.CALL_PHONE) ==
            PackageManager.PERMISSION_GRANTED
        return try {
            val intent = Intent(if (granted) Intent.ACTION_CALL else Intent.ACTION_DIAL, Uri.parse("tel:$dial"))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
            if (granted) Result.Calling(number) else Result.Dialer(number)
        } catch (e: Exception) {
            Result.Failed
        }
    }

    /** One Vietnamese sentence for [result], for a banner or to be spoken. */
    fun describe(result: Result): String = when (result) {
        Result.NoNumber -> "Chưa có số khẩn cấp. Vào mục An toàn để nhập số người thân."
        is Result.Calling -> "Đang gọi khẩn cấp tới ${result.number}."
        is Result.Dialer -> "Chưa cho phép gọi trực tiếp, đã mở bàn phím quay số với số ${result.number}. Bấm nút gọi để gọi."
        Result.Failed -> "Không gọi được. Hãy quay số trực tiếp."
    }
}
