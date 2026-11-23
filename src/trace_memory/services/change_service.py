"""Unified Change Proposal Gatekeeper: CAS preconditions, space ACL, causal order, and user locks."""

from __future__ import annotations

from typing import Any

from trace_memory.domain.memories import (
    MemoryEvidence,
    MemoryItem,
    parse_iso_datetime,
)
from trace_memory.domain.notes import RawNote, new_id, utc_now_iso
from trace_memory.domain.proposals import (
    ChangeProposal,
    ChangeResult,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
    VersionConflictError,
)
from trace_memory.domain.relations import slugify_permalink
from trace_memory.storage.repositories import (
    MemoryRepository,
    NoteRepository,
    RelationRepository,
    TraceRepository,
)
from trace_memory.storage.vector_store import VectorLifecycleService


def _merge_evidences(
    old_evs: tuple[MemoryEvidence, ...], new_evs: tuple[MemoryEvidence, ...]
) -> tuple[MemoryEvidence, ...]:
    merged: dict[tuple[str, int], MemoryEvidence] = {
        (e.note_id, e.note_revision): e for e in old_evs
    }
    for e in new_evs:
        merged[(e.note_id, e.note_revision)] = e
    return tuple(merged.values())


class ChangeService:
    """Single authoritative mutation service for all internal and external memory writes."""

    def __init__(
        self,
        note_repo: NoteRepository,
        memory_repo: MemoryRepository,
        relation_repo: RelationRepository,
        trace_repo: TraceRepository,
        vector_lifecycle: VectorLifecycleService,
        sync_tracker: Any = None,
        media_service: Any = None,
    ) -> None:
        self.note_repo = note_repo
        self.memory_repo = memory_repo
        self.relation_repo = relation_repo
        self.trace_repo = trace_repo
        self.vector_lifecycle = vector_lifecycle
        self.sync_tracker = sync_tracker
        self.media_service = media_service

    def _validate_evidence_permissions(
        self, space_id: str, evidences: tuple[MemoryEvidence, ...]
    ) -> None:
        for ev in evidences:
            any_note = self.note_repo.get_any_space(ev.note_id)
            if any_note is None:
                raise ValidationError(f"Evidence note {ev.note_id!r} does not exist")
            if any_note.space_id != space_id:
                # C09: Never leak cross-space note content in error messages
                raise PermissionDeniedError("Cross-space source reference denied")
            if any_note.is_forgotten:
                raise ValidationError("Cannot cite a forgotten note as evidence")

    def apply(
        self, proposal: ChangeProposal, *, defer_active_vector_sync: bool = False
    ) -> ChangeResult:
        # 1. Idempotency check (C08)
        if proposal.idempotency_key:
            cached = self.trace_repo.get_idempotent_result(
                proposal.space_id, proposal.idempotency_key
            )
            if cached is not None:
                mem_dict = cached.get("memory")
                mem = (
                    self.memory_repo.get(proposal.space_id, mem_dict["memory_id"], include_forgotten=True)
                    if mem_dict
                    else None
                )
                return ChangeResult(
                    proposal_id=cached["proposal_id"],
                    status="idempotent_replay",
                    memory=mem,
                    message="Idempotent replay returned cached result",
                )

        self._validate_evidence_permissions(proposal.space_id, proposal.evidences)
        proposal_id = new_id("prop")

        if proposal.action == "create":
            result = self._handle_create(
                proposal_id, proposal, defer_active_vector_sync=defer_active_vector_sync
            )
        else:
            result = self._handle_existing(
                proposal_id, proposal, defer_active_vector_sync=defer_active_vector_sync
            )

        if proposal.idempotency_key:
            self.trace_repo.save_idempotent_result(
                proposal.space_id, proposal.idempotency_key, proposal_id, result.to_dict()
            )
        if self.sync_tracker is not None and result.status in (
            "created",
            "updated",
            "applied",
            "pending_review",
        ):
            self.sync_tracker.bump(proposal.space_id, reason=proposal.action)
        return result

    def _handle_create(
        self,
        proposal_id: str,
        proposal: ChangeProposal,
        *,
        defer_active_vector_sync: bool = False,
    ) -> ChangeResult:
        mem_id = new_id("mem")
        title = proposal.title.strip() or proposal.summary[:28].strip() or "Untitled Memory"
        permalink = proposal.permalink.strip("/") or slugify_permalink(
            proposal.memory_type, title
        )
        initial_status = "pending_review" if proposal.require_review else "active"
        eff_event_time = proposal.event_time or utc_now_iso()
        eff_summary = proposal.summary.strip() or title
        create_payload = dict(proposal.payload)
        if proposal.memory_type == "semantic" and not str(create_payload.get("event_time", "")).strip():
            create_payload["event_time"] = eff_event_time
        elif proposal.memory_type == "preference":
            if not str(create_payload.get("target", "") or "").strip():
                create_payload["target"] = title
        elif proposal.memory_type == "episodic":
            if not str(create_payload.get("event_summary", "") or "").strip():
                create_payload["event_summary"] = eff_summary
        item = MemoryItem(
            memory_id=mem_id,
            space_id=proposal.space_id,
            permalink=permalink,
            memory_type=proposal.memory_type,
            title=title,
            summary=eff_summary,
            payload=create_payload,
            canonical_key=proposal.canonical_key,
            tags=proposal.tags,
            assertion_kind=proposal.assertion_kind,
            status=initial_status,
            version=1,
            user_locked=proposal.lock_for_user,
            evidences=proposal.evidences,
            event_time=eff_event_time,
        )
        saved = self.memory_repo.create(
            item=item,
            change_reason=proposal.reason or f"Created via {proposal.origin}",
            actor_id=proposal.actor_id,
            origin=proposal.origin,
        )
        if proposal.relations or proposal.replace_relations:
            self.relation_repo.replace_outgoing(
                proposal.space_id,
                saved.memory_id,
                [(r.relation_type, r.target, r.context) for r in proposal.relations],
            )
        self.relation_repo.resolve_pending_inbound(saved)
        if not (defer_active_vector_sync and saved.status == "active"):
            self.vector_lifecycle.sync_memory(saved)

        return ChangeResult(
            proposal_id=proposal_id,
            status="pending_review" if initial_status == "pending_review" else "applied",
            memory=saved,
            message="Memory created",
        )

    def _handle_existing(
        self,
        proposal_id: str,
        proposal: ChangeProposal,
        *,
        defer_active_vector_sync: bool = False,
    ) -> ChangeResult:
        # C09: Distinguish cross-space unauthorized access from missing ID without leaking content
        raw_any = self.memory_repo.get_any_space(proposal.target_memory_id)
        if raw_any is not None and raw_any.space_id != proposal.space_id:
            raise PermissionDeniedError("Access denied to target memory in another space")

        existing = self.memory_repo.get(
            proposal.space_id, proposal.target_memory_id, include_forgotten=True
        )
        if existing is None:
            raise NotFoundError(f"Memory {proposal.target_memory_id!r} not found")

        # Anti-resurrection guard: forgotten memories can never be revived or edited
        if existing.status == "forgotten":
            if proposal.action == "forget":
                return ChangeResult(
                    proposal_id=proposal_id,
                    status="applied",
                    memory=existing,
                    message="Memory already forgotten",
                )
            raise ValidationError("已遗忘的记忆不可恢复或修改")

        # CAS check (C08)
        if proposal.expected_version is not None and proposal.expected_version != existing.version:
            raise VersionConflictError(
                existing.memory_id, proposal.expected_version, existing.version
            )
        expected_ver = existing.version

        is_auto_origin = proposal.origin in ("ingest_extractor", "dream_consolidator")

        # C06: Reject resurrected inferences that user previously rejected
        if proposal.assertion_kind == "inferred" and existing.rejected_inferences:
            fingerprint = (proposal.summary or str(proposal.payload)).strip()
            if fingerprint in existing.rejected_inferences:
                return ChangeResult(
                    proposal_id=proposal_id,
                    status="skipped_user_locked",
                    memory=existing,
                    message="Skipped previously rejected inference",
                )

        # C06: User-locked cards resist automatic background overwrites (except explicit task completion)
        if (
            is_auto_origin
            and existing.user_locked
            and proposal.action not in ("complete_task", "cancel_task", "reopen_task")
        ):
            return ChangeResult(
                proposal_id=proposal_id,
                status="skipped_user_locked",
                memory=existing,
                message="Preserved user-locked card against automatic consolidation",
            )

        if proposal.action == "forget":
            old_evidences = existing.evidences
            old_note_ids = tuple(dict.fromkeys(e.note_id for e in old_evidences))
            old_semantic_value = str(existing.payload.get("value", "") or "").strip()

            forgotten = MemoryItem(
                memory_id=existing.memory_id,
                space_id=existing.space_id,
                permalink=f"forgotten/{existing.memory_id}",
                memory_type=existing.memory_type,
                title="[FORGOTTEN]",
                summary="[FORGOTTEN]",
                payload={},
                canonical_key="",
                tags=(),
                assertion_kind=existing.assertion_kind,
                status="forgotten",
                version=existing.version,
                user_locked=existing.user_locked,
                rejected_inferences=existing.rejected_inferences,
                evidences=(),
                event_time=existing.event_time,
                created_at=existing.created_at,
            )
            saved = self.memory_repo.update_cas(
                forgotten,
                expected_version=expected_ver,
                change_reason=proposal.reason or "Forgotten by request",
                actor_id=proposal.actor_id,
                origin=proposal.origin,
            )
            self.memory_repo.scrub_forgotten_versions(proposal.space_id, saved.memory_id)
            self.relation_repo.clear_inbound_on_inactive(
                proposal.space_id, saved.memory_id, redact_target_text=True
            )
            self.vector_lifecycle.sync_memory(saved)

            # Cascade-clean supporting RawNotes (exclusive notes fully scrubbed; shared notes redact non-overlapping spans)
            purged_note_ids: list[str] = []
            redacted_spans_all: list[str] = []
            for nid in old_note_ids:
                note = self.note_repo.get(proposal.space_id, nid, include_forgotten=True)
                if note is None:
                    continue
                remaining_deps = self.memory_repo.find_dependent_memories(proposal.space_id, nid)
                stated_deps = [d for d in remaining_deps if d.assertion_kind != "inferred"]
                if not stated_deps:
                    # Strip forgotten note references from surviving inferred Dream memories before purging note
                    for inf_dep in remaining_deps:
                        trimmed_evs = tuple(e for e in inf_dep.evidences if e.note_id != nid)
                        if len(trimmed_evs) != len(inf_dep.evidences):
                            trimmed_inf = MemoryItem(
                                memory_id=inf_dep.memory_id,
                                space_id=inf_dep.space_id,
                                permalink=inf_dep.permalink,
                                memory_type=inf_dep.memory_type,
                                title=inf_dep.title,
                                summary=inf_dep.summary,
                                payload=inf_dep.payload,
                                canonical_key=inf_dep.canonical_key,
                                tags=inf_dep.tags,
                                assertion_kind=inf_dep.assertion_kind,
                                status=inf_dep.status,
                                version=inf_dep.version,
                                user_locked=inf_dep.user_locked,
                                rejected_inferences=inf_dep.rejected_inferences,
                                evidences=trimmed_evs,
                                event_time=inf_dep.event_time,
                                created_at=inf_dep.created_at,
                            )
                            self.memory_repo.update_cas(
                                trimmed_inf,
                                expected_version=inf_dep.version,
                                change_reason=f"Pruned forgotten source evidence {nid}",
                                actor_id=proposal.actor_id,
                                origin=proposal.origin,
                            )
                    if not note.is_forgotten or note.content != "[FORGOTTEN]":
                        scrubbed_note = RawNote(
                            note_id=note.note_id,
                            space_id=note.space_id,
                            content="[FORGOTTEN]",
                            source_type=note.source_type,
                            actor_id=proposal.actor_id,
                            revision=note.revision + 1,
                            is_forgotten=True,
                            forgotten_spans=note.forgotten_spans,
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
                        self.note_repo.save(scrubbed_note)
                        self.vector_lifecycle.sync_note(scrubbed_note)
                    purged_note_ids.append(nid)
                elif not note.is_forgotten:
                    # Shared RawNote: redact specific spans belonging ONLY to the forgotten memory (ignoring Dream copies)
                    sibling_blob = "\n".join(
                        f"{d.title}\n{d.summary}\n{d.payload}\n"
                        + "\n".join(e.quote for e in d.evidences)
                        for d in stated_deps
                    )
                    candidate_spans: list[str] = []
                    for ev in old_evidences:
                        if ev.note_id == nid and ev.quote.strip():
                            candidate_spans.append(ev.quote.strip())
                    if len(old_semantic_value) >= 2:
                        candidate_spans.append(old_semantic_value)
                    visible_now = note.visible_content()
                    new_spans = list(note.forgotten_spans)
                    new_content = note.content
                    changed_note = False
                    for sp in candidate_spans:
                        if sp and sp in visible_now and sp not in sibling_blob and sp not in new_spans:
                            new_spans.append(sp)
                            new_content = new_content.replace(sp, "[REDACTED]")
                            redacted_spans_all.append(sp)
                            changed_note = True
                    if changed_note:
                        new_title = note.title
                        new_tags = list(note.tags)
                        for sp in redacted_spans_all:
                            new_title = new_title.replace(sp, "[REDACTED]")
                            new_tags = [t.replace(sp, "[REDACTED]") for t in new_tags]
                        redacted_note = RawNote(
                            note_id=note.note_id,
                            space_id=note.space_id,
                            content=new_content,
                            source_type=note.source_type,
                            actor_id=proposal.actor_id,
                            revision=note.revision + 1,
                            is_forgotten=False,
                            forgotten_spans=tuple(new_spans),
                            event_time=note.event_time,
                            created_at=note.created_at,
                            updated_at=utc_now_iso(),
                            note_kind=note.note_kind,
                            title=new_title,
                            pinned=note.pinned,
                            tags=tuple(new_tags),
                            mood=note.mood,
                            journal_date=note.journal_date,
                            extract_memory=note.extract_memory,
                        )
                        self.note_repo.save(redacted_note)
                        self.vector_lifecycle.sync_note(redacted_note)

            # Cascade-forget any Dream inferred insights (active or pending_review) whose sources are broken
            for cand_status in ("active", "pending_review"):
                for cand in self.memory_repo.list_memories(
                    space_id=proposal.space_id, status=cand_status, limit=1000
                ):
                    if cand.assertion_kind != "inferred" or cand.memory_id == saved.memory_id:
                        continue
                    out_rels = self.relation_repo.get_neighbors(proposal.space_id, cand.memory_id)
                    derived_edges = [
                        r
                        for r in out_rels
                        if r.from_memory_id == cand.memory_id and r.relation_type == "derived_from"
                    ]
                    if derived_edges and all(not r.to_memory_id for r in derived_edges):
                        self.apply(
                            ChangeProposal(
                                space_id=proposal.space_id,
                                action="forget",
                                memory_type=cand.memory_type,
                                target_memory_id=cand.memory_id,
                                expected_version=cand.version,
                                actor_id=proposal.actor_id,
                                origin=proposal.origin,
                                reason=f"Derived source memory {saved.memory_id} forgotten",
                            )
                        )

            if self.media_service is not None and purged_note_ids:
                self.media_service.purge_for_notes(proposal.space_id, purged_note_ids)

            self.trace_repo.redact_for_forgotten(
                proposal.space_id,
                memory_id=saved.memory_id,
                note_ids=tuple(purged_note_ids),
                spans=tuple(dict.fromkeys(redacted_spans_all)),
            )
            return ChangeResult(
                proposal_id=proposal_id, status="applied", memory=saved, message="Memory forgotten"
            )

        if proposal.action == "approve":
            if existing.status != "pending_review":
                raise ValidationError("仅待审核状态的记忆允许审批通过")
            approved = MemoryItem(
                memory_id=existing.memory_id,
                space_id=existing.space_id,
                permalink=existing.permalink,
                memory_type=existing.memory_type,
                title=existing.title,
                summary=existing.summary,
                payload=existing.payload,
                canonical_key=existing.canonical_key,
                tags=existing.tags,
                assertion_kind=existing.assertion_kind,
                status="active",
                version=existing.version,
                user_locked=existing.user_locked,
                rejected_inferences=existing.rejected_inferences,
                evidences=existing.evidences,
                event_time=existing.event_time,
                created_at=existing.created_at,
            )
            saved = self.memory_repo.update_cas(
                approved,
                expected_version=expected_ver,
                change_reason=proposal.reason or "Approved pending memory",
                actor_id=proposal.actor_id,
                origin=proposal.origin,
            )
            self.relation_repo.resolve_pending_inbound(saved)
            self.vector_lifecycle.sync_memory(saved)
            return ChangeResult(
                proposal_id=proposal_id, status="applied", memory=saved, message="Memory approved"
            )

        if proposal.action == "reject":
            rejected_fp = existing.summary.strip()
            rej_list = tuple(dict.fromkeys([*existing.rejected_inferences, rejected_fp]))
            superseded = MemoryItem(
                memory_id=existing.memory_id,
                space_id=existing.space_id,
                permalink=existing.permalink,
                memory_type=existing.memory_type,
                title=existing.title,
                summary=existing.summary,
                payload=existing.payload,
                canonical_key=existing.canonical_key,
                tags=existing.tags,
                assertion_kind=existing.assertion_kind,
                status="superseded",
                version=existing.version,
                user_locked=True,
                rejected_inferences=rej_list,
                evidences=existing.evidences,
                event_time=existing.event_time,
                created_at=existing.created_at,
            )
            saved = self.memory_repo.update_cas(
                superseded,
                expected_version=expected_ver,
                change_reason=proposal.reason or "Rejected inferred memory",
                actor_id=proposal.actor_id,
                origin=proposal.origin,
            )
            self.relation_repo.clear_inbound_on_inactive(proposal.space_id, saved.memory_id)
            self.vector_lifecycle.sync_memory(saved)
            return ChangeResult(
                proposal_id=proposal_id, status="applied", memory=saved, message="Proposal rejected"
            )

        # Handle Task State & Quadrant Transitions (C04)
        merged_evs = _merge_evidences(existing.evidences, proposal.evidences)
        new_payload: dict[str, Any] = dict(existing.payload)
        new_title = proposal.title.strip() or existing.title
        new_summary = proposal.summary.strip() or existing.summary
        new_event_time = proposal.event_time or existing.event_time

        if existing.memory_type == "task":
            if proposal.action == "complete_task":
                new_payload["task_status"] = "done"
                new_payload["close_reason"] = proposal.payload.get("close_reason") or "completed"
                new_summary = proposal.summary.strip() or f"已完成：{existing.title}"
            elif proposal.action == "cancel_task":
                new_payload["task_status"] = "done"
                new_payload["close_reason"] = proposal.payload.get("close_reason") or "cancelled"
                new_summary = proposal.summary.strip() or f"已取消：{existing.title}"
            elif proposal.action == "reopen_task":
                new_payload["task_status"] = "todo"
                new_payload["close_reason"] = ""
                new_summary = proposal.summary.strip() or existing.title
            elif proposal.action == "update_quadrant":
                for k in ("importance", "urgency", "quadrant", "priority_score", "priority_reason", "due_at"):
                    if k in proposal.payload and proposal.payload[k] is not None:
                        new_payload[k] = proposal.payload[k]
                if "quadrant" not in proposal.payload and any(
                    k in proposal.payload for k in ("importance", "urgency", "due_at")
                ):
                    new_payload.pop("quadrant", None)
                    new_payload.pop("priority_score", None)
                elif "quadrant" in proposal.payload and "priority_score" not in proposal.payload:
                    new_payload.pop("priority_score", None)
            elif proposal.action == "update":
                # C04: Never silently reopen a finished task during ordinary update!
                was_done = existing.payload.get("task_status") == "done"
                incoming_patch = dict(proposal.payload)
                if was_done and incoming_patch.get("task_status") == "todo":
                    incoming_patch.pop("task_status", None)
                    incoming_patch.pop("close_reason", None)
                new_payload.update(incoming_patch)
                if "quadrant" not in incoming_patch and not existing.user_locked and any(
                    k in incoming_patch for k in ("importance", "urgency", "due_at", "blockers")
                ):
                    new_payload.pop("quadrant", None)
                    new_payload.pop("priority_score", None)
                elif "quadrant" in incoming_patch and "priority_score" not in incoming_patch:
                    new_payload.pop("priority_score", None)

        elif existing.memory_type == "semantic":
            # C01: Temporal causal barrier — late-arriving older history must NOT overwrite newer truth
            inc_dt = parse_iso_datetime(proposal.event_time)
            cur_dt = parse_iso_datetime(existing.event_time)
            if inc_dt is not None and cur_dt is not None and inc_dt < cur_dt:
                older_val = str(proposal.payload.get("value", "")).strip()
                prev_vals = list(existing.payload.get("previous_values", []))
                if older_val and older_val != existing.payload.get("value") and older_val not in prev_vals:
                    prev_vals.append(older_val)
                new_payload["previous_values"] = prev_vals
                # Keep current title, summary, value, and current event_time intact!
                new_title = existing.title
                new_summary = existing.summary
                new_event_time = existing.event_time
                backfilled = MemoryItem(
                    memory_id=existing.memory_id,
                    space_id=existing.space_id,
                    permalink=existing.permalink,
                    memory_type=existing.memory_type,
                    title=new_title,
                    summary=new_summary,
                    payload=new_payload,
                    canonical_key=existing.canonical_key,
                    tags=tuple(dict.fromkeys([*existing.tags, *proposal.tags])),
                    assertion_kind=existing.assertion_kind,
                    status=existing.status,
                    version=existing.version,
                    user_locked=existing.user_locked,
                    rejected_inferences=existing.rejected_inferences,
                    evidences=merged_evs,
                    event_time=new_event_time,
                    created_at=existing.created_at,
                )
                saved = self.memory_repo.update_cas(
                    backfilled,
                    expected_version=expected_ver,
                    change_reason="Historical backfill recorded without overwriting newer state",
                    actor_id=proposal.actor_id,
                    origin=proposal.origin,
                )
                if not (defer_active_vector_sync and saved.status == "active"):
                    self.vector_lifecycle.sync_memory(saved)
                return ChangeResult(
                    proposal_id=proposal_id,
                    status="skipped_older_event",
                    memory=saved,
                    message="Recorded historical value in history without overwriting newer current state",
                )
            else:
                old_val = str(existing.payload.get("value", "")).strip()
                inc_val = str(proposal.payload.get("value", old_val)).strip()
                prev_vals = list(existing.payload.get("previous_values", []))
                if old_val and inc_val and old_val != inc_val and old_val not in prev_vals:
                    prev_vals.append(old_val)
                new_payload.update(proposal.payload)
                new_payload["previous_values"] = prev_vals
                new_payload["event_time"] = new_event_time

        elif existing.memory_type == "preference":
            # C01 + C03: Preserve conditions/exceptions while blocking late-arriving older history overwrite
            conds = list(
                dict.fromkeys(
                    [*existing.payload.get("conditions", []), *proposal.payload.get("conditions", [])]
                )
            )
            excs = list(
                dict.fromkeys(
                    [*existing.payload.get("exceptions", []), *proposal.payload.get("exceptions", [])]
                )
            )
            inc_dt = parse_iso_datetime(proposal.event_time)
            cur_dt = parse_iso_datetime(existing.event_time)
            if inc_dt is not None and cur_dt is not None and inc_dt < cur_dt:
                new_payload["conditions"] = conds
                new_payload["exceptions"] = excs
                backfilled_pref = MemoryItem(
                    memory_id=existing.memory_id,
                    space_id=existing.space_id,
                    permalink=existing.permalink,
                    memory_type=existing.memory_type,
                    title=existing.title,
                    summary=existing.summary,
                    payload=new_payload,
                    canonical_key=existing.canonical_key,
                    tags=tuple(dict.fromkeys([*existing.tags, *proposal.tags])),
                    assertion_kind=existing.assertion_kind,
                    status=existing.status,
                    version=existing.version,
                    user_locked=existing.user_locked,
                    rejected_inferences=existing.rejected_inferences,
                    evidences=merged_evs,
                    event_time=existing.event_time,
                    created_at=existing.created_at,
                )
                saved = self.memory_repo.update_cas(
                    backfilled_pref,
                    expected_version=expected_ver,
                    change_reason="Recorded historical preference context without overwriting newer preference",
                    actor_id=proposal.actor_id,
                    origin=proposal.origin,
                )
                if not (defer_active_vector_sync and saved.status == "active"):
                    self.vector_lifecycle.sync_memory(saved)
                return ChangeResult(
                    proposal_id=proposal_id,
                    status="skipped_older_event",
                    memory=saved,
                    message="Recorded historical preference context without overwriting newer preference",
                )
            new_payload.update(proposal.payload)
            new_payload["conditions"] = conds
            new_payload["exceptions"] = excs
            if excs and f"例外/限制" not in new_summary:
                new_summary = f"{new_summary}（例外/限制：{'; '.join(excs)}）"

        else:
            new_payload.update(proposal.payload)

        target_status = "pending_review" if proposal.require_review else existing.status
        updated = MemoryItem(
            memory_id=existing.memory_id,
            space_id=existing.space_id,
            permalink=proposal.permalink.strip("/") or existing.permalink,
            memory_type=existing.memory_type,
            title=new_title,
            summary=new_summary,
            payload=new_payload,
            canonical_key=proposal.canonical_key or existing.canonical_key,
            tags=tuple(dict.fromkeys([*existing.tags, *proposal.tags])),
            assertion_kind=proposal.assertion_kind or existing.assertion_kind,
            status=target_status,
            version=existing.version,
            user_locked=existing.user_locked or proposal.lock_for_user,
            rejected_inferences=existing.rejected_inferences,
            evidences=merged_evs,
            event_time=new_event_time,
            created_at=existing.created_at,
        )
        saved = self.memory_repo.update_cas(
            updated,
            expected_version=expected_ver,
            change_reason=proposal.reason or f"Updated via {proposal.origin}",
            actor_id=proposal.actor_id,
            origin=proposal.origin,
        )
        if proposal.relations or proposal.replace_relations:
            self.relation_repo.replace_outgoing(
                proposal.space_id,
                saved.memory_id,
                [(r.relation_type, r.target, r.context) for r in proposal.relations],
            )
        self.relation_repo.resolve_pending_inbound(saved)
        if not (defer_active_vector_sync and saved.status == "active"):
            self.vector_lifecycle.sync_memory(saved)
        return ChangeResult(
            proposal_id=proposal_id,
            status="applied",
            memory=saved,
            message="Memory updated",
        )

    def purge_legacy_forgotten_records(self) -> dict[str, int]:
        """Idempotently scrub pre-fix forgotten memories, legacy version snapshots, exclusive orphan notes, and traces."""
        import json as _json

        db = self.memory_repo.db
        stats = {
            "scrubbed_memories": 0,
            "scrubbed_versions": 0,
            "scrubbed_raw_notes": 0,
            "redacted_shared_notes": 0,
            "cascaded_inferred": 0,
            "redacted_traces": 0,
        }

        forgotten_rows = db.query_all(
            "SELECT memory_id, space_id, title, summary, payload_json, evidences_json FROM memories WHERE status = 'forgotten'"
        )
        # 1. Collect all historical note_ids & quotes referenced by forgotten memories BEFORE scrubbing versions
        legacy_links: dict[tuple[str, str], list[tuple[str, str]]] = {}
        # key=(space_id, memory_id) -> list[(note_id, quote)]
        for row in forgotten_rows:
            sp = str(row["space_id"])
            mid = str(row["memory_id"])
            refs: list[tuple[str, str]] = []
            for ev in _json.loads(row["evidences_json"] or "[]"):
                if isinstance(ev, dict) and ev.get("note_id"):
                    refs.append((str(ev["note_id"]), str(ev.get("quote") or "").strip()))
            ver_rows = db.query_all(
                "SELECT evidences_json FROM memory_versions WHERE space_id = ? AND memory_id = ?",
                (sp, mid),
            )
            for vr in ver_rows:
                for ev in _json.loads(vr["evidences_json"] or "[]"):
                    if isinstance(ev, dict) and ev.get("note_id"):
                        refs.append((str(ev["note_id"]), str(ev.get("quote") or "").strip()))
            legacy_links[(sp, mid)] = refs

            # Scrub memories row & FTS & links & vectors
            dirty_mem = (
                str(row["title"]) != "[FORGOTTEN]"
                or str(row["summary"]) != "[FORGOTTEN]"
                or str(row["evidences_json"]) != "[]"
                or str(row["payload_json"]) not in ("{}", "")
            )
            if dirty_mem:
                with db.transaction() as conn:
                    conn.execute(
                        """
                        UPDATE memories
                        SET title = '[FORGOTTEN]',
                            summary = '[FORGOTTEN]',
                            payload_json = '{}',
                            canonical_key = '',
                            tags_json = '[]',
                            evidences_json = '[]',
                            updated_at = ?
                        WHERE space_id = ? AND memory_id = ?
                        """,
                        (utc_now_iso(), sp, mid),
                    )
                    conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (mid,))
                    conn.execute("DELETE FROM memory_evidence_links WHERE memory_id = ?", (mid,))
                stats["scrubbed_memories"] += 1

            dirty_v = db.query_one(
                """
                SELECT COUNT(*) AS cnt FROM memory_versions
                WHERE space_id = ? AND memory_id = ?
                  AND (title != '[FORGOTTEN]' OR summary != '[FORGOTTEN]'
                       OR payload_json != '{}' OR evidences_json != '[]')
                """,
                (sp, mid),
            )
            if dirty_v and int(dirty_v["cnt"] or 0) > 0:
                self.memory_repo.scrub_forgotten_versions(sp, mid)
                stats["scrubbed_versions"] += int(dirty_v["cnt"] or 0)

            self.relation_repo.clear_inbound_on_inactive(sp, mid, redact_target_text=True)
            mem_obj = self.memory_repo.get(sp, mid, include_forgotten=True)
            if mem_obj is not None:
                self.vector_lifecycle.sync_memory(mem_obj)

        # 2. Scrub exclusive orphan RawNotes cited by now-forgotten memories (or partially redact shared notes)
        purged_notes_by_space: dict[str, set[str]] = {}
        seen_notes: set[tuple[str, str]] = set()
        for (sp, mid), refs in legacy_links.items():
            for nid, quote in refs:
                if (sp, nid) in seen_notes:
                    continue
                seen_notes.add((sp, nid))
                note = self.note_repo.get(sp, nid, include_forgotten=True)
                if note is None:
                    continue
                remaining_deps = self.memory_repo.find_dependent_memories(sp, nid)
                if not remaining_deps:
                    if not note.is_forgotten or note.content != "[FORGOTTEN]":
                        scrubbed_note = RawNote(
                            note_id=note.note_id,
                            space_id=note.space_id,
                            content="[FORGOTTEN]",
                            source_type=note.source_type,
                            actor_id="legacy_purge",
                            revision=note.revision + 1,
                            is_forgotten=True,
                            forgotten_spans=note.forgotten_spans,
                            event_time=note.event_time,
                            created_at=note.created_at,
                            updated_at=utc_now_iso(),
                        )
                        self.note_repo.save(scrubbed_note)
                        self.vector_lifecycle.sync_note(scrubbed_note)
                        stats["scrubbed_raw_notes"] += 1
                    purged_notes_by_space.setdefault(sp, set()).add(nid)
                elif quote and not note.is_forgotten:
                    sibling_blob = "\n".join(
                        f"{d.title}\n{d.summary}\n{d.payload}\n"
                        + "\n".join(e.quote for e in d.evidences)
                        for d in remaining_deps
                    )
                    if quote in note.visible_content() and quote not in sibling_blob:
                        new_spans = tuple(dict.fromkeys([*note.forgotten_spans, quote]))
                        redacted_note = RawNote(
                            note_id=note.note_id,
                            space_id=note.space_id,
                            content=note.content.replace(quote, "[REDACTED]"),
                            source_type=note.source_type,
                            actor_id="legacy_purge",
                            revision=note.revision + 1,
                            is_forgotten=False,
                            forgotten_spans=new_spans,
                            event_time=note.event_time,
                            created_at=note.created_at,
                            updated_at=utc_now_iso(),
                        )
                        self.note_repo.save(redacted_note)
                        self.vector_lifecycle.sync_note(redacted_note)
                        stats["redacted_shared_notes"] += 1

        # 3. Also scrub any already-forgotten RawNotes that still hold plaintext content
        dirty_notes = db.query_all(
            "SELECT note_id, space_id FROM raw_notes WHERE is_forgotten = 1 AND content != '[FORGOTTEN]'"
        )
        for nr in dirty_notes:
            sp = str(nr["space_id"])
            nid = str(nr["note_id"])
            note = self.note_repo.get(sp, nid, include_forgotten=True)
            if note is None:
                continue
            scrubbed_note = RawNote(
                note_id=note.note_id,
                space_id=note.space_id,
                content="[FORGOTTEN]",
                source_type=note.source_type,
                actor_id="legacy_purge",
                revision=note.revision + 1,
                is_forgotten=True,
                forgotten_spans=(),
                event_time=note.event_time,
                created_at=note.created_at,
                updated_at=utc_now_iso(),
            )
            self.note_repo.save(scrubbed_note)
            self.vector_lifecycle.sync_note(scrubbed_note)
            stats["scrubbed_raw_notes"] += 1
            purged_notes_by_space.setdefault(sp, set()).add(nid)

        # 4. Cascade-forget any active inferred Dream memories whose derived_from links are all broken
        spaces = {str(r["space_id"]) for r in forgotten_rows}
        for sp in spaces:
            for cand in self.memory_repo.list_memories(space_id=sp, status="active", limit=500):
                if cand.assertion_kind != "inferred":
                    continue
                out_rels = self.relation_repo.get_neighbors(sp, cand.memory_id)
                derived_edges = [
                    r
                    for r in out_rels
                    if r.from_memory_id == cand.memory_id and r.relation_type == "derived_from"
                ]
                if derived_edges and all(not r.to_memory_id for r in derived_edges):
                    self.apply(
                        ChangeProposal(
                            space_id=sp,
                            action="forget",
                            memory_type=cand.memory_type,
                            target_memory_id=cand.memory_id,
                            expected_version=cand.version,
                            actor_id="legacy_purge",
                            origin="user_direct",
                            reason="Legacy cleanup: underlying derived_from sources forgotten",
                        )
                    )
                    stats["cascaded_inferred"] += 1

        # 5. Redact traces & idempotency records referencing forgotten memories or purged notes
        for (sp, mid) in legacy_links:
            stats["redacted_traces"] += self.trace_repo.redact_for_forgotten(sp, memory_id=mid)
        for sp, nids in purged_notes_by_space.items():
            stats["redacted_traces"] += self.trace_repo.redact_for_forgotten(
                sp, note_ids=tuple(sorted(nids))
            )

        return stats
