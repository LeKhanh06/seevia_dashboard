package com.youreyes.app.core

/**
 * Which screens are shown. A screen is switched off here when nothing on the server backs it:
 * showing it would put a dead or pretend feature in front of the person (or a reviewer).
 * The code stays; flip a flag to bring the screen back once its endpoint exists.
 *
 * Audit (2026-10-21), screen by screen, against the server's public routes:
 *
 * | Screen        | Backed by                                   | Shown |
 * |---------------|---------------------------------------------|-------|
 * | Album         | local MediaStore only; no server action saves photos | no  |
 * | Cộng đồng     | static text, no endpoint                    | no    |
 * | Biên bản họp  | canned transcript on a timer, no endpoint   | no    |
 * | Dịch thuật    | canned translation, no endpoint             | no    |
 * | everything else (calls, care, chat, glasses, safety, profile, ...) | real | yes |
 */
object FeatureFlags {
    @JvmField val ALBUM: Boolean = false
    @JvmField val COMMUNITY: Boolean = false
    @JvmField val MEETING: Boolean = false
    @JvmField val TRANSLATION: Boolean = false
}
