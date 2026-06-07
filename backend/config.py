from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .models import AppSettings


@dataclass(slots=True)
class AppPaths:
    root: Path
    config_dir: Path
    data_dir: Path
    memory_dir: Path
    memory_modules_dir: Path
    memory_file: Path
    memory_index_dir: Path
    memory_observations_file: Path
    conversations_dir: Path
    cache_dir: Path
    settings_file: Path

    @classmethod
    def resolve(cls) -> "AppPaths":
        base = Path(os.environ.get("ASSISTANT_DATA_DIR", Path.cwd() / ".assistant_data"))
        config_dir = base / "config"
        data_dir = base / "data"
        memory_dir = data_dir / "memory"
        memory_modules_dir = memory_dir / "modules"
        conversations_dir = data_dir / "conversations"
        cache_dir = data_dir / "cache"
        memory_index_dir = memory_dir / "memory_index"
        memory_observations_file = memory_dir / "memory_observations.json"
        settings_file = config_dir / "settings.json"
        memory_file = memory_dir / "memory.md"

        for path in (
            config_dir,
            memory_dir,
            memory_modules_dir,
            conversations_dir,
            cache_dir,
            memory_index_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        return cls(
            root=base,
            config_dir=config_dir,
            data_dir=data_dir,
            memory_dir=memory_dir,
            memory_modules_dir=memory_modules_dir,
            memory_file=memory_file,
            memory_index_dir=memory_index_dir,
            memory_observations_file=memory_observations_file,
            conversations_dir=conversations_dir,
            cache_dir=cache_dir,
            settings_file=settings_file,
        )


def load_settings(paths: AppPaths) -> AppSettings:
    default_settings = _build_default_settings(paths)

    if not paths.settings_file.exists():
        settings = default_settings
        save_settings(paths, settings)
        return settings

    payload = json.loads(paths.settings_file.read_text(encoding="utf-8"))
    payload["memory_file_path"] = payload.get("memory_file_path") or str(paths.memory_file)
    payload["memory_root_path"] = payload.get("memory_root_path") or str(paths.memory_modules_dir)

    defaults = default_settings.model_dump(mode="json")
    for key, value in defaults.items():
        if key not in payload or payload[key] in ("", None):
            payload[key] = value

    # Migrate legacy assistant settings to the English-learning defaults. For
    # this app, medium.en is the preferred first-run ASR target: it is English
    # specific, lower latency, and easier to keep resident than large-v3.
    legacy_medium_targets = {
        "medium",
        "medium.en",
        str(Path(r"E:\program\models\asr\faster-whisper-medium.en")),
        defaults["whisper_model_name"],
    }
    legacy_large_targets = {
        str(Path(r"E:\program\models\asr\faster-whisper-large-v3\faster-whisper-large-v3")),
        str(Path(r"E:\program\models\asr\faster-whisper-large-v3")),
        str(Path(r"E:\program\models\asr\faster-distil-whisper-large-v3")),
        str(Path(r"E:\program\models\asr\faster-whisper-large-v3-turbo")),
    }
    if payload.get("whisper_model_name") in legacy_medium_targets | legacy_large_targets:
        payload["whisper_model_name"] = _resolve_default_whisper_model()

    settings = AppSettings.model_validate(payload)
    save_settings(paths, settings)
    return settings


def save_settings(paths: AppPaths, settings: AppSettings) -> None:
    payload = settings.model_dump(mode="json")
    payload["memory_file_path"] = str(paths.memory_file)
    payload["memory_root_path"] = str(paths.memory_modules_dir)
    paths.settings_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _build_default_settings(paths: AppPaths) -> AppSettings:
    return AppSettings(
        memory_file_path=str(paths.memory_file),
        memory_root_path=str(paths.memory_modules_dir),
        llm_provider=_resolve_default_llm_provider(),
        llm_api_base="",
        llm_api_model="",
        llm_api_key="",
        whisper_model_name=_resolve_default_whisper_model(),
    )


def _resolve_default_llm_provider() -> str:
    return "local"


def _resolve_default_whisper_model() -> str:
    candidates = [
        Path(r"E:\program\models\asr\faster-whisper-medium.en"),
        Path(r"E:\program\models\asr\faster-distil-whisper-large-v3"),
        Path(r"E:\program\models\asr\faster-whisper-large-v3\faster-whisper-large-v3"),
        Path(r"E:\program\models\asr\faster-whisper-large-v3"),
        Path(r"E:\program\models\asr\faster-whisper-large-v3-turbo"),
    ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return str(candidates[-1])
