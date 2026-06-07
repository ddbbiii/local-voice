from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

from .adapters.base import ASRBackend, LLMBackend, TTSBackend
from .events import EventBus
from .memory_service import MemoryService
from .models import AssistantTurnResult, ConversationMessage
from .prompt import build_prompt, extract_streamable_reply, parse_assistant_output


class ConversationOrchestrator:
    def __init__(
        self,
        asr_backend: ASRBackend,
        llm_backend: LLMBackend,
        tts_backend: TTSBackend,
        memory_service: MemoryService,
        event_bus: EventBus,
        conversations_dir: Path,
    ) -> None:
        self.asr_backend = asr_backend
        self.llm_backend = llm_backend
        self.tts_backend = tts_backend
        self.memory_service = memory_service
        self.event_bus = event_bus
        self.conversations_dir = conversations_dir
        self.conversations_dir.mkdir(parents=True, exist_ok=True)
        self._session_lock = asyncio.Lock()
        self._active_turns: dict[str, _ActiveTurn] = {}

    async def emit(self, session_id: str, event_type: str, **payload) -> None:
        await self.event_bus.emit({"type": event_type, "session_id": session_id, **payload})

    async def start_voice_turn(self, session_id: str, turn_id: str, audio_path: Path) -> None:
        cancel_signal = threading.Event()
        new_task = asyncio.create_task(self.process_voice_turn(session_id, turn_id, audio_path, cancel_signal))

        old_task: asyncio.Task | None = None
        async with self._session_lock:
            previous = self._active_turns.get(session_id)
            self._active_turns[session_id] = _ActiveTurn(turn_id=turn_id, cancel_signal=cancel_signal, task=new_task)
            if previous:
                previous.cancel_signal.set()
                self.tts_backend.cancel_current()
                old_task = previous.task

        if old_task:
            try:
                await asyncio.wait_for(old_task, timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass

    async def process_voice_turn(
        self, session_id: str, turn_id: str, audio_path: Path, cancel_signal: threading.Event
    ) -> None:
        raw_assistant_output = ""
        reply_emitted = ""
        sentence_buffer = ""
        first_sentence_started = False

        try:
            if cancel_signal.is_set():
                return
            await self.emit(session_id, "session_state", turn_id=turn_id, state="transcribing")
            asr_result = await self.asr_backend.transcribe(str(audio_path))
            if cancel_signal.is_set():
                return
            await self.emit(session_id, "asr_partial", turn_id=turn_id, text=asr_result.raw_text)
            await self.emit(session_id, "asr_final", turn_id=turn_id, text=asr_result.raw_text)
            await self.emit(session_id, "session_state", turn_id=turn_id, state="recalling")
            recall = await self.memory_service.search(asr_result.raw_text)
            if cancel_signal.is_set():
                return
            await self.emit(
                session_id, "memory_recall", turn_id=turn_id, recall=recall.model_dump(mode="json")
            )

            await self.emit(session_id, "session_state", turn_id=turn_id, state="thinking")
            recent_messages = self._load_recent_messages(session_id, limit=6)
            prompt = build_prompt(session_id, asr_result.raw_text, recall.records, recent_messages)

            await self._append_log(
                session_id,
                ConversationMessage(
                    id=str(uuid.uuid4()),
                    role="user",
                    text=asr_result.raw_text,
                    timestamp=datetime.utcnow(),
                    status="final",
                ),
            )

            async for token in self.llm_backend.stream(prompt, cancel_signal=cancel_signal):
                if cancel_signal.is_set():
                    return
                raw_assistant_output += token
                streamable_reply = extract_streamable_reply(raw_assistant_output)
                if not streamable_reply:
                    continue

                if not streamable_reply.startswith(reply_emitted):
                    continue

                new_chunk = streamable_reply[len(reply_emitted) :]
                if not new_chunk:
                    continue

                reply_emitted = streamable_reply
                await self.emit(session_id, "llm_token", turn_id=turn_id, token=new_chunk)

                for sentence in self._collect_sentences(new_chunk, sentence_buffer):
                    sentence_buffer = sentence["remainder"]
                    completed_sentence = sentence["completed"]
                    if not completed_sentence:
                        continue
                    if cancel_signal.is_set():
                        return
                    await self.emit(session_id, "llm_sentence", turn_id=turn_id, sentence=completed_sentence)
                    await self.emit(session_id, "tts_started", turn_id=turn_id, sentence=completed_sentence)
                    if not first_sentence_started:
                        first_sentence_started = True
                    async for _audio in self.tts_backend.synthesize_stream(
                        completed_sentence, "default"
                    ):
                        if cancel_signal.is_set():
                            return
                        await asyncio.sleep(0)
                    await self.emit(session_id, "tts_finished", turn_id=turn_id, sentence=completed_sentence)

            if cancel_signal.is_set():
                return
            assistant_turn = parse_assistant_output(raw_assistant_output)

            if sentence_buffer.strip():
                tail_sentence = sentence_buffer.strip()
                if cancel_signal.is_set():
                    return
                await self.emit(session_id, "llm_sentence", turn_id=turn_id, sentence=tail_sentence)
                await self.emit(session_id, "tts_started", turn_id=turn_id, sentence=tail_sentence)
                async for _audio in self.tts_backend.synthesize_stream(tail_sentence, "default"):
                    if cancel_signal.is_set():
                        return
                    await asyncio.sleep(0)
                await self.emit(session_id, "tts_finished", turn_id=turn_id, sentence=tail_sentence)

            if cancel_signal.is_set():
                return
            await self.emit(
                session_id,
                "assistant_turn",
                turn_id=turn_id,
                turn=assistant_turn.model_dump(mode="json"),
            )

            await self._append_log(
                session_id,
                ConversationMessage(
                    id=str(uuid.uuid4()),
                    role="assistant",
                    text=assistant_turn.reply_text,
                    timestamp=datetime.utcnow(),
                    status="final",
                ),
            )

            await self.emit(session_id, "turn_complete", turn_id=turn_id)
            await self.emit(session_id, "session_state", turn_id=turn_id, state="idle")
            asyncio.create_task(
                self._write_memories_async(
                    session_id=session_id,
                    turn_id=turn_id,
                    user_text=asr_result.raw_text,
                    assistant_turn=assistant_turn,
                )
            )
        except Exception as exc:
            if not cancel_signal.is_set():
                await self.emit(session_id, "error", turn_id=turn_id, message=str(exc))
                await self.emit(session_id, "session_state", turn_id=turn_id, state="error")
        finally:
            await self._clear_active_turn(session_id, turn_id)

    def _collect_sentences(self, chunk: str, current_buffer: str) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        buffer = current_buffer
        for char in chunk:
            buffer += char
            if char in ".!?":
                completed = buffer.strip()
                if completed:
                    results.append({"completed": completed, "remainder": ""})
                buffer = ""
        if not results:
            return [{"completed": "", "remainder": buffer}]

        results[-1]["remainder"] = buffer
        return results

    async def _append_log(self, session_id: str, message: ConversationMessage) -> None:
        target = self.conversations_dir / f"{session_id}.jsonl"
        payload = message.model_dump(mode="json")
        target.write_text(
            (target.read_text(encoding="utf-8") if target.exists() else "")
            + json.dumps(payload, ensure_ascii=False, default=str)
            + "\n",
            encoding="utf-8",
        )

    def _load_recent_messages(self, session_id: str, limit: int = 6) -> list[ConversationMessage]:
        target = self.conversations_dir / f"{session_id}.jsonl"
        if not target.exists():
            return []

        messages: list[ConversationMessage] = []
        try:
            for line in target.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                    messages.append(ConversationMessage.model_validate(payload))
                except Exception:
                    continue
        except OSError:
            return []

        return messages[-limit:]

    async def _write_memories_async(
        self,
        session_id: str,
        turn_id: str,
        user_text: str,
        assistant_turn: AssistantTurnResult,
    ) -> None:
        try:
            written = await self.memory_service.write_turn_memories(
                session_id,
                user_text,
                assistant_turn,
            )
            await self.emit(
                session_id,
                "memory_written",
                turn_id=turn_id,
                records=[record.model_dump(mode="json") for record in written],
            )
        except Exception:
            return

    async def _clear_active_turn(self, session_id: str, turn_id: str) -> None:
        async with self._session_lock:
            active = self._active_turns.get(session_id)
            if active and active.turn_id == turn_id:
                self._active_turns.pop(session_id, None)


class _ActiveTurn:
    def __init__(self, turn_id: str, cancel_signal: threading.Event, task: asyncio.Task) -> None:
        self.turn_id = turn_id
        self.cancel_signal = cancel_signal
        self.task = task
