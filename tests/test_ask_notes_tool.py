"""Verify the note-date tool and its Ask integration."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.domain.notes import RawNote
from trace_memory.llm.openai_compat import OpenAICompatLLMProvider


class TestAskNotesTool(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app = AppContainer.build(
            TraceMemoryConfig(data_dir=Path(temp.name), default_space="qa"),
            llm_override=OpenAICompatLLMProvider("https://example.test/v1", "test", "test"),
        )
        self.addCleanup(self.app.close)
        self.tool = self.app.ask_service.react.tools["list_notes_by_date"]

    def note(self, key, created_at, space_id="qa", **options):
        return self.app.note_repo.save(RawNote(
            note_id=key, space_id=space_id, created_at=created_at,
            content=options.pop("content", key), **options,
        ))

    def test_recording_date_scope_and_time_boundaries(self):
        first = self.note("note_start", "2026-11-18T16:00:00+00:00",
                          event_time="2025-08-02T00:00:00+08:00")
        last = self.note("note_last", "2026-11-19T23:59:59+08:00")
        self.note("note_next", "2026-11-19T16:00:00+00:00",
                  event_time="2026-11-19T12:00:00+08:00")
        self.note("note_before", "2026-11-18T15:59:59+00:00")
        self.note("note_other_space", "2026-11-19T12:00:00+08:00", space_id="other")
        self.note("note_forgotten", "2026-11-19T12:00:00+08:00", is_forgotten=True)
        result = self.tool.execute("qa", {"start_date": "2026-11-19"})
        self.assertEqual([n.note_id for n in result.notes], [first.note_id, last.note_id])
        self.assertEqual(result.notes[0].created_at, "2026-11-19T00:00:00+08:00")
        self.assertEqual(result.notes[0].event_time, first.event_time)
        self.assertEqual(result.data["total"], 2)

    def test_total_and_paging_with_mixed_timezone_timestamps(self):
        for index in range(10):
            day, hour, offset = (18, 16, "+00:00") if index % 2 else (19, 0, "+08:00")
            self.note(f"note_{index}", f"2026-11-{day:02d}T{hour:02d}:0{index}:00{offset}")
        args = {"start_date": "2026-11-19", "end_date": "2026-11-20", "limit": 3}
        first = self.tool.execute("qa", args)
        second = self.tool.execute("qa", {**args, "offset": first.data["next_offset"]})
        beyond = self.tool.execute("qa", {**args, "offset": 20})
        self.assertEqual(first.data["total"], 10)
        self.assertEqual(second.data["total"], 10)
        self.assertEqual([n.note_id for n in (*first.notes, *second.notes)],
                         [f"note_{i}" for i in range(6)])
        self.assertEqual(second.data["next_offset"], 6)
        self.assertEqual(beyond.notes, ())
        self.assertEqual(beyond.data["total"], 10)
        self.assertIsNone(beyond.data["next_offset"])

    def test_ask_tools_return_visible_original_notes_as_citations(self):
        note = self.note(
            "note_for_ask", "2026-11-19T11:00:00+08:00",
            content="给导师发了论文初稿，内部编号 SCOPE42。", forgotten_spans=("SCOPE42",),
        )
        self.note("note_outside_day", "2026-11-20T11:00:00+08:00")
        call = {
            "id": "date_call", "type": "function",
            "function": {
                "name": "list_notes_by_date", "arguments": '{"start_date":"2026-11-19"}',
            },
        }

        def reply(_url, _key, payload, **_options):
            if payload["messages"][-1]["role"] == "user":
                return {"choices": [{"message": {"content": None, "tool_calls": [call]}}]}
            observation = json.loads(payload["messages"][-1]["content"])
            self.assertEqual(observation["data"]["total"], 1)
            self.assertEqual(observation["notes"][0]["note_id"], note.note_id)
            self.assertNotIn("SCOPE42", observation["notes"][0]["content"])
            return {"choices": [{"message": {"content": json.dumps({
                "answer": "11 月 19 日你说给导师发了论文初稿。",
                "cited_memory_ids": [], "cited_note_ids": [note.note_id],
                "has_sufficient_evidence": True,
            })}}]}

        with patch("trace_memory.llm.openai_compat._post_json_with_retry", side_effect=reply):
            code, result = ApiDispatcher(self.app).dispatch(
                "POST", "/api/ask", body={"question": "11月19日我说了什么？"}
            )
        self.assertEqual(code, 200)
        self.assertEqual([n["note_id"] for n in result["citations"]["notes"]], [note.note_id])
        trace = self.app.trace_repo.list_traces("qa")[0]
        self.assertEqual(trace["details"]["tool_calls"][0]["name"], "list_notes_by_date")

    def test_invalid_dates_and_filters(self):
        for args in (
            {}, {"start_date": None}, {"start_date": "2026-02-30"},
            {"start_date": "2026-11-20", "end_date": "2026-11-19"},
            {"start_date": "2026-11-19", "limit": 0},
            {"start_date": "2026-11-19", "space_id": "other"},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.tool.validate(args)
