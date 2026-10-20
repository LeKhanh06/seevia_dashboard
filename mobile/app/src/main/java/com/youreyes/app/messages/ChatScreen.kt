package com.youreyes.app.messages

import android.app.Application
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
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.youreyes.app.ui.components.MinTouchTarget
import com.youreyes.app.ui.theme.AppTheme
import kotlinx.coroutines.delay

private val SendButtonSize = 64.dp
private const val REFRESH_MS = 10_000L

@Composable
fun ChatRoute(
    peerUserId: String,
    peerName: String,
    modifier: Modifier = Modifier,
    onBack: () -> Unit = {},
) {
    val app = LocalContext.current.applicationContext as Application
    val viewModel: ChatViewModel = viewModel(
        key = "chat-$peerUserId",
        factory = viewModelFactory { initializer { ChatViewModel(app, peerUserId, peerName) } },
    )
    val state by viewModel.state.collectAsStateWithLifecycle()

    // While this screen is on top, a message from this sender is shown here instead of notified.
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner, peerUserId, peerName) {
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_START -> MessageEvents.openChat = MessageEvents.OpenChat(peerUserId, peerName)
                Lifecycle.Event.ON_STOP -> MessageEvents.openChat = null
                else -> Unit
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
            MessageEvents.openChat = null
        }
    }
    // "Đã nghe" appears when the glasses read the message aloud; poll gently while visible.
    LaunchedEffect(lifecycleOwner) {
        lifecycleOwner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                delay(REFRESH_MS)
                viewModel.refresh()
            }
        }
    }

    ChatScreen(
        title = peerName.ifBlank { "Hội thoại" },
        state = state,
        onBack = onBack,
        onDraft = viewModel::onDraft,
        onSend = viewModel::send,
        onRetry = viewModel::retry,
        modifier = modifier,
    )
}

@Composable
fun ChatScreen(
    title: String,
    state: ChatScreenState,
    onBack: () -> Unit = {},
    onDraft: (String) -> Unit = {},
    onSend: () -> Unit = {},
    onRetry: (String) -> Unit = {},
    modifier: Modifier = Modifier,
) {
    val colors = AppTheme.colors
    val listState = rememberLazyListState()
    LaunchedEffect(state.items.size) {
        if (state.items.isNotEmpty()) listState.scrollToItem(state.items.lastIndex)
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(colors.ground)
            .imePadding(),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(MinTouchTarget)
                    .clip(RoundedCornerShape(16.dp))
                    .background(colors.surface)
                    .clickable(onClickLabel = "Quay lại màn hình trước", role = Role.Button, onClick = onBack),
                contentAlignment = Alignment.Center,
            ) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Quay lại", tint = colors.ink, modifier = Modifier.size(28.dp))
            }
            Text(
                text = title,
                style = MaterialTheme.typography.headlineSmall,
                color = colors.ink,
                modifier = Modifier
                    .padding(start = 12.dp)
                    .semantics { heading() },
            )
        }

        if (state.error.isNotBlank()) {
            Text(
                text = state.error,
                style = MaterialTheme.typography.bodyMedium,
                color = colors.danger,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 20.dp, vertical = 4.dp)
                    .semantics { liveRegion = LiveRegionMode.Polite },
            )
        }

        Box(modifier = Modifier.weight(1f).fillMaxWidth()) {
            if (state.items.isEmpty() && !state.isLoading) {
                Text(
                    text = "Chưa có tin nhắn nào. Hãy viết tin đầu tiên bên dưới; kính sẽ đọc tin này cho người đeo nghe.",
                    style = MaterialTheme.typography.bodyLarge,
                    color = colors.inkSecondary,
                    textAlign = TextAlign.Center,
                    modifier = Modifier
                        .align(Alignment.Center)
                        .padding(32.dp),
                )
            }
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize(),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                items(state.items, key = { it.id }) { item ->
                    Bubble(item, title, onRetry)
                }
            }
        }

        Composer(state = state, onDraft = onDraft, onSend = onSend)
    }
}

@Composable
private fun Bubble(item: ChatItem, peerName: String, onRetry: (String) -> Unit) {
    val colors = AppTheme.colors
    val shape = RoundedCornerShape(20.dp)
    val time = ChatUiState.timeLabel(item.createdAt)
    val who = if (item.fromMe) "Bạn" else peerName
    val spoken = buildString {
        if (item.isEmergency) append("Tin khẩn cấp. ")
        append(who).append(": ").append(item.text)
        if (time.isNotEmpty()) append(". Lúc ").append(time)
        item.statusLabel?.let { append(". ").append(it) }
    }
    Column(
        modifier = Modifier.fillMaxWidth(),
        horizontalAlignment = if (item.fromMe) Alignment.End else Alignment.Start,
    ) {
        Column(
            modifier = Modifier
                .widthIn(max = 320.dp)
                .clip(shape)
                .background(
                    when {
                        item.isEmergency -> colors.dangerSoft
                        item.fromMe -> colors.accentSoft
                        else -> colors.surface
                    },
                )
                .border(
                    BorderStroke(if (item.isEmergency) 3.dp else 1.dp, if (item.isEmergency) colors.danger else colors.border),
                    shape,
                )
                .then(if (item.canRetry) Modifier.clickable(role = Role.Button, onClickLabel = "gửi lại tin này") { onRetry(item.requestId.orEmpty()) } else Modifier)
                .padding(horizontal = 16.dp, vertical = 12.dp)
                .semantics(mergeDescendants = true) { contentDescription = spoken },
        ) {
            if (item.isEmergency) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Default.Warning, contentDescription = null, tint = colors.danger, modifier = Modifier.size(22.dp))
                    Text(
                        text = "  KHẨN CẤP",
                        style = MaterialTheme.typography.titleMedium,
                        color = colors.danger,
                    )
                }
                Spacer(modifier = Modifier.height(4.dp))
            }
            Text(
                text = if (item.isEmergency) item.text.removePrefix(ChatUiState.EMERGENCY_PREFIX).trimStart(':', ' ') else item.text,
                style = MaterialTheme.typography.bodyLarge,
                color = colors.ink,
            )
        }
        val caption = listOfNotNull(time.takeIf { it.isNotEmpty() }, item.statusLabel).joinToString(" · ")
        if (caption.isNotEmpty()) {
            Text(
                text = caption,
                style = MaterialTheme.typography.bodySmall,
                color = if (item.delivery == Delivery.FAILED) colors.danger else colors.inkSecondary,
                modifier = Modifier
                    .padding(horizontal = 8.dp, vertical = 2.dp)
                    .semantics { liveRegion = LiveRegionMode.Polite }
                    .then(if (item.canRetry) Modifier.clickable(role = Role.Button) { onRetry(item.requestId.orEmpty()) } else Modifier),
            )
        }
    }
}

@Composable
private fun Composer(state: ChatScreenState, onDraft: (String) -> Unit, onSend: () -> Unit) {
    val colors = AppTheme.colors
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(colors.surface)
            .padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.Bottom,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        OutlinedTextField(
            value = state.draft,
            onValueChange = onDraft,
            label = { Text("Tin nhắn") },
            supportingText = if (state.draft.length > ChatScreenState.MAX_LENGTH) {
                { Text("Tối đa ${ChatScreenState.MAX_LENGTH} ký tự") }
            } else {
                null
            },
            isError = state.draft.length > ChatScreenState.MAX_LENGTH,
            maxLines = 4,
            modifier = Modifier.weight(1f),
        )
        Box(
            modifier = Modifier
                .size(SendButtonSize)
                .clip(RoundedCornerShape(20.dp))
                .background(if (state.canSend) colors.accent else colors.surfaceSunken)
                .clickable(enabled = state.canSend, onClickLabel = "gửi tin nhắn", role = Role.Button, onClick = onSend)
                .semantics { contentDescription = "Gửi tin nhắn" },
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                Icons.AutoMirrored.Filled.Send,
                contentDescription = null,
                tint = if (state.canSend) colors.onAccent else colors.inkDisabled,
                modifier = Modifier.size(28.dp),
            )
        }
    }
}
