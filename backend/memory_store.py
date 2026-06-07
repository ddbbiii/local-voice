from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .models import MemoryRecord

SECTION_MAP = {
    "Constraints": "constraints",
    "Profile": "profile",
    "Frequent Mistakes": "mistakes",
    "Practice Topics": "topics",
    "Notes": "notes",
}

FILE_MAP = {
    "constraints": "constraints.md",
    "profile": "profile.md",
    "mistakes": "mistakes.md",
    "topics": "topics.md",
    "notes": "notes.md",
}

LEGACY_FILE_MAP = {
    "goals": "goals.md",
    "preferences": "preferences.md",
}

LEGACY_CATEGORY_MAP = {
    "goals": "profile",
    "preferences": "constraints",
}

MEMORY_PATTERN = re.compile(
    r"- \[(?P<memory_id>[^\]]+)\] (?P<text>.*?) "
    r"\{salience=(?P<salience>[^,]+), created_at=(?P<created_at>[^,]+), "
    r"updated_at=(?P<updated_at>[^,]+), source_turn_id=(?P<source_turn_id>[^}]+)\}"
)


class ModularMarkdownMemoryStore:
    def __init__(self, modules_dir: Path, legacy_path: Path | None = None) -> None:
        self.modules_dir = modules_dir
        self.legacy_path = legacy_path
        self.modules_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_initialized()

    def load_all(self) -> list[MemoryRecord]:
        records: list[MemoryRecord] = []
        for category, file_name in FILE_MAP.items():
            target = self.modules_dir / file_name
            if target.exists():
                records.extend(self._load_file(target, category))
        for legacy_category, file_name in LEGACY_FILE_MAP.items():
            target = self.modules_dir / file_name
            if target.exists():
                records.extend(
                    self._load_file(target, LEGACY_CATEGORY_MAP[legacy_category])
                )
        return sorted(records, key=lambda item: item.updated_at, reverse=True)

    def upsert(self, records: list[MemoryRecord]) -> None:
        existing = {record.memory_id: record for record in self.load_all()}
        for record in records:
            existing[record.memory_id] = record
        self.replace_all(existing.values())

    def replace_all(self, records: list[MemoryRecord]) -> None:
        grouped: dict[str, list[MemoryRecord]] = defaultdict(list)
        for record in records:
            grouped[record.category].append(record)

        for category, file_name in FILE_MAP.items():
            items = sorted(
                grouped.get(category, []),
                key=lambda item: item.updated_at,
                reverse=True,
            )
            (self.modules_dir / file_name).write_text(
                self._render(category, items),
                encoding="utf-8",
            )

        for file_name in LEGACY_FILE_MAP.values():
            legacy_path = self.modules_dir / file_name
            if legacy_path.exists():
                legacy_path.unlink()

    def _ensure_initialized(self) -> None:
        if self.legacy_path and self.legacy_path.exists() and not self._has_module_records():
            migrated = self._migrate_legacy()
            if migrated:
                self.replace_all(migrated)
                return

        for file_name in FILE_MAP.values():
            target = self.modules_dir / file_name
            if not target.exists():
                target.write_text(self._render_from_category_name(file_name, []), encoding="utf-8")

    def _has_module_records(self) -> bool:
        for file_name in FILE_MAP.values():
            target = self.modules_dir / file_name
            if not target.exists():
                continue
            if MEMORY_PATTERN.search(target.read_text(encoding="utf-8")):
                return True
        return False

    def _migrate_legacy(self) -> list[MemoryRecord]:
        if not self.legacy_path or not self.legacy_path.exists():
            return []

        legacy_map = {
            "Profile": "profile",
            "Preferences": "constraints",
            "Ongoing Tasks": "profile",
            "Important Events": "topics",
            "Constraints": "constraints",
            "Notes": "notes",
        }
        migrated: list[MemoryRecord] = []
        current_section = None
        content = self.legacy_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            if line.startswith("## "):
                current_section = line[3:].strip()
                continue
            match = MEMORY_PATTERN.match(line.strip())
            if not match or current_section is None:
                continue
            category = legacy_map.get(current_section)
            if not category:
                continue
            migrated.append(
                MemoryRecord(
                    memory_id=match.group("memory_id"),
                    category=category,  # type: ignore[arg-type]
                    text=match.group("text"),
                    salience=float(match.group("salience")),
                    created_at=datetime.fromisoformat(match.group("created_at")),
                    updated_at=datetime.fromisoformat(match.group("updated_at")),
                    source_turn_id=match.group("source_turn_id"),
                )
            )
        return migrated

    def _load_file(self, path: Path, category: str) -> list[MemoryRecord]:
        records: list[MemoryRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            match = MEMORY_PATTERN.match(line.strip())
            if not match:
                continue
            records.append(
                MemoryRecord(
                    memory_id=match.group("memory_id"),
                    category=category,  # type: ignore[arg-type]
                    text=match.group("text"),
                    salience=float(match.group("salience")),
                    created_at=datetime.fromisoformat(match.group("created_at")),
                    updated_at=datetime.fromisoformat(match.group("updated_at")),
                    source_turn_id=match.group("source_turn_id"),
                )
            )
        return records

    def _render(self, category: str, records: list[MemoryRecord]) -> str:
        section_name = next(
            name for name, mapped_category in SECTION_MAP.items() if mapped_category == category
        )
        lines = [f"# {section_name}", ""]
        if not records:
            lines.append("- No records yet.")
            return "\n".join(lines) + "\n"

        for record in records:
            lines.append(
                f"- [{record.memory_id}] {record.text} "
                f"{{salience={record.salience:.2f}, created_at={record.created_at.isoformat()}, "
                f"updated_at={record.updated_at.isoformat()}, source_turn_id={record.source_turn_id}}}"
            )
        lines.append("")
        return "\n".join(lines)

    def _render_from_category_name(self, file_name: str, records: list[MemoryRecord]) -> str:
        category = next(
            category_name
            for category_name, mapped_file_name in FILE_MAP.items()
            if mapped_file_name == file_name
        )
        return self._render(category, records)
