"""Verify task-list filtering and the read-only Ask integration."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.domain.memories import MemoryItem
from trace_memory.llm.openai_compat import OpenAICompatLLMProvider


_NOW = datetime.fromisoformat("2026-11-22T12:00:00+08:00")


class TestAskTaskTool(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app = AppContainer.build(
            TraceMemoryConfig(data_dir=Path(temp.name), default_space="qa"),
            llm_override=OpenAICompatLLMProvider("https://example.test/v1", "test", "test"),
        )
        self.addCleanup(self.app.close)
        clock = patch("trace_memory.services.ask_task_tool.beijing_now", return_value=_NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def make_task(
        self, identifier, *, space="qa", status="active", locked=False, updated_at="", **payload
    ):
        return self.app.memory_repo.create(
            MemoryItem(
                memory_id=identifier, space_id=space, memory_type="task",
                permalink=identifier, title=identifier, summary=identifier,
                payload=payload, status=status, user_locked=locked, updated_at=updated_at,
            ),
            change_reason="test", actor_id="test", origin="test",
        )

    def test_ask_calls_list_tasks_and_does_not_roll_over(self):
        stale = self.make_task(
            "mem_牡丹", importance=4, urgency=3,
            due_at="2026-11-21T10:00:00+08:00", auto_rollover=True,
        )
        self.make_task("mem_已完成", task_status="done")
        self.make_task("mem_跨空间", space="other")
        self.make_task("mem_历史", status="superseded")
        payloads = []

        def answer(_url, _key, payload, **_options):
            payloads.append(payload)
            if len(payloads) == 1:
                return {"choices": [{"message": {"content": json.dumps({
                    "questions": ["目前还有哪些任务没完成？"],
                    "tool_calls": [{"question": 1, "name": "list_tasks", "arguments": {"status": "todo"}}],
                })}}]}
            self.assertEqual(
                [t["function"]["name"] for t in payload["tools"]],
                ["list_tasks", "list_notes_by_date"],
            )
            bundle = json.loads(payload["messages"][-1]["content"].split(
                "按子问题组织的证据与首批工具结果：\n", 1
            )[1])
            observation = bundle["initial_tool_results"][0]
            self.assertEqual(observation["data"]["total"], 1)
            self.assertIsNone(observation["data"]["next_offset"])
            self.assertEqual([m["memory_id"] for m in observation["memories"]], [stale.memory_id])
            self.assertEqual(observation["memories"][0]["payload"]["quadrant"], "Q1_do_first")
            return {"choices": [{"message": {"content": json.dumps({
                "answer": "目前还有 1 个任务：牡丹。",
                "cited_memory_ids": [stale.memory_id], "cited_note_ids": [],
                "has_sufficient_evidence": True,
            })}}]}

        with patch("trace_memory.llm.openai_compat._post_json_with_retry", side_effect=answer):
            code, result = ApiDispatcher(self.app).dispatch(
                "POST", "/api/ask", body={"question": "目前还有哪些任务没完成？"}
            )
        self.assertEqual(code, 200)
        self.assertEqual(result["citations"]["memories"][0]["memory_id"], stale.memory_id)
        self.assertEqual(self.app.memory_repo.get("qa", stale.memory_id), stale)
        self.assertEqual(len(payloads), 2)
        trace = self.app.trace_repo.list_traces("qa")[0]["details"]["pipeline"]
        self.assertEqual(trace["initial_tools"][0]["name"], "list_tasks")
        self.assertEqual(trace["react"]["follow_up_tools"], [])

    def test_due_date_and_quadrants_are_filtered_before_paging(self):
        args = {"quadrants": ["Q1", "Q2"], "due_on": "2026-11-23", "limit": 1}
        self.make_task("mem_auto", importance=4, urgency=3, due_at="2026-11-22T20:00:00+00:00")
        self.make_task(
            "mem_locked", locked=True, importance=4, urgency=3, quadrant="Q2_schedule",
            priority_score=90, due_at="2026-11-23T09:00:00+08:00",
        )
        self.make_task("mem_q3", importance=2, urgency=5, due_at="2026-11-23")
        self.make_task("mem_wrong_day", importance=5, urgency=5, due_at="2026-11-22")
        tool = self.app.ask_service.react.tools["list_tasks"]
        first = tool.execute("qa", args)
        second = tool.execute("qa", {**args, "offset": first.data["next_offset"]})
        self.assertEqual(first.data["total"], 2)
        self.assertEqual(first.data["next_offset"], 1)
        self.assertEqual(first.memories[0].payload["quadrant"], "Q1_do_first")
        self.assertEqual(second.memories[0].payload["quadrant"], "Q2_schedule")
        self.assertIsNone(second.data["next_offset"])

    def test_old_tasks_still_match_and_bad_filters_are_rejected(self):
        self.make_task("mem_old_todo", updated_at="2020-01-01T00:00:00+00:00")
        for index in range(300):
            self.make_task(f"mem_done_{index}", task_status="done")
        tool = self.app.ask_service.react.tools["list_tasks"]
        result = tool.execute("qa", {})
        self.assertEqual(result.data["total"], 1)
        self.assertEqual(result.memories[0].memory_id, "mem_old_todo")
        for args in (
            {"status": "in_progress"}, {"quadrants": ["Q5"]},
            {"due_on": "2026-02-30"}, {"limit": True}, {"space_id": "other"},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                tool.validate(args)
