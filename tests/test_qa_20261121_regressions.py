"""Regression tests for bugs found in the 2026-11-21 test-environment QA pass."""

from __future__ import annotations

from pathlib import Path
import re
import tempfile
import unittest

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer


REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = REPO_ROOT / "src" / "trace_memory" / "api" / "dashboard.html"


def _build(tmp: str, **overrides) -> AppContainer:
    base = Path(tmp)
    return AppContainer.build(
        TraceMemoryConfig(
            default_space="qa_space",
            data_dir=base / "data",
            knowledge_dir=base / "knowledge",
            **overrides,
        )
    )


class TestVectorOnlyGate(unittest.TestCase):
    def test_config_defaults_by_provider(self) -> None:
        mock_cfg = TraceMemoryConfig.from_env(environ={"TRACE_MEMORY_LLM_PROVIDER": "mock"}, dotenv_path=Path("/nonexistent"))
        self.assertEqual(mock_cfg.vector_only_min_score, 0.0)
        real_cfg = TraceMemoryConfig.from_env(environ={"TRACE_MEMORY_LLM_PROVIDER": "openai"}, dotenv_path=Path("/nonexistent"))
        self.assertAlmostEqual(real_cfg.vector_only_min_score, 0.42)
        custom = TraceMemoryConfig.from_env(
            environ={"TRACE_MEMORY_LLM_PROVIDER": "openai", "TRACE_MEMORY_VECTOR_ONLY_MIN_SCORE": "0.5"},
            dotenv_path=Path("/nonexistent"),
        )
        self.assertAlmostEqual(custom.vector_only_min_score, 0.5)

    def test_gibberish_query_returns_nothing_when_gate_is_above_noise_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _build(tmp)
            try:
                app.ingest_service.record_note("qa_space", "明天必须修复微信支付回调超时 bug")
                # Gate disabled (mock default): pure-vector noise may surface
                baseline = app.retrieval_service.search("qa_space", "zzqqxx123")
                noise_scores = [h["vector_score"] for h in baseline["memories"] if h["bm25_rank"] is None]
                # Gate above every observed noise score: nothing pure-vector may survive
                app.search_index.vector_only_min_score = (max(noise_scores) + 0.01) if noise_scores else 0.99
                gated = app.retrieval_service.search("qa_space", "zzqqxx123")
                self.assertEqual(gated["memories"], [])
                # Keyword hits are never dropped by the gate
                kw = app.retrieval_service.search("qa_space", "微信支付")
                self.assertTrue(any(h["bm25_rank"] is not None for h in kw["memories"]))
            finally:
                app.close()


class TestIngestStatusAndMemoFacets(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.app = _build(self._tmp.name, auth_required=False)
        self.api = ApiDispatcher(self.app)

    def tearDown(self) -> None:
        self.app.close()
        self._tmp.cleanup()

    def test_recent_jobs_expose_changes_count(self) -> None:
        job_repo = self.app.job_repo
        note = self.app.ingest_service.record_note("qa_space", "随手记一句").note
        empty_job = job_repo.enqueue(space_id="qa_space", note_id=note.note_id, idempotency_key="qa-k1")
        job_repo.mark_completed(empty_job["job_id"], {"extraction_status": "completed", "changes_count": 0})
        full_job = job_repo.enqueue(space_id="qa_space", note_id=note.note_id, idempotency_key="qa-k2")
        job_repo.mark_completed(full_job["job_id"], {"extraction_status": "completed", "changes_count": 2})
        recent = {j["job_id"]: j for j in job_repo.get_space_status("qa_space")["recent_jobs"]}
        self.assertEqual(recent[empty_job["job_id"]]["changes_count"], 0)
        self.assertEqual(recent[full_job["job_id"]]["changes_count"], 2)

    def test_changes_count_parser(self) -> None:
        parse = type(self.app.job_repo)._changes_count
        self.assertEqual(parse('{"extraction_status": "completed", "changes_count": 0}'), 0)
        self.assertEqual(parse('{"changes_count": 3}'), 3)
        self.assertIsNone(parse("not json"))
        self.assertIsNone(parse(""))

    def test_memo_filter_pills_keep_unfiltered_totals(self) -> None:
        for i, pinned in enumerate([True, False, False]):
            code, _ = self.api.dispatch(
                "POST",
                "/api/memos/save",
                body={"space_id": "qa_space", "title": f"memo {i}", "content": f"内容 {i}", "pinned": pinned, "extract_memory": False},
            )
            self.assertEqual(code, 200)
        _, res = self.api.dispatch("GET", "/api/memos/list", {"space_id": ["qa_space"], "pinned_only": ["true"]})
        self.assertEqual(res["total"], 1)
        self.assertEqual(res["all_count"], 3)
        self.assertEqual(res["all_pinned_count"], 1)


class TestDashboardFrontendFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = DASHBOARD.read_text(encoding="utf-8")

    def test_quick_note_restores_input_on_failure(self) -> None:
        self.assertIn("restoreQuickInput", self.html)
        self.assertIn("内容已放回输入框", self.html)

    def test_non_json_error_bodies_do_not_leak_html(self) -> None:
        self.assertIn("looksHtml", self.html)

    def test_subtask_toggle_rolls_back_on_failure(self) -> None:
        self.assertRegex(self.html, r"async function toggleCardSubtask\(memId, subId, done, ver, el = null\)")
        self.assertIn("rollback();", self.html)

    def test_calendar_render_is_race_safe(self) -> None:
        self.assertIn("_calRenderSeq", self.html)
        self.assertGreaterEqual(self.html.count("seq !== _calRenderSeq"), 2)

    def test_editing_dateless_task_keeps_it_dateless(self) -> None:
        self.assertIn("quickDate(t ? 'clear' : 'today')", self.html)

    def test_rail_tip_strip_is_unicode_safe(self) -> None:
        self.assertNotIn("replace(/^[🎯🔥⚠️💡🧹🧠↻]\\s*/, '')", self.html)
        self.assertIn("\\p{Extended_Pictographic}", self.html)

    def test_quadrant_toast_uses_human_label(self) -> None:
        self.assertIn("QUAD_LABELS[quadrant]", self.html)

    def test_countdown_capsule_uses_calendar_days(self) -> None:
        self.assertNotIn("Math.ceil((nearest.dueMs - nowMs) / 86400000)", self.html)

    def test_no_raw_markdown_backticks_in_static_headings(self) -> None:
        static_part = self.html.split("<script", 1)[0]
        self.assertIsNone(re.search(r"<h3>[^<]*`[^<]*</h3>", static_part))
        self.assertNotIn("(`allow_registration`)", static_part)


if __name__ == "__main__":
    unittest.main()
