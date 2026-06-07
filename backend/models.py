from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


SessionState = Literal[
    "idle",
    "recording",
    "transcribing",
    "recalling",
    "thinking",
    "generating",
    "speaking",
    "error",
]


class ConversationMessage(BaseModel):
    id: str
    role: Literal["user", "assistant", "system"]
    text: str
    timestamp: datetime
    status: Literal["streaming", "final"]


class ASRResult(BaseModel):
    raw_text: str
    language: str = "en"
    segments: list[str] = Field(default_factory=list)
    duration_ms: int = 0


class MemoryRecord(BaseModel):
    memory_id: str
    category: Literal["constraints", "profile", "mistakes", "topics", "notes"]
    text: str
    salience: float = 0.5
    created_at: datetime
    updated_at: datetime
    source_turn_id: str


class MemoryRecallResult(BaseModel):
    records: list[MemoryRecord] = Field(default_factory=list)
    summary: str = ""
    source_ids: list[str] = Field(default_factory=list)


class MemorySnapshot(BaseModel):
    records: list[MemoryRecord] = Field(default_factory=list)
    grouped: dict[str, list[MemoryRecord]] = Field(default_factory=dict)
    summary: str = ""
    counts: dict[str, int] = Field(default_factory=dict)
    memory_file_path: str = ""
    memory_root_path: str = ""
    vector_index_provider: str = "json"


class PromptBundle(BaseModel):
    system_prompt: str
    memories: list[MemoryRecord] = Field(default_factory=list)
    recent_messages: list[ConversationMessage] = Field(default_factory=list)
    user_text: str
    session_id: str


class AssistantTurnResult(BaseModel):
    reply_text: str
    native_rewrite: str = ""
    optional_tip: str = ""
    raw_response: str = ""


class ResourceUsageSnapshot(BaseModel):
    cpu_percent: float | None = None
    memory_mb: float | None = None
    gpu_util_percent: float | None = None
    gpu_memory_mb: float | None = None
    gpu_scope: Literal["process", "device", "unavailable"] = "unavailable"
    sampled_at: datetime


class AppSettings(BaseModel):
    audio_input_device: str = "default"
    audio_output_device: str = "default"
    push_to_talk_hotkey: str = "Mouse Hold"
    llm_provider: Literal["auto", "local", "api"] = "auto"
    llm_model_path: str = r"E:\program\models\llm\qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"
    llm_api_base: str = ""
    llm_api_model: str = ""
    llm_api_key: str = ""
    tts_voice_id: str = "default"
    memory_file_path: str = ""
    memory_root_path: str = ""
    backend_mode: Literal["auto", "mock", "real"] = "auto"
    whisper_model_name: str = r"E:\program\models\asr\faster-whisper-medium.en"
    embedding_model_name: str = "bge-m3"
    llama_context_size: int = 4096
    llama_gpu_layers: int = 35
    coaching_mode: Literal["english_coach"] = "english_coach"
    reply_language: Literal["english_primary"] = "english_primary"
    correction_mode: Literal["reply_then_correct"] = "reply_then_correct"
    explanation_mode: Literal["minimal_chinese"] = "minimal_chinese"


class SystemStatus(BaseModel):
    backend_mode: str
    llm_available: bool
    asr_available: bool
    tts_available: bool
    vector_index_available: bool
    vector_index_provider: str = "json"
    llm_provider: str = "auto"
    llm_resolved_provider: str = "local"
    llm_error: str | None = None
    asr_error: str | None = None
    tts_error: str | None = None


class AssistantEvent(BaseModel):
    type: str
    session_id: str
    payload: dict
