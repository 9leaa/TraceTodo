"""Basic-Memory-style Markdown Knowledge View Export & Controlled Edit Reflow (`MarkdownSyncService`).

Design invariants (from FUSION_IMPLEMENTATION_PLAN §7):
1. Markdown files in `knowledge/<space_id>/` are derived views over canonical SQLite state.
   Deleting the entire folder and running `export_space()` reconstructs identical knowledge files.
2. Forgotten or superseded memories are automatically pruned from the export directory.
3. `.manifest.json` records exported `(version, sha256)` per file so re-scanning never creates
   sync loops, and concurrent edits detect version conflicts (`expected_version`) and save
   conflict drafts (`*.conflict-vN.md`) without clobbering newer database state.
4. Path traversal / symlink escapes outside `knowledge/<space_id>/` are strictly rejected.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from trace_memory.domain.memories import MemoryEvidence, MemoryItem, QUADRANT_LABELS, QUADRANTS
from trace_memory.domain.notes import RawNote, new_id
from trace_memory.domain.proposals import (
    ChangeProposal,
    ChangeResult,
    NotFoundError,
    PermissionDeniedError,
    RelationSpec,
    ValidationError,
    VersionConflictError,
)
from trace_memory.domain.relations import extract_relations_from_markdown
from trace_memory.services.change_service import ChangeService
from trace_memory.storage.repositories import (
    MemoryRepository,
    NoteRepository,
    RelationRepository,
    TraceRepository,
)
from trace_memory.storage.vector_store import VectorLifecycleService


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _parse_simple_frontmatter(markdown_text: str) -> tuple[dict[str, str], str]:
    lines = markdown_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, markdown_text
    end_idx = -1
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx == -1:
        raise ValidationError("Malformed YAML frontmatter: missing closing '---'")

    fm: dict[str, str] = {}
    for raw_line in lines[1:end_idx]:
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        fm[k.strip()] = v.strip().strip('"').strip("'")
    body = "\n".join(lines[end_idx + 1 :]).lstrip("\n")
    return fm, body


def _extract_edited_sections(body: str) -> tuple[str, str, dict[str, list[str]]]:
    """Extract `(# Title, ## Summary text, observations_by_category)` from edited Markdown."""
    title = ""
    summary_lines: list[str] = []
    observations: dict[str, list[str]] = {}
    current_section = "preamble"

    obs_re = re.compile(r"^\s*-\s*\[(?P<cat>[^\[\]\(\)]+)\]\s*(?P<val>.+?)\s*$")

    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not title:
            title = stripped[2:].strip()
            continue
        if stripped.startswith("## "):
            sec_name = stripped[3:].strip().lower()
            if "summary" in sec_name or "摘要" in sec_name:
                current_section = "summary"
            elif "observation" in sec_name or "观察" in sec_name or "属性" in sec_name:
                current_section = "observations"
            elif "relation" in sec_name or "关系" in sec_name:
                current_section = "relations"
            elif "source" in sec_name or "来源" in sec_name or "证据" in sec_name:
                current_section = "sources"
            else:
                current_section = "other"
            continue

        if current_section in ("preamble", "summary"):
            if stripped and not stripped.startswith("- [") and not stripped.startswith(">"):
                summary_lines.append(stripped)
        elif current_section == "observations":
            m = obs_re.match(line)
            if m:
                cat = m.group("cat").strip()
                val = m.group("val").strip()
                observations.setdefault(cat, []).append(val)

    return title, "\n".join(summary_lines).strip(), observations


class MarkdownSyncService:
    """Exports active memories to linked Markdown files and imports controlled external edits."""

    def __init__(
        self,
        knowledge_root: Path,
        note_repo: NoteRepository,
        memory_repo: MemoryRepository,
        relation_repo: RelationRepository,
        trace_repo: TraceRepository,
        vector_lifecycle: VectorLifecycleService,
        change_service: ChangeService,
    ) -> None:
        self.knowledge_root = Path(knowledge_root)
        self.note_repo = note_repo
        self.memory_repo = memory_repo
        self.relation_repo = relation_repo
        self.trace_repo = trace_repo
        self.vector_lifecycle = vector_lifecycle
        self.change_service = change_service

    def _space_root(self, space_id: str) -> Path:
        raw_sp = space_id.strip()
        safe_space = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]", "_", raw_sp)
        if not safe_space or safe_space in (".", ".."):
            raise ValidationError("Invalid space_id for export directory")
        if safe_space != raw_sp:
            digest = hashlib.sha1(raw_sp.encode("utf-8")).hexdigest()[:8]
            safe_space = f"{safe_space}_{digest}"
        root = (self.knowledge_root / safe_space).resolve()
        base = self.knowledge_root.resolve()
        if root != base and base not in root.parents:
            raise ValidationError("Space directory escaped knowledge_root")
        return root

    @staticmethod
    def _ensure_safe_child(space_root: Path, rel_path: str) -> Path:
        if Path(rel_path).is_absolute() or ".." in Path(rel_path).parts:
            raise ValidationError(f"Unsafe relative path rejected: {rel_path!r}")
        candidate = (space_root / rel_path).resolve()
        if space_root not in candidate.parents:
            raise ValidationError(f"Path traversal or symlink escape rejected: {rel_path!r}")
        return candidate

    def _render_memory_markdown(self, mem: MemoryItem) -> str:
        rels = [
            r
            for r in self.relation_repo.get_neighbors(mem.space_id, mem.memory_id)
            if r.from_memory_id == mem.memory_id
        ]
        tags_str = ", ".join(mem.tags)
        fm_lines = [
            "---",
            "schema_version: 1",
            f"memory_id: {mem.memory_id}",
            f"permalink: {mem.permalink}",
            f"memory_type: {mem.memory_type}",
            f"version: {mem.version}",
            f"assertion_kind: {mem.assertion_kind}",
            f"user_locked: {str(mem.user_locked).lower()}",
            f"tags: [{tags_str}]",
        ]
        if mem.memory_type == "task":
            fm_lines.append(f"task_status: {mem.payload.get('task_status', 'todo')}")
            fm_lines.append(f"quadrant: {mem.payload.get('quadrant', 'Q4_eliminate')}")
            fm_lines.append(f"importance: {mem.payload.get('importance', 3)}")
            fm_lines.append(f"urgency: {mem.payload.get('urgency', 3)}")
            if mem.payload.get("color_theme"):
                fm_lines.append(f"color_theme: {mem.payload.get('color_theme')}")
            if mem.payload.get("repeat_rule") and mem.payload.get("repeat_rule") != "never":
                fm_lines.append(f"repeat_rule: {mem.payload.get('repeat_rule')}")
        fm_lines.append("---")

        obs_lines: list[str] = []
        if mem.memory_type == "task":
            obs_lines.append(f"- [status] {mem.payload.get('task_status', 'todo')}")
            obs_lines.append(f"- [quadrant] {mem.payload.get('quadrant', 'Q4_eliminate')}")
            obs_lines.append(f"- [importance] {mem.payload.get('importance', 3)}")
            obs_lines.append(f"- [urgency] {mem.payload.get('urgency', 3)}")
            if mem.payload.get("due_at"):
                obs_lines.append(f"- [due_at] {mem.payload['due_at']}")
            if mem.payload.get("repeat_rule") and mem.payload.get("repeat_rule") != "never":
                obs_lines.append(f"- [repeat] {mem.payload['repeat_rule']}")
            if mem.payload.get("rollover_count"):
                obs_lines.append(f"- [rollover_count] {mem.payload['rollover_count']}")
            if mem.payload.get("supervision_enabled"):
                obs_lines.append(f"- [supervision] enabled (mode={mem.payload.get('task_mode', 'personal')})")
            if mem.payload.get("priority_reason"):
                obs_lines.append(f"- [priority_reason] {mem.payload['priority_reason']}")
        elif mem.memory_type == "preference":
            obs_lines.append(f"- [target] {mem.payload.get('target', '')}")
            obs_lines.append(f"- [polarity] {mem.payload.get('polarity', 'positive')}")
            for c in mem.payload.get("conditions", []):
                obs_lines.append(f"- [condition] {c}")
            for ex in mem.payload.get("exceptions", []):
                obs_lines.append(f"- [exception] {ex}")
        elif mem.memory_type == "semantic":
            obs_lines.append(f"- [subject] {mem.payload.get('subject', '')}")
            obs_lines.append(f"- [predicate] {mem.payload.get('predicate', '')}")
            obs_lines.append(f"- [value] {mem.payload.get('value', '')}")
        elif mem.memory_type == "episodic":
            obs_lines.append(f"- [event_summary] {mem.payload.get('event_summary', '')}")
            if mem.payload.get("occurred_at"):
                obs_lines.append(f"- [occurred_at] {mem.payload['occurred_at']}")

        rel_lines = [
            f"- {r.relation_type} [[{r.to_target_text}]]" + (f" ({r.context})" if r.context else "")
            for r in sorted(rels, key=lambda x: (x.relation_type, x.to_target_text))
        ]
        src_lines = [
            f"- `{ev.note_id}` (rev {ev.note_revision}): {ev.quote}"
            for ev in mem.evidences
            if self.note_repo.get(mem.space_id, ev.note_id) is not None
        ]

        sections = [
            "\n".join(fm_lines),
            "",
            f"# {mem.title}",
            "",
            "## Summary",
            mem.summary,
            "",
            "## Observations",
            "\n".join(obs_lines) if obs_lines else "- [note] structured item",
        ]
        if mem.memory_type == "task" and mem.payload.get("subtasks"):
            chk_lines = [
                f"- [{'x' if s.get('done') else ' '}] {s.get('title', '')}"
                for s in mem.payload["subtasks"]
                if isinstance(s, dict) and s.get("title")
            ]
            if chk_lines:
                sections.extend(["", "## Checklist", "\n".join(chk_lines)])
        if rel_lines:
            sections.extend(["", "## Relations", "\n".join(rel_lines)])
        if src_lines:
            sections.extend(["", "## Sources", "\n".join(src_lines)])

        return "\n".join(sections).rstrip() + "\n"

    def _render_index_markdown(self, space_id: str, memories: list[MemoryItem]) -> str:
        lines = [
            f"# Knowledge Base Index (`{space_id}`)",
            "",
            "> Derived view generated from canonical Trace-Memory state. Safe to browse in Obsidian.",
            "",
            "## Eisenhower 4-Quadrant Active Tasks",
        ]
        tasks = [
            m
            for m in memories
            if m.memory_type == "task" and m.payload.get("task_status") == "todo"
        ]
        for q in QUADRANTS:
            q_tasks = [t for t in tasks if t.payload.get("quadrant") == q]
            lines.append(f"### {QUADRANT_LABELS[q]} ({len(q_tasks)})")
            if not q_tasks:
                lines.append("- *(空)*")
            else:
                for t in sorted(
                    q_tasks, key=lambda x: float(x.payload.get("priority_score", 0.0)), reverse=True
                ):
                    lines.append(f"- [[{t.permalink}|{t.title}]] (Score: {t.payload.get('priority_score')})")
            lines.append("")

        for mtype, heading in (
            ("preference", "Preferences (偏好)"),
            ("semantic", "Semantic Facts & Insights (语义事实与归纳)"),
            ("episodic", "Episodic Experiences (情景经历)"),
        ):
            subset = [m for m in memories if m.memory_type == mtype]
            lines.append(f"## {heading} ({len(subset)})")
            for item in sorted(subset, key=lambda x: x.permalink):
                badge = " *(inferred)*" if item.assertion_kind == "inferred" else ""
                lines.append(f"- [[{item.permalink}|{item.title}]]{badge}: {item.summary}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def export_space(self, space_id: str) -> dict[str, Any]:
        """Deterministically materialize active space memories into `<knowledge_root>/<space_id>/`."""
        space_root = self._space_root(space_id)
        space_root.mkdir(parents=True, exist_ok=True)
        manifest_path = space_root / ".manifest.json"

        old_manifest: dict[str, Any] = {}
        if manifest_path.is_file():
            try:
                old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                old_manifest = {}

        active_memories = sorted(
            self.memory_repo.list_memories(space_id=space_id, status="active", limit=1000),
            key=lambda m: m.permalink,
        )

        new_files_map: dict[str, dict[str, Any]] = {}
        written_count = 0

        for mem in active_memories:
            rel_file = f"{mem.permalink}.md"
            target_path = self._ensure_safe_child(space_root, rel_file)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            rendered = self._render_memory_markdown(mem)
            digest = _sha256_text(rendered)

            if not target_path.is_file() or _sha256_text(target_path.read_text(encoding="utf-8")) != digest:
                target_path.write_text(rendered, encoding="utf-8")
                written_count += 1

            new_files_map[rel_file] = {
                "memory_id": mem.memory_id,
                "version": mem.version,
                "sha256": digest,
            }

        # Prune stale/forgotten files that were previously managed in `.manifest.json`
        removed_count = 0
        for old_rel in old_manifest.get("files", {}):
            if old_rel not in new_files_map:
                stale_path = self._ensure_safe_child(space_root, old_rel)
                if stale_path.is_file():
                    stale_path.unlink()
                    removed_count += 1

        index_content = self._render_index_markdown(space_id, active_memories)
        index_path = space_root / "index.md"
        index_digest = _sha256_text(index_content)
        if not index_path.is_file() or _sha256_text(index_path.read_text(encoding="utf-8")) != index_digest:
            index_path.write_text(index_content, encoding="utf-8")

        manifest_data = {
            "space_id": space_id,
            "index_sha256": index_digest,
            "files": new_files_map,
        }
        manifest_path.write_text(
            json.dumps(manifest_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return {
            "space_id": space_id,
            "export_dir": str(space_root),
            "active_count": len(active_memories),
            "written_count": written_count,
            "removed_stale_count": removed_count,
        }

    def render_memory_markdown(self, space_id: str, identifier: str) -> dict[str, Any]:
        """Render one active memory card into Obsidian-compatible Markdown without mutating state."""
        any_mem = self.memory_repo.get_any_space(identifier.strip())
        if any_mem is not None and any_mem.space_id != space_id:
            raise PermissionDeniedError("Access denied to memory in another space")
        mem = self.memory_repo.get(space_id, identifier)
        if mem is None:
            raise NotFoundError(f"Memory {identifier!r} not found")
        return {
            "memory_id": mem.memory_id,
            "version": mem.version,
            "permalink": mem.permalink,
            "title": mem.title,
            "markdown": self._render_memory_markdown(mem),
        }

    @staticmethod
    def _build_payload_patch(existing: MemoryItem, obs: dict[str, list[str]]) -> dict[str, Any]:
        payload_patch: dict[str, Any] = dict(existing.payload)
        if existing.memory_type == "task":
            if "quadrant" in obs:
                payload_patch["quadrant"] = obs["quadrant"][0]
            if "importance" in obs and obs["importance"][0].isdigit():
                payload_patch["importance"] = int(obs["importance"][0])
            if "urgency" in obs and obs["urgency"][0].isdigit():
                payload_patch["urgency"] = int(obs["urgency"][0])
            if "status" in obs and obs["status"][0] in ("todo", "done"):
                payload_patch["task_status"] = obs["status"][0]
        elif existing.memory_type == "semantic":
            if "subject" in obs:
                payload_patch["subject"] = obs["subject"][0]
            if "predicate" in obs:
                payload_patch["predicate"] = obs["predicate"][0]
            if "value" in obs:
                payload_patch["value"] = obs["value"][0]
        elif existing.memory_type == "preference":
            if "target" in obs:
                payload_patch["target"] = obs["target"][0]
            if "polarity" in obs and obs["polarity"][0] in ("positive", "negative", "neutral"):
                payload_patch["polarity"] = obs["polarity"][0]
            if "exception" in obs:
                payload_patch["exceptions"] = obs["exception"]
            if "condition" in obs:
                payload_patch["conditions"] = obs["condition"]
        elif existing.memory_type == "episodic":
            if "event_summary" in obs:
                payload_patch["event_summary"] = obs["event_summary"][0]
            if "occurred_at" in obs:
                payload_patch["occurred_at"] = obs["occurred_at"][0]
        return payload_patch

    def _apply_parsed_markdown_text(
        self,
        *,
        space_id: str,
        existing: MemoryItem,
        raw_text: str,
        expected_version: int,
        rel_label: str,
        actor_id: str,
        origin: str = "markdown_sync",
    ) -> ChangeResult:
        if len(raw_text) > 200_000:
            raise ValidationError(f"Markdown content exceeds size limit: {rel_label}")

        _fm, body = _parse_simple_frontmatter(raw_text)
        edited_title, edited_summary, obs = _extract_edited_sections(body)
        rel_body = re.split(r"^##\s+(?:Sources|来源|证据)\b", body, maxsplit=1, flags=re.M | re.I)[0]
        extracted_rels = [
            RelationSpec(relation_type=rt, target=tgt, context=ctx)
            for rt, tgt, ctx in extract_relations_from_markdown(rel_body)
        ]
        payload_patch = self._build_payload_patch(existing, obs)

        # Pre-check CAS version before saving synthetic [Markdown Edit] RawNote so failed CAS leaves zero orphan notes
        if int(expected_version) != existing.version:
            raise VersionConflictError(existing.memory_id, int(expected_version), existing.version)

        edit_note = RawNote(
            note_id=new_id("note"),
            space_id=space_id,
            content=f"[Markdown Edit {rel_label}] {edited_summary or edited_title}",
            source_type="markdown_edit",
            actor_id=actor_id,
        )
        self.note_repo.save(edit_note)
        self.vector_lifecycle.sync_note(edit_note)

        proposal = ChangeProposal(
            space_id=space_id,
            action="update",
            memory_type=existing.memory_type,
            target_memory_id=existing.memory_id,
            expected_version=expected_version,
            actor_id=actor_id,
            origin=origin,
            title=edited_title or existing.title,
            summary=edited_summary or existing.summary,
            payload=payload_patch,
            lock_for_user=True,
            evidences=(
                MemoryEvidence(
                    note_id=edit_note.note_id,
                    note_revision=1,
                    quote=(edited_summary or edited_title)[:160],
                ),
            ),
            relations=tuple(extracted_rels) if extracted_rels else (),
            replace_relations=True,
            reason=f"Controlled reflow from {rel_label} (base v{expected_version})",
        )
        return self.change_service.apply(proposal)

    def apply_markdown_edit(
        self,
        space_id: str,
        identifier: str,
        markdown_text: str,
        expected_version: int | None = None,
        actor_id: str = "markdown_editor",
        export_to_disk: bool = True,
    ) -> ChangeResult:
        """Apply direct in-app/Web Markdown edits with CAS guard and optional derived disk sync."""
        any_mem = self.memory_repo.get_any_space(identifier.strip())
        if any_mem is not None and any_mem.space_id != space_id:
            raise PermissionDeniedError("Access denied to target memory in another space")
        existing = self.memory_repo.get(space_id, identifier)
        if existing is None:
            raise NotFoundError(f"Memory {identifier!r} not found")

        exp_ver = existing.version if expected_version is None else int(expected_version)
        res = self._apply_parsed_markdown_text(
            space_id=space_id,
            existing=existing,
            raw_text=markdown_text,
            expected_version=exp_ver,
            rel_label=f"{existing.permalink}.md",
            actor_id=actor_id,
            origin="markdown_sync",
        )
        if export_to_disk:
            self.export_space(space_id)
        self.trace_repo.record_trace(
            space_id=space_id,
            operation="markdown_edit",
            input_summary=f"Direct Markdown edit {existing.permalink} (v{exp_ver})",
            details={"memory_id": existing.memory_id, "new_version": res.memory.version if res.memory else exp_ver},
        )
        return res

    def sync_external_edits(self, space_id: str, actor_id: str = "markdown_editor") -> dict[str, Any]:
        """Scan `<knowledge_root>/<space_id>/` for human edits and submit CAS-guarded proposals."""
        space_root = self._space_root(space_id)
        manifest_path = space_root / ".manifest.json"
        if not space_root.is_dir() or not manifest_path.is_file():
            return {
                "space_id": space_id,
                "applied": [],
                "conflicts": [],
                "unchanged": 0,
            }

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValidationError(f"Corrupt .manifest.json in {space_id}") from exc
        files_meta: dict[str, dict[str, Any]] = manifest.get("files", {})

        applied_ids: list[str] = []
        conflicts: list[dict[str, Any]] = []
        unchanged = 0

        for rel_file, meta in sorted(files_meta.items()):
            raw_child = space_root / rel_file
            if raw_child.is_symlink():
                raise ValidationError(f"Symlink escape rejected during sync: {rel_file}")
            file_path = self._ensure_safe_child(space_root, rel_file)
            if not file_path.is_file():
                # Deleting a view file NEVER triggers memory forgetting (§7.2)!
                continue

            raw_text = file_path.read_text(encoding="utf-8")
            current_sha = _sha256_text(raw_text)
            if current_sha == meta.get("sha256"):
                unchanged += 1
                continue

            # Use authoritative memory_id and baseline version from manifest (cannot spoof owner/space)
            memory_id = str(meta["memory_id"])
            expected_version = int(meta["version"])

            existing = self.memory_repo.get(space_id, memory_id)
            if existing is None:
                continue

            try:
                res = self._apply_parsed_markdown_text(
                    space_id=space_id,
                    existing=existing,
                    raw_text=raw_text,
                    expected_version=expected_version,
                    rel_label=rel_file,
                    actor_id=actor_id,
                    origin="markdown_sync",
                )
                if res.memory:
                    applied_ids.append(res.memory.memory_id)
            except VersionConflictError as exc:
                # Preserve user's draft in `*.conflict-vN.md` and restore authoritative file on re-export!
                conflict_file = file_path.with_name(
                    f"{file_path.stem}.conflict-v{expected_version}.md"
                )
                conflict_file.write_text(raw_text, encoding="utf-8")
                conflicts.append(
                    {
                        "memory_id": memory_id,
                        "file": rel_file,
                        "expected_version": exc.expected,
                        "actual_version": exc.actual,
                        "draft_saved_to": str(conflict_file.relative_to(space_root)),
                    }
                )

        # Re-export canonical versions so `.manifest.json` catches up cleanly (prevents re-trigger loop!)
        self.export_space(space_id)

        self.trace_repo.record_trace(
            space_id=space_id,
            operation="markdown_sync",
            input_summary=f"Reflow applied={len(applied_ids)} conflicts={len(conflicts)}",
            details={"applied": applied_ids, "conflicts": conflicts, "unchanged": unchanged},
        )
        return {
            "space_id": space_id,
            "applied": applied_ids,
            "conflicts": conflicts,
            "unchanged": unchanged,
        }
