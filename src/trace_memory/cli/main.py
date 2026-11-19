"""Command-Line Interface (`tm` / `python -m trace_memory`) for Trace-Memory."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Sequence

from trace_memory.api.app import run_web_server
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.mcp_server.server import serve_mcp_remote_bridge, serve_mcp_stdio


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tm",
        description="Trace-Memory: Traceable Hybrid Memory & 4-Quadrant Task Engine",
    )
    parser.add_argument("--space", default="", help="Target space_id (defaults to config)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_rec = sub.add_parser("record", help="Record a ground-truth note and extract memories")
    p_rec.add_argument("content", help="Raw note content")

    p_tasks = sub.add_parser("tasks", help="Show Eisenhower 4-Quadrant matrix & sorted to-do items")
    p_tasks.add_argument(
        "--sort",
        choices=["priority", "urgency", "importance", "updated"],
        default="priority",
    )
    p_tasks.add_argument("--all", action="store_true", help="Include completed/cancelled tasks")

    p_search = sub.add_parser("search", help="Run BM25 + Dense Vector RRF hybrid search")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--type", default="", help="Filter by memory_type")

    p_ask = sub.add_parser("ask", help="Ask an evidence-grounded question")
    p_ask.add_argument("question", help="Question text")

    p_dream = sub.add_parser("dream", help="Run ReMe-style budgeted Dream consolidation")
    p_dream.add_argument("--max-calls", type=int, default=None)
    p_dream.add_argument("--max-tokens", type=int, default=None)

    sub.add_parser("export", help="Export active memories to Markdown knowledge view")
    sub.add_parser("sync", help="Sync external Markdown edits back via CAS proposals")

    p_web = sub.add_parser("web", help="Start the Web Console (with 4-Quadrant To-Do Matrix)")
    p_web.add_argument("--host", default="")
    p_web.add_argument("--port", type=int, default=0)

    p_mcp = sub.add_parser("mcp", help="Start MCP JSON-RPC 2.0 server (local SQLite or --remote cloud proxy)")
    p_mcp.add_argument(
        "--remote",
        default="",
        help="Remote Trace-Memory server URL (e.g. http://127.0.0.1:18090); falls back to TRACE_MEMORY_REMOTE_URL",
    )
    p_mcp.add_argument(
        "--token",
        default="",
        help="Bearer auth token for remote server; falls back to TRACE_MEMORY_API_TOKEN",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "mcp":
        remote_url = (args.remote or os.environ.get("TRACE_MEMORY_REMOTE_URL", "")).strip()
        api_token = (args.token or os.environ.get("TRACE_MEMORY_API_TOKEN", "")).strip()
        if remote_url:
            serve_mcp_remote_bridge(
                remote_url=remote_url,
                auth_token=api_token,
                space_id=args.space,
            )
            return 0

    cfg = TraceMemoryConfig.from_env()
    container = AppContainer.build(cfg)
    space_id = args.space or cfg.default_space

    try:
        if args.command == "record":
            res = container.ingest_service.record_note(
                space_id=space_id, content=args.content, source_type="cli"
            )
            print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "tasks":
            matrix = container.task_matrix_service.build_matrix_analysis(
                space_id=space_id, include_done=args.all
            )
            if args.sort != "priority":
                matrix["sorted_tasks"] = container.task_matrix_service.list_sorted_tasks(
                    space_id=space_id,
                    task_status="all" if args.all else "todo",
                    sort_by=args.sort,
                )
            print(json.dumps(matrix, ensure_ascii=False, indent=2))
            return 0

        if args.command == "search":
            out = container.retrieval_service.search(
                space_id=space_id, query=args.query, memory_type=args.type
            )
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0

        if args.command == "ask":
            out = container.ask_service.ask(space_id=space_id, question=args.question)
            print(out["answer"])
            print("\n--- Citations & Trace ---")
            print(json.dumps({"trace_id": out["trace_id"], "query_mode": out["query_mode"]}, ensure_ascii=False))
            return 0

        if args.command == "dream":
            out = container.dream_service.run_dream(
                space_id=space_id,
                max_llm_calls=args.max_calls,
                max_tokens=args.max_tokens,
            )
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0

        if args.command == "export":
            out = container.markdown_sync.export_space(space_id)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0

        if args.command == "sync":
            out = container.markdown_sync.sync_external_edits(space_id)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0

        if args.command == "web":
            host = args.host or cfg.web_host
            port = args.port or cfg.web_port
            run_web_server(container, host=host, port=port)
            return 0

        if args.command == "mcp":
            serve_mcp_stdio(container, space_id=space_id)
            return 0

        return 1
    finally:
        container.close()


if __name__ == "__main__":
    sys.exit(main())
