"""Regression tests for thorough cascading forgetting, defense-bypass hardening, session revocation, and XSS hygiene."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from trace_memory.api.app import ApiDispatcher, _resolve_client_ip
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.domain.memories import MemoryEvidence
from trace_memory.domain.notes import RawNote
from trace_memory.domain.proposals import ChangeProposal, RelationSpec
from trace_memory.storage.vector_store import MEMORIES_COLLECTION, NOTES_COLLECTION


class TestSecurityAndForgetHardening(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        cfg = TraceMemoryConfig(
            data_dir=root / "data",
            knowledge_dir=root / "knowledge",
            auth_required=True,
            allow_registration=True,
            trust_proxy_headers=False,
            default_daily_note_limit=5,
            default_daily_llm_limit=10,
            rate_limit_per_minute=60,
        )
        self.app = AppContainer.build(cfg)
        self.api = ApiDispatcher(self.app)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _register(self, username: str, password: str = "secret123") -> dict:
        code, res = self.api.dispatch(
            "POST",
            "/api/auth/register",
            body={"username": username, "password": password, "display_name": username},
            client_ip="10.0.0.1",
        )
        self.assertEqual(code, 200, res)
        return res

    def test_memory_forget_cascades_raw_notes_vectors_versions_dream_graph_and_blocks_resurrection(self) -> None:
        adm = self._register("admin_f")
        tok = adm["token"]
        sp = adm["user"]["space_id"]

        # 1. Record a private note that creates a semantic fact
        code, rec = self.api.dispatch(
            "POST",
            "/api/notes/record",
            body={"content": "我现在搬到秘密基地阿尔法城定居了。", "idempotency_key": "sec-forget-1"},
            auth_token=tok,
        )
        self.assertEqual(code, 200, rec)
        note_id = rec["note"]["note_id"]
        mem = rec["changes"][0]["memory"]
        mem_id = mem["memory_id"]

        # Create a second memory linking to the target AND a derived Dream inferred insight
        c_link, link_res = self.api.dispatch(
            "POST",
            "/api/memories/propose",
            body={
                "action": "create",
                "memory_type": "semantic",
                "title": "搬迁计划关联节点",
                "summary": "关联到秘密基地搬迁计划",
                "payload": {"subject": "plan", "predicate": "ref", "value": "relocation"},
            },
            auth_token=tok,
        )
        self.assertEqual(c_link, 200)
        linker_id = link_res["memory"]["memory_id"]
        self.app.relation_repo.replace_outgoing(
            sp, linker_id, [("relates_to", mem["permalink"], "敏感搬迁上下文")]
        )

        dream_res = self.app.change_service.apply(
            ChangeProposal(
                space_id=sp,
                action="create",
                memory_type="semantic",
                assertion_kind="inferred",
                title="长期定居洞察",
                summary="用户近期在秘密基地阿尔法城稳定生活",
                payload={"subject": "user", "predicate": "insight", "value": "阿尔法城洞察"},
                relations=(RelationSpec(relation_type="derived_from", target=mem["permalink"]),),
                origin="dream_consolidator",
            )
        )
        assert dream_res.memory is not None
        dream_id = dream_res.memory.memory_id

        # Verify vectors exist before forget
        self.assertGreaterEqual(self.app.vector_store.count(NOTES_COLLECTION, sp), 1)
        self.assertGreaterEqual(self.app.vector_store.count(MEMORIES_COLLECTION, sp), 2)

        # 2. User clicks "遗忘此记忆" via POST /api/memories/propose (action="forget")
        c_f, f_res = self.api.dispatch(
            "POST",
            "/api/memories/propose",
            body={"action": "forget", "target_memory_id": mem_id, "expected_version": mem["version"]},
            auth_token=tok,
        )
        self.assertEqual(c_f, 200, f_res)

        # 3. RawNote must be scrubbed & forgotten, with FTS and vector purged!
        raw_after = self.app.note_repo.get(sp, note_id, include_forgotten=True)
        assert raw_after is not None
        self.assertTrue(raw_after.is_forgotten)
        self.assertEqual(raw_after.content, "[FORGOTTEN]")
        self.assertIsNone(self.app.note_repo.get(sp, note_id, include_forgotten=False))

        # Check traces & recent_notes BEFORE running new search/ask queries with the keyword
        _, traces_res = self.api.dispatch("GET", "/api/traces", auth_token=tok)
        for n in traces_res["recent_notes"]:
            self.assertNotIn("阿尔法城", n["content"])
        for tr in traces_res["traces"]:
            self.assertNotIn("阿尔法城", tr["input_summary"])

        # Search and Ask must NOT recall "阿尔法城" from either memories or raw_notes!
        _, search_res = self.api.dispatch(
            "GET", "/api/memories/search", {"q": ["阿尔法城"]}, auth_token=tok
        )
        self.assertEqual(search_res["memories"], [])
        self.assertEqual(search_res["notes"], [])

        _, ask_res = self.api.dispatch(
            "POST", "/api/ask", body={"question": "我搬到阿尔法城了吗？"}, auth_token=tok
        )
        self.assertFalse(ask_res["has_sufficient_evidence"])
        self.assertNotIn("阿尔法城", ask_res["answer"])

        # 4. Memories row & ALL historical memory_versions rows must be scrubbed of plaintext
        mem_row = self.app.db.query_one("SELECT * FROM memories WHERE memory_id = ?", (mem_id,))
        assert mem_row is not None
        self.assertEqual(mem_row["title"], "[FORGOTTEN]")
        self.assertEqual(mem_row["summary"], "[FORGOTTEN]")
        self.assertNotIn("阿尔法城", mem_row["payload_json"])
        self.assertEqual(json.loads(mem_row["payload_json"]).get("value", ""), "")
        self.assertEqual(mem_row["evidences_json"], "[]")

        versions = self.app.memory_repo.get_history(sp, mem_id)
        self.assertGreaterEqual(len(versions), 2)
        for v in versions:
            self.assertEqual(v.title, "[FORGOTTEN]")
            self.assertEqual(v.summary, "[FORGOTTEN]")
            self.assertEqual(v.payload, {})
            self.assertEqual(v.evidences, ())

        # 5. Derived Dream inferred memory must be cascade-forgotten too!
        dream_after = self.app.memory_repo.get(sp, dream_id, include_forgotten=True)
        assert dream_after is not None
        self.assertEqual(dream_after.status, "forgotten")
        self.assertEqual(dream_after.title, "[FORGOTTEN]")

        # 6. Inbound graph edges must redact forgotten target text and stay hidden from /api/graph
        _, graph_res = self.api.dispatch("GET", "/api/graph", auth_token=tok)
        for edge in graph_res["edges"]:
            self.assertNotIn("阿尔法城", edge["to_target_text"])
            self.assertNotEqual(edge["to_target_text"], "[FORGOTTEN]")

        # 7. Resurrection via approve or update MUST be rejected!
        c_revive, err_rev = self.api.dispatch(
            "POST",
            "/api/memories/propose",
            body={"action": "approve", "target_memory_id": mem_id},
            auth_token=tok,
        )
        self.assertEqual(c_revive, 400, err_rev)
        self.assertEqual(err_rev["code"], "VALIDATION_ERROR")

    def test_shared_note_memory_forget_and_multi_evidence_note_forget(self) -> None:
        adm = self._register("admin_shared")
        sp = adm["user"]["space_id"]

        # A) Single note producing 2 memories -> forgetting one redacts its span while keeping sibling alive
        r = self.app.ingest_service.record_note(sp, "我住在上海。平时喜欢喝拿铁咖啡。")
        self.assertEqual(len(r.changes), 2)
        city_mem = next(c.memory for c in r.changes if c.memory and "上海" in str(c.memory.payload))
        assert city_mem is not None

        self.app.change_service.apply(
            ChangeProposal(
                space_id=sp,
                action="forget",
                memory_type=city_mem.memory_type,
                target_memory_id=city_mem.memory_id,
                expected_version=city_mem.version,
            )
        )
        search_sh = self.app.retrieval_service.search(sp, "上海")
        self.assertEqual(search_sh["memories"], [])
        for nh in search_sh["notes"]:
            self.assertNotIn("上海", nh["note"]["content"])

        search_coffee = self.app.retrieval_service.search(sp, "拿铁咖啡")
        self.assertGreaterEqual(len(search_coffee["memories"]), 1)

        # B) Multi-evidence memory -> forgetting 1 of 2 supporting notes strips its quote & link
        n1 = RawNote(note_id="note_ev_1", space_id=sp, content="秘密口令12345记录一", source_type="web")
        n2 = RawNote(note_id="note_ev_2", space_id=sp, content="常规备份记录二", source_type="web")
        self.app.note_repo.save(n1)
        self.app.note_repo.save(n2)
        multi = self.app.change_service.apply(
            ChangeProposal(
                space_id=sp,
                action="create",
                memory_type="semantic",
                title="多证据项目说明",
                summary="包含两条来源",
                payload={"subject": "proj", "predicate": "note", "value": "two_sources"},
                evidences=(
                    MemoryEvidence(note_id=n1.note_id, note_revision=1, quote="秘密口令12345记录一"),
                    MemoryEvidence(note_id=n2.note_id, note_revision=1, quote="常规备份记录二"),
                ),
            )
        ).memory
        assert multi is not None

        self.app.ingest_service.forget_note(sp, n1.note_id)
        refreshed = self.app.memory_repo.get(sp, multi.memory_id)
        assert refreshed is not None
        self.assertEqual(refreshed.status, "active")
        self.assertEqual([e.note_id for e in refreshed.evidences], [n2.note_id])
        self.assertNotIn("秘密口令12345", str([e.quote for e in refreshed.evidences]))

    def test_proxy_spoofing_query_token_account_lockout_and_write_quotas(self) -> None:
        # 1. Proxy header resolution ignores spoofed X-Forwarded-For unless trust_proxy_headers=True
        spoof_hdrs = {"X-Forwarded-For": "9.9.9.9", "X-Real-IP": "8.8.8.8"}
        self.assertEqual(_resolve_client_ip(spoof_hdrs, "203.0.113.5", False), "203.0.113.5")
        self.assertEqual(_resolve_client_ip(spoof_hdrs, "203.0.113.5", True), "9.9.9.9")

        adm = self._register("admin_sec")
        bob = self._register("bob_sec")
        bob_tok = bob["token"]
        bob_space = bob["user"]["space_id"]

        # 2. Query string `?token=...` MUST be rejected with 401
        c_q, _ = self.api.dispatch("GET", "/api/tasks/matrix", query_params={"token": [bob_tok]})
        self.assertEqual(c_q, 401)
        c_h, _ = self.api.dispatch(
            "GET", "/api/tasks/matrix", headers={"Authorization": f"Bearer {bob_tok}"}
        )
        self.assertEqual(c_h, 200)

        # 3. Per-username login brute-force lockout triggers even from rotating client IPs
        for i in range(10):
            c_fail, _ = self.api.dispatch(
                "POST",
                "/api/auth/login",
                body={"username": "bob_sec", "password": "wrong_password"},
                client_ip=f"198.51.100.{i + 1}",
            )
            self.assertEqual(c_fail, 401)
        c_locked, lock_body = self.api.dispatch(
            "POST",
            "/api/auth/login",
            body={"username": "bob_sec", "password": "secret123"},
            client_ip="198.51.100.99",
        )
        self.assertEqual(c_locked, 429, lock_body)
        self.assertEqual(lock_body["code"], "RATE_LIMIT_EXCEEDED")

        # 4. Daily quota blocks abuse via /api/memories/propose, /api/tasks/save, and MCP tm_propose_change
        # Bob's default_daily_note_limit is 5; consume 5 slots across write endpoints
        for i in range(3):
            c_p, res_p = self.api.dispatch(
                "POST",
                "/api/memories/propose",
                body={
                    "action": "create",
                    "memory_type": "semantic",
                    "title": f"条目{i}",
                    "summary": f"正常内容条目{i}",
                },
                auth_token=bob_tok,
            )
            self.assertEqual(c_p, 200, res_p)

        # Non-admin markdown/save must NOT export files to server disk AND counts toward daily quota
        first_mem_id = res_p["memory"]["memory_id"]
        _, detail = self.api.dispatch("GET", "/api/memories/detail", {"id": [first_mem_id]}, auth_token=bob_tok)
        c_md, md_res = self.api.dispatch(
            "POST",
            "/api/memories/markdown/save",
            body={"memory_id": first_mem_id, "expected_version": 1, "markdown": detail["markdown"] + "\n"},
            auth_token=bob_tok,
        )
        self.assertEqual(c_md, 200, md_res)
        self.assertFalse((self.app.config.knowledge_dir / bob_space).exists())

        c_t, _ = self.api.dispatch(
            "POST",
            "/api/tasks/save",
            body={"title": "第5条配额任务", "summary": "占满当日第5条配额"},
            auth_token=bob_tok,
        )
        self.assertEqual(c_t, 200)

        # 6th write via propose or MCP tm_propose_change must be blocked by daily quota!
        c_over, over_body = self.api.dispatch(
            "POST",
            "/api/memories/propose",
            body={"action": "create", "memory_type": "semantic", "title": "超额", "summary": "应被配额拦截"},
            auth_token=bob_tok,
        )
        self.assertEqual(c_over, 429, over_body)
        self.assertEqual(over_body["code"], "QUOTA_EXCEEDED")

        mcp_req = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "tm_propose_change",
                    "arguments": {"action": "create", "title": "MCP超额", "summary": "应被拦截"},
                },
            }
        ).encode("utf-8")
        c_mcp, mcp_resp = self.api.dispatch_mcp_http(
            mcp_req, headers={"Authorization": f"Bearer {bob_tok}"}
        )
        self.assertEqual(c_mcp, 200)
        self.assertTrue(mcp_resp["result"]["isError"])

        # 5. Batch MCP > 10 requests rejected with 400
        big_batch = json.dumps([{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(11)]).encode("utf-8")
        c_b, _ = self.api.dispatch_mcp_http(big_batch, headers={"Authorization": f"Bearer {bob_tok}"})
        self.assertEqual(c_b, 400)

        # 6. Invalid integer query params return 400 instead of 500
        c_bad, bad_body = self.api.dispatch(
            "GET", "/api/memories/search", {"q": ["test"], "top_k": ["not_an_int"]}, auth_token=bob_tok
        )
        self.assertEqual(c_bad, 400)
        self.assertEqual(bad_body["code"], "VALIDATION_ERROR")

    def test_change_password_revokes_old_sessions_share_revocation_and_subtask_xss_sanitization(self) -> None:
        adm = self._register("admin_pw")
        tok1 = adm["token"]
        # Issue a second session token on another device
        c_l2, login2 = self.api.dispatch(
            "POST",
            "/api/auth/login",
            body={"username": "admin_pw", "password": "secret123", "device_label": "phone"},
            client_ip="10.0.0.2",
        )
        self.assertEqual(c_l2, 200)
        tok2 = login2["token"]

        # Change password -> both tok1 and tok2 must be revoked, and returned fresh token must work!
        c_pw, pw_res = self.api.dispatch(
            "POST",
            "/api/auth/change-password",
            body={"old_password": "secret123", "new_password": "new_secret_456"},
            auth_token=tok1,
        )
        self.assertEqual(c_pw, 200, pw_res)
        self.assertTrue(pw_res["updated"])
        self.assertGreaterEqual(pw_res["revoked_sessions"], 2)
        fresh_tok = pw_res["token"]
        self.assertTrue(fresh_tok and fresh_tok != tok1 and fresh_tok != tok2)

        self.assertEqual(self.api.dispatch("GET", "/api/auth/me", auth_token=tok1)[0], 401)
        self.assertEqual(self.api.dispatch("GET", "/api/auth/me", auth_token=tok2)[0], 401)
        self.assertEqual(self.api.dispatch("GET", "/api/auth/me", auth_token=fresh_tok)[0], 200)

        # Supervision share revocation works immediately
        c_sh, sh = self.api.dispatch("POST", "/api/supervision/share", body={"label": "测试分享"}, auth_token=fresh_tok)
        self.assertEqual(c_sh, 200)
        share_tok = sh["share_token"]
        self.assertEqual(
            self.api.dispatch("GET", "/api/supervision/board", {"share_token": [share_tok]})[0], 200
        )
        c_list, sh_list = self.api.dispatch("GET", "/api/supervision/shares", auth_token=fresh_tok)
        self.assertEqual(c_list, 200)
        self.assertEqual(len(sh_list["shares"]), 1)

        c_rev, rev_res = self.api.dispatch(
            "POST", "/api/supervision/share/revoke", body={"share_id": sh["share_id"]}, auth_token=fresh_tok
        )
        self.assertEqual(c_rev, 200)
        self.assertEqual(rev_res["revoked"], 1)
        self.assertEqual(
            self.api.dispatch("GET", "/api/supervision/board", {"share_token": [share_tok]})[0], 401
        )

        # Subtask ID XSS payload is sanitized server-side
        c_task, task_res = self.api.dispatch(
            "POST",
            "/api/tasks/save",
            body={
                "title": "安全子任务测试",
                "subtasks": [{"id": "x');alert(1);//", "title": "步骤1", "done": False}],
            },
            auth_token=fresh_tok,
        )
        self.assertEqual(c_task, 200, task_res)
        saved_sid = task_res["memory"]["payload"]["subtasks"][0]["id"]
        self.assertNotIn("'", saved_sid)
        self.assertNotIn("(", saved_sid)
        self.assertNotIn(";", saved_sid)

        # Maintenance prunes revoked sessions cleanly
        c_m, m_res = self.api.dispatch("POST", "/api/admin/maintenance", auth_token=fresh_tok)
        self.assertEqual(c_m, 200, m_res)
        self.assertGreaterEqual(m_res["pruned_sessions"], 2)

    def test_purge_legacy_forgotten_records_cleans_pre_fix_dirty_rows(self) -> None:
        adm = self._register("admin_legacy")
        tok = adm["token"]
        sp = adm["user"]["space_id"]

        # Create 2 memories: one will simulate a pre-fix dirty forgotten record, one stays active
        _, r1 = self.api.dispatch(
            "POST",
            "/api/notes/record",
            body={"content": "我现在搬到旧秘密城市贝塔城定居了。"},
            auth_token=tok,
        )
        _, r2 = self.api.dispatch(
            "POST",
            "/api/notes/record",
            body={"content": "明天必须提交季度项目复盘文档。"},
            auth_token=tok,
        )
        dirty_note_id = r1["note"]["note_id"]
        dirty_mem_id = r1["changes"][0]["memory"]["memory_id"]
        active_mem_id = r2["changes"][0]["memory"]["memory_id"]

        # Simulate pre-fix legacy forget state directly in SQLite (status='forgotten' with plaintext & orphan raw_note)
        with self.app.db.transaction() as conn:
            conn.execute(
                "UPDATE memories SET status = 'forgotten', summary = '[FORGOTTEN]', evidences_json = '[]' WHERE memory_id = ?",
                (dirty_mem_id,),
            )
            conn.execute("DELETE FROM memory_evidence_links WHERE memory_id = ?", (dirty_mem_id,))

        # Verify pre-fix leak reproduces before purge
        _, s_before = self.api.dispatch("GET", "/api/memories/search", {"q": ["贝塔城"]}, auth_token=tok)
        self.assertGreaterEqual(len(s_before["notes"]), 1)

        # Run idempotent legacy purge
        report = self.app.change_service.purge_legacy_forgotten_records()
        self.assertEqual(report["scrubbed_memories"], 1)
        self.assertGreaterEqual(report["scrubbed_versions"], 1)
        self.assertEqual(report["scrubbed_raw_notes"], 1)

        # Re-running is idempotent (0 more work)
        report2 = self.app.change_service.purge_legacy_forgotten_records()
        self.assertEqual(report2["scrubbed_memories"], 0)
        self.assertEqual(report2["scrubbed_raw_notes"], 0)

        # Leaked note & memory plaintext are completely gone, while active task is untouched
        _, s_after = self.api.dispatch("GET", "/api/memories/search", {"q": ["贝塔城"]}, auth_token=tok)
        self.assertEqual(s_after["memories"], [])
        self.assertEqual(s_after["notes"], [])
        raw_after = self.app.note_repo.get(sp, dirty_note_id, include_forgotten=True)
        assert raw_after is not None
        self.assertTrue(raw_after.is_forgotten)
        self.assertEqual(raw_after.content, "[FORGOTTEN]")

        active_after = self.app.memory_repo.get(sp, active_mem_id)
        assert active_after is not None
        self.assertEqual(active_after.status, "active")

        # Also verify forgetting preference & episodic memories succeeds cleanly with empty scrubbed payload
        for mtype, payload_data in (
            ("preference", {"target": "乌龙茶", "polarity": "positive"}),
            ("episodic", {"event_summary": "参加了秘密技术闭门会"}),
        ):
            c_cr, cr = self.api.dispatch(
                "POST",
                "/api/memories/propose",
                body={
                    "action": "create",
                    "memory_type": mtype,
                    "title": f"测试{mtype}",
                    "summary": f"测试{mtype}摘要",
                    "payload": payload_data,
                },
                auth_token=tok,
            )
            self.assertEqual(c_cr, 200, cr)
            mid_x = cr["memory"]["memory_id"]
            c_fg, fg = self.api.dispatch(
                "POST",
                "/api/memories/propose",
                body={"action": "forget", "target_memory_id": mid_x, "expected_version": 1},
                auth_token=tok,
            )
            self.assertEqual(c_fg, 200, fg)
            self.assertEqual(fg["memory"]["payload"], {})
            reloaded_fg = self.app.memory_repo.get(sp, mid_x, include_forgotten=True)
            assert reloaded_fg is not None
            self.assertEqual(reloaded_fg.payload, {})


if __name__ == "__main__":
    unittest.main()
