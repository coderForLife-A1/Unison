# Unison Standalone Native Android Application

A fully autonomous, on-device Android application for **Unison**. Runs 100% on your smartphone with **zero server, zero laptop, and zero cloud dependencies**.

---

## 🏗️ Architecture

```
📱 Android Smartphone (No PC required)
│
├── 📡 NotificationListenerService (Background)
│   └── Intercepts incoming notifications in real-time (WhatsApp, SMS, Gmail, Slack, etc.)
│
├── 🧠 On-Device LLM Inference (MediaPipe GenAI)
│   ├── Runs directly on your phone's CPU/GPU/NPU
│   └── Quantized mobile models (.bin/.task): Gemma-2-2B, Qwen2.5 (1.5B/3B)
│
├── 💾 Local Room Database (SQLite)
│   └── Offline persistence for notification history and digests
│
└── 🎨 Jetpack Compose UI (Material 3)
    ├── Sloth Mode toggle (Show only action-required notifications)
    ├── Priority classifications (🔴 High, 🟡 Low, ⚪ Noise)
    └── 1-Click suggested replies
```

---

## 🚀 How to Build the Standalone APK

### Option A: Using Android Studio (Recommended)
1. Open **Android Studio**.
2. Select **Open an existing project** &rarr; Navigate to `Unison/android`.
3. Let Gradle sync project dependencies.
4. Click **Build > Build Bundle(s) / APK(s) > Build APK(s)**.
5. Android Studio will generate the APK at:
   ```text
   Unison/android/app/build/outputs/apk/debug/app-debug.apk
   ```
6. Transfer `app-debug.apk` to your phone and install it.

### Option B: Using Gradle CLI
From the `Unison/android` directory:
```bash
./gradlew assembleDebug
```

---

## ⚙️ Initial Phone Setup (One-Time)

1. **Install the APK** on your phone.
2. **Grant Notification Access:**
   - Tap the ⚙️ icon in the app toolbar (or go to **Phone Settings > Apps > Special App Access > Notification Access**).
   - Turn on the toggle for **Unison**.
3. **Optional (High-accuracy LLM model):**
   - Place a compatible mobile model (e.g. `gemma-2b-it-cpu-int4.bin` or `qwen2.5-1.5b.bin`) into the app's files directory (`/Android/data/com.unison.app/files/model.bin`).
   - If no model is placed, Unison automatically uses its internal fast heuristic classifier!

