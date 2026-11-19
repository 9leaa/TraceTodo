"""Live End-to-End Evaluation Suite running real Alibaba Bailian `qwen3.8-flash` + `text-embedding-v3`.

Uses an isolated temporary directory so it NEVER pollutes `trace-memory/data/trace_memory.db`.
Checks 6 core real-model capabilities:
1. Condition + Exception Preference Extraction (C03)
2. Eisenhower 4-Quadrant Classification (Q1 / Q2 / Q3 / Q4) & Sorting
3. Paraphrased / Typo Natural-Language Task Completion Reconciliation (TC-FUS-02)
4. Real 1024-dim Dense Vector Cross-Word Semantic Recall + BM25 Hybrid RRF (TC-FUS-03)
5. Temporal Causal Barrier: Late Old Backfill vs Current Truth (C01)
6. Grounded Current/History QA + Topic-Gated No-Answer Refusal (C12 / TC-SXJ-02) + Budgeted Dream
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trace_memory.config import TraceMemoryConfig  # noqa: E402
from trace_memory.container import AppContainer  # noqa: E402


def run_live_eval() -> int:
    base_cfg = TraceMemoryConfig.from_env(dotenv_path=ROOT / ".env")
    if base_cfg.llm_provider != "openai" or not base_cfg.openai_api_key:
        print("ERROR: Live LLM config not found in .env", file=sys.stderr)
        return 2

    checks: list[dict] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cfg = TraceMemoryConfig(
            default_space="live_eval",
            data_dir=tmp_path / "data",
            knowledge_dir=tmp_path / "knowledge",
            vector_backend=base_cfg.vector_backend,
            embedding_dim=base_cfg.embedding_dim,
            llm_provider=base_cfg.llm_provider,
            openai_base_url=base_cfg.openai_base_url,
            openai_api_key=base_cfg.openai_api_key,
            llm_model=base_cfg.llm_model,
            embedding_model=base_cfg.embedding_model,
        )
        app = AppContainer.build(cfg)
        t_start = time.perf_counter()

        try:
            # --- Scenario 1: Preference with exception (C03) ---
            r_pref = app.ingest_service.record_note(
                "live_eval",
                "我平时很喜欢喝冰美式咖啡，但是晚上八点以后绝对不喝咖啡；另外我长期专注分布式存储架构。",
            )
            prefs = app.memory_repo.list_memories("live_eval", memory_type="preference")
            has_coffee_exc = any(
                p.payload.get("exceptions") or "晚上" in p.summary or "八点" in str(p.payload)
                for p in prefs
            )
            checks.append(
                {
                    "id": "LIVE-01",
                    "name": "偏好条件与例外抽取 (C03)",
                    "passed": len(prefs) >= 1 and has_coffee_exc,
                    "detail": [
                        {"title": p.title, "summary": p.summary, "payload": p.payload}
                        for p in prefs
                    ],
                }
            )

            # --- Scenario 2: 4-Quadrant Task Classification ---
            q_inputs = [
                "明天上午十点前必须修复线上支付网关 ERR_504 超时故障",
                "下个月系统梳理分布式存储架构演进方案",
                "今天下午下班前帮同事转发技术周会腾讯会议链接",
                "以后有空的时候顺便整理一下去年的旧书单",
            ]
            for text in q_inputs:
                app.ingest_service.record_note("live_eval", text)

            matrix_before = app.task_matrix_service.build_matrix_analysis("live_eval")
            todo_cnt_before = matrix_before["summary_counts"]["total_todo"]
            top_task = matrix_before["sorted_tasks"][0] if matrix_before["sorted_tasks"] else {}
            bot_task = matrix_before["sorted_tasks"][-1] if matrix_before["sorted_tasks"] else {}
            checks.append(
                {
                    "id": "LIVE-02",
                    "name": "艾森豪威尔四象限自动分级与排序",
                    "passed": (
                        todo_cnt_before == 4
                        and top_task.get("payload", {}).get("quadrant") == "Q1_do_first"
                        and bot_task.get("payload", {}).get("quadrant") in ("Q4_eliminate", "Q2_schedule", "Q3_delegate")
                        and top_task.get("payload", {}).get("priority_score", 0) > bot_task.get("payload", {}).get("priority_score", 0)
                    ),
                    "detail": {
                        "counts": matrix_before["summary_counts"],
                        "sorted_order": [
                            (t["title"], t["payload"]["quadrant"], t["payload"]["priority_score"])
                            for t in matrix_before["sorted_tasks"]
                        ],
                    },
                }
            )

            # --- Scenario 3: Paraphrased / Typo Task Completion Reconciliation ---
            app.ingest_service.record_note(
                "live_eval", "我已经帮同事把技术周会的腾讯会议链接转发好了"
            )
            matrix_after = app.task_matrix_service.build_matrix_analysis("live_eval")
            todo_cnt_after = matrix_after["summary_counts"]["total_todo"]
            done_cnt_after = matrix_after["summary_counts"]["total_done"]
            checks.append(
                {
                    "id": "LIVE-03",
                    "name": "自然语言跨表述完成待办对账 (防重复建卡)",
                    "passed": todo_cnt_after == 3 and done_cnt_after == 1,
                    "detail": matrix_after["summary_counts"],
                }
            )

            # --- Scenario 4: Real 1024-dim Embedding Semantic Recall + Exact Error Code BM25 ---
            hit_code = app.retrieval_service.search("live_eval", "ERR_504")
            hit_sem = app.retrieval_service.search("live_eval", "线上交易接口稳定性风险")
            top_sem = hit_sem["memories"][0] if hit_sem["memories"] else None
            checks.append(
                {
                    "id": "LIVE-04",
                    "name": "BM25 错误码精确检索 + 百炼 1024维向量跨字面语义召回",
                    "passed": (
                        len(hit_code["memories"]) >= 1
                        and top_sem is not None
                        and ("支付" in top_sem["memory"]["title"] or "504" in top_sem["memory"]["title"])
                    ),
                    "detail": {
                        "exact_bm25_top": hit_code["memories"][0]["memory"]["title"] if hit_code["memories"] else None,
                        "semantic_vector_top": (
                            {
                                "title": top_sem["memory"]["title"],
                                "vector_rank": top_sem["vector_rank"],
                                "vector_score": top_sem["vector_score"],
                                "bm25_rank": top_sem["bm25_rank"],
                            }
                            if top_sem
                            else None
                        ),
                    },
                }
            )

            # --- Scenario 5: Causal Timeline Ordering (C01) ---
            app.ingest_service.record_note(
                "live_eval", "我现在搬到上海定居了", event_time="2026-10-01T10:00:00+00:00"
            )
            app.ingest_service.record_note(
                "live_eval", "2023年的时候我住在深圳", event_time="2023-05-01T10:00:00+00:00"
            )
            ans_now = app.ask_service.ask("live_eval", "我现在定居在哪个城市？")
            checks.append(
                {
                    "id": "LIVE-05",
                    "name": "时间因果栅栏与当前态精准问答 (C01)",
                    "passed": "上海" in ans_now["answer"] and ans_now["has_sufficient_evidence"],
                    "detail": {"answer": ans_now["answer"], "query_mode": ans_now["query_mode"]},
                }
            )

            # --- Scenario 6: Unanswerable Topic Gate (TC-SXJ-02) & Markdown Export/Dream ---
            ans_movie = app.ask_service.ask("live_eval", "我最喜欢的科幻电影导演是谁？")
            dream_out = app.dream_service.run_dream("live_eval", max_llm_calls=2)
            exp_out = app.markdown_sync.export_space("live_eval")
            checks.append(
                {
                    "id": "LIVE-06",
                    "name": "无证据问题诚实拒答门控 + Dream 归纳 + Markdown 导出",
                    "passed": (
                        (not ans_movie["has_sufficient_evidence"] or "未找到" in ans_movie["answer"] or "没有" in ans_movie["answer"] or "无法" in ans_movie["answer"])
                        and "冰美式" not in ans_movie["answer"]
                        and exp_out["active_count"] >= 5
                    ),
                    "detail": {
                        "no_answer_reply": ans_movie["answer"],
                        "has_sufficient_evidence": ans_movie["has_sufficient_evidence"],
                        "dream_insights": dream_out["insights_processed"],
                        "dream_tokens": dream_out["budget"]["used_tokens"],
                        "exported_markdown_files": exp_out["active_count"],
                    },
                }
            )

            elapsed_s = round(time.perf_counter() - t_start, 2)
            traces = app.trace_repo.list_traces("live_eval", limit=50)
            total_tokens = sum(t["tokens_used"] for t in traces)
            total_calls = sum(t["llm_calls"] for t in traces)

            report = {
                "model": cfg.llm_model,
                "embedding_model": cfg.embedding_model,
                "elapsed_seconds": elapsed_s,
                "total_llm_calls": total_calls,
                "total_tokens_used": total_tokens,
                "passed_count": sum(1 for c in checks if c["passed"]),
                "total_checks": len(checks),
                "checks": checks,
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["passed_count"] == report["total_checks"] else 1
        finally:
            app.close()


if __name__ == "__main__":
    sys.exit(run_live_eval())
