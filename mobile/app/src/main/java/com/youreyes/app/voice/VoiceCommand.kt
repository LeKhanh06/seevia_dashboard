package com.youreyes.app.voice

import com.youreyes.app.voice.VoiceText.Words

/** A person the voice commands can name: the display name plus every alias the app knows. */
data class VoiceContact(
    val id: String,
    val name: String,
    val aliases: List<String> = emptyList(),
    /** Calls need an active link in which this person wears the glasses. Messages go to the same people. */
    val canCall: Boolean = true,
) {
    val allNames: List<String> get() = (listOf(name) + aliases).filter { it.isNotBlank() }
}

/** Screens a command can open. [spoken] is how the app names it aloud. */
enum class VoiceScreen(val spoken: String, internal val keywords: List<String>) {
    HOME("Trang chủ", listOf("trang chu", "man hinh chinh", "ve nha", "trang dau")),
    FEATURES("Tính năng", listOf("tinh nang")),
    SAFETY("An toàn", listOf("an toan", "sos")),
    PROFILE("Hồ sơ", listOf("ho so", "cai dat")),
    CARE("Người thân", listOf("nguoi than", "lien he", "danh ba")),
    GLASSES("Kính", listOf("lien ket kinh", "kinh")),
    DISPLAY("Hiển thị", listOf("hien thi", "co chu", "chu to", "tuong phan")),
    GUIDE("Hướng dẫn", listOf("huong dan", "cach dung")),
    ACTIVITY_LOG("Lịch sử hoạt động", listOf("lich su", "nhat ky", "hoat dong")),
    SUPPORT("Hỗ trợ", listOf("ho tro", "gop y", "phan hoi")),
    AUTH("Tài khoản", listOf("tai khoan", "dang nhap", "dang ky")),
    READINESS("Kiểm tra sẵn sàng", listOf("kiem tra", "san sang")),
}

/** Who a "gọi"/"nhắn" command names. */
sealed interface ContactMatch {
    data class Found(val contact: VoiceContact) : ContactMatch
    data class Ambiguous(val contacts: List<VoiceContact>) : ContactMatch
    data object NotFound : ContactMatch
}

sealed interface VoiceCommand {
    /** [spoken] is the name as heard ("" when none was said). */
    data class Call(val spoken: String, val match: ContactMatch) : VoiceCommand
    data class Message(val spoken: String, val match: ContactMatch, val text: String) : VoiceCommand
    data object ReadMessages : VoiceCommand
    data object GlassesStatus : VoiceCommand
    data object Emergency : VoiceCommand
    data class Open(val screen: VoiceScreen) : VoiceCommand
    data class OpenUnknown(val spoken: String) : VoiceCommand
    data object Help : VoiceCommand
    data object Stop : VoiceCommand
    data class Unknown(val heard: String) : VoiceCommand
}

/**
 * Turns what the recogniser heard into a [VoiceCommand]. Tolerant on purpose: tone marks may be
 * missing, filler words ("làm ơn", "nhé") are ignored and names match approximately. Where a
 * command could do harm (an emergency call, sending a message) the caller still asks for a
 * spoken "có" before acting; this class only understands.
 */
object VoiceParser {

    private fun p(vararg phrases: String): List<List<String>> =
        phrases.map { VoiceText.phrase(it) }.sortedByDescending { it.size }

    private val leadFiller = p(
        "lam on", "vui long", "xin", "hay", "giup toi", "giup minh", "cho toi", "cho minh", "toi muon", "minh muon",
        "toi can", "minh can", "bay gio", "seevia", "see via", "ok",
    )
    // Not "đi", "a", "ha", "nha": they are also names ("Dì", "Hà", "Nhã").
    private val trailFiller = p("nhe", "giup toi", "giup minh", "ngay", "ngay bay gio")

    private val stopPhrases = setOf(
        "dung", "dung lai", "thoi", "thoi nhe", "huy", "huy bo", "im", "im lang", "thoat", "ket thuc", "khong can",
    )

    private val messagePrefixes = p(
        "nhan tin cho", "nhan tin toi", "nhan tin den", "nhan tin", "nhan cho", "nhan toi", "nhan",
        "gui tin nhan cho", "gui tin nhan toi", "gui tin nhan den", "gui tin nhan", "gui tin cho", "gui tin toi", "gui tin",
        "soan tin cho", "soan tin", "viet tin cho", "viet tin",
    )
    private val callPrefixes = p("goi dien cho", "goi dien toi", "goi dien den", "goi dien", "goi cho", "goi toi", "goi den", "goi")
    private val openPrefixes = p(
        "mo ra", "mo", "vao", "chuyen den", "chuyen toi", "chuyen sang", "den man hinh", "toi man hinh", "di den", "di toi", "xem",
    )
    private val openNoise = setOf("man", "hinh", "trang", "muc", "phan", "tab", "cho", "toi", "cua", "tai")
    private val messageConnectors = p("voi noi dung", "noi dung", "nhu sau", "la", "rang", "bao", "noi")
    private val honorifics = setOf("anh", "chi", "em", "co", "chu", "bac", "di", "cau", "mo", "ong", "ba", "thay", "con")

    /** Best reading of one utterance. [contacts] are the people that can be named. */
    fun parse(heard: String, contacts: List<VoiceContact>): VoiceCommand {
        var words = Words.of(heard)
        if (words.isEmpty) return VoiceCommand.Unknown("")
        words = stripFiller(words)
        if (words.isEmpty) return VoiceCommand.Unknown(heard.trim())
        val joined = words.joined()

        if (joined in stopPhrases) return VoiceCommand.Stop
        // "nhận tin nhắn" (mis-heard "đọc tin nhắn") must not be read as "nhắn tin ...".
        if (words.startsWith(VoiceText.phrase("nhan tin nhan"))) return VoiceCommand.ReadMessages

        messagePrefixes.firstOrNull { words.startsWith(it) }?.let { prefix ->
            return parseMessage(words.drop(prefix.size), contacts)
        }
        callPrefixes.firstOrNull { words.startsWith(it) }?.let { prefix ->
            val rest = words.drop(prefix.size)
            if (isEmergencyWords(rest.joined())) return VoiceCommand.Emergency
            if (rest.isEmpty) return VoiceCommand.Call("", ContactMatch.NotFound)
            return VoiceCommand.Call(rest.originalText(), matchContacts(rest.originalText(), contacts))
        }
        if (isHelp(joined)) return VoiceCommand.Help
        if (isEmergencyWords(joined)) return VoiceCommand.Emergency
        if (words.containsPhrase(VoiceText.phrase("tin nhan")) || words.containsPhrase(VoiceText.phrase("doc tin"))) {
            return VoiceCommand.ReadMessages
        }
        if (mentionsGlasses(words)) return VoiceCommand.GlassesStatus
        openPrefixes.firstOrNull { words.startsWith(it) }?.let { prefix ->
            return parseOpen(words.drop(prefix.size))
        }
        // A bare screen name ("hồ sơ") opens it.
        screenFor(words)?.let { if (it.matchedWholeUtterance) return VoiceCommand.Open(it.screen) }
        return VoiceCommand.Unknown(heard.trim())
    }

    /**
     * Reads every alternative the recogniser offered, best first, and keeps the first one that is
     * understood and, for "gọi"/"nhắn", names someone in the list. Falls back to the first
     * understood one, then to the top alternative as [VoiceCommand.Unknown].
     */
    fun parseBest(alternatives: List<String>, contacts: List<VoiceContact>): VoiceCommand {
        val parsed = alternatives.filter { it.isNotBlank() }.map { parse(it, contacts) }
        if (parsed.isEmpty()) return VoiceCommand.Unknown("")
        return parsed.firstOrNull { isResolved(it) }
            ?: parsed.firstOrNull { it !is VoiceCommand.Unknown }
            ?: parsed.first()
    }

    private fun isResolved(command: VoiceCommand): Boolean = when (command) {
        is VoiceCommand.Call -> command.match is ContactMatch.Found
        is VoiceCommand.Message -> command.match is ContactMatch.Found && command.text.isNotBlank()
        is VoiceCommand.OpenUnknown, is VoiceCommand.Unknown -> false
        else -> true
    }

    /** "có" / "không" after a question. null when it is neither (or both). */
    fun parseConfirmation(heard: String): Boolean? {
        val words = Words.of(heard)
        if (words.isEmpty) return null
        val folded = words.folded
        val yes = folded.any { it in yesWords } || words.containsPhrase(VoiceText.phrase("dong y")) ||
            words.containsPhrase(VoiceText.phrase("chac chan")) || words.containsPhrase(VoiceText.phrase("xac nhan"))
        val no = folded.any { it in noWords } || words.containsPhrase(VoiceText.phrase("thoi khong")) ||
            words.containsPhrase(VoiceText.phrase("khong can"))
        return when {
            yes && !no -> true
            no && !yes -> false
            // "có không" is the question, not an answer; "không có" is a no.
            yes && no -> if (folded.first() == "khong") false else null
            else -> null
        }
    }

    private val yesWords = setOf("co", "vang", "u", "ok", "oke", "okay", "duoc", "roi", "goi", "gui", "yes")
    private val noWords = setOf("khong", "huy", "thoi", "khoi", "sai", "no")

    // ---- internals ----

    private fun stripFiller(input: Words): Words {
        var words = input
        var changed = true
        while (changed && !words.isEmpty) {
            changed = false
            leadFiller.firstOrNull { words.size > it.size && words.startsWith(it) }?.let {
                words = words.drop(it.size); changed = true
            }
            val tail = trailFiller.firstOrNull { filler ->
                words.size > filler.size && words.folded.takeLast(filler.size) == filler
            }
            if (tail != null) {
                words = words.dropLast(tail.size); changed = true
            }
        }
        return words
    }

    private fun isHelp(joined: String): Boolean =
        joined.startsWith("giup") || joined == "giup do" || joined.contains("tro giup") || joined.contains("lenh nao") ||
            joined.contains("lenh gi") || joined.contains("co the noi gi") || joined.contains("noi nhu the nao") ||
            joined.contains("noi gi")

    private fun isEmergencyWords(joined: String): Boolean =
        joined.contains("khan cap") || joined.contains("cap cuu") || joined == "sos" || joined.split(' ').contains("sos") ||
            joined == "cuu toi" || joined == "cuu minh" || joined == "cuu"

    private fun mentionsGlasses(words: Words): Boolean {
        val f = words.folded
        val hasPin = "pin" in f
        val hasKinh = "kinh" in f
        if (hasPin && (hasKinh || f.size <= 4)) return true
        if (!hasKinh) return false
        val ask = listOf("the nao", "ket noi", "tinh trang", "sao", "con", "hoat dong", "online", "khong", "bao nhieu")
        return ask.any { words.containsPhrase(VoiceText.phrase(it)) }
    }

    private fun parseOpen(rest: Words): VoiceCommand {
        if (rest.isEmpty) return VoiceCommand.OpenUnknown("")
        // "trang chủ" is a screen name although "trang" is also a filler word: try the words as said first.
        screenFor(rest)?.let { return VoiceCommand.Open(it.screen) }
        val kept = rest.folded.indices.filter { rest.folded[it] !in openNoise }
        val cleaned = Words.of(kept.joinToString(" ") { rest.original[it] })
        val hit = screenFor(cleaned) ?: return VoiceCommand.OpenUnknown(rest.originalText())
        return VoiceCommand.Open(hit.screen)
    }

    private class ScreenHit(val screen: VoiceScreen, val matchedWholeUtterance: Boolean)

    private fun screenFor(words: Words): ScreenHit? {
        var best: ScreenHit? = null
        var bestLen = 0
        for (screen in VoiceScreen.entries) {
            for (keyword in screen.keywords) {
                val phrase = VoiceText.phrase(keyword)
                if (words.containsPhrase(phrase) && phrase.size > bestLen) {
                    bestLen = phrase.size
                    best = ScreenHit(screen, words.size == phrase.size || words.size <= phrase.size + 1)
                }
            }
        }
        return best
    }

    private fun parseMessage(rest: Words, contacts: List<VoiceContact>): VoiceCommand {
        if (rest.isEmpty) return VoiceCommand.Message("", ContactMatch.NotFound, "")
        // Longest name first would swallow the body, so score every possible name length.
        var bestScore = 0
        var bestLen = 0
        var bestMatch: ContactMatch = ContactMatch.NotFound
        for (len in 1..minOf(4, rest.size)) {
            val spoken = rest.take(len)
            val (score, match) = scoreContacts(spoken.joined(), contacts, boundary = true)
            if (score > bestScore || (score == bestScore && score > 0 && len > bestLen)) {
                bestScore = score; bestLen = len; bestMatch = match
            }
        }
        if (bestScore == 0) {
            return VoiceCommand.Message(rest.take(1).originalText(), ContactMatch.NotFound, stripConnectors(rest.drop(1)).originalText())
        }
        val spoken = rest.take(bestLen).originalText()
        return VoiceCommand.Message(spoken, bestMatch, stripConnectors(rest.drop(bestLen)).originalText())
    }

    private fun stripConnectors(input: Words): Words {
        var words = input
        var changed = true
        while (changed && !words.isEmpty) {
            changed = false
            messageConnectors.firstOrNull { words.startsWith(it) }?.let { words = words.drop(it.size); changed = true }
        }
        return words
    }

    /** Public for tests and for the dialogue (e.g. re-resolving a name). */
    fun matchContacts(spoken: String, contacts: List<VoiceContact>): ContactMatch =
        scoreContacts(VoiceText.fold(spoken), contacts, boundary = false).second

    /**
     * Best contact(s) for the folded [spoken] text and the score reached (0 = none).
     * Scores: 4 same name, 3 same after dropping "anh/chị/cô...", 2 one contains the other
     * ([boundary] = false only), 1 within one or two typos.
     */
    private fun scoreContacts(spokenFolded: String, contacts: List<VoiceContact>, boundary: Boolean): Pair<Int, ContactMatch> {
        val scored = contacts.map { contact -> contact to (contact.allNames.maxOfOrNull { scoreName(spokenFolded, it, boundary) } ?: 0) }
        val best = scored.maxOfOrNull { it.second } ?: 0
        if (best == 0) return 0 to ContactMatch.NotFound
        val top = scored.filter { it.second == best }.map { it.first }.distinctBy { it.id }
        return best to if (top.size == 1) ContactMatch.Found(top.first()) else ContactMatch.Ambiguous(top)
    }

    private fun scoreName(spokenFolded: String, name: String, boundary: Boolean): Int {
        val target = VoiceText.fold(name)
        if (target.isEmpty() || spokenFolded.isEmpty()) return 0
        if (spokenFolded == target) return 4
        val spokenCore = dropHonorific(spokenFolded)
        val targetCore = dropHonorific(target)
        if (spokenCore.isNotEmpty() && spokenCore == targetCore) return 3
        if (!boundary) {
            val s = spokenFolded.split(' ')
            val t = target.split(' ')
            val contained = (containsRun(s, t) && t.any { it.length >= 2 }) || (containsRun(t, s) && s.any { it.length >= 2 })
            if (contained) return 2
        }
        val limit = when {
            target.length >= 8 -> 2
            target.length >= 4 -> 1
            else -> 0
        }
        if (limit > 0 && VoiceText.editDistance(spokenCore.ifEmpty { spokenFolded }, targetCore.ifEmpty { target }) <= limit) return 1
        return 0
    }

    private fun dropHonorific(folded: String): String {
        val parts = folded.split(' ')
        return if (parts.size > 1 && parts.first() in honorifics) parts.drop(1).joinToString(" ") else folded
    }

    private fun containsRun(haystack: List<String>, needle: List<String>): Boolean {
        if (needle.isEmpty() || needle.size > haystack.size) return false
        for (i in 0..haystack.size - needle.size) {
            if (haystack.subList(i, i + needle.size) == needle) return true
        }
        return false
    }
}
