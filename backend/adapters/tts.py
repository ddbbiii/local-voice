from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator

from .base import TTSBackend


class MockTTSAdapter(TTSBackend):
    available = True

    async def synthesize_stream(self, text: str, voice_id: str) -> AsyncIterator[bytes]:
        chunks = [text[i : i + 12] for i in range(0, len(text), 12)] or [text]
        for chunk in chunks:
            await asyncio.sleep(0.04)
            yield chunk.encode("utf-8")


class WindowsNativeTTSAdapter(TTSBackend):
    def __init__(self) -> None:
        self.available = False
        self.last_error: str | None = None
        self._engine = None
        self._engine_lock = threading.RLock()
        try:
            import pyttsx3  # noqa: F401
            import pythoncom  # noqa: F401

            self.available = True
        except Exception as exc:  # pragma: no cover - runtime dependent
            self.last_error = str(exc)

    async def synthesize_stream(self, text: str, voice_id: str) -> AsyncIterator[bytes]:
        if not self.available or not text.strip():
            yield text.encode("utf-8")
            return

        await asyncio.to_thread(self._speak_blocking, text, voice_id)
        yield text.encode("utf-8")

    def cancel_current(self) -> None:
        if not self.available or self._engine is None:
            return
        try:
            self._engine.stop()
        except Exception:
            return

    def _speak_blocking(self, text: str, voice_id: str) -> None:
        import pyttsx3
        import pythoncom

        pythoncom.CoInitialize()
        engine = None
        try:
            with self._engine_lock:
                engine = pyttsx3.init("sapi5")
                self._engine = engine
                engine.setProperty("rate", 180)
                engine.setProperty("volume", 1.0)
                selected_voice = self._resolve_voice(engine, voice_id)
                if selected_voice:
                    engine.setProperty("voice", selected_voice)
                engine.say(text)
                engine.runAndWait()
        except Exception as exc:  # pragma: no cover - runtime dependent
            self.last_error = str(exc)
        finally:
            with self._engine_lock:
                if engine is self._engine:
                    self._engine = None
            pythoncom.CoUninitialize()

    def _resolve_voice(self, engine, voice_id: str) -> str | None:
        if engine is None:
            return None

        voices = engine.getProperty("voices") or []
        requested = (voice_id or "").strip().lower()
        preferred_terms = ("zira", "aria", "jenny", "david", "en-")

        if requested:
            for voice in voices:
                name = getattr(voice, "name", "").lower()
                identifier = getattr(voice, "id", "").lower()
                if requested in name or requested in identifier:
                    return getattr(voice, "id", None)

        for term in preferred_terms:
            for voice in voices:
                name = getattr(voice, "name", "").lower()
                identifier = getattr(voice, "id", "").lower()
                if term in name or term in identifier:
                    return getattr(voice, "id", None)

        return getattr(voices[0], "id", None) if voices else None
