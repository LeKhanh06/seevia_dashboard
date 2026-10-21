package com.youreyes.app.core

import com.youreyes.app.network.HttpStatusException
import java.io.IOException

/**
 * One Vietnamese sentence for a failed request, written as what to do next. The server's own
 * text (English, with paths and status codes) is for developers; it never reaches the screen.
 */
/** A failure whose message is already a sentence for the person (Vietnamese, says what to do). */
class UserFacingException(message: String) : Exception(message)

object UserErrors {

    /** What the person was trying to do, which changes what a status code means. */
    enum class Action { GENERAL, LOGIN, REGISTER, OTP, WIFI, GLASSES_LINK }

    private const val NETWORK = "Không kết nối được máy chủ. Kiểm tra mạng rồi thử lại."
    private const val SERVER = "Máy chủ đang gặp sự cố. Thử lại sau ít phút."
    private const val UNKNOWN = "Chưa làm được. Thử lại sau ít phút."

    fun describe(error: Throwable?, action: Action = Action.GENERAL): String {
        if (error == null) return unknown(action)
        if (error is UserFacingException) return error.message.orEmpty().ifBlank { unknown(action) }
        if (error is IOException && error !is java.net.ProtocolException) return NETWORK
        val status = statusOf(error) ?: return unknown(action)
        return forStatus(status, action)
    }

    private fun unknown(action: Action): String = when (action) {
        Action.WIFI -> "Chưa gửi được cấu hình mạng. Kiểm tra tên mạng và mật khẩu, rồi thử lại."
        else -> UNKNOWN
    }

    fun forStatus(status: Int, action: Action = Action.GENERAL): String = when {
        status == 401 && action == Action.LOGIN -> "Sai số điện thoại hoặc mật khẩu. Kiểm tra rồi nhập lại."
        status == 401 && action == Action.OTP -> "Mã xác thực chưa đúng hoặc đã hết hạn. Kiểm tra mã rồi nhập lại."
        status == 401 -> "Phiên đăng nhập đã hết hạn. Vào mục Tài khoản để đăng nhập lại."
        status == 409 && action == Action.REGISTER -> "Số điện thoại này đã có tài khoản. Hãy chọn Đăng nhập."
        status == 409 && action == Action.WIFI ->
            "Kính chưa nghe được máy chủ. Nối điện thoại vào Wi-Fi VisionCare-Setup (mật khẩu visioncare) rồi gửi lại."
        status == 409 -> "Việc này đã được làm rồi hoặc đang chờ xử lý."
        status == 404 && action == Action.WIFI -> "Máy chủ chưa thấy kính này kết nối. Kiểm tra số máy in trên gọng kính."
        status == 404 && action == Action.GLASSES_LINK -> "Không tìm thấy kính có số máy này. Kiểm tra lại số in trên gọng kính."
        status == 404 -> "Không tìm thấy thông tin cần tìm. Thử lại sau."
        status == 400 || status == 422 -> when (action) {
            Action.REGISTER -> "Thông tin chưa hợp lệ. Số điện thoại cần đủ số và mật khẩu ít nhất 6 ký tự."
            Action.OTP -> "Mã xác thực chưa đúng. Kiểm tra mã rồi nhập lại."
            else -> "Thông tin chưa hợp lệ. Kiểm tra lại rồi thử lại."
        }
        status == 403 -> "Bạn chưa được phép làm việc này."
        status == 429 -> "Bạn thử quá nhiều lần. Đợi một lát rồi thử lại."
        status >= 500 -> SERVER
        else -> unknown(action)
    }

    /** Status code carried by the exception, or read from the "…(409)" / "HTTP 409: …" texts the clients produce. */
    internal fun statusOf(error: Throwable): Int? {
        if (error is HttpStatusException) return error.status
        val text = error.message.orEmpty()
        Regex("""\((\d{3})\)\s*$""").find(text)?.let { return it.groupValues[1].toInt() }
        Regex("""^HTTP (\d{3})""").find(text)?.let { return it.groupValues[1].toInt() }
        Regex("""failed \((\d{3})\)""").find(text)?.let { return it.groupValues[1].toInt() }
        return null
    }
}
