package com.youreyes.app.calls

import com.youreyes.app.network.ApiResult
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ApiErrorTextTest {
    private fun failed(code: String, status: Int = 404) = ApiResult.Failed(status, code, "english developer text")

    @Test
    fun invite_errors_have_the_agreed_wording() {
        assertEquals("Số này chưa có tài khoản SeeVia", apiErrorVi(failed("USER_NOT_FOUND")))
        assertEquals("Đã mời hoặc đã là người thân", apiErrorVi(failed("CARE_LINK_CONFLICT", 409)))
    }

    @Test
    fun session_and_network_and_server_errors_are_vietnamese() {
        assertTrue(apiErrorVi(ApiResult.Unauthorized).contains("đăng nhập"))
        assertTrue(apiErrorVi(ApiResult.Network(java.io.IOException())).contains("mạng"))
        assertTrue(apiErrorVi(failed("X", 503)).contains("Máy chủ"))
        assertTrue(apiErrorVi(failed("WHATEVER", 400)).isNotBlank())
    }
}
