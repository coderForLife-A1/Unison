package com.unison.app

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.lifecycle.lifecycleScope
import com.unison.app.data.UnisonDatabase
import com.unison.app.engine.OnDeviceLlmEngine
import com.unison.app.ui.DashboardScreen
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val db = UnisonDatabase.getDatabase(this)
        val llmEngine = OnDeviceLlmEngine.getInstance(this)

        // Attempt to load model from external files dir if present (e.g., gemma-2b.bin or qwen.bin)
        val modelFile = getExternalFilesDir(null)?.resolve("model.bin")
        if (modelFile != null && modelFile.exists()) {
            llmEngine.initialize(modelFile.absolutePath)
        }

        setContent {
            val notifications by db.notificationDao().getAllNotifications().collectAsState(initial = emptyList())

            DashboardScreen(
                notifications = notifications,
                isModelLoaded = llmEngine.isReady,
                onOpenListenerSettings = {
                    startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
                },
                onClearAll = {
                    lifecycleScope.launch {
                        db.notificationDao().clearAll()
                    }
                }
            )
        }
    }
}

