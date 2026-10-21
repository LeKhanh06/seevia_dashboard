package com.youreyes.app.ui.auth

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.youreyes.app.ui.components.ChoiceChip
import com.youreyes.app.ui.components.GradientButton
import androidx.compose.ui.semantics.semantics
import com.youreyes.app.ui.components.InlineMessage
import com.youreyes.app.ui.components.SecondaryButton
import com.youreyes.app.ui.components.ScreenShell
import com.youreyes.app.ui.components.SectionLabel
import com.youreyes.app.ui.theme.accentColor
import com.youreyes.app.ui.theme.borderColor
import com.youreyes.app.ui.theme.dangerColor
import com.youreyes.app.ui.theme.inkColor
import com.youreyes.app.ui.theme.mutedColor
import com.youreyes.app.ui.theme.shadowColor
import com.youreyes.app.ui.theme.successColor
import com.youreyes.app.ui.theme.surfaceColor

@Composable
fun AuthRoute(modifier: Modifier = Modifier, onBack: (() -> Unit)? = null) {
    val viewModel: AuthViewModel = viewModel()
    val state by viewModel.uiState.collectAsState()
    AuthScreen(
        state = state,
        onModeChange = viewModel::onModeChange,
        onPhoneNumberChange = viewModel::onPhoneNumberChange,
        onPasswordChange = viewModel::onPasswordChange,
        onDisplayNameChange = viewModel::onDisplayNameChange,
        onOtpCodeChange = viewModel::onOtpCodeChange,
        onSubmitCredentials = viewModel::submitCredentials,
        onSubmitOtp = viewModel::submitOtp,
        onLogout = viewModel::logout,
        onBack = onBack,
        modifier = modifier,
    )
}

@Composable
fun AuthScreen(
    state: AuthUiState,
    onModeChange: (AuthMode) -> Unit = {},
    onPhoneNumberChange: (String) -> Unit = {},
    onPasswordChange: (String) -> Unit = {},
    onDisplayNameChange: (String) -> Unit = {},
    onOtpCodeChange: (String) -> Unit = {},
    onSubmitCredentials: () -> Unit = {},
    onSubmitOtp: () -> Unit = {},
    onLogout: () -> Unit = {},
    modifier: Modifier = Modifier,
    onBack: (() -> Unit)? = null,
) {
    ScreenShell(modifier = modifier, title = "Tài khoản", onBack = onBack) {
        when {
            state.isLoggedIn -> LoggedInCard(state, onLogout)
            state.step == AuthStep.OTP -> OtpCard(state, onOtpCodeChange, onSubmitOtp)
            else -> CredentialsCard(
                state = state,
                onModeChange = onModeChange,
                onPhoneNumberChange = onPhoneNumberChange,
                onPasswordChange = onPasswordChange,
                onDisplayNameChange = onDisplayNameChange,
                onSubmit = onSubmitCredentials,
            )
        }

        InlineMessage(text = state.message, isError = state.isError)
    }
}

@Composable
private fun LoggedInCard(state: AuthUiState, onLogout: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = surfaceColor),
        border = BorderStroke(1.dp, borderColor),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Column(modifier = Modifier.semantics(mergeDescendants = true) {}) {
                Text(
                    text = state.loggedInDisplayName.ifBlank { "Đã đăng nhập" },
                    style = MaterialTheme.typography.titleMedium,
                    color = inkColor,
                )
                Text(
                    text = "Số điện thoại: ${state.loggedInPhoneNumber}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = mutedColor,
                )
            }
            SecondaryButton(
                text = if (state.isLoading) "Đang đăng xuất…" else "Đăng xuất",
                onClick = onLogout,
                enabled = !state.isLoading,
                danger = true,
                onClickLabel = "đăng xuất khỏi tài khoản",
            )
        }
    }
}

@Composable
private fun OtpCard(state: AuthUiState, onOtpCodeChange: (String) -> Unit, onSubmit: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = surfaceColor),
        border = BorderStroke(1.dp, borderColor),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                text = "Nhập mã xác thực cho số ${state.phoneNumber}",
                style = MaterialTheme.typography.titleMedium,
                color = inkColor,
            )
            Text(
                text = "Bản thử nghiệm chưa gửi tin nhắn SMS thật. Hãy hỏi người quản trị để lấy mã.",
                style = MaterialTheme.typography.bodyMedium,
                color = mutedColor,
            )
            OutlinedTextField(
                value = state.otpCode,
                onValueChange = onOtpCodeChange,
                label = { Text("Mã xác thực") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                modifier = Modifier.fillMaxWidth(),
            )
            GradientButton(
                text = if (state.isLoading) "Đang xác thực…" else "Xác nhận mã",
                onClick = onSubmit,
                enabled = state.canSubmitOtp,
            )
        }
    }
}

@Composable
private fun CredentialsCard(
    state: AuthUiState,
    onModeChange: (AuthMode) -> Unit,
    onPhoneNumberChange: (String) -> Unit,
    onPasswordChange: (String) -> Unit,
    onDisplayNameChange: (String) -> Unit,
    onSubmit: () -> Unit,
) {
    SectionLabel(text = "Đăng nhập hoặc đăng ký")

    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        ChoiceChip(
            text = "Đăng nhập",
            selected = state.mode == AuthMode.LOGIN,
            onClick = { onModeChange(AuthMode.LOGIN) },
            modifier = Modifier.weight(1f),
        )
        ChoiceChip(
            text = "Đăng ký",
            selected = state.mode == AuthMode.REGISTER,
            onClick = { onModeChange(AuthMode.REGISTER) },
            modifier = Modifier.weight(1f),
        )
    }

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .shadow(elevation = 4.dp, shape = RoundedCornerShape(16.dp), spotColor = shadowColor),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = surfaceColor),
        border = BorderStroke(1.dp, borderColor),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            OutlinedTextField(
                value = state.phoneNumber,
                onValueChange = onPhoneNumberChange,
                label = { Text("Số điện thoại") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = state.password,
                onValueChange = onPasswordChange,
                label = { Text("Mật khẩu") },
                supportingText = { Text("Ít nhất 6 ký tự") },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                modifier = Modifier.fillMaxWidth(),
            )
            if (state.mode == AuthMode.REGISTER) {
                OutlinedTextField(
                    value = state.displayName,
                    onValueChange = onDisplayNameChange,
                    label = { Text("Tên hiển thị") },
                    supportingText = { Text("Không bắt buộc. Người thân sẽ thấy tên này.") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            GradientButton(
                text = when {
                    state.isLoading -> "Đang xử lý…"
                    state.mode == AuthMode.REGISTER -> "Đăng ký"
                    else -> "Đăng nhập"
                },
                onClick = onSubmit,
                enabled = state.canSubmitCredentials,
            )
        }
    }
}
