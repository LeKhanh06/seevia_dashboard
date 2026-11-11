package com.youreyes.app.ui.navigation

/**
 * Which screens an intent extra may open. `MainActivity` is exported, so any app can send it a
 * route string; only the ones notifications really use get through, everything else is ignored.
 */
object RouteFilter {
    private val chat = Regex("^chat/([A-Za-z0-9_-]{1,64})(\\?name=[A-Za-z0-9%._~-]{0,300}(&toRelative=true)?)?$")

    /** The route if allowed (`auth`, `care`, `chat/<id>[?name=<encoded>[&toRelative=true]]`), else null. */
    fun accept(route: String?): String? = when {
        route == null -> null
        route == Routes.AUTH || route == Routes.CARE -> route
        chat.matches(route) -> route
        else -> null
    }
}
