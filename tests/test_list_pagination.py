from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.services.auth_service import UserAccount


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestListPaginationAcrossEndpoints(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.cfg = TraceMemoryConfig(
            default_space="default",
            data_dir=root / "data",
            knowledge_dir=root / "knowledge",
            auth_required=False,
            rate_limit_per_minute=200,
        )
        self.container = AppContainer.build(self.cfg)
        self.api = ApiDispatcher(self.container)

    def tearDown(self) -> None:
        self.container.close()
        self._tmp.cleanup()

    def test_memos_offset_pagination_pinned_first_and_disjoint_slices(self) -> None:
        for i in range(35):
            c_save, _ = self.api.dispatch(
                "POST",
                "/api/memos/save",
                body={
                    "space_id": "memo_pg",
                    "title": f"备忘标题 {i:02d}",
                    "content": f"备忘正文 {i:02d} #分页测试",
                    "pinned": (i % 5 == 0),  # 7 pinned, 28 unpinned
                    "extract_memory": False,
                },
            )
            self.assertEqual(c_save, 200)

        c0, p0 = self.api.dispatch(
            "GET", "/api/memos/list", {"space_id": ["memo_pg"], "limit": ["10"], "offset": ["0"]}
        )
        c1, p1 = self.api.dispatch(
            "GET", "/api/memos/list", {"space_id": ["memo_pg"], "limit": ["10"], "offset": ["10"]}
        )
        c3, p3 = self.api.dispatch(
            "GET", "/api/memos/list", {"space_id": ["memo_pg"], "limit": ["10"], "offset": ["30"]}
        )
        self.assertEqual((c0, c1, c3), (200, 200, 200))

        self.assertEqual(p0["total"], 35)
        self.assertEqual(p0["all_count"], 35)
        self.assertEqual(p0["all_pinned_count"], 7)
        self.assertEqual(len(p0["memos"]), 10)
        self.assertTrue(p0["has_more"])
        # First 7 items in page 0 must be all pinned memos, followed by 3 unpinned
        self.assertEqual(sum(1 for m in p0["memos"][:7] if m["pinned"]), 7)
        self.assertFalse(p0["memos"][7]["pinned"])

        self.assertEqual(len(p1["memos"]), 10)
        self.assertTrue(p1["has_more"])

        self.assertEqual(len(p3["memos"]), 5)
        self.assertFalse(p3["has_more"])

        ids_0 = {m["note_id"] for m in p0["memos"]}
        ids_1 = {m["note_id"] for m in p1["memos"]}
        ids_3 = {m["note_id"] for m in p3["memos"]}
        self.assertTrue(ids_0.isdisjoint(ids_1))
        self.assertTrue(ids_0.isdisjoint(ids_3))
        self.assertTrue(ids_1.isdisjoint(ids_3))

    def test_journal_history_offset_pagination(self) -> None:
        base_d = date(2026, 11, 1)
        for i in range(20):
            j_ymd = (base_d + timedelta(days=i)).isoformat()
            c_j, _ = self.api.dispatch(
                "POST",
                "/api/journal/save",
                body={
                    "space_id": "jrn_pg",
                    "journal_date": j_ymd,
                    "mood": "calm",
                    "content": f"日记内容 {j_ymd}",
                    "extract_memory": False,
                },
            )
            self.assertEqual(c_j, 200)

        c0, r0 = self.api.dispatch(
            "GET",
            "/api/journal/overview",
            {"space_id": ["jrn_pg"], "date": ["2026-11-20"], "limit": ["8"], "offset": ["0"]},
        )
        c1, r1 = self.api.dispatch(
            "GET",
            "/api/journal/overview",
            {"space_id": ["jrn_pg"], "date": ["2026-11-20"], "limit": ["8"], "offset": ["8"]},
        )
        c2, r2 = self.api.dispatch(
            "GET",
            "/api/journal/overview",
            {"space_id": ["jrn_pg"], "date": ["2026-11-20"], "limit": ["8"], "offset": ["16"]},
        )
        self.assertEqual((c0, c1, c2), (200, 200, 200))

        self.assertEqual(r0["timeline_total"], 20)
        self.assertEqual(len(r0["timeline"]), 8)
        self.assertTrue(r0["timeline_has_more"])
        self.assertEqual(r0["timeline"][0]["journal_date"], "2026-11-20")

        self.assertEqual(len(r1["timeline"]), 8)
        self.assertTrue(r1["timeline_has_more"])

        self.assertEqual(len(r2["timeline"]), 4)
        self.assertFalse(r2["timeline_has_more"])
        self.assertEqual(r2["timeline"][-1]["journal_date"], "2026-11-01")

        dates_all = (
            [e["journal_date"] for e in r0["timeline"]]
            + [e["journal_date"] for e in r1["timeline"]]
            + [e["journal_date"] for e in r2["timeline"]]
        )
        self.assertEqual(len(set(dates_all)), 20)
        self.assertEqual(dates_all, sorted(dates_all, reverse=True))

    def test_focus_sessions_offset_pagination(self) -> None:
        for i in range(25):
            st_h = i % 24
            st_d = "2026-11-22" if i < 20 else "2026-11-23"
            c_rec, _ = self.api.dispatch(
                "POST",
                "/api/focus/record",
                body={
                    "space_id": "focus_pg",
                    "mode": "pomodoro",
                    "status": "completed",
                    "planned_minutes": 25,
                    "duration_seconds": 1500,
                    "task_title": f"专注任务 {i:02d}",
                    "started_at": f"{st_d}T{st_h:02d}:00:00+08:00",
                    "ended_at": f"{st_d}T{st_h:02d}:25:00+08:00",
                },
            )
            self.assertEqual(c_rec, 200)

        c0, p0 = self.api.dispatch(
            "GET", "/api/focus/overview", {"space_id": ["focus_pg"], "limit": ["10"], "offset": ["0"]}
        )
        c1, p1 = self.api.dispatch(
            "GET", "/api/focus/overview", {"space_id": ["focus_pg"], "limit": ["10"], "offset": ["10"]}
        )
        c2, p2 = self.api.dispatch(
            "GET", "/api/focus/overview", {"space_id": ["focus_pg"], "limit": ["10"], "offset": ["20"]}
        )
        self.assertEqual((c0, c1, c2), (200, 200, 200))

        self.assertEqual(p0["total"], 25)
        self.assertEqual(p0["stats"]["total_sessions"], 25)
        self.assertEqual(len(p0["sessions"]), 10)
        self.assertTrue(p0["has_more"])
        self.assertEqual(len(p1["sessions"]), 10)
        self.assertTrue(p1["has_more"])
        self.assertEqual(len(p2["sessions"]), 5)
        self.assertFalse(p2["has_more"])

        sids = (
            [s["session_id"] for s in p0["sessions"]]
            + [s["session_id"] for s in p1["sessions"]]
            + [s["session_id"] for s in p2["sessions"]]
        )
        self.assertEqual(len(set(sids)), 25)

    def test_admin_users_search_pagination_and_backward_compat_list_users(self) -> None:
        with tempfile.TemporaryDirectory() as tmp2:
            root2 = Path(tmp2)
            auth_app = AppContainer.build(
                TraceMemoryConfig(
                    default_space="default",
                    data_dir=root2 / "data",
                    knowledge_dir=root2 / "knowledge",
                    auth_required=True,
                    allow_registration=True,
                    rate_limit_per_minute=200,
                )
            )
            try:
                auth_api = ApiDispatcher(auth_app)
                _, adm_reg = auth_api.dispatch(
                    "POST",
                    "/api/auth/register",
                    body={"username": "admin_root", "display_name": "Admin Root", "password": "password123"},
                )
                adm_tok = adm_reg["token"]
                adm_user_dict = adm_reg["user"]

                for idx in range(14):
                    c_crt, _ = auth_api.dispatch(
                        "POST",
                        "/api/admin/users/create",
                        body={
                            "username": f"member_{idx:02d}",
                            "display_name": f"工程师 {idx:02d}" if idx < 10 else f"设计师 {idx:02d}",
                            "password": "password123",
                        },
                        auth_token=adm_tok,
                    )
                    self.assertEqual(c_crt, 200)

                # Total 15 users (1 admin + 14 members)
                c0, p0 = auth_api.dispatch(
                    "GET",
                    "/api/admin/users",
                    {"limit": ["6"], "offset": ["0"]},
                    auth_token=adm_tok,
                )
                c1, p1 = auth_api.dispatch(
                    "GET",
                    "/api/admin/users",
                    {"limit": ["6"], "offset": ["6"]},
                    auth_token=adm_tok,
                )
                c2, p2 = auth_api.dispatch(
                    "GET",
                    "/api/admin/users",
                    {"limit": ["6"], "offset": ["12"]},
                    auth_token=adm_tok,
                )
                self.assertEqual((c0, c1, c2), (200, 200, 200))
                self.assertEqual(p0["total"], 15)
                self.assertEqual(len(p0["users"]), 6)
                self.assertTrue(p0["has_more"])
                self.assertEqual(len(p1["users"]), 6)
                self.assertTrue(p1["has_more"])
                self.assertEqual(len(p2["users"]), 3)
                self.assertFalse(p2["has_more"])

                all_uids = (
                    [u["user_id"] for u in p0["users"]]
                    + [u["user_id"] for u in p1["users"]]
                    + [u["user_id"] for u in p2["users"]]
                )
                self.assertEqual(len(set(all_uids)), 15)

                # Search filter q by display_name or username
                c_q, r_q = auth_api.dispatch(
                    "GET",
                    "/api/admin/users",
                    {"q": ["设计师"], "limit": ["20"], "offset": ["0"]},
                    auth_token=adm_tok,
                )
                self.assertEqual(c_q, 200)
                self.assertEqual(r_q["total"], 4)
                self.assertFalse(r_q["has_more"])
                self.assertEqual(len(r_q["users"]), 4)

                # Backward-compatible Python list_users(actor) still returns a plain list of all users
                actor = UserAccount(
                    user_id=adm_user_dict["user_id"],
                    username=adm_user_dict["username"],
                    display_name=adm_user_dict["display_name"],
                    role="admin",
                    status="active",
                    space_id=adm_user_dict["space_id"],
                    daily_note_limit=100,
                    daily_llm_limit=100,
                    daily_token_limit=100000,
                    created_at=adm_user_dict["created_at"],
                    updated_at=adm_user_dict.get("updated_at", adm_user_dict["created_at"]),
                )
                plain_list = auth_app.admin_service.list_users(actor)
                self.assertIsInstance(plain_list, list)
                self.assertEqual(len(plain_list), 15)
            finally:
                auth_app.close()

    def test_dashboard_html_pagination_markers(self) -> None:
        html = (REPO_ROOT / "src" / "trace_memory" / "api" / "dashboard.html").read_text(encoding="utf-8")
        for marker in (
            'id="memoLoadMoreBar"',
            'id="journalLoadMoreBar"',
            'id="focusLoadMoreBar"',
            'id="taskListLoadMoreBar"',
            'id="countdownLoadMoreBar"',
            'id="adminUsersSearchInput"',
            'id="adminUsersPagination"',
            "loadAdminUsers",
            "renderTaskViewsFromCache",
            "renderLoadMoreBar",
            "toggleExpandQuadrant",
        ):
            self.assertIn(marker, html, f"Missing pagination UI marker: {marker}")


if __name__ == "__main__":
    unittest.main()
