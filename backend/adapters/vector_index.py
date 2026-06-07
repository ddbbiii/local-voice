from __future__ import annotations

import json
import math
from pathlib import Path

from ..models import MemoryRecord
from .base import EmbeddingBackend, VectorIndexBackend


class JsonVectorIndexBackend(VectorIndexBackend):
    def __init__(self, index_path: Path, embedding_backend: EmbeddingBackend) -> None:
        self.index_path = index_path / "index.json"
        self.embedding_backend = embedding_backend
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, dict] = {}
        if self.index_path.exists():
            self._records = json.loads(self.index_path.read_text(encoding="utf-8"))

    def _persist(self) -> None:
        self.index_path.write_text(
            json.dumps(self._records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def upsert(self, records: list[MemoryRecord]) -> None:
        if not records:
            return
        vectors = await self.embedding_backend.embed([record.text for record in records])
        for record, vector in zip(records, vectors, strict=True):
            self._records[record.memory_id] = {
                "record": record.model_dump(mode="json"),
                "vector": vector,
            }
        self._persist()

    async def search(self, query: str, top_k: int) -> list[MemoryRecord]:
        if not query or not self._records:
            return []
        [query_vector] = await self.embedding_backend.embed([query])
        scored = []
        for payload in self._records.values():
            vector = payload["vector"]
            score = sum(left * right for left, right in zip(query_vector, vector, strict=False))
            scored.append((score, MemoryRecord.model_validate(payload["record"])))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in scored[:top_k]]

    async def rebuild(self, records: list[MemoryRecord]) -> None:
        self._records = {}
        self._persist()
        await self.upsert(records)
