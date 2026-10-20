package com.youreyes.app.command

import android.content.Context
import android.util.Log
import com.youreyes.app.actions.ActionExecutionResult
import com.youreyes.app.actions.ActionRegistry
import com.youreyes.app.core.DeviceCredentials
import com.youreyes.app.core.Timestamps
import org.json.JSONObject
import java.util.GregorianCalendar
import java.util.TimeZone

/** One command pushed by the server (FCM data message). */
data class PushCommand(
    val requestId: String,
    val userId: String,
    val deviceId: String,
    val action: String,
    val paramsJson: String,
    val expiresAtMillis: Long? = null,
    val mapsUrl: String? = null,
) {
    companion object {
        /** Parses an FCM data map; null when a required field is missing. */
        fun fromData(data: Map<String, String>): PushCommand? {
            return PushCommand(
                requestId = data["request_id"]?.takeIf { it.isNotBlank() } ?: return null,
                userId = data["user_id"]?.takeIf { it.isNotBlank() } ?: return null,
                deviceId = data["device_id"]?.takeIf { it.isNotBlank() } ?: return null,
                action = data["action"]?.takeIf { it.isNotBlank() } ?: return null,
                paramsJson = data["params_json"]?.takeIf { it.isNotBlank() } ?: "{}",
                expiresAtMillis = data["expires_at"]?.let(::parseExpiry),
                mapsUrl = data["maps_url"]?.takeIf { it.isNotBlank() },
            )
        }

        private fun parseExpiry(value: String): Long? {
            val match = EXPIRY_PATTERN.matchEntire(value) ?: return null
            return runCatching {
                if (match.groupValues[8] != "Z") {
                    val offsetHours = match.groupValues[10].toInt()
                    val offsetMinutes = match.groupValues[11].toInt()
                    require(offsetHours <= 18 && offsetMinutes <= 59 && (offsetHours < 18 || offsetMinutes == 0))
                }
                val calendar = GregorianCalendar(TimeZone.getTimeZone("UTC")).apply {
                    isLenient = false
                    clear()
                    set(
                        match.groupValues[1].toInt(),
                        match.groupValues[2].toInt() - 1,
                        match.groupValues[3].toInt(),
                        match.groupValues[4].toInt(),
                        match.groupValues[5].toInt(),
                        match.groupValues[6].toInt(),
                    )
                    set(
                        GregorianCalendar.MILLISECOND,
                        match.groupValues[7].padEnd(3, '0').take(3).toIntOrNull() ?: 0,
                    )
                }
                val localMillis = calendar.timeInMillis
                val sign = if (match.groupValues[9] == "-") -1 else 1
                val offsetMinutes = if (match.groupValues[8] == "Z") 0 else
                    sign * (match.groupValues[10].toInt() * 60 + match.groupValues[11].toInt())
                localMillis - offsetMinutes * 60_000L
            }.getOrNull()
        }

        private val EXPIRY_PATTERN = Regex(
            "^(\\d{4})-(\\d{2})-(\\d{2})T(\\d{2}):(\\d{2}):(\\d{2})(?:\\.(\\d{1,9}))?(Z|([+-])(\\d{2}):(\\d{2}))$"
        )
    }
}

/** What the dispatcher and flusher need from local storage ([CommandStore] in the app). */
interface CommandLog {
    fun insertCommandIfNew(record: CommandRecord): Boolean
    fun finishCommand(requestId: String, status: String, resultJson: String?, errorJson: String?)
    fun savePendingReport(record: PendingReportRecord)
    fun updateReportStatus(requestId: String, status: String, attempts: Int)
    fun unsentReports(): List<PendingReportRecord>

    /** Current status of one stored report ([ReportStatus]), or null if there is none. */
    fun reportStatus(requestId: String): String?
}

enum class ReportOutcome { SENT, RETRY, REJECTED }

/** Sends one report and classifies how it should be handled. */
fun interface ReportSender {
    fun send(credentials: DeviceCredentials, payload: DeviceReportPayload): ReportOutcome
}

sealed class DispatchResult {
    data class DuplicateIgnored(val requestId: String) : DispatchResult()
    data class Executed(
        val requestId: String,
        val executionState: ExecutionState,
        val reportSent: Boolean,
    ) : DispatchResult()
}

/**
 * Runs one pushed command exactly once and reports its result.
 *
 * 1. Record the `request_id`; a duplicate push stops here without running anything.
 * 2. Run the action's handler.
 * 3. Store the result as a pending report, then try to send it once.
 *    Unsent reports are retried later by [ReportFlusher], never by re-running the handler.
 */
class CommandDispatcher(
    private val store: CommandLog,
    private val registry: ActionRegistry,
    private val sender: ReportSender,
    private val context: Context?,
    private val clock: () -> Long = System::currentTimeMillis,
) {

    fun dispatch(command: PushCommand, credentials: DeviceCredentials?): DispatchResult {
        val receivedAt = clock()
        val isNew = store.insertCommandIfNew(
            CommandRecord(
                requestId = command.requestId,
                userId = command.userId,
                deviceId = command.deviceId,
                action = command.action,
                paramsJson = command.paramsJson,
                receivedAt = receivedAt,
                status = "PROCESSING",
            )
        )
        if (!isNew) return DispatchResult.DuplicateIgnored(command.requestId)

        val outcome = if (command.expiresAtMillis != null && clock() > command.expiresAtMillis) {
            ActionExecutionResult.Error(ReportErrorPayload("COMMAND_EXPIRED", "Command expired before execution"))
        } else try {
            val paramsJson = if (command.mapsUrl != null) {
                runCatching {
                    JSONObject(command.paramsJson).apply {
                        if (!has("maps_url")) put("maps_url", command.mapsUrl)
                    }.toString()
                }.getOrDefault(command.paramsJson)
            } else command.paramsJson
            registry.getHandler(command.action).execute(context, paramsJson)
        } catch (exc: Exception) {
            Log.e(TAG, "Handler for ${command.action} threw", exc)
            ActionExecutionResult.Error(
                ReportErrorPayload("EXECUTION_FAILED", "Handler crashed: ${exc.message}")
            )
        }
        val state: ExecutionState
        val resultJson: String?
        val errorJson: String?
        when (outcome) {
            is ActionExecutionResult.Success -> {
                state = ExecutionState.SUCCEEDED
                resultJson = JSONObject(outcome.resultData).toString()
                errorJson = null
            }
            is ActionExecutionResult.Error -> {
                state = ExecutionState.FAILED
                resultJson = null
                errorJson = outcome.errorPayload.toJson().toString()
            }
        }
        store.finishCommand(command.requestId, state.value, resultJson, errorJson)

        val report = PendingReportRecord(
            requestId = command.requestId,
            userId = command.userId,
            deviceId = command.deviceId,
            action = command.action,
            executionState = state.value,
            resultJson = resultJson,
            errorJson = errorJson,
            executedAt = clock(),
        )
        store.savePendingReport(report)

        val sendOutcome = if (credentials != null) sendOnce(store, sender, credentials, report) else ReportOutcome.RETRY
        return DispatchResult.Executed(command.requestId, state, sendOutcome == ReportOutcome.SENT)
    }

    private companion object {
        const val TAG = "CommandDispatcher"
    }
}

/** Rebuilds the report body from a stored row; null if the row is unusable. */
fun PendingReportRecord.toPayload(): DeviceReportPayload? {
    val state = ExecutionState.fromValue(executionState) ?: return null
    val result = resultJson?.let { runCatching { jsonToMap(JSONObject(it)) }.getOrNull() }
    val error = errorJson?.let { runCatching { ReportErrorPayload.fromJson(JSONObject(it)) }.getOrNull() }
    return DeviceReportPayload(
        userId = userId,
        deviceId = deviceId,
        requestId = requestId,
        action = action,
        executionState = state,
        result = result,
        error = error,
        timestamp = Timestamps.utc(executedAt),
    )
}

/** Reports being sent right now by this process, so one report never goes out twice at once. */
private val reportsInFlight: MutableSet<String> = java.util.concurrent.ConcurrentHashMap.newKeySet()

/**
 * Sends one stored report and records the attempt.
 *
 * Safe to call from several threads for the same report: only one sends, the others get
 * [ReportOutcome.RETRY] without touching the row. A report already SENT is never sent again and
 * is never downgraded afterwards (a late 409 for a report another thread delivered is not a failure).
 */
internal fun sendOnce(
    store: CommandLog,
    sender: ReportSender,
    credentials: DeviceCredentials,
    record: PendingReportRecord,
): ReportOutcome {
    if (!reportsInFlight.add(record.requestId)) return ReportOutcome.RETRY
    try {
        when (store.reportStatus(record.requestId)) {
            ReportStatus.SENT -> return ReportOutcome.SENT
            ReportStatus.ABANDONED -> return ReportOutcome.REJECTED
        }
        val payload = record.toPayload() ?: run {
            store.updateReportStatus(record.requestId, ReportStatus.ABANDONED, record.attempts)
            return ReportOutcome.REJECTED
        }
        val attempts = record.attempts + 1
        val outcome = runCatching { sender.send(credentials, payload) }.getOrDefault(ReportOutcome.RETRY)
        val status = when (outcome) {
            ReportOutcome.SENT -> ReportStatus.SENT
            ReportOutcome.RETRY -> ReportStatus.FAILED
            ReportOutcome.REJECTED -> ReportStatus.ABANDONED
        }
        if (status == ReportStatus.SENT || store.reportStatus(record.requestId) != ReportStatus.SENT) {
            store.updateReportStatus(record.requestId, status, attempts)
        }
        return outcome
    } finally {
        reportsInFlight.remove(record.requestId)
    }
}
