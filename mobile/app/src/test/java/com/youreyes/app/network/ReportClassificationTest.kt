package com.youreyes.app.network

import com.youreyes.app.command.ReportOutcome
import org.junit.Assert.assertEquals
import org.junit.Test

class ReportClassificationTest {
    @Test
    fun `permanent client report errors are rejected`() {
        listOf(400, 404, 409, 422).forEach { status ->
            assertEquals(ReportOutcome.REJECTED, classifyReportStatus(status))
        }
    }

    @Test
    fun `other report errors are retried`() {
        listOf(401, 429, 500, 503).forEach { status ->
            assertEquals(ReportOutcome.RETRY, classifyReportStatus(status))
        }
    }
}
