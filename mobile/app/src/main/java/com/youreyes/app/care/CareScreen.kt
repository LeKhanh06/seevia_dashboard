package com.youreyes.app.care

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Message
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.Groups
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.foundation.selection.toggleable
import androidx.lifecycle.viewmodel.compose.viewModel
import com.youreyes.app.calls.CallController
import com.youreyes.app.calls.CareLink
import com.youreyes.app.ui.components.EmptyState
import com.youreyes.app.ui.components.GradientButton
import com.youreyes.app.ui.components.InlineMessage
import com.youreyes.app.ui.components.ScreenShell
import com.youreyes.app.ui.components.SectionLabel
import com.youreyes.app.ui.components.StatusPill
import com.youreyes.app.ui.components.StatusTone
import com.youreyes.app.ui.components.ToggleRow
import com.youreyes.app.ui.theme.AppTheme

/** Every call or message button is at least this tall (the plan's floor for call buttons). */
private val ActionHeight = 64.dp

@Composable
fun CareRoute(
    modifier: Modifier = Modifier,
    onBack: (() -> Unit)? = null,
    onNavigateToAuth: () -> Unit = {},
    onOpenChat: (peerUserId: String, name: String, toRelative: Boolean) -> Unit = { _, _, _ -> },
) {
    val viewModel: CareViewModel = viewModel()
    val state by viewModel.state.collectAsState()
    LaunchedEffect(Unit) { viewModel.refresh() }

    val context = LocalContext.current
    var pendingCall by remember { mutableStateOf<CareLink?>(null) }
    val micPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        val link = pendingCall
        pendingCall = null
        // Without the microphone the call would connect and then end; ask first, call only if granted.
        if (granted && link != null) CallController.call(context, link.ownerUserId, link.peerDisplayName)
    }

    CareScreen(
        state = state,
        onBack = onBack,
        onNavigateToAuth = onNavigateToAuth,
        onRefresh = viewModel::refresh,
        onAccept = viewModel::accept,
        onRevoke = viewModel::revoke,
        onCall = { link ->
            pendingCall = link
            micPermission.launch(Manifest.permission.RECORD_AUDIO)
        },
        onMessage = { link ->
            // role is mine in the link: "owner" = I am the glasses owner and texting my relative.
            if (link.role == "owner") {
                onOpenChat(link.relativeUserId, link.displayName.ifBlank { link.peerDisplayName }, true)
            } else {
                onOpenChat(link.ownerUserId, link.peerDisplayName, false)
            }
        },
        onForm = viewModel::onForm,
        onInvite = viewModel::invite,
        modifier = modifier,
    )
}

@Composable
fun CareScreen(
    state: CareScreenState,
    onBack: (() -> Unit)? = null,
    onNavigateToAuth: () -> Unit = {},
    onRefresh: () -> Unit = {},
    onAccept: (CareLink) -> Unit = {},
    onRevoke: (CareLink) -> Unit = {},
    onCall: (CareLink) -> Unit = {},
    onMessage: (CareLink) -> Unit = {},
    onForm: ((InviteForm) -> InviteForm) -> Unit = {},
    onInvite: () -> Unit = {},
    modifier: Modifier = Modifier,
) {
    var confirmRevoke by remember { mutableStateOf<CareLink?>(null) }

    ScreenShell(modifier = modifier, title = "Người thân", onBack = onBack) {
        InlineMessage(text = state.message, isError = state.isError)

        if (!state.isLoggedIn) {
            EmptyState(
                title = "Chưa đăng nhập",
                message = "Đăng nhập SeeVia để mời người thân, gọi và nhắn tin với người đeo kính.",
                icon = Icons.Default.Groups,
            )
            GradientButton(text = "Đăng nhập", onClick = onNavigateToAuth, onClickLabel = "mở màn hình đăng nhập")
            return@ScreenShell
        }

        if (state.care.pendingToAccept.isNotEmpty()) {
            SectionLabel(text = "Lời mời đang chờ bạn")
            state.care.pendingToAccept.forEach { link ->
                CareCard {
                    PersonHeader(name = link.peerDisplayName.ifBlank { "Một người dùng SeeVia" }, phone = link.peerPhoneMasked)
                    Text(
                        text = "mời bạn làm người thân. Khi bạn chấp nhận, bạn gọi và nhắn tin được cho kính của họ.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = AppTheme.colors.inkSecondary,
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        ActionButton(
                            text = "Chấp nhận",
                            description = "Chấp nhận lời mời của ${link.peerDisplayName}",
                            enabled = state.busyLinkId == null,
                            onClick = { onAccept(link) },
                            primary = true,
                            modifier = Modifier.weight(1f),
                        )
                        ActionButton(
                            text = "Từ chối",
                            description = "Từ chối lời mời của ${link.peerDisplayName}",
                            enabled = state.busyLinkId == null,
                            onClick = { onRevoke(link) },
                            modifier = Modifier.weight(1f),
                        )
                    }
                }
            }
        }

        if (state.care.myGlassesOwners.isNotEmpty()) {
            SectionLabel(text = "Kính bạn có thể gọi")
            state.care.myGlassesOwners.forEach { link ->
                CareCard {
                    PersonHeader(name = link.peerDisplayName.ifBlank { "Chủ kính" }, phone = link.peerPhoneMasked)
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        ActionButton(
                            text = "Gọi kính",
                            description = "Gọi kính của ${link.peerDisplayName}",
                            icon = Icons.Default.Call,
                            onClick = { onCall(link) },
                            primary = true,
                            modifier = Modifier.weight(1f),
                        )
                        ActionButton(
                            text = "Nhắn tin",
                            description = "Nhắn tin cho ${link.peerDisplayName}",
                            icon = Icons.AutoMirrored.Filled.Message,
                            onClick = { onMessage(link) },
                            modifier = Modifier.weight(1f),
                        )
                    }
                }
            }
        }

        if (state.care.myRelatives.isNotEmpty()) {
            SectionLabel(text = "Người thân của bạn")
            state.care.myRelatives.forEach { link ->
                RelativeCard(link, state.busyLinkId == null, onMessage = { onMessage(link) }) { confirmRevoke = link }
            }
        }

        if (state.care.isEmpty && !state.isLoading) {
            EmptyState(
                title = "Chưa có người thân",
                message = "Mời người thân ở phần bên dưới. Họ cần có tài khoản SeeVia.",
                icon = Icons.Default.Groups,
            )
        }
        if (state.isLoading) {
            Text(
                text = "Đang tải danh sách…",
                style = MaterialTheme.typography.bodyMedium,
                color = AppTheme.colors.inkSecondary,
                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
            )
        } else {
            ActionButton(
                text = "Tải lại danh sách",
                description = "Tải lại danh sách người thân",
                onClick = onRefresh,
            )
        }

        SectionLabel(text = "Mời người thân")
        InviteFormCard(state, onForm, onInvite)
    }

    confirmRevoke?.let { link ->
        AlertDialog(
            onDismissRequest = { confirmRevoke = null },
            title = { Text("Thu hồi ${link.displayName.ifBlank { "người thân" }}?") },
            text = { Text("Người này sẽ không gọi hay nhắn tin được cho kính của bạn nữa. Cuộc gọi đang diễn ra sẽ bị ngắt.") },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirmRevoke = null
                        onRevoke(link)
                    },
                    modifier = Modifier.defaultMinSize(minHeight = ActionHeight),
                ) { Text("Thu hồi") }
            },
            dismissButton = {
                TextButton(onClick = { confirmRevoke = null }, modifier = Modifier.defaultMinSize(minHeight = ActionHeight)) {
                    Text("Không thu hồi")
                }
            },
        )
    }
}

@Composable
private fun RelativeCard(link: CareLink, enabled: Boolean, onMessage: () -> Unit, onRevoke: () -> Unit) {
    CareCard {
        PersonHeader(name = link.displayName.ifBlank { link.peerDisplayName }, phone = link.peerPhoneMasked)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (link.status == "pending") {
                StatusPill(text = "Đang chờ chấp nhận", tone = StatusTone.WARNING)
            } else {
                StatusPill(text = "Đã kết nối", tone = StatusTone.SUCCESS)
            }
            if (link.emergency) StatusPill(text = "Khẩn cấp · ưu tiên ${link.priority}", tone = StatusTone.DANGER)
        }
        if (link.aliases.isNotEmpty()) {
            Text(
                text = "Kính gọi là: " + link.aliases.joinToString(", "),
                style = MaterialTheme.typography.bodyMedium,
                color = AppTheme.colors.inkSecondary,
            )
        }
        if (link.status == "active") {
            ActionButton(
                text = "Nhắn tin",
                description = "Nhắn tin cho ${link.displayName.ifBlank { link.peerDisplayName }}",
                icon = Icons.AutoMirrored.Filled.Message,
                onClick = onMessage,
                primary = true,
            )
            Text(
                text = "Gọi người thân từ điện thoại chủ kính chưa làm được. Bạn nhắn tin được.",
                style = MaterialTheme.typography.bodyMedium,
                color = AppTheme.colors.inkSecondary,
            )
        }
        ActionButton(
            text = "Thu hồi",
            description = "Thu hồi ${link.displayName.ifBlank { link.peerDisplayName }}",
            enabled = enabled,
            onClick = onRevoke,
            danger = true,
        )
    }
}

@Composable
private fun InviteFormCard(state: CareScreenState, onForm: ((InviteForm) -> InviteForm) -> Unit, onInvite: () -> Unit) {
    val form = state.form
    CareCard {
        OutlinedTextField(
            value = form.phone,
            onValueChange = { v -> onForm { it.copy(phone = v.take(20)) } },
            label = { Text("Số điện thoại của người thân") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = form.displayName,
            onValueChange = { v -> onForm { it.copy(displayName = v.take(60)) } },
            label = { Text("Tên hiển thị (ví dụ: Mẹ)") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = form.aliasesText,
            onValueChange = { v -> onForm { it.copy(aliasesText = v.take(200)) } },
            label = { Text("Tên gọi khác, cách nhau dấu phẩy") },
            supportingText = { Text("Để kính nhận ra khi bạn nói “gọi cho má”. Tối đa ${InviteForm.MAX_ALIASES} tên.") },
            modifier = Modifier.fillMaxWidth(),
        )
        ToggleRow(
            label = "Gọi người này khi khẩn cấp",
            checked = form.emergency,
            onCheckedChange = { v -> onForm { it.copy(emergency = v) } },
            modifier = Modifier.defaultMinSize(minHeight = ActionHeight),
        )
        OutlinedTextField(
            value = form.priorityText,
            onValueChange = { v -> onForm { it.copy(priorityText = v.filter { c -> c.isDigit() }.take(3)) } },
            label = { Text("Thứ tự gọi khẩn cấp (0–100)") },
            supportingText = { Text("Số nhỏ được gọi trước.") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            modifier = Modifier.fillMaxWidth(),
        )
        form.problem?.let {
            if (form.phone.isNotEmpty() || form.displayName.isNotEmpty()) {
                Text(text = it, style = MaterialTheme.typography.bodyMedium, color = AppTheme.colors.warning)
            }
        }
        GradientButton(
            text = if (state.isInviting) "Đang gửi…" else "Gửi lời mời",
            onClick = onInvite,
            enabled = form.canSubmit && !state.isInviting,
            onClickLabel = "gửi lời mời cho người thân",
        )
    }
}

@Composable
private fun PersonHeader(name: String, phone: String) {
    Column(
        verticalArrangement = Arrangement.spacedBy(2.dp),
        modifier = Modifier.semantics(mergeDescendants = true) {},
    ) {
        Text(text = name, style = MaterialTheme.typography.titleLarge, color = AppTheme.colors.ink)
        if (phone.isNotEmpty()) {
            Text(
                text = "Số điện thoại $phone",
                style = MaterialTheme.typography.bodyMedium,
                color = AppTheme.colors.inkSecondary,
            )
        }
    }
}

@Composable
private fun CareCard(content: @Composable () -> Unit) {
    val colors = AppTheme.colors
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = colors.surface),
        border = BorderStroke(1.dp, colors.border),
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) { content() }
    }
}

/** A 64 dp button with a visible word; colour is never the only cue. */
@Composable
private fun ActionButton(
    text: String,
    description: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    icon: ImageVector? = null,
    primary: Boolean = false,
    danger: Boolean = false,
    enabled: Boolean = true,
) {
    val colors = AppTheme.colors
    val fill = when {
        !enabled -> colors.surfaceSunken
        primary -> colors.accent
        else -> colors.surface
    }
    val ink = when {
        !enabled -> colors.inkDisabled
        primary -> colors.onAccent
        danger -> colors.danger
        else -> colors.accentText
    }
    Row(
        modifier = modifier
            .fillMaxWidth()
            .defaultMinSize(minHeight = ActionHeight)
            .clip(RoundedCornerShape(16.dp))
            .background(fill)
            .border(2.dp, if (!enabled) colors.border else if (danger) colors.danger else if (primary) fill else colors.accentText, RoundedCornerShape(16.dp))
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 12.dp)
            .semantics(mergeDescendants = true) { contentDescription = description },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center,
    ) {
        if (icon != null) {
            Icon(icon, contentDescription = null, tint = ink, modifier = Modifier.size(24.dp))
            Spacer(modifier = Modifier.width(8.dp))
        }
        Text(text = text, style = MaterialTheme.typography.labelLarge, color = ink)
    }
}
