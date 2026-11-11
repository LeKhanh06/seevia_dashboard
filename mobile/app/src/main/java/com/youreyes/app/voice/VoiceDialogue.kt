package com.youreyes.app.voice

/** One message the voice assistant may read out. */
data class SpokenMessage(
    val id: String,
    val fromName: String,
    val text: String,
    val createdAt: String,
    /** Conversation it belongs to (the glasses owner's user id); used to remember how far it was read. */
    val peerId: String = "",
)

/** What the dialogue needs from the app. Implemented over the real API in [AppVoiceEnv]; faked in tests. */
interface VoiceEnv {
    fun isLoggedIn(): Boolean

    /** Everyone the signed-in account can call or message; empty when signed out or offline. */
    suspend fun contacts(): List<VoiceContact>

    /** Starts the call and opens the call screen. */
    fun startCall(contact: VoiceContact)

    /** null = sent, otherwise one Vietnamese sentence saying why not. */
    suspend fun sendMessage(contact: VoiceContact, text: String): String?

    /** Messages from others that were not read to the person yet; null = could not fetch. */
    suspend fun unreadMessages(contacts: List<VoiceContact>): List<SpokenMessage>?

    fun markRead(messages: List<SpokenMessage>)

    /** One or two Vietnamese sentences about the glasses (connection and battery). */
    suspend fun glassesSummary(): String

    /** The saved emergency number, digits as the phone dials them; "" when none. */
    fun emergencyNumber(): String

    /** Places the emergency call; returns one sentence about what happened. */
    fun startEmergency(): String

    fun open(screen: VoiceScreen)
}

/**
 * What to say, and whether to listen for an answer right afterwards.
 * [text] is also what appears on screen, so it is written to be read as well as heard.
 */
data class VoiceReply(val text: String, val listenForAnswer: Boolean = false)

/**
 * The conversation: one utterance in, one reply out, remembering a question that waits for "có"/"không".
 * Anything that sends a message or dials an emergency number is confirmed by voice first, and an
 * answer that cannot be understood twice in a row cancels it: doing nothing is the safe outcome.
 */
class VoiceDialogue(private val env: VoiceEnv) {

    private sealed interface Pending {
        data class ConfirmEmergency(val tries: Int) : Pending
        data class ConfirmMessage(val contact: VoiceContact, val text: String, val tries: Int) : Pending
        data class AwaitText(val contact: VoiceContact) : Pending
        data class Choose(val candidates: List<VoiceContact>, val action: Action) : Pending
    }

    private sealed interface Action {
        data object Call : Action
        data class Message(val text: String) : Action
    }

    private var pending: Pending? = null

    val isAwaitingAnswer: Boolean get() = pending != null

    fun cancel() {
        pending = null
    }

    /** The recogniser heard nothing usable. */
    fun nothingHeard(): VoiceReply {
        val waiting = pending
        return if (waiting != null) {
            pending = null
            VoiceReply("Mình không nghe thấy câu trả lời nên đã huỷ. Chạm nút Nói lệnh để nói lại.")
        } else {
            VoiceReply("Mình không nghe thấy gì. Chạm nút Nói lệnh rồi nói lại, ví dụ: gọi mẹ.")
        }
    }

    suspend fun handle(alternatives: List<String>): VoiceReply {
        val heard = alternatives.firstOrNull { it.isNotBlank() }.orEmpty().trim()
        if (heard.isEmpty()) return nothingHeard()

        pending?.let { return handlePending(it, alternatives, heard) }

        val contacts = if (env.isLoggedIn()) env.contacts() else emptyList()
        return when (val command = VoiceParser.parseBest(alternatives, contacts)) {
            is VoiceCommand.Stop -> VoiceReply("Đã dừng.")
            is VoiceCommand.Help -> VoiceReply(HELP)
            is VoiceCommand.Unknown -> VoiceReply(
                "Mình nghe được: “${command.heard}”. Mình chưa hiểu. Bạn thử nói: gọi mẹ, nhắn mẹ, đọc tin nhắn, kính thế nào, " +
                    "hoặc nói trợ giúp để nghe đủ các lệnh.",
            )
            is VoiceCommand.Open -> {
                env.open(command.screen)
                VoiceReply("Đã mở ${command.screen.spoken}.")
            }
            is VoiceCommand.OpenUnknown -> VoiceReply(
                (if (command.spoken.isBlank()) "Bạn muốn mở màn hình nào?" else "Mình không biết màn hình “${command.spoken}”.") +
                    " Bạn có thể mở: $SCREENS.",
            )
            is VoiceCommand.GlassesStatus -> VoiceReply(env.glassesSummary())
            is VoiceCommand.ReadMessages -> readMessages(contacts)
            is VoiceCommand.Emergency -> emergency()
            is VoiceCommand.Call -> call(command, contacts)
            is VoiceCommand.Message -> message(command, contacts)
        }
    }

    // ---- commands ----

    private fun needLogin(): VoiceReply {
        env.open(VoiceScreen.AUTH)
        return VoiceReply("Bạn cần đăng nhập trước. Mình đã mở màn hình tài khoản.")
    }

    private fun emergency(): VoiceReply {
        val number = env.emergencyNumber()
        if (number.isBlank()) {
            env.open(VoiceScreen.SAFETY)
            return VoiceReply("Chưa có số khẩn cấp. Mình đã mở mục An toàn để bạn nhập số người thân.")
        }
        pending = Pending.ConfirmEmergency(tries = 0)
        return VoiceReply(
            "Gọi khẩn cấp tới số ${number.toList().joinToString(" ")}? Nói có để gọi ngay, hoặc nói không để huỷ.",
            listenForAnswer = true,
        )
    }

    private suspend fun readMessages(contacts: List<VoiceContact>): VoiceReply {
        if (!env.isLoggedIn()) return needLogin()
        val unread = env.unreadMessages(contacts)
            ?: return VoiceReply("Không tải được tin nhắn. Kiểm tra mạng rồi nói lại “đọc tin nhắn”.")
        if (unread.isEmpty()) return VoiceReply("Không có tin nhắn mới.")
        env.markRead(unread)
        val count = unread.size
        val body = unread.joinToString(" ") { "${it.fromName.ifBlank { "Người thân" }} nhắn: ${it.text}." }
        return VoiceReply(if (count == 1) "Có 1 tin nhắn mới. $body" else "Có $count tin nhắn mới. $body")
    }

    private fun call(command: VoiceCommand.Call, contacts: List<VoiceContact>): VoiceReply {
        if (!env.isLoggedIn()) return needLogin()
        val callable = contacts.filter { it.canCall }
        if (command.spoken.isBlank()) return VoiceReply("Bạn muốn gọi ai? ${namesHint(callable, "gọi")}", listenForAnswer = callable.isNotEmpty())
            .also { if (callable.isNotEmpty()) pending = Pending.Choose(callable, Action.Call) }
        return when (val match = VoiceParser.matchContacts(command.spoken, callable)) {
            is ContactMatch.Found -> startCall(match.contact)
            is ContactMatch.Ambiguous -> askWhich(match.contacts, Action.Call)
            ContactMatch.NotFound -> {
                val named = VoiceParser.matchContacts(command.spoken, contacts)
                if (named is ContactMatch.Found) {
                    VoiceReply("Gọi ${named.contact.name} từ điện thoại chủ kính chưa làm được. Bạn có thể nhắn tin cho ${named.contact.name}.")
                } else {
                    VoiceReply("Mình không thấy “${command.spoken}” trong danh sách người thân. ${namesHint(callable, "gọi")}")
                }
            }
        }
    }

    private fun startCall(contact: VoiceContact): VoiceReply {
        env.startCall(contact)
        return VoiceReply("Đang gọi ${contact.name}.")
    }

    private fun message(command: VoiceCommand.Message, contacts: List<VoiceContact>): VoiceReply {
        if (!env.isLoggedIn()) return needLogin()
        if (command.spoken.isBlank()) {
            return VoiceReply("Bạn muốn nhắn cho ai? ${namesHint(contacts, "nhắn")}", listenForAnswer = contacts.isNotEmpty())
                .also { if (contacts.isNotEmpty()) pending = Pending.Choose(contacts, Action.Message(command.text)) }
        }
        return when (val match = command.match) {
            is ContactMatch.Found -> composeFor(match.contact, command.text)
            is ContactMatch.Ambiguous -> askWhich(match.contacts, Action.Message(command.text))
            ContactMatch.NotFound -> VoiceReply("Mình không thấy “${command.spoken}” trong danh sách người thân. ${namesHint(contacts, "nhắn")}")
        }
    }

    private fun composeFor(contact: VoiceContact, text: String): VoiceReply {
        if (text.isBlank()) {
            pending = Pending.AwaitText(contact)
            return VoiceReply("Bạn muốn nhắn gì cho ${contact.name}?", listenForAnswer = true)
        }
        pending = Pending.ConfirmMessage(contact, text, tries = 0)
        return VoiceReply("Gửi cho ${contact.name}: “$text”. Nói có để gửi, hoặc nói không để huỷ.", listenForAnswer = true)
    }

    private fun askWhich(candidates: List<VoiceContact>, action: Action): VoiceReply {
        pending = Pending.Choose(candidates, action)
        return VoiceReply(
            "Có ${candidates.size} người: ${candidates.joinToString(", ") { it.name }}. Bạn muốn ai?",
            listenForAnswer = true,
        )
    }

    // ---- answers to a question ----

    private suspend fun handlePending(waiting: Pending, alternatives: List<String>, heard: String): VoiceReply {
        if (VoiceParser.parse(heard, emptyList()) == VoiceCommand.Stop && waiting !is Pending.AwaitText) {
            pending = null
            return VoiceReply("Đã huỷ.")
        }
        return when (waiting) {
            is Pending.ConfirmEmergency -> when (VoiceParser.parseConfirmation(heard)) {
                true -> {
                    pending = null
                    VoiceReply(env.startEmergency())
                }
                false -> {
                    pending = null
                    VoiceReply("Đã huỷ, không gọi khẩn cấp.")
                }
                null -> if (waiting.tries >= MAX_RETRIES) {
                    pending = null
                    VoiceReply("Mình không nghe rõ câu trả lời nên đã huỷ cuộc gọi khẩn cấp. Cần gọi thì nói lại “khẩn cấp”.")
                } else {
                    pending = waiting.copy(tries = waiting.tries + 1)
                    VoiceReply("Mình chưa nghe rõ. Nói có để gọi khẩn cấp, hoặc nói không để huỷ.", listenForAnswer = true)
                }
            }
            is Pending.ConfirmMessage -> when (VoiceParser.parseConfirmation(heard)) {
                true -> {
                    pending = null
                    val problem = env.sendMessage(waiting.contact, waiting.text)
                    VoiceReply(problem ?: "Đã gửi tin nhắn cho ${waiting.contact.name}.")
                }
                false -> {
                    pending = null
                    VoiceReply("Đã huỷ, chưa gửi tin nhắn.")
                }
                null -> if (waiting.tries >= MAX_RETRIES) {
                    pending = null
                    VoiceReply("Mình không nghe rõ câu trả lời nên chưa gửi tin nhắn.")
                } else {
                    pending = waiting.copy(tries = waiting.tries + 1)
                    VoiceReply("Mình chưa nghe rõ. Nói có để gửi, hoặc nói không để huỷ.", listenForAnswer = true)
                }
            }
            is Pending.AwaitText -> {
                pending = null
                composeFor(waiting.contact, heard)
            }
            is Pending.Choose -> {
                val match = alternatives.map { VoiceParser.matchContacts(it, waiting.candidates) }
                    .firstOrNull { it is ContactMatch.Found } as? ContactMatch.Found
                if (match == null) {
                    pending = null
                    VoiceReply("Mình chưa nhận ra ai. Nói lại lệnh từ đầu, ví dụ: gọi ${waiting.candidates.first().name}.")
                } else {
                    pending = null
                    when (val action = waiting.action) {
                        Action.Call -> startCall(match.contact)
                        is Action.Message -> composeFor(match.contact, action.text)
                    }
                }
            }
        }
    }

    private fun namesHint(contacts: List<VoiceContact>, verb: String): String =
        if (contacts.isEmpty()) {
            "Bạn chưa có người thân nào. Mở màn hình Người thân để mời."
        } else {
            "Bạn có thể $verb: ${contacts.take(5).joinToString(", ") { it.name }}."
        }

    companion object {
        private const val MAX_RETRIES = 1

        const val SCREENS = "trang chủ, tính năng, an toàn, hồ sơ, người thân, kính, hiển thị, hướng dẫn, lịch sử, hỗ trợ, tài khoản"

        val HELP: String =
            "Bạn có thể nói: gọi và tên người thân. Nhắn, tên người thân, rồi nội dung. Đọc tin nhắn. " +
                "Kính thế nào, hoặc pin kính. Khẩn cấp. Mở và tên màn hình. Dừng."
    }
}
