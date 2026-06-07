from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._connections: set[Any] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: Any) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: Any) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    async def emit(self, payload: dict) -> None:
        stale = []
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        async with self._lock:
            for connection in self._connections:
                try:
                    await connection.send_text(serialized)
                except Exception:
                    stale.append(connection)
            for connection in stale:
                self._connections.discard(connection)
