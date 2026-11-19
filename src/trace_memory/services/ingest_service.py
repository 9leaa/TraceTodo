"""Ingestion pipeline: RawNote persistence, extraction, preference-calibrated task scoring, and source revocation."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import re
import time
from typing import Any

from trace_memory.domain.memories import MemoryEvidence, MemoryItem
from trace_memory.domain.notes import RawNote, new_id, utc_now_iso
from trace_memory.domain.proposals import (
    ChangeProposal,
    ChangeResult,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from trace_memory.llm.protocol import ExtractedCandidate, LLMProvider
from trace_memory.services.change_service import ChangeService
from trace_memory.services.guard_service import InputGuardService, RateLimitAndQuotaService
from trace_memory.storage.repositories import (
    MemoryRepository,
    NoteRepository,
    RelationRepository,
    TraceRepository,
)
from trace_memory.storage.vector_store import VectorLifecycleService


@dataclass(frozen=True)
class IngestResult:
    note: RawNote
    extraction_status: str  # "completed" | "degraded_error" | "idempotent_replay" | "filtered_noise"
    changes: tuple[ChangeResult, ...]
    error_message: str = ""
    trace_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "note": self.note.to_dict(),
            "extraction_status": self.extraction_status,
            "filtered": self.extraction_status == "filtered_noise",
            "filter_reason": self.error_message if self.extraction_status == "filtered_noise" else "",
            "changes": [c.to_dict() for c in self.changes],
            "error_message": self.error_message,
            "trace_id": self.trace_id,
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

    def record_note(
        self,
        space_id: str,
        content: str,
        source_type: str = "cli",
        actor_id: str = "user",
        event_time: str = "",
        idempotency_key: str = "",
    ) -> IngestResult:
        t0 = time.perf_counter()

        # 1. Entry idempotency check (C08)
        if idempotency_key:
            cached = self.trace_repo.get_idempotent_result(space_id, f"ingest:{idempotency_key}")
            if cached is not None:
                existing_note = self.note_repo.get(
                    space_id, cached["note"]["note_id"], include_forgotten=True
                )
                if existing_note is not None:
                    return IngestResult(
                        note=existing_note,
                        extraction_status="idempotent_replay",
                        changes=(),
                        trace_id=cached.get("trace_id", ""),
                    )

        # 1b. Zero-cost local InputGuard filter (catches empty, oversized, greetings, gibberish BEFORE storage/LLM/Embedding!)
        if self.input_guard is not None:
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
                duration = (time.perf_counter() - t0) * 1000.0
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
                return IngestResult(
                    note=transient_note,
                    extraction_status="filtered_noise",
                    changes=(),
                    error_message=guard_res.reason,
                    trace_id=trace_id,
                )

        # 2. Persist ground-truth RawNote BEFORE any fallible LLM work (C10)
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

        # 3. Extract structured candidates via LLMProvider (passing existing active memories for reconciliation)
        existing_active = self.memory_repo.list_memories(
            space_id=space_id, status="active", limit=40
        )
        vector_error = ""
        embed_calls = 0
        try:
            batch = self._call_extract(note.content, note.event_time, existing_active)
        except Exception as exc:
            # C10: Still index raw note vector on LLM failure so fallback retrieval keeps working
            try:
                self.vector_lifecycle.sync_note(note)
                embed_calls += 1
            except Exception as vec_exc:
                vector_error = f"Note embedding degraded: {vec_exc}"
            if self.quota_service is not None:
                self.quota_service.record_usage(
                    f"space:{space_id}", notes=1, embed_calls=embed_calls
                )
            duration = (time.perf_counter() - t0) * 1000.0
            trace_id = self.trace_repo.record_trace(
                space_id=space_id,
                operation="ingest_degraded",
                input_summary=note.content,
                details={"note_id": note.note_id, "error": str(exc), "vector_error": vector_error},
                duration_ms=duration,
            )
            return IngestResult(
                note=note,
                extraction_status="degraded_error",
                changes=(),
                error_message=str(exc),
                trace_id=trace_id,
            )

        # Selective Note Vectorization: skip costly Embedding API if LLM confirmed 0 extractable memories!
        note_vector_skipped = len(batch.candidates) == 0
        if not note_vector_skipped:
            try:
                self.vector_lifecycle.sync_note(note)
                embed_calls += 1
            except Exception as exc:
                # C10: Record embedding degradation honestly without losing the RawNote
                vector_error = f"Note embedding degraded: {exc}"

        # 4. Calibrate candidates against existing memories & user preferences, then commit via ChangeService
        active_prefs = self.memory_repo.list_memories(
            space_id=space_id, memory_type="preference", status="active", limit=30
        )
        results: list[ChangeResult] = []
        for cand in batch.candidates:
            res = self._adjudicate_candidate(note, cand, active_prefs)
            results.append(res)
            if res.memory is not None and res.status in ("created", "updated"):
                embed_calls += 1

        if self.quota_service is not None:
            self.quota_service.record_usage(
                f"space:{space_id}",
                notes=1,
                llm_calls=batch.llm_calls,
                embed_calls=embed_calls,
                tokens_used=batch.tokens_used,
            )

        duration = (time.perf_counter() - t0) * 1000.0
        status = "degraded_error" if vector_error else "completed"
        trace_id = self.trace_repo.record_trace(
            space_id=space_id,
            operation="ingest",
            input_summary=note.content,
            details={
                "note_id": note.note_id,
                "candidate_count": len(batch.candidates),
                "applied_statuses": [r.status for r in results],
                "note_vector_skipped": note_vector_skipped,
                "vector_error": vector_error,
            },
            llm_calls=batch.llm_calls,
            tokens_used=batch.tokens_used,
            duration_ms=duration,
        )
        out = IngestResult(
            note=note,
            extraction_status=status,
            changes=tuple(results),
            error_message=vector_error,
            trace_id=trace_id,
        )
        if idempotency_key:
            self.trace_repo.save_idempotent_result(
                space_id, f"ingest:{idempotency_key}", note.note_id, out.to_dict()
            )
        return out

    def _call_extract(
        self,
        text: str,
        event_time: str,
        existing_memories: list[MemoryItem],
    ):
        sig = inspect.signature(self.llm.extract_candidates)
        if len(sig.parameters) >= 3:
            return self.llm.extract_candidates(text, event_time, existing_memories)
        return self.llm.extract_candidates(text, event_time)  # type: ignore[call-arg]

    @staticmethod
    def _extract_round_marker(text: str) -> str:
        m = re.search(r"第([一二三四五六七八九十\d]+)(?:轮|期|版|次)", text)
        if not m:
            return ""
        mapping = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5"}
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
        self, space_id: str, cand: ExtractedCandidate, cand_assignee: str
    ) -> MemoryItem | None:
        """Match a task candidate against existing active tasks while preserving C05 assignee/round boundaries."""
        cand_round = self._extract_round_marker(cand.title + " " + cand.canonical_key)
        best_item: MemoryItem | None = None
        best_sim = 0.0

        for t_item in self.memory_repo.list_memories(
            space_id=space_id, memory_type="task", status="active", limit=100
        ):
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

    def _adjudicate_candidate(
        self,
        note: RawNote,
        cand: ExtractedCandidate,
        active_prefs: list[MemoryItem],
    ) -> ChangeResult:
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
                    cur_imp = int(payload.get("importance", 3))
                    if cur_imp < 5:
                        payload["importance"] = max(cur_imp, 4)
                        old_r = str(payload.get("priority_reason", ""))
                        payload["priority_reason"] = (
                            f"{old_r}；命中长期关注偏好「{target}」自动提权".strip("；")
                        )
                    break

        existing = self.memory_repo.find_by_canonical_key(note.space_id, cand.canonical_key)
        # Hybrid task reconciliation: handle typos ("告警" vs "告急") and LLM canonical_key drift!
        if existing is None and cand.memory_type == "task":
            cand_assignee = str(payload.get("assignee", "self") or "self")
            existing = self._find_reconcilable_task(note.space_id, cand, cand_assignee)

        if existing is None:
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
                canonical_key=cand.canonical_key,
                tags=cand.tags,
                payload=payload,
                assertion_kind=cand.assertion_kind,
                evidences=(ev,),
                relations=cand.relations,
                event_time=cand.event_time or note.event_time,
                reason=f"Extracted from {note.note_id} rev {note.revision}",
            )
            return self.change_service.apply(proposal)

        # Updating an existing memory card
        if effective_hint in ("complete_task", "cancel_task", "reopen_task"):
            action = effective_hint
            kept_title = existing.title
        else:
            action = "update"
            kept_title = cand.title

        proposal = ChangeProposal(
            space_id=note.space_id,
            action=action,
            memory_type=existing.memory_type,
            target_memory_id=existing.memory_id,
            expected_version=existing.version,
            actor_id=note.actor_id,
            origin="ingest_extractor",
            title=kept_title,
            summary=cand.summary,
            canonical_key=existing.canonical_key,
            tags=cand.tags,
            payload=payload,
            assertion_kind=cand.assertion_kind,
            evidences=(ev,),
            relations=cand.relations,
            event_time=cand.event_time or note.event_time,
            reason=f"Adjudicated from {note.note_id} rev {note.revision}",
        )
        return self.change_service.apply(proposal)

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
        )
        self.note_repo.save(updated_note)
        self.vector_lifecycle.sync_note(updated_note)

        # Cascade source revision invalidation (C02)
        dependents = self.memory_repo.find_dependent_memories(space_id, note_id)
        for dep in dependents:
            surviving_evs = tuple(e for e in dep.evidences if e.note_id != note_id)
            if not surviving_evs:
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

        # Re-extract fresh candidates from the revised note
        batch = self.llm.extract_candidates(updated_note.visible_content(), updated_note.event_time)
        active_prefs = self.memory_repo.list_memories(
            space_id=space_id, memory_type="preference", status="active", limit=30
        )
        changes = tuple(
            self._adjudicate_candidate(updated_note, c, active_prefs) for c in batch.candidates
        )
        trace_id = self.trace_repo.record_trace(
            space_id=space_id,
            operation="edit_note",
            input_summary=updated_note.visible_content(),
            details={"note_id": note_id, "new_revision": updated_note.revision, "re_extracted": len(changes)},
            llm_calls=batch.llm_calls,
            tokens_used=batch.tokens_used,
        )
        return IngestResult(
            note=updated_note,
            extraction_status="completed",
            changes=changes,
            trace_id=trace_id,
        )

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
            )
            self.note_repo.save(updated_note)
            self.vector_lifecycle.sync_note(updated_note)

            for dep in dependents:
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

            self.trace_repo.redact_for_forgotten(space_id, note_ids=(note_id,))
        else:
            # Partial span redaction (C07): scrub span in stored content while keeping non-selected memories intact!
            span = span_to_forget.strip()
            spans = tuple(dict.fromkeys([*note.forgotten_spans, span]))
            scrubbed_content = note.content.replace(span, "[REDACTED]")
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
            )
            self.note_repo.save(updated_note)
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

        return {
            "note_id": note_id,
            "is_forgotten": updated_note.is_forgotten,
            "forgotten_spans": list(updated_note.forgotten_spans),
            "forgotten_memory_ids": forgotten_mem_ids,
        }
