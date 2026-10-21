package com.youreyes.app.ui.safety

import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.Info
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.youreyes.app.actions.EmergencyCall
import com.youreyes.app.core.AppConfig
import com.youreyes.app.ui.components.GradientButton
import com.youreyes.app.ui.components.InlineMessage
import com.youreyes.app.ui.components.PanelCard
import com.youreyes.app.ui.components.RowCard
import com.youreyes.app.ui.components.ScreenShell
import com.youreyes.app.ui.components.SectionLabel
import com.youreyes.app.ui.components.SosButton
import com.youreyes.app.ui.overview.OverviewViewModel
import com.youreyes.app.ui.theme.AppTheme

@Composable
fun SafetyScreen(
    modifier: Modifier = Modifier,
    onNavigateToProfile: () -> Unit = {},
) {
    val context = LocalContext.current
    val colors = AppTheme.colors
    val overviewViewModel: OverviewViewModel = viewModel()
    val overviewState by overviewViewModel.uiState.collectAsState()

    val emergencyPhone = overviewState.emergencyContact.ifBlank { EmergencyCall.savedNumber(context) }

    var editedPhone by remember(emergencyPhone) { mutableStateOf(emergencyPhone) }
    var notice by remember { mutableStateOf("") }
    var noticeIsError by remember { mutableStateOf(false) }
    var confirming by remember { mutableStateOf(false) }

    fun savePhone(newPhone: String) {
        val trimmed = newPhone.trim()
        overviewViewModel.onEmergencyContactChange(trimmed)
        AppConfig.prefs(context).edit().putString(AppConfig.KEY_EMERGENCY_CONTACT, trimmed).apply()
        notice = if (trimmed.isBlank()) "Đã xóa số khẩn cấp." else "Đã lưu số khẩn cấp: $trimmed"
        noticeIsError = false
    }

    fun placeEmergencyCall() {
        val result = EmergencyCall.start(context)
        notice = EmergencyCall.describe(result)
        noticeIsError = result is EmergencyCall.Result.NoNumber || result is EmergencyCall.Result.Failed
    }

    ScreenShell(modifier = modifier) {
        InlineMessage(text = notice, isError = noticeIsError)

        PanelCard {
            Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxWidth()) {
                Text(
                    text = "Báo động khẩn cấp SOS",
                    style = MaterialTheme.typography.titleLarge,
                    color = colors.ink,
                )
                Text(
                    text = "Chạm nút SOS 3 lần liên tiếp để gọi người thân.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = colors.inkSecondary,
                    modifier = Modifier.padding(top = 2.dp, bottom = 16.dp),
                )
                SosButton(
                    onSosTriggered = { placeEmergencyCall() },
                    emergencyPhone = emergencyPhone,
                )
            }
        }

        SectionLabel(text = "Số điện thoại khẩn cấp")
        PanelCard {
            OutlinedTextField(
                value = editedPhone,
                onValueChange = { editedPhone = it },
                label = { Text("Số điện thoại người thân") },
                supportingText = { Text("Ví dụ: 0912345678") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
                modifier = Modifier.fillMaxWidth(),
            )
            GradientButton(
                text = "Lưu số khẩn cấp",
                onClick = { savePhone(editedPhone) },
                onClickLabel = "lưu số điện thoại khẩn cấp",
            )
        }

        if (emergencyPhone.isNotBlank()) {
            RowCard(
                title = "Gọi khẩn cấp ngay",
                subtitle = "Gọi tới số $emergencyPhone. Sẽ hỏi lại trước khi gọi.",
                icon = Icons.Default.Call,
                iconTone = colors.danger,
                onClick = { confirming = true },
            )
        }

        SectionLabel(text = "Cách dùng nút SOS")
        RowCard(
            title = "Chạm 3 lần trong 2 giây",
            subtitle = "Nút SOS gọi ngay khi chạm đủ 3 lần, để điện thoại trong túi không tự gọi. " +
                "Nếu dùng TalkBack, mở menu hành động của nút SOS và chọn “Gọi khẩn cấp ngay”.",
            icon = Icons.Default.Info,
        )
    }

    if (confirming) {
        EmergencyConfirmDialog(
            number = emergencyPhone,
            onDismiss = { confirming = false },
            onConfirm = {
                confirming = false
                placeEmergencyCall()
            },
        )
    }
}
