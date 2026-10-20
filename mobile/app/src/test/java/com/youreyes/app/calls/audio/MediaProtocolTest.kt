package com.youreyes.app.calls.audio

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class MediaProtocolTest {

    @Test
    fun `hangup and mute frames match the contract`() {
        assertEquals("""{"t":"hangup"}""", MediaProtocol.hangup())
        assertEquals("""{"t":"mute","on":true}""", MediaProtocol.mute(true))
        assertEquals("""{"t":"mute","on":false}""", MediaProtocol.mute(false))
    }

    @Test
    fun `parseState reads state and reason`() {
        assertEquals(
            "ended" to "glasses_hangup",
            MediaProtocol.parseState("""{"t":"state","state":"ended","reason":"glasses_hangup"}"""),
        )
    }

    @Test
    fun `parseState without reason gives null reason`() {
        assertEquals("active" to null, MediaProtocol.parseState("""{"t":"state","state":"active"}"""))
        assertEquals("active" to null, MediaProtocol.parseState("""{"t":"state","state":"active","reason":null}"""))
    }

    @Test
    fun `parseState rejects garbage`() {
        assertNull(MediaProtocol.parseState("not json at all"))
        assertNull(MediaProtocol.parseState(""))
        assertNull(MediaProtocol.parseState("""{"t":"state"}"""))
        assertNull(MediaProtocol.parseState("""{"t":"mute","on":true}"""))
        assertNull(MediaProtocol.parseState("""{"state":"active"}"""))
        assertNull(MediaProtocol.parseState("[1,2,3]"))
    }
}
