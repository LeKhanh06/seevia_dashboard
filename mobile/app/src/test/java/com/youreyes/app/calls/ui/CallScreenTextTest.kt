package com.youreyes.app.calls.ui

import com.youreyes.app.calls.CallExtras
import com.youreyes.app.calls.CallState
import com.youreyes.app.calls.endReasonVi
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CallScreenTextTest {

    @Test
    fun duration_formats_minutes_and_hours() {
        assertEquals("00:00", formatCallDuration(0))
        assertEquals("00:09", formatCallDuration(9_999))
        assertEquals("01:05", formatCallDuration(65_000))
        assertEquals("1:00:00", formatCallDuration(3_600_000))
        assertEquals("00:00", formatCallDuration(-5))
    }

    @Test
    fun status_text_is_vietnamese_for_every_state() {
        val me = CallExtras(name = "Mẹ")
        assertEquals("Cuộc gọi từ kính", callStatus(CallState.Ringing("c", "Mẹ", false), me))
        assertEquals("Cuộc gọi khẩn cấp từ kính", callStatus(CallState.Ringing("c", "Mẹ", true), me.copy(emergency = true)))
        assertEquals("Đang gọi Mẹ…", callStatus(CallState.Outgoing(""), me))
        assertEquals("Đang kết nối…", callStatus(CallState.Connecting("c"), me))
        assertEquals("Đang nói chuyện", callStatus(CallState.Active("c", 0), me))
        assertEquals("Kính đang bận", callStatus(CallState.Ended("c", "Kính đang bận"), me))
        assertEquals("", callStatus(CallState.Idle, me))
    }

    @Test
    fun title_falls_back_when_the_server_sent_no_name() {
        assertEquals("Người thân", callTitle(CallExtras()))
        assertEquals("Cô Sáu", callTitle(CallExtras(name = "Cô Sáu")))
    }

    @Test
    fun microphone_failure_has_its_own_reason() {
        assertTrue(endReasonVi("mic_unavailable").contains("micro"))
    }
}
