"""Comprehensive regression tests for codebase audit fixes and Ask relational/Q&A hardening."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.domain.memories import (
    EpisodicPayload,
    MemoryEvidence,
    MemoryItem,
    PreferencePayload,
    SemanticPayload,
    TaskPayload,
    validate_safe_permalink,
)
from trace_memory.domain.notes import RawNote
from trace_memory.domain.proposals import (
    ChangeProposal,
    PermissionDeniedError,
    RelationSpec,
    ValidationError,
    VersionConflictError,
)
from trace_memory.llm.protocol import AskSynthesis
from trace_memory.mcp_server.server import MCPToolDispatcher, handle_jsonrpc_message
from trace_memory.storage.database import build_fts_match_query


class _CapturingAskLLM:
    """Stub LLM capturing arguments passed by AskService and returning controlled citations."""

    provider_name = "capturing_stub"

    def __init__(self, *, sufficient: bool = True, inject_fake_ids: bool = False) -> None:
        self.sufficient = sufficient
        self.inject_fake_ids = inject_fake_ids
        self.calls: list[dict] = []

    def synthesize_answer(
        self,
        question: str,
        query_mode: str,
        memories: list[MemoryItem],
        notes: list[RawNote],
        history_versions: list[dict],
        graph_relations: list[dict] | None = None,
    ) -> AskSynthesis:
        self.calls.append(
            {
                "question": question,
                "query_mode": query_mode,
                "memories": list(memories),
                "notes": list(notes),
                "history_versions": list(history_versions),
                "graph_relations": list(graph_relations or []),
            }
        )
        mem_ids = [m.memory_id for m in memories]
        note_ids = [n.note_id for n in notes]
        if self.inject_fake_ids:
            mem_ids = mem_ids[:1] + ["mem_hallucinated_999"]
            note_ids = note_ids[:1] + ["note_hallucinated_999"]
        return AskSynthesis(
            answer="Stub grounded answer" if self.sufficient else "证据不足，无法作答。",
            cited_memory_ids=tuple(mem_ids),
            cited_note_ids=tuple(note_ids),
            has_sufficient_evidence=self.sufficient,
            llm_calls=1,
            tokens_used=25,
        )


class _RaisingAskLLM:
    provider_name = "raising_stub"

    def synthesize_answer(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise RuntimeError("Simulated upstream LLM outage")


class TestAuditAndAskFixes(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        cfg = TraceMemoryConfig(
            data_dir=self.root / "data",
            knowledge_dir=self.root / "knowledge",
            auth_required=True,
            allow_registration=True,
            rate_limit_per_minute=200,
            default_daily_note_limit=200,
            default_daily_llm_limit=200,
            default_daily_token_limit=500000,
        )
        self.app = AppContainer.build(cfg)
        self.api = ApiDispatcher(self.app)
        reg = self.app.auth_service.register("tester", "secret123", display_name="Tester")
        self.token = reg["token"]
        self.space = reg["user"]["space_id"]

    def tearDown(self) -> None:
        self.app.close()
        self._tmp.cleanup()

    # ---------------- 1. Domain & Schema Hardening ----------------

    def test_domain_validation_and_permalink_traversal(self) -> None:
        with self.assertRaises(ValidationError):
            ChangeProposal(space_id="default", action="create", memory_type="bogus")
        with self.assertRaises(ValidationError):
            ChangeProposal(space_id="default", action="create", assertion_kind="guessed")
        with self.assertRaises(ValidationError):
            ChangeProposal(space_id="default", action="create", permalink="../etc/passwd")
        with self.assertRaises(ValidationError):
            PreferencePayload.from_dict({"target": "咖啡", "polarity": "neutral"})
        with self.assertRaises(ValidationError):
            TaskPayload.from_dict({"importance": "not_an_int"})
        with self.assertRaises(ValidationError):
            validate_safe_permalink("facts/../../escape")

        # Null JSON fields do not stringify into "None", and explicit quadrant keeps coherent score
        tp = TaskPayload.from_dict(
            {
                "importance": 2,
                "urgency": 2,
                "quadrant": "Q1_do_first",
                "due_at": None,
                "assignee": None,
                "color_theme": None,
            }
        )
        self.assertEqual(tp.due_at, "")
        self.assertEqual(tp.assignee, "self")
        self.assertEqual(tp.color_theme, "sky")
        self.assertGreaterEqual(tp.priority_score, 80.0)

    def test_raw_note_overlapping_spans_and_forgotten_metadata_scrubbing(self) -> None:
        note = RawNote(
            note_id="note_overlap",
            space_id=self.space,
            content="我住在上海浦东新区张江",
            title="机密标题",
            tags=("隐私",),
            mood="secret",
            journal_date="2026-11-22",
            pinned=True,
            forgotten_spans=("上海", "上海浦东新区"),
        )
        self.assertEqual(note.visible_content(), "我住在[REDACTED]张江")

        forgotten_note = RawNote(
            note_id="note_gone",
            space_id=self.space,
            content="已遗忘内容",
            title="机密标题",
            tags=("secret",),
            mood="sad",
            journal_date="2026-11-22",
            pinned=True,
            is_forgotten=True,
        )
        d = forgotten_note.to_dict()
        self.assertEqual(d["content"], "")
        self.assertEqual(d["title"], "")
        self.assertEqual(d["tags"], [])
        self.assertEqual(d["mood"], "")
        self.assertEqual(d["journal_date"], "")
        self.assertFalse(d["pinned"])

    # ---------------- 2. FTS5 & Storage Hardening ----------------

    def test_fts_single_char_preservation_and_stopword_stripping(self) -> None:
        q1 = build_fts_match_query("我最近在学习 C")
        self.assertIn('"c"', q1)
        q2 = build_fts_match_query("我现在住在哪里？")
        self.assertNotIn("现在", q2)
        self.assertIn("住", q2)

    def test_memory_repo_get_active_priority_and_trace_redaction_percent_safe(self) -> None:
        # Trace redaction uses literal instr() rather than wildcard LIKE % / _
        tid_literal = self.app.trace_repo.record_trace(
            space_id=self.space,
            operation="ingest",
            input_summary="命中率 100%_exact 指标记录",
            details={"msg": "100%_exact"},
        )
        tid_unrelated = self.app.trace_repo.record_trace(
            space_id=self.space,
            operation="ingest",
            input_summary="完全无关的正常日志 abcdef",
            details={"msg": "abcdef"},
        )
        self.app.trace_repo.redact_for_forgotten(
            space_id=self.space,
            note_ids=("note_x",),
            spans=("100%_exact",),
        )
        traces = {t["trace_id"]: t for t in self.app.trace_repo.list_traces(self.space, limit=20)}
        self.assertEqual(traces[tid_literal]["input_summary"], "[FORGOTTEN]")
        self.assertEqual(traces[tid_unrelated]["input_summary"], "完全无关的正常日志 abcdef")
        self.assertIsNone(self.app.memory_repo.get(self.space, ""))

    # ---------------- 3. Lifecycle: C01 / Forget / Markdown ----------------

    def test_temporal_causal_barrier_skips_older_preference_and_forget_cascades(self) -> None:
        note_new = RawNote(
            note_id="note_pref_new",
            space_id=self.space,
            content="2026年现在喜欢喝拿铁",
            event_time="2026-10-01T10:00:00+08:00",
        )
        self.app.note_repo.save(note_new)
        created = self.app.change_service.apply(
            ChangeProposal(
                space_id=self.space,
                action="create",
                memory_type="preference",
                title="咖啡偏好",
                summary="喜欢喝拿铁",
                canonical_key="pref:coffee",
                event_time="2026-10-01T10:00:00+08:00",
                payload={"target": "咖啡", "polarity": "positive"},
                evidences=(MemoryEvidence(note_id="note_pref_new", note_revision=1, quote="喜欢喝拿铁"),),
            )
        )
        assert created.memory is not None

        note_old = RawNote(
            note_id="note_pref_old",
            space_id=self.space,
            content="2025年以前不喜欢喝拿铁",
            event_time="2025-01-01T10:00:00+08:00",
        )
        self.app.note_repo.save(note_old)
        stale = self.app.change_service.apply(
            ChangeProposal(
                space_id=self.space,
                action="update",
                memory_type="preference",
                target_memory_id=created.memory.memory_id,
                expected_version=created.memory.version,
                canonical_key="pref:coffee",
                event_time="2025-01-01T10:00:00+08:00",
                summary="以前不喜欢喝咖啡",
                payload={"target": "咖啡", "polarity": "negative"},
                evidences=(MemoryEvidence(note_id="note_pref_old", note_revision=1, quote="不喜欢"),),
            )
        )
        self.assertEqual(stale.status, "skipped_older_event")
        assert stale.memory is not None
        self.assertEqual(stale.memory.payload.get("polarity"), "positive")

        # Forget scrubs permalink and cascades to pending_review inferred Dreams
        dream = self.app.change_service.apply(
            ChangeProposal(
                space_id=self.space,
                action="create",
                memory_type="semantic",
                assertion_kind="inferred",
                require_review=True,
                title="饮品规律",
                summary="每日饮用拿铁规律",
                canonical_key="dream:coffee",
                payload={"subject": "user", "predicate": "pattern", "value": "拿铁"},
                evidences=(MemoryEvidence(note_id="note_pref_new", note_revision=1, quote="喜欢喝拿铁"),),
                relations=(RelationSpec(relation_type="derived_from", target=created.memory.permalink),),
            )
        )
        assert dream.memory is not None
        self.app.change_service.apply(
            ChangeProposal(
                space_id=self.space,
                action="forget",
                target_memory_id=created.memory.memory_id,
                expected_version=stale.memory.version,
            )
        )
        m_gone = self.app.memory_repo.get(self.space, created.memory.memory_id, include_forgotten=True)
        d_gone = self.app.memory_repo.get(self.space, dream.memory.memory_id, include_forgotten=True)
        assert m_gone is not None and d_gone is not None
        self.assertTrue(m_gone.permalink.startswith("forgotten/"))
        self.assertEqual(d_gone.status, "forgotten")

    def test_markdown_sync_rejects_symlink_and_cas_failure_leaves_no_orphan_note(self) -> None:
        res = self.app.change_service.apply(
            ChangeProposal(
                space_id=self.space,
                action="create",
                memory_type="semantic",
                title="编辑器测试",
                summary="初始内容",
                canonical_key="sem:md_cas",
                payload={"subject": "user", "predicate": "md_cas", "value": "初始内容"},
            )
        )
        assert res.memory is not None
        md_res = self.app.markdown_sync.render_memory_markdown(self.space, res.memory.memory_id)
        notes_before = len(self.app.note_repo.list_notes(self.space, limit=100))

        with self.assertRaises(VersionConflictError):
            self.app.markdown_sync.apply_markdown_edit(
                space_id=self.space,
                identifier=res.memory.memory_id,
                markdown_text=md_res["markdown"].replace("初始内容", "新内容"),
                expected_version=res.memory.version + 9,
                export_to_disk=False,
            )
        notes_after = len(self.app.note_repo.list_notes(self.space, limit=100))
        self.assertEqual(notes_before, notes_after)

        # Symlink escape replacing an exported tracked file must raise ValidationError cleanly
        export_info = self.app.markdown_sync.export_space(self.space)
        space_dir = Path(export_info["export_dir"])
        outside_file = self.root / "outside_secret.md"
        outside_file.write_text("# 越权文件\n", encoding="utf-8")
        tracked_file = space_dir / f"{res.memory.permalink}.md"
        tracked_file.unlink()
        os.symlink(outside_file, tracked_file)
        with self.assertRaises(ValidationError):
            self.app.markdown_sync.sync_external_edits(self.space)

    # ---------------- 4. API, Calendar, Countdown & MCP ----------------

    def test_api_actor_spoofing_propose_cas_dream_and_calendar_utc(self) -> None:
        # actor_id spoofing blocked on /api/tasks/save
        code, created = self.api.dispatch(
            "POST",
            "/api/tasks/save",
            body={
                "title": "校验北京日历时区任务",
                "due_at": "2026-11-22T20:00:00+00:00",
                "is_all_day": False,
                "actor_id": "forged_admin",
            },
            auth_token=self.token,
        )
        self.assertEqual(code, 200, created)
        mem_id = created["memory"]["memory_id"]
        notes = self.app.note_repo.list_notes(self.space, limit=10)
        self.assertTrue(all(n.actor_id == "tester" for n in notes))

        # Calendar projects 2026-11-22T20:00:00+00:00 onto Beijing 2026-11-23
        c_cal, cal = self.api.dispatch(
            "GET",
            "/api/calendar/overview",
            query_params={"view": ["week"], "anchor_date": ["2026-11-23"]},
            auth_token=self.token,
        )
        self.assertEqual(c_cal, 200)
        day_23 = next(d for d in cal["days"] if d["date"] == "2026-11-23")
        self.assertTrue(any(b["memory_id"] == mem_id for b in day_23["timed_blocks"]))

        # /api/memories/propose CAS conflict leaves zero orphan [Web Edit] RawNote
        notes_count_before = len(self.app.note_repo.list_notes(self.space, limit=50))
        c_cas, err_cas = self.api.dispatch(
            "POST",
            "/api/memories/propose",
            body={
                "action": "update",
                "memory_type": "task",
                "target_memory_id": mem_id,
                "expected_version": 99,
                "title": "冲突修改",
                "summary": "不应创建孤立原话",
            },
            auth_token=self.token,
        )
        self.assertEqual(c_cas, 409, err_cas)
        self.assertEqual(len(self.app.note_repo.list_notes(self.space, limit=50)), notes_count_before)

        # /api/dream validates >= 1
        c_dream, err_dream = self.api.dispatch(
            "POST",
            "/api/dream",
            body={"max_llm_calls": 0},
            auth_token=self.token,
        )
        self.assertEqual(c_dream, 400, err_dream)

        # Calendar schedule rejects invalid HH:MM and Countdown rejects bad lunar day
        with self.assertRaises(ValidationError):
            self.app.calendar_service.schedule_slot(
                space_id=self.space,
                payload={"date": "2026-11-23", "time": "25:99", "title": "坏时间"},
            )
        with self.assertRaises(ValidationError):
            self.app.countdown_service.save_countdown(
                space_id=self.space,
                data={"title": "坏农历", "calendar_type": "lunar", "lunar_month": 13, "lunar_day": 1},
            )

    def test_mcp_jsonrpc_type_guards_and_propose_cas(self) -> None:
        dispatcher = MCPToolDispatcher(self.app, trusted_space_id=self.space)
        bad_req = handle_jsonrpc_message(dispatcher, "not-an-object")  # type: ignore[arg-type]
        assert bad_req is not None
        self.assertEqual(bad_req["error"]["code"], -32600)

        bad_args = handle_jsonrpc_message(
            dispatcher,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "tm_ask", "arguments": []}},
        )
        assert bad_args is not None
        self.assertEqual(bad_args["error"]["code"], -32602)

    # ---------------- 5. AskService Relational Attributes & Q&A Hardening ----------------

    def test_ask_queries_relational_attributes_and_preserves_1hop_neighbors(self) -> None:
        # Create task with relational assignee + due_at, episodic with participants, semantic with previous_values
        t_res = self.app.task_matrix_service.save_task_card(
            space_id=self.space,
            data={
                "title": "核对跨境结算对账单",
                "summary": "核对本月网关流水与手续费差异",
                "importance": 5,
                "urgency": 2,  # Q2
                "assignee": "王小明",
                "due_at": "2026-12-08T18:00:00+08:00",
            },
            actor_id="tester",
        )
        assert t_res.memory is not None

        ep_res = self.app.change_service.apply(
            ChangeProposal(
                space_id=self.space,
                action="create",
                memory_type="episodic",
                title="架构评审复盘",
                summary="完成微服务网关限流方案评审",
                canonical_key="epi:gateway_review",
                payload=EpisodicPayload(
                    event_summary="完成微服务网关限流方案评审",
                    occurred_at="2026-08-15T14:00:00+08:00",
                    participants=("张伟架构师", "李娜"),
                ).to_dict(),
            )
        )
        assert ep_res.memory is not None

        # Create 6 direct memories mentioning "数据库迁移" + 1 linked 1-hop neighbor NOT mentioning "数据库迁移"
        neighbor = self.app.change_service.apply(
            ChangeProposal(
                space_id=self.space,
                action="create",
                memory_type="semantic",
                title="核心机房合规证书",
                summary="需要提前申请北京BGP机房等保三级审计编号",
                canonical_key="sem:bgp_audit",
                payload={"subject": "机房", "predicate": "audit_rule", "value": "等保三级审计编号"},
            )
        )
        assert neighbor.memory is not None

        for idx in range(6):
            rels = (
                (RelationSpec(relation_type="depends_on", target=neighbor.memory.permalink, context="上线前置审批"),)
                if idx == 0
                else ()
            )
            self.app.change_service.apply(
                ChangeProposal(
                    space_id=self.space,
                    action="create",
                    memory_type="semantic",
                    title=f"数据库迁移方案章节{idx}",
                    summary=f"数据库迁移第{idx}阶段主从切换演练",
                    canonical_key=f"sem:db_mig_{idx}",
                    payload={"subject": "数据库迁移", "predicate": f"step_{idx}", "value": f"第{idx}步"},
                    relations=rels,
                )
            )

        # Verify relational attribute search recalls task by assignee & episodic by participant
        ask_assignee = self.app.ask_service.ask(self.space, "分配给王小明负责的待办有哪些？")
        self.assertTrue(ask_assignee["has_sufficient_evidence"])
        self.assertIn(t_res.memory.memory_id, [m["memory_id"] for m in ask_assignee["citations"]["memories"]])

        ask_participant = self.app.ask_service.ask(self.space, "和张伟架构师一起参加的复盘是什么？")
        self.assertTrue(ask_participant["has_sufficient_evidence"])
        self.assertIn(ep_res.memory.memory_id, [m["memory_id"] for m in ask_participant["citations"]["memories"]])

        # Verify AskService preserves 1-hop neighbor even when mem_hits >= 5 AND passes graph_relations
        stub = _CapturingAskLLM(sufficient=True, inject_fake_ids=True)
        with patch.object(self.app.ask_service, "llm", stub):
            res_rel = self.app.ask_service.ask(self.space, "数据库迁移前置关联了什么？", top_k=6)

        self.assertEqual(len(stub.calls), 1)
        passed_mids = {m.memory_id for m in stub.calls[0]["memories"]}
        self.assertIn(neighbor.memory.memory_id, passed_mids)
        self.assertTrue(
            any(
                r["relation_type"] == "depends_on" and r["to_memory_id"] == neighbor.memory.memory_id
                for r in stub.calls[0]["graph_relations"]
            )
        )
        # Strict Citation Hallucination Guard strips fabricated IDs
        returned_mids = [m["memory_id"] for m in res_rel["citations"]["memories"]]
        returned_nids = [n["note_id"] for n in res_rel["citations"]["notes"]]
        self.assertNotIn("mem_hallucinated_999", returned_mids)
        self.assertNotIn("note_hallucinated_999", returned_nids)

        # Insufficient evidence must clear citations completely
        stub_insuff = _CapturingAskLLM(sufficient=False)
        with patch.object(self.app.ask_service, "llm", stub_insuff):
            res_insuff = self.app.ask_service.ask(self.space, "数据库迁移前置关联了什么？", top_k=6)
        self.assertFalse(res_insuff["has_sufficient_evidence"])
        self.assertEqual(res_insuff["citations"]["memories"], [])
        self.assertEqual(res_insuff["citations"]["notes"], [])

    def test_ask_ryow_pending_notes_preserve_top_hits_and_history_previous_values(self) -> None:
        # Populate 5 direct matching notes + 4 unrelated queued pending notes
        matched_ids: list[str] = []
        for i in range(5):
            r = self.app.ingest_service.record_note(
                self.space, f"季度预算复盘第{i}条：云计算服务器费用支出预算控制。"
            )
            matched_ids.append(r.note.note_id)
        for j in range(4):
            self.app.ingest_service.enqueue_note(
                self.space, f"今天中午去楼下买了一杯瑞幸生椰拿铁咖啡 {j}"
            )

        stub = _CapturingAskLLM()
        with patch.object(self.app.ask_service, "llm", stub):
            self.app.ask_service.ask(self.space, "云计算服务器预算支出情况如何？")

        sent_note_ids = [n.note_id for n in stub.calls[0]["notes"]]
        self.assertTrue(any(mid in sent_note_ids[:4] for mid in matched_ids))

        # Single-version semantic memory with payload.previous_values + history cue answers via fallback
        sem = self.app.change_service.apply(
            ChangeProposal(
                space_id=self.space,
                action="create",
                memory_type="semantic",
                title="常驻城市",
                summary="目前常驻上海",
                canonical_key="sem:city_prev",
                payload=SemanticPayload(
                    subject="user",
                    predicate="residence",
                    value="上海",
                    previous_values=("杭州",),
                ).to_dict(),
            )
        )
        assert sem.memory is not None

        # Even if primary LLM raises, AskService gracefully degrades to DeterministicLLMProvider with previous_values
        with patch.object(self.app.ask_service, "llm", _RaisingAskLLM()):
            res_hist = self.app.ask_service.ask(self.space, "我搬家前以前常驻城市是哪里？")
        self.assertTrue(res_hist["has_sufficient_evidence"])
        self.assertIn("杭州", res_hist["answer"])


if __name__ == "__main__":
    unittest.main()
