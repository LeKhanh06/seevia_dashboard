package com.youreyes.app.ui.overview

import com.youreyes.app.voice.VoiceContact
import org.junit.Assert.assertEquals
import org.junit.Test

class QuickStateTest {
    private val mother = VoiceContact("u1", "Mẹ")
    private val father = VoiceContact("u2", "Bố")

    @Test
    fun signed_out_says_to_sign_in() {
        val q = QuickState(loggedIn = false)
        assertEquals("Cần đăng nhập", q.callDetail)
        assertEquals("Cần đăng nhập", q.messageDetail)
    }

    @Test
    fun no_contacts() {
        val q = QuickState(loggedIn = true)
        assertEquals("Chưa có người thân", q.callDetail)
        assertEquals("Chưa có người thân", q.messageDetail)
    }

    @Test
    fun one_contact_is_named() {
        val q = QuickState(loggedIn = true, contacts = listOf(mother))
        assertEquals("Mẹ", q.callDetail)
        assertEquals("Nhắn cho Mẹ", q.messageDetail)
    }

    @Test
    fun several_contacts_are_counted() {
        val q = QuickState(loggedIn = true, contacts = listOf(mother, father))
        assertEquals("2 người thân", q.callDetail)
        assertEquals("Chọn người để nhắn", q.messageDetail)
    }

    @Test
    fun registration_failure_is_recognised_only_for_failed_attempts() {
        assertEquals(false, OverviewUiState().registrationFailed)
        assertEquals(true, OverviewUiState(registrationMessage = OverviewViewModel.REGISTER_FAILED + " x").registrationFailed)
    }
}
