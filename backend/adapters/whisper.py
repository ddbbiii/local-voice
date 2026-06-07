from __future__ import annotations

import asyncio
import os
import site
import wave
from pathlib import Path

from ..models import ASRResult
from .base import ASRBackend


def _run_transcription(model, audio_path: str) -> tuple[list[str], str]:
    segments, info = model.transcribe(
        audio_path,
        beam_size=1,
        vad_filter=True,
        language="en",
    )
    texts = [segment.text.strip() for segment in segments if segment.text.strip()]
    return texts, info.language


def _register_cuda_dll_directories() -> None:
    candidates: list[Path] = []

    for env_name in ("CUDA_PATH", "CUDA_HOME"):
        env_value = os.environ.get(env_name)
        if env_value:
            candidates.append(Path(env_value) / "bin")

    candidates.extend(
        [
            Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9\bin"),
            Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin"),
            Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6\bin"),
            Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4\bin"),
        ]
    )

    for package_root in site.getsitepackages():
        root = Path(package_root)
        candidates.extend(
            [
                root / "nvidia" / "cublas" / "bin",
                root / "nvidia" / "cuda_runtime" / "bin",
                root / "nvidia" / "cudnn" / "bin",
                root / "ctranslate2",
            ]
        )

    seen: set[str] = set()
    existing_path = os.environ.get("PATH", "")
    prepend_paths: list[str] = []
    for directory in candidates:
        resolved = str(directory)
        if resolved in seen or not directory.exists():
            continue
        seen.add(resolved)
        prepend_paths.append(resolved)
        try:
            os.add_dll_directory(resolved)
        except (AttributeError, FileNotFoundError, OSError):
            continue

    if prepend_paths:
        os.environ["PATH"] = ";".join(prepend_paths + [existing_path])


class WhisperAdapter(ASRBackend):
    def __init__(self, model_name: str = "medium.en") -> None:
        self.model_name = model_name
        self._model = None
        self.available = False
        self.last_error: str | None = None
        try:
            from faster_whisper import WhisperModel

            model_source = str(Path(model_name)) if Path(model_name).exists() else model_name
            preferred_device = os.environ.get("ASSISTANT_ASR_DEVICE", "cuda").lower()
            if preferred_device == "cuda":
                _register_cuda_dll_directories()
                load_attempts = (
                    {"device": "cuda", "compute_type": "float16"},
                    {"device": "cuda", "compute_type": "int8_float16"},
                    {"device": "cpu", "compute_type": "int8"},
                )
            else:
                load_attempts = ({"device": "cpu", "compute_type": "int8"},)
            for options in load_attempts:
                try:
                    self._model = WhisperModel(model_source, **options)
                    self.available = True
                    self.last_error = None
                    break
                except Exception as exc:
                    self._model = None
                    self.available = False
                    self.last_error = str(exc)
        except Exception as exc:
            self._model = None
            self.available = False
            self.last_error = str(exc)

    async def transcribe(self, audio_path: str) -> ASRResult:
        path = Path(audio_path)
        duration_ms = 0
        try:
            with wave.open(str(path), "rb") as wav_file:
                frames = wav_file.getnframes()
                rate = wav_file.getframerate()
                duration_ms = int(frames / float(rate) * 1000)
        except Exception:
            duration_ms = 0

        if not self._model:
            return ASRResult(
                raw_text=(
                    "This is a mock English ASR result. "
                    "Install faster-whisper and point the app to the local medium.en model."
                ),
                language="en",
                segments=[
                    "This is a mock English ASR result.",
                    "Install faster-whisper and point the app to the local medium.en model.",
                ],
                duration_ms=duration_ms,
            )

        texts, language = await asyncio.to_thread(
            _run_transcription,
            self._model,
            audio_path,
        )
        return ASRResult(
            raw_text=" ".join(texts).strip(),
            language=language,
            segments=texts,
            duration_ms=duration_ms,
        )
