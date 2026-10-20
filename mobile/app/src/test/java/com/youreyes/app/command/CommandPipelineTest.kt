package com.youreyes.app.command

import com.youreyes.app.actions.ActionExecutionResult
import com.youreyes.app.actions.ActionHandler
import com.youreyes.app.actions.ActionRegistry
import com.youreyes.app.core.DeviceCredentials
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** In-memory [CommandLog] with the same duplicate rule as the SQLite store. */
private class FakeLog : CommandLog {
    val commands = linkedMapOf<String, CommandRecord>()
    val reports = linkedMapOf<String, PendingReportRecord>()

    override fun insertCommandIfNew(record: CommandRecord): Boolean =
        commands.putIfAbsent(record.requestId, record) == null

    override fun finishCommand(requestId: String, status: String, resultJson: String?, errorJson: String?) {
        commands[requestId] = commands.getValue(requestId).copy(status = status, resultJson = resultJson, errorJson = errorJson)
    }

    override fun savePendingReport(record: PendingReportRecord) {
        reports[record.requestId] = record
    }

    override fun updateReportStatus(requestId: String, status: String, attempts: Int) {
        reports[requestId] = reports.getValue(requestId).copy(status = status, attempts = attempts)
    }

    override fun unsentReports(): List<PendingReportRecord> =
        reports.values.filter { it.status == ReportStatus.PENDING || it.status == ReportStatus.FAILED }

    override fun reportStatus(requestId: String): String? = reports[requestId]?.status
}

private class FakeSender(var outcome: ReportOutcome = ReportOutcome.SENT) : ReportSender {
    val sent = mutableListOf<DeviceReportPayload>()
    override fun send(credentials: DeviceCredentials, payload: DeviceReportPayload): ReportOutcome {
        sent += payload
        return outcome
    }
}

class CommandPipelineTest {

    private val credentials = DeviceCredentials("http://server", "device-token")
    private val log = FakeLog()
    private val sender = FakeSender()
    private var handlerRuns = 0
    private var handlerParams = ""
    private var outcome: ActionExecutionResult = ActionExecutionResult.Success(mapOf("call_state" to "calling"))
    private val registry = ActionRegistry(
        mapOf("contact_call" to ActionHandler { _, params -> handlerRuns++; handlerParams = params; outcome })
    )
    private val dispatcher = CommandDispatcher(log, registry, sender, context = null, clock = { 1_600_000_000_000 })

    private fun command(requestId: String = "req-1", action: String = "contact_call") =
        PushCommand(requestId, "user-1", "phone-1", action, """{"name":"Mẹ"}""")

    @Test
    fun `push data needs every routing field`() {
        val data = mapOf(
            "request_id" to "req-1", "user_id" to "user-1", "device_id" to "phone-1",
            "action" to "contact_call", "params_json" to """{"name":"Mẹ"}""",
        )
        assertEquals(command(), PushCommand.fromData(data))
        assertEquals("{}", PushCommand.fromData(data - "params_json")?.paramsJson)
        for (key in listOf("request_id", "user_id", "device_id", "action")) {
            assertNull(key, PushCommand.fromData(data - key))
        }
    }

    @Test
    fun `fromData parses expiry variants and maps url`() {
        val base = mapOf("request_id" to "r", "user_id" to "u", "device_id" to "d", "action" to "navigation_start")
        val utc = PushCommand.fromData(base + mapOf(
            "expires_at" to "2026-10-19T08:01:00.000000+00:00",
            "maps_url" to " https://maps.example/route ",
        ))
        val local = PushCommand.fromData(base + ("expires_at" to "2026-10-19T15:01:00+07:00"))
        val invalid = PushCommand.fromData(base + ("expires_at" to "rác"))

        assertEquals(1_792_396_860_000L, utc?.expiresAtMillis)
        assertEquals(utc?.expiresAtMillis, local?.expiresAtMillis)
        assertEquals(" https://maps.example/route ", utc?.mapsUrl)
        assertNull(invalid?.expiresAtMillis)
        assertNull(PushCommand.fromData(base + ("maps_url" to ""))?.mapsUrl)
    }

    @Test
    fun `expired command is not executed and reports COMMAND_EXPIRED`() {
        val expiring = CommandDispatcher(log, registry, sender, null, clock = { 1_600_000_000_001 })
        val result = expiring.dispatch(command().copy(expiresAtMillis = 1_600_000_000_000), credentials)

        assertEquals(0, handlerRuns)
        assertEquals(ExecutionState.FAILED, (result as DispatchResult.Executed).executionState)
        assertEquals("COMMAND_EXPIRED", sender.sent.single().error?.code)
        assertEquals("Command expired before execution", sender.sent.single().error?.message)
        assertEquals("req-1", sender.sent.single().requestId)
        assertEquals(DispatchResult.DuplicateIgnored("req-1"), expiring.dispatch(command(), credentials))
    }

    @Test
    fun `command without expiry still runs`() {
        dispatcher.dispatch(command().copy(expiresAtMillis = null), credentials)

        assertEquals(1, handlerRuns)
    }

    @Test
    fun `maps url is passed to handler unless params already define it`() {
        dispatcher.dispatch(command().copy(mapsUrl = "https://maps.example/route"), credentials)
        assertEquals("https://maps.example/route", org.json.JSONObject(handlerParams).getString("maps_url"))

        dispatcher.dispatch(command("req-2").copy(
            paramsJson = """{"maps_url":"https://params.example/route"}""",
            mapsUrl = "https://maps.example/route",
        ), credentials)
        assertEquals("https://params.example/route", org.json.JSONObject(handlerParams).getString("maps_url"))
    }

    @Test
    fun `a command runs once and its report is sent immediately`() {
        val result = dispatcher.dispatch(command(), credentials)

        assertEquals(DispatchResult.Executed("req-1", ExecutionState.SUCCEEDED, reportSent = true), result)
        assertEquals(1, handlerRuns)
        val report = sender.sent.single()
        assertEquals("phone-1", report.deviceId)
        assertEquals("contact_call", report.action)
        assertEquals("calling", report.result?.get("call_state"))
        assertEquals("2020-09-13T12:26:40Z", report.timestamp)
        assertEquals(ReportStatus.SENT, log.reports.getValue("req-1").status)
    }

    @Test
    fun `a duplicate push never runs the handler again`() {
        dispatcher.dispatch(command(), credentials)

        val second = dispatcher.dispatch(command(), credentials)

        assertEquals(DispatchResult.DuplicateIgnored("req-1"), second)
        assertEquals(1, handlerRuns)
        assertEquals(1, sender.sent.size)
    }

    @Test
    fun `an unsent report is retried later without re-running the handler`() {
        sender.outcome = ReportOutcome.RETRY
        dispatcher.dispatch(command(), credentials)
        assertEquals(ReportStatus.FAILED, log.reports.getValue("req-1").status)

        sender.outcome = ReportOutcome.SENT
        val resent = ReportFlusher(log, sender).flush(credentials)

        assertEquals(1, resent)
        assertEquals(1, handlerRuns)
        assertEquals(2, sender.sent.size)
        assertEquals(sender.sent[0], sender.sent[1])
        assertEquals(ReportStatus.SENT, log.reports.getValue("req-1").status)
        assertEquals(0, ReportFlusher(log, sender).flush(credentials))
    }

    @Test
    fun `without credentials the report waits in the queue`() {
        val result = dispatcher.dispatch(command(), credentials = null)

        assertEquals(false, (result as DispatchResult.Executed).reportSent)
        assertTrue(sender.sent.isEmpty())
        assertEquals(ReportStatus.PENDING, log.reports.getValue("req-1").status)
    }

    @Test
    fun `failure details survive the retry path`() {
        val candidates = listOf(mapOf("name" to "Mẹ", "phone_number" to "***768"))
        outcome = ActionExecutionResult.Error(
            ReportErrorPayload("MULTIPLE_CONTACTS_FOUND", "Multiple contacts matched", mapOf("candidates" to candidates))
        )
        sender.outcome = ReportOutcome.RETRY
        dispatcher.dispatch(command(), credentials)
        sender.outcome = ReportOutcome.SENT

        ReportFlusher(log, sender).flush(credentials)

        val retried = sender.sent.last()
        assertEquals(ExecutionState.FAILED, retried.executionState)
        assertNull(retried.result)
        assertEquals("MULTIPLE_CONTACTS_FOUND", retried.error?.code)
        assertEquals(candidates, retried.error?.details?.get("candidates"))
    }

    @Test
    fun `unknown action and crashing handler are both reported as failures`() {
        dispatcher.dispatch(command("req-unknown", action = "camera_capture"), credentials)
        val crashing = CommandDispatcher(
            log, ActionRegistry(mapOf("contact_call" to ActionHandler { _, _ -> error("boom") })), sender, null,
        )
        crashing.dispatch(command("req-crash"), credentials)

        assertEquals("UNSUPPORTED_ACTION", sender.sent[0].error?.code)
        assertEquals("EXECUTION_FAILED", sender.sent[1].error?.code)
        assertEquals(ExecutionState.FAILED.value, log.commands.getValue("req-crash").status)
    }

    @Test
    fun `a report the server keeps refusing is abandoned after max attempts`() {
        sender.outcome = ReportOutcome.RETRY
        dispatcher.dispatch(command(), credentials)
        val flusher = ReportFlusher(log, sender)

        repeat(ReportFlusher.MAX_ATTEMPTS + 2) { flusher.flush(credentials) }

        assertEquals(ReportStatus.ABANDONED, log.reports.getValue("req-1").status)
        assertEquals(ReportFlusher.MAX_ATTEMPTS, sender.sent.size)
    }

    @Test
    fun `rejected report is abandoned immediately`() {
        sender.outcome = ReportOutcome.REJECTED

        dispatcher.dispatch(command(), credentials)
        val first = log.reports.getValue("req-1")
        val flushed = ReportFlusher(log, sender).flush(credentials)

        assertEquals(ReportStatus.ABANDONED, first.status)
        assertEquals(1, first.attempts)
        assertEquals(0, flushed)
        assertEquals(1, sender.sent.size)
    }

    @Test
    fun `retry report stays failed`() {
        sender.outcome = ReportOutcome.RETRY

        dispatcher.dispatch(command(), credentials)

        assertEquals(ReportStatus.FAILED, log.reports.getValue("req-1").status)
        assertEquals(1, log.reports.getValue("req-1").attempts)
    }

    @Test
    fun `a corrupt stored report is abandoned instead of guessed`() {
        log.savePendingReport(
            PendingReportRecord("req-bad", "user-1", "phone-1", "contact_call", executionState = "unknown")
        )

        ReportFlusher(log, sender).flush(credentials)

        assertTrue(sender.sent.isEmpty())
        assertEquals(ReportStatus.ABANDONED, log.reports.getValue("req-bad").status)
    }

    @Test
    fun `two flushes at the same time send a pending report only once`() {
        log.savePendingReport(PendingReportRecord("req-slow", "user-1", "phone-1", "contact_call", executionState = "succeeded"))
        val inside = java.util.concurrent.CountDownLatch(1)
        val release = java.util.concurrent.CountDownLatch(1)
        val blocking = object : ReportSender {
            val sent = java.util.concurrent.atomic.AtomicInteger()
            override fun send(credentials: DeviceCredentials, payload: DeviceReportPayload): ReportOutcome {
                sent.incrementAndGet()
                inside.countDown()
                release.await(5, java.util.concurrent.TimeUnit.SECONDS)
                return ReportOutcome.SENT
            }
        }
        var firstResult = -1
        val first = Thread { firstResult = ReportFlusher(log, blocking).flush(credentials) }
        first.start()
        assertTrue(inside.await(5, java.util.concurrent.TimeUnit.SECONDS))

        val second = ReportFlusher(log, blocking).flush(credentials) // a flush is running: skipped
        release.countDown()
        first.join(5_000)

        assertEquals(0, second)
        assertEquals(1, firstResult)
        assertEquals(1, blocking.sent.get())
        assertEquals(ReportStatus.SENT, log.reports.getValue("req-slow").status)
    }

    @Test
    fun `a report that is already sent is not sent again by a later flush or dispatch path`() {
        dispatcher.dispatch(command(), credentials)
        assertEquals(ReportStatus.SENT, log.reports.getValue("req-1").status)

        // Another path still holds the stale PENDING copy it read earlier.
        val stale = log.reports.getValue("req-1").copy(status = ReportStatus.PENDING)
        val outcome = sendOnce(log, sender, credentials, stale)

        assertEquals(ReportOutcome.SENT, outcome)
        assertEquals(1, sender.sent.size)
    }

    @Test
    fun `a late 409 never turns a delivered report into abandoned`() {
        log.savePendingReport(PendingReportRecord("req-409", "user-1", "phone-1", "contact_call", executionState = "succeeded"))
        val racing = object : ReportSender {
            override fun send(credentials: DeviceCredentials, payload: DeviceReportPayload): ReportOutcome {
                // Meanwhile the other path delivered it; this duplicate gets refused.
                log.updateReportStatus("req-409", ReportStatus.SENT, 1)
                return ReportOutcome.REJECTED
            }
        }

        sendOnce(log, racing, credentials, log.reports.getValue("req-409"))

        assertEquals(ReportStatus.SENT, log.reports.getValue("req-409").status)
    }

    @Test
    fun `a report being sent right now is left to the sender in progress`() {
        log.savePendingReport(PendingReportRecord("req-busy", "user-1", "phone-1", "contact_call", executionState = "succeeded"))
        val inside = java.util.concurrent.CountDownLatch(1)
        val release = java.util.concurrent.CountDownLatch(1)
        val slow = ReportSender { _, _ ->
            inside.countDown()
            release.await(5, java.util.concurrent.TimeUnit.SECONDS)
            ReportOutcome.SENT
        }
        val record = log.reports.getValue("req-busy")
        val thread = Thread { sendOnce(log, slow, credentials, record) }
        thread.start()
        assertTrue(inside.await(5, java.util.concurrent.TimeUnit.SECONDS))

        val outcome = sendOnce(log, sender, credentials, record)
        release.countDown()
        thread.join(5_000)

        assertEquals(ReportOutcome.RETRY, outcome)
        assertTrue(sender.sent.isEmpty())
    }
}
