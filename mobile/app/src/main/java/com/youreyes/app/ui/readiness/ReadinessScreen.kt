package com.youreyes.app.ui.readiness

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Warning
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.ui.unit.dp
import com.youreyes.app.ui.components.RowCard
import com.youreyes.app.ui.components.ScreenShell
import com.youreyes.app.ui.components.SectionLabel
import com.youreyes.app.ui.components.StatusPill
import com.youreyes.app.ui.components.StatusTone
import com.youreyes.app.ui.theme.AppTheme

/** Re-reads the permissions each time the screen returns to the foreground (e.g. from system settings). */
@Composable
fun RefreshOnResume(onResume: () -> Unit) {
    val owner = LocalLifecycleOwner.current
    DisposableEffect(owner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) onResume()
        }
        owner.lifecycle.addObserver(observer)
        onDispose { owner.lifecycle.removeObserver(observer) }
    }
}

@Composable
fun ReadinessRoute(
    modifier: Modifier = Modifier,
    onBack: (() -> Unit)? = null,
    onOpenEmergencyContact: () -> Unit = {},
) {
    val viewModel: ReadinessViewModel = viewModel()
    val state by viewModel.uiState.collectAsState()
    RefreshOnResume(viewModel::refresh)

    val context = LocalContext.current
    ReadinessScreen(
        state = state,
        onFix = { key ->
            if (key == ReadinessKey.EMERGENCY_CONTACT) onOpenEmergencyContact()
            else ReadinessChecker.openFix(context, key)
        },
        onBack = onBack,
        modifier = modifier,
    )
}

@Composable
fun ReadinessScreen(
    state: ReadinessUiState,
    onFix: (ReadinessKey) -> Unit = {},
    modifier: Modifier = Modifier,
    onBack: (() -> Unit)? = null,
) {
    ScreenShell(modifier = modifier, title = "Sẵn sàng nhận lệnh", onBack = onBack) {
        ReadinessSummaryCard(state = state)

        val missing = state.missing
        if (missing.isNotEmpty()) {
            SectionLabel(text = "Cần bật")
            Column(verticalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
                missing.forEach { item -> ReadinessRow(item = item, onFix = { onFix(item.key) }) }
            }
        }

        val ready = state.items.filter { it.ready }
        if (ready.isNotEmpty()) {
            SectionLabel(text = "Đã bật")
            Column(verticalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
                ready.forEach { item -> ReadinessRow(item = item, onFix = {}) }
            }
        }
    }
}

/** The one-line answer to "will the glasses' commands work?", used on Home and at the top of the screen. */
@Composable
fun ReadinessSummaryCard(
    state: ReadinessUiState,
    modifier: Modifier = Modifier,
    onClick: (() -> Unit)? = null,
) {
    if (state.items.isEmpty()) return
    val colors = AppTheme.colors
    RowCard(
        title = state.summary,
        subtitle = if (state.allReady) {
            "Mọi quyền cần thiết đã bật (${state.readyCount}/${state.total})."
        } else {
            "${state.readyCount}/${state.total} mục đã bật." +
                if (onClick != null) " Chạm để xem và bật." else ""
        },
        icon = if (state.allReady) Icons.Default.CheckCircle else Icons.Default.Warning,
        iconTone = if (state.allReady) colors.success else colors.warning,
        onClick = onClick,
        modifier = modifier,
    )
}

@Composable
private fun ReadinessRow(item: ReadinessItem, onFix: () -> Unit) {
    val colors = AppTheme.colors
    val hint = if (item.key == ReadinessKey.EMERGENCY_CONTACT) "Chạm để nhập số." else "Chạm để mở cài đặt."
    RowCard(
        title = item.title,
        // The state is spoken as words as well as shown: TalkBack reads "title. subtitle".
        subtitle = if (item.ready) "Đã bật. ${item.reason}" else "Cần bật. ${item.reason} $hint",
        icon = if (item.ready) Icons.Default.CheckCircle else Icons.Default.Warning,
        iconTone = if (item.ready) colors.success else colors.warning,
        onClick = if (item.ready) null else onFix,
        trailing = {
            StatusPill(
                text = if (item.ready) "Đã bật" else "Cần bật",
                tone = if (item.ready) StatusTone.SUCCESS else StatusTone.WARNING,
            )
        },
    )
}
