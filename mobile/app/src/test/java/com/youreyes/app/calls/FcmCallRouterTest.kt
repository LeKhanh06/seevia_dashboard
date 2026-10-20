package com.youreyes.app.calls

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FcmCallRouterTest {

    @Test
    fun `call_incoming payload becomes IncomingCall`() {
        val push = FcmCallRouter.parse(
            mapOf(
                "type" to "call_incoming",
                "call_id" to "1886c84f-9436-4edd-a417-24cc1aa66c3c",
                "from_display_name" to "Cô Sáu",
                "emergency" to "false",
            )
        )
        assertEquals(CallPush.IncomingCall("1886c84f-9436-4edd-a417-24cc1aa66c3c", "Cô Sáu", false), push)
    }

    @Test
    fun `emergency true string marks the call as emergency`() {
        val push = FcmCallRouter.parse(
            mapOf("type" to "call_incoming", "call_id" to "c1", "from_display_name" to "Bác Ba", "emergency" to "true")
        ) as CallPush.IncomingCall
        assertTrue(push.emergency)
    }

    @Test
    fun `call_answered payload carries media url and token`() {
        val push = FcmCallRouter.parse(
            mapOf(
                "type" to "call_answered",
                "call_id" to "c2",
                "media_url" to "wss://api.visioncare-host.uk/v2/call",
                "media_token" to "s3cr3t-one-time-token",
            )
        )
        assertEquals(CallPush.Answered("c2", "wss://api.visioncare-host.uk/v2/call", "s3cr3t-one-time-token"), push)
    }

    @Test
    fun `call_ended payload carries the reason`() {
        val push = FcmCallRouter.parse(mapOf("type" to "call_ended", "call_id" to "c3", "reason" to "glasses_hangup"))
        assertEquals(CallPush.Ended("c3", "glasses_hangup"), push)
    }

    @Test
    fun `call_ended without reason is still routed`() {
        assertEquals(CallPush.Ended("c3", null), FcmCallRouter.parse(mapOf("type" to "call_ended", "call_id" to "c3")))
    }

    @Test
    fun `message_in payload becomes MessageIn`() {
        val push = FcmCallRouter.parse(
            mapOf("type" to "message_in", "message_id" to "m1", "from_display_name" to "Cô Sáu", "text" to "Con sẽ về muộn")
        )
        assertEquals(CallPush.MessageIn("m1", "Cô Sáu", "Con sẽ về muộn"), push)
    }

    @Test
    fun `missing call_id returns null`() {
        assertNull(FcmCallRouter.parse(mapOf("type" to "call_incoming", "from_display_name" to "Cô Sáu")))
        assertNull(FcmCallRouter.parse(mapOf("type" to "call_ended", "call_id" to "  ")))
        assertNull(FcmCallRouter.parse(mapOf("type" to "call_answered", "call_id" to "c2", "media_url" to "wss://x")))
    }

    @Test
    fun `unknown type returns null`() {
        assertNull(FcmCallRouter.parse(mapOf("type" to "video_call", "call_id" to "c1")))
    }

    @Test
    fun `legacy command payload without type returns null`() {
        val legacy = mapOf(
            "request_id" to "req-1", "user_id" to "u", "device_id" to "d", "action" to "music_play", "params" to "{}",
        )
        assertNull(FcmCallRouter.parse(legacy))
    }

    @Test
    fun `toString of pushes never shows the media token or message text`() {
        val answered = CallPush.Answered("c2", "wss://x", "s3cr3t-one-time-token").toString()
        val message = CallPush.MessageIn("m1", "Cô Sáu", "nội dung riêng tư").toString()
        assertFalse(answered.contains("s3cr3t"))
        assertFalse(message.contains("riêng tư"))
    }
}
