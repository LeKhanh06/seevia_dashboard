package com.youreyes.app.voice

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private class FakeEnv(
    var loggedIn: Boolean = true,
    var people: List<VoiceContact> = listOf(
        VoiceContact("u-me", "Mẹ", listOf("má")),
        VoiceContact("u-nam", "Anh Nam"),
    ),
    var number: String = "0901234567",
) : VoiceEnv {
    val calls = mutableListOf<String>()
    val sent = mutableListOf<Pair<String, String>>()
    val opened = mutableListOf<VoiceScreen>()
    var emergencyStarted = 0
    var sendProblem: String? = null
    var unread: List<SpokenMessage>? = emptyList()
    val markedRead = mutableListOf<String>()

    override fun isLoggedIn() = loggedIn
    override suspend fun contacts() = people
    override fun startCall(contact: VoiceContact) { calls += contact.id }
    override suspend fun sendMessage(contact: VoiceContact, text: String): String? {
        sent += contact.id to text
        return sendProblem
    }
    override suspend fun unreadMessages(contacts: List<VoiceContact>) = unread
    override fun markRead(messages: List<SpokenMessage>) { markedRead += messages.map { it.id } }
    override suspend fun glassesSummary() = "Kính đang hoạt động."
    override fun emergencyNumber() = number
    override fun startEmergency(): String { emergencyStarted++; return "Đang gọi khẩn cấp." }
    override fun open(screen: VoiceScreen) { opened += screen }
}

class VoiceDialogueTest {

    private fun run(dialogue: VoiceDialogue, vararg heard: String): VoiceReply =
        runBlocking { dialogue.handle(heard.toList()) }

    @Test
    fun call_by_name_starts_the_call() {
        val env = FakeEnv()
        val reply = run(VoiceDialogue(env), "gọi mẹ")
        assertEquals(listOf("u-me"), env.calls)
        assertEquals("Đang gọi Mẹ.", reply.text)
        assertFalse(reply.listenForAnswer)
    }

    @Test
    fun call_unknown_name_lists_who_can_be_called_and_dials_nobody() {
        val env = FakeEnv()
        val reply = run(VoiceDialogue(env), "gọi ông Tư")
        assertTrue(env.calls.isEmpty())
        assertTrue(reply.text.contains("ông Tư"))
        assertTrue(reply.text.contains("Mẹ"))
    }

    @Test
    fun call_with_no_contacts_says_how_to_add_one() {
        val env = FakeEnv(people = emptyList())
        val reply = run(VoiceDialogue(env), "gọi mẹ")
        assertTrue(reply.text.contains("Người thân"))
    }

    @Test
    fun signed_out_opens_the_account_screen() {
        val env = FakeEnv(loggedIn = false)
        val reply = run(VoiceDialogue(env), "gọi mẹ")
        assertEquals(listOf(VoiceScreen.AUTH), env.opened)
        assertTrue(env.calls.isEmpty())
        assertTrue(reply.text.contains("đăng nhập"))
    }

    @Test
    fun call_without_a_name_asks_who_then_calls() {
        val env = FakeEnv()
        val dialogue = VoiceDialogue(env)
        val ask = run(dialogue, "gọi")
        assertTrue(ask.listenForAnswer)
        assertTrue(dialogue.isAwaitingAnswer)
        run(dialogue, "anh nam")
        assertEquals(listOf("u-nam"), env.calls)
        assertFalse(dialogue.isAwaitingAnswer)
    }

    @Test
    fun ambiguous_name_asks_which_one() {
        val env = FakeEnv(people = listOf(VoiceContact("a", "Hoa", listOf("Hoa")), VoiceContact("b", "Hoa", listOf("Hoa Nhi"))))
        val dialogue = VoiceDialogue(env)
        val ask = run(dialogue, "gọi hoa")
        assertTrue(ask.listenForAnswer)
        assertTrue(env.calls.isEmpty())
    }

    // ---- messages: always confirmed ----

    @Test
    fun message_is_read_back_and_sent_only_after_yes() {
        val env = FakeEnv()
        val dialogue = VoiceDialogue(env)
        val confirm = run(dialogue, "nhắn mẹ tối nay con về muộn")
        assertTrue(confirm.listenForAnswer)
        assertTrue(confirm.text.contains("tối nay con về muộn"))
        assertTrue(env.sent.isEmpty())
        val done = run(dialogue, "có")
        assertEquals(listOf("u-me" to "tối nay con về muộn"), env.sent)
        assertEquals("Đã gửi tin nhắn cho Mẹ.", done.text)
    }

    @Test
    fun message_no_means_not_sent() {
        val env = FakeEnv()
        val dialogue = VoiceDialogue(env)
        run(dialogue, "nhắn mẹ xin chào")
        val reply = run(dialogue, "không")
        assertTrue(env.sent.isEmpty())
        assertTrue(reply.text.contains("huỷ"))
        assertFalse(dialogue.isAwaitingAnswer)
    }

    @Test
    fun message_unclear_answer_twice_cancels_without_sending() {
        val env = FakeEnv()
        val dialogue = VoiceDialogue(env)
        run(dialogue, "nhắn mẹ xin chào")
        val again = run(dialogue, "hôm nay trời đẹp")
        assertTrue(again.listenForAnswer)
        val end = run(dialogue, "ừm hừm")
        assertFalse(end.listenForAnswer)
        assertTrue(env.sent.isEmpty())
        assertFalse(dialogue.isAwaitingAnswer)
    }

    @Test
    fun message_without_body_asks_for_it() {
        val env = FakeEnv()
        val dialogue = VoiceDialogue(env)
        val ask = run(dialogue, "nhắn mẹ")
        assertTrue(ask.listenForAnswer)
        val confirm = run(dialogue, "con đã đến nơi")
        assertTrue(confirm.text.contains("con đã đến nơi"))
        run(dialogue, "vâng")
        assertEquals(listOf("u-me" to "con đã đến nơi"), env.sent)
    }

    @Test
    fun message_send_failure_is_spoken() {
        val env = FakeEnv().apply { sendProblem = "Không kết nối được máy chủ. Kiểm tra mạng rồi thử lại." }
        val dialogue = VoiceDialogue(env)
        run(dialogue, "nhắn mẹ xin chào")
        val reply = run(dialogue, "có")
        assertEquals("Không kết nối được máy chủ. Kiểm tra mạng rồi thử lại.", reply.text)
    }

    @Test
    fun message_to_unknown_name_is_not_sent() {
        val env = FakeEnv()
        val reply = run(VoiceDialogue(env), "nhắn ông Tư mai tôi qua")
        assertTrue(env.sent.isEmpty())
        assertTrue(reply.text.contains("Mẹ"))
    }

    // ---- emergency: always confirmed ----

    @Test
    fun emergency_asks_then_calls_on_yes() {
        val env = FakeEnv()
        val dialogue = VoiceDialogue(env)
        val ask = run(dialogue, "khẩn cấp")
        assertTrue(ask.listenForAnswer)
        assertTrue(ask.text.contains("0 9 0 1 2 3 4 5 6 7"))
        assertEquals(0, env.emergencyStarted)
        val done = run(dialogue, "có")
        assertEquals(1, env.emergencyStarted)
        assertEquals("Đang gọi khẩn cấp.", done.text)
    }

    @Test
    fun emergency_no_does_not_call() {
        val env = FakeEnv()
        val dialogue = VoiceDialogue(env)
        run(dialogue, "gọi khẩn cấp")
        val reply = run(dialogue, "không")
        assertEquals(0, env.emergencyStarted)
        assertTrue(reply.text.contains("huỷ"))
    }

    @Test
    fun emergency_unclear_then_stop_does_not_call() {
        val env = FakeEnv()
        val dialogue = VoiceDialogue(env)
        run(dialogue, "khẩn cấp")
        assertTrue(run(dialogue, "ờ").listenForAnswer)
        run(dialogue, "dừng")
        assertEquals(0, env.emergencyStarted)
        assertFalse(dialogue.isAwaitingAnswer)
    }

    @Test
    fun emergency_unclear_twice_cancels() {
        val env = FakeEnv()
        val dialogue = VoiceDialogue(env)
        run(dialogue, "khẩn cấp")
        run(dialogue, "ờ")
        val end = run(dialogue, "ờ")
        assertFalse(end.listenForAnswer)
        assertEquals(0, env.emergencyStarted)
        assertTrue(end.text.contains("huỷ"))
    }

    @Test
    fun emergency_without_a_saved_number_opens_safety() {
        val env = FakeEnv(number = "")
        val reply = run(VoiceDialogue(env), "khẩn cấp")
        assertEquals(listOf(VoiceScreen.SAFETY), env.opened)
        assertFalse(reply.listenForAnswer)
        assertEquals(0, env.emergencyStarted)
    }

    @Test
    fun emergency_works_even_when_signed_out() {
        val env = FakeEnv(loggedIn = false)
        val dialogue = VoiceDialogue(env)
        run(dialogue, "khẩn cấp")
        run(dialogue, "có")
        assertEquals(1, env.emergencyStarted)
    }

    // ---- reading, status, navigation ----

    @Test
    fun read_messages_reads_and_marks_read() {
        val env = FakeEnv().apply {
            unread = listOf(
                SpokenMessage("m1", "Mẹ", "Nhớ ăn cơm nhé", "2026-10-21T01:00:00Z"),
                SpokenMessage("m2", "Anh Nam", "Mai họp lúc 9 giờ", "2026-10-21T02:00:00Z"),
            )
        }
        val reply = run(VoiceDialogue(env), "đọc tin nhắn")
        assertTrue(reply.text.startsWith("Có 2 tin nhắn mới."))
        assertTrue(reply.text.contains("Mẹ nhắn: Nhớ ăn cơm nhé."))
        assertEquals(listOf("m1", "m2"), env.markedRead)
    }

    @Test
    fun read_messages_when_none() {
        val reply = run(VoiceDialogue(FakeEnv()), "đọc tin nhắn")
        assertEquals("Không có tin nhắn mới.", reply.text)
    }

    @Test
    fun read_messages_when_the_network_fails_says_so_and_keeps_them_unread() {
        val env = FakeEnv().apply { unread = null }
        val reply = run(VoiceDialogue(env), "đọc tin nhắn")
        assertTrue(reply.text.contains("mạng"))
        assertTrue(env.markedRead.isEmpty())
    }

    @Test
    fun glasses_status_is_spoken() {
        assertEquals("Kính đang hoạt động.", run(VoiceDialogue(FakeEnv()), "kính thế nào").text)
    }

    @Test
    fun open_screen_navigates_and_confirms() {
        val env = FakeEnv()
        val reply = run(VoiceDialogue(env), "mở hồ sơ")
        assertEquals(listOf(VoiceScreen.PROFILE), env.opened)
        assertEquals("Đã mở Hồ sơ.", reply.text)
    }

    @Test
    fun help_lists_the_commands() {
        val reply = run(VoiceDialogue(FakeEnv()), "trợ giúp")
        assertTrue(reply.text.contains("gọi"))
        assertTrue(reply.text.contains("Khẩn cấp"))
        assertTrue(reply.text.contains("Dừng"))
    }

    @Test
    fun not_understood_repeats_what_was_heard_and_suggests() {
        val reply = run(VoiceDialogue(FakeEnv()), "hôm nay trời đẹp quá")
        assertTrue(reply.text.contains("hôm nay trời đẹp quá"))
        assertTrue(reply.text.contains("trợ giúp"))
    }

    @Test
    fun nothing_heard_is_never_silent() {
        val dialogue = VoiceDialogue(FakeEnv())
        assertTrue(dialogue.nothingHeard().text.isNotBlank())
        assertTrue(runBlocking { dialogue.handle(listOf("", "  ")) }.text.isNotBlank())
    }

    @Test
    fun nothing_heard_while_waiting_for_an_answer_cancels() {
        val env = FakeEnv()
        val dialogue = VoiceDialogue(env)
        run(dialogue, "khẩn cấp")
        val reply = dialogue.nothingHeard()
        assertFalse(dialogue.isAwaitingAnswer)
        assertTrue(reply.text.contains("huỷ"))
        assertEquals(0, env.emergencyStarted)
    }

    @Test
    fun stop_clears_everything() {
        val dialogue = VoiceDialogue(FakeEnv())
        run(dialogue, "khẩn cấp")
        dialogue.cancel()
        assertFalse(dialogue.isAwaitingAnswer)
        assertNull(null)
    }
}
