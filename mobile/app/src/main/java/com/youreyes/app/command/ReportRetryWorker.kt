package com.youreyes.app.command

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.youreyes.app.core.AppConfig
import com.youreyes.app.network.DeviceApiClient
import java.util.concurrent.TimeUnit

class ReportRetryWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val credentials = AppConfig.deviceCredentials(applicationContext) ?: return Result.success()
        return runCatching {
            val store = CommandStore(applicationContext)
            ReportFlusher(store, DeviceApiClient().asReportSender()).flush(credentials)
            if (store.unsentReports().isNotEmpty()) Result.retry() else Result.success()
        }.getOrElse { Result.retry() }
    }

    companion object {
        private const val WORK_NAME = "report-retry"

        fun enqueue(context: Context) {
            val request = OneTimeWorkRequestBuilder<ReportRetryWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 15, TimeUnit.SECONDS)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(WORK_NAME, ExistingWorkPolicy.KEEP, request)
        }
    }
}
