package com.youreyes.app.command

import android.util.Log
import com.youreyes.app.core.DeviceCredentials
import java.util.concurrent.locks.ReentrantLock

/**
 * Re-sends reports that did not reach the server the first time.
 *
 * Runs after slow-lane commands and from [ReportRetryWorker]. All callers share one lock: if a
 * flush is already running, this one does nothing (the running one covers the same rows).
 * A report is only ever re-sent, never re-executed. After [MAX_ATTEMPTS] it is
 * abandoned: a report the server refuses (e.g. the operation already timed out)
 * would fail the same way forever.
 */
class ReportFlusher(
    private val store: CommandLog,
    private val sender: ReportSender,
) {

    /** Returns how many reports were delivered. Never throws. */
    fun flush(credentials: DeviceCredentials): Int {
        if (!flushLock.tryLock()) return 0
        try {
            return flushLocked(credentials)
        } finally {
            flushLock.unlock()
        }
    }

    private fun flushLocked(credentials: DeviceCredentials): Int {
        val pending = runCatching { store.unsentReports() }.getOrElse {
            Log.w(TAG, "Could not read pending reports", it)
            return 0
        }
        var sent = 0
        for (record in pending) {
            if (record.attempts >= MAX_ATTEMPTS) {
                store.updateReportStatus(record.requestId, ReportStatus.ABANDONED, record.attempts)
                continue
            }
            if (sendOnce(store, sender, credentials, record) == ReportOutcome.SENT) sent++
        }
        if (pending.isNotEmpty()) Log.i(TAG, "Re-sent $sent of ${pending.size} pending report(s)")
        return sent
    }

    companion object {
        const val MAX_ATTEMPTS = 8
        private val flushLock = ReentrantLock()
        private const val TAG = "ReportFlusher"
    }
}
