package com.youreyes.app.command

enum class Lane { FAST, SLOW }

object CommandLanes {
    private val FAST_ACTIONS = setOf(
        "emergency_call", "call_answer", "call_reject", "location_get", "capabilities_get",
    )

    fun laneFor(action: String): Lane = if (action in FAST_ACTIONS) Lane.FAST else Lane.SLOW
}
