package com.youreyes.app.ui.overview

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Help
import androidx.compose.material.icons.automirrored.filled.Message
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.youreyes.app.R
import com.youreyes.app.actions.EmergencyCall
import com.youreyes.app.core.AppConfig
import com.youreyes.app.ui.components.InlineMessage
import com.youreyes.app.ui.components.RowCard
import com.youreyes.app.ui.components.ScreenShell
import com.youreyes.app.ui.components.SecondaryButton
import com.youreyes.app.ui.components.SectionLabel
import com.youreyes.app.ui.readiness.ReadinessSummaryCard
import com.youreyes.app.ui.readiness.ReadinessUiState
import com.youreyes.app.ui.readiness.ReadinessViewModel
import com.youreyes.app.ui.readiness.RefreshOnResume
import com.youreyes.app.ui.safety.EmergencyConfirmDialog
import com.youreyes.app.ui.theme.AppTheme
import com.youreyes.app.voice.GlassesBrief
import com.youreyes.app.voice.VoicePhase
import com.youreyes.app.voice.VoiceUi
import com.youreyes.app.voice.voiceButtonDescription

@Composable
fun OverviewRoute(
    voice: VoiceUi,
    modifier: Modifier = Modifier,
    onNavigateToActivityLog: () -> Unit = {},
    onNavigateToGuide: () -> Unit = {},
    onNavigateToCare: () -> Unit = {},
    onNavigateToGlasses: () -> Unit = {},
    onNavigateToReadiness: () -> Unit = {},
    onOpenChat: (peerUserId: String, name: String, toRelative: Boolean) -> Unit = { _, _, _ -> },
) {
    val viewModel: OverviewViewModel = viewModel()
    val state by viewModel.uiState.collectAsState()
    val quick by viewModel.quick.collectAsState()
    // Picks up a login that happened on the Tài Khoản screen since this ViewModel was
    // first created (see OverviewViewModel.refreshUserId doc).
    LaunchedEffect(Unit) {
        viewModel.refreshUserId()
        viewModel.refreshQuick()
    }

    val readinessViewModel: ReadinessViewModel = viewModel()
    val readiness by readinessViewModel.uiState.collectAsState()
    RefreshOnResume(readinessViewModel::refresh)

    OverviewScreen(
        state = state,
        quick = quick,
        voice = voice,
        readiness = readiness,
        onRetryRegister = viewModel::register,
        onNavigateToReadiness = onNavigateToReadiness,
        onNavigateToActivityLog = onNavigateToActivityLog,
        onNavigateToGuide = onNavigateToGuide,
        onNavigateToCare = onNavigateToCare,
        onNavigateToGlasses = onNavigateToGlasses,
        onMessages = {
            val only = quick.contacts.singleOrNull()
            if (only != null) onOpenChat(only.id, only.name, !only.canCall) else onNavigateToCare()
        },
        modifier = modifier,
    )
}

/**
 * Home. The things done every day sit at the top as large buttons (at least 80dp tall, 18sp
 * lettering, an icon and a word on each): talk to the app, call, message, glasses, emergency.
 * Emergency never acts on one touch; it always asks first.
 */
@Composable
fun OverviewScreen(
    state: OverviewUiState,
    quick: QuickState,
    voice: VoiceUi,
    readiness: ReadinessUiState = ReadinessUiState(),
    onRetryRegister: () -> Unit = {},
    onNavigateToReadiness: () -> Unit = {},
    onNavigateToActivityLog: () -> Unit = {},
    onNavigateToGuide: () -> Unit = {},
    onNavigateToCare: () -> Unit = {},
    onNavigateToGlasses: () -> Unit = {},
    onMessages: () -> Unit = {},
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    var confirmEmergency by remember { mutableStateOf(false) }
    var emergencyResult by remember { mutableStateOf("") }
    val greetingName = remember { AppConfig.prefs(context).getString(AppConfig.KEY_DISPLAY_NAME, "").orEmpty().trim() }

    ScreenShell(modifier = modifier) {
        Header(greetingName)

        // The phone could not register with the server: the glasses cannot reach it until it does.
        if (!state.isRegistered && !state.isLoading && state.registrationFailed) {
            InlineMessage(
                text = "Điện thoại chưa kết nối được máy chủ nên kính chưa gửi lệnh tới điện thoại được. Kiểm tra mạng rồi thử lại.",
                isError = true,
            )
            SecondaryButton(text = "Thử kết nối lại", onClick = onRetryRegister)
        }
        InlineMessage(text = emergencyResult, isError = emergencyResult.startsWith("Chưa") || emergencyResult.startsWith("Không"))

        ActionTile(
            title = if (voice.state.phase == VoicePhase.LISTENING) "Đang nghe… chạm để dừng" else "Nói lệnh",
            detail = "Ví dụ: “gọi mẹ”, “đọc tin nhắn”, “kính thế nào”",
            icon = Icons.Default.Mic,
            tone = TileTone.PRIMARY,
            wide = true,
            onClick = voice.onMic,
            description = voiceButtonDescription(voice.state.phase),
        )

        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
            ActionTile(
                title = "Gọi người thân",
                detail = quick.callDetail,
                icon = Icons.Default.Call,
                onClick = onNavigateToCare,
                modifier = Modifier.weight(1f),
            )
            ActionTile(
                title = "Tin nhắn",
                detail = quick.messageDetail,
                icon = Icons.AutoMirrored.Filled.Message,
                onClick = onMessages,
                modifier = Modifier.weight(1f),
            )
        }

        ActionTile(
            title = "Kính",
            detail = quick.glasses.title + ". " + GlassesBrief.NO_BATTERY,
            icon = Icons.Default.Visibility,
            wide = true,
            onClick = onNavigateToGlasses,
        )

        ActionTile(
            title = "Khẩn cấp",
            detail = "Gọi người thân ngay, có hỏi lại trước khi gọi",
            icon = Icons.Default.Warning,
            tone = TileTone.DANGER,
            wide = true,
            onClick = { confirmEmergency = true },
            description = "Khẩn cấp. Chạm hai lần để mở hộp xác nhận, chưa gọi ngay.",
        )

        ReadinessSummaryCard(state = readiness, onClick = onNavigateToReadiness)

        SectionLabel(text = "Xem thêm")
        RowCard(
            title = "Lịch sử hoạt động",
            subtitle = "Các lệnh kính đã gửi tới điện thoại: đặt xe, nhạc, điều hướng, khẩn cấp.",
            icon = Icons.Default.DateRange,
            onClick = onNavigateToActivityLog,
        )
        RowCard(
            title = "Hướng dẫn sử dụng",
            subtitle = "Cách dùng từng tính năng của SeeVia.",
            icon = Icons.AutoMirrored.Filled.Help,
            onClick = onNavigateToGuide,
        )
    }

    if (confirmEmergency) {
        EmergencyConfirmDialog(
            number = EmergencyCall.savedNumber(context),
            onDismiss = { confirmEmergency = false },
            onConfirm = {
                confirmEmergency = false
                emergencyResult = EmergencyCall.describe(EmergencyCall.start(context))
            },
        )
    }
}

@Composable
private fun Header(greetingName: String) {
    val colors = AppTheme.colors
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        if (colors.isHighContrast) {
            // The wordmark is dark ink on transparency: it would vanish on the near-black ground.
            Text(
                text = "SeeVia",
                style = MaterialTheme.typography.displaySmall,
                color = colors.accent,
                modifier = Modifier.semantics { heading() },
            )
        } else {
            Image(
                painter = painterResource(id = R.drawable.seevia_wordmark),
                contentDescription = "SeeVia, trang chủ",
                contentScale = ContentScale.Fit,
                alignment = Alignment.CenterStart,
                modifier = Modifier
                    .height(64.dp)
                    .semantics { heading() },
            )
        }
        Text(
            text = if (greetingName.isBlank()) "Xin chào" else "Xin chào, $greetingName",
            style = MaterialTheme.typography.titleLarge,
            color = colors.ink,
        )
    }
}

enum class TileTone { DEFAULT, PRIMARY, DANGER }

/**
 * A big daily-use button. Icon plus words (never colour alone), at least 80dp tall, one focus stop
 * for TalkBack that reads the title and then the detail line.
 */
@Composable
fun ActionTile(
    title: String,
    detail: String,
    icon: ImageVector,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    tone: TileTone = TileTone.DEFAULT,
    wide: Boolean = false,
    description: String? = null,
) {
    val colors = AppTheme.colors
    val shape = RoundedCornerShape(24.dp)
    val fill: Color = when (tone) {
        TileTone.PRIMARY -> colors.accent
        TileTone.DANGER -> colors.danger
        TileTone.DEFAULT -> colors.surface
    }
    val ink: Color = when (tone) {
        TileTone.DEFAULT -> colors.ink
        else -> colors.onAccent
    }
    val inkSecondary: Color = when (tone) {
        TileTone.DEFAULT -> colors.inkSecondary
        else -> colors.onAccent
    }
    val iconTint: Color = if (tone == TileTone.DEFAULT) colors.accentText else colors.onAccent

    val base = modifier
        .fillMaxWidth()
        .defaultMinSize(minHeight = if (wide) 88.dp else 128.dp)
        .clip(shape)
        .background(fill)
        .border(
            width = if (tone == TileTone.DEFAULT || colors.isHighContrast) 2.dp else 0.dp,
            color = if (tone == TileTone.DEFAULT) colors.border else colors.ink,
            shape = shape,
        )
        .clickable(role = Role.Button, onClick = onClick)
        .padding(horizontal = 18.dp, vertical = 16.dp)
        .semantics(mergeDescendants = true) {
            contentDescription = description ?: if (detail.isBlank()) title else "$title. $detail"
        }

    if (wide) {
        Row(modifier = base, verticalAlignment = Alignment.CenterVertically) {
            Icon(imageVector = icon, contentDescription = null, tint = iconTint, modifier = Modifier.size(36.dp))
            Spacer(modifier = Modifier.width(16.dp))
            Column(modifier = Modifier.weight(1f)) { TileText(title, detail, ink, inkSecondary) }
        }
    } else {
        Column(modifier = base, horizontalAlignment = Alignment.Start, verticalArrangement = Arrangement.Center) {
            Icon(imageVector = icon, contentDescription = null, tint = iconTint, modifier = Modifier.size(40.dp))
            Spacer(modifier = Modifier.height(8.dp))
            TileText(title, detail, ink, inkSecondary)
        }
    }
}

@Composable
private fun TileText(title: String, detail: String, ink: Color, inkSecondary: Color) {
    Text(text = title, style = MaterialTheme.typography.titleMedium, color = ink)
    if (detail.isNotBlank()) {
        Text(
            text = detail,
            style = MaterialTheme.typography.bodyMedium,
            color = inkSecondary,
            modifier = Modifier.padding(top = 2.dp),
        )
    }
}
