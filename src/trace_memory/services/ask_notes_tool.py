"""Date-based lookup of user input notes for Ask."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from trace_memory.domain.notes import BEIJING_TZ, to_beijing
from trace_memory.services.ask_react import AskTool, AskToolResult
from trace_memory.storage.repositories import NoteRepository


_DEFAULT_LIMIT = 20
_MAX_LIMIT = 30


def _parse_date(value: Any) -> date:
    if not isinstance(value, str):
        raise ValueError("Date must be YYYY-MM-DD")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Date must be YYYY-MM-DD")
    return parsed


def _beijing_recorded_at(value: str) -> str:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return to_beijing(timestamp).isoformat()


def make_list_notes_by_date_tool(note_repo: NoteRepository) -> AskTool:
    properties = {
        "start_date": {
            "type": "string",
            "format": "date",
            "description": "北京时间起始日期，格式 YYYY-MM-DD。查一天时只传此字段。",
        },
        "end_date": {
            "type": "string",
            "format": "date",
            "description": "北京时间结束日期，包含当天。省略时等于 start_date。",
        },
        "limit": {
            "type": "integer", "minimum": 1, "maximum": _MAX_LIMIT, "default": _DEFAULT_LIMIT,
        },
        "offset": {
            "type": "integer", "minimum": 0, "default": 0,
            "description": "后续页使用上一页返回的 next_offset。",
        },
    }

    def validate(args: dict[str, Any]) -> None:
        if args.keys() - properties.keys():
            raise ValueError("Unknown note filters")
        start = _parse_date(args.get("start_date"))
        end = _parse_date(args.get("end_date", args["start_date"]))
        if end < start or end == date.max:
            raise ValueError("Invalid note date range")
        limit = args.get("limit", _DEFAULT_LIMIT)
        offset = args.get("offset", 0)
        if type(limit) is not int or not 1 <= limit <= _MAX_LIMIT:
            raise ValueError("limit out of range")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative integer")

    def execute(space_id: str, args: dict[str, Any]) -> AskToolResult:
        start = date.fromisoformat(args["start_date"])
        end = date.fromisoformat(args.get("end_date", args["start_date"]))
        start_time = datetime.combine(start, time.min, tzinfo=BEIJING_TZ)
        end_time = datetime.combine(end + timedelta(days=1), time.min, tzinfo=BEIJING_TZ)
        offset = args.get("offset", 0)
        limit = args.get("limit", _DEFAULT_LIMIT)
        notes, total = note_repo.page_notes_by_created_at(
            space_id, start_time.isoformat(), end_time.isoformat(), limit, offset
        )
        read_count = offset + len(notes)
        return AskToolResult(
            data={
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "timezone": "Asia/Shanghai",
                "total": total,
                "offset": offset,
                "next_offset": read_count if read_count < total else None,
            },
            notes=tuple(
                replace(note, created_at=_beijing_recorded_at(note.created_at))
                for note in notes
            ),
        )

    return AskTool(
        name="list_notes_by_date",
        description=(
            "按原话实际录入日期查当前空间的记录。适用于『11月19日我说了什么』、"
            "『昨天我都记录了什么』和『那天记了几条』。根据北京时间锚点把相对日期换成具体日期，"
            "未写年份时按锚点所在年份。按 created_at 查询，事件发生时间请使用相关记忆证据。"
            "回答日期清单时以此工具返回的 notes 和 total 为准，按录入顺序列出本页原话。"
            "显示录入时间时直接使用已转为北京时间的 created_at。"
            "next_offset 为空时已查到末页，若不为空则说明还有更多。"
        ),
        parameters={
            "type": "object", "properties": properties,
            "required": ["start_date"], "additionalProperties": False,
        },
        validate=validate,
        execute=execute,
    )
