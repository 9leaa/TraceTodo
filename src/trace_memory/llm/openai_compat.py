"""OpenAI-compatible (DeepSeek / Qwen / OpenAI) HTTP LLM & Embedding provider.

Uses Python standard library `urllib.request` with strict timeouts and single timeout retry,
so it works in any Python environment without requiring heavy SDK dependencies, while also
falling back cleanly or surfacing explicit errors on misconfiguration.
"""

from __future__ import annotations

from datetime import datetime
import json
import math
import re
import socket
from typing import Any
import urllib.error
import urllib.request

from trace_memory.domain.memories import MemoryItem
from trace_memory.domain.notes import RawNote
from trace_memory.domain.proposals import RelationSpec
from trace_memory.domain.relations import extract_relations_from_markdown, slugify_permalink
from trace_memory.llm.mock_provider import DeterministicLLMProvider, SemanticHashEmbedder
from trace_memory.llm.protocol import (
    AskSynthesis,
    DecomposeBatch,
    DreamBatch,
    DreamInsight,
    ExtractedCandidate,
    ExtractionBatch,
    SubtaskSuggestion,
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
    last_err: Exception | None = None
    # At most 1 retry strictly on socket timeout (mirroring Suixinji's LLM boundary rule)
    for attempt in range(2):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except socket.timeout as exc:
            last_err = exc
            if attempt == 0:
                continue
            raise
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                detail = str(exc)
            raise RuntimeError(f"LLM API HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout) and attempt == 0:
                last_err = exc
                continue
            raise RuntimeError(f"LLM API request failed: {exc}") from exc
    raise RuntimeError(f"LLM API timed out: {last_err}")


class OpenAICompatEmbedder:
    """Calls `/embeddings` endpoint on OpenAI / DashScope / SiliconFlow compatible servers."""

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

    def embed(self, text: str) -> list[float]:
        if not self.api_key:
            return self._fallback.embed(text)
        data = _post_json_with_retry(
            f"{self.base_url}/embeddings",
            self.api_key,
            {"model": self.model, "input": text},
            timeout_seconds=self.timeout_seconds,
        )
        raw_vec: list[float] = data["data"][0]["embedding"]
        if len(raw_vec) > self.dimension:
            raw_vec = raw_vec[: self.dimension]
        norm = math.sqrt(sum(v * v for v in raw_vec))
        if norm > 1e-12:
            raw_vec = [round(v / norm, 6) for v in raw_vec]
        return raw_vec


class OpenAICompatLLMProvider:
    """Calls `/chat/completions` for structured memory extraction, Dream consolidation, and QA."""

    provider_name = "openai_compat"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("TRACE_MEMORY_OPENAI_API_KEY is required when provider='openai'")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._rule_helper = DeterministicLLMProvider()

    def _chat_json(self, system_prompt: str, user_prompt: str) -> tuple[dict[str, Any], int]:
        req_payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        # DashScope Qwen3 hybrid-thinking models require enable_thinking=False for JSON mode
        if "dashscope.aliyuncs.com" in self.base_url:
            req_payload["enable_thinking"] = False

        resp = _post_json_with_retry(
            f"{self.base_url}/chat/completions",
            self.api_key,
            req_payload,
            timeout_seconds=self.timeout_seconds,
        )
        content = str(resp["choices"][0]["message"]["content"]).strip()
        # Strip optional markdown code fences if returned by compatible providers
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.DOTALL).strip()
        tokens = int(resp.get("usage", {}).get("total_tokens", 0))
        return json.loads(content), tokens

    def extract_candidates(
        self,
        note_text: str,
        default_event_time: str,
        existing_memories: list[MemoryItem] | None = None,
    ) -> ExtractionBatch:
        raw_relations = tuple(
            RelationSpec(relation_type=r_type, target=target, context=ctx)
            for r_type, target, ctx in extract_relations_from_markdown(note_text)
        )
        existing_ctx_lines: list[str] = []
        for m in (existing_memories or [])[:30]:
            extra_state = ""
            if m.memory_type == "task":
                extra_state = f", task_status={m.payload.get('task_status', 'todo')}"
            existing_ctx_lines.append(
                f"- [{m.memory_type}] canonical_key={m.canonical_key!r} | title={m.title!r}{extra_state}"
            )
        existing_block = (
            "\n当前已存在的活跃记忆（若用户是在完成/取消/更新其中某项任务或事实，即使有轻微错别字如“告警/告急”或简称，"
            "也必须直接复用该条目的 canonical_key，并将任务完成设为 mutation_hint='complete_task'、取消设为 'cancel_task'）：\n"
            + "\n".join(existing_ctx_lines)
            if existing_ctx_lines
            else ""
        )
        sys_prompt = (
            "你是可追溯个人记忆抽取与状态对账引擎。请从用户原文中抽取结构化记忆候选，输出严格 JSON 对象：\n"
            '{"candidates": [{"memory_type": "task|preference|semantic|episodic", '
            '"title": "...", "summary": "...", "canonical_key": "...", '
            '"mutation_hint": "upsert|complete_task|cancel_task|reopen_task", '
            '"payload": {...}, "tags": [...], "event_time": "ISO8601", "quote": "原文摘录"}]}\n'
            "低成本噪音拦截规则（极其重要）：\n"
            "0. 如果原文只是寒暄、感叹、无意义测试、闲聊水话或乱码（例如“你好”“今天天气不错”“哈哈”“在吗”“测试一下”），"
            "且不包含任何可落地的待办事项(task)、明确个人偏好(preference)、客观长期事实(semantic)或值得记住的个人经历(episodic)，"
            "必须直接返回 {\"candidates\": []}，严禁硬凑或编造记忆！严禁在没有明确任务指代时误把现有任务设为 complete_task 或 cancel_task。\n"
            "各类别 payload 必填字段规范：\n"
            "1. task: {\"task_status\": \"todo\"|\"done\", \"importance\": 1-5整数, \"urgency\": 1-5整数, "
            "\"priority_reason\": \"四象限判定依据\", \"due_at\": \"ISO8601或空字符串\", \"assignee\": \"self\"}\n"
            "   注意：当用户表达某事已做完/已修好/已买好时，必须设置 mutation_hint=\"complete_task\" 且 task_status=\"done\"！\n"
            "2. preference: {\"target\": \"偏好对象名\", \"polarity\": \"positive\"|\"negative\"|\"conditional\", "
            "\"conditions\": [], \"exceptions\": [\"例外情况如晚上不喝\"], \"details\": \"补充说明\"}\n"
            "3. semantic: {\"subject\": \"user或实体名\", \"predicate\": \"属性名如residence/workplace\", \"value\": \"当前值\"}\n"
            "4. episodic: {\"event_summary\": \"事件概述\", \"occurred_at\": \"ISO8601\"}"
        )
        parsed, tokens = self._chat_json(
            sys_prompt,
            f"默认时间: {default_event_time}{existing_block}\n\n用户最新原文:\n{note_text}",
        )
        type_alias = {"fact": "semantic", "experience": "episodic", "todo": "task", "pref": "preference"}
        items: list[ExtractedCandidate] = []
        for raw in parsed.get("candidates") or []:
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
            raw_payload = dict(raw.get("payload") or {})

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

            ckey = str(raw.get("canonical_key") or "").strip() or (
                f"{mtype}:{slugify_permalink(mtype, title)}"
            )
            hint = str(raw.get("mutation_hint") or "upsert")
            if hint not in ("upsert", "complete_task", "cancel_task", "reopen_task"):
                hint = "upsert"

            items.append(
                ExtractedCandidate(
                    memory_type=mtype,
                    title=title,
                    summary=summary,
                    canonical_key=ckey,
                    payload=raw_payload,
                    mutation_hint=hint,
                    tags=tuple(str(t) for t in (raw.get("tags") or []) if t),
                    relations=raw_relations,
                    event_time=str(raw.get("event_time") or default_event_time),
                    quote=str(raw.get("quote") or note_text[:120]),
                )
            )
        return ExtractionBatch(candidates=tuple(items), llm_calls=1, tokens_used=tokens)

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

    def synthesize_answer(
        self,
        question: str,
        query_mode: str,
        memories: list[MemoryItem],
        notes: list[RawNote],
        history_versions: list[dict[str, Any]],
    ) -> AskSynthesis:
        if not memories and not notes:
            return self._rule_helper.synthesize_answer(
                question, query_mode, memories, notes, history_versions
            )
        ctx_mem = "\n".join(
            f"- [{m.memory_id}] ({m.assertion_kind}) {m.title}: {m.summary} | payload={m.payload}"
            for m in memories[:6]
        )
        ctx_note = "\n".join(
            f"- [{n.note_id}] ({n.event_time}) {n.visible_content()}" for n in notes[:5]
        )
        now_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M (%A)")
        sys_prompt = (
            "你是严格基于证据回答的个人记忆助手。只能依据提供的记忆和原文作答，不得编造。"
            "回答可使用清晰的中文分点结构，务必在 cited_memory_ids 和 cited_note_ids 中准确列出实际引用的 ID。"
            '请输出 JSON: {"answer": "...", "cited_memory_ids": [...], "cited_note_ids": [...], '
            '"has_sufficient_evidence": true|false}'
        )
        user_msg = (
            f"当前本地时间: {now_str}\n查询模式: {query_mode}\n问题: {question}\n\n"
            f"候选记忆:\n{ctx_mem}\n\n原始记录:\n{ctx_note}\n\n历史版本:\n{history_versions[:5]}"
        )
        parsed, tokens = self._chat_json(sys_prompt, user_msg)
        return AskSynthesis(
            answer=str(parsed.get("answer", "")),
            cited_memory_ids=tuple(str(x) for x in parsed.get("cited_memory_ids", [])),
            cited_note_ids=tuple(str(x) for x in parsed.get("cited_note_ids", [])),
            has_sufficient_evidence=bool(parsed.get("has_sufficient_evidence", True)),
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
            f"任务标题：{task_title}\n"
            f"任务补充说明：{task_summary or task_title}\n\n"
            f"检索到的个人长期记忆背景：\n{ctx_block}"
        )
        parsed, tokens = self._chat_json(sys_prompt, user_msg)
        steps: list[SubtaskSuggestion] = []
        for raw in (parsed.get("subtasks") or [])[:6]:
            if isinstance(raw, str):
                t = raw.strip()
                est = 15
            elif isinstance(raw, dict):
                t = str(raw.get("title") or "").strip()
                est = max(5, min(180, int(raw.get("estimated_minutes") or 15)))
            else:
                continue
            if t:
                steps.append(SubtaskSuggestion(title=t, estimated_minutes=est))
        if not steps:
            return self._rule_helper.decompose_task(task_title, task_summary, context_memories)
        used_ctx = tuple(str(x) for x in (parsed.get("memory_context_used") or []) if str(x).strip())
        return DecomposeBatch(
            subtasks=tuple(steps),
            memory_context_used=used_ctx,
            llm_calls=1,
            tokens_used=tokens,
        )
