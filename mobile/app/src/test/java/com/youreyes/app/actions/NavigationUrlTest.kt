package com.youreyes.app.actions

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NavigationUrlTest {
    @Test
    fun `target prefers a nonblank address then requires coordinate pair`() {
        assertEquals("Bưu điện Thành phố Hồ Chí Minh", NavigationUrls.target(
            JSONObject("""{"address":"Bưu điện Thành phố Hồ Chí Minh","lat":1,"lng":2}"""),
        ))
        assertEquals("10.7799,106.6999", NavigationUrls.target(JSONObject("""{"lat":10.7799,"lng":106.6999}""")))
        assertNull(NavigationUrls.target(JSONObject("""{"lat":10.7}""")))
        assertNull(NavigationUrls.target(JSONObject("""{"address":"   "}""")))
    }

    @Test
    fun `web and app urls encode unicode destinations`() {
        val target = "Bưu điện Thành phố Hồ Chí Minh"
        assertEquals(
            "https://www.google.com/maps/dir/?api=1&destination=B%C6%B0u%20%C4%91i%E1%BB%87n%20Th%C3%A0nh%20ph%E1%BB%91%20H%E1%BB%93%20Ch%C3%AD%20Minh&travelmode=walking",
            NavigationUrls.webUrl(target),
        )
        assertEquals(
            "google.navigation:q=B%C6%B0u%20%C4%91i%E1%BB%87n%20Th%C3%A0nh%20ph%E1%BB%91%20H%E1%BB%93%20Ch%C3%AD%20Minh&mode=w",
            NavigationUrls.appUri(target),
        )
    }
}
