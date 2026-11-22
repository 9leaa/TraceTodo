"""Read-only task listing for Ask."""

from __future__ import annotations

from datetime import date
from typing import Any

from trace_memory.domain.memories import QUADRANTS, TASK_STATUSES
from trace_memory.domain.notes import beijing_now
from trace_memory.services.ask_react import AskTool, AskToolResult
from trace_memory.services.task_matrix_service import TaskMatrixService


_STATUSES = (*TASK_STATUSES, "all")
_QUADRANT_CODES = {quadrant.split("_", 1)[0]: quadrant for quadrant in QUADRANTS}
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 30


def make_list_tasks_tool(task_service: TaskMatrixService) -> AskTool:
    properties = {
        "status": {
            "type": "string",
            "enum": list(_STATUSES),
            "default": "todo",
            "description": "todo=未完成，done=已结束（含完成或取消），all=全部",
        },
        "quadrants": {
            "type": "array",
            "items": {"type": "string", "enum": list(_QUADRANT_CODES)},
            "description": "指定一个或多个四象限，省略时查所有象限",
        },
        "due_on": {
            "type": "string",
            "format": "date",
            "description": "只查北京时间指定日期到期的任务，格式为 YYYY-MM-DD",
        },
        "limit": {
            "type": "integer", "minimum": 1, "maximum": _MAX_LIMIT, "default": _DEFAULT_LIMIT,
        },
        "offset": {
            "type": "integer", "minimum": 0, "default": 0,
            "description": "后续页使用上一页返回的 next_offset",
        },
    }

    def validate(args: dict[str, Any]) -> None:
        if args.keys() - properties.keys() or args.get("status", "todo") not in _STATUSES:
            raise ValueError("Invalid task filters")
        quadrants = args.get("quadrants", [])
        if not isinstance(quadrants, list) or any(
            not isinstance(code, str) or code not in _QUADRANT_CODES for code in quadrants
        ):
            raise ValueError("quadrants must contain Q1–Q4")
        if "due_on" in args and date.fromisoformat(args["due_on"]).isoformat() != args["due_on"]:
            raise ValueError("due_on must be YYYY-MM-DD")
        limit = args.get("limit", _DEFAULT_LIMIT)
        offset = args.get("offset", 0)
        if type(limit) is not int or not 1 <= limit <= _MAX_LIMIT:
            raise ValueError("limit out of range")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative integer")

    def execute(space_id: str, args: dict[str, Any]) -> AskToolResult:
        now = beijing_now()
        offset = args.get("offset", 0)
        limit = args.get("limit", _DEFAULT_LIMIT)
        status = args.get("status", "todo")
        quadrants = args.get("quadrants", [])
        due_on = args.get("due_on")
        tasks, total = task_service.query_task_page(
            space_id,
            task_status=status,
            quadrants={_QUADRANT_CODES[code] for code in quadrants},
            due_on=date.fromisoformat(due_on) if due_on else None,
            limit=limit,
            offset=offset,
            now=now,
        )
        end = offset + len(tasks)
        return AskToolResult(
            data={
                "total": total,
                "offset": offset,
                "next_offset": end if end < total else None,
                "status": status,
                "quadrants": quadrants,
                "due_on": due_on,
                "as_of": now.isoformat(),
            },
            memories=tasks,
        )

    return AskTool(
        name="list_tasks",
        description=(
            "查询当前空间的任务清单和准确总数。一旦用户问还有哪些任务、"
            "任务数量、未完成状态或 Q1–Q4 清单，使用此工具获取当前结果。"
            "默认查询未完成任务，按优先级排序。"
            "以返回的 task_status、quadrant 和 total 为准。"
            "回答时展示本页任务，若 next_offset 非空，说明还有更多。"
        ),
        parameters={"type": "object", "properties": properties, "additionalProperties": False},
        validate=validate,
        execute=execute,
    )
