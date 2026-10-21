# AGENTS.md — Technical Context & Guidelines for AI Coding Agents

> **Project Name:** Unison  
> **Repository Scope:** Local-first notification aggregation, vector deduplication, and LLM processing engine.  
> **Theme:** "Slow and Steady" — Prioritize efficiency, local execution, low resource overhead, and minimal user distraction.

---

## 1. System Mission & Core Constraints

1. **Strict 100% Local Privacy:** NEVER add cloud LLM fallbacks (e.g., OpenAI, Anthropic, Google Gemini APIs). All model calls MUST go through local inference providers (specifically `Ollama` or `llama.cpp`).
2. **Zero Overhead Design:** Unison is built for low power consumption. Implement batch processing and debouncing rather than running LLM inference on every single raw notification event.
3. **Strict Type Validation:** All model outputs MUST be strictly typed using `Pydantic v2` and parsed using the `Instructor` library.

---

## 2. Technology Stack & Key Dependencies

- **Python Version:** `>=3.10`
- **Inference Server:** `Ollama` running model `qwen2.5:3b` (or `llama3.2:3b`).
- **Structured LLM Output:** `instructor` (with Ollama client integration).
- **Embeddings Engine:** `fastembed` (Model: `BAAI/bge-small-en-v1.5`).
- **Vector Database:** `lancedb` (embedded file-based storage in `.lancedb/`).
- **User Interface:** `streamlit` for the single-page dashboard.
- **Data Models:** `pydantic` v2.

---

## 3. Architecture Overview & Components

```text
Unison/
├── AGENTS.md              # Instructions & context for AI coding agents
├── README.md              # User documentation
├── .gitignore             # Ignored directories and local database files
├── requirements.txt       # Dependencies
├── src/
│   ├── __init__.py
│   ├── ingestion/         # Adapters for notifications, SMS, emails
│   ├── storage/           # LanceDB vector storage and embedding logic
│   ├── engine/            # Ollama + Instructor summarization pipeline
│   └── models/            # Pydantic schema definitions
└── app.py                 # Streamlit UI dashboard

---

## 4. Core Pydantic Schemas

When writing logic in src/models/ or src/engine/, adhere to these standard schemas:

from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import datetime

class IngestedNotification(BaseModel):
    id: str = Field(description="Unique identifier for the incoming message")
    source: Literal["SMS", "WhatsApp", "Email", "System"] = Field(...)
    sender: str = Field(description="Sender name, number, or email address")
    content: str = Field(description="Raw text payload")
    timestamp: datetime = Field(default_factory=datetime.now)

class UnisonSummary(BaseModel):
    source: str
    priority: Literal["High", "Low", "Noise"] = Field(description="Priority classification")
    category: str = Field(description="Topic tag, e.g., Work, Personal, OTP, Social")
    summary: str = Field(description="1-sentence actionable summary")
    action_required: bool = Field(description="True if user needs to respond or take action")
    suggested_reply: Optional[str] = Field(default=None, description="Optional quick draft reply")

---

## 5. Development Rules for Coding Agents

Always handle model offline failures gracefully: If Ollama is not running, throw a clean, human-readable error or UI alert asking the user to run ollama serve.

Do not invent mock data in production paths: Keep mock data generators isolated in tests or debug utilities.

Keep Streamlit code clean: Avoid heavy computations directly inside Streamlit render loops. Pass heavy tasks to background workers or cache them using @st.cache_resource / @st.cache_data.

Async Usage: Use Python asyncio for non-blocking file/db reads and parallel batch ingestion.