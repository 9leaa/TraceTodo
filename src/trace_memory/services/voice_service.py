"""Lightweight ephemeral short-audio transcription fallback (`转完即删临时音频`).

Anti-bloat iron rules:
- No resident local speech recognition model on the server.
- Browser Web Speech API and Apple SFSpeechRecognizer are primary zero-cost engines;
  this service only handles short-audio fallback when native browser speech API is unavailable.
- Temporary audio files are ALWAYS physically deleted in `finally` immediately after transcription.
"""

from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request
import uuid

from trace_memory.config import TraceMemoryConfig
from trace_memory.domain.proposals import TraceMemoryError, ValidationError


_ALLOWED_AUDIO_MIMES: dict[str, str] = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/m4a": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}

_MAX_AUDIO_BYTES = 3 * 1024 * 1024  # 3 MB cap (~1-2 min compressed voice note)


def _decode_audio_payload(raw_b64: str, mime_type: str = "audio/webm") -> tuple[bytes, str, str]:
    clean = (raw_b64 or "").strip()
    if not clean:
        raise ValidationError("音频内容不能为空")

    detected_mime = (mime_type or "audio/webm").split(";")[0].strip().lower()
    if clean.startswith("data:"):
        header, _, b64_part = clean.partition(",")
        clean = b64_part.strip()
        if ":" in header and ";" in header:
            detected_mime = header.split(":", 1)[1].split(";", 1)[0].strip().lower()

    if detected_mime not in _ALLOWED_AUDIO_MIMES:
        raise ValidationError(f"不支持的音频格式: {detected_mime}")

    try:
        audio_bytes = base64.b64decode(clean, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError("无效的 Base64 音频数据") from exc

    if len(audio_bytes) < 16:
        raise ValidationError("音频片段过短，请重新录制")
    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        raise ValidationError("单条语音时长或体积超出上限（最大 3MB）")

    ext = _ALLOWED_AUDIO_MIMES[detected_mime]
    return audio_bytes, detected_mime, ext


class VoiceTranscriptionService:
    """Short-audio transcription fallback with guaranteed immediate temp-file cleanup."""

    def __init__(self, config: TraceMemoryConfig) -> None:
        self.config = config
        self.tmp_dir = Path(config.data_dir) / "tmp_voice"

    def transcribe_short_audio(
        self,
        *,
        audio_base64: str,
        mime_type: str = "audio/webm",
        language: str = "zh",
        hint_text: str = "",
    ) -> dict[str, Any]:
        audio_bytes, norm_mime, ext = _decode_audio_payload(audio_base64, mime_type)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.tmp_dir / f"voice_{uuid.uuid4().hex}{ext}"

        try:
            tmp_path.write_bytes(audio_bytes)
            if self.config.llm_provider == "openai" and self.config.openai_api_key:
                text, tokens = self._transcribe_remote(
                    tmp_path=tmp_path,
                    audio_bytes=audio_bytes,
                    mime_type=norm_mime,
                    language=language,
                )
                provider = "openai_compat_asr"
            else:
                # Deterministic zero-dependency fallback when running in mock/offline mode
                clean_hint = (hint_text or "").strip()
                text = clean_hint if clean_hint else "明天下午三点复盘项目架构方案"
                tokens = 0
                provider = "mock_asr"
        finally:
            tmp_path.unlink(missing_ok=True)

        return {
            "text": text.strip(),
            "provider": provider,
            "tokens_used": tokens,
            "temp_file_deleted": not tmp_path.exists(),
        }

    def _transcribe_remote(
        self,
        *,
        tmp_path: Path,
        audio_bytes: bytes,
        mime_type: str,
        language: str,
    ) -> tuple[str, int]:
        base = self.config.openai_base_url.rstrip("/")
        model = (self.config.asr_model or "whisper-1").strip()

        # Support multimodal audio chat models (e.g. qwen-audio-turbo / gpt-4o-audio-preview)
        if "audio" in model.lower() and "whisper" not in model.lower():
            b64_str = base64.b64encode(audio_bytes).decode("ascii")
            fmt = tmp_path.suffix.lstrip(".") or "wav"
            payload: dict[str, Any] = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {"data": f"data:{mime_type};base64,{b64_str}", "format": fmt},
                            },
                            {"type": "text", "text": "请将这段中文或英文语音逐字准确转写为纯文本，不要添加任何多余解释。"},
                        ],
                    }
                ],
            }
            req = urllib.request.Request(
                f"{base}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.config.openai_api_key}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.config.llm_timeout_seconds) as resp:
                    parsed = json.loads(resp.read().decode("utf-8"))
                txt = str(parsed["choices"][0]["message"]["content"] or "").strip()
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                raise TraceMemoryError(f"语音转写服务响应错误 ({exc.code}): {detail}") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                raise TraceMemoryError(f"语音转写服务调用失败: {exc}") from exc
            usage_obj = parsed.get("usage")
            usage = usage_obj if isinstance(usage_obj, dict) else {}
            tok = int(usage.get("total_tokens", 0) or 0)
            return txt, tok

        # Standard OpenAI-compatible multipart/form-data `/audio/transcriptions`
        boundary = f"----TraceTodoBoundary{uuid.uuid4().hex}"
        lang_code = (language or "zh").split("-")[0].strip()[:8] or "zh"
        parts: list[bytes] = []

        def _add_field(name: str, val: str) -> None:
            parts.append(f"--{boundary}\r\n".encode("utf-8"))
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
            parts.append(f"{val}\r\n".encode("utf-8"))

        _add_field("model", model)
        _add_field("language", lang_code)

        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(
            f'Content-Disposition: form-data; name="file"; filename="{tmp_path.name}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8")
        )
        parts.append(audio_bytes)
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))

        body = b"".join(parts)
        req = urllib.request.Request(
            f"{base}/audio/transcriptions",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Authorization": f"Bearer {self.config.openai_api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.llm_timeout_seconds) as resp:
                parsed = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise TraceMemoryError(f"语音转写服务响应错误 ({exc.code}): {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise TraceMemoryError(f"语音转写服务调用失败: {exc}") from exc
        return str((parsed if isinstance(parsed, dict) else {}).get("text") or "").strip(), 0
