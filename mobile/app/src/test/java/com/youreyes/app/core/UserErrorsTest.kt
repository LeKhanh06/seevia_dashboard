package com.youreyes.app.core

import com.youreyes.app.core.UserErrors.Action
import com.youreyes.app.network.HttpStatusException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.SocketTimeoutException
import java.net.UnknownHostException

class UserErrorsTest {

    @Test
    fun network_failures_say_to_check_the_network() {
        val expected = "Không kết nối được máy chủ. Kiểm tra mạng rồi thử lại."
        assertEquals(expected, UserErrors.describe(UnknownHostException("x")))
        assertEquals(expected, UserErrors.describe(SocketTimeoutException("x")))
    }

    @Test
    fun login_401_is_wrong_credentials_not_expired_session() {
        val e = HttpStatusException(401, "POST /api/v1/auth/login failed (401): {...}")
        assertTrue(UserErrors.describe(e, Action.LOGIN).startsWith("Sai số điện thoại hoặc mật khẩu"))
        assertTrue(UserErrors.describe(e, Action.GENERAL).contains("đăng nhập lại"))
        assertTrue(UserErrors.describe(e, Action.OTP).contains("Mã xác thực"))
    }

    @Test
    fun register_conflict_points_to_login() {
        assertTrue(UserErrors.describe(HttpStatusException(409, "x"), Action.REGISTER).contains("Đăng nhập"))
    }

    @Test
    fun wifi_conflict_explains_the_hotspot() {
        val e = IllegalStateException("Kính không nghe được (409)")
        assertTrue(UserErrors.describe(e, Action.WIFI).contains("VisionCare-Setup"))
    }

    @Test
    fun status_is_read_from_the_texts_the_clients_produce() {
        assertEquals(409, UserErrors.statusOf(IllegalStateException("detail (409)")))
        assertEquals(500, UserErrors.statusOf(IllegalStateException("HTTP 500: boom")))
        assertEquals(422, UserErrors.statusOf(IllegalStateException("POST /x failed (422): y")))
        assertEquals(null, UserErrors.statusOf(IllegalStateException("whatever")))
    }

    @Test
    fun server_errors_and_unknown_never_leak_technical_text() {
        val server = UserErrors.describe(HttpStatusException(503, "POST /secret/path failed (503): <html>"))
        assertTrue(server.startsWith("Máy chủ đang gặp sự cố"))
        listOf(null, IllegalStateException("java.lang.NullPointerException at com.x"), HttpStatusException(418, "teapot")).forEach {
            val text = UserErrors.describe(it)
            assertFalse(text, text.contains("Exception") || text.contains("HTTP") || text.contains("/"))
            assertTrue(text.isNotBlank())
        }
    }
}
