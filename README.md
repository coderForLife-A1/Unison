🦥 Unison

Slow and Steady Notification Intelligence.

A local-first notification, SMS, WhatsApp, and email aggregation and summarization dashboard. Built for the "Slow and Steady" hackathon, Unison brings all your communication channels into a single, quiet interface powered by local open-weight LLMs.

📸 Overview

Modern digital communication forces us to constantly context-switch across dozens of messaging platforms, email inboxes, and system popups. This creates notification fatigue, fragmented attention, and unnecessary stress.

Unison flips this dynamic on its head. Instead of reacting to endless pings, Unison passively ingests alerts from your platforms, deduplicates repeated chatter using local vector storage, and runs structured local inference to categorize messages into a clean, actionable digest.

Core Highlights

100% Local & Air-Gapped: Your notifications, SMS logs, and private email bodies never leave your machine.

Intelligent Deduplication: Merges multi-message chat floods into coherent single-thread digests using semantic vector similarity.

Structured Unison: Powered by local inference, messages are classified strictly into Action Items, Social Summaries, and Low-Priority Noise.

Zero-Cloud Dependency: Designed to run seamlessly offline on standard laptop hardware.

🏗️ Architecture

Unison uses a modular ingestion-to-unison pipeline executing entirely on local device runtime:

┌─────────────────────────────────────────────────────────────────┐
│                       Ingestion Sources                         │
│   [ OS Alerts ]    [ Android SMS ]   [ WhatsApp ]   [ IMAP ]    │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Local Ingestion Adapter                      │
│        Normalizes heterogenous message payloads to standard     │
│                         JSON format                             │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Local Deduplication Engine                     │
│      FastEmbed (bge-small-en-v1.5) + LanceDB Local Storage      │
│      Groups related chatter into unified conversation blocks    │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                 Local LLM Structured Unison                     │
│               Ollama (Qwen2.5-3B-Instruct)                      │
│    Structured JSON schema via Instructor / Pydantic parsing     │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Unison Unified Dashboard                      │
│        Single-page UI displaying Action Items & Digests         │
└─────────────────────────────────────────────────────────────────┘


🛠️ Tech Stack & Open Source Components

Local LLM Engine: Ollama running Qwen2.5-3B-Instruct (or Llama-3.2-3B-Instruct).

Embeddings & Vector Database: FastEmbed (bge-small-en-v1.5) paired with LanceDB for fast local vector lookups.

Structured Output Enforcement: Instructor + Pydantic to guarantee strict JSON output schemas.

User Interface: Streamlit / Tauri + React.

Data Adapters: Local IMAP polling client, Android NotificationListenerService bridge.

✨ Features

Unified Inbox: View SMS, WhatsApp, email, and system alerts in one consolidated interface.

Sloth Mode (Action-First Digest): Hide raw message body noise and display only explicit actions required of you.

Noise Reduction Filters: Automatically demotes promotional emails, verification OTPs, and system warnings to low-priority background logs.

Thread Consolidation: If a group chat fires 15 notifications in 2 minutes, Unison collapses them into a single 1-sentence narrative summary.

Privacy Assurance: Fully functioning with internet connection disabled (once models are downloaded).

🚀 Getting Started

Prerequisites

Install Python 3.10+.

Install Ollama.

Pull the default lightweight open-weight model:

ollama pull qwen2.5:3b


Installation

Clone the repository:

git clone https://github.com/coderForLife-A1/Unison.git
cd Unison


Set up a virtual environment:

python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate


Install dependencies:

pip install -r requirements.txt


Configuration:
Create your local configuration file from the provided example:

cp .env.example .env


Run Unison:

python main.py


⚙️ How It Works

Ingest: Background adapters monitor local notification hooks, email IMAP servers, or Android bridge outputs.

Deduplicate: FastEmbed generates local embeddings for incoming alerts, comparing them against recent entries in LanceDB to merge related chat messages.

Unison: The local Qwen2.5-3B model processes the batch using Instructor to enforce structured JSON output:

{
  "source": "WhatsApp - Family Group",
  "priority": "High",
  "category": "Social",
  "summary": "Mom and Dad are planning Sunday lunch at 1 PM.",
  "action_required": true,
  "action_item": "Confirm attendance by Saturday evening."
}


Display: Unison renders the parsed results onto the unified local dashboard sorted by priority.

🛡️ Privacy & Security Guarantees

Unison was designed from the ground up to respect user data sovereignty:

No Telemetry: No analytical data, message logs, or usage statistics are transmitted outside your local machine.

Air-Gapped Processing: Inference and vector indexing run entirely on your CPU/GPU hardware.

Local Storage: Vector databases and sqlite caches remain encrypted/stored strictly in your project workspace directory.

📜 License

Distributed under the MIT License. See LICENSE for details.