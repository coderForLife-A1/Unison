package com.unison.app.data

import androidx.room.Entity
import androidx.room.PrimaryKey
import java.util.UUID

@Entity(tableName = "notifications")
data class NotificationEntity(
    @PrimaryKey val id: String = UUID.randomUUID().toString(),
    val source: String,          // e.g. "WhatsApp", "SMS", "Gmail"
    val sender: String,          // Contact / Group / Title
    val content: String,         // Notification text body
    val timestamp: Long = System.currentTimeMillis(),
    val priority: String = "Low",// "High", "Low", "Noise"
    val category: String = "General",
    val summary: String = "",    // 1-sentence AI summary
    val actionRequired: Boolean = false,
    val suggestedReply: String? = null
)

