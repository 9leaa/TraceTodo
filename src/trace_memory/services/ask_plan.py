"""Plan Ask's first batch of searches and read-only calls."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

MAX_QUESTIONS = 3

_PLAN_CUES = re.compile(
    r"[？?].+|[，,；;].*(?:我|哪里|什么|怎么)|分别|各自|另外|顺便|同时|以及|和|与|还有|哪些|多少|几[条个项件]|"
    r"待办|任务|[Qq][1-4]|说了|提过|记了|录入|什么话|怎么说|说过|写了|记录|日志|\b(?:and|tasks?|today)\b"
)


@dataclass(frozen=True)
class PlannedTool:
    question_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AskPlan:
    questions: tuple[str, ...]
    tools: tuple[PlannedTool, ...] = ()
    mode: str = "direct"

    @classmethod
    def parse(cls, data: dict[str, Any], available_tools: set[str]) -> AskPlan:
        if not isinstance(data, dict):
            raise ValueError("Ask plan must be an object")
        questions = data.get("questions")
        if data.get("overflow") is True or isinstance(questions, list) and len(questions) > MAX_QUESTIONS:
            return cls((), mode="overflow")
        if not isinstance(questions, list) or not questions or not all(
            isinstance(q, str) and q.strip() and len(q) <= 2000 for q in questions
        ):
            raise ValueError("Invalid Ask plan questions")
        calls = data.get("tool_calls", [])
        if not isinstance(calls, list) or len(calls) > MAX_QUESTIONS:
            raise ValueError("Invalid Ask plan tool list")
        tools = []
        for call in calls:
            if not isinstance(call, dict):
                raise ValueError("Invalid Ask plan tool call")
            goal, name, args = call.get("question"), call.get("name"), call.get("arguments")
            if (
                type(goal) is not int or not 1 <= goal <= len(questions)
                or not isinstance(name, str) or name not in available_tools
                or not isinstance(args, dict)
            ):
                raise ValueError("Invalid Ask plan tool call")
            tools.append(PlannedTool(f"q{goal}", name, args))
        return cls(tuple(q.strip() for q in questions), tuple(tools), mode="planned")

    def to_trace(self) -> dict[str, Any]:
        return {
            "status": self.mode,
            "questions": [
                {"question_id": f"q{i}", "query": q}
                for i, q in enumerate(self.questions, 1)
            ],
            "tool_calls": [
                {"question_id": tool.question_id, "name": tool.name, "arguments": tool.arguments}
                for tool in self.tools
            ],
        }


def needs_planning(question: str) -> bool:
    return bool(_PLAN_CUES.search(question))
