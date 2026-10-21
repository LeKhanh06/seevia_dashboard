package com.youreyes.app.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceParserTest {

    private val me = VoiceContact("u-me", "Mẹ", listOf("má", "mẹ ơi"))
    private val nam = VoiceContact("u-nam", "Anh Nam", listOf("Nam"))
    private val hoa = VoiceContact("u-hoa", "Cô Hoa", listOf("Hoa"))
    private val hoaNhi = VoiceContact("u-hn", "Hoa Nhi")
    private val di = VoiceContact("u-di", "Dì")
    private val contacts = listOf(me, nam, hoa, di)

    private fun parse(heard: String, list: List<VoiceContact> = contacts) = VoiceParser.parse(heard, list)

    private fun foundId(match: ContactMatch): String? = (match as? ContactMatch.Found)?.contact?.id

    // ---- folding ----

    @Test
    fun fold_removes_tone_marks_and_maps_d_stroke() {
        assertEquals("me hoa", VoiceText.fold("Mẹ Hoa!"))
        assertEquals("dung dua", VoiceText.fold("Đúng ĐỪA"))
        assertEquals("a b", VoiceText.fold("a,   b"))
        assertEquals("", VoiceText.fold("  ...  "))
    }

    // ---- calls ----

    @Test
    fun call_by_name_with_and_without_tone_marks() {
        listOf("gọi mẹ", "goi me", "Gọi cho mẹ", "gọi điện cho mẹ", "GỌI MẸ", "làm ơn gọi mẹ nhé").forEach {
            val c = parse(it) as VoiceCommand.Call
            assertEquals("$it", "u-me", foundId(c.match))
        }
    }

    @Test
    fun call_uses_aliases() {
        assertEquals("u-me", foundId((parse("gọi má") as VoiceCommand.Call).match))
        assertEquals("u-nam", foundId((parse("gọi Nam") as VoiceCommand.Call).match))
    }

    @Test
    fun call_ignores_honorific_before_the_name() {
        assertEquals("u-nam", foundId((parse("gọi anh Nam") as VoiceCommand.Call).match))
        assertEquals("u-hoa", foundId((parse("gọi cô hoa") as VoiceCommand.Call).match))
        // contact saved as "Hoa" only, user says "cô Hoa"
        val onlyHoa = listOf(VoiceContact("x", "Hoa"))
        assertEquals("x", foundId((parse("gọi cô Hoa", onlyHoa) as VoiceCommand.Call).match))
    }

    @Test
    fun call_tolerates_a_small_typo_in_a_long_name() {
        val c = listOf(VoiceContact("l", "Nguyễn Thị Lan"))
        assertEquals("l", foundId((parse("gọi nguyen thi lam", c) as VoiceCommand.Call).match))
    }

    @Test
    fun call_does_not_guess_a_short_name() {
        // "mẹ" and "me" are 2 letters: one typo must not turn "mo" into "mẹ".
        assertEquals(ContactMatch.NotFound, (parse("gọi mo") as VoiceCommand.Call).match)
    }

    @Test
    fun call_with_the_name_inside_a_longer_phrase() {
        assertEquals("u-me", foundId((parse("gọi mẹ ơi") as VoiceCommand.Call).match))
        val noAunt = listOf(me, nam)
        assertEquals("u-me", foundId((parse("gọi mẹ đi", noAunt) as VoiceCommand.Call).match))
    }

    @Test
    fun call_with_a_word_that_is_also_another_persons_name_is_ambiguous() {
        // "gọi mẹ đi" with a contact called "Dì": both fit, the dialogue must ask rather than guess.
        assertTrue((parse("gọi mẹ đi") as VoiceCommand.Call).match is ContactMatch.Ambiguous)
    }

    @Test
    fun call_unknown_name_is_reported_with_what_was_heard() {
        val c = parse("gọi ông Tư") as VoiceCommand.Call
        assertEquals(ContactMatch.NotFound, c.match)
        assertEquals("ông Tư", c.spoken)
    }

    @Test
    fun call_without_a_name() {
        val c = parse("gọi") as VoiceCommand.Call
        assertEquals("", c.spoken)
        assertEquals(ContactMatch.NotFound, c.match)
    }

    @Test
    fun call_two_people_with_the_same_name_is_ambiguous() {
        val twin = listOf(VoiceContact("a", "Hoa"), VoiceContact("b", "Hoa"))
        val c = parse("gọi hoa", twin) as VoiceCommand.Call
        assertTrue(c.match is ContactMatch.Ambiguous)
        assertEquals(2, (c.match as ContactMatch.Ambiguous).contacts.size)
    }

    @Test
    fun call_prefers_the_exact_name_over_a_longer_one() {
        val list = listOf(VoiceContact("a", "Hoa"), hoaNhi)
        assertEquals("a", foundId((parse("gọi hoa", list) as VoiceCommand.Call).match))
        assertEquals("u-hn", foundId((parse("gọi hoa nhi", list) as VoiceCommand.Call).match))
    }

    @Test
    fun a_name_that_is_also_a_filler_word_still_works() {
        // "Dì" folds to "di", which must not be swallowed as filler.
        assertEquals("u-di", foundId((parse("gọi dì") as VoiceCommand.Call).match))
    }

    // ---- messages ----

    @Test
    fun message_splits_name_and_body() {
        val m = parse("nhắn mẹ tối nay con về muộn") as VoiceCommand.Message
        assertEquals("u-me", foundId(m.match))
        assertEquals("tối nay con về muộn", m.text)
    }

    @Test
    fun message_keeps_tone_marks_of_the_body_even_when_the_name_has_none() {
        val m = parse("nhan tin cho me toi nay con ve muon") as VoiceCommand.Message
        assertEquals("u-me", foundId(m.match))
        assertEquals("toi nay con ve muon", m.text)
        val m2 = parse("Nhắn tin cho Mẹ: Con đã về tới nhà") as VoiceCommand.Message
        assertEquals("Con đã về tới nhà", m2.text)
    }

    @Test
    fun message_drops_connector_words() {
        listOf("nhắn mẹ là con đã ăn cơm", "nhắn cho mẹ rằng con đã ăn cơm", "gửi tin nhắn cho mẹ nội dung con đã ăn cơm").forEach {
            val m = parse(it) as VoiceCommand.Message
            assertEquals(it, "con đã ăn cơm", m.text)
            assertEquals(it, "u-me", foundId(m.match))
        }
    }

    @Test
    fun message_with_a_two_word_name() {
        val list = listOf(me, hoaNhi)
        val m = parse("nhắn hoa nhi mai đi học sớm", list) as VoiceCommand.Message
        assertEquals("u-hn", foundId(m.match))
        assertEquals("mai đi học sớm", m.text)
    }

    @Test
    fun message_with_honorific() {
        val m = parse("nhắn anh Nam đang ở đâu", contacts) as VoiceCommand.Message
        assertEquals("u-nam", foundId(m.match))
        assertEquals("đang ở đâu", m.text)
    }

    @Test
    fun message_name_only_has_empty_body() {
        val m = parse("nhắn mẹ") as VoiceCommand.Message
        assertEquals("u-me", foundId(m.match))
        assertEquals("", m.text)
    }

    @Test
    fun message_to_unknown_name() {
        val m = parse("nhắn ông Tư mai tôi qua") as VoiceCommand.Message
        assertEquals(ContactMatch.NotFound, m.match)
        assertTrue(m.spoken.isNotBlank())
    }

    @Test
    fun message_without_anything() {
        val m = parse("nhắn tin") as VoiceCommand.Message
        assertEquals("", m.spoken)
    }

    @Test
    fun heard_receive_messages_is_not_send_a_message() {
        assertEquals(VoiceCommand.ReadMessages, parse("nhận tin nhắn"))
    }

    // ---- other commands ----

    @Test
    fun read_messages_variants() {
        listOf("đọc tin nhắn", "doc tin nhan", "đọc tin nhắn mới", "có tin nhắn mới không", "đọc tin").forEach {
            assertEquals(it, VoiceCommand.ReadMessages, parse(it))
        }
    }

    @Test
    fun glasses_status_variants() {
        listOf("kính thế nào", "kinh the nao", "pin kính", "pin kính còn bao nhiêu", "kính có kết nối không", "kính còn pin không", "tình trạng kính", "pin").forEach {
            assertEquals(it, VoiceCommand.GlassesStatus, parse(it))
        }
    }

    @Test
    fun emergency_variants() {
        listOf("khẩn cấp", "khan cap", "gọi khẩn cấp", "gọi cấp cứu", "SOS", "cứu tôi", "tôi cần cấp cứu").forEach {
            assertEquals(it, VoiceCommand.Emergency, parse(it))
        }
    }

    @Test
    fun emergency_word_inside_a_message_is_just_text() {
        val m = parse("nhắn mẹ con đang khẩn cấp") as VoiceCommand.Message
        assertEquals("con đang khẩn cấp", m.text)
    }

    @Test
    fun open_screens() {
        assertEquals(VoiceCommand.Open(VoiceScreen.SAFETY), parse("mở an toàn"))
        assertEquals(VoiceCommand.Open(VoiceScreen.PROFILE), parse("mở hồ sơ"))
        assertEquals(VoiceCommand.Open(VoiceScreen.HOME), parse("mở trang chủ"))
        assertEquals(VoiceCommand.Open(VoiceScreen.CARE), parse("mở màn hình người thân"))
        assertEquals(VoiceCommand.Open(VoiceScreen.GLASSES), parse("mở kính"))
        assertEquals(VoiceCommand.Open(VoiceScreen.DISPLAY), parse("vào hiển thị"))
        assertEquals(VoiceCommand.Open(VoiceScreen.ACTIVITY_LOG), parse("mo lich su hoat dong"))
        assertEquals(VoiceCommand.Open(VoiceScreen.GUIDE), parse("mở hướng dẫn"))
        assertEquals(VoiceCommand.Open(VoiceScreen.SUPPORT), parse("mở hỗ trợ"))
        assertEquals(VoiceCommand.Open(VoiceScreen.READINESS), parse("mở kiểm tra sẵn sàng"))
        assertEquals(VoiceCommand.Open(VoiceScreen.HOME), parse("trang chủ"))
    }

    @Test
    fun open_unknown_screen() {
        val c = parse("mở ngân hàng") as VoiceCommand.OpenUnknown
        assertEquals("ngân hàng", c.spoken)
        assertEquals(VoiceCommand.OpenUnknown(""), parse("mở"))
    }

    @Test
    fun hidden_screens_cannot_be_opened_by_voice() {
        assertTrue(parse("mở album") is VoiceCommand.OpenUnknown)
        assertTrue(parse("mở dịch thuật") is VoiceCommand.OpenUnknown)
        assertTrue(parse("mở cộng đồng") is VoiceCommand.OpenUnknown)
    }

    @Test
    fun help_and_stop() {
        listOf("trợ giúp", "tro giup", "giúp", "bạn làm được gì? có những lệnh nào", "tôi nói gì").forEach {
            assertEquals(it, VoiceCommand.Help, parse(it))
        }
        listOf("dừng", "dừng lại", "thôi", "huỷ", "im lặng").forEach {
            assertEquals(it, VoiceCommand.Stop, parse(it))
        }
    }

    @Test
    fun nonsense_and_empty_are_unknown_and_keep_what_was_heard() {
        assertEquals(VoiceCommand.Unknown("hôm nay trời đẹp quá"), parse("hôm nay trời đẹp quá"))
        assertEquals(VoiceCommand.Unknown(""), parse(""))
        assertEquals(VoiceCommand.Unknown(""), parse("   ...  "))
    }

    @Test
    fun commands_work_with_no_contacts() {
        assertEquals(VoiceCommand.ReadMessages, parse("đọc tin nhắn", emptyList()))
        val c = parse("gọi mẹ", emptyList()) as VoiceCommand.Call
        assertEquals(ContactMatch.NotFound, c.match)
    }

    // ---- alternatives ----

    @Test
    fun best_alternative_is_the_one_that_names_a_known_person() {
        val command = VoiceParser.parseBest(listOf("gọi mơ", "gọi mẹ", "gọi me"), contacts)
        assertEquals("u-me", foundId((command as VoiceCommand.Call).match))
    }

    @Test
    fun best_alternative_falls_back_to_the_first_understood_one() {
        val command = VoiceParser.parseBest(listOf("blah blah", "gọi ông Tư"), contacts)
        assertTrue(command is VoiceCommand.Call)
        assertEquals(VoiceCommand.Unknown("blah blah"), VoiceParser.parseBest(listOf("blah blah", "xin chào"), contacts))
        assertEquals(VoiceCommand.Unknown(""), VoiceParser.parseBest(emptyList(), contacts))
    }

    // ---- yes / no ----

    @Test
    fun confirmation_yes() {
        listOf("có", "Có ạ", "vâng", "đồng ý", "được", "ừ", "ok", "gọi đi", "gửi đi", "chắc chắn", "xác nhận").forEach {
            assertEquals(it, true, VoiceParser.parseConfirmation(it))
        }
    }

    @Test
    fun confirmation_no() {
        listOf("không", "không ạ", "huỷ", "thôi", "thôi không", "không cần", "không có").forEach {
            assertEquals(it, false, VoiceParser.parseConfirmation(it))
        }
    }

    @Test
    fun confirmation_unclear() {
        listOf("", "hôm nay", "có không", "mẹ").forEach {
            assertNull(it, VoiceParser.parseConfirmation(it))
        }
        assertFalse(VoiceParser.parseConfirmation("không") == true)
    }
}
