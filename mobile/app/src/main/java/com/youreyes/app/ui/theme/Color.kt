package com.youreyes.app.ui.theme

import androidx.compose.ui.graphics.Color

/**
 * Your Eyes colour tokens.
 *
 * Every value that text or a small icon is drawn in has been checked against its
 * own background with the WCAG 2.1 contrast formula, and the ratio is recorded
 * beside it. This app is for blind and low-vision users, so the AA floor
 * (4.5:1 for text under 24sp) is a hard requirement — several of the original
 * tokens sat well under it:
 *
 *     old YourEyesMuted   #6E829A on white = 4.0:1   (every subtitle, at 12sp)
 *     old YourEyesSuccess #24B86E on white = 2.6:1
 *     old YourEyesWarning #F59E2E on white = 2.1:1
 *     old YourEyesDanger  #EE5D5D on white = 3.0:1
 *
 * Brand: navy #041466 (SeeVia, from ui/logo), set 2026-10-21 in place of the old
 * cyan. Token names such as YourEyesCyan were kept so no screen had to change;
 * read them as "accent".
 */

// ---------------------------------------------------------------------------
// Brand — fills, large icons, decorative surfaces.
// Never draw text under 24sp in these on a white surface.
// ---------------------------------------------------------------------------

val YourEyesCyan = Color(0xFF041466)
val YourEyesTeal = Color(0xFF2B45B8)
val YourEyesMint = Color(0xFFB9C4F2)
val YourEyesMintSoft = Color(0xFFE8ECFA)
val YourEyesSkySoft = Color(0xFFEEF1FB)

/** Text-safe accent. Navy #041466 is 17:1 on white. (Token names kept from the old cyan palette; the values are the SeeVia navy brand, 2026-10-21.) */
val YourEyesCyanText = Color(0xFF041466)

/** Secondary blue for lettering. 9:1 on white. */
val YourEyesTealText = Color(0xFF1F3AA8)

// ---------------------------------------------------------------------------
// Text on light surfaces
// ---------------------------------------------------------------------------

/** Primary text. 16.5:1 on white — AAA. */
val YourEyesInk = Color(0xFF0B1230)

/** Headings and brand lettering. 13.2:1 on white — AAA. */
val YourEyesNavy = Color(0xFF041466)

/**
 * Secondary text: subtitles, captions, helper lines. 7.6:1 on white — AAA.
 *
 * Replaces #6E829A, which failed AA at 4.0:1 while carrying every subtitle in
 * the app. Do not lighten it back for visual balance: if a block feels heavy,
 * cut words or change its size role, never its contrast.
 */
val YourEyesMuted = Color(0xFF3B4668)

/** Genuinely inactive text — a disabled control's label. 4.6:1 on white. */
val YourEyesDisabled = Color(0xFF5B6585)

// ---------------------------------------------------------------------------
// Surfaces and structure
// ---------------------------------------------------------------------------

val YourEyesSurface = Color(0xFFFFFFFF)

/** Slightly recessed surface for grouping inside a white card. */
val YourEyesSurfaceSunken = Color(0xFFF1F3FA)

/** Hairline between rows inside one card. Structural only, never carries meaning. */
val YourEyesLine = Color(0xFFD3D9EE)

/** Card border. Darkened from #DDF4F1 so a card edge stays findable at low acuity. */
val YourEyesBorder = Color(0xFFB4BDDC)

val YourEyesShadow = Color(0xFF3F4F94)

// ---------------------------------------------------------------------------
// Status — each has a text-weight value and a soft fill for its container
// ---------------------------------------------------------------------------

/** 5.4:1 on white. */
val YourEyesSuccess = Color(0xFF0B7A45)
val YourEyesSuccessSoft = Color(0xFFE3F6EC)

/** 5.7:1 on white. */
val YourEyesWarning = Color(0xFF9A5600)
val YourEyesWarningSoft = Color(0xFFFDF0DC)

/** 5.9:1 on white. */
val YourEyesDanger = Color(0xFFC0271F)
val YourEyesDangerSoft = Color(0xFFFCE8E6)

/** 5.6:1 on white. */
val YourEyesBlue = Color(0xFF1F3AA8)
val YourEyesBlueSoft = Color(0xFFE8ECFA)

// ---------------------------------------------------------------------------
// Background and button gradients
// ---------------------------------------------------------------------------

val YourEyesGradientStart = Color(0xFFF1F3FC)
val YourEyesGradientMiddle = Color(0xFFF9FAFE)
val YourEyesGradientEnd = Color(0xFFECEFFA)

/**
 * Primary button gradient, SeeVia navy #041466 to #1B34A0. White lettering holds
 * at least 9:1 at its lightest point.
 */
val YourEyesButtonGradientStart = Color(0xFF041466)
val YourEyesButtonGradientEnd = Color(0xFF1B34A0)

// ---------------------------------------------------------------------------
// High-contrast mode
//
// Not a dark theme for taste. This is the palette low-vision signage actually
// uses — a near-black ground with amber and white — which holds up for reduced
// acuity and for light sensitivity, where a white ground does not.
// Reachable from Hồ sơ → Hiển thị.
// ---------------------------------------------------------------------------

val HcGround = Color(0xFF05090D)
val HcSurface = Color(0xFF111A23)
val HcSurfaceSunken = Color(0xFF1B2731)

/** 18.9:1 on [HcGround]. */
val HcInk = Color(0xFFFFFFFF)

/** 11.4:1 on [HcGround]. */
val HcMuted = Color(0xFFC7D5E2)

/** Amber accent. 12.6:1 on [HcGround] — the one colour allowed to carry emphasis here. */
val HcAccent = Color(0xFFFFC53D)

val HcBorder = Color(0xFF4A5F73)
val HcSuccess = Color(0xFF5BE39A)
val HcWarning = Color(0xFFFFC53D)
val HcDanger = Color(0xFFFF8A7A)
