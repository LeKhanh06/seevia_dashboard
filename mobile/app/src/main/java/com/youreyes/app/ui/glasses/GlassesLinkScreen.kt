package com.youreyes.app.ui.glasses

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import com.youreyes.app.ui.components.InlineMessage
import com.youreyes.app.ui.components.MinTouchTarget
import com.youreyes.app.ui.components.SecondaryButton
import com.youreyes.app.ui.theme.accentTextColor
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Info
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.youreyes.app.network.GlassesStatus
import com.youreyes.app.ui.components.GradientButton
import com.youreyes.app.ui.components.RowCard
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
fun GlassesLinkRoute(
    modifier: Modifier = Modifier,
    onBack: (() -> Unit)? = null,
) {
    val viewModel: GlassesLinkViewModel = viewModel()
    val state by viewModel.uiState.collectAsState()
    LaunchedEffect(Unit) {
        viewModel.refreshUserId()
        viewModel.refreshStatus()
    }

    GlassesLinkScreen(
        state = state,
        onGlassesDeviceIdChange = viewModel::onGlassesDeviceIdChange,
        onLinkClick = viewModel::link,
        onUnlinkClick = viewModel::unlink,
        onRefreshStatus = viewModel::refreshStatus,
        onWifiSsidChange = viewModel::onWifiSsidChange,
        onWifiPasswordChange = viewModel::onWifiPasswordChange,
        onScanNetworks = viewModel::scanNetworks,
        onSendWifi = viewModel::sendWifi,
        onUpdateFirmware = viewModel::updateFirmware,
        onBack = onBack,
        modifier = modifier,
    )
}

@Composable
fun GlassesLinkScreen(
    state: GlassesLinkUiState = GlassesLinkUiState(),
    onGlassesDeviceIdChange: (String) -> Unit = {},
    onLinkClick: () -> Unit = {},
    onUnlinkClick: () -> Unit = {},
    onRefreshStatus: () -> Unit = {},
    onWifiSsidChange: (String) -> Unit = {},
    onWifiPasswordChange: (String) -> Unit = {},
    onScanNetworks: () -> Unit = {},
    onSendWifi: () -> Unit = {},
    onUpdateFirmware: () -> Unit = {},
    modifier: Modifier = Modifier,
    onBack: (() -> Unit)? = null,
) {
    ScreenShell(modifier = modifier, title = "Kính SeeVia", onBack = onBack) {
        HeroCard()

        PairingStatusCard(pairedDeviceId = state.pairedDeviceId)

        SectionLabel(text = "Liên kết kính")

        PanelCard {
            OutlinedTextField(
                value = state.glassesDeviceId,
                onValueChange = onGlassesDeviceIdChange,
                label = { Text("Số máy in trên gọng kính") },
                supportingText = { Text("Chỉ cần nhập số, ví dụ 01.") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )

            GradientButton(
                text = if (state.isLoading) "Đang kết nối…" else "Liên kết kính",
                onClick = onLinkClick,
                enabled = state.canSubmit && !state.isLoading,
            )

            SecondaryButton(
                text = "Hủy liên kết kính hiện tại",
                onClick = onUnlinkClick,
                enabled = state.canUnlink,
                danger = true,
            )

            InlineMessage(text = state.message, isError = state.isFailureMessage)
        }

        SectionLabel(text = "Tình trạng kính")
        GlassesStatusCard(
            status = state.status,
            error = state.statusError,
            isRefreshing = state.isRefreshing,
            canRefresh = state.canRefreshStatus,
            onRefresh = onRefreshStatus,
        )

        SectionLabel(text = "Đổi Wi-Fi cho kính")
        WifiPanel(
            state = state,
            onWifiSsidChange = onWifiSsidChange,
            onWifiPasswordChange = onWifiPasswordChange,
            onScanNetworks = onScanNetworks,
            onSendWifi = onSendWifi,
        )

        SectionLabel(text = "Cập nhật phần mềm kính")
        FirmwarePanel(
            state = state,
            onUpdateFirmware = onUpdateFirmware,
        )

        SectionLabel(text = "Hướng dẫn")

        RowCard(
            title = "Tìm số máy trên kính",
            subtitle = "Số máy được in ở mặt trong gọng kính phải. Bản đầu tiên là số 01.",
            icon = Icons.Default.Info,
            iconTone = accentColor,
        )

        RowCard(
            title = "Kính mất Wi-Fi thì làm sao?",
            subtitle = "Kính tự phát ra một mạng Wi-Fi tên \"VisionCare-Setup\" (mật khẩu " +
                "visioncare). Nối điện thoại vào mạng đó rồi khai báo mạng mới ở trên — " +
                "app tự biết phải đi đường nào.",
            icon = Icons.Default.Info,
            iconTone = accentColor,
        )
    }
}

@Composable
private fun HeroCard() {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .shadow(elevation = 6.dp, shape = RoundedCornerShape(22.dp), spotColor = shadowColor)
            .semantics(mergeDescendants = true) {},
        shape = RoundedCornerShape(22.dp),
        colors = CardDefaults.cardColors(containerColor = surfaceColor),
        border = androidx.compose.foundation.BorderStroke(1.dp, borderColor),
    ) {
        Column(modifier = Modifier.padding(18.dp)) {
            Text(
                text = "Liên kết kính SeeVia",
                style = MaterialTheme.typography.titleLarge.copy(
                    fontWeight = FontWeight.Black,
                    color = inkColor,
                ),
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = "Nhập số máy in ở gọng kính để liên kết kính với tài khoản của bạn.",
                style = MaterialTheme.typography.bodyMedium.copy(color = mutedColor),
            )
        }
    }
}

@Composable
private fun PanelCard(content: @Composable ColumnScopeAlias.() -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = surfaceColor),
        border = androidx.compose.foundation.BorderStroke(1.dp, borderColor),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            content = content,
        )
    }
}

private typealias ColumnScopeAlias = androidx.compose.foundation.layout.ColumnScope

/**
 * The live picture of the glasses. The Wi-Fi row is the reason this card
 * exists: without it the user sees "the glasses aren't working" and has no way
 * to know which network they are waiting for.
 */
@Composable
private fun GlassesStatusCard(
    status: GlassesStatus?,
    error: String,
    isRefreshing: Boolean,
    canRefresh: Boolean,
    onRefresh: () -> Unit,
) {
    PanelCard {
        when {
            isRefreshing -> Text(
                text = "Đang đọc trạng thái kính...",
                style = MaterialTheme.typography.bodyMedium.copy(color = mutedColor),
            )

            error.isNotBlank() -> InlineMessage(text = error, isError = true)

            status == null -> Text(
                text = "Nhập số máy rồi bấm \"Đọc lại\" để xem tình trạng kính.",
                style = MaterialTheme.typography.bodyMedium.copy(color = mutedColor),
            )

            // "Chưa từng kết nối" and "đang tắt" need different sentences: the
            // first means the serial is probably wrong, the second means the
            // network is. Collapsing them sends the user to fix the wrong thing.
            !status.known -> Text(
                text = "Chưa có kính số ${status.serial} nào kết nối tới máy chủ. " +
                    "Kiểm tra lại số máy in trên gọng kính.",
                style = MaterialTheme.typography.bodyMedium.copy(color = dangerColor),
            )

            else -> {
                StatusRow(
                    online = status.online,
                    label = if (status.online) "Kính đang hoạt động" else "Kính đang tắt hoặc mất mạng",
                    detail = "Số máy ${status.serial}",
                )
                InfoLine(
                    label = "Mạng Wi-Fi đang đăng ký",
                    value = status.wifiSsid ?: "chưa biết — kính chưa báo về lần nào",
                    highlight = status.wifiSsid != null,
                )
                InfoLine(
                    label = "Phần mềm kính",
                    value = status.firmwareVersion ?: "chưa biết",
                )
                InfoLine(
                    label = "Kênh điều khiển",
                    value = if (status.downlinkOpen) {
                        "đang mở — đổi mạng và cập nhật từ xa được"
                    } else {
                        "chưa mở — phải nối thẳng tới kính qua Wi-Fi VisionCare-Setup"
                    },
                )
                status.pairedTo?.let {
                    InfoLine(label = "Đang ghép với", value = it)
                }
            }
        }

        SecondaryButton(
            text = if (isRefreshing) "Đang đọc…" else "Đọc lại tình trạng",
            onClick = onRefresh,
            enabled = canRefresh,
        )
    }
}

@Composable
private fun WifiPanel(
    state: GlassesLinkUiState,
    onWifiSsidChange: (String) -> Unit,
    onWifiPasswordChange: (String) -> Unit,
    onScanNetworks: () -> Unit,
    onSendWifi: () -> Unit,
) {
    PanelCard {
        // 🔴 Không còn cặp nút "Qua máy chủ / Nối thẳng tới kính", và không còn
        // ô địa chỉ IP. Người dùng chốt 2026-08-25: app chỉ phơi ra ĐỔI WI-FI và
        // CẬP NHẬT FIRMWARE.
        //
        // Bỏ được vì điều kiện chọn đường là thứ MÁY biết chắc còn người thì
        // phải đoán: kính còn giữ kênh `/events` thì đi qua máy chủ, không thì
        // kính đã mất mạng và đường duy nhất là SoftAP. `sendWifi()` tự chọn.
        Text(
            text = if (state.status?.downlinkOpen == true) {
                "Kính đang nghe được máy chủ. Khai tên mạng mới rồi gửi — kính " +
                    "tự khởi động lại và vào mạng đó."
            } else {
                "Kính hiện không nghe được máy chủ. Nối điện thoại vào Wi-Fi " +
                    "\"VisionCare-Setup\" (mật khẩu visioncare), rồi khai mạng mới."
            },
            style = MaterialTheme.typography.bodySmall.copy(color = mutedColor),
        )

        SecondaryButton(
            text = if (state.isScanning) "Đang hỏi kính…" else "Hỏi kính xem có mạng nào",
            onClick = onScanNetworks,
            enabled = !state.isScanning,
        )

        // Chỉ bản quét CỦA KÍNH mới tính. Điện thoại thấy cả mạng 5 GHz mà
        // radio ESP32-S3 không thể vào, và chọn nhầm một trong số đó nhìn y hệt
        // như gõ sai mật khẩu.
        state.visibleNetworks.forEach { network ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .defaultMinSize(minHeight = MinTouchTarget)
                    .clickable(onClickLabel = "chọn mạng ${network.ssid}", role = Role.Button) { onWifiSsidChange(network.ssid) }
                    .semantics(mergeDescendants = true) {
                        contentDescription = "Mạng ${network.ssid}, sóng ${network.rssi} dBm" + if (network.secure) "" else ", không có mật khẩu"
                    }
                    .padding(vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = network.ssid,
                    style = MaterialTheme.typography.bodyMedium.copy(
                        color = inkColor,
                        fontWeight = if (network.ssid == state.wifiSsid) {
                            FontWeight.Bold
                        } else {
                            FontWeight.Normal
                        },
                    ),
                    modifier = Modifier.weight(1f),
                )
                Text(
                    text = "${network.rssi} dBm${if (network.secure) "" else " · mở"}",
                    style = MaterialTheme.typography.bodySmall.copy(color = mutedColor),
                )
            }
        }

        OutlinedTextField(
            value = state.wifiSsid,
            onValueChange = onWifiSsidChange,
            label = { Text("Tên mạng Wi-Fi") },
            supportingText = { Text("Kính chỉ bắt được Wi-Fi 2.4 GHz, không bắt được 5 GHz.") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = state.wifiPassword,
            onValueChange = onWifiPasswordChange,
            label = { Text("Mật khẩu Wi-Fi") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth(),
        )

        GradientButton(
            text = if (state.isLoading) "Đang gửi…" else "Gửi mạng Wi-Fi cho kính",
            onClick = onSendWifi,
            enabled = state.canSendWifi,
        )
    }
}

@Composable
private fun FirmwarePanel(
    state: GlassesLinkUiState,
    onUpdateFirmware: () -> Unit,
) {
    val status = state.status
    PanelCard {
        InfoLine(
            label = "Bản kính đang chạy",
            value = status?.firmwareVersion ?: "chưa biết",
        )
        InfoLine(
            label = "Bản mới nhất",
            value = status?.latestVersion ?: "chưa phát hành bản nào",
        )

        Text(
            text = when {
                status == null -> "Đọc tình trạng kính trước để biết có bản mới không."
                status.updateAvailable && status.downlinkOpen ->
                    "Có bản mới. Kính tải qua Wi-Fi, mất khoảng 1-2 phút, xong tự khởi động lại."
                status.updateAvailable ->
                    "Có bản mới nhưng kính chưa nghe được máy chủ. Kính sẽ tự cập nhật ở lần " +
                        "kiểm tra định kỳ tiếp theo khi có mạng."
                else -> "Kính đang chạy bản mới nhất."
            },
            style = MaterialTheme.typography.bodySmall.copy(color = mutedColor),
        )

        GradientButton(
            text = if (state.isUpdatingFirmware) "Đang gửi lệnh…" else "Cập nhật kính ngay",
            onClick = onUpdateFirmware,
            enabled = state.canUpdateFirmware,
        )
    }
}

@Composable
private fun InfoLine(label: String, value: String, highlight: Boolean = false) {
    Column(modifier = Modifier.semantics(mergeDescendants = true) {}) {
        Text(
            text = label,
            style = MaterialTheme.typography.bodySmall.copy(color = mutedColor),
        )
        Text(
            text = value,
            style = MaterialTheme.typography.bodyMedium.copy(
                color = if (highlight) accentTextColor else inkColor,
                fontWeight = FontWeight.Bold,
            ),
        )
    }
}

@Composable
private fun StatusRow(online: Boolean, label: String, detail: String) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier.semantics(mergeDescendants = true) {},
    ) {
        Box(
            modifier = Modifier
                .size(10.dp)
                .clip(CircleShape)
                .background(if (online) successColor else mutedColor),
        )
        Spacer(modifier = Modifier.width(10.dp))
        Column {
            Text(
                text = label,
                style = MaterialTheme.typography.bodyMedium.copy(
                    fontWeight = FontWeight.Bold,
                    color = inkColor,
                ),
            )
            Text(
                text = detail,
                style = MaterialTheme.typography.bodySmall.copy(color = mutedColor),
            )
        }
    }
}

@Composable
private fun PairingStatusCard(pairedDeviceId: String) {
    val isPaired = pairedDeviceId.isNotBlank()
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = surfaceColor),
        border = androidx.compose.foundation.BorderStroke(1.dp, borderColor),
    ) {
        Row(
            modifier = Modifier
                .padding(14.dp)
                .semantics(mergeDescendants = true) {},
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(10.dp)
                    .clip(CircleShape)
                    .background(if (isPaired) successColor else mutedColor),
            )
            Spacer(modifier = Modifier.width(10.dp))
            Column {
                Text(
                    text = if (isPaired) "Đã liên kết với kính" else "Chưa liên kết kính nào",
                    style = MaterialTheme.typography.bodyMedium.copy(
                        fontWeight = FontWeight.Bold,
                        color = inkColor,
                    ),
                )
            }
        }
    }
}
