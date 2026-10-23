# 🦥 Unison

> **Slow and Steady Notification Intelligence.**  
> A private, local-first communication aggregator, semantic deduplicator, and AI digest engine. Built for quiet minds and zero-distraction focus.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.160+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)](https://react.dev)
[![Ollama](https://img.shields.io/badge/Ollama-Offline%20Inference-black?logo=ollama&logoColor=white)](https://ollama.com)
[![LanceDB](https://img.shields.io/badge/LanceDB-Vector%20Store-EC5A3D)](https://lancedb.com)
[![Zero Cloud](https://img.shields.io/badge/Privacy-100%25%20Air--Gapped-success)](#-privacy--security-guarantees)

---

## 📸 Overview

Modern digital communication forces us to constantly context-switch across dozens of messaging platforms, email inboxes, and system popups. This creates notification fatigue, fragmented attention, and chronic cognitive overload.

**Unison flips this dynamic on its head.** Instead of reacting to endless real-time pings:
1. **Passively Ingests** alerts from WhatsApp, Android SMS, emails, and system feeds.
2. **Semantically Deduplicates** floods of incoming chatter using local vector embeddings.
3. **Triages & Summarizes** messages into clean, actionable digests using offline local LLMs.
4. **Presents a Unified Dashboard** accessible seamlessly across both **Desktop** and **Android devices**.

---

## ✨ Key Features

- 🔒 **100% Local & Air-Gapped:** Zero telemetry, zero cloud API calls. Notification bodies, credentials, and contacts never leave your device.
- 🧬 **Intelligent Vector Deduplication:** High-frequency chat floods (e.g. 15 messages in 2 minutes) are collapsed into single coherent conversation digests via cosine similarity.
- 🦥 **Sloth Mode (Action-First Digest):** Hide informational noise and view strictly what requires your active response or decision.
- 💬 **Suggested Quick Drafts:** AI drafts one-click response suggestions for actionable notifications.
- 📱 **Cross-Platform Responsive (Desktop & Android):** Modern React SPA with PWA support, optimized for desktop monitors, Android mobile browsers, and native WebViews.
- ⚡ **Debounced Batch Processing:** LLM inference is triggered conservatively in batches rather than on every raw event, maximizing battery life and reducing CPU/GPU overhead.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             Ingestion Sources                               │
│       [ OS Alerts ]    [ Android SMS ]    [ WhatsApp ]    [ IMAP Email ]    │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Pluggable Ingestion Adapters                       │
│    Normalizes heterogeneous payloads into IngestedNotification Pydantic schemas │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Local Deduplication Engine                          │
│          FastEmbed (bge-small-en-v1.5) + LanceDB Embedded Storage           │
│        Groups semantically similar messages within dynamic time windows     │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Local LLM Structured Engine                         │
│               Ollama (Qwen2.5:3b / Qwen2.5-Coder / Llama3.2)                │
│             Structured extraction via Instructor + Pydantic v2              │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                 Unison Unified Dashboard (Desktop & Android)                │
│         FastAPI Backend + React / HTML5 / CSS PWA (Local LAN Hosted)        │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Tech Stack & Open Source Stack

| Layer | Technology | Purpose |
|---|---|---|
| **Local LLM Engine** | [Ollama](https://ollama.com) (`qwen2.5:3b`, `llama3.2:3b`, etc.) | Fully offline local inference |
| **Type-Safe Extraction** | [Instructor](https://github.com/jxnl/instructor) + [Pydantic v2](https://pydantic.dev) | Guarantees strict JSON output schema |
| **Embeddings** | [FastEmbed](https://github.com/qdrant/fastembed) (`BAAI/bge-small-en-v1.5`) | CPU-accelerated local text embeddings |
| **Vector Storage** | [LanceDB](https://lancedb.com) (`.lancedb/`) | Serverless, disk-based vector similarity search |
| **Backend API** | [FastAPI](https://fastapi.tiangolo.com) + [Uvicorn](https://www.uvicorn.org) | Async REST API & static asset delivery |
| **Frontend UI** | [React](https://react.dev) + [Vite](https://vite.dev) + Lucide Icons | Responsive Desktop & Android PWA dashboard |

---

## 🚀 Getting Started

### Prerequisites

1. **Python 3.10+**
2. **Node.js 18+** & `npm`
3. **[Ollama](https://ollama.com)** installed and running locally.
4. Pull your preferred lightweight model:
   ```bash
   ollama pull qwen2.5:3b
   ```

---

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/coderForLife-A1/Unison.git
   cd Unison
   ```

2. **Set up a Python virtual environment:**
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate
   ```

3. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Install & build Frontend assets:**
   ```bash
   cd frontend
   npm install
   npm run build
   cd ..
   ```

5. **Configure environment settings:**
   ```bash
   cp .env.example .env
   ```
   *(Review `.env` to customize model names, vector thresholds, or optional IMAP mailbox credentials)*.

---

### Running Unison

#### 1. Interactive Dashboard (Desktop & Mobile)
```bash
python main.py
```
- **Desktop:** Open [http://localhost:8000](http://localhost:8000)
- **Android Phone (Same Wi-Fi):** Open `http://<YOUR_COMPUTER_IP>:8000`  
  *(Tip: Tap **"Add to Home screen"** in Chrome on Android for the full native app experience).*

#### 2. Headless CLI Mode
Run a one-shot ingestion, deduplication, and triage cycle directly in the terminal:
```bash
python main.py --cli
```

---

## 📱 Mobile Integration (Android)

Unison includes a native REST endpoint for direct alert forwarding:

```http
POST /api/notifications/direct
Content-Type: application/json

{
  "source": "WhatsApp",
  "sender": "Lead Engineer",
  "content": "Can you review PR #42 before today's deployment?"
}
```

- **Automation:** Use apps like **Tasker**, **MacroDroid**, or an Android **`NotificationListenerService`** to forward incoming notifications to `http://<YOUR_LAN_IP>:8000/api/notifications/direct`.
- **Simulator:** You can test real-time mobile push notifications directly within the dashboard using the built-in **"Simulate Mobile Alert"** tool.

---

## 🧪 Testing

Run the automated test suite:
```bash
pytest tests
```

---

## 🛡️ Privacy & Security Guarantees

- **No Remote Telemetry:** No usage metrics or message traces leave your machine.
- **Air-Gapped Ready:** After initial dependency & model downloads, Unison operates with zero network connectivity.
- **Embedded Storage:** LanceDB and caches remain in your local repository folder (`.lancedb/`).

---

## 📜 License

Distributed under the MIT License. See [LICENSE](LICENSE) for details.
