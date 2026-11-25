"""v2 ingest pipeline: parallel extraction, ordered commits, reference-aware patch updates,
skip-ahead rule, dependency-based forget cascade and the LLM key pool."""

from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import time
from typing import Any
import unittest

from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.domain.exceptions import StructuredOutputError
from trace_memory.domain.proposals import ChangeProposal, TraceMemoryError
from trace_memory.llm.mock_provider import DeterministicLLMProvider
from trace_memory.llm.protocol import ExtractedCandidate, ExtractionBatch

TOMORROW_15 = "2026-11-24T15:00:00+08:00"
TOMORROW_16 = "2026-11-24T16:00:00+08:00"


def _task(title: str, **kw: Any) -> ExtractedCandidate:
    payload = {
        "task_status": "todo",
        "importance": 3,
        "urgency": 3,
        "priority_reason": "测试",
        "start_at": "",
        "due_at": kw.pop("due_at", ""),
        "duration_minutes": None,
        "is_all_day": False,
        "assignee": "self",
    }
    payload.update(kw.pop("payload", {}))
    return ExtractedCandidate(
        memory_type="task",
        title=title,
        summary=kw.pop("summary", title),
        canonical_key=kw.pop("canonical_key", f"task:{title}"),
        payload=payload,
        mutation_hint=kw.pop("mutation_hint", "upsert"),
        event_time="2026-11-23T09:00:00+08:00",
        quote=title,
        **kw,
    )


class ScriptedLLM(DeterministicLLMProvider):
    """Returns scripted candidates per keyword; a keyword can be gated to simulate a slow model call."""

    def __init__(self) -> None:
        super().__init__()
        self.script: dict[str, Any] = {}
        self.gates: dict[str, threading.Event] = {}
        self.started: dict[str, threading.Event] = {}
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []
        self._lock = threading.Lock()

    def extract_candidates(
        self, note_text, default_event_time, existing_memories=None, recent_notes=None
    ):
        with self._lock:
            self.calls.append((note_text, [dict(w) for w in (recent_notes or [])]))
        for key, ev in self.started.items():
            if key in note_text:
                ev.set()
        for key, gate in self.gates.items():
            if key in note_text:
                gate.wait(timeout=10)
        for key, maker in self.script.items():
            if key in note_text:
                cands = maker(recent_notes or []) if callable(maker) else maker
                return ExtractionBatch(candidates=tuple(cands), llm_calls=1, tokens_used=1)
        return ExtractionBatch(candidates=(), llm_calls=1, tokens_used=1)


def _build(tmp: str, llm: Any, **cfg: Any) -> AppContainer:
    base = Path(tmp)
    params: dict[str, Any] = dict(
        default_space="v2",
        data_dir=base / "data",
        knowledge_dir=base / "know",
        ingest_worker_count=8,
        ingest_space_concurrency=8,
        rate_limit_per_minute=500,
        ingest_pipeline="v2",
    )
    params.update(cfg)
    return AppContainer.build(TraceMemoryConfig(**params), llm_override=llm)


def _tasks(app: AppContainer, space: str = "v2", status: str = "active"):
    return app.memory_repo.list_memories(space, memory_type="task", status=status)


class _PipelineBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.llm = ScriptedLLM()

    def tearDown(self) -> None:
        app = getattr(self, "app", None)
        if app is not None:
            app.close()
        self._tmp.cleanup()

    def start(self, **cfg: Any) -> AppContainer:
        self.app = _build(self._tmp.name, self.llm, **cfg)
        self.app.ingest_worker.start()
        return self.app

    def send(self, text: str, space: str = "v2"):
        res = self.app.ingest_service.enqueue_note(space, text)
        self.app.ingest_worker.notify()
        return res

    def idle(self, space: str = "v2", timeout: float = 8.0) -> None:
        self.assertTrue(self.app.ingest_worker.wait_for_space_idle(space, timeout=timeout))

    # ---------------------------------------------------------------- ① slow, ② updates ①
    def _script_meeting(self) -> None:
        self.llm.script["开会讨论预算"] = [_task("开会讨论预算", due_at=TOMORROW_15)]
        self.llm.script["改到4点"] = lambda win: [
            _task(
                "改到4点开会",
                due_at=TOMORROW_16,
                relation="update",
                refers_to_label=win[-1]["label"] if win else "",
                changed_fields=("due_at",),
                payload={"due_at": TOMORROW_16},
            )
        ]


class PipelineV2Test(_PipelineBase):
    def test_json_format_failure_retries_the_same_note(self) -> None:
        def extract(_window):
            if len(self.llm.calls) == 1:
                raise StructuredOutputError("non_object", "Expected a JSON object", 8)
            return [_task("整理季度复盘")]

        self.llm.script["整理季度复盘"] = extract
        self.start(ingest_max_retries=2)
        queued = self.send("整理季度复盘")
        self.idle()

        job = self.app.job_repo.get_job(queued.job_id)
        self.assertEqual(job["attempts"], 2)
        self.assertEqual(job["note_id"], queued.note.note_id)
        self.assertEqual(job["result"]["extraction_status"], "completed")
        self.assertEqual(len(_tasks(self.app)), 1)
        count = self.app.db.query_one("SELECT count(*) AS n FROM raw_notes WHERE space_id='v2'")
        self.assertEqual(count["n"], 1)
        errors = [
            run for run in self.app.trace_repo.list_traces("v2")
            if run["operation"] == "ingest_format_error"
        ]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["details"], {
            "note_id": queued.note.note_id, "reason": "non_object", "will_retry": True,
        })
        self.assertEqual(errors[0]["tokens_used"], 8)

    def test_update_while_first_still_extracting_yields_one_card(self) -> None:
        self._script_meeting()
        gate = threading.Event()
        started = threading.Event()
        self.llm.gates["开会讨论预算"] = gate
        self.llm.started["开会讨论预算"] = started
        self.start()
        self.send("明天下午3点开会讨论预算")
        self.assertTrue(started.wait(3))
        r2 = self.send("改到4点")
        # ② extracts in parallel while ① is still waiting on the model ...
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job2 = self.app.job_repo.get_job(r2.job_id)
            if job2 and job2["status"] == "extracted":
                break
            time.sleep(0.01)
        self.assertEqual(self.app.job_repo.get_job(r2.job_id)["status"], "extracted")
        # ... and saw ①'s raw words in its context window, marked as still processing
        win2 = [w for t, w in self.llm.calls if "改到4点" in t][0]
        self.assertEqual([w["content"] for w in win2], ["明天下午3点开会讨论预算"])
        self.assertEqual(win2[0]["status"], "处理中")
        self.assertEqual(_tasks(self.app), [], "② must not commit before ①")
        gate.set()
        self.idle()
        tasks = _tasks(self.app)
        self.assertEqual(len(tasks), 1, [t.title for t in tasks])
        card = tasks[0]
        self.assertEqual(card.title, "开会讨论预算", "patch must not overwrite the title")
        self.assertEqual(card.payload["due_at"], TOMORROW_16)
        roles = {e.note_id: e.role for e in card.evidences}
        self.assertEqual(len(roles), 2)
        self.assertIn("base", roles.values())

    def test_patch_keeps_user_edited_title(self) -> None:
        self._script_meeting()
        self.start()
        self.send("明天下午3点开会讨论预算")
        self.idle()
        card = _tasks(self.app)[0]
        self.app.change_service.apply(
            ChangeProposal(
                space_id="v2",
                action="update",
                memory_type="task",
                target_memory_id=card.memory_id,
                expected_version=card.version,
                title="Q4 预算评审会",
                payload=dict(card.payload),
                origin="user_direct",
            )
        )
        self.send("改到4点")
        self.idle()
        tasks = _tasks(self.app)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].title, "Q4 预算评审会")
        self.assertEqual(tasks[0].payload["due_at"], TOMORROW_16)

    def test_user_locked_card_is_not_patched(self) -> None:
        self._script_meeting()
        self.start()
        self.send("明天下午3点开会讨论预算")
        self.idle()
        card = _tasks(self.app)[0]
        self.app.change_service.apply(
            ChangeProposal(
                space_id="v2",
                action="update",
                memory_type="task",
                target_memory_id=card.memory_id,
                expected_version=card.version,
                title=card.title,
                payload=dict(card.payload),
                origin="user_direct",
                lock_for_user=True,
            )
        )
        r2 = self.send("改到4点")
        self.idle()
        tasks = _tasks(self.app)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].payload["due_at"], TOMORROW_15)
        job = self.app.job_repo.get_job(r2.job_id)
        self.assertEqual(job["result"].get("skipped_locked_count"), 1, job)

    def test_complete_without_resolvable_target_creates_nothing(self) -> None:
        self.llm.script["那个搞定了"] = [
            _task("那个", relation="complete", mutation_hint="complete_task", payload={"task_status": "done"})
        ]
        self.start()
        r = self.send("那个搞定了")
        self.idle()
        self.assertEqual(_tasks(self.app, status="all"), [])
        job = self.app.job_repo.get_job(r.job_id)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"].get("unresolved_reference_count"), 1, job)

    # ---------------------------------------------------------------- ordering / skip-ahead
    def test_unrelated_text_message_skips_slow_text_head_after_deadline(self) -> None:
        self.llm.script["整理季度复盘"] = [_task("整理季度复盘")]
        self.llm.script["买牛奶"] = [_task("买牛奶")]
        gate = threading.Event()
        started = threading.Event()
        self.llm.gates["整理季度复盘"] = gate
        self.llm.started["整理季度复盘"] = started
        self.start(commit_deadline_seconds=0.3)
        r1 = self.send("下周整理季度复盘材料")
        self.assertTrue(started.wait(3))
        r2 = self.send("下班记得买牛奶")
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and not _tasks(self.app):
            time.sleep(0.02)
        self.assertEqual([t.title for t in _tasks(self.app)], ["买牛奶"])
        self.assertTrue(self.app.job_repo.get_job(r2.job_id)["skipped_ahead"])
        gate.set()
        self.idle()
        self.assertEqual({t.title for t in _tasks(self.app)}, {"买牛奶", "整理季度复盘"})
        self.assertFalse(self.app.job_repo.get_job(r1.job_id)["skipped_ahead"])

    def test_related_message_never_skips_ahead(self) -> None:
        self._script_meeting()
        gate = threading.Event()
        started = threading.Event()
        self.llm.gates["开会讨论预算"] = gate
        self.llm.started["开会讨论预算"] = started
        self.start(commit_deadline_seconds=0.2)
        self.send("明天下午3点开会讨论预算")
        self.assertTrue(started.wait(3))
        self.send("改到4点")
        time.sleep(0.8)
        self.assertEqual(_tasks(self.app), [], "an update of ① must wait for ①")
        gate.set()
        self.idle()
        self.assertEqual(len(_tasks(self.app)), 1)

    def test_image_only_head_blocks_skip_ahead(self) -> None:
        self.llm.script["买牛奶"] = [_task("买牛奶")]
        self.app = _build(self._tmp.name, self.llm, commit_deadline_seconds=0.0)
        img = self.app.ingest_service.enqueue_note("v2", "[图片记录]")
        txt = self.app.ingest_service.enqueue_note("v2", "整理季度复盘材料")
        pipe = self.app.ingest_worker
        row = {
            "job_id": "x",
            "requires_barrier": False,
            "extraction": {"kind": "ok", "candidates": [_task("买牛奶").to_dict()]},
        }
        img_row = self.app.job_repo.get_job(img.job_id)
        txt_row = self.app.job_repo.get_job(txt.job_id)
        self.assertFalse(pipe._may_skip_ahead("v2", row, [img_row]))
        self.assertTrue(pipe._may_skip_ahead("v2", row, [txt_row]))
        similar = dict(row, extraction={"kind": "ok", "candidates": [_task("整理季度复盘").to_dict()]})
        self.assertFalse(pipe._may_skip_ahead("v2", similar, [txt_row]))

    def test_shutdown_keeps_order_and_resumes_extracted_jobs(self) -> None:
        self.llm.script["模块A"] = [_task("推进模块A联调")]
        self.llm.script["模块B"] = [_task("提交模块B压测报告")]
        gate = threading.Event()
        started = threading.Event()
        self.llm.gates["模块A"] = gate
        self.llm.started["模块A"] = started
        self.start()
        ra = self.send("明天必须推进模块A联调方案")
        self.assertTrue(started.wait(3))
        rb = self.send("周五必须提交模块B压测报告")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and self.app.job_repo.get_job(rb.job_id)["status"] != "extracted":
            time.sleep(0.01)
        stopper = threading.Thread(target=self.app.ingest_worker.stop, kwargs={"timeout": 2.0})
        stopper.start()
        time.sleep(0.2)
        gate.set()
        stopper.join(5)
        self.assertEqual(_tasks(self.app), [], "nothing commits once stop() began")
        self.assertEqual(self.app.job_repo.get_job(rb.job_id)["status"], "extracted")
        self.app.ingest_worker.start()
        self.idle()
        a = self.app.job_repo.get_job(ra.job_id)
        b = self.app.job_repo.get_job(rb.job_id)
        self.assertEqual((a["status"], b["status"]), ("completed", "completed"))
        self.assertLessEqual(a["updated_at"], b["updated_at"])
        self.assertEqual(len(_tasks(self.app)), 2)

    # ---------------------------------------------------------------- stale reference
    def test_edit_of_referenced_note_requeues_dependent_job(self) -> None:
        self._script_meeting()
        self.start()
        self.send("明天下午3点开会讨论预算")
        self.idle()
        art = {
            "kind": "ok",
            "window": [{"label": "N1", "note_id": _tasks(self.app)[0].evidences[0].note_id, "revision": 999}],
            "candidates": [{"refers_to_label": "N1"}],
        }
        self.assertTrue(self.app.ingest_service.stale_reference_in("v2", art))
        art["window"][0]["revision"] = self.app.note_repo.get(
            "v2", art["window"][0]["note_id"]
        ).revision
        self.assertFalse(self.app.ingest_service.stale_reference_in("v2", art))

    # ---------------------------------------------------------------- forget cascade
    def test_forgetting_base_note_voids_card_and_reextracts_dependent(self) -> None:
        self._script_meeting()
        self.llm.script["改到4点"] = lambda win: [
            _task(
                "4点开会",
                due_at=TOMORROW_16,
                relation="update" if win else "new",
                refers_to_label=win[-1]["label"] if win else "",
                changed_fields=("due_at",) if win else (),
                payload={"due_at": TOMORROW_16},
            )
        ]
        self.start()
        r1 = self.send("明天下午3点开会讨论预算")
        self.idle()
        self.send("改到4点")
        self.idle()
        self.assertEqual(len(_tasks(self.app)), 1)
        old_id = _tasks(self.app)[0].memory_id
        self.app.ingest_service.forget_note("v2", r1.note.note_id)
        self.idle()
        tasks = _tasks(self.app)
        self.assertNotIn(old_id, [t.memory_id for t in tasks])
        # ② was re-extracted without ① in its context and stands on its own now
        self.assertEqual([t.title for t in tasks], ["4点开会"])
        last_win = [w for t, w in self.llm.calls if "改到4点" in t][-1]
        self.assertEqual(last_win, [])

    def test_forgetting_independent_evidence_keeps_card(self) -> None:
        self.llm.script["交周报"] = [_task("交周报", canonical_key="task:weekly")]
        self.start()
        r1 = self.send("周五交周报")
        self.idle()
        self.send("别忘了周五交周报")  # same canonical key, no refers_to: independent evidence
        self.idle()
        tasks = _tasks(self.app)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(len(tasks[0].evidences), 2)
        self.app.ingest_service.forget_note("v2", r1.note.note_id)
        self.idle()
        tasks = _tasks(self.app)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(len(tasks[0].evidences), 1)


class EditAndMultiCardTest(_PipelineBase):
    # ---------------------------------------------------------------- editing a base note
    def test_editing_base_note_voids_card_and_replays_dependent_in_order(self) -> None:
        self._script_meeting()
        self.llm.script["和老王吃饭"] = [_task("和老王吃饭", due_at=TOMORROW_15)]
        self.start()
        r1 = self.send("明天下午3点开会讨论预算")
        self.idle()
        self.send("改到4点")
        self.idle()
        old_id = _tasks(self.app)[0].memory_id
        res = self.app.ingest_service.edit_raw_note("v2", r1.note.note_id, "明天下午3点和老王吃饭")
        self.assertEqual(res.extraction_status, "queued")
        self.idle()
        tasks = _tasks(self.app)
        self.assertEqual(len(tasks), 1, [t.title for t in tasks])
        self.assertNotEqual(tasks[0].memory_id, old_id)
        self.assertEqual(tasks[0].title, "和老王吃饭")
        self.assertEqual(tasks[0].payload["due_at"], TOMORROW_16, "② is replayed on top of the new ①")
        old = self.app.memory_repo.get("v2", old_id)
        self.assertEqual(old.status, "superseded")
        # ② saw the edited ① in its context when it was replayed
        last_win = [w for t, w in self.llm.calls if "改到4点" in t][-1]
        self.assertEqual([w["content"] for w in last_win], ["明天下午3点和老王吃饭"])

    def test_editing_base_note_without_worker_runs_synchronously(self) -> None:
        self._script_meeting()
        self.llm.script["和老王吃饭"] = [_task("和老王吃饭", due_at=TOMORROW_15)]
        self.app = _build(self._tmp.name, self.llm)
        svc = self.app.ingest_service
        r1 = svc.record_note("v2", "明天下午3点开会讨论预算")
        svc.record_note("v2", "改到4点")
        self.assertEqual(len(_tasks(self.app)), 1)
        svc.edit_raw_note("v2", r1.note.note_id, "明天下午3点和老王吃饭")
        tasks = _tasks(self.app)
        self.assertEqual([(t.title, t.payload["due_at"]) for t in tasks], [("和老王吃饭", TOMORROW_16)])

    # ---------------------------------------------------------------- several cards from one note
    def _two_cards(self) -> None:
        self.llm.script["开会，顺便买咖啡"] = [
            _task("开会", due_at=TOMORROW_15, canonical_key="task:meeting"),
            _task("买咖啡", canonical_key="task:coffee"),
        ]

    def _update(self, key: str, title: str, canonical_key: str = "") -> None:
        self.llm.script[key] = lambda win: [
            _task(
                title,
                due_at=TOMORROW_16,
                relation="update",
                refers_to_label=win[-1]["label"],
                changed_fields=("due_at",),
                payload={"due_at": TOMORROW_16},
                canonical_key=canonical_key or f"task:{title}",
            )
        ]

    def _due(self) -> dict[str, str]:
        return {t.title: t.payload.get("due_at", "") for t in _tasks(self.app)}

    def test_multi_card_clear_title_match(self) -> None:
        self._two_cards()
        self._update("开会改到4点", "4点开会")
        self.start()
        self.send("明天3点开会，顺便买咖啡")
        self.idle()
        self.send("开会改到4点")
        self.idle()
        self.assertEqual(self._due(), {"开会": TOMORROW_16, "买咖啡": ""})

    def test_multi_card_exact_key_match_beats_vague_title(self) -> None:
        self._two_cards()
        self._update("那个改一下", "改一下", canonical_key="task:coffee")
        self.start()
        self.send("明天3点开会，顺便买咖啡")
        self.idle()
        self.send("那个改一下")
        self.idle()
        self.assertEqual(self._due(), {"开会": TOMORROW_15, "买咖啡": TOMORROW_16})

    def test_multi_card_ambiguous_asks_model(self) -> None:
        self._two_cards()
        self._update("时间改一下", "时间调整")
        asked: list[list[str]] = []

        def chooser(note_text, cand_title, options):
            asked.append([o["title"] for o in options])
            return [o["title"] for o in options].index("开会"), 1, 5

        self.llm.choose_reference_card = chooser  # type: ignore[attr-defined]
        self.start()
        self.send("明天3点开会，顺便买咖啡")
        self.idle()
        self.send("时间改一下")
        self.idle()
        self.assertEqual(len(asked), 1)
        self.assertEqual(sorted(asked[0]), ["买咖啡", "开会"])
        self.assertEqual(self._due(), {"开会": TOMORROW_16, "买咖啡": ""})

    def test_multi_card_ambiguous_and_model_unsure_changes_nothing(self) -> None:
        self._two_cards()
        self._update("时间改一下", "时间调整")
        self.llm.choose_reference_card = lambda n, t, o: (-1, 1, 5)  # type: ignore[attr-defined]
        self.start()
        self.send("明天3点开会，顺便买咖啡")
        self.idle()
        r2 = self.send("时间改一下")
        self.idle()
        self.assertEqual(self._due(), {"开会": TOMORROW_15, "买咖啡": ""})
        job = self.app.job_repo.get_job(r2.job_id)
        self.assertEqual(job["result"].get("unresolved_reference_count"), 1, job)


class KeyPoolTest(unittest.TestCase):
    def test_choose_reference_card_parsing(self) -> None:
        from trace_memory.llm import openai_compat as oc

        p = oc.OpenAICompatLLMProvider(base_url="http://x", api_key="k", model="m")
        replies = iter([({"choice": 2, "confident": True}, 3), ({"choice": 1, "confident": False}, 3)])
        p._chat_json = lambda s, u: next(replies)  # type: ignore[method-assign]
        opts = [{"title": "开会"}, {"title": "买咖啡"}]
        self.assertEqual(p.choose_reference_card("改一下", "改一下", opts), (1, 1, 3))
        self.assertEqual(p.choose_reference_card("改一下", "改一下", opts)[0], -1)

    def test_429_cools_key_and_retries_on_next(self) -> None:
        from trace_memory.llm import openai_compat as oc

        used: list[str] = []

        def fake_post(url, api_key, payload, timeout_seconds=30.0):
            used.append(api_key)
            if api_key == "k1":
                raise TraceMemoryError("LLM API HTTP 429: rate limited")
            return {"choices": [{"message": {"content": "{}"}}], "usage": {"total_tokens": 3}}

        orig = oc._post_json_with_retry
        oc._post_json_with_retry = fake_post
        try:
            p = oc.OpenAICompatLLMProvider(
                base_url="http://x", api_key="k1", model="m", api_keys=("k1", "k2")
            )
            p._keys._next = 0
            turn = p._chat_turn([{"role": "user", "content": "hi"}])
            self.assertEqual(turn.tokens_used, 3)
            self.assertEqual(used, ["k1", "k2"])
            used.clear()
            p._chat_turn([{"role": "user", "content": "hi"}])
            self.assertEqual(used, ["k2"], "cooling key must not be tried first")
        finally:
            oc._post_json_with_retry = orig

    def test_all_keys_cooling_surfaces_429(self) -> None:
        from trace_memory.llm import openai_compat as oc

        def fake_post(url, api_key, payload, timeout_seconds=30.0):
            raise TraceMemoryError("LLM API HTTP 429: rate limited")

        orig = oc._post_json_with_retry
        oc._post_json_with_retry = fake_post
        try:
            p = oc.OpenAICompatLLMProvider(
                base_url="http://x", api_key="k1", model="m", api_keys=("k1", "k2")
            )
            with self.assertRaises(TraceMemoryError) as ctx:
                p._chat_turn([{"role": "user", "content": "hi"}])
            self.assertIn("429", str(ctx.exception))
        finally:
            oc._post_json_with_retry = orig

    def test_parse_relation_fields(self) -> None:
        from trace_memory.llm import openai_compat as oc

        p = oc.OpenAICompatLLMProvider(base_url="http://x", api_key="k", model="m")
        captured: list[str] = []

        def fake_chat_json(sys_p, usr_p):
            captured.append(usr_p)
            return (
                {
                    "candidates": [
                        {
                            "memory_type": "task",
                            "title": "开会",
                            "relation": "update",
                            "refers_to_note": "n1",
                            "changed_fields": ["due_at", "rm -rf"],
                            "payload": {"due_at": TOMORROW_16},
                        },
                        {"memory_type": "task", "title": "买菜", "refers_to_note": "N9"},
                    ]
                },
                1,
            )

        p._chat_json = fake_chat_json  # type: ignore[method-assign]
        batch = p.extract_candidates(
            "改到4点",
            "2026-11-23T09:00:00+08:00",
            recent_notes=[{"label": "N1", "content": "明天3点开会", "status": "处理中"}],
        )
        self.assertIn("[N1 · 处理中] 明天3点开会", captured[0])
        c1, c2 = batch.candidates
        self.assertEqual((c1.relation, c1.refers_to_label, c1.changed_fields), ("update", "N1", ("due_at",)))
        self.assertEqual((c2.relation, c2.refers_to_label), ("new", ""))


if __name__ == "__main__":
    unittest.main()
