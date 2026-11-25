"""OpenAI-compatible (DeepSeek / Qwen / OpenAI) HTTP LLM & Embedding provider.

Uses Python standard library `urllib.request` with strict timeouts and single timeout retry,
so it works in any Python environment without requiring heavy SDK dependencies, while also
falling back cleanly or surfacing explicit errors on misconfiguration.
"""

from __future__ import annotations

from datetime import datetime
import http.client
import json
import math
import re
import socket
import ssl
import threading
import time
from typing import Any
import urllib.error
from urllib.parse import urlsplit
import urllib.request

from trace_memory.domain.exceptions import StructuredOutputError
from trace_memory.domain.memories import MemoryItem, parse_iso_datetime
from trace_memory.domain.notes import (
    RawNote,
    beijing_now,
    format_beijing_prompt_clock,
    to_beijing,
)
from trace_memory.domain.proposals import RelationSpec, TraceMemoryError
from trace_memory.domain.relations import extract_relations_from_markdown, slugify_permalink
from trace_memory.llm.mock_provider import DeterministicLLMProvider, SemanticHashEmbedder
from trace_memory.llm.protocol import (
    AskSynthesis,
    AskTurn,
    DecomposeBatch,
    DreamBatch,
    DreamInsight,
    ExtractedCandidate,
    ExtractionBatch,
    SubtaskSuggestion,
)


def _normalize_beijing_iso(value: str, fallback: str = "") -> str:
    """Normalize an ISO-8601 / YYYY-MM-DD timestamp string to Beijing Time (+08:00)."""
    raw = str(value or "").strip()
    if not raw:
        return fallback
    parsed = parse_iso_datetime(raw)
    if parsed is None:
        return fallback or raw
    return to_beijing(parsed).replace(microsecond=0).isoformat()


class _KeepAliveConnectionPool:
    """Thread-safe stdlib HTTP/1.1 Keep-Alive connection pool eliminating per-call TLS handshakes."""

    def __init__(self, max_idle_per_origin: int = 16) -> None:
        self._max_idle = max(1, int(max_idle_per_origin))
        self._lock = threading.Lock()
        self._idle: dict[tuple[str, str, int], list[http.client.HTTPConnection]] = {}
        self._ssl_ctx = ssl.create_default_context()

    def _checkout(
        self, scheme: str, host: str, port: int, timeout: float
    ) -> tuple[http.client.HTTPConnection, bool]:
        key = (scheme, host, port)
        with self._lock:
            bucket = self._idle.get(key)
            while bucket:
                conn = bucket.pop()
                if getattr(conn, "sock", None) is not None:
                    conn.timeout = timeout
                    try:
                        conn.sock.settimeout(timeout)
                    except OSError:
                        try:
                            conn.close()
                        except Exception:
                            pass
                        continue
                    return conn, True
        if scheme == "https":
            return (
                http.client.HTTPSConnection(host, port, timeout=timeout, context=self._ssl_ctx),
                False,
            )
        return http.client.HTTPConnection(host, port, timeout=timeout), False

    def _checkin(self, scheme: str, host: str, port: int, conn: http.client.HTTPConnection) -> None:
        if getattr(conn, "sock", None) is None:
            return
        key = (scheme, host, port)
        with self._lock:
            bucket = self._idle.setdefault(key, [])
            if len(bucket) < self._max_idle:
                bucket.append(conn)
                return
        try:
            conn.close()
        except Exception:
            pass

    def post_bytes(
        self,
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> tuple[int, bytes]:
        parts = urlsplit(url)
        scheme = (parts.scheme or "https").lower()
        host = parts.hostname or ""
        if scheme not in ("http", "https") or not host:
            raise urllib.error.URLError(f"Unsupported URL: {url}")
        port = parts.port or (443 if scheme == "https" else 80)
        target_path = parts.path or "/"
        if parts.query:
            target_path = f"{target_path}?{parts.query}"

        req_headers = {
            **headers,
            "Connection": "keep-alive",
            "Content-Length": str(len(body)),
        }

        # If a pooled socket was closed while idle, transparently retry once on a fresh socket
        for stale_retry in range(2):
            conn, reused = self._checkout(scheme, host, port, timeout_seconds)
            try:
                conn.request("POST", target_path, body=body, headers=req_headers)
                resp = conn.getresponse()
                raw = resp.read()
                status = int(resp.status)
                if not resp.will_close:
                    self._checkin(scheme, host, port, conn)
                else:
                    conn.close()
                return status, raw
            except (
                http.client.RemoteDisconnected,
                http.client.CannotSendRequest,
                http.client.ResponseNotReady,
                BrokenPipeError,
                ConnectionResetError,
                EOFError,
            ):
                try:
                    conn.close()
                except Exception:
                    pass
                if reused and stale_retry == 0:
                    continue
                raise
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                raise
        raise ConnectionResetError("Keep-alive connection reset")


# Sized for the v2 ingest pipeline (~80 concurrent model calls) so busy bursts reuse warm TLS sockets.
_HTTP_POOL = _KeepAliveConnectionPool(max_idle_per_origin=128)


class _ApiKeyPool:
    """Round-robin over several API keys; a key that hits HTTP 429 cools down for a while."""

    def __init__(self, keys: tuple[str, ...], cooldown_seconds: float = 15.0) -> None:
        uniq: list[str] = []
        for k in keys:
            k = str(k or "").strip()
            if k and k not in uniq:
                uniq.append(k)
        self.keys = tuple(uniq)
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._lock = threading.Lock()
        self._next = 0
        self._cool_until: dict[str, float] = {}

    def __len__(self) -> int:
        return len(self.keys)

    def candidates(self) -> list[str]:
        """Keys to try for one request: available ones first (round-robin), cooling ones last."""
        now = time.monotonic()
        with self._lock:
            n = len(self.keys)
            start = self._next % n if n else 0
            self._next += 1
            order = [self.keys[(start + i) % n] for i in range(n)]
            ready = [k for k in order if self._cool_until.get(k, 0.0) <= now]
            cooling = sorted(
                (k for k in order if self._cool_until.get(k, 0.0) > now),
                key=lambda k: self._cool_until.get(k, 0.0),
            )
        return ready + cooling

    def cool(self, key: str) -> None:
        with self._lock:
            self._cool_until[key] = time.monotonic() + self.cooldown_seconds

    def is_cooling(self, key: str) -> bool:
        with self._lock:
            return self._cool_until.get(key, 0.0) > time.monotonic()


def _is_rate_limited(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "http 429" in msg or "rate limit" in msg or "too many requests" in msg


_ALLOWED_CHANGED_FIELDS = frozenset(
    {
        "title",
        "summary",
        "due_at",
        "start_at",
        "duration_minutes",
        "is_all_day",
        "importance",
        "urgency",
        "assignee",
        "task_status",
        "location",
        "details",
        "value",
        "target",
        "polarity",
        "conditions",
        "exceptions",
        "event_summary",
        "occurred_at",
        "participants",
        "tags",
    }
)


def _post_json_with_retry(
    url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    use_proxy = bool(urllib.request.getproxies())
    last_err: Exception | None = None
    # At most 1 retry strictly on socket timeout (mirroring Suixinji's LLM boundary rule)
    for attempt in range(2):
        try:
            if use_proxy:
                req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                    raw_bytes = resp.read()
            else:
                status_code, raw_bytes = _HTTP_POOL.post_bytes(
                    url, body, headers, timeout_seconds=timeout_seconds
                )
                if status_code < 200 or status_code >= 300:
                    detail = raw_bytes.decode("utf-8", errors="replace")[:500]
                    raise TraceMemoryError(f"LLM API HTTP {status_code}: {detail}")
            parsed = json.loads(raw_bytes.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise TraceMemoryError("LLM API returned non-object JSON payload")
            return parsed
        except TraceMemoryError:
            raise
        except socket.timeout as exc:
            last_err = exc
            if attempt == 0:
                continue
            raise TraceMemoryError(f"LLM API timed out: {exc}") from exc
        except TimeoutError as exc:
            last_err = exc
            if attempt == 0:
                continue
            raise TraceMemoryError(f"LLM API timed out: {exc}") from exc
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                detail = str(exc)
            raise TraceMemoryError(f"LLM API HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout) and attempt == 0:
                last_err = exc
                continue
            raise TraceMemoryError(f"LLM API request failed: {exc}") from exc
        except (
            http.client.HTTPException,
            ConnectionError,
            ssl.SSLError,
        ) as exc:
            raise TraceMemoryError(f"LLM API request failed: {exc}") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TraceMemoryError(f"LLM API response parse error: {exc}") from exc
    raise TraceMemoryError(f"LLM API timed out: {last_err}")


class OpenAICompatEmbedder:
    """Calls `/embeddings` endpoint on OpenAI / DashScope / SiliconFlow compatible servers."""

    # DashScope text-embedding-v3 enforces <= 10 inputs per batch request
    MAX_BATCH_SIZE = 10

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        dimension: int = 128,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dimension = dimension
        self.timeout_seconds = timeout_seconds
        self._fallback = SemanticHashEmbedder(dimension=dimension)

    def _normalize_vector(self, raw_embedding: Any) -> list[float]:
        try:
            raw_vec = [float(v) for v in raw_embedding]
        except (TypeError, ValueError) as exc:
            raise TraceMemoryError(f"Embedding API schema error: {exc}") from exc
        if len(raw_vec) > self.dimension:
            raw_vec = raw_vec[: self.dimension]
        norm = math.sqrt(sum(v * v for v in raw_vec))
        if norm > 1e-12:
            raw_vec = [round(v / norm, 6) for v in raw_vec]
        return raw_vec

    def embed(self, text: str) -> list[float]:
        if not self.api_key or not str(text or "").strip():
            return self._fallback.embed(text)
        data = _post_json_with_retry(
            f"{self.base_url}/embeddings",
            self.api_key,
            {"model": self.model, "input": text},
            timeout_seconds=self.timeout_seconds,
        )
        try:
            first_emb = data["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as exc:
            raise TraceMemoryError(f"Embedding API schema error: {exc}") from exc
        return self._normalize_vector(first_emb)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Batch-embed multiple texts in minimal `/embeddings` round-trips (<= 10 per call)."""
        if not texts:
            return []
        if not self.api_key:
            return [self._fallback.embed(t) for t in texts]

        out: list[list[float] | None] = [None] * len(texts)
        non_empty_indices: list[int] = []
        for idx, t in enumerate(texts):
            if not str(t or "").strip():
                out[idx] = self._fallback.embed(t)
            else:
                non_empty_indices.append(idx)

        if len(non_empty_indices) == 1:
            only_idx = non_empty_indices[0]
            out[only_idx] = self.embed(texts[only_idx])
            return [v for v in out if v is not None]

        for start in range(0, len(non_empty_indices), self.MAX_BATCH_SIZE):
            batch_indices = non_empty_indices[start : start + self.MAX_BATCH_SIZE]
            batch_inputs = [texts[i] for i in batch_indices]
            data = _post_json_with_retry(
                f"{self.base_url}/embeddings",
                self.api_key,
                {"model": self.model, "input": batch_inputs},
                timeout_seconds=self.timeout_seconds,
            )
            raw_items = data.get("data")
            if not isinstance(raw_items, list) or len(raw_items) != len(batch_indices):
                raise TraceMemoryError("Embedding API batch schema mismatch")
            ordered = sorted(
                enumerate(raw_items),
                key=lambda pair: int(pair[1].get("index", pair[0])) if isinstance(pair[1], dict) else pair[0],
            )
            for pos, (_, item) in enumerate(ordered):
                if not isinstance(item, dict) or "embedding" not in item:
                    raise TraceMemoryError("Embedding API item missing embedding vector")
                out[batch_indices[pos]] = self._normalize_vector(item["embedding"])

        return [v if v is not None else self._fallback.embed("") for v in out]


class OpenAICompatLLMProvider:
    """Calls `/chat/completions` for structured memory extraction, Dream consolidation, and QA."""

    provider_name = "openai_compat"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        api_keys: tuple[str, ...] | list[str] = (),
        key_cooldown_seconds: float = 15.0,
    ) -> None:
        if not api_key and not api_keys:
            raise ValueError("TRACE_MEMORY_OPENAI_API_KEY is required when provider='openai'")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or tuple(api_keys)[0]
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._keys = _ApiKeyPool((self.api_key, *tuple(api_keys)), key_cooldown_seconds)
        self._rule_helper = DeterministicLLMProvider()

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST with the key pool: a 429 cools that key and immediately retries on the next one."""
        if len(self._keys) <= 1:
            return _post_json_with_retry(url, self.api_key, payload, timeout_seconds=self.timeout_seconds)
        last_exc: Exception | None = None
        for key in self._keys.candidates():
            if last_exc is not None and self._keys.is_cooling(key):
                break  # every remaining key is cooling: surface the 429 so the queue backs off
            try:
                return _post_json_with_retry(url, key, payload, timeout_seconds=self.timeout_seconds)
            except TraceMemoryError as exc:
                if not _is_rate_limited(exc):
                    raise
                self._keys.cool(key)
                last_exc = exc
        assert last_exc is not None
        raise last_exc

    def _chat_turn(
        self, messages: list[dict[str, Any]], **options: Any
    ) -> AskTurn:
        req_payload = {
            "model": self.model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": messages,
            **options,
        }
        # DashScope Qwen3 hybrid-thinking models require enable_thinking=False for JSON mode
        if "dashscope.aliyuncs.com" in self.base_url:
            req_payload["enable_thinking"] = False

        resp = self._post(f"{self.base_url}/chat/completions", req_payload)
        try:
            msg = resp["choices"][0]["message"]
            if not isinstance(msg, dict):
                raise TypeError("message is not a dict")
        except (KeyError, IndexError, TypeError) as exc:
            raise TraceMemoryError(f"LLM API malformed choices payload: {exc}") from exc
        usage_obj = resp.get("usage")
        usage = usage_obj if isinstance(usage_obj, dict) else {}
        try:
            tokens = int(usage.get("total_tokens", 0) or 0)
        except (TypeError, ValueError):
            tokens = 0
        return AskTurn(message=msg, tokens_used=tokens)

    def _chat_json(self, system_prompt: str, user_prompt: str) -> tuple[dict[str, Any], int]:
        turn = self._chat_turn([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
        raw_content = turn.message.get("content")
        if isinstance(raw_content, dict):
            return raw_content, turn.tokens_used
        if raw_content is None or isinstance(raw_content, str) and not raw_content.strip():
            raise StructuredOutputError(
                "empty_content", "LLM API returned empty message content", turn.tokens_used
            )
        if not isinstance(raw_content, str):
            raise StructuredOutputError(
                "non_object", "LLM API returned non-object structured JSON", turn.tokens_used
            )
        content = raw_content.strip()
        # Strip optional markdown code fences if returned by compatible providers
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.DOTALL).strip()
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise StructuredOutputError(
                "invalid_json", f"LLM API returned invalid JSON content: {exc}", turn.tokens_used
            ) from exc
        if not isinstance(parsed, dict):
            raise StructuredOutputError(
                "non_object", "LLM API returned non-object structured JSON", turn.tokens_used
            )
        return parsed, turn.tokens_used

    def ask_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        allow_tools: bool,
    ) -> AskTurn:
        if not tools:
            return self._chat_turn(messages)
        return self._chat_turn(
            messages, tools=tools, tool_choice="auto" if allow_tools else "none"
        )

    def extract_candidates(
        self,
        note_text: str,
        default_event_time: str,
        existing_memories: list[MemoryItem] | None = None,
        recent_notes: list[dict[str, Any]] | None = None,
    ) -> ExtractionBatch:
        raw_relations = tuple(
            RelationSpec(relation_type=r_type, target=target, context=ctx)
            for r_type, target, ctx in extract_relations_from_markdown(note_text)
        )
        existing_ctx_lines: list[str] = []
        for m in (existing_memories or [])[:30]:
            extra_state = ""
            if m.memory_type == "task":
                extra_state = (
                    f", task_status={m.payload.get('task_status', 'todo')}"
                    f", assignee={m.payload.get('assignee', 'self')}"
                )
            existing_ctx_lines.append(
                f"- [{m.memory_type}] canonical_key={m.canonical_key!r} | title={m.title!r}{extra_state}"
            )
        existing_block = (
            "\n当前已存在的活跃记忆（若用户是在完成/取消/更新其中某项任务或事实，即使有轻微错别字如“告警/告急”或简称，"
            "也必须直接复用该条目的 canonical_key，并将任务完成设为 mutation_hint='complete_task'、取消设为 'cancel_task'；"
            "注意：不同负责人（assignee 不同，例如 self 与其他人的名字）的同类事项属于不同任务，严禁复用同一 canonical_key 合并）：\n"
            + "\n".join(existing_ctx_lines)
            if existing_ctx_lines
            else ""
        )
        window = [w for w in (recent_notes or []) if isinstance(w, dict) and w.get("label")]
        labels = {str(w["label"]) for w in window}
        recent_block = ""
        if window:
            recent_block = (
                "\n\n最近原话（仅供理解指代和上下文，按时间从早到晚；严禁从这些原话中抽取新记忆，它们已各自单独处理）：\n"
                + "\n".join(
                    f"[{w['label']} · {w.get('status') or '已处理'}] {w.get('content', '')}" for w in window
                )
            )
        ref_dt = parse_iso_datetime(default_event_time) or beijing_now()
        ref_iso = to_beijing(ref_dt).replace(microsecond=0).isoformat()
        clock_anchor = format_beijing_prompt_clock(ref_dt)
        context_rules = (
            "\n上下文指代规则（仅当提供了「最近原话」时适用）：\n"
            "- 只从「用户最新原文」抽取候选；最近原话只用来理解“他/她/它/这个/那件事/改到/顺便”等指代与省略。\n"
            "- 每个候选可额外输出 \"relation\": \"new|update|complete|cancel\"、\"refers_to_note\": \"N编号或空字符串\"、"
            "\"changed_fields\": [本次真正改动的字段名]。\n"
            "- 若最新原文是在补充、修改、完成或取消某条最近原话里提到的事项，refers_to_note 填那条原话的编号（如 \"N3\"），"
            "relation 填 update/complete/cancel；若该原话状态显示已生成卡片，canonical_key 复用其 key。\n"
            "- relation=update 时 payload 只需包含改动的字段，changed_fields 列出这些字段（如 [\"due_at\"]），"
            "不要重写用户没提到的标题和其它字段。\n"
            "- 拿不准是新事项还是对最近事项的补充时，优先判为 update 并给出 refers_to_note。\n"
            "- 与最近原话无关的全新事项：relation=\"new\"，refers_to_note=\"\"。"
            if window
            else ""
        )
        sys_prompt = (
            "你是可追溯个人记忆抽取与状态对账引擎。请从用户原文中抽取结构化记忆候选，输出严格 JSON 对象：\n"
            '{"candidates": [{"memory_type": "task|preference|semantic|episodic", '
            '"title": "...", "summary": "...", "canonical_key": "...", '
            '"mutation_hint": "upsert|complete_task|cancel_task|reopen_task", '
            '"payload": {...}, "tags": [...], "event_time": "ISO8601(+08:00)", "quote": "原文摘录"}]}\n'
            "时区与相对时间铁律（极其重要，面向中国大陆用户）：\n"
            "- 系统统一固定使用**北京时间（Asia/Shanghai，UTC+8，偏移量 +08:00）**。\n"
            "- 当用户原文出现「今天」「昨天」「前天」「明天」「后天」「今晚」「明早」「本周五」「下周」等相对时间表达时，"
            "必须严格对照下方给出的【当前北京时间锚点】与【相对日期锚点】换算具体日期，严禁按 UTC 零时区或模型记忆自行猜测日期。\n"
            "- 所有输出时间字段（event_time、task.due_at、episodic.occurred_at）必须输出带 `+08:00` 后缀的标准 ISO8601 字符串"
            "（例如 `2026-11-19T18:00:00+08:00`）；若只提到某天未指定具体几点，截止时间 due_at 默认该日 `23:59:59+08:00`。\n"
            "低成本噪音拦截规则（极其重要）：\n"
            "0. 如果原文只是寒暄、感叹、无意义测试、闲聊水话或乱码（例如“你好”“今天天气不错”“哈哈”“在吗”“测试一下”），"
            "且不包含任何可落地的待办事项(task)、明确个人偏好(preference)、客观长期事实(semantic)或值得记住的个人经历(episodic)，"
            "必须直接返回 {\"candidates\": []}，严禁硬凑或编造记忆！严禁在没有明确任务指代时误把现有任务设为 complete_task 或 cancel_task。\n"
            "各类别 payload 必填字段规范：\n"
            "1. task: {\"task_status\": \"todo\"|\"done\", \"importance\": 1-5整数, \"urgency\": 1-5整数, "
            "\"priority_reason\": \"四象限判定依据（若涉及日期请写具体月日如 11-22，严禁只写明天/今天等相对词）\", "
            "\"start_at\": \"北京时间ISO8601(+08:00)或空字符串\", "
            "\"due_at\": \"北京时间ISO8601(+08:00)或空字符串\", "
            "\"duration_minutes\": 15-1440整数或null, \"is_all_day\": true|false, \"assignee\": \"self\"}\n"
            "   注意：当用户给出明确时间段（如「今天21:00-23:00，我要写作业」「晚上9点到11点」），"
            "必须设置 start_at 为开始时间、due_at 为结束时间、duration_minutes 为持续分钟数（如 120）、is_all_day=false。\n"
            "   注意：当用户表达某事已做完/已修好/已买好时，必须设置 mutation_hint=\"complete_task\" 且 task_status=\"done\"！\n"
            "   importance 评分标尺（与紧急度相互独立，切勿因为“不急”就压低重要度）：5=线上故障/核心交付/重大风险；"
            "4=长期能力成长（学习新技能/考证）、健康、职业发展、重要关系等对长期目标有实质价值的事（不紧急时应落入 Q2）；"
            "3=常规工作或生活事务；2=琐碎杂事（如“有空整理桌面”）；1=可有可无。\n"
            "2. preference: {\"target\": \"偏好对象名\", \"polarity\": \"positive\"|\"negative\"|\"conditional\", "
            "\"conditions\": [], \"exceptions\": [\"例外情况如晚上不喝\"], \"details\": \"补充说明\"}\n"
            "3. semantic: {\"subject\": \"user或实体名\", \"predicate\": \"属性名如residence/workplace\", \"value\": \"当前值\"}\n"
            "4. episodic: {\"event_summary\": \"事件概述\", \"occurred_at\": \"北京时间ISO8601(+08:00)\"}"
            + context_rules
        )
        parsed, tokens = self._chat_json(
            sys_prompt,
            f"当前北京时间锚点: {clock_anchor}\n默认记录时间(北京时间): {ref_iso}{existing_block}{recent_block}"
            f"\n\n用户最新原文:\n{note_text}",
        )
        type_alias = {"fact": "semantic", "experience": "episodic", "todo": "task", "pref": "preference"}
        items: list[ExtractedCandidate] = []
        raw_cands = parsed.get("candidates")
        for raw in (raw_cands if isinstance(raw_cands, list) else []):
            if not isinstance(raw, dict):
                continue
            raw_type = str(raw.get("memory_type", "semantic")).lower().strip()
            mtype = type_alias.get(raw_type, raw_type)
            if mtype not in ("task", "preference", "semantic", "episodic"):
                mtype = "semantic"
            title = str(raw.get("title") or "").strip()
            summary = str(raw.get("summary") or "").strip()
            if not title and not summary:
                continue
            if not title:
                title = summary[:24]
            if not summary:
                summary = title
            raw_payload_obj = raw.get("payload")
            raw_payload = dict(raw_payload_obj) if isinstance(raw_payload_obj, dict) else {}

            # Normalize fallback defaults so LLM schema drift never causes HTTP 400
            if mtype == "preference":
                if not str(raw_payload.get("target") or "").strip():
                    raw_payload["target"] = title
                pol = str(raw_payload.get("polarity") or "positive").lower()
                raw_payload["polarity"] = pol if pol in ("positive", "negative", "conditional") else "positive"
            elif mtype == "semantic":
                raw_payload["subject"] = str(raw_payload.get("subject") or "user").strip() or "user"
                raw_payload["predicate"] = str(raw_payload.get("predicate") or "fact").strip() or "fact"
                raw_payload["value"] = str(raw_payload.get("value") or summary)
            elif mtype == "episodic":
                raw_payload["event_summary"] = str(raw_payload.get("event_summary") or summary)
                if raw_payload.get("occurred_at"):
                    raw_payload["occurred_at"] = _normalize_beijing_iso(
                        str(raw_payload.get("occurred_at")), fallback=ref_iso
                    )
            elif mtype == "task":
                if raw_payload.get("start_at"):
                    raw_payload["start_at"] = _normalize_beijing_iso(str(raw_payload.get("start_at")))
                if raw_payload.get("due_at"):
                    raw_payload["due_at"] = _normalize_beijing_iso(str(raw_payload.get("due_at")))

            ckey = str(raw.get("canonical_key") or "").strip() or (
                f"{mtype}:{slugify_permalink(mtype, title)}"
            )
            hint = str(raw.get("mutation_hint") or "upsert")
            if hint not in ("upsert", "complete_task", "cancel_task", "reopen_task"):
                hint = "upsert"

            raw_tags = raw.get("tags")
            clean_tags = tuple(str(t) for t in (raw_tags if isinstance(raw_tags, list) else []) if t)

            relation = str(raw.get("relation") or "new").strip().lower()
            if relation not in ("new", "update", "complete", "cancel"):
                relation = "new"
            refers = str(raw.get("refers_to_note") or "").strip().upper()
            if refers not in labels:
                refers = ""
            raw_changed = raw.get("changed_fields")
            changed = tuple(
                dict.fromkeys(
                    str(f).strip()
                    for f in (raw_changed if isinstance(raw_changed, list) else [])
                    if str(f).strip() in _ALLOWED_CHANGED_FIELDS
                )
            )
            if relation == "complete" and hint == "upsert":
                hint = "complete_task"
            elif relation == "cancel" and hint == "upsert":
                hint = "cancel_task"
            if relation == "new" and refers:
                relation = "update"
            items.append(
                ExtractedCandidate(
                    memory_type=mtype,
                    title=title,
                    summary=summary,
                    canonical_key=ckey,
                    payload=raw_payload,
                    mutation_hint=hint,
                    tags=clean_tags,
                    relations=raw_relations,
                    event_time=_normalize_beijing_iso(str(raw.get("event_time") or ""), fallback=ref_iso),
                    quote=str(raw.get("quote") or note_text[:120]),
                    relation=relation,
                    refers_to_label=refers,
                    changed_fields=changed,
                )
            )
        return ExtractionBatch(candidates=tuple(items), llm_calls=1, tokens_used=tokens)

    def choose_reference_card(
        self, note_text: str, candidate_title: str, options: list[dict[str, Any]]
    ) -> tuple[int, int, int]:
        """Pick which of several cards the latest note modifies. Returns (index or -1, llm_calls, tokens)."""
        if not options:
            return -1, 0, 0
        lines = []
        for i, o in enumerate(options[:8], start=1):
            pl = o.get("payload") or {}
            extra = ", ".join(
                f"{k}={pl[k]}" for k in ("due_at", "start_at", "task_status", "assignee", "value") if pl.get(k)
            )
            lines.append(f"{i}. {o.get('title', '')}：{o.get('summary', '')}" + (f"（{extra}）" if extra else ""))
        sys_prompt = (
            "你是指代消解助手。用户上一条消息生成了多张卡片，现在用户发来一条新消息在修改/完成/取消其中一张。"
            "请判断新消息指的是哪一张，只输出 JSON：{\"choice\": 编号整数, \"confident\": true|false}。"
            "如果无法确定，输出 {\"choice\": 0, \"confident\": false}，严禁猜测。"
        )
        user_prompt = (
            "候选卡片：\n" + "\n".join(lines)
            + f"\n\n用户新消息：{note_text}\n（抽取出的意图标题：{candidate_title}）"
        )
        parsed, tokens = self._chat_json(sys_prompt, user_prompt)
        try:
            choice = int(parsed.get("choice") or 0)
        except (TypeError, ValueError):
            choice = 0
        confident = parsed.get("confident", True)
        if isinstance(confident, str):
            confident = confident.strip().lower() not in ("false", "0", "no", "")
        if not confident or not (1 <= choice <= min(8, len(options))):
            return -1, 1, tokens
        return choice - 1, 1, tokens

    def synthesize_dream_cluster(
        self, topic: str, memories: list[MemoryItem]
    ) -> DreamBatch:
        if len(memories) < 2:
            return DreamBatch(insights=(), llm_calls=0, tokens_used=0)
        mem_lines = "\n".join(f"- [{m.memory_id}] ({m.permalink}) {m.summary}" for m in memories)
        sys_prompt = (
            "你是睡眠记忆归纳引擎（Dream Consolidator）。请基于给定的多条事实/经历，提炼一条跨记录的长期规律或经验，"
            '返回 JSON: {"title": "...", "summary": "...", "value": "..."}'
        )
        parsed, tokens = self._chat_json(sys_prompt, f"主题：{topic}\n记忆列表：\n{mem_lines}")
        rels = tuple(
            RelationSpec(relation_type="derived_from", target=m.permalink, context="Dream 归纳来源")
            for m in memories[:6]
        )
        insight = DreamInsight(
            title=str(parsed.get("title") or f"【归纳洞察】{topic}"),
            summary=str(parsed.get("summary") or ""),
            canonical_key=f"dream:insight:{slugify_permalink('semantic', topic)}",
            payload={
                "subject": topic,
                "predicate": "dream_consolidated_pattern",
                "value": str(parsed.get("value") or parsed.get("summary") or ""),
            },
            source_memory_ids=tuple(m.memory_id for m in memories[:6]),
            tags=("dream_insight", topic),
            relations=rels,
        )
        return DreamBatch(insights=(insight,), llm_calls=1, tokens_used=tokens)

    def plan_ask(
        self, question: str, tools: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], int]:
        prompt = (
            "你是个人记忆问答的消息拆解和工具规划助手。只输出计划，不回答问题或执行操作。\n"
            "1. 每个独立目标输出一个检索问题。只有需要分别检索的独立目标才拆开，"
            "单目标沿用用户原句。拆开时保留用户指定的对象、时间及其他限定，并使每个子问题可单独检索。"
            "最多 3 个问题，超过时返回 "
            '{"overflow":true,"questions":[],"tool_calls":[]}。\n'
            "2. 对原问题或每个子问题，只有工具能直接获取该问题所需的证据时才规划调用。"
            "整次问答的首批工具调用最多 1 个。若有多个适用调用，选最能补足关键证据的一个。"
            "工具用途仅与主题相关时，交给检索。没有适用的调用时，tool_calls 返回空数组。"
            "question 必须是从 1 开始的整数序号，arguments 必须是 JSON 对象。"
            "工具参数根据用户表达和工具定义填写，相对时间以提供的北京时间为准。\n"
            '只输出 JSON 对象，格式为 {"overflow":false,"questions":["..."],'
            '"tool_calls":[{"question":1,"name":"...","arguments":{}}]}。'
        )
        return self._chat_json(
            prompt,
            f"北京时间锚点：{format_beijing_prompt_clock()}\n"
            f"工具定义：{json.dumps(tools, ensure_ascii=False)}\n用户问题：{question}",
        )

    def build_ask_messages(
        self,
        question: str,
        query_mode: str,
        memories: list[MemoryItem],
        notes: list[RawNote],
        history_versions: list[dict[str, Any]],
        graph_relations: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        ctx_mem = "\n".join(
            f"- [{m.memory_id}] ({m.memory_type} | {m.assertion_kind} | event_time={m.event_time or 'N/A'} | tags={list(m.tags)}) "
            f"{m.title}: {m.summary} | payload={m.payload}"
            for m in memories[:24]
        )
        rel_lines = [
            f"- {r.get('from_title') or r.get('from_memory_id', '')} [{r.get('relation_type', 'relates_to')}] -> "
            f"{r.get('to_title') or r.get('to_memory_id', '')}"
            + (f" ({r['context']})" if r.get("context") else "")
            + f" (from_id={r.get('from_memory_id', '')}, to_id={r.get('to_memory_id', '')})"
            for r in (graph_relations or [])[:16]
        ]
        ctx_rels = "\n".join(rel_lines) if rel_lines else "（无）"
        ctx_note = "\n".join(
            f"- [{n.note_id}] ({n.event_time}) {n.visible_content()}" for n in notes[:12]
        )
        clock_anchor = format_beijing_prompt_clock()
        sys_prompt = (
            "你是严格基于证据回答的个人记忆助手。只能依据提供的记忆、关联图谱关系、历史版本和原文作答，不得编造。"
            "系统统一固定使用**北京时间（Asia/Shanghai，UTC+8 / +08:00）**："
            "用户提问中的「今天」「昨天」「前天」「明天」「后天」「最近」等相对时间均须严格对照提供的北京时间锚点判定，"
            "回答中提及日期和时间也统一使用北京时间。"
            "请充分结合每条记忆的 `event_time`、`tags`、`payload` 结构化属性（如 task 的 assignee/quadrant/due_at，"
            "semantic 的 subject/predicate/value/previous_values，episodic 的 occurred_at/participants，preference 的 exceptions）"
            "以及「关联图谱关系」推理跨实体的因果或关联关系。"
            "注意：带有 `[刚录入·整理中]` 标记的原始记录属于用户刚刚提交、后台正在异步整理中的最新真实原话，应优先作为最新事实参考。"
            "回答可使用清晰的中文分点结构，务必在 cited_memory_ids 和 cited_note_ids 中准确列出实际引用的 ID；若证据不足以回答问题，请如实说明并将 has_sufficient_evidence 设为 false。"
            '请输出 JSON: {"answer": "...", "cited_memory_ids": [...], "cited_note_ids": [...], '
            '"has_sufficient_evidence": true|false}'
        )
        user_msg = (
            f"当前北京时间: {clock_anchor}\n查询模式: {query_mode}\n问题: {question}\n\n"
            f"候选记忆:\n{ctx_mem}\n\n关联图谱关系:\n{ctx_rels}\n\n"
            f"原始记录:\n{ctx_note}\n\n历史版本:\n{history_versions[:16]}"
        )
        return [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ]

    def synthesize_answer(
        self,
        question: str,
        query_mode: str,
        memories: list[MemoryItem],
        notes: list[RawNote],
        history_versions: list[dict[str, Any]],
        graph_relations: list[dict[str, Any]] | None = None,
    ) -> AskSynthesis:
        if not memories and not notes and not graph_relations:
            return self._rule_helper.synthesize_answer(
                question, query_mode, memories, notes, history_versions, graph_relations=graph_relations
            )
        system, user = self.build_ask_messages(
            question,
            query_mode,
            memories,
            notes,
            history_versions,
            graph_relations=graph_relations,
        )
        parsed, tokens = self._chat_json(system["content"], user["content"])
        raw_mem_ids = parsed.get("cited_memory_ids")
        raw_note_ids = parsed.get("cited_note_ids")
        raw_suff = parsed.get("has_sufficient_evidence", True)
        if isinstance(raw_suff, bool):
            has_sufficient = raw_suff
        elif isinstance(raw_suff, str):
            has_sufficient = raw_suff.strip().lower() not in ("false", "0", "no", "")
        else:
            has_sufficient = bool(raw_suff)
        return AskSynthesis(
            answer=str(parsed.get("answer", "")),
            cited_memory_ids=tuple(str(x) for x in raw_mem_ids) if isinstance(raw_mem_ids, list) else (),
            cited_note_ids=tuple(str(x) for x in raw_note_ids) if isinstance(raw_note_ids, list) else (),
            has_sufficient_evidence=has_sufficient,
            llm_calls=1,
            tokens_used=tokens,
        )

    def decompose_task(
        self,
        task_title: str,
        task_summary: str = "",
        context_memories: list[MemoryItem] | None = None,
    ) -> DecomposeBatch:
        ctx_lines: list[str] = []
        for m in (context_memories or [])[:8]:
            ctx_lines.append(f"- [{m.memory_type}] {m.title}: {m.summary}")
        ctx_block = "\n".join(ctx_lines) if ctx_lines else "（暂无关联长期记忆）"
        sys_prompt = (
            "你是个人记忆驱动的任务拆解助手。请结合用户的长期记忆（偏好/事实/经历），"
            "将给定的待办任务拆解为 3 到 6 个具体、立即可执行的子步骤（Checklist）。\n"
            '严格输出 JSON: {"subtasks": [{"title": "具体可执行步骤", "estimated_minutes": 15}], '
            '"memory_context_used": ["引用到的个人记忆要点简述"]}'
        )
        user_msg = (
            f"当前北京时间：{format_beijing_prompt_clock()}\n"
            f"任务标题：{task_title}\n"
            f"任务补充说明：{task_summary or task_title}\n\n"
            f"检索到的个人长期记忆背景：\n{ctx_block}"
        )
        parsed, tokens = self._chat_json(sys_prompt, user_msg)
        steps: list[SubtaskSuggestion] = []
        raw_subtasks = parsed.get("subtasks")
        for raw in (raw_subtasks if isinstance(raw_subtasks, list) else [])[:6]:
            if isinstance(raw, str):
                t = raw.strip()
                est = 15
            elif isinstance(raw, dict):
                t = str(raw.get("title") or "").strip()
                try:
                    est_val = int(raw.get("estimated_minutes") or 15)
                except (TypeError, ValueError):
                    est_val = 15
                est = max(5, min(180, est_val))
            else:
                continue
            if t:
                steps.append(SubtaskSuggestion(title=t, estimated_minutes=est))
        if not steps:
            return self._rule_helper.decompose_task(task_title, task_summary, context_memories)
        raw_used = parsed.get("memory_context_used")
        used_ctx = tuple(
            str(x) for x in (raw_used if isinstance(raw_used, list) else []) if str(x).strip()
        )
        return DecomposeBatch(
            subtasks=tuple(steps),
            memory_context_used=used_ctx,
            llm_calls=1,
            tokens_used=tokens,
        )
