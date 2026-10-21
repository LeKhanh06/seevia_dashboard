package com.youreyes.app.voice

import java.text.Normalizer
import java.util.Locale

/**
 * Text helpers for matching what the speech recogniser heard against names and keywords.
 * Pure Kotlin (no Android classes) so every rule is covered by a JVM test.
 *
 * The recogniser may or may not return Vietnamese tone marks ("mẹ" / "me"), so every comparison
 * is made on the *folded* form: lower case, no diacritics, "đ" as "d", punctuation as spaces.
 */
object VoiceText {

    /** "Mẹ Hoa!" -> "me hoa". */
    fun fold(text: String): String {
        val decomposed = Normalizer.normalize(text.lowercase(Locale.ROOT), Normalizer.Form.NFD)
        val out = StringBuilder(decomposed.length)
        var lastWasSpace = true
        for (ch in decomposed) {
            val type = Character.getType(ch)
            val mapped: Char? = when {
                type == Character.NON_SPACING_MARK.toInt() -> null
                ch == 'đ' -> 'd'
                ch.isLetterOrDigit() -> ch
                else -> ' '
            }
            if (mapped == null) continue
            if (mapped == ' ') {
                if (!lastWasSpace) out.append(' ')
                lastWasSpace = true
            } else {
                out.append(mapped)
                lastWasSpace = false
            }
        }
        return out.toString().trim()
    }

    /** A spoken sentence as parallel lists: the words as heard, and their folded form. */
    class Words private constructor(val original: List<String>, val folded: List<String>) {
        val size: Int get() = folded.size
        val isEmpty: Boolean get() = folded.isEmpty()

        fun joined(): String = folded.joinToString(" ")

        fun startsWith(phrase: List<String>): Boolean =
            phrase.isNotEmpty() && folded.size >= phrase.size && folded.subList(0, phrase.size) == phrase

        fun drop(count: Int): Words = Words(original.drop(count), folded.drop(count))
        fun dropLast(count: Int): Words = Words(original.dropLast(count), folded.dropLast(count))
        fun take(count: Int): Words = Words(original.take(count), folded.take(count))

        /** The words as originally heard (tone marks kept), for text that is passed on, e.g. a message body. */
        fun originalText(): String = original.joinToString(" ").trim(' ', ',', '.', ':', ';', '!', '?', '"', '“', '”')

        /** True when [phrase] occurs as consecutive words. */
        fun containsPhrase(phrase: List<String>): Boolean = indexOfPhrase(phrase) >= 0

        fun indexOfPhrase(phrase: List<String>): Int {
            if (phrase.isEmpty() || phrase.size > folded.size) return -1
            for (i in 0..folded.size - phrase.size) {
                if (folded.subList(i, i + phrase.size) == phrase) return i
            }
            return -1
        }

        companion object {
            fun of(heard: String): Words {
                val originals = ArrayList<String>()
                val folds = ArrayList<String>()
                for (token in heard.trim().split(Regex("\\s+"))) {
                    val folded = fold(token).replace(" ", "")
                    if (folded.isEmpty()) continue
                    originals += token
                    folds += folded
                }
                return Words(originals, folds)
            }
        }
    }

    /** Folded words of [phrase] ("Mẹ Hoa" -> ["me", "hoa"]). */
    fun phrase(text: String): List<String> = fold(text).split(' ').filter { it.isNotEmpty() }

    /** Edit distance, capped by the caller's use: small inputs only (names, keywords). */
    fun editDistance(a: String, b: String): Int {
        if (a == b) return 0
        if (a.isEmpty()) return b.length
        if (b.isEmpty()) return a.length
        var prev = IntArray(b.length + 1) { it }
        var cur = IntArray(b.length + 1)
        for (i in 1..a.length) {
            cur[0] = i
            for (j in 1..b.length) {
                val cost = if (a[i - 1] == b[j - 1]) 0 else 1
                cur[j] = minOf(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
            }
            val tmp = prev; prev = cur; cur = tmp
        }
        return prev[b.length]
    }
}
