from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
import threading

from ..models import ASRResult, MemoryRecord, PromptBundle


class ASRBackend(ABC):
    @abstractmethod
    async def transcribe(self, audio_path: str) -> ASRResult:
        raise NotImplementedError


class LLMBackend(ABC):
    @abstractmethod
    async def stream(
        self, prompt: PromptBundle, cancel_signal: threading.Event | None = None
    ) -> AsyncIterator[str]:
        raise NotImplementedError

    @abstractmethod
    async def complete(
        self, messages: list[dict[str, str]], cancel_signal: threading.Event | None = None
    ) -> str:
        raise NotImplementedError


class EmbeddingBackend(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class VectorIndexBackend(ABC):
    @abstractmethod
    async def upsert(self, records: list[MemoryRecord]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def search(self, query: str, top_k: int) -> list[MemoryRecord]:
        raise NotImplementedError

    @abstractmethod
    async def rebuild(self, records: list[MemoryRecord]) -> None:
        raise NotImplementedError


class TTSBackend(ABC):
    @abstractmethod
    async def synthesize_stream(self, text: str, voice_id: str) -> AsyncIterator[bytes]:
        raise NotImplementedError

    def cancel_current(self) -> None:
        return None
