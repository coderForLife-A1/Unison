package com.unison.app.service

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import com.unison.app.data.NotificationEntity
import com.unison.app.data.UnisonDatabase
import com.unison.app.engine.OnDeviceLlmEngine
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class UnisonNotificationListener : NotificationListenerService() {

    private val TAG = "UnisonNotifListener"
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        super.onNotificationPosted(sbn)
        if (sbn == null) return

        val pkgName = sbn.packageName
        // Ignore self notifications and persistent system services
        if (pkgName == packageName || sbn.isOngoing) return

        val extras = sbn.notification.extras
        val title = extras.getString(Notification.EXTRA_TITLE) ?: ""
        val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: ""

        if (text.isBlank() && title.isBlank()) return

        val friendlyAppSource = mapPackageToSourceName(pkgName)

        Log.d(TAG, "Captured notification from $friendlyAppSource ($title): $text")

        serviceScope.launch {
            val db = UnisonDatabase.getDatabase(applicationContext)
            val llmEngine = OnDeviceLlmEngine.getInstance(applicationContext)

            // 1. Run local on-device triage inference
            val result = llmEngine.triageNotification(
                source = friendlyAppSource,
                sender = title,
                content = text
            )

            // 2. Persist to local Room database
            val entity = NotificationEntity(
                source = friendlyAppSource,
                sender = title,
                content = text,
                timestamp = System.currentTimeMillis(),
                priority = result.priority,
                category = result.category,
                summary = result.summary,
                actionRequired = result.actionRequired,
                suggestedReply = result.suggestedReply
            )

            db.notificationDao().insertNotification(entity)
        }
    }

    private fun mapPackageToSourceName(pkg: String): String {
        return when {
            pkg.contains("whatsapp") -> "WhatsApp"
            pkg.contains("messaging") || pkg.contains("mms") -> "SMS"
            pkg.contains("gm") || pkg.contains("mail") || pkg.contains("outlook") -> "Email"
            pkg.contains("slack") -> "Slack"
            pkg.contains("telegram") -> "Telegram"
            pkg.contains("instagram") -> "Instagram"
            else -> pkg.substringAfterLast(".").replaceFirstChar { it.uppercase() }
        }
    }
}

