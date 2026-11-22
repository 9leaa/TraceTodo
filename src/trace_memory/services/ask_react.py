"""Execute bounded, read-only tool turns for Ask."""

from __future__ import annotations

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
_MAX_TOOL_CALLS = 3


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
        self, space_id: str, messages: list[dict[str, Any]]
    ) -> AskReActResult:
        messages = list(messages)
        definitions = [tool.definition() for tool in self.tools.values()]
        memories: dict[str, MemoryItem] = {}
        notes: dict[str, RawNote] = {}
        tool_trace: list[dict[str, Any]] = []
        remaining = _MAX_TOOL_CALLS
        tokens = 0
        calls = 0
        stop_reason = "answer"
        while True:
            turn = self.model.ask_turn(messages, definitions, allow_tools=remaining > 0)
            calls += 1
            tokens += turn.tokens_used
            requested = turn.message.get("tool_calls")
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
                started = time.perf_counter()
                function = call["function"]
                name = function["name"]
                if remaining:
                    remaining -= 1
                    status, content, result = self._execute(
                        space_id, name, function.get("arguments", "{}")
                    )
                    memories.update((m.memory_id, m) for m in result.memories)
                    notes.update((n.note_id, n) for n in result.notes)
                else:
                    status = "tool_limit"
                    content = json.dumps({"error": status})

                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": content,
                })
                tool_trace.append({
                    "name": name,
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                })

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

    def _execute(
        self, space_id: str, name: str, raw_arguments: str
    ) -> tuple[str, str, AskToolResult]:
        tool = self.tools.get(name)
        if tool is None:
            return "unknown_tool", '{"error":"unknown_tool"}', AskToolResult()
        try:
            arguments = json.loads(raw_arguments)
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be an object")
            tool.validate(arguments)
        except (TypeError, ValueError, ValidationError):
            return "invalid_arguments", '{"error":"invalid_arguments"}', AskToolResult()

        try:
            result = tool.execute(space_id, arguments)
            memories = tuple(
                m for m in result.memories if m.space_id == space_id and m.status != "forgotten"
            )
            notes = tuple(
                n for n in result.notes if n.space_id == space_id and not n.is_forgotten
            )
            content = json.dumps({
                "data": result.data,
                "memories": [
                    {
                        "memory_id": m.memory_id, "title": m.title, "summary": m.summary,
                        "assertion_kind": m.assertion_kind, "payload": m.payload,
                    }
                    for m in memories
                ],
                "notes": [
                    {
                        "note_id": n.note_id, "content": n.visible_content(),
                        "event_time": n.event_time, "created_at": n.created_at,
                    }
                    for n in notes
                ],
            }, ensure_ascii=False)
        except Exception:
            _LOGGER.exception("Ask tool execution failed: %s", name)
            return "tool_error", '{"error":"tool_error"}', AskToolResult()
        return "ok", content, AskToolResult(memories=memories, notes=notes)


def _parse_ask_answer(content: Any) -> dict[str, Any] | None:
    if not isinstance(content, str):
        return None
    content = content.strip()
    try:
        data = json.loads(content)
    except ValueError:
        fenced_json = re.search(
            r"```(?:json)?[ \t]*\n(.*?)\n[ \t]*```\s*$",
            content,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if fenced_json is None:
            return None
        try:
            data = json.loads(fenced_json.group(1))
        except ValueError:
            return None
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("answer"), str)
        or not data["answer"].strip()
        or not isinstance(data.get("has_sufficient_evidence"), bool)
    ):
        return None
    for key in ("cited_memory_ids", "cited_note_ids"):
        values = data.get(key)
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            return None
    return data
