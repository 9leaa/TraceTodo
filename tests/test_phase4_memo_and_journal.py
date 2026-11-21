from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.domain.notes import beijing_today_date
from trace_memory.storage.database import Database


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestPhase4MemoAndJournal(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.cfg = TraceMemoryConfig(
            default_space="default",
            data_dir=root / "data",
            knowledge_dir=root / "knowledge",
            auth_required=False,
        )
        self.container = AppContainer.build(self.cfg)
        self.api = ApiDispatcher(self.container)

    def tearDown(self) -> None:
        self.container.close()
        self._tmp.cleanup()

    def test_legacy_sqlite_migration_adds_raw_notes_columns_safely(self) -> None:
        legacy_db_path = Path(self._tmp.name) / "legacy.db"
        raw_conn = sqlite3.connect(str(legacy_db_path))
        raw_conn.execute(
            """
            CREATE TABLE raw_notes (
                note_id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL,
                content TEXT NOT NULL,
                source_type TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                is_forgotten INTEGER NOT NULL DEFAULT 0,
                forgotten_spans_json TEXT NOT NULL DEFAULT '[]',
                event_time TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        raw_conn.execute(
            """
            INSERT INTO raw_notes VALUES (
                'note_old1', 'default', '旧版随记内容', 'web', 'user', 1, 0, '[]',
                '2026-11-20T10:00:00+08:00', '2026-11-20T10:00:00+08:00', '2026-11-20T10:00:00+08:00'
            )
            """
        )
        raw_conn.commit()
        raw_conn.close()

        migrated = Database(legacy_db_path)
        try:
            cols = {str(r[1]) for r in migrated.query_all("PRAGMA table_info(raw_notes)")}
            for expected_col in ("note_kind", "title", "pinned", "tags_json", "mood", "journal_date", "extract_memory"):
                self.assertIn(expected_col, cols)
            row = migrated.query_one("SELECT note_kind, pinned FROM raw_notes WHERE note_id = 'note_old1'")
            assert row is not None
            self.assertEqual(row["note_kind"], "note")
            self.assertEqual(row["pinned"], 0)
        finally:
            migrated.close()

    def test_memo_pin_tags_search_and_silent_memory_extraction_filter(self) -> None:
        # 1. Pure Memo (extract_memory=False): should NOT extract tasks even if text mentions "明天"
        c1, r1 = self.api.dispatch(
            "POST",
            "/api/memos/save",
            body={
                "space_id": "memo_user",
                "title": "读书摘录：设计数据密集型应用",
                "content": "明天有空重读第三章存储引擎与 LSM-Tree 索引对比 #读书 #架构",
                "pinned": False,
                "extract_memory": False,
            },
        )
        self.assertEqual(c1, 200)
        m1 = r1["memo"]
        self.assertEqual(m1["note_kind"], "memo")
        self.assertIn("读书", m1["tags"])
        self.assertIn("架构", m1["tags"])

        # Matrix must have 0 tasks (pure memo)
        _, mat0 = self.api.dispatch("GET", "/api/tasks/matrix", {"space_id": ["memo_user"]})
        self.assertEqual(mat0["summary_counts"]["total_todo"], 0)

        # 2. Silent-extraction Memo (extract_memory=True): extracts preference, suppresses task noise!
        c2, r2 = self.api.dispatch(
            "POST",
            "/api/memos/save",
            body={
                "space_id": "memo_user",
                "title": "日常饮食与习惯备忘",
                "content": "我喜欢喝无糖冰美式咖啡。明天必须买一包新的咖啡豆 #生活",
                "pinned": True,
                "extract_memory": True,
            },
        )
        self.assertEqual(c2, 200)
        m2 = r2["memo"]
        self.assertTrue(m2["pinned"])

        _, mat1 = self.api.dispatch("GET", "/api/tasks/matrix", {"space_id": ["memo_user"]})
        self.assertEqual(mat1["summary_counts"]["total_todo"], 0)

        _, prefs = self.api.dispatch(
            "GET",
            "/api/memories/search",
            {"space_id": ["memo_user"], "memory_type": ["preference"]},
        )
        self.assertGreaterEqual(len(prefs["memories"]), 1)

        # 3. List memos -> pinned memo #1, tags_catalog populated, tag filtering & BM25 search work
        c_list, listing = self.api.dispatch("GET", "/api/memos/list", {"space_id": ["memo_user"]})
        self.assertEqual(c_list, 200)
        self.assertEqual(listing["total"], 2)
        self.assertEqual(listing["pinned_count"], 1)
        self.assertEqual(listing["memos"][0]["note_id"], m2["note_id"])

        _, by_tag = self.api.dispatch("GET", "/api/memos/list", {"space_id": ["memo_user"], "tag": ["架构"]})
        self.assertEqual(len(by_tag["memos"]), 1)
        self.assertEqual(by_tag["memos"][0]["note_id"], m1["note_id"])

        _, by_q = self.api.dispatch("GET", "/api/memos/list", {"space_id": ["memo_user"], "q": ["LSM-Tree"]})
        self.assertEqual(len(by_q["memos"]), 1)

        # 4. Edit memo increments revision; toggle pin updates pinned flag
        c_edit, r_edit = self.api.dispatch(
            "POST",
            "/api/memos/save",
            body={
                "space_id": "memo_user",
                "note_id": m1["note_id"],
                "title": "读书摘录：DDIA（修订版）",
                "content": "补充 B-Tree 与 LSM-Tree 写放大对比 #读书 #架构",
                "pinned": True,
                "extract_memory": False,
            },
        )
        self.assertEqual(c_edit, 200)
        self.assertEqual(r_edit["memo"]["revision"], 2)
        self.assertTrue(r_edit["memo"]["pinned"])

        c_unpin, r_unpin = self.api.dispatch(
            "POST",
            "/api/memos/pin",
            body={"space_id": "memo_user", "note_id": m1["note_id"], "pinned": False},
        )
        self.assertEqual(c_unpin, 200)
        self.assertFalse(r_unpin["memo"]["pinned"])

        # 5. Forget memo cascades cleanly via unified RawNote kernel
        c_fg, _ = self.api.dispatch(
            "POST",
            "/api/notes/forget",
            body={"space_id": "memo_user", "note_id": m2["note_id"]},
        )
        self.assertEqual(c_fg, 200)
        _, prefs_after = self.api.dispatch(
            "GET",
            "/api/memories/search",
            {"space_id": ["memo_user"], "memory_type": ["preference"]},
        )
        self.assertEqual(len(prefs_after["memories"]), 0)

    def test_daily_mood_journal_one_page_upsert_ai_summary_and_calendar_badge(self) -> None:
        today = beijing_today_date()

        # 1. Create & complete a task today + record a 25-min Pomodoro session today
        _, t_res = self.api.dispatch(
            "POST",
            "/api/tasks/save",
            body={"space_id": "jrn_user", "title": "上线 TraceTodo 四阶段防臃肿升级", "due_at": today},
        )
        tid = t_res["memory"]["memory_id"]
        tver = t_res["memory"]["version"]
        self.api.dispatch(
            "POST",
            "/api/tasks/transition",
            body={"space_id": "jrn_user", "memory_id": tid, "action": "complete_task", "expected_version": tver},
        )
        self.api.dispatch(
            "POST",
            "/api/focus/record",
            body={
                "space_id": "jrn_user",
                "mode": "pomodoro",
                "status": "completed",
                "planned_minutes": 25,
                "duration_seconds": 1500,
                "task_title": "架构联调",
            },
        )

        # 2. Generate "AI 今日小结" (+ Dream consolidation trigger)
        c_sum, r_sum = self.api.dispatch(
            "POST",
            "/api/journal/ai-summary",
            body={"space_id": "jrn_user", "date": today, "trigger_dream": True},
        )
        self.assertEqual(c_sum, 200)
        self.assertIn("上线 TraceTodo 四阶段防臃肿升级", r_sum["summary_markdown"])
        self.assertIn("25 分钟", r_sum["summary_markdown"])
        self.assertIsNotNone(r_sum["dream_report"])

        # 3. Save journal twice on same Beijing day -> One-Page-Per-Day upsert (no duplicate entries!)
        c_j1, r_j1 = self.api.dispatch(
            "POST",
            "/api/journal/save",
            body={
                "space_id": "jrn_user",
                "journal_date": today,
                "mood": "focused",
                "content": r_sum["summary_markdown"],
                "extract_memory": True,
            },
        )
        self.assertEqual(c_j1, 200)
        jid = r_j1["journal"]["note_id"]

        c_j2, r_j2 = self.api.dispatch(
            "POST",
            "/api/journal/save",
            body={
                "space_id": "jrn_user",
                "journal_date": today,
                "mood": "joy",
                "content": r_sum["summary_markdown"] + "\n\n晚上散步心情很好！ #复盘",
                "extract_memory": True,
            },
        )
        self.assertEqual(c_j2, 200)
        self.assertEqual(r_j2["journal"]["note_id"], jid)
        self.assertEqual(r_j2["journal"]["revision"], 2)
        self.assertEqual(r_j2["journal"]["mood"], "joy")

        c_ov, j_ov = self.api.dispatch("GET", "/api/journal/overview", {"space_id": ["jrn_user"], "date": [today]})
        self.assertEqual(c_ov, 200)
        self.assertEqual(len(j_ov["timeline"]), 1)
        self.assertEqual(j_ov["current_entry"]["mood_meta"]["icon"], "😊")

        # 4. Verify Calendar overview reflects journal_count == 1 on today
        _, cal_ov = self.api.dispatch(
            "GET",
            "/api/calendar/overview",
            {"space_id": ["jrn_user"], "view": ["week"], "anchor_date": [today]},
        )
        today_bucket = next(d for d in cal_ov["days"] if d["date"] == today)
        self.assertEqual(today_bucket["journal_count"], 1)

    def test_web_and_apple_memo_journal_markers_present(self) -> None:
        html = (REPO_ROOT / "src" / "trace_memory" / "api" / "dashboard.html").read_text(encoding="utf-8")
        for m in ("memoStudioSection", "journalStudioSection", "generateJournalAiSummary", "memo-masonry"):
            self.assertIn(m, html)
        swift_src = (REPO_ROOT / "apple-app" / "Sources" / "TraceTodo" / "MemoAndJournalViews.swift").read_text(
            encoding="utf-8"
        )
        self.assertIn("struct MemoAndJournalStudioView: View", swift_src)
        self.assertIn("struct JournalDailyStudioView: View", swift_src)


if __name__ == "__main__":
    unittest.main()
