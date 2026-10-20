package com.youreyes.app.calls.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.CallEnd
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.youreyes.app.calls.CallExtras
import com.youreyes.app.calls.CallState
import com.youreyes.app.ui.theme.AppTheme
import kotlinx.coroutines.delay

/** Buttons of an active call are at least this big; the plan's floor is 64 dp. */
private val CallButtonSize = 72.dp

/** "mm:ss", or "h:mm:ss" past one hour. */
fun formatCallDuration(millis: Long): String {
    val total = (millis / 1000).coerceAtLeast(0)
    val hours = total / 3600
    val minutes = (total % 3600) / 60
    val seconds = total % 60
    return if (hours > 0) "%d:%02d:%02d".format(hours, minutes, seconds) else "%02d:%02d".format(minutes, seconds)
}

/** Who is on the line: the peer's name, or a plain word when the server gave none. */
fun callTitle(extras: CallExtras): String = extras.name.ifBlank { "Người thân" }

/** One line saying what is happening (also read aloud by TalkBack when it changes). */
fun callStatus(state: CallState, extras: CallExtras): String = when (state) {
    CallState.Idle -> ""
    is CallState.Ringing ->
        if (extras.emergency) "Cuộc gọi khẩn cấp từ kính" else "Cuộc gọi từ kính"
    is CallState.Outgoing -> "Đang gọi ${callTitle(extras)}…"
    is CallState.Connecting -> "Đang kết nối…"
    is CallState.Active -> "Đang nói chuyện"
    is CallState.Ended -> state.reasonVi
}

/**
 * The call screen: ringing, calling, talking, ended. Pure UI over [state]/[extras]; the activity
 * owns permissions and the controller calls.
 *
 * Layout rules (kept for people with low vision or a shaky hand): the answer and reject buttons
 * sit far apart so one cannot be hit for the other; every control carries a visible word as well
 * as an icon and a colour; state changes are announced; nothing moves or fades.
 */
@Composable
fun CallScreen(
    state: CallState,
    extras: CallExtras,
    notice: String,
    onAnswer: () -> Unit,
    onReject: () -> Unit,
    onHangup: () -> Unit,
    onMute: (Boolean) -> Unit,
    onSpeaker: (Boolean) -> Unit,
    onClose: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = AppTheme.colors
    Box(
        modifier = modifier
            .fillMaxSize()
            .background(colors.ground)
            .safeDrawingPadding(),
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 24.dp, vertical = 24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            if (extras.emergency && state !is CallState.Ended) EmergencyBanner()

            Spacer(modifier = Modifier.weight(1f))

            Text(
                text = callTitle(extras),
                style = MaterialTheme.typography.displaySmall,
                color = colors.ink,
                textAlign = TextAlign.Center,
                modifier = Modifier
                    .fillMaxWidth()
                    .semantics { heading() },
            )
            Spacer(modifier = Modifier.height(12.dp))
            Text(
                text = callStatus(state, extras),
                style = MaterialTheme.typography.titleLarge,
                color = if (state is CallState.Ended) colors.danger else colors.inkSecondary,
                textAlign = TextAlign.Center,
                modifier = Modifier
                    .fillMaxWidth()
                    .semantics { liveRegion = LiveRegionMode.Polite },
            )
            if (state is CallState.Active) {
                Spacer(modifier = Modifier.height(8.dp))
                CallTimer(sinceMillis = state.sinceMillis)
            }
            if (notice.isNotBlank()) {
                Spacer(modifier = Modifier.height(16.dp))
                Text(
                    text = notice,
                    style = MaterialTheme.typography.bodyLarge,
                    color = colors.danger,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                )
            }

            Spacer(modifier = Modifier.weight(1f))

            when (state) {
                is CallState.Ringing -> RingingControls(onAnswer = onAnswer, onReject = onReject)
                is CallState.Outgoing -> SingleAction(
                    label = "Huỷ cuộc gọi",
                    description = "Huỷ cuộc gọi đang gọi ${callTitle(extras)}",
                    onClick = onHangup,
                )
                is CallState.Connecting -> SingleAction(
                    label = "Cúp máy",
                    description = "Cúp máy, kết thúc cuộc gọi",
                    onClick = onHangup,
                )
                is CallState.Active -> ActiveControls(extras, onMute, onSpeaker, onHangup)
                is CallState.Ended -> SingleAction(
                    label = "Đóng",
                    description = "Đóng màn hình cuộc gọi",
                    onClick = onClose,
                    icon = null,
                    destructive = false,
                )
                CallState.Idle -> Unit
            }
        }
    }
}

@Composable
private fun EmergencyBanner() {
    val colors = AppTheme.colors
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(colors.dangerSoft)
            .border(BorderStroke(2.dp, colors.danger), RoundedCornerShape(16.dp))
            .padding(horizontal = 16.dp, vertical = 14.dp)
            .semantics(mergeDescendants = true) { liveRegion = LiveRegionMode.Assertive },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center,
    ) {
        Icon(Icons.Default.Warning, contentDescription = null, tint = colors.danger, modifier = Modifier.size(28.dp))
        Spacer(modifier = Modifier.width(12.dp))
        Text(
            text = "KHẨN CẤP",
            style = MaterialTheme.typography.headlineSmall,
            color = colors.danger,
        )
    }
}

@Composable
private fun CallTimer(sinceMillis: Long) {
    var elapsed by remember(sinceMillis) { mutableLongStateOf(System.currentTimeMillis() - sinceMillis) }
    LaunchedEffect(sinceMillis) {
        while (true) {
            elapsed = System.currentTimeMillis() - sinceMillis
            delay(1000)
        }
    }
    Text(
        text = formatCallDuration(elapsed),
        style = MaterialTheme.typography.headlineSmall,
        color = AppTheme.colors.ink,
        fontFamily = FontFamily.Monospace,
        // Not a live region: announcing every second would drown out everything else.
        modifier = Modifier.semantics { contentDescription = "Thời gian gọi ${formatCallDuration(elapsed)}" },
    )
}

@Composable
private fun RingingControls(onAnswer: () -> Unit, onReject: () -> Unit) {
    val colors = AppTheme.colors
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.Bottom,
    ) {
        RoundCallButton(
            label = "Từ chối",
            description = "Từ chối cuộc gọi",
            icon = Icons.Default.CallEnd,
            fill = colors.danger,
            onClick = onReject,
        )
        RoundCallButton(
            label = "Nghe",
            description = "Nghe máy",
            icon = Icons.Default.Call,
            fill = colors.success,
            onClick = onAnswer,
        )
    }
}

@Composable
private fun ActiveControls(
    extras: CallExtras,
    onMute: (Boolean) -> Unit,
    onSpeaker: (Boolean) -> Unit,
    onHangup: () -> Unit,
) {
    val colors = AppTheme.colors
    Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(24.dp)) {
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
            RoundCallButton(
                label = if (extras.muted) "Mở mic" else "Tắt mic",
                description = if (extras.muted) "Mở micro" else "Tắt micro",
                state = if (extras.muted) "Micro đang tắt" else "Micro đang bật",
                icon = if (extras.muted) Icons.Default.MicOff else Icons.Default.Mic,
                fill = if (extras.muted) colors.warning else colors.accent,
                onClick = { onMute(!extras.muted) },
            )
            RoundCallButton(
                label = if (extras.speaker) "Dùng tai nghe" else "Loa ngoài",
                description = if (extras.speaker) "Chuyển về loa trong" else "Bật loa ngoài",
                state = if (extras.speaker) "Loa ngoài đang bật" else "Loa ngoài đang tắt",
                icon = Icons.AutoMirrored.Filled.VolumeUp,
                fill = if (extras.speaker) colors.warning else colors.accent,
                onClick = { onSpeaker(!extras.speaker) },
            )
        }
        SingleAction(
            label = "Cúp máy",
            description = "Cúp máy, kết thúc cuộc gọi",
            onClick = onHangup,
        )
    }
}

/** A circle with an icon and a word below it. The word is for reading; TalkBack hears [description]. */
@Composable
private fun RoundCallButton(
    label: String,
    description: String,
    icon: ImageVector,
    fill: Color,
    onClick: () -> Unit,
    state: String? = null,
    size: Dp = CallButtonSize,
) {
    val colors = AppTheme.colors
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier
            .defaultMinSize(minWidth = 96.dp)
            .clip(RoundedCornerShape(20.dp))
            .clickable(role = Role.Button, onClick = onClick)
            .padding(8.dp)
            .semantics(mergeDescendants = true) {
                contentDescription = description
                if (state != null) stateDescription = state
            },
    ) {
        Box(
            modifier = Modifier
                .size(size)
                .clip(CircleShape)
                .background(fill)
                .then(if (colors.isHighContrast) Modifier.border(2.dp, colors.ink, CircleShape) else Modifier),
            contentAlignment = Alignment.Center,
        ) {
            Icon(icon, contentDescription = null, tint = colors.onAccent, modifier = Modifier.size(36.dp))
        }
        Spacer(modifier = Modifier.height(8.dp))
        Text(
            text = label,
            style = MaterialTheme.typography.titleMedium,
            color = colors.ink,
            textAlign = TextAlign.Center,
        )
    }
}

/** A wide button (hang up, cancel, close): the one obvious thing to press. */
@Composable
private fun SingleAction(
    label: String,
    description: String,
    onClick: () -> Unit,
    icon: ImageVector? = Icons.Default.CallEnd,
    destructive: Boolean = true,
) {
    val colors = AppTheme.colors
    val fill = if (destructive) colors.danger else colors.accent
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .defaultMinSize(minHeight = CallButtonSize)
            .clip(RoundedCornerShape(36.dp))
            .background(fill)
            .then(if (colors.isHighContrast) Modifier.border(2.dp, colors.ink, RoundedCornerShape(36.dp)) else Modifier)
            .clickable(role = Role.Button, onClick = onClick)
            .padding(horizontal = 24.dp, vertical = 16.dp)
            .semantics(mergeDescendants = true) {
                contentDescription = description
                role = Role.Button
            },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center,
    ) {
        if (icon != null) {
            Icon(icon, contentDescription = null, tint = colors.onAccent, modifier = Modifier.size(32.dp))
            Spacer(modifier = Modifier.width(12.dp))
        }
        Text(text = label, style = MaterialTheme.typography.titleLarge, color = colors.onAccent)
    }
}
