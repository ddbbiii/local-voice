from __future__ import annotations

import json
import re
from hashlib import sha1
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .adapters.base import LLMBackend, VectorIndexBackend
from .memory_store import ModularMarkdownMemoryStore
from .models import AssistantTurnResult, MemoryRecord, MemoryRecallResult
from .prompt import build_memory_extraction_messages, parse_memory_extraction_payload

VALID_CATEGORIES = {"constraints", "profile", "mistakes", "topics", "notes"}
LEARNING_TOPIC_KEYWORDS = (
    "daily conversation",
    "small talk",
    "job interview",
    "interview",
    "meeting",
    "presentation",
    "travel",
    "pronunciation",
    "grammar",
    "vocabulary",
    "listening",
    "speaking",
    "ielts",
    "toefl",
    "cet",
)
NOISE_PATTERNS = (
    "keep up the good work",
    "great job",
    "practice makes progress",
    "consistency is key",
    "focus on the progress",
    "language exchange platforms",
    "tandem",
    "hellotalk",
    "prefer saying:",
    "recent correction focus:",
)
@dataclass(slots=True)
class MemoryCandidate:
    category: str
    text: str
    salience: float
    admission: str
    key: str
    last_example: str = ""


class MemoryService:
    def __init__(
        self,
        store: ModularMarkdownMemoryStore,
        vector_backend: VectorIndexBackend,
        llm_backend: LLMBackend,
        observations_path: Path,
    ) -> None:
        self.store = store
        self.vector_backend = vector_backend
        self.llm_backend = llm_backend
        self.observations_path = observations_path
        self.observations_path.parent.mkdir(parents=True, exist_ok=True)
        self._observations = self._load_observations()

    async def initialize(self) -> None:
        cleaned = self._compact_existing_records(self.store.load_all())
        self.store.replace_all(cleaned)
        self._persist_observations()
        await self.vector_backend.rebuild(cleaned)

    async def search(self, query: str, top_k: int = 5) -> MemoryRecallResult:
        all_records = self.store.load_all()
        matched = await self.vector_backend.search(query, max(top_k, 8))

        constraints = self._top_records(
            [record for record in all_records if record.category == "constraints"],
            limit=6,
        )
        profile = self._top_records(
            [record for record in all_records if record.category == "profile"],
            limit=5,
        )
        matched_mistakes = [record for record in matched if record.category == "mistakes"]
        if not matched_mistakes and self._query_mentions_correction(query):
            matched_mistakes = [record for record in all_records if record.category == "mistakes"]
        mistakes = self._top_records(
            matched_mistakes,
            limit=4,
        )
        topics = self._top_records(
            [record for record in matched if record.category == "topics"],
            limit=3,
        )

        ordered = self._dedupe_records(constraints + profile + mistakes + topics)
        summary = " | ".join(
            f"[{record.category}] {record.text}" for record in ordered[:6]
        )
        return MemoryRecallResult(
            records=ordered,
            summary=summary,
            source_ids=[record.memory_id for record in ordered],
        )

    def snapshot(self, memory_file_path: str = "", memory_root_path: str = "", vector_provider: str = "json") -> dict:
        records = self.store.load_all()
        grouped: dict[str, list[MemoryRecord]] = {
            category: [] for category in ("constraints", "profile", "mistakes", "topics", "notes")
        }
        for record in records:
            grouped.setdefault(record.category, []).append(record)

        counts = {category: len(items) for category, items in grouped.items()}
        focus_records = self._dedupe_records(
            self._top_records(grouped.get("constraints", []), 3)
            + self._top_records(grouped.get("profile", []), 3)
            + self._top_records(grouped.get("mistakes", []), 3)
            + self._top_records(grouped.get("topics", []), 2)
        )
        summary = " | ".join(
            f"[{record.category}] {record.text}" for record in focus_records[:8]
        )

        return {
            "records": records,
            "grouped": grouped,
            "summary": summary,
            "counts": counts,
            "memory_file_path": memory_file_path,
            "memory_root_path": memory_root_path,
            "vector_index_provider": vector_provider,
        }

    async def write_turn_memories(
        self,
        session_id: str,
        user_text: str,
        assistant_turn: AssistantTurnResult,
    ) -> list[MemoryRecord]:
        existing = self.store.load_all()
        candidates = await self._extract_memories(session_id, user_text, assistant_turn)
        admitted = self._admit_candidates(existing, candidates, session_id)
        if not admitted:
            self._persist_observations()
            return []

        self.store.upsert(admitted)
        await self.vector_backend.upsert(admitted)
        self._persist_observations()
        return admitted

    async def _extract_memories(
        self,
        session_id: str,
        user_text: str,
        assistant_turn: AssistantTurnResult,
    ) -> list[MemoryCandidate]:
        llm_candidates = await self._extract_with_llm(session_id, user_text, assistant_turn)
        rule_candidates = self._extract_with_rules(session_id, user_text, assistant_turn)
        mistake_candidates = self._extract_mistake_patterns(user_text, assistant_turn)
        return self._dedupe_candidates(llm_candidates + rule_candidates + mistake_candidates)

    async def _extract_with_llm(
        self,
        session_id: str,
        user_text: str,
        assistant_turn: AssistantTurnResult,
    ) -> list[MemoryCandidate]:
        try:
            payload = await self.llm_backend.complete(
                build_memory_extraction_messages(user_text, assistant_turn)
            )
        except Exception:
            return []

        parsed = parse_memory_extraction_payload(payload)
        if not parsed:
            return []

        candidates: list[MemoryCandidate] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            category = self._normalize_category(str(item.get("category", "")).strip().lower())
            text = self._normalize_candidate_text(category, str(item.get("text", "")).strip())
            if category not in VALID_CATEGORIES or not text or self._is_noise_text(text):
                continue
            if category in {"mistakes", "notes"}:
                continue
            try:
                salience = float(item.get("salience", 0.55))
            except (TypeError, ValueError):
                salience = 0.55
            salience = max(0.3, min(salience, 0.9))
            admission = "immediate" if category == "constraints" else "observe"
            candidates.append(
                MemoryCandidate(
                    category=category,
                    text=text,
                    salience=salience,
                    admission=admission,
                    key=self._memory_key(category, text),
                )
            )
        return candidates

    def _extract_with_rules(
        self,
        session_id: str,
        user_text: str,
        assistant_turn: AssistantTurnResult,
    ) -> list[MemoryCandidate]:
        lowered = user_text.lower()
        candidates: list[MemoryCandidate] = []

        constraint_rules = [
            (
                r"\b(use|speak|reply in) (mostly |primarily |mainly )?english\b",
                "Use English as the default response language.",
            ),
            (
                r"\b(no chinese|don't use chinese|minimal chinese|less chinese|chinese.*only when necessary)\b",
                "Keep Chinese hints minimal and only use them when necessary.",
            ),
            (
                r"\b(reply|answer).*(first).*(correct)|(correct).*(after)\b",
                "Reply naturally first, then give a short correction.",
            ),
            (
                r"\b(respond more quickly|reply faster|be quicker)\b",
                "Respond quickly and keep replies concise.",
            ),
            (
                r"\b(speak slower|slow down|talk slower|more slowly)\b",
                "Speak at a slower, learner-friendly pace.",
            ),
            (
                r"\b(correct me strictly|be strict|more correction|correct more|point out my mistakes)\b",
                "Use a stricter correction style when the learner asks for it.",
            ),
            (
                r"\b(don't correct every sentence|do not correct every sentence|light correction|correct less|less correction)\b",
                "Keep corrections light and avoid correcting every sentence.",
            ),
            (
                r"\b(only english|english only|no translation)\b",
                "Use English only unless the learner explicitly asks for Chinese.",
            ),
            (
                r"\b(don't be too abrupt|be more natural when asking me to slow down)\b",
                "Use a natural, non-abrupt tone when asking the learner to slow down.",
            ),
        ]
        for pattern, text in constraint_rules:
            if re.search(pattern, user_text, flags=re.IGNORECASE):
                candidates.append(
                    MemoryCandidate(
                        category="constraints",
                        text=text,
                        salience=0.85,
                        admission="immediate",
                        key=self._memory_key("constraints", text),
                    )
                )

        profile_matchers = [
            (r"\b(i am|i'm)\s+(a\s+)?(beginner|intermediate|advanced)\b", "Level: {value}"),
            (r"\bmy english level is\s+([a-z0-9\- ]+)\b", "Level: {value}"),
            (
                r"\b(my goal is|i want to improve|i want to practice)\s+([a-z0-9 ,\-]+)",
                "Goal: {value}",
            ),
            (
                r"\b(i'm preparing for|i am preparing for|i need to prepare for)\s+([a-z0-9 ,\-]+)",
                "Goal: prepare for {value}",
            ),
        ]
        for pattern, template in profile_matchers:
            for match in re.findall(pattern, user_text, flags=re.IGNORECASE):
                if isinstance(match, tuple):
                    value = next((part for part in reversed(match) if part and part.strip()), "")
                else:
                    value = str(match)
                value = self._clean_fragment(value)
                if not value:
                    continue
                text = template.format(value=value)
                candidates.append(
                    MemoryCandidate(
                        category="profile",
                        text=text,
                        salience=0.78,
                        admission="immediate",
                        key=self._memory_key("profile", text),
                    )
                )

        if any(exam in lowered for exam in ("ielts", "toefl", "cet")):
            exam_target = next(exam for exam in ("ielts", "toefl", "cet") if exam in lowered)
            text = f"Goal: {exam_target.upper()} preparation"
            candidates.append(
                MemoryCandidate(
                    category="profile",
                    text=text,
                    salience=0.82,
                    admission="immediate",
                    key=self._memory_key("profile", text),
                )
            )

        for keyword in LEARNING_TOPIC_KEYWORDS:
            if keyword in lowered:
                text = f"Practice topic: {keyword}"
                candidates.append(
                    MemoryCandidate(
                        category="topics",
                        text=text,
                        salience=0.66,
                        admission="observe",
                        key=self._memory_key("topics", text),
                    )
                )

        topic_patterns = [
            r"\btoday i want to practice\s+([a-z0-9 ,\-]+)",
            r"\blet's practice\s+([a-z0-9 ,\-]+)",
            r"\bcan we practice\s+([a-z0-9 ,\-]+)",
            r"\bi need to practice\s+([a-z0-9 ,\-]+)",
        ]
        for pattern in topic_patterns:
            for match in re.findall(pattern, user_text, flags=re.IGNORECASE):
                text = f"Practice topic: {self._clean_fragment(str(match))}"
                if text.endswith(":"):
                    continue
                candidates.append(
                    MemoryCandidate(
                        category="topics",
                        text=text,
                        salience=0.62,
                        admission="observe",
                        key=self._memory_key("topics", text),
                    )
                )

        return candidates

    def _extract_mistake_patterns(
        self, user_text: str, assistant_turn: AssistantTurnResult
    ) -> list[MemoryCandidate]:
        lowered = user_text.lower()
        rewrite = assistant_turn.native_rewrite.lower()
        candidates: list[MemoryCandidate] = []

        if re.search(r"\bi am (very )?fun\b", lowered):
            label = 'word-choice: use "have fun" instead of "be fun" when describing your own experience'
            text = (
                f"{label} "
                '(count={count}; latest example: {example})'
            )
            candidates.append(
                MemoryCandidate(
                    category="mistakes",
                    text=text,
                    salience=0.82,
                    admission="immediate",
                    key=self._memory_key("mistakes", label),
                    last_example=user_text.strip(),
                )
            )

        if re.search(r"\bi have a [a-z ]*day\b", lowered) and re.search(r"\bi had\b", rewrite):
            label = "past tense consistency when talking about a finished day or event"
            text = (
                f"{label} "
                "(count={count}; latest example: {example})"
            )
            candidates.append(
                MemoryCandidate(
                    category="mistakes",
                    text=text,
                    salience=0.8,
                    admission="immediate",
                    key=self._memory_key("mistakes", label),
                    last_example=user_text.strip(),
                )
            )

        pattern_specs = [
            (
                r"\bi very like\b",
                'word order: say "I really like..." instead of "I very like..."',
                0.84,
            ),
            (
                r"\bdiscuss about\b",
                'verb pattern: say "discuss something" instead of "discuss about something"',
                0.78,
            ),
            (
                r"\bdepend of\b",
                'preposition: say "depend on" instead of "depend of"',
                0.78,
            ),
            (
                r"\bmore better\b",
                'comparative form: say "better" or "much better" instead of "more better"',
                0.78,
            ),
        ]
        for pattern, label, salience in pattern_specs:
            if not re.search(pattern, lowered):
                continue
            text = (
                f"{label} "
                "(count={count}; latest example: {example})"
            )
            candidates.append(
                MemoryCandidate(
                    category="mistakes",
                    text=text,
                    salience=salience,
                    admission="immediate",
                    key=self._memory_key("mistakes", label),
                    last_example=user_text.strip(),
                )
            )

        return candidates

    def _admit_candidates(
        self,
        existing: list[MemoryRecord],
        candidates: list[MemoryCandidate],
        session_id: str,
    ) -> list[MemoryRecord]:
        existing_by_key = {self._memory_key(record.category, record.text): record for record in existing}
        admitted: list[MemoryRecord] = []
        now = datetime.utcnow()

        for candidate in candidates:
            if candidate.category not in VALID_CATEGORIES or self._is_noise_text(candidate.text):
                continue

            existing_record = self._find_existing(existing, candidate)
            if candidate.category == "mistakes":
                admitted.append(
                    self._merge_mistake_record(existing_record, candidate, session_id, now)
                )
                self._observations.pop(candidate.key, None)
                continue

            if existing_record:
                existing_record.updated_at = now
                existing_record.salience = max(existing_record.salience, candidate.salience)
                admitted.append(existing_record)
                self._observations.pop(candidate.key, None)
                continue

            observed_count = self._observe_candidate(candidate, now)
            should_admit = candidate.admission == "immediate" or observed_count >= 2
            if not should_admit:
                continue

            admitted.append(
                MemoryRecord(
                    memory_id=self._stable_memory_id(candidate.key),
                    category=candidate.category,  # type: ignore[arg-type]
                    text=candidate.text,
                    salience=candidate.salience,
                    created_at=now,
                    updated_at=now,
                    source_turn_id=session_id,
                )
            )
            self._observations.pop(candidate.key, None)

        merged_by_id = {record.memory_id: record for record in admitted}
        return list(merged_by_id.values())

    def _merge_mistake_record(
        self,
        existing_record: MemoryRecord | None,
        candidate: MemoryCandidate,
        session_id: str,
        now: datetime,
    ) -> MemoryRecord:
        previous_count = self._extract_count(existing_record.text) if existing_record else 0
        observed_count = self._observe_candidate(candidate, now)
        total_count = max(previous_count + 1, observed_count)
        text = candidate.text.format(count=total_count, example=candidate.last_example or "n/a")
        return MemoryRecord(
            memory_id=self._stable_memory_id(candidate.key),
            category="mistakes",
            text=text,
            salience=max(candidate.salience, existing_record.salience if existing_record else 0.0),
            created_at=existing_record.created_at if existing_record else now,
            updated_at=now,
            source_turn_id=session_id,
        )

    def _find_existing(
        self, existing: list[MemoryRecord], candidate: MemoryCandidate
    ) -> MemoryRecord | None:
        for record in existing:
            if record.category != candidate.category:
                continue
            if record.category == "mistakes" and self._memory_key("mistakes", record.text) == candidate.key:
                return record
            if self._memory_key(record.category, record.text) == candidate.key:
                return record
        return None

    def _observe_candidate(self, candidate: MemoryCandidate, now: datetime) -> int:
        payload = self._observations.get(candidate.key, {})
        count = int(payload.get("count", 0)) + 1
        self._observations[candidate.key] = {
            "category": candidate.category,
            "text": candidate.text,
            "count": count,
            "last_seen": now.isoformat(),
            "last_example": candidate.last_example,
        }
        return count

    def _load_observations(self) -> dict[str, dict]:
        if not self.observations_path.exists():
            return {}
        try:
            payload = json.loads(self.observations_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _persist_observations(self) -> None:
        self.observations_path.write_text(
            json.dumps(self._observations, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _compact_existing_records(self, records: list[MemoryRecord]) -> list[MemoryRecord]:
        cleaned: list[MemoryRecord] = []
        latest_by_key: dict[str, MemoryRecord] = {}
        now = datetime.utcnow()

        for record in records:
            normalized = self._normalize_existing_record(record, now)
            if not normalized:
                continue
            key = self._memory_key(normalized.category, normalized.text)
            previous = latest_by_key.get(key)
            if not previous or normalized.updated_at >= previous.updated_at:
                latest_by_key[key] = normalized

        cleaned = sorted(latest_by_key.values(), key=lambda item: item.updated_at, reverse=True)
        return cleaned

    def _normalize_existing_record(
        self, record: MemoryRecord, now: datetime
    ) -> MemoryRecord | None:
        text = record.text.strip()
        lowered = text.lower()
        if self._is_noise_text(text):
            return None

        if record.category == "mistakes":
            if lowered.startswith("prefer saying:"):
                return None
            if "use 'was' for past actions" in lowered or "what had it doing" in lowered:
                text = (
                    "past tense consistency when talking about a finished day or event "
                    "(count=1; latest example: unclear past action phrasing)"
                )
            elif "count=" not in lowered:
                return None
            return MemoryRecord(
                memory_id=self._stable_memory_id(self._memory_key("mistakes", text)),
                category="mistakes",
                text=text,
                salience=max(record.salience, 0.75),
                created_at=record.created_at,
                updated_at=record.updated_at,
                source_turn_id=record.source_turn_id,
            )

        if record.category == "constraints":
            text = self._normalize_candidate_text("constraints", text)
            if not text:
                return None
            return MemoryRecord(
                memory_id=self._stable_memory_id(self._memory_key("constraints", text)),
                category="constraints",
                text=text,
                salience=record.salience,
                created_at=record.created_at,
                updated_at=record.updated_at,
                source_turn_id=record.source_turn_id,
            )

        if record.category == "profile":
            text = self._normalize_candidate_text("profile", text)
            if not text:
                return None
            return MemoryRecord(
                memory_id=self._stable_memory_id(self._memory_key("profile", text)),
                category="profile",
                text=text,
                salience=record.salience,
                created_at=record.created_at,
                updated_at=record.updated_at,
                source_turn_id=record.source_turn_id,
            )

        if record.category == "topics":
            text = self._normalize_candidate_text("topics", text)
            if not text:
                return None
            return MemoryRecord(
                memory_id=self._stable_memory_id(self._memory_key("topics", text)),
                category="topics",
                text=text,
                salience=record.salience,
                created_at=record.created_at,
                updated_at=record.updated_at,
                source_turn_id=record.source_turn_id,
            )

        if record.category == "notes":
            text = self._normalize_candidate_text("notes", text)
            if not text:
                return None
            return MemoryRecord(
                memory_id=self._stable_memory_id(self._memory_key("notes", text)),
                category="notes",
                text=text,
                salience=record.salience,
                created_at=record.created_at,
                updated_at=record.updated_at,
                source_turn_id=record.source_turn_id,
            )

        if "respond more quickly" in lowered:
            text = "Respond quickly and keep replies concise."
            category = "constraints"
        elif "less abrupt" in lowered or "slow down" in lowered:
            text = "Use a natural, non-abrupt tone when asking the learner to slow down."
            category = "constraints"
        elif "english speaking" in lowered or "goal:" in lowered or "preparation" in lowered:
            text = self._normalize_candidate_text("profile", text)
            category = "profile"
        elif record.category == "topics" and "practice topic:" in lowered:
            text = self._normalize_candidate_text("topics", text)
            category = "topics"
        else:
            return None

        return MemoryRecord(
            memory_id=self._stable_memory_id(self._memory_key(category, text)),
            category=category,  # type: ignore[arg-type]
            text=text,
            salience=record.salience,
            created_at=record.created_at,
            updated_at=record.updated_at,
            source_turn_id=record.source_turn_id,
        )

    def _normalize_category(self, category: str) -> str:
        if category == "preferences":
            return "constraints"
        if category == "goals":
            return "profile"
        return category

    def _normalize_candidate_text(self, category: str, text: str) -> str:
        cleaned = self._clean_fragment(text)
        if not cleaned:
            return ""
        if category == "profile":
            if cleaned.lower().startswith("goal:") or cleaned.lower().startswith("level:"):
                return cleaned
            if any(word in cleaned.lower() for word in ("improve", "practice", "preparation")):
                return f"Goal: {cleaned}"
            return f"Profile: {cleaned}"
        if category == "topics":
            if cleaned.lower().startswith("practice topic:"):
                return cleaned
            return f"Practice topic: {cleaned}"
        return cleaned

    def _memory_key(self, category: str, text: str) -> str:
        if category == "mistakes":
            pattern_match = re.match(r"([^(]+)", text.strip())
            if pattern_match:
                return f"mistakes:{pattern_match.group(1).strip().lower()}"
        normalized = re.sub(r"\s+", " ", text.strip().lower())
        return f"{category}:{normalized}"

    def _stable_memory_id(self, key: str) -> str:
        digest = sha1(key.encode("utf-8")).hexdigest()[:16]
        return f"mem-{digest}"

    def _extract_count(self, text: str) -> int:
        match = re.search(r"count=(\d+)", text)
        return int(match.group(1)) if match else 1

    def _clean_fragment(self, value: str) -> str:
        value = re.sub(r"\s+", " ", value.strip(" .,!?:;"))
        return value

    def _is_noise_text(self, text: str) -> bool:
        lowered = text.lower().strip()
        if not lowered or lowered == "no records yet.":
            return True
        return any(pattern in lowered for pattern in NOISE_PATTERNS)

    def _query_mentions_correction(self, query: str) -> bool:
        lowered = query.lower()
        return any(
            keyword in lowered
            for keyword in (
                "correct",
                "mistake",
                "wrong",
                "grammar",
                "natural",
                "rewrite",
                "say this",
                "how to say",
            )
        )

    def _top_records(self, records: list[MemoryRecord], limit: int) -> list[MemoryRecord]:
        ranked = sorted(records, key=lambda item: (item.salience, item.updated_at), reverse=True)
        return self._dedupe_records(ranked)[:limit]

    def _dedupe_records(self, records: list[MemoryRecord]) -> list[MemoryRecord]:
        seen: set[str] = set()
        deduped: list[MemoryRecord] = []
        for record in records:
            if record.memory_id in seen:
                continue
            seen.add(record.memory_id)
            deduped.append(record)
        return deduped

    def _dedupe_candidates(self, candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
        deduped: dict[str, MemoryCandidate] = {}
        for candidate in candidates:
            existing = deduped.get(candidate.key)
            if not existing or candidate.salience >= existing.salience:
                deduped[candidate.key] = candidate
        return list(deduped.values())
