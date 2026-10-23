package com.unison.app.engine

import android.content.Context
import android.util.Log
import com.google.mediapipe.tasks.genai.llminference.LlmInference
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File

/**
 * On-Device LLM Inference Engine powered by MediaPipe GenAI.
 * Runs 100% locally on the phone's chip (GPU/NPU acceleration).
 * Models: Gemma-2-2B, Qwen2.5-1.5B/3B (.bin/.task format).
 */
class OnDeviceLlmEngine private constructor(private val context: Context) {

    private var llmInference: LlmInference? = null
    private val TAG = "OnDeviceLlmEngine"

    companion object {
        @Volatile
        private var INSTANCE: OnDeviceLlmEngine? = null

        fun getInstance(context: Context): OnDeviceLlmEngine {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: OnDeviceLlmEngine(context.applicationContext).also { INSTANCE = it }
            }
        }
    }

    /**
     * Initializes the on-device model from the internal app storage path.
     */
    fun initialize(modelPath: String): Boolean {
        return try {
            val file = File(modelPath)
            if (!file.exists()) {
                Log.w(TAG, "Model file not found at: $modelPath")
                return false
            }

            val options = LlmInference.LlmInferenceOptions.builder()
                .setModelPath(modelPath)
                .setMaxTokens(512)
                .build()

            llmInference = LlmInference.createFromOptions(context, options)
            Log.i(TAG, "On-device model successfully loaded into memory!")
            true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to load on-device LLM: ${e.message}", e)
            false
        }
    }

    val isReady: Boolean
        get() = llmInference != null

    /**
     * Triages raw notification text into structured summary attributes:
     * priority, category, summary, actionRequired, suggestedReply.
     */
    suspend fun triageNotification(
        source: String,
        sender: String,
        content: String
    ): TriageResult = withContext(Dispatchers.Default) {
        val prompt = """
            You are Unison, a quiet on-device notification intelligence assistant.
            Triage this notification into JSON with keys:
            "priority": ("High" or "Low" or "Noise")
            "category": ("Work", "Personal", "Social", "OTP", "Promo", "System")
            "summary": (1 concise sentence)
            "action_required": (true or false)
            "suggested_reply": (1 short suggested reply if action_required, otherwise null)

            Notification:
            Source: $source
            Sender: $sender
            Text: $content

            Respond with ONLY the JSON object.
        """.trimIndent()

        val inference = llmInference
        if (inference == null) {
            // Fallback rule-based parsing if model file has not been loaded into storage yet
            return@withContext fallbackTriage(source, sender, content)
        }

        try {
            val rawOutput = inference.generateResponse(prompt)
            parseJsonResponse(rawOutput)
        } catch (e: Exception) {
            Log.e(TAG, "Inference failed: ${e.message}", e)
            fallbackTriage(source, sender, content)
        }
    }

    private fun parseJsonResponse(raw: String): TriageResult {
        return try {
            val cleaned = raw.substringAfter("{").substringBeforeLast("}")
            val json = JSONObject("{$cleaned}")
            TriageResult(
                priority = json.optString("priority", "Low"),
                category = json.optString("category", "General"),
                summary = json.optString("summary", "Notification received."),
                actionRequired = json.optBoolean("action_required", false),
                suggestedReply = json.optString("suggested_reply", null).takeIf { it != "null" && it.isNotBlank() }
            )
        } catch (e: Exception) {
            TriageResult(
                priority = "Low",
                category = "General",
                summary = raw.take(120),
                actionRequired = false,
                suggestedReply = null
            )
        }
    }

    private fun fallbackTriage(source: String, sender: String, content: String): TriageResult {
        val lower = content.lowercase()
        val isOtp = lower.contains("code") || lower.contains("otp") || lower.contains("password")
        val isPromo = lower.contains("sale") || lower.contains("off") || lower.contains("coupon")
        val hasQuestion = content.contains("?") || lower.contains("can you") || lower.contains("please")

        return TriageResult(
            priority = if (isOtp || hasQuestion) "High" else if (isPromo) "Noise" else "Low",
            category = if (isOtp) "OTP" else if (isPromo) "Promo" else "Personal",
            summary = "$sender: $content",
            actionRequired = hasQuestion || isOtp,
            suggestedReply = if (hasQuestion) "On it, will check shortly." else null
        )
    }
}

data class TriageResult(
    val priority: String,
    val category: String,
    val summary: String,
    val actionRequired: Boolean,
    val suggestedReply: String?
)

