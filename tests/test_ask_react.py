"""Exercise the Ask tool loop through its usual API and model adapter."""

from __future__ import annotations

from dataclasses import replace
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
from trace_memory.services.ask_react import AskTool, AskToolResult


def _response(*, calls=(), answer="42", notes=()):
    content = None if calls else json.dumps({
        "answer": answer,
        "cited_memory_ids": [],
        "cited_note_ids": list(notes),
        "has_sufficient_evidence": True,
    })
    return {
        "choices": [{"message": {"role": "assistant", "content": content, "tool_calls": calls}}],
        "usage": {"total_tokens": 10},
    }


def _tool_call(call_id, name="get_test_value", arguments="{}"):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": arguments,
    }}


class TestAskReAct(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.executions = []
        self.local_note = RawNote(note_id="note_test", space_id="qa", content="查到的数值是 73841")
        self.other_note = RawNote(note_id="note_other", space_id="other", content="不属于当前空间")

        def validate(arguments):
            if arguments:
                raise ValueError("Unexpected parameters")

        def execute(space_id, arguments):
            self.executions.append((space_id, arguments))
            return AskToolResult(notes=(self.local_note, self.other_note))

        tool = AskTool(
            name="get_test_value",
            description="Retrieve the test value",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            validate=validate,
            execute=execute,
        )
        self.app = AppContainer.build(
            TraceMemoryConfig(data_dir=Path(temp.name), default_space="qa"),
            llm_override=OpenAICompatLLMProvider("https://example.test/v1", "test", "test"),
            ask_tools=(tool,),
        )
        self.addCleanup(self.app.close)
        self.api = ApiDispatcher(self.app)

    def ask(self, question, *turns):
        payloads = []

        def reply(_url, _api_key, payload, **_options):
            payloads.append(json.loads(json.dumps(payload)))
            if len(payloads) == 1:
                self.assertNotIn("tools", payload)
                return {
                    "choices": [{"message": {"content": json.dumps({
                        "questions": [question], "tool_calls": [],
                    })}}],
                    "usage": {"total_tokens": 10},
                }
            return turns[len(payloads) - 2]

        with patch("trace_memory.llm.openai_compat._post_json_with_retry", side_effect=reply):
            status, result = self.api.dispatch("POST", "/api/ask", body={"question": question})
        self.assertEqual(status, 200, result)
        self.assertEqual(len(payloads), len(turns) + 1)
        trace = next(row for row in self.app.trace_repo.list_traces("qa")
                     if row["trace_id"] == result["trace_id"])
        self.assertEqual(trace["llm_calls"], len(payloads))
        self.assertEqual(trace["tokens_used"], len(payloads) * 10)
        return result, payloads[1:], trace["details"]

    def test_direct_answer_with_tools_is_one_react_call(self):
        initial = RawNote(note_id="note_initial", space_id="qa", content="测试数值是 42")
        self.app.note_repo.save(initial)
        result, payloads, trace = self.ask("测试数值是什么", _response(notes=[initial.note_id]))
        self.assertEqual(payloads[0]["tool_choice"], "auto")
        self.assertIn(initial.note_id, payloads[0]["messages"][1]["content"])
        self.assertEqual(result["answer"], "42")
        self.assertEqual(result["citations"]["notes"][0]["note_id"], initial.note_id)
        self.assertEqual(self.executions, [])
        self.assertEqual(trace["tool_calls"], [])

    def test_fenced_direct_answer_stays_in_one_react_call(self):
        turn = _response()
        msg = turn["choices"][0]["message"]
        msg["content"] = "根据提供的证据。\n```json\n" + msg["content"] + "\n```"
        result, _, trace = self.ask("测试数值是什么", turn)
        self.assertEqual(result["answer"], "42")
        self.assertEqual(trace["stop_reason"], "answer")

    def test_tool_answer_with_empty_initial_context_and_scoped_citations(self):
        result, payloads, trace = self.ask(
            "测试数值是什么",
            _response(calls=[_tool_call("call_1")]),
            _response(answer="73841", notes=["note_test", "note_other", "note_unknown"]),
        )
        self.assertEqual(self.executions, [("qa", {})])
        self.assertEqual([req["tool_choice"] for req in payloads], ["auto", "none"])
        first, second = (req["messages"] for req in payloads)
        self.assertEqual(second[:2], first)
        self.assertEqual(second[2]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(second[3]["tool_call_id"], "call_1")
        tool_result = json.loads(second[3]["content"])
        self.assertEqual([note["note_id"] for note in tool_result["notes"]], ["note_test"])
        self.assertEqual(result["answer"], "73841")
        self.assertEqual([n["note_id"] for n in result["citations"]["notes"]], ["note_test"])
        self.assertEqual(trace["tool_calls"][0]["status"], "ok")

    def test_invalid_calls_count_toward_limit_and_get_tool_messages(self):
        for invalid, status in (
            (_tool_call("call_1", "unknown"), "unknown_tool"),
            (_tool_call("call_1", arguments='{"unexpected":true}'), "invalid_arguments"),
        ):
            with self.subTest(status=status):
                _, payloads, trace = self.ask(
                    "测试数值是什么",
                    _response(calls=[invalid, _tool_call("call_2")]),
                    _response(answer="73841"),
                )
                self.assertEqual(self.executions, [])
                self.assertEqual(payloads[-1]["tool_choice"], "none")
                self.assertEqual(
                    [m["tool_call_id"] for m in payloads[-1]["messages"][3:]],
                    ["call_1", "call_2"],
                )
                self.assertEqual([call["status"] for call in trace["tool_calls"]],
                                 [status, "tool_limit"])
                self.assertEqual(trace["stop_reason"], "tool_limit")

    def test_tool_failure_returns_an_observation(self):
        def fail(_space_id, _args):
            raise RuntimeError("simulated internal failure")

        tool = self.app.ask_service.react.tools["get_test_value"]
        self.app.ask_service.react.tools[tool.name] = replace(tool, execute=fail)
        with self.assertLogs("trace_memory.services.ask_react", "ERROR"):
            _, payloads, trace = self.ask(
                "测试数值是什么", _response(calls=[_tool_call("call_1")]),
                _response(answer="本次查询没有返回证据"),
            )
        self.assertEqual(json.loads(payloads[-1]["messages"][-1]["content"]),
                         {"error": "tool_error"})
        self.assertEqual(trace["tool_calls"][0]["status"], "tool_error")

    def test_multiple_requests_in_one_round_execute_only_one_tool(self):
        _, payloads, trace = self.ask(
            "测试数值是什么",
            _response(calls=[_tool_call("call_1"), _tool_call("call_2")]),
            _response(answer="73841"),
        )
        self.assertEqual(self.executions, [("qa", {})])
        self.assertEqual([req["tool_choice"] for req in payloads], ["auto", "none"])
        self.assertEqual(
            [msg["tool_call_id"] for msg in payloads[-1]["messages"] if msg["role"] == "tool"],
            ["call_1", "call_2"],
        )
        self.assertEqual([call["status"] for call in trace["tool_calls"]], ["ok", "tool_limit"])
        self.assertEqual(trace["stop_reason"], "tool_limit")
