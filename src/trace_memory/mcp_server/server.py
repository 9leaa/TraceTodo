"""Model Context Protocol (MCP) Server with Basic-Memory-style tool annotations & Proposal safety."""

from __future__ import annotations

from dataclasses import replace
import json
import sys
from typing import Any, TextIO
import urllib.error
import urllib.parse
import urllib.request

from trace_memory.container import AppContainer
from trace_memory.domain.memories import MemoryEvidence
from trace_memory.domain.notes import RawNote, new_id
from trace_memory.domain.proposals import (
    ChangeProposal,
    NotFoundError,
    PermissionDeniedError,
    QuotaExceededError,
    RateLimitError,
    TraceMemoryError,
    ValidationError,
    VersionConflictError,
)


MCP_PROTOCOL_VERSION = "2024-11-05"


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "tm_search",
        "description": "Hybrid BM25 + Dense Vector RRF search across active memories and ground-truth notes.",
        "annotations": {
            "title": "Hybrid Memory Search",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (supports keywords & semantic meaning)"},
                "memory_type": {
                    "type": "string",
                    "enum": ["", "task", "preference", "semantic", "episodic"],
                    "default": "",
                },
                "top_k": {"type": "integer", "default": 8, "minimum": 1, "maximum": 30},
            },
            "required": ["query"],
        },
    },
    {
        "name": "tm_read",
        "description": "Read a memory card by memory:// URI, permalink, or memory_id, with source notes and version history.",
        "annotations": {
            "title": "Read Memory & Provenance",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "identifier": {"type": "string", "description": "memory:// permalink, bare permalink, or memory_id"}
            },
            "required": ["identifier"],
        },
    },
    {
        "name": "tm_context",
        "description": "Traverse [[wikilink]] graph relations around a seed memory/query within strict hop & node budgets.",
        "annotations": {
            "title": "Build Graph Context",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed": {"type": "string", "description": "Seed memory:// URI, memory_id, or query"},
                "max_hops": {"type": "integer", "default": 2, "minimum": 0, "maximum": 4},
                "max_nodes": {"type": "integer", "default": 10, "minimum": 1, "maximum": 30},
            },
            "required": ["seed"],
        },
    },
    {
        "name": "tm_tasks",
        "description": "Get to-do items sorted by priority/urgency/importance and analyzed by the Eisenhower 4-Quadrant Matrix.",
        "annotations": {
            "title": "Eisenhower 4-Quadrant Tasks",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "sort_by": {
                    "type": "string",
                    "enum": ["priority", "urgency", "importance", "updated"],
                    "default": "priority",
                },
                "include_done": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "tm_ask",
        "description": "Strictly read-only evidence-grounded QA over structured memories and raw notes.",
        "annotations": {
            "title": "Ask Grounded Question",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Question about current state or historical evolution"}
            },
            "required": ["question"],
        },
    },
    {
        "name": "tm_record",
        "description": "Persist a raw note and asynchronously/atomically extract traceable memories and 4-quadrant tasks.",
        "annotations": {
            "title": "Record Note & Extract Memories",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Original statement or note text"},
                "idempotency_key": {"type": "string", "default": ""},
            },
            "required": ["content"],
        },
    },
    {
        "name": "tm_propose_change",
        "description": "Submit a CAS-guarded ChangeProposal (create, update, complete_task, cancel_task, update_quadrant). Never overwrites blind.",
        "annotations": {
            "title": "Propose Memory Change",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "update", "complete_task", "cancel_task", "reopen_task", "update_quadrant"],
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["task", "preference", "semantic", "episodic"],
                    "default": "semantic",
                },
                "target_memory_id": {"type": "string", "default": ""},
                "expected_version": {"type": "integer"},
                "title": {"type": "string", "default": ""},
                "summary": {"type": "string", "default": ""},
                "payload": {"type": "object", "default": {}},
                "idempotency_key": {"type": "string", "default": ""},
                "reason": {"type": "string", "default": ""},
                "require_review": {"type": "boolean", "default": False},
            },
            "required": ["action"],
        },
    },
]


class MCPToolDispatcher:
    """Trusted-session MCP tool dispatcher bound to a configured `space_id`."""

    def __init__(
        self,
        container: AppContainer,
        trusted_space_id: str | None = None,
        *,
        actor_id: str = "mcp_client",
        actor_user_id: str = "",
        enforce_remote_guards: bool = False,
        client_ip: str = "127.0.0.1",
        daily_note_limit: int | None = None,
        daily_llm_limit: int | None = None,
        daily_token_limit: int | None = None,
    ) -> None:
        self.container = container
        self.space_id = trusted_space_id or container.config.default_space
        self.actor_id = actor_id or "mcp_client"
        self.actor_user_id = actor_user_id
        self.enforce_remote_guards = enforce_remote_guards
        self.client_ip = client_ip
        self.daily_note_limit = daily_note_limit
        self.daily_llm_limit = daily_llm_limit
        self.daily_token_limit = daily_token_limit

    def list_tools(self) -> list[dict[str, Any]]:
        return TOOL_DEFINITIONS

    def _guard_rate_limit(self) -> None:
        if not self.enforce_remote_guards:
            return
        bucket = (
            f"mcp:usr:{self.actor_user_id}"
            if self.actor_user_id
            else f"mcp:ip:{self.client_ip}:sp:{self.space_id}"
        )
        self.container.quota_service.check_rate_limit(bucket, action_label="MCP 请求")

    def _guard_daily_quota(self, *, require_note_slot: bool = False, require_llm_slot: bool = False) -> None:
        if not self.enforce_remote_guards:
            return
        self.container.quota_service.check_daily_quota(
            f"space:{self.space_id}",
            note_limit=self.daily_note_limit,
            llm_limit=self.daily_llm_limit,
            token_limit=self.daily_token_limit,
            require_note_slot=require_note_slot,
            require_llm_slot=require_llm_slot,
        )

    @staticmethod
    def _parse_int_arg(raw: Any, default_val: int, name: str) -> int:
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return default_val
        if isinstance(raw, bool):
            raise ValidationError(f"Argument {name} must be an integer")
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"Argument {name} must be an integer") from exc

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        if len(json.dumps(args, ensure_ascii=False)) > 50_000:
            return self._error_payload("Input arguments exceed 50KB limit")

        try:
            self._guard_rate_limit()

            if name == "tm_search":
                q = str(args.get("query", ""))[:500]
                mtype = str(args.get("memory_type", ""))
                top_k = max(1, min(30, self._parse_int_arg(args.get("top_k"), 8, "top_k")))
                data = self.container.retrieval_service.search(
                    space_id=self.space_id, query=q, memory_type=mtype, top_k=top_k
                )
                return self._ok_payload(data)

            if name == "tm_read":
                ident = str(args.get("identifier", ""))
                data = self.container.retrieval_service.read_memory_detail(self.space_id, ident)
                return self._ok_payload(data)

            if name == "tm_context":
                seed = str(args.get("seed", ""))
                hops = max(0, min(4, self._parse_int_arg(args.get("max_hops"), 2, "max_hops")))
                nodes = max(1, min(30, self._parse_int_arg(args.get("max_nodes"), 10, "max_nodes")))
                data = self.container.retrieval_service.build_graph_context(
                    space_id=self.space_id,
                    seed_identifier_or_query=seed,
                    max_hops=hops,
                    max_nodes=nodes,
                )
                return self._ok_payload(data)

            if name == "tm_tasks":
                sort_by = str(args.get("sort_by", "priority"))
                inc_done = bool(args.get("include_done", False))
                matrix = self.container.task_matrix_service.build_matrix_analysis(
                    space_id=self.space_id, include_done=inc_done
                )
                if sort_by != "priority":
                    matrix["sorted_tasks"] = self.container.task_matrix_service.list_sorted_tasks(
                        space_id=self.space_id,
                        task_status="all" if inc_done else "todo",
                        sort_by=sort_by,
                    )
                return self._ok_payload(matrix)

            if name == "tm_ask":
                question = str(args.get("question", ""))[:2000]
                guard_ask = self.container.input_guard.inspect_ask(question)
                if guard_ask.allowed and self.enforce_remote_guards:
                    self._guard_daily_quota(require_llm_slot=True)
                data = self.container.ask_service.ask(self.space_id, question)
                return self._ok_payload(data)

            if name == "tm_record":
                content = str(args.get("content", "")).strip()
                if not content:
                    return self._error_payload("content must not be empty")
                idem = str(args.get("idempotency_key", "")).strip()
                guard_res = self.container.input_guard.inspect_note(content)
                if self.enforce_remote_guards and guard_res.allowed:
                    if not idem:
                        is_dup, dup_reason = self.container.quota_service.check_duplicate_note(
                            self.space_id, content
                        )
                        if is_dup:
                            self.container.quota_service.record_usage(
                                f"space:{self.space_id}", blocked_noise=1
                            )
                            return self._ok_payload(
                                {
                                    "note": RawNote(
                                        note_id="filtered_duplicate",
                                        space_id=self.space_id,
                                        content=content,
                                        source_type="mcp",
                                        actor_id=self.actor_id,
                                    ).to_dict(),
                                    "extraction_status": "filtered_noise",
                                    "filtered": True,
                                    "filter_reason": dup_reason,
                                    "changes": [],
                                    "error_message": dup_reason,
                                    "trace_id": "",
                                }
                            )
                    self._guard_daily_quota(require_note_slot=True, require_llm_slot=True)

                res = self.container.ingest_service.record_note(
                    space_id=self.space_id,
                    content=content,
                    source_type="mcp",
                    actor_id=self.actor_id,
                    idempotency_key=idem,
                )
                return self._ok_payload(res.to_dict())

            if name == "tm_propose_change":
                action = str(args.get("action", ""))
                # MCP agents cannot pass `approve` or `forget` directly to bypass human review!
                if action in ("approve", "reject", "forget"):
                    return self._error_payload(
                        f"Action {action!r} is restricted to trusted human UI/CLI"
                    )
                if action not in ("create", "update", "complete_task", "cancel_task", "reopen_task", "update_quadrant"):
                    return self._error_payload(f"Unsupported action: {action!r}")
                summary_txt = str(args.get("summary") or args.get("title") or f"MCP {action}")
                cfg = self.container.settings_store.get_all()
                max_chars = int(cfg.get("max_note_chars", 2000))
                if len(summary_txt) > max_chars or len(str(args.get("title", ""))) > 200:
                    return self._error_payload(f"Input text exceeds max length ({max_chars} chars)")
                raw_payload = args.get("payload")
                if raw_payload is not None and not isinstance(raw_payload, dict):
                    raise ValidationError("payload must be an object")

                exp_ver_raw = args.get("expected_version")
                exp_ver = (
                    self._parse_int_arg(exp_ver_raw, 1, "expected_version")
                    if exp_ver_raw is not None and str(exp_ver_raw).strip() != ""
                    else None
                )
                idem = str(args.get("idempotency_key", ""))
                prop = ChangeProposal(
                    space_id=self.space_id,
                    action=action,
                    memory_type=str(args.get("memory_type", "semantic")),
                    target_memory_id=str(args.get("target_memory_id", "")),
                    expected_version=exp_ver,
                    idempotency_key=idem,
                    actor_id=self.actor_id,
                    origin="mcp_agent",
                    title=str(args.get("title", ""))[:200],
                    summary=str(args.get("summary", ""))[:max_chars],
                    payload=dict(raw_payload or {}),
                    require_review=bool(args.get("require_review", False)),
                    evidences=(),
                    reason=str(args.get("reason", "MCP agent proposal"))[:200],
                )
                is_replay = bool(
                    idem and self.container.trace_repo.get_idempotent_result(self.space_id, idem) is not None
                )
                if is_replay:
                    res = self.container.change_service.apply(prop)
                    return self._ok_payload(res.to_dict())

                if action != "create":
                    any_mem = self.container.memory_repo.get_any_space(prop.target_memory_id)
                    if any_mem is not None and any_mem.space_id != self.space_id:
                        raise PermissionDeniedError("Permission denied: resource does not belong to active space")
                    target_mem = self.container.memory_repo.get(
                        self.space_id, prop.target_memory_id, include_forgotten=True
                    )
                    if target_mem is None:
                        raise NotFoundError(f"Target memory {prop.target_memory_id!r} not found")
                    if target_mem.status == "forgotten":
                        raise ValidationError("已遗忘的记忆不可恢复或修改")
                    if exp_ver is not None and exp_ver != target_mem.version:
                        raise VersionConflictError(target_mem.memory_id, exp_ver, target_mem.version)

                if self.enforce_remote_guards:
                    self._guard_daily_quota(require_note_slot=True, require_llm_slot=False)

                src_note = RawNote(
                    note_id=new_id("note"),
                    space_id=self.space_id,
                    content=f"[MCP Proposal] {summary_txt}",
                    source_type="mcp",
                    actor_id=self.actor_id,
                )
                self.container.note_repo.save(src_note)
                self.container.vector_lifecycle.sync_note(src_note)
                prop = replace(
                    prop,
                    evidences=(
                        MemoryEvidence(
                            note_id=src_note.note_id,
                            note_revision=1,
                            quote=summary_txt[:160],
                        ),
                    ),
                )
                res = self.container.change_service.apply(prop)
                if self.enforce_remote_guards and res.status != "idempotent_replay":
                    self.container.quota_service.record_usage(
                        f"space:{self.space_id}", notes=1, embed_calls=2
                    )
                return self._ok_payload(res.to_dict())

            return self._error_payload(f"Unknown MCP tool: {name!r}")

        except PermissionDeniedError:
            return self._error_payload("Permission denied: resource does not belong to active space")
        except RateLimitError as exc:
            return self._error_payload(
                str(exc),
                extra={"code": "RATE_LIMIT_EXCEEDED", "retry_after": exc.retry_after_seconds},
            )
        except QuotaExceededError as exc:
            return self._error_payload(str(exc), extra={"code": "QUOTA_EXCEEDED"})
        except VersionConflictError as exc:
            return self._error_payload(
                f"Version conflict: expected v{exc.expected}, actual v{exc.actual}",
                extra={"code": "VERSION_CONFLICT", "expected": exc.expected, "actual": exc.actual},
            )
        except TraceMemoryError as exc:
            return self._error_payload(str(exc))

    @staticmethod
    def _ok_payload(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "isError": False,
            "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}],
            "structuredContent": data,
        }

    @staticmethod
    def _error_payload(msg: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"error": msg}
        if extra:
            body.update(extra)
        return {
            "isError": True,
            "content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}],
            "structuredContent": body,
        }


def handle_jsonrpc_message(
    dispatcher: MCPToolDispatcher, message: dict[str, Any]
) -> dict[str, Any] | None:
    """Process one MCP JSON-RPC 2.0 request/notification without polluting stdout."""
    if not isinstance(message, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
    method = message.get("method", "")
    msg_id = message.get("id")

    # Notifications have no `id` and expect no response
    if msg_id is None:
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "trace-memory", "version": "0.1.0"},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": dispatcher.list_tools()},
        }
    if method == "tools/call":
        raw_params = message.get("params")
        if raw_params is None:
            raw_params = {}
        if not isinstance(raw_params, dict):
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": "Invalid params: params must be an object"},
            }
        tool_name = str(raw_params.get("name", ""))
        raw_args = raw_params.get("arguments")
        if raw_args is not None and not isinstance(raw_args, dict):
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": "Invalid params: arguments must be an object"},
            }
        tool_args = dict(raw_args or {})
        call_res = dispatcher.call_tool(tool_name, tool_args)
        return {"jsonrpc": "2.0", "id": msg_id, "result": call_res}

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def serve_mcp_stdio(
    container: AppContainer,
    space_id: str | None = None,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> None:
    """Run MCP stdio loop (newline-delimited JSON-RPC 2.0)."""
    dispatcher = MCPToolDispatcher(container=container, trusted_space_id=space_id)
    for raw_line in stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
            stdout.write(json.dumps(err_resp) + "\n")
            stdout.flush()
            continue

        resp = handle_jsonrpc_message(dispatcher, msg)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()


def _normalize_remote_mcp_url(remote_url: str, space_id: str = "") -> str:
    clean = remote_url.strip().rstrip("/")
    if not clean.endswith("/mcp"):
        clean = f"{clean}/mcp"
    if space_id.strip():
        sep = "&" if "?" in clean else "?"
        clean = f"{clean}{sep}space_id={urllib.parse.quote(space_id.strip())}"
    return clean


def serve_mcp_remote_bridge(
    remote_url: str,
    auth_token: str = "",
    space_id: str = "",
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    timeout_seconds: float = 35.0,
    opener: Any = None,
) -> None:
    """Bridge local stdio JSON-RPC 2.0 to an authenticated remote cloud `/mcp` endpoint."""
    clean = _normalize_remote_mcp_url(remote_url, space_id)

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if auth_token.strip():
        headers["Authorization"] = f"Bearer {auth_token.strip()}"

    for raw_line in stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            err_resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            stdout.write(json.dumps(err_resp) + "\n")
            stdout.flush()
            continue

        msg_id = msg.get("id") if isinstance(msg, dict) else None
        payload_bytes = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        try:
            if opener is not None:
                status_code, resp_text = opener(clean, headers, payload_bytes)
            else:
                req = urllib.request.Request(clean, data=payload_bytes, headers=headers, method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                        status_code = int(getattr(resp, "status", 200))
                        resp_text = resp.read().decode("utf-8")
                except urllib.error.HTTPError as exc:
                    status_code = int(exc.code)
                    resp_text = exc.read().decode("utf-8", errors="replace")

            if msg_id is None or status_code == 202 or not resp_text.strip():
                continue

            if status_code >= 400:
                try:
                    err_obj = json.loads(resp_text)
                except json.JSONDecodeError:
                    err_obj = {"error": {"code": -32000, "message": resp_text or f"HTTP {status_code}"}}
                if "jsonrpc" in err_obj:
                    stdout.write(json.dumps(err_obj, ensure_ascii=False) + "\n")
                else:
                    msg_err = str(
                        err_obj.get("error", {}).get("message")
                        if isinstance(err_obj.get("error"), dict)
                        else (err_obj.get("error") or err_obj.get("message") or f"HTTP {status_code}")
                    )
                    rpc_err = {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {"code": -32001 if status_code == 401 else -32000, "message": msg_err},
                    }
                    stdout.write(json.dumps(rpc_err, ensure_ascii=False) + "\n")
                stdout.flush()
                continue

            stdout.write(resp_text.strip() + "\n")
            stdout.flush()
        except Exception as exc:
            if msg_id is not None:
                rpc_err = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32000, "message": f"Remote MCP bridge error: {exc}"},
                }
                stdout.write(json.dumps(rpc_err, ensure_ascii=False) + "\n")
                stdout.flush()
