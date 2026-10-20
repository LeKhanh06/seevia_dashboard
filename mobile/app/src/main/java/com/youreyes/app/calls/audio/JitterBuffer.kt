package com.youreyes.app.calls.audio

/**
 * Receive-side jitter buffer for call audio (protocol 7.3: the server does not buffer glasses -> phone).
 *
 * - Before playback starts, and again after an underrun, it holds back until [targetMs] of audio is queued.
 * - [pop] always returns exactly one frame: the oldest queued frame, or one frame of silence. A late or
 *   missing frame therefore plays as silence instead of a click or a crash.
 * - Frames whose size is not [frameBytes] are dropped; more than [maxMs] queued drops the oldest frames, so a
 *   burst after a network stall cannot add lasting delay.
 *
 * [push] (socket thread) and [pop] (playback thread) are safe to call concurrently.
 */
class JitterBuffer(
    private val frameBytes: Int = MediaProtocol.FRAME_BYTES,
    targetMs: Int = 60,
    frameMs: Int = MediaProtocol.FRAME_MS,
    maxMs: Int = 200,
) {
    private val targetFrames = maxOf(1, targetMs / frameMs)
    private val maxFrames = maxOf(targetFrames, maxMs / frameMs)
    private val queue = ArrayDeque<ByteArray>()
    private var playing = false

    /** Times playback ran dry after it had started (one per gap, not per silent frame). */
    @get:Synchronized
    var underruns: Int = 0
        private set

    /** Frames thrown away: wrong size, or the oldest ones of an over-long queue. */
    @get:Synchronized
    var dropped: Int = 0
        private set

    val size: Int
        @Synchronized get() = queue.size

    @Synchronized
    fun push(frame: ByteArray) {
        if (frame.size != frameBytes) {
            dropped++
            return
        }
        queue.addLast(frame)
        while (queue.size > maxFrames) {
            queue.removeFirst()
            dropped++
        }
    }

    @Synchronized
    fun pop(): ByteArray {
        if (!playing) {
            if (queue.size < targetFrames) return ByteArray(frameBytes)
            playing = true
        }
        val next = queue.removeFirstOrNull()
        if (next == null) {
            playing = false // re-prebuffer so the next burst does not play in fits and starts
            underruns++
            return ByteArray(frameBytes)
        }
        return next
    }
}
