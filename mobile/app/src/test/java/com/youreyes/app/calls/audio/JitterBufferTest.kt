package com.youreyes.app.calls.audio

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

class JitterBufferTest {

    private val buffer = JitterBuffer()

    /** A frame whose bytes all equal [tag], so order is easy to assert. */
    private fun frame(tag: Int, size: Int = 640) = ByteArray(size) { tag.toByte() }

    private val silence = ByteArray(640)

    @Test
    fun `prebuffers 60ms before playing`() {
        buffer.push(frame(1))
        buffer.push(frame(2))
        assertArrayEquals("2 frames = 40 ms: still prebuffering", silence, buffer.pop())
        buffer.push(frame(3))
        assertArrayEquals("3 frames = 60 ms: playback starts with the first frame", frame(1), buffer.pop())
        assertArrayEquals(frame(2), buffer.pop())
        assertEquals("prebuffering at start is not an underrun", 0, buffer.underruns)
    }

    @Test
    fun `underrun returns silence and counts once per gap`() {
        for (i in 1..3) buffer.push(frame(i))
        for (i in 1..3) assertArrayEquals(frame(i), buffer.pop())
        assertArrayEquals(silence, buffer.pop())
        assertEquals(1, buffer.underruns)
        // still rebuffering: more silence, but it is the same gap
        assertArrayEquals(silence, buffer.pop())
        assertEquals(1, buffer.underruns)
    }

    @Test
    fun `playback resumes only after prebuffering again`() {
        for (i in 1..3) buffer.push(frame(i))
        for (i in 1..3) buffer.pop()
        buffer.pop() // underrun
        buffer.push(frame(4))
        buffer.push(frame(5))
        assertArrayEquals("2 frames is not enough after an underrun", silence, buffer.pop())
        buffer.push(frame(6))
        assertArrayEquals(frame(4), buffer.pop())
    }

    @Test
    fun `empty buffer pop is silence of one frame`() {
        assertEquals(640, buffer.pop().size)
        assertArrayEquals(silence, buffer.pop())
    }

    @Test
    fun `wrong size frame dropped`() {
        buffer.push(frame(1, size = 639))
        buffer.push(frame(1, size = 641))
        buffer.push(ByteArray(0))
        buffer.push(frame(1, size = 16 * 1024))
        assertEquals(0, buffer.size)
        assertEquals(4, buffer.dropped)
        // the good frames are unaffected
        for (i in 1..3) buffer.push(frame(i))
        assertEquals(3, buffer.size)
        assertArrayEquals(frame(1), buffer.pop())
    }

    @Test
    fun `burst over 200ms drops oldest`() {
        for (i in 1..15) buffer.push(frame(i))
        assertEquals(10, buffer.size)
        assertEquals(5, buffer.dropped)
        assertArrayEquals("frames 1..5 were the oldest and are gone", frame(6), buffer.pop())
    }

    @Test
    fun `order preserved`() {
        for (i in 1..3) buffer.push(frame(i))
        assertArrayEquals(frame(1), buffer.pop())
        buffer.push(frame(4))
        assertArrayEquals(frame(2), buffer.pop())
        assertArrayEquals(frame(3), buffer.pop())
        assertArrayEquals(frame(4), buffer.pop())
    }

    @Test
    fun `custom sizes follow the constructor`() {
        val small = JitterBuffer(frameBytes = 4, targetMs = 40, frameMs = 20, maxMs = 60)
        small.push(byteArrayOf(1, 1, 1, 1))
        assertArrayEquals(ByteArray(4), small.pop())
        small.push(byteArrayOf(2, 2, 2, 2))
        assertArrayEquals(byteArrayOf(1, 1, 1, 1), small.pop())
        for (i in 1..5) small.push(byteArrayOf(i.toByte(), 0, 0, 0))
        assertEquals(3, small.size)
    }
}
