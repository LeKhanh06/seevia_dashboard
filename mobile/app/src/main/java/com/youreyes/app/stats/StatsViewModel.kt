package com.youreyes.app.stats

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
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

data class StatsUiState(
    val isLoggedIn: Boolean = false,
    val isLoading: Boolean = false,
    val period: StatsPeriod = StatsPeriod.WEEK,
    val content: StatsContent? = null,
    val message: String = "",
)

/** Error text for this screen; statistics switched off on the server is not a "try again later". */
fun statsErrorVi(result: ApiResult<*>): String =
    if (result is ApiResult.Failed && result.code == "USAGE_NOT_CONFIGURED") {
        "Máy chủ chưa bật thống kê sử dụng. Hãy báo đội ngũ SeeVia."
    } else {
        apiErrorVi(result)
    }

/** The owner's own usage (`GET /api/v1/me/stats`). Network on IO, state on Main. */
class StatsViewModel(application: Application) : AndroidViewModel(application) {

    private val _state = MutableStateFlow(StatsUiState(isLoggedIn = SessionApi.hasSession(application)))
    val state: StateFlow<StatsUiState> = _state.asStateFlow()

    fun selectPeriod(period: StatsPeriod) {
        if (period == _state.value.period && _state.value.content != null) return
        _state.update { it.copy(period = period) }
        refresh()
    }

    /** Re-checks the login and reloads the selected period. */
    fun refresh() {
        val loggedIn = SessionApi.hasSession(getApplication())
        _state.update { it.copy(isLoggedIn = loggedIn, message = if (loggedIn) it.message else "") }
        if (!loggedIn) return
        val period = _state.value.period
        _state.update { it.copy(isLoading = true, message = "") }
        viewModelScope.launch {
            val (from, to) = statsRange(period, System.currentTimeMillis())
            val result = withContext(Dispatchers.IO) { SessionApi.run(getApplication()) { it.myStats(from, to) } }
            if (_state.value.period != period) return@launch // người dùng đã đổi kỳ trong lúc tải
            _state.update {
                when (result) {
                    is ApiResult.Ok -> it.copy(isLoading = false, content = buildStatsContent(result.data, period))
                    else -> it.copy(
                        isLoading = false,
                        isLoggedIn = result !is ApiResult.Unauthorized,
                        message = statsErrorVi(result),
                    )
                }
            }
        }
    }
}
