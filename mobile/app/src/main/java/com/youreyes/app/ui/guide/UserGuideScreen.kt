package com.youreyes.app.ui.guide

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import com.youreyes.app.ui.components.MinTouchTarget
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.youreyes.app.ui.components.ScreenShell
import com.youreyes.app.ui.theme.borderColor
import com.youreyes.app.ui.theme.inkColor
import com.youreyes.app.ui.theme.mutedColor
import com.youreyes.app.ui.theme.surfaceColor

data class GuideSection(val title: String, val body: String)

/** One entry per real, working flow in the app — keep this list in sync as new screens/flows land. */
val guideSections = listOf(
    GuideSection(
        title = "Bắt đầu: đăng ký và đăng nhập",
        body = "Vào Hồ sơ, chọn Tài khoản, nhập số điện thoại và mật khẩu để đăng ký. " +
            "Bản thử nghiệm chưa gửi tin nhắn SMS thật, nên mã xác thực do người quản trị cung cấp.",
    ),
    GuideSection(
        title = "Nói lệnh với điện thoại",
        body = "Chạm nút Nói lệnh rồi nói: “gọi mẹ”, “nhắn mẹ tối nay con về muộn”, “đọc tin nhắn”, “kính thế nào”, " +
            "“khẩn cấp”, “mở hồ sơ”. Nói “trợ giúp” để nghe đủ các lệnh, “dừng” để dừng. " +
            "Gửi tin nhắn và gọi khẩn cấp luôn được hỏi lại bằng giọng nói: nói “có” để làm, “không” để huỷ. " +
            "Với TalkBack, chạm hai lần vào nút Nói lệnh rồi nói.",
    ),
    GuideSection(
        title = "Gọi và nhắn tin cho người thân",
        body = "Vào Người thân để mời người thân bằng số điện thoại. Khi họ chấp nhận, bạn gọi được vào kính của họ " +
            "và nhắn tin cho họ; kính sẽ đọc tin nhắn cho người đeo nghe.",
    ),
    GuideSection(
        title = "Liên kết kính",
        body = "Vào màn hình Kính, nhập số máy in ở gọng kính rồi chọn Liên kết kính. " +
            "Có thể hủy liên kết bất cứ lúc nào bằng nút Hủy liên kết kính hiện tại.",
    ),
    GuideSection(
        title = "Điều khiển bằng giọng nói qua kính",
        body = "Đặt xe, phát nhạc, điều hướng, gọi điện đều do kính tự kích hoạt khi bạn nói — " +
            "điện thoại chỉ thực hiện lệnh nhận được, bạn không cần chạm vào màn hình.",
    ),
    GuideSection(
        title = "An toàn khẩn cấp (SOS)",
        body = "Vào An toàn, nhập số điện thoại người thân. Chạm nút SOS 3 lần liên tiếp trong 2 giây để gọi khẩn cấp, " +
            "hoặc chọn Khẩn cấp ở màn hình chính và xác nhận.",
    ),
    GuideSection(
        title = "Lịch sử hoạt động",
        body = "Xem lại các lệnh kính đã gửi tới điện thoại (đặt xe, nhạc, điều hướng, khẩn cấp) " +
            "tại Trang chủ, mục Lịch sử hoạt động.",
    ),
    GuideSection(
        title = "Cỡ chữ và tương phản",
        body = "Vào Hồ sơ, chọn Hiển thị để chọn cỡ chữ To và bật Tương phản cao. " +
            "Thay đổi áp dụng ngay, không cần đăng nhập.",
    ),
    GuideSection(
        title = "Hỗ trợ và góp ý",
        body = "Vào Hồ sơ, chọn Hỗ trợ và góp ý để gửi phản hồi hoặc nhờ đội ngũ SeeVia hỗ trợ.",
    ),
)

@Composable
fun UserGuideScreen(modifier: Modifier = Modifier, onBack: (() -> Unit)? = null) {
    ScreenShell(modifier = modifier, title = "Hướng dẫn sử dụng", onBack = onBack) {
        guideSections.forEach { section -> GuideCard(section) }
    }
}

@Composable
private fun GuideCard(section: GuideSection) {
    var expanded by remember { mutableStateOf(false) }

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .defaultMinSize(minHeight = MinTouchTarget)
            .clickable(
                onClickLabel = if (expanded) "thu gọn" else "mở rộng",
                role = Role.Button,
            ) { expanded = !expanded }
            .semantics(mergeDescendants = true) {
                stateDescription = if (expanded) "đang mở" else "đang thu gọn"
            },
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = surfaceColor),
        border = BorderStroke(1.dp, borderColor),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = section.title,
                    style = MaterialTheme.typography.titleSmall,
                    color = inkColor,
                    modifier = Modifier.weight(1f),
                )
                Icon(
                    imageVector = if (expanded) Icons.Default.KeyboardArrowUp else Icons.Default.KeyboardArrowDown,
                    contentDescription = null,
                    tint = mutedColor,
                )
            }
            if (expanded) {
                Text(
                    text = section.body,
                    style = MaterialTheme.typography.bodyMedium,
                    color = mutedColor,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }
        }
    }
}
