package com.youreyes.app.care

import com.youreyes.app.calls.CareLink
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CareUiStateTest {

    private fun link(id: String, role: String, status: String, name: String = id, priority: Int = 0) = CareLink(
        id = id, role = role, status = status, displayName = name, aliases = emptyList(), emergency = false,
        priority = priority, ownerUserId = "user-owner", relativeUserId = "user-rel",
        peerDisplayName = name, peerPhoneMasked = "***768",
    )

    private val relativePending = link("a", "relative", "pending")
    private val relativeActive = link("b", "relative", "active")
    private val ownerActive = link("c", "owner", "active")
    private val ownerPending = link("d", "owner", "pending")

    @Test
    fun groups_four_sample_links() {
        val state = CareUiState.from(listOf(relativePending, relativeActive, ownerActive, ownerPending), "user-me")
        assertEquals(listOf("a"), state.pendingToAccept.map { it.id })
        assertEquals(listOf("b"), state.myGlassesOwners.map { it.id })
        assertEquals(setOf("c", "d"), state.myRelatives.map { it.id }.toSet())
        assertFalse(state.isEmpty)
    }

    @Test
    fun revoked_links_never_appear() {
        val state = CareUiState.from(
            listOf(link("x", "relative", "revoked"), link("y", "owner", "revoked"), relativeActive),
            "user-me",
        )
        assertEquals(listOf("b"), state.myGlassesOwners.map { it.id })
        assertTrue(state.pendingToAccept.isEmpty())
        assertTrue(state.myRelatives.isEmpty())
    }

    @Test
    fun only_revoked_is_empty() {
        assertTrue(CareUiState.from(listOf(link("x", "owner", "revoked")), "me").isEmpty)
        assertTrue(CareUiState.from(emptyList(), "me").isEmpty)
    }

    @Test
    fun relatives_are_listed_by_call_priority() {
        val state = CareUiState.from(
            listOf(link("late", "owner", "active", "Bố", priority = 5), link("first", "owner", "active", "Mẹ", priority = 0)),
            "me",
        )
        assertEquals(listOf("first", "late"), state.myRelatives.map { it.id })
    }

    @Test
    fun invite_form_validates_like_the_server() {
        val ok = InviteForm(phone = "0911 000 001", displayName = "Mẹ", aliasesText = "mẹ, má , ,mẹ", priorityText = "3")
        assertNull(ok.problem)
        assertEquals(listOf("mẹ", "má"), ok.aliases)
        assertEquals(3, ok.priority)
        assertTrue(ok.canSubmit)

        assertFalse(ok.copy(phone = "09").canSubmit)
        assertFalse(ok.copy(displayName = "  ").canSubmit)
        assertFalse(ok.copy(priorityText = "101").canSubmit)
        assertFalse(ok.copy(priorityText = "-1").canSubmit)
        assertFalse(ok.copy(priorityText = "abc").canSubmit)
        assertEquals(0, ok.copy(priorityText = "").priority)
        val eleven = (1..11).joinToString(",") { "t$it" }
        assertFalse(ok.copy(aliasesText = eleven).canSubmit)
        assertTrue(ok.copy(aliasesText = (1..10).joinToString(",") { "t$it" }).canSubmit)
    }
}
