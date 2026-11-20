from __future__ import annotations

import base64
from pathlib import Path
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
