from __future__ import annotations

import base64
from pathlib import Path
import tempfile
import unittest

from urllib.parse import parse_qs, urlparse

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestPhase2VoiceAndImageMemory(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cfg = TraceMemoryConfig(
            default_space="default",
            data_dir=self.root / "data",
            knowledge_dir=self.root / "knowledge",
            auth_required=False,
        )
        self.container = AppContainer.build(self.cfg)
        self.api = ApiDispatcher(self.container)

    def tearDown(self) -> None:
        self.container.close()
        self._tmp.cleanup()

    def test_short_voice_transcription_fallback_auto_ingest_and_zero_residual_temp_files(self) -> None:
        fake_webm = b"\x1a\x45\xdf\xa3" + (b"\x01\x02\x03\x04" * 20)
        b64_data = "data:audio/webm;base64," + base64.b64encode(fake_webm).decode("ascii")

        code, res = self.api.dispatch(
            "POST",
            "/api/voice/transcribe",
            body={
                "space_id": "voice_sp",
                "audio_base64": b64_data,
                "mime_type": "audio/webm",
                "hint_text": "明天必须完成线上灰度发布检查",
                "auto_ingest": True,
            },
        )
        self.assertEqual(code, 200)
        self.assertEqual(res["text"], "明天必须完成线上灰度发布检查")
        self.assertTrue(res["temp_file_deleted"])
        self.assertIn("ingest", res)

        # Verify zero residual temporary audio files exist on disk
        tmp_voice_dir = self.cfg.data_dir / "tmp_voice"
        if tmp_voice_dir.exists():
            self.assertEqual(list(tmp_voice_dir.iterdir()), [])

        # Verify task was automatically extracted into voice_sp
        _, matrix = self.api.dispatch("GET", "/api/tasks/matrix", {"space_id": ["voice_sp"]})
        self.assertGreaterEqual(matrix["summary_counts"]["total_todo"], 1)

        # Reject non-audio MIME types cleanly
        code_bad, _ = self.api.dispatch(
            "POST",
            "/api/voice/transcribe",
            body={"audio_base64": base64.b64encode(b"x" * 64).decode("ascii"), "mime_type": "application/pdf"},
        )
        self.assertEqual(code_bad, 400)

    def test_voice_input_ui_markers_in_web_and_apple(self) -> None:
        html = (REPO_ROOT / "src" / "trace_memory" / "api" / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn('id="quickVoiceBtn"', html)
        self.assertIn('id="notesVoiceBtn"', html)
        self.assertIn("webkitSpeechRecognition", html)
        self.assertIn("/api/voice/transcribe", html)

        swift_voice = (REPO_ROOT / "apple-app" / "Sources" / "TraceTodo" / "VoiceInputManager.swift").read_text(
            encoding="utf-8"
        )
        self.assertIn("SFSpeechRecognizer", swift_voice)
        self.assertIn("struct VoiceMicButton: View", swift_voice)

    def test_image_upload_vlm_ocr_fts_qa_provenance_and_cascade_disk_cleanup(self) -> None:
        # Minimal valid JPEG magic header + payload
        fake_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + (b"\xab" * 256) + b"\xff\xd9"
        fake_thumb = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + (b"\xcd" * 64) + b"\xff\xd9"
        img_b64 = "data:image/jpeg;base64," + base64.b64encode(fake_jpeg).decode("ascii")
        th_b64 = "data:image/jpeg;base64," + base64.b64encode(fake_thumb).decode("ascii")

        # 1) Upload image to alice space
        code_up, up_res = self.api.dispatch(
            "POST",
            "/api/media/upload",
            body={
                "space_id": "alice",
                "image_base64": img_b64,
                "thumb_base64": th_b64,
                "width": 1280,
                "height": 960,
                "vision_hint": "明天必须修复微信网关超时告警 G1208白板记录",
            },
        )
        self.assertEqual(code_up, 200)
        media = up_res["media"]
        media_id = media["media_id"]

        # Verify physical files exist on disk under data/uploads/alice/
        alice_upload_dir = self.cfg.uploads_dir / "alice"
        self.assertTrue((alice_upload_dir / f"{media_id}.jpg").is_file())
        self.assertTrue((alice_upload_dir / f"{media_id}_thumb.jpg").is_file())

        # Verify signed URL serves binary JPEG & tampered signature under cross-space fails
        parsed_url = urlparse(media["url"])
        qp_valid = parse_qs(parsed_url.query)
        code_f, mime_f, bytes_f = self.api.resolve_media_file(qp_valid)
        self.assertEqual(code_f, 200)
        self.assertEqual(mime_f, "image/jpeg")
        self.assertEqual(bytes_f, fake_jpeg)

        code_forbid, _, _ = self.api.resolve_media_file({"id": [media_id], "space_id": ["bob"]})
        self.assertEqual(code_forbid, 403)

        # 2) Attach image to a synchronous note -> VLM OCR indexed into FTS5 + Task extracted + QA cites image
        code_rec, rec_res = self.api.dispatch(
            "POST",
            "/api/notes/record",
            query_params={"sync": ["1"]},
            body={
                "space_id": "alice",
                "content": "白板拍照存档",
                "media_ids": [media_id],
                "async_mode": False,
            },
        )
        self.assertEqual(code_rec, 200)
        note_id = rec_res["note"]["note_id"]

        # FTS5 recalls note via OCR token 'G1208' that was NOT in typed content!
        code_nl, nl_res = self.api.dispatch("GET", "/api/notes/list", {"space_id": ["alice"], "q": ["G1208"]})
        self.assertEqual(code_nl, 200)
        self.assertEqual(nl_res["count"], 1)
        self.assertEqual(len(nl_res["notes"][0]["media"]), 1)

        # Ask QA recalls and returns image provenance in citations.media
        code_ask, ask_res = self.api.dispatch(
            "POST",
            "/api/ask",
            body={"space_id": "alice", "question": "白板拍照里有什么微信网关告警待办？"},
        )
        self.assertEqual(code_ask, 200)
        self.assertTrue(ask_res["has_sufficient_evidence"])
        self.assertGreaterEqual(len(ask_res["citations"]["media"]), 1)
        self.assertEqual(ask_res["citations"]["media"][0]["media_id"], media_id)

        # 3) Forget the RawNote -> disk image & thumbnail MUST be physically deleted!
        code_fg, _ = self.api.dispatch(
            "POST",
            "/api/notes/forget",
            body={"space_id": "alice", "note_id": note_id},
        )
        self.assertEqual(code_fg, 200)
        self.assertFalse((alice_upload_dir / f"{media_id}.jpg").exists())
        self.assertFalse((alice_upload_dir / f"{media_id}_thumb.jpg").exists())
        _, after_list = self.api.dispatch("GET", "/api/media/list", {"space_id": ["alice"]})
        self.assertEqual(after_list["count"], 0)

        # 4) Also verify cascade disk cleanup when forgetting a MemoryItem directly
        _, up2 = self.api.dispatch(
            "POST",
            "/api/media/upload",
            body={
                "space_id": "alice",
                "image_base64": img_b64,
                "vision_hint": "我现在居住在苏州市工业园区",
            },
        )
        mid2 = up2["media"]["media_id"]
        _, rec2 = self.api.dispatch(
            "POST",
            "/api/notes/record",
            query_params={"sync": ["1"]},
            body={"space_id": "alice", "content": "", "media_ids": [mid2], "async_mode": False},
        )
        self.assertTrue((alice_upload_dir / f"{mid2}.jpg").is_file())
        created_mem = rec2["changes"][0]["memory"]
        code_mf, _ = self.api.dispatch(
            "POST",
            "/api/memories/propose",
            body={
                "space_id": "alice",
                "action": "forget",
                "memory_type": created_mem["memory_type"],
                "target_memory_id": created_mem["memory_id"],
                "expected_version": created_mem["version"],
            },
        )
        self.assertEqual(code_mf, 200)
        self.assertFalse((alice_upload_dir / f"{mid2}.jpg").exists())

        # 5) Reject non-image HTML polyglot payload masquerading as image
        bad_html_b64 = base64.b64encode(b"<html><script>alert(1)</script></html>").decode("ascii")
        code_poly, _ = self.api.dispatch(
            "POST",
            "/api/media/upload",
            body={"space_id": "alice", "image_base64": bad_html_b64},
        )
        self.assertEqual(code_poly, 400)


if __name__ == "__main__":
    unittest.main()
