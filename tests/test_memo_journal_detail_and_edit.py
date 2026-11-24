from __future__ import annotations

import base64
from pathlib import Path
import tempfile
import unittest

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.domain.notes import beijing_now


REPO_ROOT = Path(__file__).resolve().parents[1]

FAKE_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    + (b"\xab" * 192)
    + b"\xff\xd9"
)
FAKE_THUMB = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    + (b"\xcd" * 64)
    + b"\xff\xd9"
)


class TestMemoJournalDetailAndEdit(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cfg = TraceMemoryConfig(
            default_space="default",
            data_dir=self.root / "data",
            knowledge_dir=self.root / "knowledge",
            auth_required=False,
            rate_limit_per_minute=200,
        )
        self.container = AppContainer.build(self.cfg)
        self.api = ApiDispatcher(self.container)

    def tearDown(self) -> None:
        self.container.close()
        self._tmp.cleanup()

    def _upload_sample_image(self, space_id: str, vision_hint: str) -> dict:
        img_b64 = "data:image/jpeg;base64," + base64.b64encode(FAKE_JPEG).decode("ascii")
        th_b64 = "data:image/jpeg;base64," + base64.b64encode(FAKE_THUMB).decode("ascii")
        code, res = self.api.dispatch(
            "POST",
            "/api/media/upload",
            body={
                "space_id": space_id,
                "image_base64": img_b64,
                "thumb_base64": th_b64,
                "width": 800,
                "height": 600,
                "vision_hint": vision_hint,
            },
        )
        self.assertEqual(code, 200)
        return res["media"]

    def test_delete_bound_media_refreshes_memo_fts_and_removes_attachment(self) -> None:
        media = self._upload_sample_image("memo_media_sp", "白板手写密钥 ZEBRA889 会议记录")
        media_id = media["media_id"]

        c_save, r_save = self.api.dispatch(
            "POST",
            "/api/memos/save",
            body={
                "space_id": "memo_media_sp",
                "title": "架构复盘",
                "content": "核心主体词 ALPHA101 需要保留在备忘正文中",
                "tags": ["架构"],
                "pinned": False,
                "extract_memory": False,
                "media_ids": [media_id],
            },
        )
        self.assertEqual(c_save, 200)
        note_id = r_save["memo"]["note_id"]
        self.assertEqual(len(r_save["memo"]["media"]), 1)

        # Both OCR token (from image) and body token match before deletion
        c_ocr1, r_ocr1 = self.api.dispatch(
            "GET", "/api/memos/list", {"space_id": ["memo_media_sp"], "q": ["ZEBRA889"]}
        )
        self.assertEqual(c_ocr1, 200)
        self.assertEqual(len(r_ocr1["memos"]), 1)
        self.assertEqual(r_ocr1["memos"][0]["note_id"], note_id)

        # Delete bound media directly (edit-mode delete old photo)
        c_del, r_del = self.api.dispatch(
            "POST",
            "/api/media/delete",
            body={"space_id": "memo_media_sp", "media_id": media_id},
        )
        self.assertEqual(c_del, 200)
        self.assertTrue(r_del["deleted"])

        # Deleted image file & OCR FTS hit must be gone, while memo body hit remains
        self.assertFalse((self.cfg.uploads_dir / "memo_media_sp" / f"{media_id}.jpg").exists())
        c_ocr2, r_ocr2 = self.api.dispatch(
            "GET", "/api/memos/list", {"space_id": ["memo_media_sp"], "q": ["ZEBRA889"]}
        )
        self.assertEqual(c_ocr2, 200)
        self.assertEqual(len(r_ocr2["memos"]), 0)

        c_body, r_body = self.api.dispatch(
            "GET", "/api/memos/list", {"space_id": ["memo_media_sp"], "q": ["ALPHA101"]}
        )
        self.assertEqual(c_body, 200)
        self.assertEqual(len(r_body["memos"]), 1)
        self.assertEqual(r_body["memos"][0]["media"], [])

    def test_historical_journal_edit_and_image_delete_preserves_today(self) -> None:
        today_ymd = beijing_now().date().isoformat()
        hist_ymd = "2026-10-15"
        hist_media = self._upload_sample_image("jrn_edit_sp", "旧照片 OCR 词 HIST991")
        media_id = hist_media["media_id"]

        # Create historical journal with attached image + today's journal
        c_h1, r_h1 = self.api.dispatch(
            "POST",
            "/api/journal/save",
            body={
                "space_id": "jrn_edit_sp",
                "journal_date": hist_ymd,
                "mood": "calm",
                "content": "历史日记初版内容",
                "extract_memory": False,
                "media_ids": [media_id],
            },
        )
        self.assertEqual(c_h1, 200)
        self.assertEqual(r_h1["journal"]["revision"], 1)
        self.assertEqual(len(r_h1["journal"]["media"]), 1)

        c_t1, _ = self.api.dispatch(
            "POST",
            "/api/journal/save",
            body={
                "space_id": "jrn_edit_sp",
                "journal_date": today_ymd,
                "mood": "joy",
                "content": "今日日记完整内容",
                "extract_memory": False,
            },
        )
        self.assertEqual(c_t1, 200)

        # Delete the historical image, then edit & re-save the historical journal
        c_del, r_del = self.api.dispatch(
            "POST",
            "/api/media/delete",
            body={"space_id": "jrn_edit_sp", "media_id": media_id},
        )
        self.assertEqual(c_del, 200)
        self.assertTrue(r_del["deleted"])

        c_h2, r_h2 = self.api.dispatch(
            "POST",
            "/api/journal/save",
            body={
                "space_id": "jrn_edit_sp",
                "journal_date": hist_ymd,
                "mood": "focused",
                "content": "历史日记修订第二版",
                "extract_memory": False,
                "media_ids": [],
            },
        )
        self.assertEqual(c_h2, 200)
        self.assertEqual(r_h2["journal"]["revision"], 2)
        self.assertEqual(r_h2["journal"]["mood"], "focused")
        self.assertEqual(r_h2["journal"]["media"], [])

        # Confirm historical overview & today overview remain independent
        c_gh, r_gh = self.api.dispatch(
            "GET", "/api/journal/overview", {"space_id": ["jrn_edit_sp"], "date": [hist_ymd]}
        )
        self.assertEqual(c_gh, 200)
        self.assertEqual(r_gh["current_entry"]["content"], "历史日记修订第二版")
        self.assertEqual(r_gh["current_entry"]["revision"], 2)
        self.assertEqual(r_gh["current_entry"]["media"], [])

        c_gt, r_gt = self.api.dispatch(
            "GET", "/api/journal/overview", {"space_id": ["jrn_edit_sp"], "date": [today_ymd]}
        )
        self.assertEqual(c_gt, 200)
        self.assertEqual(r_gt["current_entry"]["content"], "今日日记完整内容")
        self.assertEqual(r_gt["current_entry"]["mood"], "joy")

    def test_dashboard_html_exposes_detail_modal_and_historical_edit_ui(self) -> None:
        html = (REPO_ROOT / "src" / "trace_memory" / "api" / "dashboard.html").read_text(encoding="utf-8")
        for marker in (
            'id="noteDetailModal"',
            'id="journalEditingBanner"',
            'id="journalSaveBtn"',
            "openNoteDetailModal",
            "closeNoteDetailModal",
            "editJournalByDate",
            "updateJournalEditingUi",
            "removeExistingNoteMedia",
            "_existingEditMedia",
            "_inlineEditMemoId",
            "saveInlineMemoEdit",
            "cancelInlineMemoEdit",
            "_inlineEditJournalId",
            "startInlineJournalEdit",
            "saveInlineJournalEdit",
            "cancelInlineJournalEdit",
            "is-inline-editing",
            'id="mediaLightboxModal" style="z-index: 75;"',
        ):
            self.assertIn(marker, html, f"Missing UI marker: {marker}")


if __name__ == "__main__":
    unittest.main()
