package com.youreyes.app.calls.audio

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import android.os.Build
import android.os.Process
import android.util.Log
import androidx.core.content.ContextCompat
import java.util.concurrent.locks.LockSupport

/**
 * Microphone -> [CallMediaSocket] and glasses audio -> speaker for one call (protocol 7.3).
 *
 * Format is fixed by the protocol: PCM16 LE, 16 kHz, mono, one 640-byte frame every 20 ms.
 * Capture is paced by the microphone itself, so frames leave evenly and are never merged. Playback
 * takes one frame per 20 ms from a [JitterBuffer] (60 ms), playing silence when none is there.
 *
 * Wiring: the socket's `Listener.onAudio` must call [onRemoteAudio]; the controller owns that glue.
 * Needs `RECORD_AUDIO` granted before [start]. Never logs audio or anything about the call.
 */
class CallAudioEngine(
    context: Context,
    private val socket: CallMediaSocket,
) {
    private val appContext = context.applicationContext
    private val audioManager = appContext.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private val jitter = JitterBuffer()

    @Volatile private var muted = false
    @Volatile private var running = false

    private var record: AudioRecord? = null
    private var track: AudioTrack? = null
    private var echoCanceler: AcousticEchoCanceler? = null
    private var noiseSuppressor: NoiseSuppressor? = null
    private var captureThread: Thread? = null
    private var playbackThread: Thread? = null

    private var previousMode = AudioManager.MODE_NORMAL
    private var focusRequest: AudioFocusRequest? = null
    private val focusListener = AudioManager.OnAudioFocusChangeListener { }

    /** Feeds one frame from the socket into the jitter buffer. Safe from any thread. */
    fun onRemoteAudio(frame: ByteArray) = jitter.push(frame)

    /** Returns false (and leaves everything released) when the microphone or speaker cannot be opened. */
    @Synchronized
    @SuppressLint("MissingPermission") // checked just below
    fun start(): Boolean {
        if (running) return true
        if (ContextCompat.checkSelfPermission(appContext, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            return false
        }
        enterCommunicationMode()
        val newRecord = createRecord()
        val newTrack = createTrack()
        if (newRecord == null || newTrack == null) {
            newRecord?.release()
            newTrack?.release()
            leaveCommunicationMode()
            return false
        }
        record = newRecord
        track = newTrack
        if (AcousticEchoCanceler.isAvailable()) {
            echoCanceler = AcousticEchoCanceler.create(newRecord.audioSessionId)?.also { it.enabled = true }
        }
        if (NoiseSuppressor.isAvailable()) {
            noiseSuppressor = NoiseSuppressor.create(newRecord.audioSessionId)?.also { it.enabled = true }
        }
        running = true
        newRecord.startRecording()
        newTrack.play()
        captureThread = Thread(::captureLoop, "call-capture").also { it.start() }
        playbackThread = Thread(::playbackLoop, "call-playback").also { it.start() }
        return true
    }

    @Synchronized
    fun stop() {
        val wasRunning = running
        running = false
        captureThread?.join(JOIN_TIMEOUT_MS)
        playbackThread?.join(JOIN_TIMEOUT_MS)
        captureThread = null
        playbackThread = null
        echoCanceler?.release()
        noiseSuppressor?.release()
        echoCanceler = null
        noiseSuppressor = null
        record?.let { runCatching { it.stop() }; it.release() }
        track?.let { runCatching { it.stop() }; it.release() }
        record = null
        track = null
        if (wasRunning) {
            Log.i(TAG, "call audio stopped, underruns=${jitter.underruns}, dropped=${jitter.dropped}")
            leaveCommunicationMode()
        }
    }

    /** Stops sending microphone frames and tells the server (informational). */
    fun setMuted(muted: Boolean) {
        this.muted = muted
        socket.mute(muted)
    }

    fun setSpeaker(on: Boolean) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            if (on) {
                audioManager.availableCommunicationDevices
                    .firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }
                    ?.let { audioManager.setCommunicationDevice(it) }
            } else {
                audioManager.clearCommunicationDevice()
            }
        } else {
            @Suppress("DEPRECATION")
            audioManager.isSpeakerphoneOn = on
        }
    }

    // ---- loops ----

    private fun captureLoop() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
        val source = record ?: return
        val frame = ByteArray(MediaProtocol.FRAME_BYTES)
        while (running) {
            var filled = 0
            while (running && filled < frame.size) {
                val read = source.read(frame, filled, frame.size - filled)
                if (read < 0) return // device lost; the call ends through the socket / user
                filled += read
            }
            if (running && filled == frame.size && !muted) socket.sendAudio(frame.copyOf())
        }
    }

    private fun playbackLoop() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
        val sink = track ?: return
        val periodNanos = MediaProtocol.FRAME_MS * 1_000_000L
        var nextTick = System.nanoTime()
        while (running) {
            val frame = jitter.pop()
            if (sink.write(frame, 0, frame.size) < 0) return
            nextTick += periodNanos
            val wait = nextTick - System.nanoTime()
            if (wait > 0) LockSupport.parkNanos(wait) else if (wait < -periodNanos * 5) nextTick = System.nanoTime()
        }
    }

    // ---- setup ----

    @SuppressLint("MissingPermission") // start() checks RECORD_AUDIO
    private fun createRecord(): AudioRecord? {
        val minBuffer = AudioRecord.getMinBufferSize(MediaProtocol.SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (minBuffer <= 0) return null
        val created = AudioRecord(
            MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            MediaProtocol.SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minBuffer, MediaProtocol.FRAME_BYTES * 4),
        )
        if (created.state != AudioRecord.STATE_INITIALIZED) {
            created.release()
            return null
        }
        return created
    }

    private fun createTrack(): AudioTrack? {
        val minBuffer = AudioTrack.getMinBufferSize(MediaProtocol.SAMPLE_RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (minBuffer <= 0) return null
        val created = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(MediaProtocol.SAMPLE_RATE)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build()
            )
            .setBufferSizeInBytes(maxOf(minBuffer, MediaProtocol.FRAME_BYTES * 4))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        if (created.state != AudioTrack.STATE_INITIALIZED) {
            created.release()
            return null
        }
        return created
    }

    private fun enterCommunicationMode() {
        previousMode = audioManager.mode
        audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                .setOnAudioFocusChangeListener(focusListener)
                .build()
            focusRequest = request
            audioManager.requestAudioFocus(request)
        } else {
            @Suppress("DEPRECATION")
            audioManager.requestAudioFocus(focusListener, AudioManager.STREAM_VOICE_CALL, AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
        }
    }

    private fun leaveCommunicationMode() {
        setSpeaker(false)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            focusRequest?.let { audioManager.abandonAudioFocusRequest(it) }
            focusRequest = null
        } else {
            @Suppress("DEPRECATION")
            audioManager.abandonAudioFocus(focusListener)
        }
        audioManager.mode = previousMode
    }

    private companion object {
        const val TAG = "CallAudioEngine"
        const val JOIN_TIMEOUT_MS = 500L
    }
}
