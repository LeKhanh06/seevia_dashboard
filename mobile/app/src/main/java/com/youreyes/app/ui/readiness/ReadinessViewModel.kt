package com.youreyes.app.ui.readiness

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

class ReadinessViewModel(application: Application) : AndroidViewModel(application) {

    private val _uiState = MutableStateFlow(ReadinessUiState())
    val uiState: StateFlow<ReadinessUiState> = _uiState.asStateFlow()

    init {
        refresh()
    }

    /** Re-reads every permission. Call on resume: the person has just come back from system settings. */
    fun refresh() {
        _uiState.value = ReadinessUiState.build(ReadinessChecker.snapshot(getApplication()))
    }
}
