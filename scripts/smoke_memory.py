from __future__ import annotations

import asyncio
import tempfile
import sys
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.adapters.base import LLMBackend, VectorIndexBackend
from backend.memory_service import MemoryService
from backend.memory_store import ModularMarkdownMemoryStore
from backend.models import AssistantTurnResult, MemoryRecord, PromptBundle


class FakeLLMBackend(LLMBackend):
    available = True
    last_error = None

    async def stream(self, prompt: PromptBundle, cancel_signal=None) -> AsyncIterator[str]:
        if False:
            yield ""

    async def complete(self, messages: list[dict[str, str]], cancel_signal=None) -> str:
        return (
            '[{"category":"profile","text":"Goal: job interview speaking practice",'
            '"salience":0.82}]'
        )


class FakeVectorIndexBackend(VectorIndexBackend):
    def __init__(self) -> None:
        self.records: list[MemoryRecord] = []
        self.rebuild_count = 0

    async def upsert(self, records: list[MemoryRecord]) -> None:
        by_id = {record.memory_id: record for record in self.records}
        for record in records:
            by_id[record.memory_id] = record
        self.records = list(by_id.values())

    async def search(self, query: str, top_k: int) -> list[MemoryRecord]:
        lowered = query.lower()
        ranked = [
            record
            for record in self.records
            if any(token in record.text.lower() for token in lowered.split())
        ]
        return (ranked or self.records)[:top_k]

    async def rebuild(self, records: list[MemoryRecord]) -> None:
        self.records = list(records)
        self.rebuild_count += 1


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="assistant-memory-") as tmp:
        root = Path(tmp)
        store = ModularMarkdownMemoryStore(root / "modules", root / "memory.md")
        vector = FakeVectorIndexBackend()
        service = MemoryService(store, vector, FakeLLMBackend(), root / "observations.json")

        await service.initialize()
        user_text = (
            "I'm intermediate. I want to practice job interview small talk. "
            "Please correct me strictly, use minimal Chinese, and speak slower. "
            "I very like practicing English."
        )
        assistant_turn = AssistantTurnResult(
            reply_text="That is a useful goal.",
            native_rewrite="I really like practicing English.",
            optional_tip='Say "I really like", not "I very like".',
            raw_response="",
        )

        written = await service.write_turn_memories("smoke-turn", user_text, assistant_turn)
        records = store.load_all()
        snapshot = service.snapshot(
            memory_file_path=str(root / "memory.md"),
            memory_root_path=str(root / "modules"),
            vector_provider="fake",
        )
        recall = await service.search("Can you correct my job interview English?", top_k=5)

        assert written, "Expected durable memory records to be written."
        assert records, "Expected Markdown memory records to reload."
        assert vector.records, "Expected vector index records to be upserted."
        assert vector.rebuild_count >= 1, "Expected initial index rebuild."
        assert snapshot["counts"]["constraints"] >= 2, "Expected coaching constraints."
        assert snapshot["counts"]["profile"] >= 1, "Expected learner profile memory."
        assert snapshot["counts"]["mistakes"] >= 1, "Expected mistake pattern memory."
        assert recall.records, "Expected memory recall records."

        ids_before = sorted(record.memory_id for record in records)
        reloaded_store = ModularMarkdownMemoryStore(root / "modules", root / "memory.md")
        ids_after = sorted(record.memory_id for record in reloaded_store.load_all())
        assert ids_before == ids_after, "Expected stable memory IDs after reload."

        now = datetime.utcnow().isoformat(timespec="seconds")
        print(f"[{now}] Memory smoke passed: {len(records)} records, {len(recall.records)} recalled.")


if __name__ == "__main__":
    asyncio.run(main())
