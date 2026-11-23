import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch

from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.domain.notes import RawNote
from trace_memory.llm.openai_compat import OpenAICompatLLMProvider
from trace_memory.llm.protocol import AskTurn
from trace_memory.services.ask_react import AskTool, AskToolResult


class PlannerModel(OpenAICompatLLMProvider):
    def __init__(self):
        super().__init__("https://example.test/v1", "test", "test")
        self.plan = {}
        self.planner_calls = []
        self.answer_inputs = []
        self.turns = []

    def plan_ask(self, question, tools):
        self.planner_calls.append(question)
        return self.plan, 9

    def ask_turn(self, messages, tools, *, allow_tools):
        self.answer_inputs.append(list(messages))
        return self.turns.pop(0)

    @staticmethod
    def answer(note_ids=()):
        return AskTurn({"content": json.dumps({
            "answer": "这是回答",
            "cited_note_ids": list(note_ids),
            "cited_memory_ids": [],
            "has_sufficient_evidence": bool(note_ids),
        })}, 12)


class TestAskPlanPipeline(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.model = PlannerModel()
        self.executions = []
        self.on_execute = lambda: None
        self.tool_note = RawNote(note_id="note_tool", space_id="qa", content="首批工具原话")
        self.other_note = RawNote(note_id="note_other_space", space_id="elsewhere", content="私密")
        tool = AskTool(
            name="inspect", description="test", parameters={"type": "object"},
            validate=lambda args: None, execute=self.execute,
        )
        self.app = AppContainer.build(
            TraceMemoryConfig(data_dir=Path(temp.name)),
            llm_override=self.model, ask_tools=(tool,),
        )
        self.addCleanup(self.app.close)

    def execute(self, space_id, args):
        self.executions.append((space_id, args))
        self.on_execute()
        note = self.tool_note if args["kind"] == "first" else RawNote(
            note_id="note_follow", space_id="qa", content="后续工具原话"
        )
        return AskToolResult(data={"total": 1}, notes=(note, self.other_note))

    def trace(self):
        return self.app.trace_repo.list_traces("qa")[0]

    def test_simple_question_is_planned_and_retrieved_verbatim(self):
        question = "实习哪天结束？"
        self.model.plan = {"questions": ["实习结束日期"], "tool_calls": []}
        self.model.turns = [self.model.answer()]
        self.app.ask_service.ask("qa", question)
        self.assertEqual(self.model.planner_calls, [question])
        self.assertEqual(len(self.model.answer_inputs), 1)
        trace = self.trace()
        self.assertEqual(trace["llm_calls"], 2)
        pipeline = trace["details"]["pipeline"]
        self.assertEqual(pipeline["planning"]["status"], "planned")
        self.assertEqual(pipeline["planning"]["questions"][0]["query"], question)
        self.assertEqual(pipeline["retrievals"][0]["query"], question)
        self.assertEqual(pipeline["initial_tools"], [])

    def test_invalid_plan_falls_back_to_original_query(self):
        question = "我喜欢喝什么？"
        self.model.turns = [self.model.answer()]
        self.app.ask_service.ask("qa", question)
        pipeline = self.trace()["details"]["pipeline"]
        self.assertEqual(pipeline["planning"]["status"], "fallback")
        self.assertEqual(pipeline["planning"]["llm_calls"], 1)
        self.assertEqual(pipeline["retrievals"][0]["query"], question)
        self.assertEqual(pipeline["initial_tools"], [])
        self.assertEqual(pipeline["react"]["stop_reason"], "answer")

    def test_overflow_stops_before_retrieval_or_execution(self):
        self.model.plan = {"questions": ["A", "B", "C", "D"]}
        with patch.object(self.app.ask_service, "_retrieve") as retrieve:
            result = self.app.ask_service.ask("qa", "A？B？C？D？")
        retrieve.assert_not_called()
        self.assertEqual(result["planning_status"], "overflow")
        self.assertIn("3", result["answer"])
        self.assertEqual(self.executions, [])
        self.assertEqual(self.model.answer_inputs, [])
        self.assertEqual(self.trace()["details"]["pipeline"]["retrievals"], [])

    def test_parallel_plan_limits_initial_and_follow_up_tools(self):
        query = "咖啡偏好是什么，另外有哪些任务？"
        self.model.plan = {
            "questions": ["咖啡偏好", "有哪些任务"],
            "tool_calls": [
                {"question": 2, "name": "inspect", "arguments": {"kind": "first"}},
                {"question": 1, "name": "inspect", "arguments": {"kind": "extra"}},
            ],
        }
        shared = self.app.note_repo.save(RawNote(
            note_id="note_shared", space_id="qa", content="咖啡偏好 有哪些任务"
        ))
        self.model.turns = [
            AskTurn({"tool_calls": [
                {"id": "again", "type": "function",
                 "function": {"name": "inspect", "arguments": '{"kind":"first"}'}},
                {"id": "follow", "type": "function",
                 "function": {"name": "inspect", "arguments": '{"kind":"follow"}'}},
            ]}, 10),
            self.model.answer([shared.note_id, "note_tool", "note_follow", "note_other_space"]),
        ]
        initial = Barrier(3, timeout=3)
        self.on_execute = lambda: initial.wait() if len(self.executions) == 1 else None
        impl = self.app.ask_service._retrieve

        def retrieve(*args):
            initial.wait()
            return impl(*args)

        with patch.object(self.app.ask_service, "_retrieve", side_effect=retrieve):
            result = self.app.ask_service.ask("qa", query)

        self.assertEqual(self.executions, [("qa", {"kind": "first"})])
        first_model_input = self.model.answer_inputs[0][-1]["content"]
        bundle = json.loads(first_model_input.split("按子问题组织的证据与首批工具结果：\n")[1])
        self.assertEqual([g["tool_result_ids"] for g in bundle["questions"]], [[], ["t1"]])
        self.assertEqual([g["note_ids"] for g in bundle["questions"]], [[shared.note_id]] * 2)
        self.assertEqual([n["note_id"] for n in bundle["notes"]], [shared.note_id])
        self.assertNotIn("note_other_space", first_model_input)
        self.assertEqual(
            [n["note_id"] for n in result["citations"]["notes"]],
            [shared.note_id, "note_tool"],
        )
        trace = self.trace()
        stages = trace["details"]["pipeline"]
        self.assertEqual((trace["llm_calls"], trace["tokens_used"]), (3, 31))
        self.assertEqual([r["question_id"] for r in stages["retrievals"]], ["q1", "q2"])
        self.assertEqual([r["question_id"] for r in stages["planning"]["tool_calls"]], ["q2"])
        self.assertEqual([r["question_id"] for r in stages["initial_tools"]], ["q2"])
        self.assertEqual(stages["initial_tools"][0]["result"]["data"]["total"], 1)
        react = stages["react"]
        self.assertEqual([r["status"] for r in react["rounds"]], ["tool_calls", "answer"])
        self.assertEqual([r["status"] for r in react["follow_up_tools"]], ["reused", "tool_limit"])
        self.assertEqual(react["result"]["cited_note_ids"], [shared.note_id, "note_tool"])
        for row in [stages["planning"], *stages["retrievals"], *stages["initial_tools"],
                    react, *react["rounds"], *react["follow_up_tools"]]:
            self.assertGreaterEqual(row["duration_ms"], 0)
