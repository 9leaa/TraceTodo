"""Disk-isolated Image Attachment storage, VLM Caption/OCR extraction, and Cascade Purge.

Anti-bloat & security guarantees:
- Binary image files live strictly on disk (`data/uploads/{safe_space_id}/`), NEVER in SQLite.
- Validates real image magic bytes (JPEG / PNG / WebP / GIF) to prevent polyglot/HTML XSS uploads.
- Enforces per-file size cap and per-space storage quota (`space_media_quota_mb`).
- Extracts visual Caption + OCR via `TRACE_MEMORY_VISION_MODEL` (default `qwen3.8-omni-flash`) or
  deterministic offline mock fallback, indexing into FTS5 + Vector Store.
- Physically deletes disk image + thumbnail files when parent `RawNote` or `MemoryItem` is forgotten.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Any
from urllib.parse import quote
import urllib.request

from trace_memory.config import TraceMemoryConfig
from trace_memory.domain.notes import RawNote, beijing_now_iso, new_id
from trace_memory.domain.proposals import (
    NotFoundError,
    PermissionDeniedError,
    QuotaExceededError,
    ValidationError,
)
from trace_memory.storage.database import Database, tokenize_for_fts


_MAX_SINGLE_IMAGE_BYTES = int(2.5 * 1024 * 1024)  # 2.5 MB hard cap (clients compress to ~150-300 KB)


def _detect_image_mime_and_ext(raw_bytes: bytes) -> tuple[str, str]:
    if raw_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if len(raw_bytes) >= 12 and raw_bytes[:4] == b"RIFF" and raw_bytes[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if raw_bytes.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", ".gif"
    raise ValidationError("不支持的图片格式或文件头损坏（仅支持 JPEG / PNG / WebP / GIF）")


def _decode_base64_image(raw_b64: str) -> tuple[bytes, str, str]:
    clean = (raw_b64 or "").strip()
    if not clean:
        raise ValidationError("图片数据不能为空")
    if clean.startswith("data:"):
        _, _, clean = clean.partition(",")
        clean = clean.strip()
    try:
        img_bytes = base64.b64decode(clean, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError("无效的 Base64 图片编码") from exc

    if len(img_bytes) < 16:
        raise ValidationError("图片数据过小或已损坏")
    if len(img_bytes) > _MAX_SINGLE_IMAGE_BYTES:
        raise ValidationError("单张图片超出大小上限（请在端侧压缩后上传，上限 2.5MB）")

    mime, ext = _detect_image_mime_and_ext(img_bytes)
    return img_bytes, mime, ext


def _safe_space_dirname(space_id: str) -> str:
    sp = (space_id or "default").strip() or "default"
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", sp)[:40].strip("_")
    if cleaned and cleaned == sp:
        return cleaned
    digest = hashlib.sha256(sp.encode("utf-8")).hexdigest()[:10]
    return f"{cleaned or 'sp'}_{digest}"


class MediaAttachmentService:
    """Manages disk image files, `media_attachments` metadata, VLM OCR, and cascade purge."""

    def __init__(
        self,
        db: Database,
        config: TraceMemoryConfig,
        sync_tracker: Any = None,
    ) -> None:
        self.db = db
        self.config = config
        self.sync_tracker = sync_tracker
        self.uploads_root = Path(config.uploads_dir)
        self._hmac_secret = self._load_or_init_hmac_secret()

    def _load_or_init_hmac_secret(self) -> bytes:
        row = self.db.query_one(
            "SELECT value_json FROM system_settings WHERE key = 'media_hmac_secret'"
        )
        if row is not None:
            try:
                sec = str(json.loads(row["value_json"])).strip()
                if len(sec) >= 32:
                    return sec.encode("utf-8")
            except Exception:
                pass
        fresh = secrets.token_hex(32)
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO system_settings (key, value_json, updated_at)
                VALUES ('media_hmac_secret', ?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (json.dumps(fresh), beijing_now_iso()),
            )
        row2 = self.db.query_one(
            "SELECT value_json FROM system_settings WHERE key = 'media_hmac_secret'"
        )
        if row2 is not None:
            return str(json.loads(row2["value_json"])).encode("utf-8")
        return fresh.encode("utf-8")

    def sign_media_sig(self, space_id: str, media_id: str) -> str:
        msg = f"{space_id}:{media_id}".encode("utf-8")
        return hmac.new(self._hmac_secret, msg, hashlib.sha256).hexdigest()[:32]

    def verify_media_sig(self, space_id: str, media_id: str, sig: str) -> bool:
        if not sig:
            return False
        expected = self.sign_media_sig(space_id, media_id)
        return hmac.compare_digest(expected, sig.strip())

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        sp = str(row["space_id"])
        mid = str(row["media_id"])
        sig = self.sign_media_sig(sp, mid)
        qs_base = f"id={quote(mid)}&space_id={quote(sp)}&sig={sig}"
        return {
            "media_id": mid,
            "space_id": sp,
            "note_id": str(row["note_id"] or ""),
            "mime_type": str(row["mime_type"]),
            "width": int(row["width"] or 0),
            "height": int(row["height"] or 0),
            "size_bytes": int(row["size_bytes"] or 0),
            "caption": str(row["caption"] or ""),
            "ocr_text": str(row["ocr_text"] or ""),
            "vision_status": str(row["vision_status"] or "pending"),
            "url": f"/api/media/file?{qs_base}",
            "thumb_url": f"/api/media/file?{qs_base}&thumb=1",
            "created_at": str(row["created_at"]),
        }

    def get_space_used_bytes(self, space_id: str) -> int:
        row = self.db.query_one(
            "SELECT COALESCE(SUM(size_bytes), 0) AS used FROM media_attachments WHERE space_id = ?",
            (space_id,),
        )
        return int(row["used"]) if row else 0

    def upload_image(
        self,
        *,
        space_id: str,
        image_base64: str,
        thumb_base64: str = "",
        width: int = 0,
        height: int = 0,
        note_id: str = "",
        vision_hint: str = "",
        analyze_immediately: bool = False,
    ) -> dict[str, Any]:
        sp = (space_id or "default").strip() or "default"
        img_bytes, mime, ext = _decode_base64_image(image_base64)

        thumb_bytes = b""
        thumb_ext = ext
        if (thumb_base64 or "").strip():
            thumb_bytes, _, thumb_ext = _decode_base64_image(thumb_base64)

        add_bytes = len(img_bytes) + len(thumb_bytes)
        quota_bytes = max(1, int(self.config.space_media_quota_mb)) * 1024 * 1024
        used_bytes = self.get_space_used_bytes(sp)
        if used_bytes + add_bytes > quota_bytes:
            raise QuotaExceededError(
                f"当前空间图片存储配额已满（上限 {self.config.space_media_quota_mb}MB），请先清理或遗忘不需要的旧图"
            )

        media_id = new_id("med")
        safe_dir = _safe_space_dirname(sp)
        target_dir = self.uploads_root / safe_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        file_rel = f"{safe_dir}/{media_id}{ext}"
        abs_file = (self.uploads_root / file_rel).resolve()
        root_resolved = self.uploads_root.resolve()
        if not abs_file.is_relative_to(root_resolved):
            raise PermissionDeniedError("非法文件路径")
        abs_file.write_bytes(img_bytes)

        if thumb_bytes:
            thumb_rel = f"{safe_dir}/{media_id}_thumb{thumb_ext}"
            abs_thumb = (self.uploads_root / thumb_rel).resolve()
            if not abs_thumb.is_relative_to(root_resolved):
                raise PermissionDeniedError("非法缩略图路径")
            abs_thumb.write_bytes(thumb_bytes)
        else:
            thumb_rel = file_rel

        caption = ""
        ocr_text = ""
        vision_status = "pending"
        clean_hint = (vision_hint or "").strip()[:500]
        if clean_hint:
            # Store optional hint metadata for deterministic mock mode or context
            caption = clean_hint

        now_iso = beijing_now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO media_attachments (
                    media_id, space_id, note_id, file_rel_path, thumb_rel_path,
                    mime_type, width, height, size_bytes, caption, ocr_text,
                    vision_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    media_id,
                    sp,
                    (note_id or "").strip(),
                    file_rel,
                    thumb_rel,
                    mime,
                    max(0, int(width or 0)),
                    max(0, int(height or 0)),
                    add_bytes,
                    caption,
                    ocr_text,
                    vision_status,
                    now_iso,
                ),
            )

        if analyze_immediately:
            self.analyze_single_media(sp, media_id)

        row = self.db.query_one(
            "SELECT * FROM media_attachments WHERE media_id = ? AND space_id = ?",
            (media_id, sp),
        )
        assert row is not None
        return self._row_to_dict(row)

    def bind_to_note(self, space_id: str, note_id: str, media_ids: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
        sp = (space_id or "default").strip() or "default"
        clean_ids = [str(m).strip() for m in media_ids if str(m).strip()]
        if not clean_ids:
            return []

        for mid in clean_ids:
            any_row = self.db.query_one(
                "SELECT space_id, note_id FROM media_attachments WHERE media_id = ?",
                (mid,),
            )
            if any_row is None:
                raise NotFoundError(f"图片附件不存在: {mid}")
            if str(any_row["space_id"]) != sp:
                raise PermissionDeniedError("无权绑定其他空间的图片附件")

        with self.db.transaction() as conn:
            for mid in clean_ids:
                conn.execute(
                    "UPDATE media_attachments SET note_id = ? WHERE media_id = ? AND space_id = ?",
                    (note_id, mid, sp),
                )

        return self.list_for_notes(sp, [note_id]).get(note_id, [])

    def analyze_single_media(self, space_id: str, media_id: str) -> tuple[dict[str, Any], int]:
        row = self.db.query_one(
            "SELECT * FROM media_attachments WHERE media_id = ? AND space_id = ?",
            (media_id, space_id),
        )
        if row is None:
            raise NotFoundError(f"图片附件不存在: {media_id}")

        abs_file = (self.uploads_root / str(row["file_rel_path"])).resolve()
        if not abs_file.is_file():
            return self._row_to_dict(row), 0

        existing_hint = str(row["caption"] or "").strip()
        mime = str(row["mime_type"] or "image/jpeg")
        tokens_used = 0

        if self.config.llm_provider == "openai" and self.config.openai_api_key:
            try:
                caption, ocr_text, tokens_used = self._call_remote_vlm(abs_file.read_bytes(), mime)
                status = "completed"
            except Exception as exc:
                caption = existing_hint or f"图片附件 ({mime})"
                ocr_text = ""
                status = f"degraded:{str(exc)[:60]}"
        else:
            # Deterministic zero-dependency Vision/OCR fallback for mock mode & offline tests
            if existing_hint:
                caption = f"图片视觉摘要：{existing_hint}"
                ocr_text = existing_hint
            else:
                caption = f"已归档视觉图片 ({row['width']}x{row['height']})"
                ocr_text = ""
            status = "completed"

        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE media_attachments
                SET caption = ?, ocr_text = ?, vision_status = ?
                WHERE media_id = ? AND space_id = ?
                """,
                (caption[:600], ocr_text[:1500], status, media_id, space_id),
            )

        fresh = self.db.query_one(
            "SELECT * FROM media_attachments WHERE media_id = ? AND space_id = ?",
            (media_id, space_id),
        )
        assert fresh is not None
        return self._row_to_dict(fresh), tokens_used

    def analyze_pending_for_note(self, space_id: str, note_id: str) -> tuple[str, int, int]:
        """Run VLM Caption + OCR on any pending media attached to `note_id`.

        Returns `(combined_visual_text, vlm_calls, vlm_tokens)`.
        """
        rows = self.db.query_all(
            "SELECT * FROM media_attachments WHERE space_id = ? AND note_id = ? ORDER BY created_at ASC",
            (space_id, note_id),
        )
        if not rows:
            return "", 0, 0

        parts: list[str] = []
        calls = 0
        total_toks = 0
        for r in rows:
            if str(r["vision_status"]) == "pending":
                info, toks = self.analyze_single_media(space_id, str(r["media_id"]))
                if self.config.llm_provider == "openai" and self.config.openai_api_key:
                    calls += 1
                total_toks += toks
            else:
                info = self._row_to_dict(r)
            cap = str(info.get("caption") or "").strip()
            ocr = str(info.get("ocr_text") or "").strip()
            seg = " | ".join(x for x in (f"图片摘要: {cap}" if cap else "", f"OCR文字: {ocr}" if ocr else "") if x)
            if seg:
                parts.append(seg)

        return "\n".join(parts), calls, total_toks

    def get_visual_text_for_note(self, space_id: str, note_id: str) -> str:
        rows = self.db.query_all(
            "SELECT caption, ocr_text FROM media_attachments WHERE space_id = ? AND note_id = ? ORDER BY created_at ASC",
            (space_id, note_id),
        )
        parts: list[str] = []
        for r in rows:
            cap = str(r["caption"] or "").strip()
            ocr = str(r["ocr_text"] or "").strip()
            seg = " ".join(x for x in (cap, ocr) if x)
            if seg:
                parts.append(seg)
        return "\n".join(parts)

    def refresh_note_fts_with_media(self, space_id: str, note: RawNote) -> str:
        """Re-index `notes_fts` combining `note.visible_content()` + attached VLM Caption & OCR."""
        vis = note.visible_content()
        visual_extra = self.get_visual_text_for_note(space_id, note.note_id)
        combined = f"{vis}\n{visual_extra}".strip() if visual_extra else vis
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM notes_fts WHERE note_id = ?", (note.note_id,))
            if not note.is_forgotten and combined:
                conn.execute(
                    "INSERT INTO notes_fts (note_id, space_id, segmented_text) VALUES (?, ?, ?)",
                    (note.note_id, space_id, tokenize_for_fts(combined)),
                )
        return combined

    def _call_remote_vlm(self, image_bytes: bytes, mime_type: str) -> tuple[str, str, int]:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        base = self.config.openai_base_url.rstrip("/")
        model = (self.config.vision_model or "qwen3.8-omni-flash").strip()
        payload: dict[str, Any] = {
            "model": model,
            "temperature": 0.1,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                        {
                            "type": "text",
                            "text": (
                                "请解析这张图片，提取简洁准确的中文视觉摘要(caption)与图中出现的全部关键文字"
                                "(ocr_text，例如白板待办、火车票/机票时间车次、账单截图、会议纪要)。"
                                '请仅输出严格 JSON 对象：{"caption": "...", "ocr_text": "..."}'
                            ),
                        },
                    ],
                }
            ],
        }
        if "dashscope.aliyuncs.com" in base:
            payload["enable_thinking"] = False

        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.openai_api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.config.llm_timeout_seconds) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
        content = str(parsed["choices"][0]["message"]["content"] or "").strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.DOTALL).strip()
        tokens = int(parsed.get("usage", {}).get("total_tokens", 0) or 0)
        try:
            obj = json.loads(content)
            return str(obj.get("caption") or "").strip(), str(obj.get("ocr_text") or "").strip(), tokens
        except json.JSONDecodeError:
            return content[:400], content[:800], tokens

    def list_for_notes(self, space_id: str, note_ids: list[str] | tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
        clean = [str(n).strip() for n in note_ids if str(n).strip()]
        if not clean:
            return {}
        placeholders = ",".join("?" for _ in clean)
        rows = self.db.query_all(
            f"SELECT * FROM media_attachments WHERE space_id = ? AND note_id IN ({placeholders}) ORDER BY created_at ASC",
            (space_id, *clean),
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            nid = str(r["note_id"])
            grouped.setdefault(nid, []).append(self._row_to_dict(r))
        return grouped

    def list_for_space(self, space_id: str, limit: int = 60) -> list[dict[str, Any]]:
        sp = (space_id or "default").strip() or "default"
        rows = self.db.query_all(
            "SELECT * FROM media_attachments WHERE space_id = ? ORDER BY created_at DESC LIMIT ?",
            (sp, max(1, min(200, int(limit or 60)))),
        )
        return [self._row_to_dict(r) for r in rows]

    def read_binary_file(self, space_id: str, media_id: str, *, thumb: bool = False) -> tuple[bytes, str]:
        any_row = self.db.query_one(
            "SELECT space_id, file_rel_path, thumb_rel_path, mime_type FROM media_attachments WHERE media_id = ?",
            (media_id,),
        )
        if any_row is None:
            raise NotFoundError("图片附件不存在或已被清理")
        if str(any_row["space_id"]) != space_id:
            raise PermissionDeniedError("无权访问其他空间的图片附件")

        rel = str(any_row["thumb_rel_path"] or any_row["file_rel_path"]) if thumb else str(any_row["file_rel_path"])
        abs_path = (self.uploads_root / rel).resolve()
        if not abs_path.is_relative_to(self.uploads_root.resolve()) or not abs_path.is_file():
            raise NotFoundError("磁盘图片文件不存在或已被遗忘清理")
        return abs_path.read_bytes(), str(any_row["mime_type"] or "image/jpeg")

    def _unlink_row_files(self, row: sqlite3.Row) -> int:
        removed = 0
        root_res = self.uploads_root.resolve()
        for col in ("file_rel_path", "thumb_rel_path"):
            rel = str(row[col] or "").strip()
            if not rel:
                continue
            cand = (self.uploads_root / rel).resolve()
            if cand.is_relative_to(root_res) and cand.is_file():
                cand.unlink(missing_ok=True)
                removed += 1
        return removed

    def delete_media(self, space_id: str, media_id: str) -> bool:
        any_row = self.db.query_one("SELECT * FROM media_attachments WHERE media_id = ?", (media_id,))
        if any_row is None:
            return False
        if str(any_row["space_id"]) != space_id:
            raise PermissionDeniedError("无权删除其他空间的图片附件")
        bound_note_id = str(any_row["note_id"] or "").strip()
        self._unlink_row_files(any_row)
        with self.db.transaction() as conn:
            conn.execute(
                "DELETE FROM media_attachments WHERE media_id = ? AND space_id = ?",
                (media_id, space_id),
            )
        if bound_note_id:
            from trace_memory.storage.repositories import NoteRepository

            note = NoteRepository(self.db).get(space_id, bound_note_id)
            if note is not None:
                self.refresh_note_fts_with_media(space_id, note)
        if self.sync_tracker is not None:
            self.sync_tracker.bump(space_id, reason="media_deleted")
        return True

    def purge_for_notes(self, space_id: str, note_ids: list[str] | tuple[str, ...]) -> int:
        """Physically delete disk images + thumbnails and metadata rows for forgotten notes."""
        clean = [str(n).strip() for n in note_ids if str(n).strip()]
        if not clean:
            return 0
        placeholders = ",".join("?" for _ in clean)
        rows = self.db.query_all(
            f"SELECT * FROM media_attachments WHERE space_id = ? AND note_id IN ({placeholders})",
            (space_id, *clean),
        )
        if not rows:
            return 0
        for r in rows:
            self._unlink_row_files(r)
        with self.db.transaction() as conn:
            conn.execute(
                f"DELETE FROM media_attachments WHERE space_id = ? AND note_id IN ({placeholders})",
                (space_id, *clean),
            )
        return len(rows)
