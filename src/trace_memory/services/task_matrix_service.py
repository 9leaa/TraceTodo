"""Eisenhower 4-Quadrant Task Matrix, Dynamic Urgency Escalation, and Memory-Driven Diagnosis."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import re
from typing import TYPE_CHECKING, Any

from trace_memory.domain.memories import (
    COLOR_THEMES,
    QUADRANT_LABELS,
    QUADRANTS,
    REPEAT_RULES,
    TASK_MODES,
    MemoryEvidence,
    MemoryItem,
    compute_effective_urgency_and_quadrant,
    compute_next_repeat_due_at,
    parse_iso_datetime,
)
from trace_memory.domain.notes import RawNote, beijing_now, new_id, to_beijing, utc_now_iso
from trace_memory.domain.proposals import (
    ChangeProposal,
    ChangeResult,
    NotFoundError,
    PermissionDeniedError,
    RelationSpec,
    ValidationError,
    VersionConflictError,
)
from trace_memory.domain.relations import slugify_permalink
from trace_memory.services.change_service import ChangeService
from trace_memory.storage.repositories import (
    MemoryRepository,
    NoteRepository,
    TraceRepository,
)

if TYPE_CHECKING:
    from trace_memory.llm.protocol import LLMProvider
    from trace_memory.services.retrieval_service import RetrievalService
    from trace_memory.storage.vector_store import VectorLifecycleService


class TaskMatrixService:
    """Provides multi-axis task sorting, 4-quadrant matrix grouping, DuoDo workflows, and AI insights."""

    def __init__(
        self,
        memory_repo: MemoryRepository,
        change_service: ChangeService,
        note_repo: NoteRepository | None = None,
        vector_lifecycle: VectorLifecycleService | None = None,
        llm: LLMProvider | None = None,
        retrieval_service: RetrievalService | None = None,
        trace_repo: TraceRepository | None = None,
    ) -> None:
        self.memory_repo = memory_repo
        self.change_service = change_service
        self.note_repo = note_repo
        self.vector_lifecycle = vector_lifecycle
        self.llm = llm
        self.retrieval_service = retrieval_service
        self.trace_repo = trace_repo

    def _project_live_task_dict(
        self, item: MemoryItem, now: datetime
    ) -> dict[str, Any]:
        """Recompute dynamic deadline urgency at read time unless user locked the quadrant."""
        d = item.to_dict()
        payload = dict(d["payload"])
        imp = int(payload.get("importance", 3))
        base_urg = int(payload.get("urgency", 3))
        due_at = str(payload.get("due_at", "") or "")
        has_blockers = bool(payload.get("blockers"))

        eff_urg, auto_quad, auto_score = compute_effective_urgency_and_quadrant(
            imp, base_urg, due_at, now=now, has_blockers=has_blockers
        )
        payload["effective_urgency"] = eff_urg

        if item.user_locked:
            # Respect user's manually chosen quadrant, while still keeping score fresh within it
            quad = payload.get("quadrant", auto_quad)
            quad_base = {
                "Q1_do_first": 80.0,
                "Q2_schedule": 60.0,
                "Q3_delegate": 40.0,
                "Q4_eliminate": 15.0,
            }.get(quad, 40.0)
            payload["priority_score"] = round(quad_base + imp * 5.0 + eff_urg * 4.0, 2)
        else:
            payload["quadrant"] = auto_quad
            payload["quadrant_label"] = QUADRANT_LABELS.get(auto_quad, auto_quad)
            payload["priority_score"] = auto_score

        due_dt = parse_iso_datetime(due_at)
        if due_dt is not None:
            hours_left = round((due_dt - now).total_seconds() / 3600.0, 1)
            payload["hours_until_due"] = hours_left
            payload["is_overdue"] = hours_left < 0 and payload.get("task_status") == "todo"
        else:
            payload["hours_until_due"] = None
            payload["is_overdue"] = False

        d["payload"] = payload
        return d

    def list_sorted_tasks(
        self,
        space_id: str,
        task_status: str = "todo",  # "todo" | "done" | "all"
        sort_by: str = "priority",  # "priority" | "urgency" | "importance" | "updated"
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        ref_now = to_beijing(now) if now is not None else beijing_now()
        raw_items = self.memory_repo.list_memories(
            space_id=space_id, memory_type="task", status="active", limit=300
        )
        projected = [self._project_live_task_dict(m, ref_now) for m in raw_items]
        if task_status in ("todo", "done"):
            projected = [
                t for t in projected if t["payload"].get("task_status") == task_status
            ]
        self._sort_projected_tasks(projected, sort_by)
        return projected

    @staticmethod
    def _sort_projected_tasks(projected: list[dict[str, Any]], sort_by: str) -> None:
        def _due_sort_key(t: dict[str, Any]) -> float:
            h = t["payload"].get("hours_until_due")
            return float(h) if h is not None else 999999.0

        if sort_by == "urgency":
            projected.sort(
                key=lambda t: (
                    t["payload"].get("task_status") == "todo",
                    int(t["payload"].get("effective_urgency", 1)),
                    -_due_sort_key(t),
                    float(t["payload"].get("priority_score", 0.0)),
                ),
                reverse=True,
            )
        elif sort_by == "importance":
            projected.sort(
                key=lambda t: (
                    t["payload"].get("task_status") == "todo",
                    int(t["payload"].get("importance", 1)),
                    float(t["payload"].get("priority_score", 0.0)),
                ),
                reverse=True,
            )
        elif sort_by == "updated":
            projected.sort(key=lambda t: t["updated_at"], reverse=True)
        else:
            # Default: composite priority_score
            projected.sort(
                key=lambda t: (
                    t["payload"].get("task_status") == "todo",
                    float(t["payload"].get("priority_score", 0.0)),
                    -_due_sort_key(t),
                ),
                reverse=True,
            )

    def query_task_page(
        self,
        space_id: str,
        *,
        task_status: str,
        quadrants: set[str],
        due_on: date | None,
        limit: int,
        offset: int,
        now: datetime,
    ) -> tuple[tuple[MemoryItem, ...], int]:
        """Read a page with live quadrants without running task maintenance."""
        items = self.memory_repo.list_memories(
            space_id=space_id, memory_type="task", status="active", limit=None
        )
        by_id = {
            m.memory_id: m for m in items
            if task_status == "all" or m.payload.get("task_status", "todo") == task_status
        }
        matches = []
        for item in by_id.values():
            if due_on is not None:
                due = parse_iso_datetime(str(item.payload.get("due_at") or ""))
                if due is None or due.date() != due_on:
                    continue
            task = self._project_live_task_dict(item, now)
            if not quadrants or task["payload"]["quadrant"] in quadrants:
                matches.append(task)

        # Stable tie-breaker for paging. The priority sort preserves this order for ties.
        matches.sort(key=lambda task: task["memory_id"])
        self._sort_projected_tasks(matches, "priority")
        page = tuple(
            replace(by_id[task["memory_id"]], payload=task["payload"])
            for task in matches[offset : offset + limit]
        )
        return page, len(matches)

    def apply_auto_rollover(
        self, space_id: str, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Roll over unfinished `auto_rollover=True` tasks whose due date is before today."""
        ref_now = to_beijing(now) if now is not None else beijing_now()
        today_date = ref_now.date()
        today_str = today_date.isoformat()

        rolled: list[dict[str, Any]] = []
        raw_items = self.memory_repo.list_memories(
            space_id=space_id, memory_type="task", status="active", limit=300
        )
        for item in raw_items:
            p = item.payload
            if p.get("task_status") != "todo" or not p.get("auto_rollover"):
                continue
            if str(p.get("last_rollover_date", "")) == today_str:
                continue
            due_at = str(p.get("due_at", "") or "")
            if not due_at:
                continue
            due_dt = parse_iso_datetime(due_at)
            if due_dt is None:
                continue
            due_local = due_dt.astimezone(ref_now.tzinfo)
            if due_local.date() >= today_date:
                continue

            new_rc = int(p.get("rollover_count", 0) or 0) + 1
            patch_start_at: str | None = None
            if p.get("is_all_day", True):
                next_due_dt = ref_now.replace(hour=23, minute=59, second=59, microsecond=0)
                next_due = next_due_dt.isoformat()
            else:
                next_due_dt = ref_now.replace(
                    hour=due_local.hour,
                    minute=due_local.minute,
                    second=due_local.second,
                    microsecond=0,
                )
                next_due = next_due_dt.isoformat()
                start_raw = str(p.get("start_at", "") or "").strip()
                if start_raw:
                    start_dt = parse_iso_datetime(start_raw)
                    if start_dt is not None:
                        start_local = start_dt.astimezone(ref_now.tzinfo)
                        span = due_local - start_local
                        if span.total_seconds() >= 0:
                            next_start_dt = next_due_dt - span
                        else:
                            next_start_dt = ref_now.replace(
                                hour=start_local.hour,
                                minute=start_local.minute,
                                second=start_local.second,
                                microsecond=0,
                            )
                        patch_start_at = next_start_dt.replace(microsecond=0).isoformat()

            imp = int(p.get("importance", 3))
            urg = int(p.get("urgency", 3))
            _, auto_quad, auto_score = compute_effective_urgency_and_quadrant(
                imp, urg, next_due, now=ref_now, has_blockers=bool(p.get("blockers"))
            )
            patch: dict[str, Any] = {
                "due_at": next_due,
                "rollover_count": new_rc,
                "last_rollover_date": today_str,
            }
            if patch_start_at is not None:
                patch["start_at"] = patch_start_at
            if not item.user_locked:
                patch["quadrant"] = auto_quad
                patch["priority_score"] = auto_score

            res = self.change_service.apply(
                ChangeProposal(
                    space_id=space_id,
                    action="update",
                    memory_type="task",
                    target_memory_id=item.memory_id,
                    expected_version=item.version,
                    actor_id="rollover_scheduler",
                    origin="system_rollover",
                    payload=patch,
                    reason=f"跨日自动顺延至 {today_str}（第 {new_rc} 次）",
                )
            )
            if res.memory is not None:
                rolled.append(res.memory.to_dict())
        return rolled

    def refresh_due_task_statuses(
        self, space_id: str, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Persist due-date urgency/quadrant escalations & overdue flags for active todo tasks."""
        ref_now = to_beijing(now) if now is not None else beijing_now()
        refreshed: list[dict[str, Any]] = []
        raw_items = self.memory_repo.list_memories(
            space_id=space_id, memory_type="task", status="active", limit=300
        )
        for item in raw_items:
            p = item.payload
            if p.get("task_status") != "todo":
                continue
            due_at = str(p.get("due_at", "") or "")
            if not due_at:
                continue
            due_dt = parse_iso_datetime(due_at)
            if due_dt is None:
                continue

            imp = int(p.get("importance", 3))
            base_urg = int(p.get("urgency", 3))
            eff_urg, auto_quad, auto_score = compute_effective_urgency_and_quadrant(
                imp, base_urg, due_at, now=ref_now, has_blockers=bool(p.get("blockers"))
            )
            hours_left = (due_dt - ref_now).total_seconds() / 3600.0
            is_overdue = hours_left <= 0

            stored_quad = str(p.get("quadrant", auto_quad))
            stored_score = float(p.get("priority_score", 0.0) or 0.0)
            target_quad = stored_quad if item.user_locked else auto_quad

            needs_update = (
                target_quad != stored_quad
                or abs(auto_score - stored_score) >= 1.5
                or (is_overdue and "已逾期" not in str(p.get("priority_reason", "")))
            )
            if not needs_update:
                continue

            patch: dict[str, Any] = {
                "priority_score": auto_score,
            }
            if not item.user_locked:
                patch["quadrant"] = auto_quad
            if is_overdue:
                old_r = str(p.get("priority_reason", "")).strip().rstrip("。；;，, ")
                if "已逾期" not in old_r:
                    patch["priority_reason"] = (
                        f"{old_r}；任务已逾期自动提权" if old_r else "任务已逾期自动提权"
                    )
            elif eff_urg > base_urg:
                old_r = str(p.get("priority_reason", "")).strip().rstrip("。；;，, ")
                if "临近截止" not in old_r:
                    patch["priority_reason"] = (
                        f"{old_r}；临近截止自动提权" if old_r else "临近截止自动提权"
                    )

            res = self.change_service.apply(
                ChangeProposal(
                    space_id=space_id,
                    action="update",
                    memory_type="task",
                    target_memory_id=item.memory_id,
                    expected_version=item.version,
                    actor_id="daily_due_scheduler",
                    origin="system_daily_check",
                    payload=patch,
                    reason=f"每日到期状态巡检刷新（{ref_now.strftime('%Y-%m-%d %H:%M')}）",
                )
            )
            if res.memory is not None:
                refreshed.append(res.memory.to_dict())
        return refreshed

    def build_matrix_analysis(
        self,
        space_id: str,
        include_done: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Build 2x2 Eisenhower Matrix + Memory-Driven Workload Diagnosis & Next Actions."""
        ref_now = to_beijing(now) if now is not None else beijing_now()
        rolled_tasks = self.apply_auto_rollover(space_id, now=ref_now)
        status_filter = "all" if include_done else "todo"
        sorted_tasks = self.list_sorted_tasks(
            space_id=space_id, task_status=status_filter, sort_by="priority", now=ref_now
        )

        quadrants: dict[str, dict[str, Any]] = {
            q: {
                "quadrant": q,
                "label": QUADRANT_LABELS[q],
                "count": 0,
                "tasks": [],
            }
            for q in QUADRANTS
        }

        todo_tasks = [t for t in sorted_tasks if t["payload"].get("task_status") == "todo"]
        done_count = len(
            [
                m
                for m in self.memory_repo.list_memories(
                    space_id=space_id, memory_type="task", status="active", limit=300
                )
                if m.payload.get("task_status") == "done"
            ]
        )

        for t in sorted_tasks:
            q = t["payload"].get("quadrant", "Q4_eliminate")
            if q not in quadrants:
                q = "Q4_eliminate"
            quadrants[q]["tasks"].append(t)
            if t["payload"].get("task_status") == "todo":
                quadrants[q]["count"] += 1

        total_todo = len(todo_tasks)
        q1_cnt = quadrants["Q1_do_first"]["count"]
        q2_cnt = quadrants["Q2_schedule"]["count"]
        q3_cnt = quadrants["Q3_delegate"]["count"]
        q4_cnt = quadrants["Q4_eliminate"]["count"]
        overdue_cnt = sum(1 for t in todo_tasks if t["payload"].get("is_overdue"))
        high_rollover_tasks = [
            t for t in todo_tasks if int(t["payload"].get("rollover_count", 0) or 0) >= 3
        ]

        # Memory-driven context: read active preferences & key facts
        prefs = self.memory_repo.list_memories(
            space_id=space_id, memory_type="preference", status="active", limit=10
        )
        pref_summaries = [p.summary for p in prefs[:4]]

        diagnostics: list[str] = []
        if total_todo == 0:
            diagnostics.append("当前没有未完成的待办事项，可以直接在上方输入框用自然语言记录或点击「+ 新建任务」。")
        else:
            if overdue_cnt > 0:
                diagnostics.append(f"⚠️ 有 {overdue_cnt} 项待办已超过截止时间，已自动提权至紧急队列，请优先清理或重新排期。")
            for rt in high_rollover_tasks[:2]:
                rc = int(rt["payload"].get("rollover_count", 0))
                diagnostics.append(
                    f"↻ 《{rt['title']}》已连续顺延 {rc} 天，建议用「✨ AI 拆解」拆小颗粒度，或移入 Q4 精简放弃。"
                )
            if q1_cnt >= 3 or (total_todo >= 4 and q1_cnt / total_todo > 0.5):
                diagnostics.append(
                    f"🔥 Q1（重要且紧急）积压 {q1_cnt} 项（占比偏高），容易陷入救火模式；建议今日集中精力先攻克排名前 2 项。"
                )
            elif q1_cnt > 0:
                diagnostics.append(f"🎯 当前有 {q1_cnt} 项 Q1 核心急事，建议作为今日第一顺位立即执行。")

            if q2_cnt == 0 and total_todo >= 2:
                diagnostics.append(
                    "💡 Q2（重要不紧急）当前为空：建议补充长期高价值规划（如架构演进、技术沉淀），避免只做短期琐事。"
                )
            elif q2_cnt > 0:
                diagnostics.append(
                    f"📅 Q2（重要不紧急）有 {q2_cnt} 项长期高价值事项，建议在日历中预留整块免打扰专注时间推进。"
                )

            if q3_cnt > 0:
                diagnostics.append(
                    f"⚡ Q3（紧急不重要）有 {q3_cnt} 项临时协作/事务性杂事，建议控制在 15 分钟内速办或转交他人。"
                )
            if q4_cnt > 0:
                diagnostics.append(
                    f"🧹 Q4（不重要不紧急）有 {q4_cnt} 项低优事项，若长期无需推进可直接点击「取消/精简」。"
                )

        if pref_summaries:
            diagnostics.append(
                f"🧠 已关联 {len(prefs)} 条长期偏好记忆参与优先级校准（如：{pref_summaries[0]}）。"
            )

        recommended_focus = [
            {
                "memory_id": t["memory_id"],
                "title": t["title"],
                "quadrant": t["payload"]["quadrant"],
                "quadrant_label": t["payload"]["quadrant_label"],
                "priority_score": t["payload"]["priority_score"],
                "reason": t["payload"].get("priority_reason") or "高综合优先级",
                "evidence_count": len(t.get("evidences", [])),
            }
            for t in todo_tasks[:3]
        ]

        return {
            "summary_counts": {
                "total_todo": total_todo,
                "total_done": done_count,
                "overdue": overdue_cnt,
                "Q1_do_first": q1_cnt,
                "Q2_schedule": q2_cnt,
                "Q3_delegate": q3_cnt,
                "Q4_eliminate": q4_cnt,
            },
            "quadrants": quadrants,
            "sorted_tasks": sorted_tasks,
            "recommended_focus": recommended_focus,
            "diagnostics": diagnostics,
            "preference_context": pref_summaries,
            "rolled_over_today": len(rolled_tasks),
        }

    def update_task_quadrant_or_priority(
        self,
        space_id: str,
        memory_id: str,
        expected_version: int | None = None,
        importance: int | None = None,
        urgency: int | None = None,
        quadrant: str | None = None,
        due_at: str | None = None,
        actor_id: str = "user",
    ) -> ChangeResult:
        patch: dict[str, Any] = {}
        if importance is not None:
            patch["importance"] = int(importance)
        if urgency is not None:
            patch["urgency"] = int(urgency)
        if quadrant is not None and quadrant in QUADRANTS:
            patch["quadrant"] = quadrant
            # Align imp/urg when user drags a card directly to another quadrant without specifying numbers
            if importance is None and urgency is None:
                quad_defaults = {
                    "Q1_do_first": (5, 5),
                    "Q2_schedule": (5, 2),
                    "Q3_delegate": (2, 5),
                    "Q4_eliminate": (2, 2),
                }
                patch["importance"], patch["urgency"] = quad_defaults[quadrant]
            patch["priority_reason"] = f"用户手动调整至 {QUADRANT_LABELS[quadrant]}"
        if due_at is not None:
            patch["due_at"] = due_at

        return self.change_service.apply(
            ChangeProposal(
                space_id=space_id,
                action="update_quadrant",
                memory_type="task",
                target_memory_id=memory_id,
                expected_version=expected_version,
                actor_id=actor_id,
                origin="user_direct",
                payload=patch,
                lock_for_user=True,
                reason="Manual priority/quadrant adjustment",
            )
        )

    def transition_task_state(
        self,
        space_id: str,
        memory_id: str,
        action: str,  # "complete_task" | "cancel_task" | "reopen_task"
        expected_version: int | None = None,
        actor_id: str = "user",
    ) -> ChangeResult:
        return self.change_service.apply(
            ChangeProposal(
                space_id=space_id,
                action=action,
                memory_type="task",
                target_memory_id=memory_id,
                expected_version=expected_version,
                actor_id=actor_id,
                origin="user_direct",
                reason=f"User triggered {action}",
            )
        )

    @staticmethod
    def _parse_int(raw: Any, default_val: int, name: str) -> int:
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return default_val
        if isinstance(raw, bool):
            raise ValidationError(f"参数 {name} 必须为整数")
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"参数 {name} 必须为整数") from exc

    def transition_task_state_with_repeat(
        self,
        space_id: str,
        memory_id: str,
        action: str,
        expected_version: int | None = None,
        actor_id: str = "user",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Transition task state and spawn next recurring cycle when completing a repeating task."""
        prev_task = self.memory_repo.get(space_id, memory_id)
        was_done = prev_task is not None and prev_task.payload.get("task_status") == "done"
        res = self.transition_task_state(
            space_id=space_id,
            memory_id=memory_id,
            action=action,
            expected_version=expected_version,
            actor_id=actor_id,
        )
        next_task_dict: dict[str, Any] | None = None
        if (
            action == "complete_task"
            and res.memory is not None
            and res.status != "idempotent_replay"
            and not was_done
        ):
            p = res.memory.payload
            repeat_rule = str(p.get("repeat_rule", "never") or "never")
            if repeat_rule in ("daily", "weekdays", "weekly", "monthly"):
                next_due = compute_next_repeat_due_at(
                    str(p.get("due_at", "") or ""), repeat_rule, ref_now=now
                )
                reset_subs = [
                    {"id": s["id"], "title": s["title"], "done": False}
                    for s in p.get("subtasks", [])
                    if isinstance(s, dict) and s.get("title")
                ]
                next_res = self.save_task_card(
                    space_id=space_id,
                    data={
                        "title": res.memory.title,
                        "summary": res.memory.summary,
                        "importance": p.get("importance", 3),
                        "urgency": p.get("urgency", 3),
                        "due_at": next_due,
                        "is_all_day": p.get("is_all_day", True),
                        "repeat_rule": repeat_rule,
                        "reminder_offset_minutes": p.get("reminder_offset_minutes"),
                        "color_theme": p.get("color_theme", "sky"),
                        "task_mode": p.get("task_mode", "personal"),
                        "supervision_enabled": p.get("supervision_enabled", False),
                        "assignee": p.get("assignee", "self"),
                        "auto_rollover": p.get("auto_rollover", False),
                        "subtasks": reset_subs,
                        "focus_lock_enabled": p.get("focus_lock_enabled", False),
                        "focus_minutes": p.get("focus_minutes", 25),
                        "priority_reason": f"周期重复 ({repeat_rule}) 自动生成下一期",
                        "canonical_key": f"task:repeat:{res.memory.memory_id}:{next_due[:10]}:{new_id('cyc')[-4:]}",
                        "relations": [
                            {
                                "relation_type": "next_cycle_of",
                                "target": res.memory.permalink,
                                "context": f"周期重复 ({repeat_rule})",
                            }
                        ],
                    },
                    actor_id=actor_id,
                )
                if next_res.memory is not None:
                    next_task_dict = next_res.memory.to_dict()

        out = res.to_dict()
        out["next_task"] = next_task_dict
        return out

    def save_task_card(
        self,
        space_id: str,
        data: dict[str, Any],
        actor_id: str = "user",
    ) -> ChangeResult:
        """Create or update a rich DuoDo-style Task Card with traceable ground-truth RawNote."""
        target_id = str(data.get("target_memory_id") or data.get("memory_id") or "").strip()
        title = str(data.get("title") or "").strip()
        if not target_id and not title:
            raise ValidationError("任务标题不能为空")

        existing: MemoryItem | None = None
        if target_id:
            any_mem = self.memory_repo.get_any_space(target_id)
            if any_mem is not None and any_mem.space_id != space_id:
                raise PermissionDeniedError("Access denied to target memory in another space")
            existing = self.memory_repo.get(space_id, target_id, include_forgotten=True)
            if existing is None:
                raise NotFoundError(f"Task {target_id!r} not found")
            if existing.status == "forgotten":
                raise ValidationError("已遗忘的记忆不可恢复或修改")

        exp_ver_raw = data.get("expected_version")
        exp_ver: int | None = (
            self._parse_int(exp_ver_raw, 1, "expected_version")
            if exp_ver_raw is not None and str(exp_ver_raw).strip() != ""
            else None
        )
        if existing is not None and exp_ver is not None and exp_ver != existing.version:
            raise VersionConflictError(existing.memory_id, exp_ver, existing.version)

        base_payload = dict(existing.payload) if existing else {}
        raw_imp = data.get("importance")
        if "importance" in data and raw_imp is not None and str(raw_imp).strip() != "":
            imp = self._parse_int(raw_imp, 3, "importance")
            if not 1 <= imp <= 5:
                raise ValidationError("importance 必须在 1 到 5 之间")
        else:
            imp = max(1, min(5, self._parse_int(base_payload.get("importance", 3), 3, "importance")))

        raw_urg = data.get("urgency")
        if "urgency" in data and raw_urg is not None and str(raw_urg).strip() != "":
            urg = self._parse_int(raw_urg, 3, "urgency")
            if not 1 <= urg <= 5:
                raise ValidationError("urgency 必须在 1 到 5 之间")
        else:
            urg = max(1, min(5, self._parse_int(base_payload.get("urgency", 3), 3, "urgency")))
        start_at = (
            str(data["start_at"] or "").strip()
            if "start_at" in data
            else str(base_payload.get("start_at", "") or "").strip()
        )
        due_at = (
            str(data["due_at"] or "")
            if "due_at" in data
            else str(base_payload.get("due_at", "") or "")
        )
        is_all_day = (
            bool(data["is_all_day"])
            if "is_all_day" in data
            else bool(base_payload.get("is_all_day", True))
        )
        if is_all_day:
            start_at = ""
        else:
            if start_at:
                parsed_start = parse_iso_datetime(start_at)
                if parsed_start is not None:
                    start_at = to_beijing(parsed_start).replace(microsecond=0).isoformat()
            if due_at and len(due_at.strip()) > 10:
                parsed_due = parse_iso_datetime(due_at)
                if parsed_due is not None:
                    due_at = to_beijing(parsed_due).replace(microsecond=0).isoformat()
        color = str(data.get("color_theme", base_payload.get("color_theme", "sky")) or "sky")
        if color not in COLOR_THEMES:
            color = "sky"
        mode = str(data.get("task_mode", base_payload.get("task_mode", "personal")) or "personal")
        if mode not in TASK_MODES:
            mode = "personal"
        repeat_rule = str(data.get("repeat_rule", base_payload.get("repeat_rule", "never")) or "never")
        if repeat_rule not in REPEAT_RULES:
            repeat_rule = "never"

        if "reminder_offset_minutes" in data:
            rem_raw = data.get("reminder_offset_minutes")
            rem_val = (
                self._parse_int(rem_raw, 0, "reminder_offset_minutes")
                if rem_raw is not None and str(rem_raw).strip() != ""
                else None
            )
        else:
            rem_val = base_payload.get("reminder_offset_minutes")

        supervision = (
            bool(data["supervision_enabled"])
            if "supervision_enabled" in data
            else bool(base_payload.get("supervision_enabled", False))
        )
        if mode == "multiplayer":
            supervision = True

        assignee = str(data.get("assignee", base_payload.get("assignee", "self")) or "self").strip()
        auto_rollover = (
            bool(data["auto_rollover"])
            if "auto_rollover" in data
            else bool(base_payload.get("auto_rollover", False))
        )
        focus_lock = (
            bool(data["focus_lock_enabled"])
            if "focus_lock_enabled" in data
            else bool(base_payload.get("focus_lock_enabled", False))
        )
        focus_min = max(1, min(240, self._parse_int(data.get("focus_minutes", base_payload.get("focus_minutes", 25)), 25, "focus_minutes")))
        explicit_dur = (
            "duration_minutes" in data
            and data.get("duration_minutes") is not None
            and str(data.get("duration_minutes")).strip() != ""
        )
        if "duration_minutes" in data:
            raw_dur = data.get("duration_minutes")
            if raw_dur is None or str(raw_dur).strip() == "":
                duration_min: int | None = None
            else:
                duration_min = max(15, min(1440, self._parse_int(raw_dur, 60, "duration_minutes")))
        elif base_payload.get("duration_minutes") is not None:
            duration_min = max(15, min(1440, self._parse_int(base_payload["duration_minutes"], 60, "duration_minutes")))
        else:
            duration_min = None

        if not is_all_day and start_at and due_at:
            st_dt = parse_iso_datetime(start_at)
            du_dt = parse_iso_datetime(due_at)
            if st_dt is not None and du_dt is not None:
                if du_dt > st_dt:
                    if not explicit_dur:
                        duration_min = max(15, min(1440, int((du_dt - st_dt).total_seconds() // 60)))
                else:
                    eff_dur = duration_min or max(30, focus_min or 60)
                    due_at = (st_dt + timedelta(minutes=eff_dur)).replace(microsecond=0).isoformat()
                    duration_min = eff_dur
        raw_subtasks = (
            data["subtasks"]
            if "subtasks" in data
            else base_payload.get("subtasks", [])
        )
        subtasks: list[dict[str, Any]] = []
        if isinstance(raw_subtasks, list):
            for s in raw_subtasks[:50]:
                if not isinstance(s, dict):
                    continue
                st_title = str(s.get("title") or "").strip()[:200]
                if not st_title:
                    continue
                raw_sid = re.sub(r"[^a-zA-Z0-9_-]", "", str(s.get("id") or ""))[:40]
                subtasks.append(
                    {
                        "id": raw_sid or new_id("sub"),
                        "title": st_title,
                        "done": bool(s.get("done", False)),
                    }
                )

        # Recompute dynamic urgency & quadrant unless user explicitly locks quadrant override
        eff_urg, auto_quad, auto_score = compute_effective_urgency_and_quadrant(
            imp, urg, due_at, has_blockers=bool(base_payload.get("blockers"))
        )
        manual_quad = str(data.get("quadrant") or "").strip()
        if manual_quad and manual_quad not in QUADRANTS:
            raise ValidationError(f"非法的四象限枚举值: {manual_quad}")
        lock_quad = bool(data.get("lock_for_user", False))
        if lock_quad and manual_quad in QUADRANTS:
            quad = manual_quad
            quad_base = {
                "Q1_do_first": 80.0,
                "Q2_schedule": 60.0,
                "Q3_delegate": 40.0,
                "Q4_eliminate": 15.0,
            }[quad]
            score = round(quad_base + imp * 5.0 + eff_urg * 4.0, 2)
        else:
            quad = auto_quad
            score = auto_score

        final_title = (title or (existing.title if existing else "未命名待办"))[:200]
        final_summary = (
            str(data.get("summary") or "").strip() or (existing.summary if existing else final_title)
        )[:2000]
        prio_reason = str(data.get("priority_reason") or "").strip() or str(
            base_payload.get("priority_reason") or QUADRANT_LABELS.get(quad, "")
        )

        patch: dict[str, Any] = {
            **base_payload,
            "task_status": base_payload.get("task_status", "todo"),
            "importance": imp,
            "urgency": urg,
            "effective_urgency": eff_urg,
            "quadrant": quad,
            "priority_score": score,
            "priority_reason": prio_reason,
            "start_at": start_at,
            "due_at": due_at,
            "assignee": assignee,
            "color_theme": color,
            "task_mode": mode,
            "supervision_enabled": supervision,
            "is_all_day": is_all_day,
            "repeat_rule": repeat_rule,
            "reminder_offset_minutes": rem_val,
            "auto_rollover": auto_rollover,
            "subtasks": subtasks,
            "focus_lock_enabled": focus_lock,
            "focus_minutes": focus_min,
            "duration_minutes": duration_min,
        }

        evidences: tuple[MemoryEvidence, ...] = ()
        if self.note_repo is not None:
            prefix = "[Task Edit]" if existing else "[Task Card]"
            raw_note = RawNote(
                note_id=new_id("note"),
                space_id=space_id,
                content=f"{prefix} {final_title}" + (f" ({due_at[:16]})" if due_at else ""),
                source_type="web",
                actor_id=actor_id,
            )
            self.note_repo.save(raw_note)
            if self.vector_lifecycle is not None:
                self.vector_lifecycle.sync_note(raw_note)
            evidences = (
                MemoryEvidence(note_id=raw_note.note_id, note_revision=1, quote=final_title[:160]),
            )

        rels = tuple(
            RelationSpec(
                relation_type=str(r.get("relation_type", "related_to")),
                target=str(r.get("target", "")),
                context=str(r.get("context", "")),
            )
            for r in (data.get("relations") or [])
            if isinstance(r, dict) and r.get("target")
        )

        if existing is None:
            ckey = str(data.get("canonical_key") or "").strip() or (
                f"task:{assignee}:card:{slugify_permalink('task', final_title)}:{new_id('k')[-4:]}"
            )
            return self.change_service.apply(
                ChangeProposal(
                    space_id=space_id,
                    action="create",
                    memory_type="task",
                    actor_id=actor_id,
                    origin="user_direct",
                    title=final_title,
                    summary=final_summary,
                    canonical_key=ckey,
                    tags=("task", mode),
                    payload=patch,
                    lock_for_user=lock_quad,
                    evidences=evidences,
                    relations=rels,
                    idempotency_key=str(data.get("idempotency_key") or ""),
                    reason="Created task via DuoDo card sheet",
                )
            )

        return self.change_service.apply(
            ChangeProposal(
                space_id=space_id,
                action="update",
                memory_type="task",
                target_memory_id=existing.memory_id,
                expected_version=exp_ver if exp_ver is not None else existing.version,
                actor_id=actor_id,
                origin="user_direct",
                title=final_title,
                summary=final_summary,
                payload=patch,
                lock_for_user=lock_quad,
                evidences=evidences,
                relations=rels,
                replace_relations="relations" in data,
                reason="Updated task via DuoDo card sheet",
            )
        )

    def decompose_task_with_memory(
        self,
        space_id: str,
        title: str,
        summary: str = "",
        memory_id: str = "",
    ) -> dict[str, Any]:
        """Use LLM + retrieved personal memories (preferences/facts) to decompose task into checklist steps."""
        clean_title = title.strip()
        if not clean_title:
            raise ValidationError("请先输入任务标题再进行 AI 拆解")

        ctx_memories: list[MemoryItem] = []
        seen_ids: set[str] = set()

        if self.retrieval_service is not None:
            hits = self.retrieval_service.search(space_id=space_id, query=clean_title, top_k=6)
            for h in hits.get("memories", []):
                m_dict = h.get("memory", {})
                mid = str(m_dict.get("memory_id", ""))
                if mid and mid != memory_id and mid not in seen_ids:
                    mem_obj = self.memory_repo.get(space_id, mid)
                    if mem_obj is not None and mem_obj.memory_type != "task":
                        ctx_memories.append(mem_obj)
                        seen_ids.add(mid)

        for mtype in ("preference", "semantic"):
            for m in self.memory_repo.list_memories(
                space_id=space_id, memory_type=mtype, status="active", limit=5
            ):
                if m.memory_id not in seen_ids:
                    ctx_memories.append(m)
                    seen_ids.add(m.memory_id)

        from trace_memory.llm.mock_provider import DeterministicLLMProvider

        provider = self.llm
        if provider is None or not hasattr(provider, "decompose_task"):
            provider = DeterministicLLMProvider()

        try:
            batch = provider.decompose_task(clean_title, summary, ctx_memories[:8])
        except Exception:
            batch = DeterministicLLMProvider().decompose_task(clean_title, summary, ctx_memories[:8])
        formatted_subs = [
            {
                "id": new_id("sub"),
                "title": s.title,
                "done": False,
                "estimated_minutes": s.estimated_minutes,
            }
            for s in batch.subtasks
        ]
        if self.trace_repo is not None:
            self.trace_repo.record_trace(
                space_id=space_id,
                operation="task_decompose",
                input_summary=clean_title,
                details={
                    "subtask_count": len(formatted_subs),
                    "memory_context_used": list(batch.memory_context_used),
                },
                llm_calls=batch.llm_calls,
                tokens_used=batch.tokens_used,
            )
        return {
            "subtasks": formatted_subs,
            "memory_context_used": list(batch.memory_context_used),
            "llm_calls": batch.llm_calls,
            "tokens_used": batch.tokens_used,
        }

    def toggle_subtask(
        self,
        space_id: str,
        memory_id: str,
        subtask_id: str,
        done: bool,
        expected_version: int | None = None,
        actor_id: str = "user",
    ) -> dict[str, Any]:
        """Toggle a single subtask checkbox with CAS protection."""
        any_mem = self.memory_repo.get_any_space(memory_id)
        if any_mem is not None and any_mem.space_id != space_id:
            raise PermissionDeniedError("Access denied to target memory in another space")
        existing = self.memory_repo.get(space_id, memory_id)
        if existing is None:
            raise NotFoundError(f"Task {memory_id!r} not found")

        subs = [dict(s) for s in existing.payload.get("subtasks", []) if isinstance(s, dict)]
        matched = False
        for s in subs:
            if s.get("id") == subtask_id:
                s["done"] = bool(done)
                matched = True
                break
        if not matched:
            raise NotFoundError(f"Subtask {subtask_id!r} not found")

        res = self.change_service.apply(
            ChangeProposal(
                space_id=space_id,
                action="update",
                memory_type="task",
                target_memory_id=memory_id,
                expected_version=expected_version if expected_version is not None else existing.version,
                actor_id=actor_id,
                origin="user_direct",
                payload={"subtasks": subs},
                reason=f"Checklist step {'completed' if done else 'reopened'}",
            )
        )
        all_done = bool(subs) and all(bool(s.get("done")) for s in subs)
        out = res.to_dict()
        out["all_subtasks_done"] = all_done
        return out

    def record_focus_event(
        self,
        space_id: str,
        memory_id: str,
        event: str,  # "complete_session" | "abort_session"
        reason: str = "",
        elapsed_seconds: int = 0,
        expected_version: int | None = None,
        actor_id: str = "user",
    ) -> ChangeResult:
        """Record completed Pomodoro focus session or early interruption reason."""
        any_mem = self.memory_repo.get_any_space(memory_id)
        if any_mem is not None and any_mem.space_id != space_id:
            raise PermissionDeniedError("Access denied to target memory in another space")
        existing = self.memory_repo.get(space_id, memory_id)
        if existing is None:
            raise NotFoundError(f"Task {memory_id!r} not found")

        patch: dict[str, Any] = {}
        if event == "complete_session":
            patch["focus_sessions_completed"] = int(
                existing.payload.get("focus_sessions_completed", 0) or 0
            ) + 1
            change_reason = "完成一轮沉浸专注倒计时"
        elif event == "abort_session":
            interrupts = list(existing.payload.get("focus_interruptions", []))
            interrupts.append(
                {
                    "reason": reason.strip() or "提前退出专注",
                    "elapsed_seconds": max(0, self._parse_int(elapsed_seconds, 0, "elapsed_seconds")),
                    "created_at": utc_now_iso(),
                }
            )
            patch["focus_interruptions"] = interrupts[-20:]
            change_reason = f"专注锁屏提前退出：{reason.strip() or '未填写原因'}"
        else:
            raise ValidationError(f"Unsupported focus event: {event!r}")

        return self.change_service.apply(
            ChangeProposal(
                space_id=space_id,
                action="update",
                memory_type="task",
                target_memory_id=memory_id,
                expected_version=expected_version if expected_version is not None else existing.version,
                actor_id=actor_id,
                origin="user_direct",
                payload=patch,
                reason=change_reason,
            )
        )

    def build_supervision_board(
        self,
        space_id: str,
        assignee_filter: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return multiplayer & friend-supervised tasks ONLY (private personal tasks stay hidden)."""
        ref_now = to_beijing(now) if now is not None else beijing_now()
        all_tasks = self.list_sorted_tasks(
            space_id=space_id, task_status="all", sort_by="priority", now=ref_now
        )
        supervised = [
            t
            for t in all_tasks
            if bool(t["payload"].get("supervision_enabled"))
            or t["payload"].get("task_mode") == "multiplayer"
        ]
        assignees = sorted(
            {str(t["payload"].get("assignee") or "self") for t in supervised}
        )
        if assignee_filter.strip():
            supervised = [
                t
                for t in supervised
                if str(t["payload"].get("assignee") or "self") == assignee_filter.strip()
            ]
        total = len(supervised)
        done = sum(1 for t in supervised if t["payload"].get("task_status") == "done")
        todo = total - done
        rate = round((done / total) * 100.0, 1) if total > 0 else 0.0
        return {
            "space_id": space_id,
            "summary": {
                "total_supervised": total,
                "done_supervised": done,
                "todo_supervised": todo,
                "completion_rate": rate,
            },
            "assignees": assignees,
            "tasks": supervised,
        }

    def add_supervision_nudge(
        self,
        space_id: str,
        memory_id: str,
        kind: str = "cheer",  # "cheer" | "nudge"
        from_user: str = "好友",
        message: str = "",
    ) -> ChangeResult:
        """Allow a friend/collaborator to cheer or nudge a supervised/multiplayer task."""
        any_mem = self.memory_repo.get_any_space(memory_id)
        if any_mem is not None and any_mem.space_id != space_id:
            raise PermissionDeniedError("Access denied to target memory in another space")
        existing = self.memory_repo.get(space_id, memory_id)
        if existing is None:
            raise NotFoundError(f"Task {memory_id!r} not found")

        p = existing.payload
        if not p.get("supervision_enabled") and p.get("task_mode") != "multiplayer":
            raise ValidationError("该任务未开启好友监督，私人任务不可外部催办")

        norm_kind = "nudge" if kind == "nudge" else "cheer"
        sender = from_user.strip() or "监工好友"
        msg_text = message.strip() or ("⚡ 催一催：抓紧推进这一项！" if norm_kind == "nudge" else "👍 加油！稳步推进中！")
        nudges = list(p.get("nudges", []))
        nudges.append(
            {
                "id": new_id("ndg"),
                "from_user": sender,
                "kind": norm_kind,
                "message": msg_text,
                "created_at": utc_now_iso(),
            }
        )
        return self.change_service.apply(
            ChangeProposal(
                space_id=space_id,
                action="update",
                memory_type="task",
                target_memory_id=memory_id,
                expected_version=existing.version,
                actor_id=sender,
                origin="friend_supervision",
                payload={"nudges": nudges[-30:]},
                reason=f"好友监督互动（{sender}：{norm_kind}）",
            )
        )
