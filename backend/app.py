from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .adapters.chroma import ChromaAdapter
from .adapters.embedding import HashEmbeddingBackend
from .adapters.llama_cpp import LlamaCppAdapter
from .adapters.openai_compatible import OpenAICompatibleAdapter
from .adapters.tts import MockTTSAdapter, WindowsNativeTTSAdapter
from .adapters.whisper import WhisperAdapter
from .config import AppPaths, load_settings
from .events import EventBus
from .memory_service import MemoryService
from .memory_store import ModularMarkdownMemoryStore
from .models import MemorySnapshot
from .services import ConversationOrchestrator
from .telemetry import ResourceMonitor


paths = AppPaths.resolve()
settings = load_settings(paths)
event_bus = EventBus()
embedding_backend = HashEmbeddingBackend()
vector_backend = ChromaAdapter(paths.memory_index_dir, embedding_backend)
asr_backend = WhisperAdapter(settings.whisper_model_name)
llm_backend = None
resolved_llm_provider = settings.llm_provider


def _build_llm_backend():
    global resolved_llm_provider
    provider = settings.llm_provider
    api_base = settings.llm_api_base or os.environ.get("ASSISTANT_LLM_API_BASE", "")
    api_model = settings.llm_api_model or os.environ.get("ASSISTANT_LLM_API_MODEL", "")
    api_key = (
        settings.llm_api_key
        or os.environ.get("ASSISTANT_LLM_API_KEY", "")
        or os.environ.get("OPENAI_API_KEY", "")
        or os.environ.get("DEEPSEEK_API_KEY", "")
    )

    if provider == "auto":
        provider = "api" if api_base and api_model and api_key else "local"
    resolved_llm_provider = provider

    if provider == "api":
        return OpenAICompatibleAdapter(
            api_base=api_base,
            model=api_model,
            api_key=api_key,
        )

    return LlamaCppAdapter(
        settings.llm_model_path,
        settings.llama_context_size,
        settings.llama_gpu_layers,
    )


llm_backend = _build_llm_backend()
memory_store = ModularMarkdownMemoryStore(paths.memory_modules_dir, paths.memory_file)
memory_service = MemoryService(
    memory_store,
    vector_backend,
    llm_backend,
    paths.memory_observations_file,
)
tts_backend = WindowsNativeTTSAdapter()
if not tts_backend.available:
    tts_backend = MockTTSAdapter()
resource_monitor = ResourceMonitor()
orchestrator = ConversationOrchestrator(
    asr_backend=asr_backend,
    llm_backend=llm_backend,
    tts_backend=tts_backend,
    memory_service=memory_service,
    event_bus=event_bus,
    conversations_dir=paths.conversations_dir,
)


def _vector_index_provider() -> str:
    return "chroma" if getattr(vector_backend, "available", False) else "json"


@asynccontextmanager
async def lifespan(_: FastAPI):
    await memory_service.initialize()
    yield


app = FastAPI(title="Local Voice Memory Assistant", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    mode = settings.backend_mode
    if mode == "auto":
        mode = "real" if llm_backend.available and asr_backend.available else "mock"
    return {
        "status": {
            "backend_mode": mode,
            "llm_available": llm_backend.available,
            "asr_available": asr_backend.available,
            "tts_available": getattr(tts_backend, "available", True),
            "vector_index_available": True,
            "vector_index_provider": _vector_index_provider(),
            "llm_provider": settings.llm_provider,
            "llm_resolved_provider": resolved_llm_provider,
            "llm_error": getattr(llm_backend, "last_error", None),
            "asr_error": getattr(asr_backend, "last_error", None),
            "tts_error": getattr(tts_backend, "last_error", None),
        }
    }


@app.get("/api/settings")
async def get_settings() -> dict:
    payload = settings.model_dump(mode="json")
    payload.pop("llm_api_key", None)
    payload["memory_file_path"] = str(paths.memory_file)
    payload["memory_root_path"] = str(paths.memory_modules_dir)
    return payload


@app.get("/api/resource-usage")
async def get_resource_usage() -> dict:
    return {"usage": resource_monitor.sample().model_dump(mode="json")}


@app.get("/api/memory")
async def get_memory_snapshot() -> dict:
    snapshot = memory_service.snapshot(
        memory_file_path=str(paths.memory_file),
        memory_root_path=str(paths.memory_modules_dir),
        vector_provider=_vector_index_provider(),
    )
    return {
        "memory": MemorySnapshot.model_validate(snapshot).model_dump(mode="json")
    }


@app.post("/api/memory/rebuild")
async def rebuild_memory_index() -> dict:
    await memory_service.initialize()
    snapshot = memory_service.snapshot(
        memory_file_path=str(paths.memory_file),
        memory_root_path=str(paths.memory_modules_dir),
        vector_provider=_vector_index_provider(),
    )
    return {
        "ok": True,
        "memory": MemorySnapshot.model_validate(snapshot).model_dump(mode="json"),
    }


@app.post("/api/voice-turn")
async def submit_voice_turn(
    session_id: str = Form(...), turn_id: str = Form(...), audio: UploadFile = File(...)
) -> dict:
    target = paths.cache_dir / f"{session_id}-{audio.filename or 'utterance.wav'}"
    target.write_bytes(await audio.read())
    await orchestrator.start_voice_turn(session_id, turn_id, target)
    return {"session_id": session_id, "turn_id": turn_id, "accepted": True}


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket) -> None:
    await event_bus.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await event_bus.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765, reload=False)
