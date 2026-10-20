package com.youreyes.app.care

import com.youreyes.app.calls.CareLink

/**
 * The care links of the signed-in account split into what the "Người thân" screen shows
 * (protocol 7.1). One account can be a glasses owner and a relative at once; `role` is the
 * caller's own role in each link. Revoked links never appear.
 */
data class CareUiState(
    /** I am the relative and the owner's invitation waits for my Chấp nhận. */
    val pendingToAccept: List<CareLink> = emptyList(),
    /** I am the relative and the link is active: I can call / message that owner's glasses. */
    val myGlassesOwners: List<CareLink> = emptyList(),
    /** I am the owner: the relatives I invited, pending or active (Thu hồi). */
    val myRelatives: List<CareLink> = emptyList(),
) {
    val isEmpty: Boolean
        get() = pendingToAccept.isEmpty() && myGlassesOwners.isEmpty() && myRelatives.isEmpty()

    companion object {
        /** [myUserId] is the caller's `public_user_id`; the grouping itself follows each link's `role`. */
        @Suppress("UNUSED_PARAMETER")
        fun from(links: List<CareLink>, myUserId: String): CareUiState {
            val live = links.filter { it.status == "active" || it.status == "pending" }
            return CareUiState(
                pendingToAccept = live.filter { it.role == "relative" && it.status == "pending" }.sortedBy { it.peerDisplayName },
                myGlassesOwners = live.filter { it.role == "relative" && it.status == "active" }.sortedBy { it.peerDisplayName },
                myRelatives = live.filter { it.role == "owner" }
                    .sortedWith(compareBy<CareLink> { it.priority }.thenBy { it.displayName }),
            )
        }
    }
}

/** The invite form (7.1): phone, display name, aliases (≤ 10, comma separated), emergency, priority 0–100. */
data class InviteForm(
    val phone: String = "",
    val displayName: String = "",
    val aliasesText: String = "",
    val emergency: Boolean = false,
    val priorityText: String = "0",
) {
    val aliases: List<String>
        get() = aliasesText.split(',').map { it.trim() }.filter { it.isNotEmpty() }.distinct()

    /** Blank counts as 0 (called first); anything outside 0–100 or not a number is null. */
    val priority: Int?
        get() = if (priorityText.isBlank()) 0 else priorityText.trim().toIntOrNull()?.takeIf { it in 0..MAX_PRIORITY }

    val phoneDigits: String
        get() = phone.filter { it.isDigit() }

    /** First problem in Vietnamese, or null when the form can be sent. */
    val problem: String?
        get() = when {
            phoneDigits.length < MIN_PHONE_DIGITS -> "Nhập số điện thoại của người thân"
            displayName.isBlank() -> "Nhập tên hiển thị, ví dụ: Mẹ"
            aliases.size > MAX_ALIASES -> "Tên gọi khác tối đa $MAX_ALIASES tên"
            priority == null -> "Thứ tự ưu tiên là số từ 0 đến $MAX_PRIORITY"
            else -> null
        }

    val canSubmit: Boolean
        get() = problem == null

    companion object {
        const val MAX_ALIASES = 10
        const val MAX_PRIORITY = 100
        const val MIN_PHONE_DIGITS = 8
    }
}
