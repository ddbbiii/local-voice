from __future__ import annotations

import json
import re

from .models import AssistantTurnResult, MemoryRecord, PromptBundle

REPLY_OPEN = "<REPLY>"
REPLY_CLOSE = "</REPLY>"
REWRITE_OPEN = "<NATIVE_REWRITE>"
REWRITE_CLOSE = "</NATIVE_REWRITE>"
TIP_OPEN = "<TIP>"
TIP_CLOSE = "</TIP>"

SYSTEM_PROMPT = f"""You are a local English speaking coach for an English learner.

Core behavior:
1. Reply in natural conversational English first.
2. Keep the main reply short, warm, and directly responsive.
3. If the learner's wording can be made more natural, provide one full-sentence rewrite.
4. Do not over-correct. If the learner is understandable, keep the correction light.
5. Only add a short extra tip when it is clearly useful.
6. Use the learning memory only when it is relevant to the learner's goal, topic, or frequent mistakes.
7. Recent conversation turns are provided to you in the chat history. Use them when they are relevant.
8. Never claim you cannot remember the user's immediately previous message if it is present in the recent turns.
9. Adapt your vocabulary, pacing, and correction intensity to the learner profile and constraints.
10. If a frequent mistake from memory appears in the current input, prioritize that one correction over minor issues.
11. Use Chinese only as a tiny safety net when the memory or current input says it is helpful.

Output rules:
- Output exactly these XML-like sections and nothing else.
- Put the direct conversational reply inside {REPLY_OPEN}...{REPLY_CLOSE}
- Put one more natural whole-sentence version of the learner's idea inside {REWRITE_OPEN}...{REWRITE_CLOSE}
- Put an optional short tip inside {TIP_OPEN}...{TIP_CLOSE}
- If there is no better rewrite, leave the rewrite section empty.
- If there is no extra tip, leave the tip section empty.
- Never put labels like "Reply:" or "Correction:" in the content.
- Do not lecture. One useful correction is better than five tiny edits.
"""

MEMORY_EXTRACTION_SYSTEM_PROMPT = """You extract durable learning memory for an English speaking coach.

Return JSON only. The JSON must be an array of objects with:
- category: one of ["constraints", "profile", "topics", "notes"]
- text: a short durable memory sentence
- salience: a number from 0.3 to 0.9

Rules:
- Store only durable learning information, not one-off chatter or summaries of this single turn.
- Prefer explicit speaking constraints, learner level, long-term goals, correction intensity, Chinese usage preference, and recurring practice topics.
- Do not store assistant encouragement, generic study advice, or whole-sentence rewrites.
- Do not invent facts.
- Return [] when nothing is worth saving.
"""


def build_prompt(
    session_id: str,
    user_text: str,
    memories: list[MemoryRecord],
    recent_messages,
) -> PromptBundle:
    return PromptBundle(
        system_prompt=SYSTEM_PROMPT,
        memories=memories,
        recent_messages=recent_messages,
        user_text=user_text,
        session_id=session_id,
    )


def format_memory_context(memories: list[MemoryRecord]) -> str:
    if not memories:
        return "No relevant learning memory."

    labels = {
        "constraints": "Coach constraints",
        "profile": "Learner profile",
        "mistakes": "Frequent mistake signals",
        "topics": "Practice topics",
        "notes": "Notes",
    }
    grouped: dict[str, list[MemoryRecord]] = {}
    for record in memories:
        grouped.setdefault(record.category, []).append(record)

    lines: list[str] = []
    for category in ("constraints", "profile", "mistakes", "topics", "notes"):
        records = grouped.get(category, [])
        if not records:
            continue
        lines.append(f"{labels[category]}:")
        for record in sorted(records, key=lambda item: (item.salience, item.updated_at), reverse=True):
            lines.append(f"- {record.text}")

    return "\n".join(lines) if lines else "No relevant learning memory."


def build_memory_extraction_messages(
    user_text: str,
    assistant_turn: AssistantTurnResult,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": MEMORY_EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Learner input:\n"
                f"{user_text}\n\n"
                "Coach reply:\n"
                f"{assistant_turn.reply_text}\n\n"
                "More natural rewrite:\n"
                f"{assistant_turn.native_rewrite or 'NONE'}\n\n"
                "Optional tip:\n"
                f"{assistant_turn.optional_tip or 'NONE'}"
            ),
        },
    ]


def extract_streamable_reply(buffer: str) -> str:
    open_index = buffer.find(REPLY_OPEN)
    if open_index == -1:
        return ""

    content = buffer[open_index + len(REPLY_OPEN) :]
    close_index = content.find(REPLY_CLOSE)
    if close_index != -1:
        return content[:close_index].strip()

    guard = len(REPLY_CLOSE) - 1
    if len(content) <= guard:
        return ""
    return content[:-guard].strip()


def parse_assistant_output(raw_response: str) -> AssistantTurnResult:
    def extract(open_tag: str, close_tag: str) -> str:
        pattern = re.compile(
            re.escape(open_tag) + r"\s*([\s\S]*?)\s*" + re.escape(close_tag),
            re.IGNORECASE,
        )
        match = pattern.search(raw_response)
        return match.group(1).strip() if match else ""

    reply_text = extract(REPLY_OPEN, REPLY_CLOSE)
    native_rewrite = extract(REWRITE_OPEN, REWRITE_CLOSE)
    optional_tip = extract(TIP_OPEN, TIP_CLOSE)

    if not reply_text:
        stripped = re.sub(
            re.escape(REWRITE_OPEN) + r"[\s\S]*?" + re.escape(REWRITE_CLOSE),
            "",
            raw_response,
            flags=re.IGNORECASE,
        )
        stripped = re.sub(
            re.escape(TIP_OPEN) + r"[\s\S]*?" + re.escape(TIP_CLOSE),
            "",
            stripped,
            flags=re.IGNORECASE,
        )
        stripped = re.sub(r"</?(REPLY|NATIVE_REWRITE|TIP)>", "", stripped, flags=re.IGNORECASE)
        reply_text = stripped.strip()

    if not reply_text:
        reply_text = raw_response.strip()

    return AssistantTurnResult(
        reply_text=reply_text,
        native_rewrite=native_rewrite,
        optional_tip=optional_tip,
        raw_response=raw_response,
    )


def parse_memory_extraction_payload(payload: str) -> list[dict]:
    try:
        decoded = json.loads(payload)
        return decoded if isinstance(decoded, list) else []
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", payload)
        if not match:
            return []
        try:
            decoded = json.loads(match.group(0))
            return decoded if isinstance(decoded, list) else []
        except json.JSONDecodeError:
            return []
