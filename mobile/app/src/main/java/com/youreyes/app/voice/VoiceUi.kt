package com.youreyes.app.voice

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import com.youreyes.app.ui.components.GradientButton
import com.youreyes.app.ui.components.SecondaryButton
import com.youreyes.app.ui.theme.AppTheme

/** What the screens need to show and drive voice control; built once in the navigation host. */
data class VoiceUi(
    val state: VoiceUiState = VoiceUiState(),
    val onMic: () -> Unit = {},
    val onStop: () -> Unit = {},
    val onDismiss: () -> Unit = {},
    val onOpenSettings: () -> Unit = {},
)

/** What TalkBack says for the voice button in each phase. */
fun voiceButtonDescription(phase: VoicePhase): String = when (phase) {
    VoicePhase.IDLE -> "Nói lệnh. Chạm hai lần rồi nói, ví dụ: gọi mẹ."
    VoicePhase.LISTENING -> "Đang nghe. Chạm hai lần để dừng."
    VoicePhase.WORKING -> "Đang xử lý. Chạm hai lần để dừng."
    VoicePhase.SPEAKING -> "Đang trả lời. Chạm hai lần để dừng đọc và nói lệnh khác."
}

/** The always-available way to start talking: a 64dp pill with an icon and the words "Nói lệnh". */
@Composable
fun VoiceFab(ui: VoiceUi, modifier: Modifier = Modifier) {
    val colors = AppTheme.colors
    val active = ui.state.phase == VoicePhase.LISTENING
    val shape = RoundedCornerShape(32.dp)
    Row(
        modifier = modifier
            .shadow(if (colors.isHighContrast) 0.dp else 8.dp, shape, spotColor = colors.accent)
            .clip(shape)
            .background(if (active) colors.danger else colors.accent)
            .then(if (colors.isHighContrast) Modifier.border(2.dp, colors.ink, shape) else Modifier)
            .defaultMinSize(minHeight = 64.dp)
            .clickable(role = Role.Button, onClick = ui.onMic)
            .padding(horizontal = 22.dp, vertical = 12.dp)
            .semantics(mergeDescendants = true) {
                contentDescription = voiceButtonDescription(ui.state.phase)
                stateDescription = ui.state.statusText
            },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center,
    ) {
        Icon(
            imageVector = if (ui.state.phase == VoicePhase.IDLE) Icons.Default.Mic else Icons.Default.Stop,
            contentDescription = null,
            tint = colors.onAccent,
            modifier = Modifier.size(28.dp),
        )
        Spacer(modifier = Modifier.width(10.dp))
        Text(
            text = if (ui.state.phase == VoicePhase.IDLE) "Nói lệnh" else "Dừng",
            style = MaterialTheme.typography.titleMedium,
            color = colors.onAccent,
        )
    }
}

/**
 * What the app heard and answered. Shown while voice control is busy and until it is dismissed, so a
 * sighted person can read what a blind person hears. Not a live region: the spoken answer is
 * announced once through [VoiceViewModel.announcements].
 */
@Composable
fun VoicePanel(ui: VoiceUi, modifier: Modifier = Modifier) {
    val state = ui.state
    if (!state.isBusy && state.message.isBlank() && state.heard.isBlank()) return
    val colors = AppTheme.colors
    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(24.dp),
        colors = CardDefaults.cardColors(containerColor = colors.surface),
        border = BorderStroke(if (colors.isHighContrast) 2.dp else 1.dp, colors.accentText),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = if (state.isBusy) state.statusText else "Lệnh giọng nói",
                style = MaterialTheme.typography.titleMedium,
                color = colors.accentText,
            )
            if (state.heard.isNotBlank()) {
                Text(
                    text = "Bạn nói: “${state.heard}”",
                    style = MaterialTheme.typography.bodyLarge,
                    color = colors.ink,
                )
            }
            if (state.message.isNotBlank()) {
                Text(
                    text = state.message,
                    style = MaterialTheme.typography.bodyLarge,
                    color = colors.ink,
                )
            }
            if (state.micBlocked) {
                SecondaryButton(text = "Mở cài đặt quyền", onClick = ui.onOpenSettings)
            }
            if (state.isBusy) {
                SecondaryButton(text = "Dừng", onClick = ui.onStop, danger = true, onClickLabel = "dừng lệnh giọng nói")
            } else {
                SecondaryButton(text = "Đóng", onClick = ui.onDismiss, onClickLabel = "đóng khung lệnh giọng nói")
            }
        }
    }
}

/** Why the microphone is needed, shown before the system permission dialog. */
@Composable
fun MicRationaleDialog(onAllow: () -> Unit, onLater: () -> Unit) {
    val colors = AppTheme.colors
    Dialog(onDismissRequest = onLater) {
        Card(
            shape = RoundedCornerShape(24.dp),
            colors = CardDefaults.cardColors(containerColor = colors.surface),
            border = BorderStroke(2.dp, colors.accentText),
        ) {
            Column(modifier = Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                Text(
                    text = "Cho phép dùng micro?",
                    style = MaterialTheme.typography.headlineSmall,
                    color = colors.ink,
                    modifier = Modifier.semantics { heading() },
                )
                Text(
                    text = "SeeVia cần micro để nghe lệnh của bạn, ví dụ “gọi mẹ” hay “đọc tin nhắn”. " +
                        "Âm thanh chỉ được gửi tới dịch vụ nhận giọng nói của điện thoại (Google) để đổi thành chữ, " +
                        "SeeVia không ghi âm lại.",
                    style = MaterialTheme.typography.bodyLarge,
                    color = colors.inkSecondary,
                )
                GradientButton(text = "Cho phép", onClick = onAllow, onClickLabel = "cho phép dùng micro")
                SecondaryButton(text = "Để sau", onClick = onLater)
            }
        }
    }
}
