package com.youreyes.app.stats

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountCircle
import androidx.compose.material.icons.filled.Insights
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.youreyes.app.ui.components.ChoiceChip
import com.youreyes.app.ui.components.EmptyState
import com.youreyes.app.ui.components.GradientButton
import com.youreyes.app.ui.components.InlineMessage
import com.youreyes.app.ui.components.MiniStat
import com.youreyes.app.ui.components.PanelCard
import com.youreyes.app.ui.components.ScreenShell
import com.youreyes.app.ui.components.SecondaryButton
import com.youreyes.app.ui.components.SectionLabel
import com.youreyes.app.ui.theme.AppTheme
import com.youreyes.app.ui.theme.LocalTypeScale

@Composable
fun StatsRoute(
    modifier: Modifier = Modifier,
    onBack: (() -> Unit)? = null,
    onNavigateToAuth: () -> Unit = {},
) {
    val viewModel: StatsViewModel = viewModel()
    val state by viewModel.state.collectAsState()
    LaunchedEffect(Unit) { viewModel.refresh() }
    StatsScreen(
        state = state,
        onSelectPeriod = viewModel::selectPeriod,
        onRefresh = viewModel::refresh,
        onNavigateToAuth = onNavigateToAuth,
        onBack = onBack,
        modifier = modifier,
    )
}

/**
 * "Thống kê sử dụng" of the signed-in glasses owner. Order is chosen for TalkBack: one summary sentence,
 * then the figures (one focus stop each), then the chart as ONE stop with a spoken summary (plus an
 * optional per-day list), then the per-feature list. Colour never carries meaning on its own.
 */
@Composable
fun StatsScreen(
    state: StatsUiState,
    onSelectPeriod: (StatsPeriod) -> Unit,
    onRefresh: () -> Unit,
    onNavigateToAuth: () -> Unit,
    modifier: Modifier = Modifier,
    onBack: (() -> Unit)? = null,
) {
    ScreenShell(modifier = modifier, title = "Thống kê sử dụng", onBack = onBack) {
        if (!state.isLoggedIn) {
            EmptyState(
                title = "Cần đăng nhập",
                message = "Đăng nhập bằng tài khoản chủ kính để xem bạn đã dùng kính thế nào.",
                icon = Icons.Default.AccountCircle,
            )
            GradientButton(text = "Đăng nhập", onClick = onNavigateToAuth, icon = Icons.Default.AccountCircle)
            return@ScreenShell
        }

        PeriodPicker(selected = state.period, enabled = !state.isLoading, onSelect = onSelectPeriod)

        InlineMessage(text = state.message, isError = true)

        val content = state.content
        when {
            state.isLoading && content == null -> Loading()
            content == null -> SecondaryButton(text = "Thử lại", onClick = onRefresh, icon = Icons.Default.Refresh)
            else -> StatsBody(content, state.period, isLoading = state.isLoading, onRefresh = onRefresh)
        }
    }
}

@Composable
private fun PeriodPicker(selected: StatsPeriod, enabled: Boolean, onSelect: (StatsPeriod) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
        StatsPeriod.entries.forEach { period ->
            ChoiceChip(
                text = period.label,
                selected = period == selected,
                onClick = { onSelect(period) },
                enabled = enabled,
                modifier = Modifier
                    .weight(1f)
                    .semantics { this.selected = period == selected },
            )
        }
    }
}

@Composable
private fun Loading() {
    Box(Modifier.fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) {
        CircularProgressIndicator(
            color = AppTheme.colors.accentText,
            modifier = Modifier.semantics { contentDescription = "Đang tải thống kê" },
        )
    }
}

@Composable
private fun StatsBody(content: StatsContent, period: StatsPeriod, isLoading: Boolean, onRefresh: () -> Unit) {
    val colors = AppTheme.colors
    PanelCard(modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite }) {
        Text(
            text = content.summary,
            style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold),
            color = colors.ink,
        )
        if (isLoading) {
            Text("Đang cập nhật…", style = MaterialTheme.typography.bodySmall, color = colors.inkSecondary)
        }
    }

    if (content.isEmpty) {
        EmptyState(
            title = "Chưa có số liệu",
            message = "Khi bạn dùng kính (đọc chữ, hỏi đường, gọi người thân…), số lượt sẽ hiện ở đây sau vài phút.",
            icon = Icons.Default.Insights,
        )
        SecondaryButton(text = "Tải lại", onClick = onRefresh, icon = Icons.Default.Refresh)
        return
    }

    SectionLabel(text = "Tổng quan")
    // Chữ phóng to: một cột để số và nhãn không bị cắt.
    val columns = if (LocalTypeScale.current > 1.25f) 1 else 2
    content.tiles.chunked(columns).forEach { row ->
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
            row.forEach { tile ->
                MiniStat(
                    label = tile.label,
                    value = tile.value,
                    modifier = Modifier
                        .weight(1f)
                        .clearAndSetSemantics { contentDescription = tile.spoken },
                )
            }
            repeat(columns - row.size) { Box(Modifier.weight(1f)) }
        }
    }

    SectionLabel(text = "Theo ngày")
    DayChart(content, period)

    if (content.features.isNotEmpty()) {
        SectionLabel(text = "Theo chức năng")
        PanelCard {
            content.features.forEach { row -> FeatureLine(row) }
        }
    }
}

@Composable
private fun DayChart(content: StatsContent, period: StatsPeriod) {
    val colors = AppTheme.colors
    var showDays by rememberSaveable(period) { mutableStateOf(false) }
    val max = content.bars.maxOfOrNull { it.uses ?: 0 }?.coerceAtLeast(1) ?: 1
    // Nhãn ngày dưới cột: 7 ngày ghi đủ, 30 ngày ghi mỗi 5 ngày để chữ không chồng nhau.
    val labelEvery = if (content.bars.size <= 7) 1 else 5
    PanelCard {
        Row(
            horizontalArrangement = Arrangement.spacedBy(if (content.bars.size <= 7) 8.dp else 2.dp),
            verticalAlignment = Alignment.Bottom,
            modifier = Modifier
                .fillMaxWidth()
                .height(140.dp)
                .clearAndSetSemantics { contentDescription = content.chartSummary },
        ) {
            content.bars.forEach { bar ->
                val fraction = (bar.uses ?: 0).toFloat() / max
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxHeight(fraction.coerceAtLeast(if ((bar.uses ?: 0) > 0) 0.04f else 0.01f))
                        .clip(RoundedCornerShape(topStart = 4.dp, topEnd = 4.dp))
                        .background(if (bar.uses == null) colors.line else colors.accent),
                )
            }
        }
        Row(modifier = Modifier.fillMaxWidth().clearAndSetSemantics {}) {
            content.bars.forEachIndexed { index, bar ->
                val last = index == content.bars.lastIndex
                Text(
                    text = if (index % labelEvery == 0 || last) bar.label else "",
                    style = MaterialTheme.typography.labelSmall,
                    color = colors.inkSecondary,
                    textAlign = TextAlign.Center,
                    maxLines = 1,
                    modifier = Modifier.weight(1f),
                )
            }
        }
        Text(content.chartSummary, style = MaterialTheme.typography.bodySmall, color = colors.inkSecondary,
            modifier = Modifier.clearAndSetSemantics {})
        SecondaryButton(
            text = if (showDays) "Ẩn số từng ngày" else "Đọc số từng ngày",
            onClick = { showDays = !showDays },
        )
        if (showDays) {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                content.bars.forEach { bar ->
                    Text(
                        text = bar.spoken.replaceFirstChar { it.uppercase() },
                        style = MaterialTheme.typography.bodyMedium,
                        color = colors.ink,
                    )
                }
            }
        }
    }
}

@Composable
private fun FeatureLine(row: FeatureRow) {
    val colors = AppTheme.colors
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 6.dp)
            .clearAndSetSemantics { contentDescription = row.spoken },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(row.label, style = MaterialTheme.typography.bodyLarge, color = colors.ink, fontWeight = FontWeight.SemiBold)
            row.rateText?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = colors.inkSecondary) }
        }
        Text(row.usesText, style = MaterialTheme.typography.titleMedium, color = colors.ink, fontWeight = FontWeight.Bold)
    }
}
