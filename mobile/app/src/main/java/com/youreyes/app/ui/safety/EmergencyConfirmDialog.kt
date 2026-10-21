package com.youreyes.app.ui.safety

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
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
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import com.youreyes.app.ui.components.SecondaryButton
import com.youreyes.app.ui.theme.AppTheme

/**
 * "Gọi khẩn cấp?" Asked every time before an emergency call is placed from a button, so a stray
 * touch can never dial. "Không, quay lại" is the first thing TalkBack reaches after the question;
 * the destructive choice is the second, and is a full-width 72dp target.
 */
@Composable
fun EmergencyConfirmDialog(
    number: String,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    val colors = AppTheme.colors
    Dialog(onDismissRequest = onDismiss) {
        Card(
            shape = RoundedCornerShape(24.dp),
            colors = CardDefaults.cardColors(containerColor = colors.surface),
            border = BorderStroke(3.dp, colors.danger),
        ) {
            Column(
                modifier = Modifier.padding(20.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Icon(
                    imageVector = Icons.Default.Warning,
                    contentDescription = null,
                    tint = colors.danger,
                    modifier = Modifier.size(40.dp),
                )
                Text(
                    text = "Gọi khẩn cấp?",
                    style = MaterialTheme.typography.headlineSmall,
                    color = colors.ink,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.semantics {
                        heading()
                        liveRegion = LiveRegionMode.Assertive
                    },
                )
                Text(
                    text = if (number.isBlank()) {
                        "Chưa có số khẩn cấp. Vào mục An toàn để nhập số người thân."
                    } else {
                        "Sẽ gọi ngay tới số $number."
                    },
                    style = MaterialTheme.typography.bodyLarge,
                    color = colors.inkSecondary,
                    textAlign = TextAlign.Center,
                )
                SecondaryButton(text = "Không, quay lại", onClick = onDismiss)
                if (number.isNotBlank()) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .defaultMinSize(minHeight = 72.dp)
                            .clip(RoundedCornerShape(20.dp))
                            .background(colors.danger)
                            .clickable(onClickLabel = "gọi khẩn cấp tới số $number", role = Role.Button, onClick = onConfirm)
                            .padding(horizontal = 20.dp, vertical = 14.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.Center,
                    ) {
                        Icon(Icons.Default.Call, contentDescription = null, tint = colors.onAccent, modifier = Modifier.size(28.dp))
                        Spacer(modifier = Modifier.width(10.dp))
                        Text(
                            text = "Gọi khẩn cấp ngay",
                            style = MaterialTheme.typography.titleLarge,
                            color = colors.onAccent,
                        )
                    }
                }
                Spacer(modifier = Modifier.height(2.dp))
            }
        }
    }
}
