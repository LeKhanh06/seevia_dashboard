package com.youreyes.app.ui.overview

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OverviewUiStateTest {
    @Test
    fun `setup is not ready by default`() {
        assertFalse(OverviewUiState().isReady)
    }

    @Test
    fun `setup is ready only after server and device are configured`() {
        assertFalse(OverviewUiState(serverUrl = "http://10.0.2.2:8000", isRegistered = false).isReady)
        assertFalse(OverviewUiState(serverUrl = "", isRegistered = true).isReady)

        val state = OverviewUiState(
            serverUrl = "http://10.0.2.2:8000",
            isRegistered = true,
        )

        assertTrue(state.isReady)
    }

    private val provisioned = OverviewUiState(
        serverUrl = "https://app.example.test",
        bearerToken = "device-token",
        userId = "user-100",
        deviceId = "device-100",
    )

    @Test
    fun `an unregistered phone with a full build configuration registers by itself`() {
        assertTrue(provisioned.shouldAutoRegister)
    }

    @Test
    fun `a registered phone does not register again on every launch`() {
        assertFalse(provisioned.copy(isRegistered = true).shouldAutoRegister)
    }

    @Test
    fun `a registration already in flight is not started twice`() {
        assertFalse(provisioned.copy(isLoading = true).shouldAutoRegister)
    }

    @Test
    fun `a build without a token leaves registration to the person`() {
        assertFalse(provisioned.copy(bearerToken = "").shouldAutoRegister)
    }

    @Test
    fun `missing server or ids never trigger a blind registration`() {
        assertFalse(provisioned.copy(serverUrl = " ").shouldAutoRegister)
        assertFalse(provisioned.copy(userId = "").shouldAutoRegister)
        assertFalse(provisioned.copy(deviceId = "").shouldAutoRegister)
    }

    @Test
    fun `glasses are linked by themselves only when a demo id is configured and none is paired`() {
        assertTrue(OverviewUiState.shouldAutoLinkGlasses(configuredId = "01", pairedId = ""))
        assertFalse(OverviewUiState.shouldAutoLinkGlasses(configuredId = "", pairedId = ""))
        assertFalse(OverviewUiState.shouldAutoLinkGlasses(configuredId = "  ", pairedId = ""))
        assertFalse(OverviewUiState.shouldAutoLinkGlasses(configuredId = "01", pairedId = "glasses-02"))
    }
}
