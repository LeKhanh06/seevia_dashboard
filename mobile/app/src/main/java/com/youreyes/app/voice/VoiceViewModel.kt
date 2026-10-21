package com.youreyes.app.voice

import android.app.Application
import android.content.Context
import android.content.Intent
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.view.accessibility.AccessibilityManager
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.youreyes.app.calls.CallController
import com.youreyes.app.calls.CallState
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import java.util.Locale
import java.util.UUID
import kotlin.coroutines.resume

enum class VoicePhase { IDLE, LISTENING, WORKING, SPEAKING }

data class VoiceUiState(
    val phase: VoicePhase = VoicePhase.IDLE,
    /** What the recogniser heard (partial while listening). Shown to the person, never logged. */
    val heard: String = "",
    /** What the app answered. */
    val message: String = "",
    /** The microphone permission was refused for good: offer the system settings. */
    val micBlocked: Boolean = false,
) {
    val isBusy: Boolean get() = phase != VoicePhase.IDLE

    /** One phrase for the status line and for TalkBack. */
    val statusText: String
        get() = when (phase) {
            VoicePhase.IDLE -> "Sẵn sàng"
            VoicePhase.LISTENING -> "Đang nghe, bạn hãy nói"
            VoicePhase.WORKING -> "Đang xử lý"
            VoicePhase.SPEAKING -> "Đang trả lời"
        }
}

/**
 * Voice control: listens with the system [SpeechRecognizer] (`vi-VN`), hands the words to
 * [VoiceDialogue] and answers aloud. Activity-scoped, so the home-screen button and the floating
 * button share one conversation.
 *
 * Answers: with TalkBack off they are spoken by [TextToSpeech]; with TalkBack on they go to
 * TalkBack as an announcement instead (two voices talking over each other, and the microphone
 * hearing them, helps nobody). Either way the text is on screen and emitted on [announcements].
 */
class VoiceViewModel(application: Application) : AndroidViewModel(application) {

    private val _state = MutableStateFlow(VoiceUiState())
    val state: StateFlow<VoiceUiState> = _state.asStateFlow()

    /** Route to open; collected by the navigation host. */
    private val _navigation = MutableSharedFlow<String>(extraBufferCapacity = 4, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val navigation: SharedFlow<String> = _navigation.asSharedFlow()

    /** Text for `View.announceForAccessibility`. */
    private val _announcements = MutableSharedFlow<String>(extraBufferCapacity = 4, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val announcements: SharedFlow<String> = _announcements.asSharedFlow()

    private val env = AppVoiceEnv(application) { route -> _navigation.tryEmit(route) }
    private val dialogue = VoiceDialogue(env)

    /** The home screen's glasses tile reads the same status the voice command speaks. */
    suspend fun glassesBrief(): GlassesBrief = env.glassesBrief()

    private var recognizer: SpeechRecognizer? = null
    private var session: Job? = null

    private var tts: TextToSpeech? = null
    private val ttsReady = CompletableDeferred<Boolean>()
    private val utterances = java.util.concurrent.ConcurrentHashMap<String, kotlin.coroutines.Continuation<Unit>>()

    // ---- entry points ----

    /** Button pressed: stop what is happening, or start listening. */
    fun onMicPressed() {
        when (_state.value.phase) {
            VoicePhase.LISTENING, VoicePhase.WORKING -> stop("Đã dừng.")
            VoicePhase.SPEAKING, VoicePhase.IDLE -> startListening()
        }
    }

    /** "Dừng" on screen. */
    fun stop(message: String = "") {
        session?.cancel()
        session = null
        destroyRecognizer()
        stopSpeaking()
        dialogue.cancel()
        _state.update { it.copy(phase = VoicePhase.IDLE, heard = "", message = message) }
    }

    fun dismissMessage() {
        _state.update { if (it.isBusy) it else it.copy(message = "", heard = "", micBlocked = false) }
    }

    /** The microphone permission was refused. */
    fun onMicDenied(permanently: Boolean) {
        val text = if (permanently) {
            "Chưa có quyền micro nên chưa nghe được lệnh. Mở Cài đặt, chọn Ứng dụng, SeeVia, Quyền, rồi bật Micro."
        } else {
            "Chưa có quyền micro nên chưa nghe được lệnh. Chạm “Nói lệnh” lần nữa và chọn Cho phép."
        }
        _state.update { it.copy(phase = VoicePhase.IDLE, message = text, micBlocked = permanently) }
        _announcements.tryEmit(text)
    }

    // ---- listening ----

    fun startListening() {
        val app = getApplication<Application>()
        stopSpeaking()
        destroyRecognizer()
        session?.cancel()

        val call = CallController.state.value
        if (call != CallState.Idle && call !is CallState.Ended) {
            respond("Đang có cuộc gọi nên chưa dùng được lệnh giọng nói.", listenAfter = false)
            return
        }
        if (!SpeechRecognizer.isRecognitionAvailable(app)) {
            respond(
                "Điện thoại này chưa có dịch vụ nhận giọng nói. Hãy cài hoặc bật ứng dụng Google rồi thử lại.",
                listenAfter = false,
            )
            return
        }
        _state.update { it.copy(phase = VoicePhase.LISTENING, heard = "", message = "", micBlocked = false) }
        session = viewModelScope.launch {
            beep()
            // Let TalkBack finish announcing the button before the microphone opens.
            delay(if (talkBackOn()) 700 else 250)
            beginRecognizer()
        }
    }

    private fun beginRecognizer() {
        val app = getApplication<Application>()
        val sr = SpeechRecognizer.createSpeechRecognizer(app)
        recognizer = sr
        sr.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) = Unit
            override fun onBeginningOfSpeech() = Unit
            override fun onRmsChanged(rmsdB: Float) = Unit
            override fun onBufferReceived(buffer: ByteArray?) = Unit
            override fun onEndOfSpeech() = Unit
            override fun onEvent(eventType: Int, params: Bundle?) = Unit

            override fun onPartialResults(partialResults: Bundle?) {
                val partial = partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty()
                if (partial.isNotBlank()) _state.update { it.copy(heard = partial) }
            }

            override fun onResults(results: Bundle?) {
                val alternatives = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION).orEmpty()
                handleHeard(alternatives)
            }

            override fun onError(error: Int) {
                if (error == SpeechRecognizer.ERROR_CLIENT) return // we cancelled it ourselves
                handleError(error)
            }
        })
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, VI_VN)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, VI_VN)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 5)
            putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, app.packageName)
        }
        try {
            sr.startListening(intent)
        } catch (e: Exception) {
            handleError(SpeechRecognizer.ERROR_AUDIO)
        }
    }

    private fun handleHeard(alternatives: List<String>) {
        destroyRecognizer()
        _state.update { it.copy(phase = VoicePhase.WORKING, heard = alternatives.firstOrNull().orEmpty()) }
        session = viewModelScope.launch {
            val reply = try {
                dialogue.handle(alternatives)
            } catch (e: Exception) {
                dialogue.cancel()
                VoiceReply("Có lỗi khi xử lý lệnh. Bạn thử nói lại nhé.")
            }
            respond(reply.text, listenAfter = reply.listenForAnswer)
        }
    }

    private fun handleError(error: Int) {
        destroyRecognizer()
        val reply: VoiceReply = when (error) {
            SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> dialogue.nothingHeard()
            SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT, SpeechRecognizer.ERROR_SERVER ->
                VoiceReply("Nhận giọng nói cần có mạng. Kiểm tra mạng rồi chạm Nói lệnh lại.")
            SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> {
                onMicDenied(permanently = true)
                return
            }
            SpeechRecognizer.ERROR_RECOGNIZER_BUSY, ERROR_TOO_MANY_REQUESTS ->
                VoiceReply("Bộ nhận giọng nói đang bận. Đợi một chút rồi chạm Nói lệnh lại.")
            ERROR_LANGUAGE_NOT_SUPPORTED, ERROR_LANGUAGE_UNAVAILABLE ->
                VoiceReply("Máy chưa có gói tiếng Việt để nhận giọng nói. Vào Cài đặt, Google, Giọng nói để tải gói tiếng Việt.")
            else -> VoiceReply("Không nghe được. Kiểm tra micro rồi chạm Nói lệnh lại.")
        }
        // A question was waiting: dialogue.nothingHeard() already cancelled it.
        respond(reply.text, listenAfter = false)
    }

    // ---- answering ----

    private fun respond(text: String, listenAfter: Boolean) {
        _state.update { it.copy(phase = VoicePhase.SPEAKING, message = text) }
        session = viewModelScope.launch {
            say(text)
            if (listenAfter) {
                startListening()
            } else {
                _state.update { if (it.phase == VoicePhase.SPEAKING) it.copy(phase = VoicePhase.IDLE) else it }
            }
        }
    }

    /** Speaks [text] and returns when it has been said (or a safe estimate of that). */
    private suspend fun say(text: String) {
        _announcements.tryEmit(text)
        if (talkBackOn()) {
            delay((600L + text.length * 60L).coerceAtMost(15_000L))
            return
        }
        val engine = ensureTts()
        if (engine == null) {
            delay((600L + text.length * 55L).coerceAtMost(12_000L))
            return
        }
        withTimeoutOrNull(30_000L) {
            suspendCancellableCoroutine { cont ->
                val id = UUID.randomUUID().toString()
                utterances[id] = cont
                cont.invokeOnCancellation { utterances.remove(id) }
                val status = engine.speak(text, TextToSpeech.QUEUE_FLUSH, null, id)
                if (status != TextToSpeech.SUCCESS) {
                    utterances.remove(id)
                    cont.resume(Unit)
                }
            }
        }
    }

    private suspend fun ensureTts(): TextToSpeech? {
        if (tts == null) {
            lateinit var created: TextToSpeech
            created = TextToSpeech(getApplication()) { status ->
                if (status == TextToSpeech.SUCCESS) {
                    // No Vietnamese voice installed: the result is ignored and the default voice reads
                    // the text; it is also always on screen.
                    created.setLanguage(Locale("vi", "VN"))
                    created.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                        override fun onStart(utteranceId: String?) = Unit
                        override fun onDone(utteranceId: String?) = finish(utteranceId)
                        @Deprecated("Deprecated in Java")
                        override fun onError(utteranceId: String?) = finish(utteranceId)
                        override fun onError(utteranceId: String?, errorCode: Int) = finish(utteranceId)
                    })
                    ttsReady.complete(true)
                } else {
                    ttsReady.complete(false)
                }
            }
            tts = created
        }
        val ok = withTimeoutOrNull(4_000L) { ttsReady.await() } ?: false
        return if (ok) tts else null
    }

    private fun finish(utteranceId: String?) {
        val cont = utterances.remove(utteranceId ?: return) ?: return
        runCatching { cont.resume(Unit) }
    }

    private fun stopSpeaking() {
        tts?.stop()
        utterances.values.toList().forEach { runCatching { it.resume(Unit) } }
        utterances.clear()
    }

    // ---- helpers ----

    private fun talkBackOn(): Boolean {
        val am = getApplication<Application>().getSystemService(Context.ACCESSIBILITY_SERVICE) as? AccessibilityManager
        return am?.isEnabled == true && am.isTouchExplorationEnabled
    }

    private fun beep() {
        runCatching {
            val tone = ToneGenerator(AudioManager.STREAM_MUSIC, 70)
            tone.startTone(ToneGenerator.TONE_PROP_ACK, 120)
            viewModelScope.launch {
                delay(300)
                tone.release()
            }
        }
    }

    private fun destroyRecognizer() {
        recognizer?.let { runCatching { it.cancel() }; runCatching { it.destroy() } }
        recognizer = null
    }

    override fun onCleared() {
        destroyRecognizer()
        stopSpeaking()
        tts?.shutdown()
        tts = null
        super.onCleared()
    }

    private companion object {
        const val VI_VN = "vi-VN"
        // SpeechRecognizer.ERROR_TOO_MANY_REQUESTS (API 31), ERROR_LANGUAGE_NOT_SUPPORTED / _UNAVAILABLE (API 31).
        const val ERROR_TOO_MANY_REQUESTS = 10
        const val ERROR_LANGUAGE_NOT_SUPPORTED = 12
        const val ERROR_LANGUAGE_UNAVAILABLE = 13
    }
}
