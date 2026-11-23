"""Execute bounded, read-only tool turns for Ask."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import json
import logging
import re
import time
from typing import Any, Callable

from trace_memory.domain.memories import MemoryItem
from trace_memory.domain.notes import RawNote
from trace_memory.domain.proposals import ValidationError
from trace_memory.llm.protocol import AskSynthesis, AskToolModel


_LOGGER = logging.getLogger(__name__)
_MAX_TOOL_CALLS = 1


@dataclass(frozen=True)
class AskToolResult:
    data: dict[str, Any] = field(default_factory=dict)
    memories: tuple[MemoryItem, ...] = ()
    notes: tuple[RawNote, ...] = ()


@dataclass(frozen=True)
class AskTool:
    name: str
    description: str
    parameters: dict[str, Any]
    validate: Callable[[dict[str, Any]], None]
    execute: Callable[[str, dict[str, Any]], AskToolResult]

    def definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def ask_evidence(
    memories: Iterable[MemoryItem], notes: Iterable[RawNote], *, preview: bool = False,
) -> dict[str, Any]:
    return {
        "memories": [
            {
                "memory_id": m.memory_id, "title": m.title,
                "summary": m.summary[:240] if preview else m.summary,
                "assertion_kind": m.assertion_kind,
                **({"memory_type": m.memory_type} if preview else {"payload": m.payload}),
            }
            for m in memories
        ],
        "notes": [
            {
                "note_id": n.note_id, "event_time": n.event_time, "created_at": n.created_at,
                "content": n.visible_content()[:240] if preview else n.visible_content(),
            }
            for n in notes
        ],
    }


@dataclass(frozen=True)
class ToolObservation:
    name: str
    arguments: dict[str, Any]
    status: str
    result: AskToolResult
    duration_ms: float

    def content(self) -> dict[str, Any]:
        if self.status != "ok":
            return {"error": self.status}
        return {"data": self.result.data, **ask_evidence(self.result.memories, self.result.notes)}

    def to_trace(self, *, reused: bool = False) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": self.arguments,
            "status": "reused" if reused else self.status,
            "duration_ms": 0 if reused else self.duration_ms,
            "result": {
                "data": self.result.data,
                **ask_evidence(self.result.memories, self.result.notes, preview=True),
            } if self.status == "ok" else {"error": self.status},
        }


@dataclass(frozen=True)
class AskReActResult:
    synthesis: AskSynthesis
    memories: tuple[MemoryItem, ...]
    notes: tuple[RawNote, ...]
    tool_trace: list[dict[str, Any]]
    stop_reason: str


class AskReAct:
    def __init__(self, model: AskToolModel, tools: tuple[AskTool, ...]) -> None:
        self.model = model
        self.tools = {tool.name: tool for tool in tools}
        if len(self.tools) != len(tools):
            raise ValueError("Ask tool names must be unique")

    def run(
        self, space_id: str, messages: list[dict[str, Any]], *,
        initial_tools: dict[tuple[str, str], ToolObservation] | None = None,
        rounds: list[dict[str, Any]] | None = None,
        tool_trace: list[dict[str, Any]] | None = None,
    ) -> AskReActResult:
        messages = list(messages)
        definitions = [tool.definition() for tool in self.tools.values()]
        memories: dict[str, MemoryItem] = {}
        notes: dict[str, RawNote] = {}
        if tool_trace is None:
            tool_trace = []
        if rounds is None:
            rounds = []
        cache = dict(initial_tools or {})
        remaining = _MAX_TOOL_CALLS
        tokens = 0
        calls = 0
        stop_reason = "answer"
        while True:
            started = time.perf_counter()
            round_event: dict[str, Any] = {"round": calls + 1}
            rounds.append(round_event)
            try:
                turn = self.model.ask_turn(messages, definitions, allow_tools=remaining > 0)
            except Exception:
                round_event.update(
                    status="error", duration_ms=round((time.perf_counter() - started) * 1000, 2),
                )
                raise
            calls += 1
            tokens += turn.tokens_used
            requested = turn.message.get("tool_calls")
            round_event.update(
                status="tool_calls" if requested else "answer",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                tokens_used=turn.tokens_used,
                requested_tools=[call["function"]["name"] for call in requested or ()],
            )
            if not requested:
                if not remaining:
                    stop_reason = "tool_limit"
                break
            if not remaining:
                stop_reason = "unexpected_tool_call"
                break

            messages.append({
                "role": "assistant",
                "content": turn.message.get("content"),
                "tool_calls": requested,
            })
            for call in requested:
                function = call["function"]
                name = function["name"]
                if remaining:
                    remaining -= 1
                    try:
                        args = json.loads(function.get("arguments", "{}"))
                    except (TypeError, ValueError):
                        args = None
                    key = (name, json.dumps(args, sort_keys=True))
                    cached = cache.get(key)
                    observation = cached or self.execute(space_id, name, args)
                    result = observation.result
                    memories.update((m.memory_id, m) for m in result.memories)
                    notes.update((n.note_id, n) for n in result.notes)
                    event = observation.to_trace(reused=cached is not None)
                else:
                    observation = ToolObservation(name, {}, "tool_limit", AskToolResult(), 0)
                    event = observation.to_trace()

                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(observation.content(), ensure_ascii=False),
                })
                tool_trace.append({**event, "round": calls, "tool_call_id": call["id"]})

        parsed = _parse_ask_answer(turn.message.get("content")) if not requested else None
        if parsed is None:
            if stop_reason != "unexpected_tool_call":
                stop_reason = "invalid_answer"
            parsed = {
                "answer": "本次未能生成可验证的答案。",
                "cited_memory_ids": [],
                "cited_note_ids": [],
                "has_sufficient_evidence": False,
            }
        return AskReActResult(
            synthesis=AskSynthesis(
                answer=parsed["answer"],
                cited_memory_ids=tuple(parsed["cited_memory_ids"]),
                cited_note_ids=tuple(parsed["cited_note_ids"]),
                has_sufficient_evidence=parsed["has_sufficient_evidence"],
                llm_calls=calls,
                tokens_used=tokens,
            ),
            memories=tuple(memories.values()),
            notes=tuple(notes.values()),
            tool_trace=tool_trace,
            stop_reason=stop_reason,
        )

    def execute(self, space_id: str, name: str, arguments: Any) -> ToolObservation:
        started = time.perf_counter()
        tool = self.tools.get(name)
        result = AskToolResult()
        try:
            if tool is None:
                status = "unknown_tool"
            else:
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be an object")
                tool.validate(arguments)
                status = "ok"
        except (TypeError, ValueError, ValidationError):
            status = "invalid_arguments"

        if status == "ok":
            try:
                raw_result = tool.execute(space_id, arguments)
                result = AskToolResult(
                    data=raw_result.data,
                    memories=tuple(
                        m for m in raw_result.memories
                        if m.space_id == space_id and m.status != "forgotten"
                    ),
                    notes=tuple(
                        n for n in raw_result.notes if n.space_id == space_id and not n.is_forgotten
                    ),
                )
            except Exception:
                _LOGGER.exception("Ask tool execution failed: %s", name)
                status = "tool_error"
        return ToolObservation(
            name, arguments if isinstance(arguments, dict) else {}, status, result,
            round((time.perf_counter() - started) * 1000, 2),
        )


def _coerce_sufficient_flag(raw: Any) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
    return None


def _validate_ask_payload(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    answer = data.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return None
    suff = _coerce_sufficient_flag(data.get("has_sufficient_evidence"))
    if suff is None:
        return None
    out: dict[str, Any] = {
        "answer": answer,
        "has_sufficient_evidence": suff,
    }
    for key in ("cited_memory_ids", "cited_note_ids"):
        values = data.get(key)
        if values is None:
            out[key] = []
        elif isinstance(values, list) and all(isinstance(v, str) for v in values):
            out[key] = values
        else:
            return None
    return out


def _parse_ask_answer(content: Any) -> dict[str, Any] | None:
    if not isinstance(content, str):
        return None
    text = content.strip()
    if not text:
        return None

    candidates: list[Any] = []
    try:
        candidates.append(json.loads(text))
    except ValueError:
        pass

    for m in re.finditer(r"```(?:json)?[ \t]*\n?(.*?)\n?[ \t]*```", text, flags=re.IGNORECASE | re.DOTALL):
        try:
            candidates.append(json.loads(m.group(1).strip()))
        except ValueError:
            pass

    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text, idx)
        except ValueError:
            continue
        candidates.append(obj)

    for obj in reversed(candidates):
        validated = _validate_ask_payload(obj)
        if validated is not None:
            return validated
    return None
