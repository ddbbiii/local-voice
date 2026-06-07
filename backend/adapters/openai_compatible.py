from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import AsyncIterator
from typing import Any

from ..prompt import format_memory_context
from .base import LLMBackend


def _build_messages(prompt) -> list[dict[str, str]]:
    memory_text = format_memory_context(prompt.memories)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": prompt.system_prompt},
        {
            "role": "system",
            "content": (
                "Relevant learning memory. Treat this as durable coaching context, "
                "not as user text to repeat:\n"
                f"{memory_text}"
            ),
        },
        {
            "role": "system",
            "content": (
                "The recent turns below are part of the current conversation. "
                "Use them as short-term context. If the user asks about the immediately previous message, "
                "answer from those turns instead of saying you do not remember."
            ),
        },
    ]

    for message in prompt.recent_messages:
        if message.role in {"user", "assistant"} and message.text.strip():
            messages.append({"role": message.role, "content": message.text})

    messages.append({"role": "user", "content": prompt.user_text})
    return messages


class OpenAICompatibleAdapter(LLMBackend):
    def __init__(
        self,
        api_base: str,
        model: str,
        api_key: str | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.api_key = api_key or _resolve_api_key()
        self.timeout_s = timeout_s
        self.available = bool(self.api_key and self.api_base and self.model)
        self.last_error: str | None = None if self.available else self._build_unavailable_reason()

    async def stream(
        self, prompt, cancel_signal: threading.Event | None = None
    ) -> AsyncIterator[str]:
        if not self.available:
            raise RuntimeError(self.last_error or "Cloud LLM is not configured.")

        import httpx

        payload = {
            "model": self.model,
            "messages": _build_messages(prompt),
            "temperature": 0.2,
            "stream": True,
            "thinking": {"type": "disabled"},
        }

        headers = _build_headers(self.api_key)
        url = f"{self.api_base}/chat/completions"

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for raw_line in response.aiter_lines():
                    if cancel_signal and cancel_signal.is_set():
                        break
                    if not raw_line or not raw_line.startswith("data:"):
                        continue
                    data = raw_line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        parsed = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = _extract_delta_content(parsed)
                    if delta:
                        yield delta

    async def complete(
        self, messages: list[dict[str, str]], cancel_signal: threading.Event | None = None
    ) -> str:
        if not self.available:
            raise RuntimeError(self.last_error or "Cloud LLM is not configured.")
        if cancel_signal and cancel_signal.is_set():
            return ""

        import httpx

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,
            "stream": False,
            "thinking": {"type": "disabled"},
        }

        headers = _build_headers(self.api_key)
        url = f"{self.api_base}/chat/completions"

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            parsed = response.json()
            return _extract_message_content(parsed)

    def _build_unavailable_reason(self) -> str:
        missing: list[str] = []
        if not self.api_key:
            missing.append("API key")
        if not self.api_base:
            missing.append("API base")
        if not self.model:
            missing.append("API model")
        return f"Cloud LLM unavailable: missing {', '.join(missing)}."


def _resolve_api_key() -> str:
    return (
        os.environ.get("ASSISTANT_LLM_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or ""
    )


def _build_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _extract_delta_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    delta = choices[0].get("delta", {})
    if isinstance(delta, dict):
        content = delta.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            )
    return ""


def _extract_message_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message", {})
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return ""
