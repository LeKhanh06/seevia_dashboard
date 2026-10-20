package com.youreyes.app.ui.navigation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RouteFilterTest {

    @Test
    fun accepts_the_routes_notifications_use() {
        assertEquals("auth", RouteFilter.accept("auth"))
        assertEquals("care", RouteFilter.accept("care"))
        assertEquals("chat/user-A1B2C3", RouteFilter.accept("chat/user-A1B2C3"))
        assertEquals("chat/user_9", RouteFilter.accept("chat/user_9"))
    }

    @Test
    fun accepts_the_chat_route_the_app_itself_builds() {
        val built = Routes.chatRoute("user-A1B2C3", "Cô Sáu & Mẹ/1")
        assertEquals(built, RouteFilter.accept(built))
    }

    @Test
    fun drops_everything_else() {
        listOf(
            null, "", "home", "dev-test", "glasses", "chat", "chat/", "chat//x", "chat/a/b",
            "chat/has space", "chat/../auth", "chat/x?y=1", "chat/${"a".repeat(65)}",
            "chat/id?name=<script>", "auth?x=1", "AUTH", "https://evil", " auth",
        ).forEach { assertNull("should drop: $it", RouteFilter.accept(it)) }
    }

    @Test
    fun accepts_an_id_of_exactly_64_characters() {
        val id = "a".repeat(64)
        assertEquals("chat/$id", RouteFilter.accept("chat/$id"))
    }
}
