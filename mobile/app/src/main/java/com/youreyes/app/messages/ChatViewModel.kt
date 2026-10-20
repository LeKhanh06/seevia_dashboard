package com.youreyes.app.messages

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.youreyes.app.calls.ChatMessage
import com.youreyes.app.calls.SessionApi
import com.youreyes.app.calls.apiErrorVi
import com.youreyes.app.core.Timestamps
import com.youreyes.app.network.ApiResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.UUID

data class ChatScreenState(
    val items: List<ChatItem> = emptyList(),
    val draft: String = "",
    val isLoading: Boolean = true,
    val error: String = "",
) {
    val canSend: Boolean
        get() = draft.isNotBlank() && draft.length <= MAX_LENGTH

    companion object {
        /** Protocol 7.4: text is 1–1000 characters. */
        const val MAX_LENGTH = 1000
    }
}

/**
 * One conversation with a glasses owner (protocol 7.4). History comes from the server only (no
 * local database); messages that arrive by FCM while the screen is open are inserted directly and
 * reconciled by `id` on the next load. All state changes happen on Main; only the calls run on IO.
 */
class ChatViewModel(
    application: Application,
    private val peerUserId: String,
    val peerName: String,
) : AndroidViewModel(application) {

    private val _state = MutableStateFlow(ChatScreenState())
    val state: StateFlow<ChatScreenState> = _state.asStateFlow()

    private var server: List<ChatMessage> = emptyList()
    private var incoming: List<ChatMessage> = emptyList()
    private var pending: List<PendingSend> = emptyList()

    init {
        refresh()
        viewModelScope.launch {
            MessageEvents.incoming.collect { push ->
                if (MessageEvents.sameName(peerName, push.fromName)) {
                    incoming = incoming + ChatMessage(push.messageId, "to_relative", false, push.text, Timestamps.utc(System.currentTimeMillis()), null)
                    rebuild()
                    refresh() // the server copy carries the real timestamp
                }
            }
        }
    }

    fun refresh() {
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) { SessionApi.run(getApplication()) { it.messages(peerUserId) } }
            if (result is ApiResult.Ok) {
                server = result.data
                rebuild(error = "")
            } else {
                rebuild(error = apiErrorVi(result))
            }
        }
    }

    fun onDraft(text: String) = _state.apply { value = value.copy(draft = text.take(ChatScreenState.MAX_LENGTH + 1)) }

    fun send() {
        val text = _state.value.draft.trim()
        if (!_state.value.canSend) return
        val send = PendingSend(UUID.randomUUID().toString(), text, Timestamps.utc(System.currentTimeMillis()))
        pending = pending + send
        _state.value = _state.value.copy(draft = "")
        rebuild()
        deliver(send)
    }

    /** Chạm vào tin "Gửi lỗi": gửi lại với cùng `request_id`, nên không bao giờ nhắn đôi. */
    fun retry(requestId: String) {
        val failed = pending.firstOrNull { it.requestId == requestId && it.failed } ?: return
        val again = failed.retrying()
        pending = pending.map { if (it.requestId == requestId) again else it }
        rebuild()
        deliver(again)
    }

    private fun deliver(send: PendingSend) {
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                SessionApi.run(getApplication()) { it.sendMessage(peerUserId, send.text, send.requestId) }
            }
            if (result is ApiResult.Ok) {
                pending = pending.filterNot { it.requestId == send.requestId }
                incoming = incoming + ChatMessage(result.data, "to_glasses", true, send.text, send.createdAt, null)
                rebuild(error = "")
            } else {
                pending = pending.map { if (it.requestId == send.requestId) it.failed() else it }
                rebuild(error = apiErrorVi(result))
            }
        }
    }

    private fun rebuild(error: String? = null) {
        val items = ChatUiState.withPending(ChatUiState.merge(server, incoming), pending)
        _state.value = _state.value.copy(items = items, isLoading = false, error = error ?: _state.value.error)
    }
}
