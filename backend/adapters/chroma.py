from __future__ import annotations

from typing import Any

from ..models import MemoryRecord
from .base import EmbeddingBackend, VectorIndexBackend
from .vector_index import JsonVectorIndexBackend


class ChromaAdapter(VectorIndexBackend):
    def __init__(self, index_dir, embedding_backend: EmbeddingBackend) -> None:
        self.embedding_backend = embedding_backend
        self._fallback = JsonVectorIndexBackend(index_dir, embedding_backend)
        self.available = False
        self._collection: Any = None
        try:
            import chromadb

            client = chromadb.PersistentClient(path=str(index_dir))
            self._collection = client.get_or_create_collection(
                name="assistant_memory_index"
            )
            self.available = True
        except Exception:
            self._collection = None
            self.available = False

    async def upsert(self, records: list[MemoryRecord]) -> None:
        if not self._collection:
            await self._fallback.upsert(records)
            return
        if not records:
            return
        embeddings = await self.embedding_backend.embed([record.text for record in records])
        self._collection.upsert(
            ids=[record.memory_id for record in records],
            documents=[record.text for record in records],
            embeddings=embeddings,
            metadatas=[record.model_dump(mode="json") for record in records],
        )

    async def search(self, query: str, top_k: int) -> list[MemoryRecord]:
        if not self._collection:
            return await self._fallback.search(query, top_k)
        if not query:
            return []
        [embedding] = await self.embedding_backend.embed([query])
        result = self._collection.query(query_embeddings=[embedding], n_results=top_k)
        metadatas = result.get("metadatas", [[]])[0]
        return [MemoryRecord.model_validate(metadata) for metadata in metadatas]

    async def rebuild(self, records: list[MemoryRecord]) -> None:
        if not self._collection:
            await self._fallback.rebuild(records)
            return
        existing = self._collection.get()
        ids = existing.get("ids", [])
        if ids:
            self._collection.delete(ids=ids)
        await self.upsert(records)
