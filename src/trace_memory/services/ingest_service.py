"""Ingestion pipeline: RawNote persistence, extraction, preference-calibrated task scoring, and source revocation."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field, replace
from datetime import timedelta
import inspect
import re
import threading
import time
from typing import Any, Callable

from trace_memory.domain.exceptions import StructuredOutputError
from trace_memory.domain.memories import MemoryEvidence, MemoryItem, parse_iso_datetime
from trace_memory.domain.notes import (
    RawNote,
    beijing_now,
    extract_beijing_time_range,
    new_id,
    to_beijing,
    utc_now_iso,
)
from trace_memory.domain.proposals import (
    ChangeProposal,
    ChangeResult,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
    VersionConflictError,
)
from trace_memory.llm.protocol import ExtractedCandidate, LLMProvider
from trace_memory.services.change_service import ChangeService
from trace_memory.services.guard_service import InputGuardService, RateLimitAndQuotaService
from trace_memory.services.ingest_queue import IngestJobRepository, _is_transient_llm_error
from trace_memory.storage.repositories import (
    MemoryRepository,
    NoteRepository,
    RelationRepository,
    TraceRepository,
)
from trace_memory.storage.vector_store import (
    MEMORIES_COLLECTION,
    NOTES_COLLECTION,
    VectorLifecycleService,
)


@dataclass(frozen=True)
class IngestResult:
    note: RawNote
    extraction_status: str  # "completed" | "queued" | "cancelled_forgotten" | "degraded_error" | "idempotent_replay" | "filtered_noise"
    changes: tuple[ChangeResult, ...]
    error_message: str = ""
    trace_id: str = ""
    job_id: str = ""
    deferred_vector_sync: Callable[[], None] | None = field(
        default=None, repr=False, compare=False
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "note": self.note.to_dict(),
            "extraction_status": self.extraction_status,
            "filtered": self.extraction_status == "filtered_noise",
            "filter_reason": self.error_message if self.extraction_status == "filtered_noise" else "",
            "changes": [c.to_dict() for c in self.changes],
            "error_message": self.error_message,
            "trace_id": self.trace_id,
            "job_id": self.job_id,
        }


class IngestService:
    """Persists ground-truth `RawNote` first, then adjudicates structured memory candidates."""

    def __init__(
        self,
        note_repo: NoteRepository,
        memory_repo: MemoryRepository,
        relation_repo: RelationRepository,
        trace_repo: TraceRepository,
        vector_lifecycle: VectorLifecycleService,
        change_service: ChangeService,
        llm: LLMProvider,
        input_guard: InputGuardService | None = None,
        quota_service: RateLimitAndQuotaService | None = None,
        job_repo: IngestJobRepository | None = None,
        sync_tracker: Any = None,
        media_service: Any = None,
    ) -> None:
        self.note_repo = note_repo
        self.memory_repo = memory_repo
        self.relation_repo = relation_repo
        self.trace_repo = trace_repo
        self.vector_lifecycle = vector_lifecycle
        self.change_service = change_service
        self.llm = llm
        self.input_guard = input_guard
        self.quota_service = quota_service
        self.job_repo = job_repo
        self.sync_tracker = sync_tracker
        self.media_service = media_service
        # Recent raw-note context window for reference-aware extraction (overridden by the container)
        self.context_notes = 20
        self.context_chars = 2000
        self.context_note_chars = 150
        self.context_hours = 24.0
        # Set by the container: returns True while the async ingest worker/pipeline is running
        self.worker_running: Callable[[], bool] | None = None
        self.worker_notify: Callable[[], None] | None = None

    def _preflight_and_persist_note(
        self,
        space_id: str,
        content: str,
        source_type: str = "cli",
        actor_id: str = "user",
        event_time: str = "",
        idempotency_key: str = "",
        media_ids: list[str] | tuple[str, ...] = (),
        t0: float | None = None,
    ) -> tuple[RawNote | None, IngestResult | None]:
        """Run idempotency check, zero-cost InputGuard filter, and durable RawNote + FTS5 save."""
        start = t0 if t0 is not None else time.perf_counter()
        clean_media_ids = tuple(str(m).strip() for m in (media_ids or ()) if str(m).strip())
        if clean_media_ids and not content.strip():
            content = "[图片记录]"

        # 1. Entry idempotency check (C08)
        if idempotency_key:
            cached = self.trace_repo.get_idempotent_result(space_id, f"ingest:{idempotency_key}")
            if cached is not None:
                existing_note = self.note_repo.get(
                    space_id, cached["note"]["note_id"], include_forgotten=True
                )
                if existing_note is not None:
                    return None, IngestResult(
                        note=existing_note,
                        extraction_status="idempotent_replay",
                        changes=(),
                        trace_id=cached.get("trace_id", ""),
                        job_id=str(cached.get("job_id", "")),
                    )

        # 1b. Zero-cost local InputGuard filter (skip noise filter when explicit image attachments are present!)
        if self.input_guard is not None and not clean_media_ids:
            guard_res = self.input_guard.inspect_note(content)
            if not guard_res.allowed:
                if guard_res.category in ("empty", "too_long"):
                    raise ValidationError(guard_res.reason)
                if self.quota_service is not None:
                    self.quota_service.record_usage(f"space:{space_id}", blocked_noise=1)
                    self.quota_service.log_security_event(
                        action="ingest_noise_filtered",
                        status=guard_res.category or "filtered",
                        actor_user_id=actor_id,
                        space_id=space_id,
                        detail={"reason": guard_res.reason, "preview": content.strip()[:40]},
                    )
                duration = (time.perf_counter() - start) * 1000.0
                trace_id = self.trace_repo.record_trace(
                    space_id=space_id,
                    operation="ingest_filtered_noise",
                    input_summary=content.strip()[:60],
                    details={"category": guard_res.category, "reason": guard_res.reason},
                    llm_calls=0,
                    tokens_used=0,
                    duration_ms=duration,
                )
                transient_note = RawNote(
                    note_id="filtered_noise",
                    space_id=space_id,
                    content=content.strip(),
                    source_type=source_type,
                    actor_id=actor_id,
                    revision=1,
                    event_time=event_time or utc_now_iso(),
                )
                return None, IngestResult(
                    note=transient_note,
                    extraction_status="filtered_noise",
                    changes=(),
                    error_message=guard_res.reason,
                    trace_id=trace_id,
                )

        # 2. Persist ground-truth RawNote + FTS5 BEFORE any fallible LLM work (C10)
        note = RawNote(
            note_id=new_id("note"),
            space_id=space_id,
            content=content.strip(),
            source_type=source_type,
            actor_id=actor_id,
            revision=1,
            event_time=event_time or utc_now_iso(),
        )
        self.note_repo.save(note)
        if clean_media_ids and self.media_service is not None:
            self.media_service.bind_to_note(space_id, note.note_id, clean_media_ids)
        return note, None

    def enqueue_note(
        self,
        space_id: str,
        content: str,
        source_type: str = "web",
        actor_id: str = "user",
        event_time: str = "",
        idempotency_key: str = "",
        media_ids: list[str] | tuple[str, ...] = (),
    ) -> IngestResult:
        """Fast-Ack (<5ms) async ingest: persist RawNote + FTS5 + durable ingest_job and return immediately."""
        if self.job_repo is None:
            return self.record_note(
                space_id=space_id,
                content=content,
                source_type=source_type,
                actor_id=actor_id,
                event_time=event_time,
                idempotency_key=idempotency_key,
                media_ids=media_ids,
            )

        t0 = time.perf_counter()
        note, short_circuit = self._preflight_and_persist_note(
            space_id=space_id,
            content=content,
            source_type=source_type,
            actor_id=actor_id,
            event_time=event_time,
            idempotency_key=idempotency_key,
            media_ids=media_ids,
            t0=t0,
        )
        if short_circuit is not None or note is None:
            assert short_circuit is not None
            return short_circuit

        # Reserve note quota slot immediately so async bursts cannot bypass daily note limits
        if self.quota_service is not None:
            self.quota_service.record_usage(f"space:{space_id}", notes=1)

        job = self.job_repo.enqueue(
            space_id=space_id,
            note_id=note.note_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            content_hint=note.content,
        )
        if self.sync_tracker is not None:
            self.sync_tracker.bump(space_id, reason="note_queued")
        duration = (time.perf_counter() - t0) * 1000.0
        trace_id = self.trace_repo.record_trace(
            space_id=space_id,
            operation="ingest_queued",
            input_summary=note.content[:80],
            details={"note_id": note.note_id, "job_id": job["job_id"]},
            llm_calls=0,
            tokens_used=0,
            duration_ms=duration,
        )
        out = IngestResult(
            note=note,
            extraction_status="queued",
            changes=(),
            trace_id=trace_id,
            job_id=job["job_id"],
        )
        if idempotency_key:
            self.trace_repo.save_idempotent_result(
                space_id, f"ingest:{idempotency_key}", note.note_id, out.to_dict()
            )
        return out

    # ------------------------------------------------------------------
    # Two-stage ingest: `extract_stage` (parallel, read-mostly) + `commit_artifact` (ordered writes)
    # ------------------------------------------------------------------

    def _build_context_window(
        self, note: RawNote, excludes: tuple[str, ...] = ()
    ) -> list[dict[str, Any]]:
        """Recent earlier raw notes of the same space (incl. ones still being extracted), oldest first."""
        if self.context_notes <= 0 or self.context_chars <= 0:
            return []
        rows = self.note_repo.db.query_all(
            """
            SELECT note_id FROM raw_notes
            WHERE space_id = ? AND is_forgotten = 0 AND extract_memory = 1 AND note_kind = 'note'
              AND rowid < (SELECT rowid FROM raw_notes WHERE space_id = ? AND note_id = ?)
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (note.space_id, note.space_id, note.note_id, int(self.context_notes) + len(excludes)),
        )
        excluded = set(excludes)
        cutoff = beijing_now() - timedelta(hours=float(self.context_hours)) if self.context_hours > 0 else None
        picked: list[dict[str, Any]] = []
        used = 0
        for r in rows:
            nid = str(r["note_id"])
            if nid in excluded or len(picked) >= self.context_notes:
                continue
            n_obj = self.note_repo.get(note.space_id, nid)
            if n_obj is None or n_obj.is_forgotten:
                continue
            if cutoff is not None:
                created = parse_iso_datetime(n_obj.created_at)
                if created is not None and to_beijing(created) < cutoff:
                    break
            text = n_obj.visible_content().strip().replace("\n", " ")
            if not text:
                continue
            text = text[: self.context_note_chars]
            if used + len(text) > self.context_chars and picked:
                break
            used += len(text)
            status_text = "已处理，无卡片"
            pending = False
            if self.job_repo is not None:
                jrow = self.note_repo.db.query_one(
                    "SELECT status FROM ingest_jobs WHERE space_id = ? AND note_id = ? ORDER BY rowid DESC LIMIT 1",
                    (note.space_id, nid),
                )
                pending = jrow is not None and str(jrow["status"]) in ("queued", "processing", "extracted")
            card_ids: list[str] = []
            if pending:
                status_text = "处理中"
            else:
                cards = self.memory_repo.find_memories_by_evidence_note(note.space_id, nid)[:3]
                card_ids = [c.memory_id for c in cards]
                if cards:
                    status_text = "已生成: " + "；".join(
                        f"{c.title}(key={c.canonical_key})" for c in cards
                    )
            picked.append(
                {
                    "note_id": nid,
                    "revision": n_obj.revision,
                    "content": text,
                    "status": status_text,
                    "card_ids": card_ids,
                }
            )
        picked.reverse()
        for i, item in enumerate(picked, start=1):
            item["label"] = f"N{i}"
        return picked

    def extract_stage(
        self,
        space_id: str,
        note_id: str,
        *,
        llm_semaphore: threading.Semaphore | None = None,
        raise_on_transient_llm_error: bool = False,
        context_excludes: tuple[str, ...] | list[str] = (),
    ) -> dict[str, Any]:
        """Stage 1: VLM enrichment + LLM extraction. Never writes memories; safe to run in parallel.

        Returns a JSON-serializable artifact consumed by `commit_artifact` (stage 2).
        Transient LLM errors are re-raised when `raise_on_transient_llm_error` (caller retries).
        """
        t0 = time.perf_counter()
        note = self.note_repo.get(space_id, note_id, include_forgotten=True)
        if note is None or note.is_forgotten:
            return {"kind": "forgotten", "t0_ms": 0.0}
        start_revision = note.revision

        visual_text = ""
        vlm_calls = 0
        vlm_tokens = 0
        if self.media_service is not None:
            visual_text, vlm_calls, vlm_tokens = self.media_service.analyze_pending_for_note(
                space_id, note_id
            )
            if visual_text:
                self.media_service.refresh_note_fts_with_media(space_id, note)

        base_art: dict[str, Any] = {
            "start_revision": start_revision,
            "visual_text": visual_text,
            "vlm_calls": vlm_calls,
            "vlm_tokens": vlm_tokens,
            "window": [],
            "stage1_ms": 0.0,
        }
        if not note.extract_memory:
            base_art["kind"] = "pure"
            base_art["stage1_ms"] = (time.perf_counter() - t0) * 1000.0
            return base_art

        extract_input = self._extract_input(note, visual_text)
        window = self._build_context_window(note, tuple(context_excludes or ()))
        base_art["window"] = [
            {"label": w["label"], "note_id": w["note_id"], "revision": w["revision"]} for w in window
        ]
        existing_active = self.memory_repo.list_memories(space_id=space_id, status="active", limit=40)
        linked = {cid for w in window for cid in w.get("card_ids", ())}
        if linked:
            existing_active.sort(key=lambda m: 0 if m.memory_id in linked else 1)
        try:
            if llm_semaphore is not None:
                with llm_semaphore:
                    batch = self._call_extract(extract_input, note.event_time, existing_active, window)
            else:
                batch = self._call_extract(extract_input, note.event_time, existing_active, window)
        except Exception as exc:
            will_retry = raise_on_transient_llm_error and _is_transient_llm_error(exc)
            if isinstance(exc, StructuredOutputError):
                if self.quota_service is not None:
                    self.quota_service.record_usage(
                        f"space:{space_id}", llm_calls=1, tokens_used=exc.tokens_used
                    )
                self.trace_repo.record_trace(
                    space_id=space_id,
                    operation="ingest_format_error",
                    input_summary="",
                    details={"note_id": note_id, "reason": exc.reason, "will_retry": will_retry},
                    llm_calls=1,
                    tokens_used=exc.tokens_used,
                    duration_ms=(time.perf_counter() - t0) * 1000.0,
                )
            if will_retry:
                if self.quota_service is not None and (vlm_calls or vlm_tokens):
                    self.quota_service.record_usage(
                        f"space:{space_id}", llm_calls=vlm_calls, tokens_used=vlm_tokens,
                    )
                raise
            base_art["kind"] = "error"
            base_art["error"] = str(exc)
            base_art["stage1_ms"] = (time.perf_counter() - t0) * 1000.0
            return base_art

        labels = {w["label"] for w in window}
        cands = []
        for c in batch.candidates:
            if c.refers_to_label and c.refers_to_label not in labels:
                c = replace(c, refers_to_label="")
            cands.append(c.to_dict())
        base_art.update(
            {
                "kind": "ok",
                "candidates": cands,
                "llm_calls": batch.llm_calls,
                "tokens_used": batch.tokens_used,
                "stage1_ms": (time.perf_counter() - t0) * 1000.0,
            }
        )
        return base_art

    @staticmethod
    def _extract_input(note: RawNote, visual_text: str) -> str:
        extract_input = f"{note.content}\n{visual_text}".strip() if visual_text else note.content
        if note.title and note.title not in extract_input:
            extract_input = f"{note.title}\n{extract_input}".strip()
        return extract_input

    @staticmethod
    def _vec_note(fresh: RawNote, content: str) -> RawNote:
        return RawNote(
            note_id=fresh.note_id,
            space_id=fresh.space_id,
            content=content,
            source_type=fresh.source_type,
            actor_id=fresh.actor_id,
            revision=fresh.revision,
            event_time=fresh.event_time,
            created_at=fresh.created_at,
            updated_at=fresh.updated_at,
            note_kind=fresh.note_kind,
            title=fresh.title,
            pinned=fresh.pinned,
            tags=fresh.tags,
            mood=fresh.mood,
            journal_date=fresh.journal_date,
            extract_memory=fresh.extract_memory,
        )

    def stale_reference_in(self, space_id: str, art: dict[str, Any]) -> bool:
        """True when a window note the candidates refer to was forgotten or edited after extraction."""
        win = {w["label"]: w for w in art.get("window", ())}
        for c in art.get("candidates", ()):
            label = c.get("refers_to_label") or ""
            if not label or label not in win:
                continue
            ref = self.note_repo.get(space_id, win[label]["note_id"], include_forgotten=True)
            if ref is None or ref.is_forgotten or ref.revision != int(win[label]["revision"]):
                return True
        return False

    def commit_artifact(
        self,
        space_id: str,
        note_id: str,
        art: dict[str, Any],
        *,
        idempotency_key: str = "",
        count_note_in_quota: bool = True,
        defer_embeddings: bool = False,
        defer_error_embedding: bool = False,
        t0: float | None = None,
    ) -> IngestResult:
        """Stage 2: re-validate against the current note revision, adjudicate candidates and persist.

        Must run in strict per-space sequence order (v1: commit barrier; v2: CommitPump).
        """
        t_start = t0 if t0 is not None else time.perf_counter()
        stage1_ms = float(art.get("stage1_ms", 0.0) or 0.0)

        def _elapsed_ms() -> float:
            return (time.perf_counter() - t_start) * 1000.0 + (stage1_ms if t0 is None else 0.0)

        kind = art.get("kind")
        if kind == "forgotten":
            note = self.note_repo.get(space_id, note_id, include_forgotten=True)
            fallback = note or RawNote(
                note_id=note_id, space_id=space_id, content="[FORGOTTEN]", is_forgotten=True
            )
            return IngestResult(
                note=fallback,
                extraction_status="cancelled_forgotten",
                changes=(),
                error_message="Source note already forgotten before extraction",
            )

        start_revision = int(art.get("start_revision", 0))
        visual_text = str(art.get("visual_text", "") or "")
        vlm_calls = int(art.get("vlm_calls", 0) or 0)
        vlm_tokens = int(art.get("vlm_tokens", 0) or 0)
        note = self.note_repo.get(space_id, note_id, include_forgotten=True)
        if note is None:
            note = RawNote(note_id=note_id, space_id=space_id, content="[FORGOTTEN]", is_forgotten=True)

        # Pure Memo / Journal mode (extract_memory == False): index FTS5 + Vector + VLM OCR without task/fact extraction
        if kind == "pure":
            embed_calls = 0
            vector_error = ""
            deferred_pure_fn: Callable[[], None] | None = None
            fresh_pure = self.note_repo.get(space_id, note_id, include_forgotten=True)
            if fresh_pure is not None and not fresh_pure.is_forgotten and fresh_pure.revision == start_revision:
                vec_note = (
                    self._vec_note(fresh_pure, self._extract_input(fresh_pure, visual_text))
                    if (visual_text or fresh_pure.title)
                    else fresh_pure
                )
                if defer_embeddings:
                    def _run_pure_vec(target_note: RawNote = vec_note) -> None:
                        def _pure_guard(n_obj: RawNote) -> bool:
                            cur = self.note_repo.get(space_id, n_obj.note_id, include_forgotten=True)
                            if cur is None or cur.is_forgotten:
                                self.vector_lifecycle.vector_store.delete(NOTES_COLLECTION, n_obj.note_id)
                                return False
                            return cur.revision == start_revision

                        try:
                            cnt = self.vector_lifecycle.sync_batch(notes=(target_note,), note_guard=_pure_guard)
                            if cnt and self.quota_service is not None:
                                self.quota_service.record_usage(f"space:{space_id}", embed_calls=cnt)
                        except Exception:
                            pass

                    deferred_pure_fn = _run_pure_vec
                else:
                    try:
                        self.vector_lifecycle.sync_note(vec_note)
                        embed_calls += 1
                    except Exception as exc:
                        vector_error = f"Note embedding degraded: {exc}"
            if self.quota_service is not None:
                self.quota_service.record_usage(
                    f"space:{space_id}",
                    notes=1 if count_note_in_quota else 0,
                    llm_calls=vlm_calls,
                    embed_calls=embed_calls,
                    tokens_used=vlm_tokens,
                )
            trace_id = self.trace_repo.record_trace(
                space_id=space_id,
                operation=f"ingest_{note.note_kind}_pure",
                input_summary=(note.title or note.content)[:60],
                details={"note_id": note.note_id, "note_kind": note.note_kind, "vector_error": vector_error},
                llm_calls=vlm_calls,
                tokens_used=vlm_tokens,
                duration_ms=_elapsed_ms(),
            )
            return IngestResult(
                note=fresh_pure or note,
                extraction_status="completed" if not vector_error else "degraded_error",
                changes=(),
                error_message=vector_error,
                trace_id=trace_id,
                deferred_vector_sync=deferred_pure_fn,
            )

        vector_error = ""
        embed_calls = 0
        if kind == "error":
            err_text = str(art.get("error", "") or "extraction failed")
            # C10/C07: Check if note was forgotten mid-flight BEFORE writing trace_runs!
            fresh_err = self.note_repo.get(space_id, note_id, include_forgotten=True)
            if fresh_err is None or fresh_err.is_forgotten or fresh_err.revision != start_revision:
                if (fresh_err is None or fresh_err.is_forgotten) and self.media_service is not None:
                    self.media_service.purge_for_notes(space_id, [note_id])
                if self.quota_service is not None:
                    self.quota_service.record_usage(
                        f"space:{space_id}",
                        notes=1 if count_note_in_quota else 0,
                        llm_calls=vlm_calls,
                        tokens_used=vlm_tokens,
                    )
                trace_id = self.trace_repo.record_trace(
                    space_id=space_id,
                    operation="ingest_aborted_forgotten_or_stale",
                    input_summary="[REDACTED]",
                    details={"note_id": note_id, "error": err_text},
                    duration_ms=_elapsed_ms(),
                )
                return IngestResult(
                    note=fresh_err or note,
                    extraction_status="cancelled_forgotten",
                    changes=(),
                    error_message="Source note was forgotten or modified during extraction",
                    trace_id=trace_id,
                )
            deferred_err_fn: Callable[[], None] | None = None
            if defer_error_embedding:
                def _run_err_vec(target_note: RawNote = fresh_err) -> None:
                    try:
                        self.vector_lifecycle.sync_note(target_note)
                        if self.quota_service is not None:
                            self.quota_service.record_usage(f"space:{space_id}", embed_calls=1)
                    except Exception:
                        pass

                deferred_err_fn = _run_err_vec
            else:
                try:
                    self.vector_lifecycle.sync_note(fresh_err)
                    embed_calls += 1
                except Exception as vec_exc:
                    vector_error = f"Note embedding degraded: {vec_exc}"
            if self.quota_service is not None:
                self.quota_service.record_usage(
                    f"space:{space_id}",
                    notes=1 if count_note_in_quota else 0,
                    llm_calls=vlm_calls,
                    embed_calls=embed_calls,
                    tokens_used=vlm_tokens,
                )
            trace_id = self.trace_repo.record_trace(
                space_id=space_id,
                operation="ingest_degraded",
                input_summary=fresh_err.visible_content(),
                details={"note_id": note.note_id, "error": err_text, "vector_error": vector_error},
                duration_ms=_elapsed_ms(),
            )
            return IngestResult(
                note=fresh_err,
                extraction_status="degraded_error",
                changes=(),
                error_message=err_text,
                trace_id=trace_id,
                deferred_vector_sync=deferred_err_fn,
            )

        # kind == "ok"
        candidates = [ExtractedCandidate.from_dict(c) for c in art.get("candidates", ())]
        llm_calls = int(art.get("llm_calls", 0) or 0)
        tokens_used = int(art.get("tokens_used", 0) or 0)
        window_map = {
            str(w["label"]): (str(w["note_id"]), int(w["revision"])) for w in art.get("window", ())
        }

        # Double-Check Race Guard: if user forgot or edited the RawNote while LLM was running, discard result!
        fresh_note = self.note_repo.get(space_id, note_id, include_forgotten=True)
        if fresh_note is None or fresh_note.is_forgotten or fresh_note.revision != start_revision:
            if (fresh_note is None or fresh_note.is_forgotten) and self.media_service is not None:
                self.media_service.purge_for_notes(space_id, [note_id])
            if self.quota_service is not None:
                self.quota_service.record_usage(
                    f"space:{space_id}",
                    notes=1 if count_note_in_quota else 0,
                    llm_calls=llm_calls,
                    tokens_used=tokens_used,
                )
            trace_id = self.trace_repo.record_trace(
                space_id=space_id,
                operation="ingest_aborted_forgotten_or_stale",
                input_summary="[REDACTED]",
                details={
                    "note_id": note_id,
                    "start_revision": start_revision,
                    "current_forgotten": bool(fresh_note is None or fresh_note.is_forgotten),
                    "current_revision": fresh_note.revision if fresh_note else -1,
                },
                llm_calls=llm_calls,
                tokens_used=tokens_used,
                duration_ms=_elapsed_ms(),
            )
            return IngestResult(
                note=fresh_note or note,
                extraction_status="cancelled_forgotten",
                changes=(),
                error_message="Source note was forgotten or modified during extraction; candidates discarded",
                trace_id=trace_id,
            )

        extract_input = self._extract_input(fresh_note, visual_text)
        # Selective Note Vectorization: skip costly Embedding API if LLM confirmed 0 extractable memories (unless image visual text or memo/journal)!
        note_vector_skipped = len(candidates) == 0 and not visual_text and fresh_note.note_kind == "note"
        note_for_vec: RawNote | None = None
        if not note_vector_skipped:
            note_for_vec = self._vec_note(fresh_note, extract_input) if visual_text else fresh_note

        # Calibrate candidates against existing memories & user preferences, then commit to SQLite BEFORE vector embedding
        active_prefs = self.memory_repo.list_memories(
            space_id=space_id, memory_type="preference", status="active", limit=30
        )
        results: list[ChangeResult] = []
        memories_for_vec: list[MemoryItem] = []
        aborted_mid_commit = False
        for cand in candidates:
            # Silent extraction for memos/journals distills ONLY long-term preferences/facts/experiences, never transient tasks
            if fresh_note.note_kind in ("memo", "journal") and cand.memory_type == "task":
                continue
            # Final per-candidate check against mid-commit forget
            still_alive = self.note_repo.get(space_id, note_id, include_forgotten=True)
            if still_alive is None or still_alive.is_forgotten or still_alive.revision != start_revision:
                aborted_mid_commit = True
                break
            res = self._adjudicate_with_reference(
                fresh_note, cand, active_prefs, window_map, defer_active_vector_sync=True
            )
            results.append(res)
            if (
                res.memory is not None
                and res.memory.status == "active"
                and res.status in ("created", "updated", "applied", "skipped_older_event")
            ):
                memories_for_vec.append(res.memory)

        if aborted_mid_commit:
            if self.quota_service is not None:
                self.quota_service.record_usage(
                    f"space:{space_id}",
                    notes=1 if count_note_in_quota else 0,
                    llm_calls=llm_calls + vlm_calls,
                    tokens_used=tokens_used + vlm_tokens,
                )
            if self.media_service is not None:
                self.media_service.purge_for_notes(space_id, [note_id])
            trace_id = self.trace_repo.record_trace(
                space_id=space_id,
                operation="ingest_aborted_forgotten_or_stale",
                input_summary="[REDACTED]",
                details={"note_id": fresh_note.note_id, "aborted_mid_commit": True},
                llm_calls=llm_calls,
                tokens_used=tokens_used,
                duration_ms=_elapsed_ms(),
            )
            return IngestResult(
                note=fresh_note,
                extraction_status="cancelled_forgotten",
                changes=tuple(results),
                error_message="Source note was forgotten or modified during candidate commit",
                trace_id=trace_id,
            )

        notes_for_vec: tuple[RawNote, ...] = (note_for_vec,) if note_for_vec is not None else ()
        mems_for_vec_tuple: tuple[MemoryItem, ...] = tuple(memories_for_vec)
        deferred_vec_fn: Callable[[], None] | None = None

        def _note_guard(n_obj: RawNote) -> bool:
            cur = self.note_repo.get(space_id, n_obj.note_id, include_forgotten=True)
            if cur is None or cur.is_forgotten:
                self.vector_lifecycle.vector_store.delete(NOTES_COLLECTION, n_obj.note_id)
                return False
            return cur.revision == start_revision

        def _mem_guard(m_obj: MemoryItem) -> bool:
            cur = self.memory_repo.get(space_id, m_obj.memory_id, include_forgotten=True)
            if cur is None or cur.status != "active":
                self.vector_lifecycle.vector_store.delete(MEMORIES_COLLECTION, m_obj.memory_id)
                return False
            return True

        if notes_for_vec or mems_for_vec_tuple:
            if defer_embeddings:
                def _run_deferred_vectors() -> None:
                    try:
                        cnt = self.vector_lifecycle.sync_batch(
                            notes=notes_for_vec,
                            memories=mems_for_vec_tuple,
                            note_guard=_note_guard,
                            memory_guard=_mem_guard,
                        )
                        if cnt and self.quota_service is not None:
                            self.quota_service.record_usage(f"space:{space_id}", embed_calls=cnt)
                    except Exception:
                        pass

                deferred_vec_fn = _run_deferred_vectors
            else:
                try:
                    embed_calls += self.vector_lifecycle.sync_batch(
                        notes=notes_for_vec,
                        memories=mems_for_vec_tuple,
                        note_guard=_note_guard,
                        memory_guard=_mem_guard,
                    )
                except Exception as exc:
                    # C10: Record embedding degradation honestly without losing the RawNote or committed memories
                    vector_error = f"Note embedding degraded: {exc}"

        if self.quota_service is not None:
            self.quota_service.record_usage(
                f"space:{space_id}",
                notes=1 if count_note_in_quota else 0,
                llm_calls=llm_calls + vlm_calls,
                embed_calls=embed_calls,
                tokens_used=tokens_used + vlm_tokens,
            )

        status = "degraded_error" if vector_error else "completed"
        trace_id = self.trace_repo.record_trace(
            space_id=space_id,
            operation="ingest",
            input_summary=fresh_note.content,
            details={
                "note_id": fresh_note.note_id,
                "candidate_count": len(candidates),
                "applied_statuses": [r.status for r in results],
                "note_vector_skipped": note_vector_skipped,
                "vector_deferred": bool(deferred_vec_fn is not None),
                "vector_error": vector_error,
                "context_window": len(window_map),
                "references": [c.refers_to_label for c in candidates if c.refers_to_label],
                "stage1_ms": round(stage1_ms, 1),
            },
            llm_calls=llm_calls,
            tokens_used=tokens_used,
            duration_ms=_elapsed_ms(),
        )
        out = IngestResult(
            note=fresh_note,
            extraction_status=status,
            changes=tuple(results),
            error_message=vector_error,
            trace_id=trace_id,
            deferred_vector_sync=deferred_vec_fn,
        )
        if idempotency_key:
            self.trace_repo.save_idempotent_result(
                space_id, f"ingest:{idempotency_key}", fresh_note.note_id, out.to_dict()
            )
        return out

    def process_persisted_note(
        self,
        space_id: str,
        note_id: str,
        idempotency_key: str = "",
        count_note_in_quota: bool = True,
        llm_semaphore: threading.Semaphore | None = None,
        raise_on_transient_llm_error: bool = False,
        defer_embeddings: bool = False,
        commit_barrier: Callable[[], AbstractContextManager[None]] | None = None,
        context_excludes: tuple[str, ...] | list[str] = (),
    ) -> IngestResult:
        """Run LLM extraction concurrently when eligible, then commit to SQLite in strict sequence order."""
        eff_barrier: Callable[[], AbstractContextManager[None]] = commit_barrier or nullcontext
        t0 = time.perf_counter()
        art = self.extract_stage(
            space_id,
            note_id,
            llm_semaphore=llm_semaphore,
            raise_on_transient_llm_error=raise_on_transient_llm_error,
            context_excludes=context_excludes,
        )
        # Ordered Sequence Commit Barrier: preserve strict FIFO causal commit order across concurrent extractions!
        with eff_barrier():
            if art.get("kind") == "ok" and self.stale_reference_in(space_id, art):
                # A referenced window note changed mid-flight: re-extract once against the current context
                art = self.extract_stage(
                    space_id,
                    note_id,
                    llm_semaphore=llm_semaphore,
                    raise_on_transient_llm_error=False,
                    context_excludes=context_excludes,
                )
            art = self.prepare_references(space_id, note_id, art)
            return self.commit_artifact(
                space_id,
                note_id,
                art,
                idempotency_key=idempotency_key,
                count_note_in_quota=count_note_in_quota,
                defer_embeddings=defer_embeddings,
                t0=t0,
            )

    def record_note(
        self,
        space_id: str,
        content: str,
        source_type: str = "cli",
        actor_id: str = "user",
        event_time: str = "",
        idempotency_key: str = "",
        media_ids: list[str] | tuple[str, ...] = (),
    ) -> IngestResult:
        t0 = time.perf_counter()
        note, short_circuit = self._preflight_and_persist_note(
            space_id=space_id,
            content=content,
            source_type=source_type,
            actor_id=actor_id,
            event_time=event_time,
            idempotency_key=idempotency_key,
            media_ids=media_ids,
            t0=t0,
        )
        if short_circuit is not None or note is None:
            assert short_circuit is not None
            return short_circuit
        return self.process_persisted_note(
            space_id=space_id,
            note_id=note.note_id,
            idempotency_key=idempotency_key,
            count_note_in_quota=True,
        )

    def _call_extract(
        self,
        text: str,
        event_time: str,
        existing_memories: list[MemoryItem],
        recent_notes: list[dict[str, Any]] | None = None,
    ):
        sig = inspect.signature(self.llm.extract_candidates)
        params = sig.parameters
        if "recent_notes" in params or any(p.kind == p.VAR_KEYWORD for p in params.values()):
            return self.llm.extract_candidates(
                text, event_time, existing_memories, recent_notes=list(recent_notes or ())
            )
        if len(params) >= 3:
            return self.llm.extract_candidates(text, event_time, existing_memories)
        return self.llm.extract_candidates(text, event_time)  # type: ignore[call-arg]

    @staticmethod
    def _extract_round_marker(text: str) -> str:
        m = re.search(r"第([一二三四五六七八九十\d]+)(?:轮|期|版|次)", text)
        if not m:
            return ""
        mapping = {
            "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
            "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
        }
        raw = m.group(1)
        return mapping.get(raw, raw)

    @staticmethod
    def _title_dice_similarity(a: str, b: str) -> float:
        ca = re.sub(r"[^\w\u4e00-\u9fff]", "", a.lower())
        cb = re.sub(r"[^\w\u4e00-\u9fff]", "", b.lower())
        if not ca or not cb:
            return 0.0
        if ca in cb or cb in ca:
            return 0.9
        grams_a = {ca[i : i + 2] for i in range(len(ca) - 1)} if len(ca) >= 2 else {ca}
        grams_b = {cb[i : i + 2] for i in range(len(cb) - 1)} if len(cb) >= 2 else {cb}
        inter = len(grams_a & grams_b)
        return (2.0 * inter) / (len(grams_a) + len(grams_b))

    def _find_reconcilable_task(
        self, space_id: str, cand: ExtractedCandidate, cand_assignee: str, *, include_done: bool = False
    ) -> MemoryItem | None:
        """Match a task candidate against existing active tasks while preserving C05 assignee/round boundaries."""
        cand_round = self._extract_round_marker(cand.title + " " + cand.canonical_key)
        best_item: MemoryItem | None = None
        best_sim = 0.0

        for t_item in self.memory_repo.list_memories(
            space_id=space_id, memory_type="task", status="active", limit=100
        ):
            if not include_done and t_item.payload.get("task_status") == "done":
                continue
            item_assignee = str(t_item.payload.get("assignee", "self") or "self")
            # C05 Guard 1: Never merge tasks belonging to different assignees
            if item_assignee != cand_assignee:
                continue
            # C05 Guard 2: Never merge tasks from explicitly different rounds/batches
            item_round = self._extract_round_marker(t_item.title + " " + t_item.canonical_key)
            if cand_round != item_round:
                continue

            sim = self._title_dice_similarity(cand.title, t_item.title)
            if sim > best_sim:
                best_sim = sim
                best_item = t_item

        # Require >= 0.55 bigram Dice overlap (e.g. "修复线上订单支付超时告急" vs "...告警" is 0.89!)
        return best_item if best_sim >= 0.55 else None

    @staticmethod
    def _title_coverage(title: str, text: str) -> float:
        """Share of `title`'s character bigrams that also occur in `text` (0..1)."""
        ca = re.sub(r"[^\w一-鿿]", "", (title or "").lower())
        cb = re.sub(r"[^\w一-鿿]", "", (text or "").lower())
        if not ca or not cb:
            return 0.0
        grams_a = {ca[i : i + 2] for i in range(len(ca) - 1)} if len(ca) >= 2 else {ca}
        grams_b = {cb[i : i + 2] for i in range(len(cb) - 1)} if len(cb) >= 2 else {cb}
        return len(grams_a & grams_b) / max(1, len(grams_a))

    # Thresholds for picking one card among several produced by the referenced note
    REF_PICK_MIN_SCORE = 0.5
    REF_PICK_MIN_GAP = 0.2

    def _reference_cards(
        self, space_id: str, cand: ExtractedCandidate, window_map: dict[str, tuple[str, int]]
    ) -> tuple[list[MemoryItem], MemoryEvidence | None]:
        """The referenced note's live cards of the candidate's type (+ that note as a `base` evidence)."""
        ref = window_map.get(cand.refers_to_label or "")
        if ref is None:
            return [], None
        ref_note_id, _ref_rev = ref
        ref_note = self.note_repo.get(space_id, ref_note_id)
        if ref_note is None or ref_note.is_forgotten:
            return [], None
        base_ev = MemoryEvidence(
            note_id=ref_note_id,
            note_revision=ref_note.revision,
            quote=ref_note.visible_content()[:160],
            role="base",
        )
        mems = self.memory_repo.find_memories_by_evidence_note(
            space_id, ref_note_id, memory_type=cand.memory_type
        )
        if cand.memory_type == "task" and cand.relation == "update":
            open_tasks = [m for m in mems if m.payload.get("task_status") != "done"]
            mems = open_tasks or mems
        return mems, base_ev

    def _pick_reference_card(
        self, cand: ExtractedCandidate, mems: list[MemoryItem]
    ) -> MemoryItem | None:
        """Deterministic tiers: single card -> exact canonical_key -> clearly best title. None = ambiguous."""
        if len(mems) == 1:
            return mems[0]
        if cand.canonical_key:
            keyed = [m for m in mems if m.canonical_key == cand.canonical_key]
            if len(keyed) == 1:
                return keyed[0]
        scored = sorted(
            ((self._title_dice_similarity(cand.title, m.title), m) for m in mems),
            key=lambda x: x[0],
            reverse=True,
        )
        if not scored:
            return None
        best = scored[0][0]
        second = scored[1][0] if len(scored) > 1 else 0.0
        if best >= self.REF_PICK_MIN_SCORE and best - second >= self.REF_PICK_MIN_GAP:
            return scored[0][1]
        return None

    def prepare_references(self, space_id: str, note_id: str, art: dict[str, Any]) -> dict[str, Any]:
        """Before the commit transaction: settle ambiguous multi-card references, asking the model if needed.

        Runs at the head of the space's commit order (earlier notes' cards exist) but outside the SQLite
        write transaction, so a slow model call never holds the database lock.
        """
        if art.get("kind") != "ok":
            return art
        window_map = {
            str(w["label"]): (str(w["note_id"]), int(w["revision"])) for w in art.get("window", ())
        }
        note = None
        for c in art.get("candidates", ()):
            if not c.get("refers_to_label") or c.get("resolved_card_id") or c.get("reference_ambiguous"):
                continue
            cand = ExtractedCandidate.from_dict(c)
            mems, _ = self._reference_cards(space_id, cand, window_map)
            if len(mems) <= 1 or self._pick_reference_card(cand, mems) is not None:
                continue
            chooser = getattr(self.llm, "choose_reference_card", None)
            chosen = ""
            if callable(chooser):
                if note is None:
                    note = self.note_repo.get(space_id, note_id)
                try:
                    idx, calls, tokens = chooser(
                        note.visible_content() if note is not None else "",
                        cand.title,
                        [{"title": m.title, "summary": m.summary, "payload": m.payload} for m in mems],
                    )
                    if self.quota_service is not None and (calls or tokens):
                        self.quota_service.record_usage(
                            f"space:{space_id}", llm_calls=calls, tokens_used=tokens
                        )
                    if 0 <= int(idx) < len(mems):
                        chosen = mems[int(idx)].memory_id
                except Exception:
                    chosen = ""
            if chosen:
                c["resolved_card_id"] = chosen
            else:
                c["reference_ambiguous"] = True
        return art

    def _resolve_reference_target(
        self, note: RawNote, cand: ExtractedCandidate, window_map: dict[str, tuple[str, int]]
    ) -> tuple[MemoryItem | None, MemoryEvidence | None, bool]:
        """Map `refers_to_label` -> the referenced note -> the card it produced.

        Returns (target, base_evidence, ambiguous). `ambiguous` = several cards and none could be chosen.
        """
        mems, base_ev = self._reference_cards(note.space_id, cand, window_map)
        if not mems:
            return None, base_ev, False
        if cand.resolved_card_id:
            for m in mems:
                if m.memory_id == cand.resolved_card_id:
                    return m, base_ev, False
        picked = self._pick_reference_card(cand, mems)
        if picked is not None:
            return picked, base_ev, False
        return None, base_ev, True

    def _adjudicate_with_reference(
        self,
        note: RawNote,
        cand: ExtractedCandidate,
        active_prefs: list[MemoryItem],
        window_map: dict[str, tuple[str, int]],
        *,
        defer_active_vector_sync: bool = False,
    ) -> ChangeResult:
        """Reference-aware adjudication on top of `_adjudicate_candidate`.

        relation=update + target  -> patch only `changed_fields` (CAS, user_locked respected)
        relation=update, no target -> create from the merged full card (+ base evidence)
        relation=complete/cancel   -> transition the target; no target -> create nothing
        relation=new               -> legacy path (canonical key / similarity reconciliation)
        """
        explicit = cand.relation in ("update", "complete", "cancel")
        target: MemoryItem | None = None
        base_ev: MemoryEvidence | None = None
        if cand.refers_to_label:
            target, base_ev, ambiguous = self._resolve_reference_target(note, cand, window_map)
            if ambiguous:
                # Several cards fit and neither rules nor the model could tell which: change nothing
                # rather than patch the wrong card. The raw note is kept and the job reports it.
                return ChangeResult(
                    proposal_id="",
                    status="unresolved_reference",
                    memory=None,
                    message="Referenced note produced several cards; target ambiguous, nothing changed",
                )
        extra = (base_ev,) if base_ev is not None else ()
        patch = None
        if cand.relation == "update":
            patch = tuple(cand.changed_fields) or tuple(k for k in cand.payload.keys())
        return self._adjudicate_candidate(
            note,
            cand,
            active_prefs,
            defer_active_vector_sync=defer_active_vector_sync,
            forced_target=target,
            extra_evidences=extra,
            patch_fields=patch,
            strict_reference=explicit and cand.relation in ("complete", "cancel"),
        )

    def _adjudicate_candidate(
        self,
        note: RawNote,
        cand: ExtractedCandidate,
        active_prefs: list[MemoryItem],
        *,
        defer_active_vector_sync: bool = False,
        forced_target: MemoryItem | None = None,
        extra_evidences: tuple[MemoryEvidence, ...] = (),
        patch_fields: tuple[str, ...] | None = None,
        strict_reference: bool = False,
    ) -> ChangeResult:
        """Adjudicate one candidate.

        - `forced_target`: the card resolved deterministically from `refers_to` (skips key/similarity lookup).
        - `extra_evidences`: e.g. the referenced note as a `base` evidence (dependency for forget cascade).
        - `patch_fields`: on update, only these fields are written (title/summary kept unless listed).
        - `strict_reference`: an explicit complete/cancel that matches no card creates nothing.
        """
        ev = MemoryEvidence(
            note_id=note.note_id,
            note_revision=note.revision,
            quote=cand.quote or note.content[:160],
        )
        payload = dict(cand.payload)

        # Normalize task completion/cancellation even if LLM emitted `mutation_hint="upsert"` with `task_status="done"`
        effective_hint = cand.mutation_hint
        if cand.memory_type == "task" and effective_hint == "upsert":
            if payload.get("task_status") == "done":
                effective_hint = (
                    "cancel_task"
                    if payload.get("close_reason") in ("cancelled", "dropped")
                    else "complete_task"
                )

        # Normalize time-range tasks (`start_at` .. `due_at`) and auto-enable 15-minute reminder for timed tasks
        if cand.memory_type == "task" and effective_hint == "upsert":
            ref_dt = to_beijing(parse_iso_datetime(note.event_time) or beijing_now())
            start_str = str(payload.get("start_at", "") or "").strip()
            due_str = str(payload.get("due_at", "") or "").strip()
            start_dt = parse_iso_datetime(start_str) if start_str else None
            due_dt = parse_iso_datetime(due_str) if due_str else None
            if start_dt is not None and due_dt is not None and due_dt > start_dt:
                dur_min = max(15, min(1440, int((due_dt - start_dt).total_seconds() // 60)))
                payload["start_at"] = to_beijing(start_dt).replace(microsecond=0).isoformat()
                payload["due_at"] = to_beijing(due_dt).replace(microsecond=0).isoformat()
                payload["duration_minutes"] = dur_min
                payload["is_all_day"] = False
            else:
                tr = extract_beijing_time_range(cand.quote or note.content, ref_dt)
                if tr is None and cand.quote and cand.quote != note.content:
                    tr = extract_beijing_time_range(note.content, ref_dt)
                if tr is not None:
                    tr_start, tr_end, tr_dur = tr
                    payload["start_at"] = tr_start
                    payload["due_at"] = tr_end
                    payload["duration_minutes"] = tr_dur
                    payload["is_all_day"] = False
                    due_str = tr_end
                    due_dt = parse_iso_datetime(tr_end)

            if due_str and payload.get("reminder_offset_minutes") is None:
                if due_dt is None:
                    due_dt = parse_iso_datetime(due_str)
                if due_dt is not None and not (due_dt.hour == 23 and due_dt.minute == 59):
                    payload["is_all_day"] = False
                    payload["reminder_offset_minutes"] = 15

        # Memory-driven task importance calibration: boost task importance if aligned with user preferences!
        if cand.memory_type == "task" and effective_hint == "upsert" and active_prefs:
            task_text = cand.title + " " + cand.summary
            for pref in active_prefs:
                target = str(pref.payload.get("target", "")).strip()
                if not target or pref.payload.get("polarity") != "positive":
                    continue
                stems = [target]
                if len(target) >= 4:
                    stems.extend(target[i : i + 4] for i in range(len(target) - 3))
                if any(s in task_text for s in stems):
                    try:
                        cur_imp = int(payload.get("importance", 3) or 3)
                    except (TypeError, ValueError):
                        cur_imp = 3
                    if cur_imp < 5:
                        payload["importance"] = max(cur_imp, 4)
                        old_r = str(payload.get("priority_reason", "")).strip().rstrip("。；;，, ")
                        payload["priority_reason"] = (
                            f"{old_r}；命中长期关注偏好「{target}」自动提权"
                            if old_r
                            else f"命中长期关注偏好「{target}」自动提权"
                        )
                    break

        is_state_transition = effective_hint in ("complete_task", "cancel_task", "reopen_task")
        if forced_target is not None:
            existing: MemoryItem | None = forced_target
            eff_canonical_key = forced_target.canonical_key
        else:
            eff_canonical_key = cand.canonical_key
            existing = self.memory_repo.find_by_canonical_key(note.space_id, eff_canonical_key)
            is_state_transition = effective_hint in ("complete_task", "cancel_task", "reopen_task")
            cand_assignee = str(payload.get("assignee", "self") or "self").strip() or "self"
            if (
                existing is not None
                and cand.memory_type == "task"
                and existing.memory_type == "task"
            ):
                existing_assignee = str(existing.payload.get("assignee", "self") or "self").strip() or "self"
                if existing_assignee != cand_assignee:
                    eff_canonical_key = f"{cand.canonical_key}:{cand_assignee}"
                    existing = self.memory_repo.find_by_canonical_key(note.space_id, eff_canonical_key)
            if (
                existing is not None
                and cand.memory_type == "task"
                and not is_state_transition
                and existing.payload.get("task_status") == "done"
            ):
                existing = None
            # Hybrid task reconciliation: handle typos ("告警" vs "告急") and LLM canonical_key drift!
            if existing is None and cand.memory_type == "task":
                existing = self._find_reconcilable_task(
                    note.space_id, cand, cand_assignee, include_done=is_state_transition
                )

        if existing is None:
            if strict_reference and effective_hint in ("complete_task", "cancel_task"):
                return ChangeResult(
                    proposal_id="",
                    status="unresolved_reference",
                    memory=None,
                    message="No matching task found to complete or cancel; raw note kept",
                )
            action = "create"
            if effective_hint in ("complete_task", "cancel_task"):
                payload["task_status"] = "done"
                payload["close_reason"] = (
                    "completed" if effective_hint == "complete_task" else "cancelled"
                )
            proposal = ChangeProposal(
                space_id=note.space_id,
                action=action,
                memory_type=cand.memory_type,
                actor_id=note.actor_id,
                origin="ingest_extractor",
                title=cand.title,
                summary=cand.summary,
                canonical_key=eff_canonical_key,
                tags=cand.tags,
                payload=payload,
                assertion_kind=cand.assertion_kind,
                evidences=(ev, *extra_evidences),
                relations=cand.relations,
                event_time=cand.event_time or note.event_time,
                reason=f"Extracted from {note.note_id} rev {note.revision}",
            )
            return self.change_service.apply(
                proposal, defer_active_vector_sync=defer_active_vector_sync
            )

        # Updating an existing memory card
        if effective_hint in ("complete_task", "cancel_task", "reopen_task"):
            action = effective_hint
            kept_title = existing.title
        else:
            action = "update"
            kept_title = cand.title

        kept_summary = cand.summary
        if patch_fields is not None and action == "update":
            # Patch semantics: only fields this note actually changed; everything else stays as on the card
            allowed = set(patch_fields)
            if allowed & {"start_at", "due_at"}:
                allowed |= {"start_at", "due_at", "duration_minutes", "is_all_day", "reminder_offset_minutes"}
            if "importance" in allowed or "urgency" in allowed:
                allowed |= {"priority_reason"}
            payload = {k: v for k, v in payload.items() if k in allowed}
            if "title" not in allowed:
                kept_title = existing.title
            if "summary" not in allowed:
                kept_summary = existing.summary

        for attempt in range(3):
            proposal = ChangeProposal(
                space_id=note.space_id,
                action=action,
                memory_type=existing.memory_type,
                target_memory_id=existing.memory_id,
                expected_version=existing.version,
                actor_id=note.actor_id,
                origin="ingest_extractor",
                title=kept_title,
                summary=kept_summary,
                canonical_key=existing.canonical_key,
                tags=cand.tags,
                payload=payload,
                assertion_kind=cand.assertion_kind,
                evidences=(ev, *extra_evidences),
                relations=cand.relations,
                event_time=cand.event_time or note.event_time,
                reason=f"Adjudicated from {note.note_id} rev {note.revision}",
            )
            try:
                return self.change_service.apply(
                    proposal, defer_active_vector_sync=defer_active_vector_sync
                )
            except VersionConflictError:
                if attempt >= 2:
                    raise
                reloaded = self.memory_repo.get(note.space_id, existing.memory_id)
                if reloaded is None or reloaded.status == "forgotten":
                    return ChangeResult(
                        proposal_id="",
                        status="rejected",
                        memory=None,
                        message="Target memory was forgotten during extraction",
                    )
                existing = reloaded
                if existing.user_locked and existing.memory_type == "task":
                    # Preserve user's manual quadrant/priority edits when retrying after CAS conflict
                    for locked_key in ("quadrant", "importance", "urgency", "priority_score", "priority_reason"):
                        payload.pop(locked_key, None)
        raise VersionConflictError("Unexpected CAS retry exhaustion")

    def edit_raw_note(
        self, space_id: str, note_id: str, new_content: str, actor_id: str = "user"
    ) -> IngestResult:
        """Edit an existing RawNote (C02): invalidate memories dependent solely on old revision, then re-extract."""
        any_note = self.note_repo.get_any_space(note_id)
        if any_note is not None and any_note.space_id != space_id:
            raise PermissionDeniedError("Cross-space note edit denied")
        old_note = self.note_repo.get(space_id, note_id)
        if old_note is None:
            raise NotFoundError(f"Note {note_id!r} not found")

        updated_note = RawNote(
            note_id=old_note.note_id,
            space_id=old_note.space_id,
            content=new_content.strip(),
            source_type=old_note.source_type,
            actor_id=actor_id,
            revision=old_note.revision + 1,
            is_forgotten=old_note.is_forgotten,
            forgotten_spans=old_note.forgotten_spans,
            event_time=old_note.event_time,
            created_at=old_note.created_at,
            updated_at=utc_now_iso(),
            note_kind=old_note.note_kind,
            title=old_note.title,
            pinned=old_note.pinned,
            tags=old_note.tags,
            mood=old_note.mood,
            journal_date=old_note.journal_date,
            extract_memory=old_note.extract_memory,
        )
        self.note_repo.save(updated_note)
        self.vector_lifecycle.sync_note(updated_note)
        if self.job_repo is not None:
            # Pending runs of the old revision are superseded by the re-extraction below
            self.job_repo.cancel_jobs_for_note(space_id, note_id, reason="note_edited")

        # Cascade source revision invalidation (C02)
        dependents = self.memory_repo.find_dependent_memories(space_id, note_id)
        requeue_note_ids: list[str] = []
        for dep in dependents:
            surviving_evs = tuple(e for e in dep.evidences if e.note_id != note_id)
            is_base_dep = any(e.note_id == note_id and e.role == "base" for e in dep.evidences)
            if is_base_dep:
                # Dependency cascade: a later note's write was built on this note (refers_to).
                # Void the card; the edited note and then its dependents are re-extracted in order.
                for e in surviving_evs:
                    if e.note_id not in requeue_note_ids:
                        requeue_note_ids.append(e.note_id)
            if not surviving_evs or is_base_dep:
                # Dependent card had no other supporting sources -> mark superseded & purge vector!
                invalidated = MemoryItem(
                    memory_id=dep.memory_id,
                    space_id=dep.space_id,
                    permalink=dep.permalink,
                    memory_type=dep.memory_type,
                    title=dep.title,
                    summary=dep.summary,
                    payload=dep.payload,
                    canonical_key=dep.canonical_key,
                    tags=dep.tags,
                    assertion_kind=dep.assertion_kind,
                    status="superseded",
                    version=dep.version,
                    user_locked=dep.user_locked,
                    rejected_inferences=dep.rejected_inferences,
                    evidences=(),
                    event_time=dep.event_time,
                    created_at=dep.created_at,
                )
                saved_inv = self.memory_repo.update_cas(
                    invalidated,
                    expected_version=dep.version,
                    change_reason=f"Source note {note_id} revised to v{updated_note.revision}",
                    actor_id=actor_id,
                    origin="user_direct",
                )
                self.relation_repo.clear_inbound_on_inactive(space_id, saved_inv.memory_id)
                self.vector_lifecycle.sync_memory(saved_inv)
            else:
                trimmed = MemoryItem(
                    memory_id=dep.memory_id,
                    space_id=dep.space_id,
                    permalink=dep.permalink,
                    memory_type=dep.memory_type,
                    title=dep.title,
                    summary=dep.summary,
                    payload=dep.payload,
                    canonical_key=dep.canonical_key,
                    tags=dep.tags,
                    assertion_kind=dep.assertion_kind,
                    status=dep.status,
                    version=dep.version,
                    user_locked=dep.user_locked,
                    rejected_inferences=dep.rejected_inferences,
                    evidences=surviving_evs,
                    event_time=dep.event_time,
                    created_at=dep.created_at,
                )
                saved_trim = self.memory_repo.update_cas(
                    trimmed,
                    expected_version=dep.version,
                    change_reason=f"Removed stale revision reference to {note_id}",
                    actor_id=actor_id,
                    origin="user_direct",
                )
                self.vector_lifecycle.sync_memory(saved_trim)

        # Also invalidate any Dream insights derived solely from now-superseded memories (C02)
        for m in self.memory_repo.list_memories(space_id=space_id, status="active", limit=200):
            if m.assertion_kind == "inferred":
                out_rels = self.relation_repo.get_neighbors(space_id, m.memory_id)
                derived_edges = [
                    r for r in out_rels if r.from_memory_id == m.memory_id and r.relation_type == "derived_from"
                ]
                if derived_edges and all(not r.to_memory_id for r in derived_edges):
                    inv_dream = MemoryItem(
                        memory_id=m.memory_id,
                        space_id=m.space_id,
                        permalink=m.permalink,
                        memory_type=m.memory_type,
                        title=m.title,
                        summary=m.summary,
                        payload=m.payload,
                        canonical_key=m.canonical_key,
                        tags=m.tags,
                        assertion_kind=m.assertion_kind,
                        status="superseded",
                        version=m.version,
                        user_locked=m.user_locked,
                        rejected_inferences=m.rejected_inferences,
                        evidences=m.evidences,
                        event_time=m.event_time,
                        created_at=m.created_at,
                    )
                    saved_d = self.memory_repo.update_cas(
                        inv_dream,
                        expected_version=m.version,
                        change_reason="Underlying source facts superseded",
                        actor_id=actor_id,
                        origin="system",
                    )
                    self.vector_lifecycle.sync_memory(saved_d)

        # Re-extract the revised note through the regular pipeline (context window, refers_to, ordered
        # commit), then the notes whose cards depended on it, in their original order.
        worker_up = bool(self.worker_running()) if callable(self.worker_running) else False
        if self.job_repo is not None and worker_up:
            job = self.job_repo.enqueue(
                space_id=space_id,
                note_id=note_id,
                actor_id=actor_id,
                content_hint=updated_note.content,
            )
            if requeue_note_ids:
                self._requeue_dependent_notes(space_id, requeue_note_ids, excluded_note_id="")
            elif callable(self.worker_notify):
                self.worker_notify()
            if self.sync_tracker is not None:
                self.sync_tracker.bump(space_id, reason="note_edited")
            trace_id = self.trace_repo.record_trace(
                space_id=space_id,
                operation="edit_note",
                input_summary=updated_note.visible_content(),
                details={
                    "note_id": note_id,
                    "new_revision": updated_note.revision,
                    "job_id": job["job_id"],
                    "requeued_dependents": requeue_note_ids,
                },
            )
            return IngestResult(
                note=updated_note,
                extraction_status="queued",
                changes=(),
                trace_id=trace_id,
                job_id=job["job_id"],
            )
        res = self.process_persisted_note(space_id, note_id, count_note_in_quota=False)
        if requeue_note_ids:
            self._requeue_dependent_notes(space_id, requeue_note_ids, excluded_note_id="")
        trace_id = self.trace_repo.record_trace(
            space_id=space_id,
            operation="edit_note",
            input_summary=updated_note.visible_content(),
            details={
                "note_id": note_id,
                "new_revision": updated_note.revision,
                "re_extracted": len(res.changes),
                "requeued_dependents": requeue_note_ids,
            },
        )
        return IngestResult(
            note=res.note or updated_note,
            extraction_status=res.extraction_status,
            changes=res.changes,
            trace_id=trace_id,
        )

    def _requeue_dependent_notes(
        self, space_id: str, note_ids: list[str], excluded_note_id: str
    ) -> None:
        """Re-extract notes whose card depended on a forgotten note, with that note kept out of context."""
        alive = [n for n in (self.note_repo.get(space_id, nid) for nid in note_ids) if n is not None]
        alive = [n for n in alive if not n.is_forgotten and n.extract_memory]
        rowids = {
            str(r["note_id"]): int(r["rid"])
            for r in self.note_repo.db.query_all(
                f"SELECT note_id, rowid AS rid FROM raw_notes WHERE space_id = ? AND note_id IN ({','.join('?' for _ in alive)})",
                (space_id, *[n.note_id for n in alive]),
            )
        } if alive else {}
        alive.sort(key=lambda n: rowids.get(n.note_id, 0))
        worker_up = bool(self.worker_running()) if callable(self.worker_running) else False
        excludes = (excluded_note_id,) if excluded_note_id else ()
        for n in alive:
            if self.job_repo is not None and worker_up:
                self.job_repo.enqueue(
                    space_id=space_id,
                    note_id=n.note_id,
                    actor_id=n.actor_id,
                    content_hint=n.content,
                    context_excludes=excludes,
                )
            else:
                self.process_persisted_note(
                    space_id, n.note_id, count_note_in_quota=False, context_excludes=excludes
                )
        if worker_up and alive and callable(self.worker_notify):
            self.worker_notify()

    def forget_note(
        self, space_id: str, note_id: str, span_to_forget: str = "", actor_id: str = "user"
    ) -> dict[str, Any]:
        """Full or partial span forgetting with cascading memory & vector revocation (C07)."""
        any_note = self.note_repo.get_any_space(note_id)
        if any_note is not None and any_note.space_id != space_id:
            raise PermissionDeniedError("Cross-space forget denied")
        note = self.note_repo.get(space_id, note_id, include_forgotten=True)
        if note is None:
            raise NotFoundError(f"Note {note_id!r} not found")

        forgotten_mem_ids: list[str] = []
        dependents = self.memory_repo.find_dependent_memories(space_id, note_id)

        if not span_to_forget.strip():
            # Full note forget: scrub plaintext content so raw DB storage retains no forgotten text
            updated_note = RawNote(
                note_id=note.note_id,
                space_id=note.space_id,
                content="[FORGOTTEN]",
                source_type=note.source_type,
                actor_id=actor_id,
                revision=note.revision + 1,
                is_forgotten=True,
                forgotten_spans=(),
                event_time=note.event_time,
                created_at=note.created_at,
                updated_at=utc_now_iso(),
                note_kind=note.note_kind,
                title="",
                pinned=False,
                tags=(),
                mood="",
                journal_date="",
                extract_memory=note.extract_memory,
            )
            self.note_repo.save(updated_note)
            if self.job_repo is not None:
                self.job_repo.cancel_jobs_for_note(space_id, note_id, reason="note_forgotten")
            self.vector_lifecycle.sync_note(updated_note)

            requeue_note_ids: list[str] = []
            for dep in dependents:
                if any(e.note_id == note_id and e.role == "base" for e in dep.evidences):
                    # Dependency cascade: a later note's write was built on this note (refers_to).
                    # The card cannot be trusted without it -> void it and re-extract the dependent notes
                    # with this note excluded from their context window.
                    others = tuple(dict.fromkeys(e.note_id for e in dep.evidences if e.note_id != note_id))
                    res = self.change_service.apply(
                        ChangeProposal(
                            space_id=space_id,
                            action="forget",
                            memory_type=dep.memory_type,
                            target_memory_id=dep.memory_id,
                            expected_version=dep.version,
                            actor_id=actor_id,
                            origin="user_direct",
                            reason=f"Base note {note_id} forgotten; dependent memory voided",
                            keep_note_ids=others,
                        )
                    )
                    if res.memory:
                        forgotten_mem_ids.append(res.memory.memory_id)
                    for e in dep.evidences:
                        if e.note_id != note_id and e.note_id not in requeue_note_ids:
                            requeue_note_ids.append(e.note_id)
                    continue
                surviving = tuple(e for e in dep.evidences if e.note_id != note_id)
                if not surviving:
                    res = self.change_service.apply(
                        ChangeProposal(
                            space_id=space_id,
                            action="forget",
                            memory_type=dep.memory_type,
                            target_memory_id=dep.memory_id,
                            expected_version=dep.version,
                            actor_id=actor_id,
                            origin="user_direct",
                            reason=f"Source note {note_id} forgotten",
                        )
                    )
                    if res.memory:
                        forgotten_mem_ids.append(res.memory.memory_id)
                else:
                    # Strip forgotten note's evidence quote & link from surviving multi-evidence memory
                    trimmed = MemoryItem(
                        memory_id=dep.memory_id,
                        space_id=dep.space_id,
                        permalink=dep.permalink,
                        memory_type=dep.memory_type,
                        title=dep.title,
                        summary=dep.summary,
                        payload=dep.payload,
                        canonical_key=dep.canonical_key,
                        tags=dep.tags,
                        assertion_kind=dep.assertion_kind,
                        status=dep.status,
                        version=dep.version,
                        user_locked=dep.user_locked,
                        rejected_inferences=dep.rejected_inferences,
                        evidences=surviving,
                        event_time=dep.event_time,
                        created_at=dep.created_at,
                    )
                    saved_trim = self.memory_repo.update_cas(
                        trimmed,
                        expected_version=dep.version,
                        change_reason=f"Removed forgotten evidence note {note_id}",
                        actor_id=actor_id,
                        origin="user_direct",
                    )
                    self.vector_lifecycle.sync_memory(saved_trim)

            if self.media_service is not None:
                self.media_service.purge_for_notes(space_id, [note_id])
            self.trace_repo.redact_for_forgotten(space_id, note_ids=(note_id,))
            if requeue_note_ids:
                self._requeue_dependent_notes(space_id, requeue_note_ids, excluded_note_id=note_id)
            if self.sync_tracker is not None:
                self.sync_tracker.bump(space_id, reason="note_forgotten")
        else:
            # Never resurrect an already-forgotten RawNote when span_to_forget is supplied
            span = span_to_forget.strip()
            if note.is_forgotten:
                self.trace_repo.redact_for_forgotten(space_id, note_ids=(note_id,), spans=(span,))
                return {
                    "note_id": note.note_id,
                    "is_forgotten": True,
                    "forgotten_memory_ids": [],
                    "forgotten_spans": list(note.forgotten_spans),
                }
            # Partial span redaction (C07): scrub span in stored content while keeping non-selected memories intact!
            spans = tuple(dict.fromkeys([*note.forgotten_spans, span]))
            scrubbed_content = note.content.replace(span, "[REDACTED]")
            redacted_title = note.title.replace(span, "[REDACTED]") if note.title else ""
            redacted_tags = tuple(t.replace(span, "[REDACTED]") for t in note.tags)
            updated_note = RawNote(
                note_id=note.note_id,
                space_id=note.space_id,
                content=scrubbed_content,
                source_type=note.source_type,
                actor_id=actor_id,
                revision=note.revision + 1,
                is_forgotten=False,
                forgotten_spans=spans,
                event_time=note.event_time,
                created_at=note.created_at,
                updated_at=utc_now_iso(),
                note_kind=note.note_kind,
                title=redacted_title,
                pinned=note.pinned,
                tags=redacted_tags,
                mood=note.mood,
                journal_date=note.journal_date,
                extract_memory=note.extract_memory,
            )
            self.note_repo.save(updated_note)
            if self.job_repo is not None:
                self.job_repo.cancel_jobs_for_note(space_id, note_id, reason="note_forgotten")
            self.vector_lifecycle.sync_note(updated_note)

            for dep in dependents:
                touched = (
                    span in dep.title
                    or span in dep.summary
                    or span in str(dep.payload)
                    or any(span in e.quote for e in dep.evidences if e.note_id == note_id)
                )
                if touched:
                    res = self.change_service.apply(
                        ChangeProposal(
                            space_id=space_id,
                            action="forget",
                            memory_type=dep.memory_type,
                            target_memory_id=dep.memory_id,
                            expected_version=dep.version,
                            actor_id=actor_id,
                            origin="user_direct",
                            reason=f"Span redacted in {note_id}",
                        )
                    )
                    if res.memory:
                        forgotten_mem_ids.append(res.memory.memory_id)
                else:
                    # Update surviving memory's evidence revision pointer to keep it valid
                    refreshed_evs = tuple(
                        MemoryEvidence(
                            note_id=e.note_id,
                            note_revision=updated_note.revision if e.note_id == note_id else e.note_revision,
                            quote=e.quote,
                            role=e.role,
                        )
                        for e in dep.evidences
                    )
                    kept = MemoryItem(
                        memory_id=dep.memory_id,
                        space_id=dep.space_id,
                        permalink=dep.permalink,
                        memory_type=dep.memory_type,
                        title=dep.title,
                        summary=dep.summary,
                        payload=dep.payload,
                        canonical_key=dep.canonical_key,
                        tags=dep.tags,
                        assertion_kind=dep.assertion_kind,
                        status=dep.status,
                        version=dep.version,
                        user_locked=dep.user_locked,
                        rejected_inferences=dep.rejected_inferences,
                        evidences=refreshed_evs,
                        event_time=dep.event_time,
                        created_at=dep.created_at,
                    )
                    self.memory_repo.update_cas(
                        kept,
                        expected_version=dep.version,
                        change_reason="Preserved unaffected memory during partial span forget",
                        actor_id=actor_id,
                        origin="user_direct",
                    )

            self.trace_repo.redact_for_forgotten(space_id, spans=(span,))
            if self.sync_tracker is not None:
                self.sync_tracker.bump(space_id, reason="note_span_forgotten")

        return {
            "note_id": note_id,
            "is_forgotten": updated_note.is_forgotten,
            "forgotten_spans": list(updated_note.forgotten_spans),
            "forgotten_memory_ids": forgotten_mem_ids,
        }
