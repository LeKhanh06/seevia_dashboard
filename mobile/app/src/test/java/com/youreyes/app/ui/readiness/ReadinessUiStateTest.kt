package com.youreyes.app.ui.readiness

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ReadinessUiStateTest {
    private val allReady = ReadinessSnapshot(
        notificationAccess = true,
        batteryOptimizationOff = true,
        overlay = true,
        locationSwitchOn = true,
        backgroundLocation = true,
        contacts = true,
        sms = true,
        call = true,
        phoneState = true,
        emergencyContactSet = true,
        bluetooth = true,
    )

    @Test
    fun `nothing is shown as ready before the first check`() {
        val state = ReadinessUiState()

        assertFalse(state.allReady)
        assertEquals(0, state.total)
        assertEquals("Đang kiểm tra...", state.summary)
    }

    @Test
    fun `every dependency of a server command gets exactly one row`() {
        val keys = ReadinessUiState.build(allReady).items.map { it.key }

        assertEquals(ReadinessKey.entries.toSet(), keys.toSet())
        assertEquals(keys.size, keys.toSet().size)
    }

    @Test
    fun `a fully granted phone is ready and says so`() {
        val state = ReadinessUiState.build(allReady)

        assertTrue(state.allReady)
        assertEquals(state.total, state.readyCount)
        assertEquals("Đã sẵn sàng nhận lệnh", state.summary)
    }

    @Test
    fun `each missing item is counted and named in the summary`() {
        val state = ReadinessUiState.build(
            allReady.copy(notificationAccess = false, locationSwitchOn = false),
        )

        assertFalse(state.allReady)
        assertEquals(2, state.missing.size)
        assertEquals(state.total - 2, state.readyCount)
        assertEquals("Còn 2 mục cần bật", state.summary)
    }

    @Test
    fun `missing items are listed before ready ones so the fix is at the top`() {
        val state = ReadinessUiState.build(allReady.copy(overlay = false, call = false))

        assertEquals(listOf(false, false), state.items.take(2).map { it.ready })
        assertTrue(state.items.drop(2).all { it.ready })
    }

    @Test
    fun `the readiness flag of each row follows its own permission only`() {
        fun readyOf(snapshot: ReadinessSnapshot, key: ReadinessKey) =
            ReadinessUiState.build(snapshot).items.first { it.key == key }.ready

        assertFalse(readyOf(allReady.copy(notificationAccess = false), ReadinessKey.NOTIFICATION_ACCESS))
        assertFalse(readyOf(allReady.copy(batteryOptimizationOff = false), ReadinessKey.BATTERY_OPTIMIZATION))
        assertFalse(readyOf(allReady.copy(locationSwitchOn = false), ReadinessKey.LOCATION_SWITCH))
        assertFalse(readyOf(allReady.copy(emergencyContactSet = false), ReadinessKey.EMERGENCY_CONTACT))
        assertFalse(readyOf(allReady.copy(bluetooth = false), ReadinessKey.BLUETOOTH))
        // Turning one thing off never flips an unrelated row.
        assertTrue(readyOf(allReady.copy(sms = false), ReadinessKey.CALL))
    }

    @Test
    fun `every row explains why it matters in words a wearer can act on`() {
        ReadinessUiState.build(allReady).items.forEach {
            assertTrue("${it.key} has no title", it.title.isNotBlank())
            assertTrue("${it.key} has no reason", it.reason.isNotBlank())
        }
    }
}
