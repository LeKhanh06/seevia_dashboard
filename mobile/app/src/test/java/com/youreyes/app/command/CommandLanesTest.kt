package com.youreyes.app.command

import org.junit.Assert.assertEquals
import org.junit.Test

class CommandLanesTest {
    @Test
    fun `urgent actions use the fast lane`() {
        listOf("emergency_call", "call_answer", "call_reject", "location_get", "capabilities_get")
            .forEach { assertEquals(it, Lane.FAST, CommandLanes.laneFor(it)) }
    }

    @Test
    fun `other and unknown actions use the slow lane`() {
        listOf("music_play", "contact_call", "navigation_start", "unknown_x")
            .forEach { assertEquals(it, Lane.SLOW, CommandLanes.laneFor(it)) }
    }
}
