package com.youreyes.app.care

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.youreyes.app.calls.CareLink
import com.youreyes.app.calls.SessionApi
import com.youreyes.app.calls.apiErrorVi
import com.youreyes.app.network.ApiResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class CareScreenState(
    val isLoggedIn: Boolean = false,
    val isLoading: Boolean = false,
    val care: CareUiState = CareUiState(),
    val form: InviteForm = InviteForm(),
    val isInviting: Boolean = false,
    /** Link id with a Chấp nhận / Thu hồi in flight; its buttons are disabled meanwhile. */
    val busyLinkId: String? = null,
    val message: String = "",
    val isError: Boolean = false,
)

/** Care links of the signed-in account (protocol 7.1). Network on IO, state on Main. */
class CareViewModel(application: Application) : AndroidViewModel(application) {

    private val _state = MutableStateFlow(CareScreenState(isLoggedIn = SessionApi.hasSession(application)))
    val state: StateFlow<CareScreenState> = _state.asStateFlow()

    private val myUserId get() = SessionApi.myUserId(getApplication())

    /** Re-checks the login and reloads; call when the screen is (re)entered. */
    fun refresh() {
        val loggedIn = SessionApi.hasSession(getApplication())
        _state.update { it.copy(isLoggedIn = loggedIn) }
        if (!loggedIn) return
        _state.update { it.copy(isLoading = true) }
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) { SessionApi.run(getApplication()) { it.careLinks() } }
            applyLinks(result)
        }
    }

    private fun applyLinks(result: ApiResult<List<CareLink>>) {
        _state.update {
            when (result) {
                is ApiResult.Ok -> it.copy(isLoading = false, care = CareUiState.from(result.data, myUserId))
                else -> it.copy(isLoading = false, isLoggedIn = result !is ApiResult.Unauthorized, message = apiErrorVi(result), isError = true)
            }
        }
    }

    fun onForm(change: (InviteForm) -> InviteForm) = _state.update { it.copy(form = change(it.form)) }

    fun invite() {
        val form = _state.value.form
        val priority = form.priority ?: return
        if (!form.canSubmit || _state.value.isInviting) return
        _state.update { it.copy(isInviting = true, message = "", isError = false) }
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                SessionApi.run(getApplication()) {
                    it.invite(form.phone.trim(), form.displayName.trim(), form.aliases, form.emergency, priority)
                }
            }
            if (result is ApiResult.Ok) {
                _state.update {
                    it.copy(
                        isInviting = false,
                        form = InviteForm(),
                        message = "Đã gửi lời mời. Người thân cần mở SeeVia và bấm Chấp nhận.",
                        isError = false,
                    )
                }
                refresh()
            } else {
                _state.update { it.copy(isInviting = false, message = apiErrorVi(result), isError = true) }
            }
        }
    }

    fun accept(link: CareLink) = act(link, "Đã chấp nhận. Bây giờ bạn có thể gọi và nhắn tin cho ${link.peerDisplayName.ifBlank { "người này" }}.") {
        SessionApi.run(getApplication()) { it.accept(link.id) }
    }

    /** Used for "Thu hồi" (owner) and "Từ chối" (relative declining a pending invite). */
    fun revoke(link: CareLink) = act(link, "Đã thu hồi.") {
        SessionApi.run(getApplication()) { it.revoke(link.id) }
    }

    private fun act(link: CareLink, doneMessage: String, call: () -> ApiResult<CareLink>) {
        if (_state.value.busyLinkId != null) return
        _state.update { it.copy(busyLinkId = link.id, message = "", isError = false) }
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) { call() }
            if (result is ApiResult.Ok) {
                _state.update { it.copy(busyLinkId = null, message = doneMessage, isError = false) }
            } else {
                _state.update { it.copy(busyLinkId = null, message = apiErrorVi(result), isError = true) }
            }
            refresh()
        }
    }
}
