from __future__ import annotations

import asyncio
import ctypes
import os
import threading
from collections.abc import AsyncIterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from ..models import PromptBundle
from ..prompt import format_memory_context
from .base import LLMBackend


def _build_messages(prompt: PromptBundle) -> list[dict[str, str]]:
    memory_text = format_memory_context(prompt.memories)
    messages = [
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
                "The recent turns below are short-term context. If the learner asks about the "
                "immediately previous message, answer from these turns instead of saying you do not remember."
            ),
        },
    ]
    for message in prompt.recent_messages:
        if message.role in {"user", "assistant"} and message.text.strip():
            messages.append({"role": message.role, "content": message.text})
    messages.append({"role": "user", "content": prompt.user_text})
    return messages


class LlamaCppAdapter(LLMBackend):
    def __init__(self, model_path: str, n_ctx: int, n_gpu_layers: int) -> None:
        self.model_path = model_path
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        self.available = False
        self._llm = None
        self.last_error: str | None = None
        self._inference_lock = asyncio.Lock()
        self._abort_callback = None
        if not model_path:
            self.last_error = "Local LLM model path is empty."
            return
        if not Path(model_path).exists():
            self.last_error = f"Local LLM model file not found: {model_path}"
            return
        try:
            from llama_cpp import Llama
            import llama_cpp.llama_cpp as llama_cpp_native

            errors: list[str] = []
            for gpu_layers in _build_gpu_layer_attempts(n_gpu_layers):
                try:
                    with _disable_extra_bufts(llama_cpp_native):
                        self._llm = Llama(
                            model_path=model_path,
                            n_ctx=n_ctx,
                            n_gpu_layers=gpu_layers,
                            n_threads=_resolve_thread_count(),
                            verbose=False,
                        )
                    self.n_gpu_layers = gpu_layers
                    self.available = True
                    self.last_error = None
                    break
                except Exception as exc:
                    self._llm = None
                    errors.append(f"n_gpu_layers={gpu_layers}: {exc}")
            if not self.available:
                self.last_error = "Local LLM load failed. " + " | ".join(errors)
        except Exception as exc:
            self._llm = None
            self.available = False
            self.last_error = str(exc)

    async def stream(
        self, prompt: PromptBundle, cancel_signal: threading.Event | None = None
    ) -> AsyncIterator[str]:
        if not self._llm:
            fallback = (
                "<REPLY>I heard you clearly. The local coach is still in mock mode right now.</REPLY>"
                "<NATIVE_REWRITE></NATIVE_REWRITE><TIP></TIP>"
            )
            for token in fallback:
                await asyncio.sleep(0.01)
                yield token
            return

        async with self._inference_lock:
            messages = _build_messages(prompt)
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[object] = asyncio.Queue()
            sentinel = object()

            def worker() -> None:
                try:
                    with self._abortable_context(cancel_signal):
                        stream = self._llm.create_chat_completion(messages=messages, stream=True)
                        for chunk in stream:
                            if cancel_signal and cancel_signal.is_set():
                                break
                            delta = chunk["choices"][0]["delta"].get("content", "")
                            if delta:
                                loop.call_soon_threadsafe(queue.put_nowait, delta)
                except Exception as exc:
                    if not (cancel_signal and cancel_signal.is_set()):
                        loop.call_soon_threadsafe(queue.put_nowait, exc)
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, sentinel)

            threading.Thread(target=worker, daemon=True).start()

            while True:
                item = await queue.get()
                if item is sentinel:
                    break
                if isinstance(item, Exception):
                    raise item
                if cancel_signal and cancel_signal.is_set():
                    break
                yield cast(str, item)

    async def complete(
        self, messages: list[dict[str, str]], cancel_signal: threading.Event | None = None
    ) -> str:
        if not self._llm:
            return "[]"

        loop = asyncio.get_running_loop()

        def worker() -> str:
            with self._abortable_context(cancel_signal):
                response = self._llm.create_chat_completion(messages=messages, stream=False)
            message = response["choices"][0]["message"]["content"]
            return cast(str, message or "")

        async with self._inference_lock:
            return await loop.run_in_executor(None, worker)

    @contextmanager
    def _abortable_context(self, cancel_signal: threading.Event | None):
        if not self._llm:
            yield
            return

        try:
            import llama_cpp.llama_cpp as llama_cpp_native
        except Exception:
            yield
            return

        ctx = getattr(getattr(self._llm, "_ctx", None), "ctx", None)
        if ctx is None:
            yield
            return

        callback = llama_cpp_native.ggml_abort_callback(
            lambda _data: bool(cancel_signal and cancel_signal.is_set())
        )
        self._abort_callback = callback
        llama_cpp_native.llama_set_abort_callback(ctx, callback, ctypes.c_void_p())
        try:
            yield
        finally:
            null_callback = llama_cpp_native.ggml_abort_callback(lambda _data: False)
            self._abort_callback = null_callback
            llama_cpp_native.llama_set_abort_callback(ctx, null_callback, ctypes.c_void_p())


@contextmanager
def _disable_extra_bufts(llama_cpp_native):
    original = llama_cpp_native.llama_model_default_params

    def patched():
        params = original()
        if hasattr(params, "use_extra_bufts"):
            params.use_extra_bufts = False
        return params

    llama_cpp_native.llama_model_default_params = patched
    try:
        yield
    finally:
        llama_cpp_native.llama_model_default_params = original


def _build_gpu_layer_attempts(configured_layers: int) -> list[int]:
    candidates = [
        configured_layers,
        min(configured_layers, 24),
        min(configured_layers, 12),
        0,
    ]
    deduped: list[int] = []
    for value in candidates:
        value = max(0, int(value))
        if value not in deduped:
            deduped.append(value)
    return deduped


def _resolve_thread_count() -> int:
    raw_value = os.environ.get("ASSISTANT_LLAMA_THREADS", "").strip()
    if raw_value:
        try:
            return max(1, int(raw_value))
        except ValueError:
            pass

    cpu_count = os.cpu_count() or 8
    return max(4, min(12, cpu_count // 2))
