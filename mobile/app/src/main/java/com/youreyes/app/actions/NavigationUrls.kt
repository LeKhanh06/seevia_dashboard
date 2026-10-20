package com.youreyes.app.actions

import org.json.JSONObject
import java.net.URLEncoder

object NavigationUrls {
    fun target(dest: JSONObject?): String? {
        if (dest == null) return null
        val address = (dest.opt("address") as? String)?.takeIf { it.isNotBlank() }
        if (address != null) return address
        val lat = dest.opt("lat") as? Number ?: return null
        val lng = dest.opt("lng") as? Number ?: return null
        val latitude = lat.toDouble()
        val longitude = lng.toDouble()
        if (!latitude.isFinite() || !longitude.isFinite()) return null
        return "$latitude,$longitude"
    }

    fun encode(s: String): String = URLEncoder.encode(s, "UTF-8").replace("+", "%20")

    fun webUrl(target: String): String =
        "https://www.google.com/maps/dir/?api=1&destination=${encode(target)}&travelmode=walking"

    fun appUri(target: String): String = "google.navigation:q=${encode(target)}&mode=w"
}
