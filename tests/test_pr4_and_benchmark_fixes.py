"""Regression & improvement tests for P0 (ReAct fallback/parsing), P1, and P2 fixes."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.domain.memories import MemoryItem
from trace_memory.domain.notes import RawNote
from trace_memory.llm.mock_provider import DeterministicLLMProvider
from trace_memory.llm.openai_compat import OpenAICompatLLMProvider
from trace_memory.llm.protocol import ExtractedCandidate, ExtractionBatch
from trace_memory.services.ask_react import AskTool, AskToolResult, _parse_ask_answer
from trace_memory.storage.hybrid_search import HybridSearchIndex


class _StubEmbedder:
    dimension = 4

    def __init__(self, vectors: dict[str, list[float]] | None = None, default: list[float] | None = None) -> None:
        self._vectors = vectors or {}
        self._default = default or [1.0, 0.0, 0.0, 0.0]

    def embed(self, text: str) -> list[float]:
        return list(self._vectors.get(text, self._default))


class _FixedC05LLM(DeterministicLLMProvider):
    """Simulates LLM returning identical canonical_key despite differing assignees."""

    def extract_candidates(
        self,
        note_text: str,
        default_event_time: str,
        existing_memories: list[MemoryItem] | None = None,
    ) -> ExtractionBatch:
        assignee = "小王" if "小王" in note_text else "self"
        return ExtractionBatch(
            candidates=(
                ExtractedCandidate(
                    memory_type="task",
                    title=f"跟进线上发布检查({assignee})",
                    summary=note_text,
                    canonical_key="task:release-check",
                    payload={
                        "task_status": "todo",
                        "importance": 4,
                        "urgency": 3,
                        "assignee": assignee,
                    },
                    event_time=default_event_time,
                    quote=note_text,
                ),
            ),
            llm_calls=1,
            tokens_used=20,
        )


class TestPR4AndBenchmarkFixes(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = TraceMemoryConfig(
            data_dir=Path(self.temp.name),
            default_space="qa",
            default_daily_note_limit=500,
            default_daily_llm_limit=500,
            default_daily_token_limit=500_000,
            rate_limit_per_minute=200,
        )

    def test_p0_parse_ask_answer_tolerates_prefixes_think_blocks_and_string_bools(self) -> None:
        p1 = _parse_ask_answer(
            'JSON: {"answer": "曾经居住在杭州。", "cited_memory_ids": ["mem_1"], "has_sufficient_evidence": "true"}'
        )
        self.assertIsNotNone(p1)
        assert p1 is not None
        self.assertEqual(p1["answer"], "曾经居住在杭州。")
        self.assertEqual(p1["cited_memory_ids"], ["mem_1"])
        self.assertEqual(p1["cited_note_ids"], [])
        self.assertTrue(p1["has_sufficient_evidence"])

        p2 = _parse_ask_answer(
            '[Thinking] 检查完成，无需工具。\n{"answer": "未找到记录。", "has_sufficient_evidence": "false", "cited_memory_ids": [], "cited_note_ids": []}\n以上为最终结果。'
        )
        self.assertIsNotNone(p2)
        assert p2 is not None
        self.assertFalse(p2["has_sufficient_evidence"])
        self.assertIsNone(_parse_ask_answer('[{"id": "mem_1", "title": "raw list"}]'))
        self.assertIsNone(_parse_ask_answer("[Thinking] 证据充分，无需调用工具。"))

    def test_p0_react_invalid_answer_recovers_via_non_tool_synthesis_with_tool_evidence(self) -> None:
        tool_note = RawNote(
            note_id="note_from_tool",
            space_id="qa",
            content="2024年曾在杭州西湖区居住两年。",
        )
        tool = AskTool(
            name="fetch_residence_notes",
            description="Retrieve residence notes",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            validate=lambda args: None,
            execute=lambda space_id, args: AskToolResult(notes=(tool_note,)),
        )
        app = AppContainer.build(
            self.cfg,
            llm_override=OpenAICompatLLMProvider("https://example.test/v1", "k", "qwen"),
            ask_tools=(tool,),
        )
        self.addCleanup(app.close)
        api = ApiDispatcher(app)

        payloads: list[dict] = []

        def fake_post(_url, _key, payload, **_opts):
            payloads.append(json.loads(json.dumps(payload)))
            if len(payloads) == 1:
                self.assertNotIn("tools", payload)
                plan = {"questions": ["我以前住在哪里？"], "tool_calls": []}
                return {
                    "choices": [{"message": {"content": json.dumps(plan)}}],
                    "usage": {"total_tokens": 7},
                }
            if len(payloads) == 2:
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_res",
                                        "type": "function",
                                        "function": {"name": "fetch_residence_notes", "arguments": "{}"},
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"total_tokens": 11},
                }
            if len(payloads) == 3:
                # Simulate Qwen emitting plain [Thinking] text when tools are present
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "[Thinking] 已获取居住历史笔记，无需再调用工具。",
                            }
                        }
                    ],
                    "usage": {"total_tokens": 13},
                }
            # Recovery uses pure JSON mode and keeps the original tool observation.
            self.assertNotIn("tools", payload)
            tool_messages = [m for m in payload["messages"] if m["role"] == "tool"]
            self.assertEqual(len(tool_messages), 1)
            self.assertIn("note_from_tool", tool_messages[0]["content"])
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "answer": "您2024年曾在杭州西湖区居住。",
                                    "cited_memory_ids": [],
                                    "cited_note_ids": ["note_from_tool"],
                                    "has_sufficient_evidence": True,
                                }
                            ),
                        }
                    }
                ],
                "usage": {"total_tokens": 17},
            }

        with patch("trace_memory.llm.openai_compat._post_json_with_retry", side_effect=fake_post):
            code, out = api.dispatch("POST", "/api/ask", body={"question": "我以前住在哪里？"})

        self.assertEqual(code, 200, out)
        self.assertTrue(out["has_sufficient_evidence"])
        self.assertIn("杭州", out["answer"])
        self.assertEqual([n["note_id"] for n in out["citations"]["notes"]], ["note_from_tool"])
        trace = app.trace_repo.list_traces("qa")[0]
        self.assertEqual(trace["details"]["stop_reason"], "invalid_answer_recovered")
        self.assertEqual(trace["llm_calls"], 4)
        self.assertEqual(trace["tokens_used"], 48)

    def test_p1_default_preference_and_episodic_payloads_enable_propose_and_dream(self) -> None:
        app = AppContainer.build(self.cfg)
        self.addCleanup(app.close)
        api = ApiDispatcher(app)

        for mtype, title, summary in (
            ("preference", "Special!@#$%^&*()Chars", "喜欢无糖冷萃咖啡"),
            ("preference", "Unicode Café Résumé", "手冲偏好深烘豆"),
            ("episodic", "   Emoji 🚀🔥🧠 Title   ", "周末跑了杭州半程马拉松"),
            ("episodic", "上海夜跑", "周三晚世纪公园十公里夜跑"),
        ):
            code, res = api.dispatch(
                "POST",
                "/api/memories/propose",
                body={
                    "action": "create",
                    "memory_type": mtype,
                    "title": title,
                    "summary": summary,
                    "payload": {},
                },
            )
            self.assertEqual(code, 200, res)
            self.assertTrue(res["memory"]["permalink"])

        d_code, dream = api.dispatch("POST", "/api/dream", body={"max_llm_calls": 5, "max_tokens": 8000})
        self.assertEqual(d_code, 200, dream)
        self.assertGreaterEqual(dream["clusters_evaluated"], 1)
        self.assertGreaterEqual(dream["insights_processed"], 1)

    def test_p1_trace_redacts_newly_logged_forgotten_spans(self) -> None:
        app = AppContainer.build(self.cfg)
        self.addCleanup(app.close)
        api = ApiDispatcher(app)

        c1, p_res = api.dispatch(
            "POST",
            "/api/memories/propose",
            body={
                "action": "create",
                "memory_type": "semantic",
                "title": "私密凭据",
                "summary": "PublicGoal_0 但绝对不能泄露 PIN_7000",
            },
        )
        self.assertEqual(c1, 200, p_res)
        mem_id = p_res["memory"]["memory_id"]
        _, detail = api.dispatch("GET", "/api/memories/detail", {"id": [mem_id]})
        note_id = detail["sources"][0]["note_id"]

        cf, _ = api.dispatch("POST", "/api/notes/forget", body={"note_id": note_id, "span": "PIN_7000"})
        self.assertEqual(cf, 200)

        # Post-forget verification search must NOT re-persist PIN_7000 in trace_runs
        cs, sr = api.dispatch("GET", "/api/memories/search", {"q": ["PIN_7000"]})
        self.assertEqual(cs, 200)
        self.assertEqual(sr["memories"], [])

        _, tr_res = api.dispatch("GET", "/api/traces")
        tr_blob = json.dumps(tr_res["traces"], ensure_ascii=False)
        self.assertNotIn("PIN_7000", tr_blob)
        self.assertIn("[REDACTED]", tr_blob)

    def test_p1_hybrid_search_gibberish_ootd_pref_task_and_shorthand_bm25(self) -> None:
        # 1. CJK shorthand BM25 ("半马" matches "半程马拉松")
        app = AppContainer.build(self.cfg)
        self.addCleanup(app.close)
        api = ApiDispatcher(app)

        cm, mr = api.dispatch(
            "POST",
            "/api/memories/propose",
            body={
                "action": "create",
                "memory_type": "episodic",
                "title": "完成杭州半程马拉松两小时零五分",
                "summary": "长跑耐力半程马拉松比赛个人最好成绩",
            },
        )
        self.assertEqual(cm, 200, mr)
        _, sr_shorthand = api.dispatch("GET", "/api/memories/search", {"q": ["半马"], "top_k": ["5"]})
        self.assertEqual(len(sr_shorthand["memories"]), 1)
        self.assertIsNotNone(sr_shorthand["memories"][0]["bm25_rank"])

        # 2. Title included in synthetic RawNote for /api/notes/list?q=OpenSSL
        _, mr2 = api.dispatch(
            "POST",
            "/api/memories/propose",
            body={
                "action": "create",
                "memory_type": "task",
                "title": "升级 OpenSSL 修复 CVE-2026-1042 漏洞",
                "summary": "修复加密传输组件高危安全漏洞补丁",
            },
        )
        self.assertIn("memory", mr2)
        _, notes_sr = api.dispatch("GET", "/api/notes/list", {"q": ["OpenSSL"]})
        self.assertGreaterEqual(notes_sr["count"], 1)

        app2 = AppContainer.build(
            TraceMemoryConfig(
                data_dir=Path(self.temp.name) / "v2",
                default_space="qa",
                vector_only_min_score=0.50,
            ),
            embedder_override=_StubEmbedder(default=[0.56, 0.828, 0.0, 0.0]),
        )
        self.addCleanup(app2.close)
        from trace_memory.domain.proposals import ChangeProposal

        res_task = app2.change_service.apply(
            ChangeProposal(
                space_id="qa",
                action="create",
                memory_type="task",
                title="下个月系统学习 Rust 所有权与异步编程",
                summary="系统学习编程与异步语法",
                payload={"task_status": "todo", "importance": 4, "urgency": 3},
            )
        ).memory
        assert res_task is not None

        controlled_embedder = _StubEmbedder(
            vectors={
                res_task.searchable_text(): [1.0, 0.0, 0.0, 0.0],
                "nope_match_xyz": [0.56, 0.828, 0.0, 0.0],
                "x7y8z9w0q1": [0.56, 0.828, 0.0, 0.0],
                "深海热液喷口化能合成生态系统": [0.35, 0.936, 0.0, 0.0],
                "我最喜欢的编程语言是什么": [0.35, 0.936, 0.0, 0.0],
            },
            default=[1.0, 0.0, 0.0, 0.0],
        )
        from trace_memory.storage.vector_store import MEMORIES_COLLECTION

        # Store clean vector for res_task and run controlled search index
        app2.vector_store.upsert(
            collection=MEMORIES_COLLECTION,
            doc_id=res_task.memory_id,
            space_id="qa",
            vector=[1.0, 0.0, 0.0, 0.0],
            metadata={"status": "active", "memory_type": "task"},
        )
        idx = HybridSearchIndex(
            note_repo=app2.note_repo,
            memory_repo=app2.memory_repo,
            vector_store=app2.vector_store,
            embedder=controlled_embedder,
            vector_only_min_score=0.50,
        )
        self.assertEqual(idx.search_memories("qa", "nope_match_xyz"), [])
        self.assertEqual(idx.search_memories("qa", "x7y8z9w0q1"), [])
        self.assertEqual(idx.search_memories("qa", "深海热液喷口化能合成生态系统"), [])
        self.assertEqual(idx.search_memories("qa", "我最喜欢的编程语言是什么"), [])

    def test_p1_c05_assignee_isolation_in_ingest_and_prompt(self) -> None:
        app = AppContainer.build(self.cfg, llm_override=_FixedC05LLM())
        self.addCleanup(app.close)

        app.ingest_service.record_note("qa", "我明天跟进线上发布检查")
        app.ingest_service.record_note("qa", "让小王明天跟进线上发布检查")
        tasks = app.memory_repo.list_memories("qa", memory_type="task", status="active")
        self.assertEqual(len(tasks), 2)
        self.assertEqual(
            {t.payload.get("assignee") for t in tasks},
            {"self", "小王"},
        )

        # Verify OpenAICompatLLMProvider prompt includes assignee of existing tasks
        openai_llm = OpenAICompatLLMProvider("https://example.test/v1", "k", "m")
        captured: list[str] = []

        def fake_chat_json(_sys: str, usr: str):
            captured.append(usr)
            return {"candidates": []}, 5

        openai_llm._chat_json = fake_chat_json  # type: ignore[method-assign]
        openai_llm.extract_candidates("让小王也准备周报", "2026-11-22T12:00:00+08:00", existing_memories=tasks)
        self.assertIn("assignee=", captured[0])

    def test_p2_strict_out_of_range_400_validations(self) -> None:
        app = AppContainer.build(self.cfg)
        self.addCleanup(app.close)
        api = ApiDispatcher(app)

        bad_calls = [
            ("GET", "/api/memories", {"limit": ["0"]}, None),
            ("GET", "/api/memories", {"limit": ["101"]}, None),
            ("GET", "/api/memories", {"offset": ["-1"]}, None),
            ("GET", "/api/memories/search", {"q": ["test"], "top_k": ["0"]}, None),
            ("GET", "/api/memories/search", {"q": ["test"], "top_k": ["101"]}, None),
            ("POST", "/api/memories/propose", None, {"action": "create", "title": "x", "summary": "y", "expected_version": 0}),
            ("POST", "/api/memories/propose", None, {"action": "update", "title": "x", "summary": "y"}),
            ("POST", "/api/tasks/save", None, {"title": "非法象限", "quadrant": "Q9_invalid"}),
            ("POST", "/api/tasks/save", None, {"title": "越界重要度", "importance": 6}),
            ("POST", "/api/tasks/save", None, {"title": "越界紧急度", "urgency": 0}),
        ]
        for method, path, qp, body in bad_calls:
            with self.subTest(method=method, path=path, qp=qp, body=body):
                code, res = api.dispatch(method, path, query_params=qp, body=body)
                self.assertEqual(code, 400, f"expected 400 got {code}: {res}")
                self.assertEqual(res.get("code"), "VALIDATION_ERROR")

        # Empty string quadrant from client remains valid (auto-assigned)
        ok_code, ok_res = api.dispatch(
            "POST", "/api/tasks/save", body={"title": "正常任务", "importance": 4, "urgency": 3, "quadrant": ""}
        )
        self.assertEqual(ok_code, 200, ok_res)


if __name__ == "__main__":
    unittest.main()
