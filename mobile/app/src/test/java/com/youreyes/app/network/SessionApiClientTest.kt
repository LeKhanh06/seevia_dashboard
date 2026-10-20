package com.youreyes.app.network

import com.youreyes.app.calls.maskPhone
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SessionApiClientTest {

    @Test
    fun `parseCareLink keeps only the masked phone`() {
        val link = parseCareLink(
            JSONObject(
                """{ "id": "9c1f0d0e-6c43-4b8e-9f41-0d5b6a3f2a11", "role": "owner", "status": "active",
                "display_name": "Mẹ", "aliases": ["mẹ", "má"], "emergency": true, "priority": 0,
                "owner_user_id": "user-A1B2C3", "relative_user_id": "user-D4E5F6",
                "peer_display_name": "Mẹ Hoa", "peer_phone_number": "0911000001", "created_at": "2026-10-20T07:10:00Z" }"""
            )
        )
        assertEquals("***001", link.peerPhoneMasked)
        assertEquals("9c1f0d0e-6c43-4b8e-9f41-0d5b6a3f2a11", link.id)
        assertEquals("owner", link.role)
        assertEquals("active", link.status)
        assertEquals(listOf("mẹ", "má"), link.aliases)
        assertTrue(link.emergency)
        assertEquals(0, link.priority)
        assertEquals("user-A1B2C3", link.ownerUserId)
        assertEquals("user-D4E5F6", link.relativeUserId)
        assertEquals("Mẹ Hoa", link.peerDisplayName)
        assertFalse(link.toString().contains("0911000001"))
    }

    @Test
    fun `parseCareLink tolerates null names and phone`() {
        val link = parseCareLink(
            JSONObject(
                """{ "id": "l1", "role": "relative", "status": "pending", "display_name": null, "aliases": [],
                "emergency": false, "priority": 50, "owner_user_id": "a", "relative_user_id": "b",
                "peer_display_name": null, "peer_phone_number": null }"""
            )
        )
        assertEquals("", link.peerPhoneMasked)
        assertEquals("", link.peerDisplayName)
        assertEquals("", link.displayName)
    }

    @Test
    fun `parseCallAnswer reads media url and one time token`() {
        val answer = parseCallAnswer(
            JSONObject(
                """{ "call_id": "1886c84f-9436-4edd-a417-24cc1aa66c3c", "media_url": "wss://api.visioncare-host.uk/v2/call",
                "media_token": "s3cr3t-one-time-token" }"""
            )
        )
        assertEquals("1886c84f-9436-4edd-a417-24cc1aa66c3c", answer.callId)
        assertEquals("wss://api.visioncare-host.uk/v2/call", answer.mediaUrl)
        assertEquals("s3cr3t-one-time-token", answer.mediaToken)
        assertFalse(answer.toString().contains("s3cr3t"))
    }

    @Test
    fun `parseCallAnswer with null token on repeated answer`() {
        val answer = parseCallAnswer(JSONObject("""{ "call_id": "c", "media_url": "wss://x/v2/call", "media_token": null }"""))
        assertNull(answer.mediaToken)
    }

    @Test
    fun `parseCallInfo reads the call state sample`() {
        val info = parseCallInfo(
            JSONObject(
                """{ "call_id": "1886c84f-9436-4edd-a417-24cc1aa66c3c", "direction": "to_relative", "state": "active",
                "end_reason": null, "emergency": false, "peer_display_name": "Cô Sáu", "media_url": "wss://api.visioncare-host.uk/v2/call",
                "media_token": null, "created_at": "2026-10-20T07:10:01Z", "answered_at": "2026-10-20T07:10:03Z", "ended_at": null }"""
            )
        )
        assertEquals("to_relative", info.direction)
        assertEquals("active", info.state)
        assertNull(info.endReason)
        assertEquals("Cô Sáu", info.peerDisplayName)
        assertNull(info.mediaToken)
        assertEquals("wss://api.visioncare-host.uk/v2/call", info.mediaUrl)
    }

    @Test
    fun `parseMessage reads both directions`() {
        val msg = parseMessage(
            JSONObject(
                """{ "id": "m1", "direction": "to_glasses", "from_me": true, "text": "Con sẽ về muộn",
                "created_at": "2026-10-20T07:10:00Z", "read_at": null }"""
            )
        )
        assertEquals("m1", msg.id)
        assertTrue(msg.fromMe)
        assertNull(msg.readAt)
        assertEquals("Con sẽ về muộn", msg.text)
        assertFalse(msg.toString().contains("về muộn"))
    }

    @Test
    fun `parseError reads the envelope`() {
        val failed = parseError(
            404,
            """{ "status": "error", "error": { "code": "CARE_LINK_NOT_FOUND", "message": "Care link not found", "details": {} } }""",
        )
        assertEquals(404, failed.httpStatus)
        assertEquals("CARE_LINK_NOT_FOUND", failed.code)
        assertEquals("Care link not found", failed.message)
    }

    @Test
    fun `parseError survives non json bodies`() {
        val failed = parseError(502, "<html>Bad gateway</html>")
        assertEquals(502, failed.httpStatus)
        assertEquals("HTTP_502", failed.code)
        assertEquals("", failed.message)
        assertEquals("HTTP_500", parseError(500, "").code)
    }

    @Test
    fun `maskPhone keeps the last three digits`() {
        assertEquals("***768", maskPhone("0912345768"))
        assertEquals("***768", maskPhone("+84 912 345 768"))
        assertEquals("***12", maskPhone("12"))
        assertEquals("", maskPhone(null))
    }
}
