from __future__ import annotations

import hashlib
import math

from .base import EmbeddingBackend


class HashEmbeddingBackend(EmbeddingBackend):
    def __init__(self, dimensions: int = 128) -> None:
        self.dimensions = dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            buckets = [0.0] * self.dimensions
            for token in text.lower().split():
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                for index, byte in enumerate(digest[: self.dimensions]):
                    buckets[index] += (byte / 255.0) - 0.5
            norm = math.sqrt(sum(value * value for value in buckets)) or 1.0
            vectors.append([value / norm for value in buckets])
        return vectors
