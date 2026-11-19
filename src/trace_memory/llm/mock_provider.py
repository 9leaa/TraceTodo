"""Zero-API-Key Deterministic Semantic Embedder & Extraction/Dream/Ask Engine.

Designed so the entire system — including cross-word semantic vector similarity,
Eisenhower 4-quadrant task classification, C01-C12 domain invariants, Dream consolidation,
and evidence-cited Q&A — works deterministically offline in milliseconds.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import math
import re
from typing import Any

from trace_memory.domain.memories import MemoryItem, parse_iso_datetime
from trace_memory.domain.notes import BEIJING_TZ, RawNote, beijing_now, to_beijing
from trace_memory.domain.proposals import RelationSpec
from trace_memory.domain.relations import extract_relations_from_markdown, slugify_permalink
from trace_memory.llm.protocol import (
    AskSynthesis,
    DecomposeBatch,
    DreamBatch,
    DreamInsight,
    ExtractedCandidate,
    ExtractionBatch,
    SubtaskSuggestion,
)


# Concept clusters enable true semantic recall even when surface words don't overlap!
_CONCEPT_CLUSTERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "CONCEPT_PAYMENT",
        ("支付", "微信支付", "支付宝", "结算", "账单", "对账", "收款", "退款", "交易", "payment", "billing"),
    ),
    (
        "CONCEPT_INCIDENT",
        ("故障", "报错", "超时", "异常", "崩溃", "风险", "隐患", "漏洞", "修复", "修好", "bug", "outage", "timeout", "fix"),
    ),
    (
        "CONCEPT_RESIDENCE",
        ("搬到", "搬家", "住在", "居住", "定居", "住所", "常住", "哪个城市", "住哪", "live", "moved"),
    ),
    (
        "CONCEPT_COFFEE_DRINK",
        ("咖啡", "美式", "拿铁", "浓缩", "饮品", "喝什么", "提神", "coffee", "espresso", "latte"),
    ),
    (
        "CONCEPT_ARCHITECTURE",
        ("架构", "重构", "技术债", "演进", "设计方案", "性能", "扩展性", "模块", "向量", "architecture", "refactor"),
    ),
    (
        "CONCEPT_ADMIN_CHORE",
        ("转发", "通知", "报销", "贴发票", "快递", "订餐", "会议链接", "表格统计", "顺手"),
    ),
    (
        "CONCEPT_REPORT_DOC",
        ("周报", "月报", "总结", "汇报", "文档", "笔记", "读书", "复盘", "report"),
    ),
    (
        "CONCEPT_CAREER_INTERVIEW",
        ("面试", "简历", "算法题", "项目准备", "求职", "offer", "八股", "系统设计", "interview"),
    ),
)


class SemanticHashEmbedder:
    """Concept-augmented character/word n-gram L2-normalized dense vector embedder."""

    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension

    def _project_feature(self, vec: list[float], feature: str, weight: float) -> None:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        h1 = int.from_bytes(digest[:4], "little")
        h2 = int.from_bytes(digest[4:], "little")
        idx1 = h1 % self.dimension
        sign1 = 1.0 if (h1 & 1) == 0 else -1.0
        idx2 = h2 % self.dimension
        sign2 = 1.0 if (h2 & 1) == 0 else -1.0
        vec[idx1] += sign1 * weight
        vec[idx2] += sign2 * (weight * 0.5)

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        cleaned = text.strip().lower()
        if not cleaned:
            return vec

        # 1. Inject semantic concept cluster activations (high weight for cross-word recall)
        for cluster_name, keywords in _CONCEPT_CLUSTERS:
            hits = sum(1 for kw in keywords if kw in cleaned)
            if hits > 0:
                self._project_feature(vec, f"cluster:{cluster_name}", 3.2 * min(hits, 3))

        # 2. Inject word & CJK character 2-grams / 3-grams (skipping grammatical stop-chars for c1)
        stop_chars = set("的了是在我有和就都而及与着或把被让给向从到个吗呢啊吧么你他她它")
        words = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", cleaned)
        for token in words:
            if re.match(r"^[a-z0-9_]+$", token):
                self._project_feature(vec, f"w:{token}", 1.5)
            else:
                for ch in token:
                    if ch not in stop_chars:
                        self._project_feature(vec, f"c1:{ch}", 0.45)
                for i in range(len(token) - 1):
                    bg = token[i : i + 2]
                    if not all(c in stop_chars for c in bg):
                        self._project_feature(vec, f"c2:{bg}", 1.4)
                for i in range(len(token) - 2):
                    self._project_feature(vec, f"c3:{token[i:i+3]}", 1.6)

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 1e-12:
            vec = [round(v / norm, 6) for v in vec]
        return vec


_DATE_EXPLICIT_RE = re.compile(r"(20\d{2})[-年/](\d{1,2})(?:[-月/](\d{1,2})日?)?")
_YEAR_ONLY_RE = re.compile(r"(20\d{2})年")
_ASSIGNEE_PREFIX_RE = re.compile(r"^(?P<who>小[王李张刘陈赵]|同事[A-Z]?|[A-Z][a-z]+)负责(?P<rest>.+)$")
_RESIDENCE_RE = re.compile(
    r"(?:我|目前|现在|之前|以前|当时)?(?:搬到|搬去|定居在?|住在|常住在?)(?P<city>北京|上海|深圳|广州|杭州|成都|武汉|南京|苏州|西安|东京|新加坡)"
)
_WORKPLACE_RE = re.compile(
    r"(?:我|目前|现在)?在(?P<org>[A-Za-z0-9\u4e00-\u9fff]{2,16})(?:担任|做|负责|工作)(?P<role>[A-Za-z0-9\u4e00-\u9fff]{0,12})"
)
_PREF_POS_RE = re.compile(r"(?:我)?(?:很|非常|比较|平时)?(?:喜欢|偏好|习惯|爱喝|爱吃|倾向于)(?P<rest>[^。；\n]+)")
_PREF_NEG_RE = re.compile(r"(?:我)?(?:很|非常)?(?:不喜欢|讨厌|忌口|反感|不爱)(?P<rest>[^。；\n]+)")


def _infer_event_time(clause: str, default_iso: str) -> str:
    base_dt = to_beijing(parse_iso_datetime(default_iso) or beijing_now())
    m_full = _DATE_EXPLICIT_RE.search(clause)
    if m_full:
        year = int(m_full.group(1))
        month = max(1, min(12, int(m_full.group(2))))
        day = max(1, min(28, int(m_full.group(3) or 1)))
        return datetime(year, month, day, 12, 0, tzinfo=BEIJING_TZ).isoformat()
    m_year = _YEAR_ONLY_RE.search(clause)
    if m_year:
        year = int(m_year.group(1))
        return datetime(year, 6, 1, 12, 0, tzinfo=BEIJING_TZ).isoformat()
    if "前天" in clause:
        return (base_dt - timedelta(days=2)).replace(microsecond=0).isoformat()
    if "昨天" in clause or "昨日" in clause:
        return (base_dt - timedelta(days=1)).replace(microsecond=0).isoformat()
    if "去年" in clause:
        return (base_dt - timedelta(days=365)).replace(microsecond=0).isoformat()
    if "上个月" in clause:
        return (base_dt - timedelta(days=30)).replace(microsecond=0).isoformat()
    if "三年前" in clause:
        return (base_dt - timedelta(days=365 * 3)).replace(microsecond=0).isoformat()
    return base_dt.replace(microsecond=0).isoformat()


def _infer_due_at(clause: str, default_iso: str) -> str:
    base_dt = to_beijing(parse_iso_datetime(default_iso) or beijing_now())
    m_full = _DATE_EXPLICIT_RE.search(clause)
    if m_full and any(k in clause for k in ("前", "截止", "之前", "due", "交付")):
        year = int(m_full.group(1))
        month = max(1, min(12, int(m_full.group(2))))
        day = max(1, min(28, int(m_full.group(3) or 28)))
        return datetime(year, month, day, 18, 0, tzinfo=BEIJING_TZ).isoformat()
    if any(k in clause for k in ("今天", "今日", "下午", "今晚", "马上", "立刻")):
        return (base_dt + timedelta(hours=8)).replace(microsecond=0).isoformat()
    if any(k in clause for k in ("明天", "明日")):
        return (base_dt + timedelta(hours=28)).replace(microsecond=0).isoformat()
    if any(k in clause for k in ("后天", "两天内", "48小时")):
        return (base_dt + timedelta(hours=46)).replace(microsecond=0).isoformat()
    if any(k in clause for k in ("本周", "这周", "周五前", "周三前", "下周")):
        return (base_dt + timedelta(days=5)).replace(microsecond=0).isoformat()
    if any(k in clause for k in ("下个月", "本季度", "年底", "长期")):
        return (base_dt + timedelta(days=30)).replace(microsecond=0).isoformat()
    return ""


def _score_task_importance_urgency(clause: str, due_at: str) -> tuple[int, int, str]:
    imp = 3
    urg = 2
    reasons: list[str] = []

    if any(
        k in clause
        for k in ("线上", "支付", "核心", "架构", "面试", "安全", "漏洞", "故障", "bug", "必须", "关键", "里程碑", "发布", "主链路")
    ):
        imp = 5
        reasons.append("涉及核心交付/关键质量或个人核心目标（高重要度）")
    elif any(k in clause for k in ("设计", "方案", "优化", "测试", "文档", "学习", "复盘", "周报", "评审")):
        imp = 4
        reasons.append("属于研发质量、规划或成长建设事项（重要）")
    elif any(k in clause for k in ("转发", "帮同事", "订餐", "快递", "报销", "通知", "统计表格")):
        imp = 2
        reasons.append("属于日常协作或行政事务（低重要度）")

    if any(k in clause for k in ("有空", "闲暇", "以后", "随缘", "顺便", "不急", "慢慢")):
        imp = min(imp, 2)
        urg = 1
        reasons.append("明确标注非紧迫/闲暇处理")

    if any(k in clause for k in ("马上", "立刻", "紧急", "今天", "下午", "今晚", "立刻修", "明天")):
        urg = 5
        reasons.append("时间窗口极短或标注紧急（高紧急度）")
    elif any(k in clause for k in ("后天", "周五前", "周三前", "尽快", "这周")):
        urg = max(urg, 4)
        reasons.append("近期存在明确截止节点")
    elif due_at and urg < 3:
        urg = 3

    if not reasons:
        reasons.append("常规待办事项，默认居中评估")

    return imp, urg, "；".join(reasons)


def _clean_task_core_title(clause: str) -> tuple[str, str, int]:
    """Return `(clean_title, assignee, round_num)` for accurate canonical task matching (C04/C05)."""
    text = re.sub(r"\[\[[^\]\n]+\]\]", "", clause).strip(" ，。！？；!?;")

    # Detect explicit round / batch number (C05: same person different rounds shouldn't merge)
    round_num = 0
    m_round = re.search(r"第([一二三四五六七八九十\d]+)(?:轮|期|版|次)", text)
    if m_round:
        mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
        raw_r = m_round.group(1)
        round_num = mapping.get(raw_r, int(raw_r) if raw_r.isdigit() else 1)

    assignee = "self"
    m_who = _ASSIGNEE_PREFIX_RE.match(text)
    if m_who:
        assignee = m_who.group("who")
        text = m_who.group("rest").strip()
    elif text.startswith("我负责"):
        text = text[len("我负责") :].strip()

    # Strip state transition words and temporal prefixes to get stable task subject
    prefixes = (
        "待办：",
        "待办:",
        "任务：",
        "任务:",
        "提醒我",
        "记得",
        "需要",
        "必须",
        "得把",
        "先把",
        "把",
        "我已经",
        "我已",
        "已经",
        "已",
        "今天下午三点前",
        "今天下午",
        "今天",
        "明天",
        "后天",
        "周五前",
        "周三前",
        "这周",
        "下周",
        "有空的时候",
        "有空时",
        "有空",
        "请重新开启",
        "重新开启",
        "重新打开",
        "恢复任务",
        "重开",
    )
    changed = True
    while changed:
        changed = False
        for p in prefixes:
            if text.startswith(p):
                text = text[len(p) :].strip()
                changed = True

    suffixes = (
        "已经完成了",
        "已完成",
        "完成了",
        "做完了",
        "搞定了",
        "修好了",
        "修好",
        "交了",
        "已经取消了",
        "取消了",
        "不做了",
        "不用做了",
        "放弃了",
        "重新开启",
    )
    for s in suffixes:
        if text.endswith(s) and len(text) > len(s) + 1:
            text = text[: -len(s)].strip()
            break

    return (text or clause.strip()), assignee, round_num


class DeterministicLLMProvider:
    """High-precision deterministic NLP extractor, Dream synthesizer, and grounded QA engine."""

    provider_name = "deterministic_mock"

    def extract_candidates(
        self,
        note_text: str,
        default_event_time: str,
        existing_memories: list[MemoryItem] | None = None,
    ) -> ExtractionBatch:
        raw_relations = [
            RelationSpec(relation_type=r_type, target=target, context=ctx)
            for r_type, target, ctx in extract_relations_from_markdown(note_text)
        ]

        # Split compound sentences while keeping conditional `但是/但/不过` attached for preferences!
        clauses = [
            c.strip()
            for c in re.split(r"[。！？\n；;]+", note_text)
            if c.strip() and not c.strip().startswith("- ")
        ]
        if not clauses and note_text.strip():
            clauses = [note_text.strip()]

        candidates: list[ExtractedCandidate] = []

        for clause in clauses:
            ev_time = _infer_event_time(clause, default_event_time)

            # 1. Check task state transitions first (C04)
            if any(k in clause for k in ("重新开启", "重新打开", "重开", "恢复待办", "恢复任务")):
                title, assignee, round_num = _clean_task_core_title(clause)
                ckey = f"task:{assignee}:r{round_num}:{slugify_permalink('task', title)}"
                candidates.append(
                    ExtractedCandidate(
                        memory_type="task",
                        title=title,
                        summary=f"任务已明确重新开启：{title}",
                        canonical_key=ckey,
                        payload={"task_status": "todo", "assignee": assignee},
                        mutation_hint="reopen_task",
                        relations=tuple(raw_relations),
                        event_time=ev_time,
                        quote=clause,
                    )
                )
                continue

            if any(k in clause for k in ("取消了", "已经取消", "不做了", "不用做了", "放弃了")):
                title, assignee, round_num = _clean_task_core_title(clause)
                reason_code = "dropped" if "放弃" in clause else "cancelled"
                ckey = f"task:{assignee}:r{round_num}:{slugify_permalink('task', title)}"
                candidates.append(
                    ExtractedCandidate(
                        memory_type="task",
                        title=title,
                        summary=f"任务已结束（{reason_code}）：{title}",
                        canonical_key=ckey,
                        payload={
                            "task_status": "done",
                            "close_reason": reason_code,
                            "assignee": assignee,
                        },
                        mutation_hint="cancel_task",
                        relations=tuple(raw_relations),
                        event_time=ev_time,
                        quote=clause,
                    )
                )
                continue

            if any(
                k in clause
                for k in ("完成了", "已完成", "已修复", "已经修复", "修好了", "做完了", "搞定了", "已经修好", "已交付", "买好了", "已买")
            ):
                title, assignee, round_num = _clean_task_core_title(clause)
                ckey = f"task:{assignee}:r{round_num}:{slugify_permalink('task', title)}"
                candidates.append(
                    ExtractedCandidate(
                        memory_type="task",
                        title=title,
                        summary=f"任务已完成：{title}",
                        canonical_key=ckey,
                        payload={
                            "task_status": "done",
                            "close_reason": "completed",
                            "assignee": assignee,
                        },
                        mutation_hint="complete_task",
                        relations=tuple(raw_relations),
                        event_time=ev_time,
                        quote=clause,
                    )
                )
                continue

            # 2. Check Preferences (with conditions & exceptions C03)
            m_pos = _PREF_POS_RE.search(clause)
            m_neg = _PREF_NEG_RE.search(clause) if not m_pos else None
            if m_pos or m_neg:
                polarity = "positive" if m_pos else "negative"
                rest = (m_pos.group("rest") if m_pos else m_neg.group("rest")).strip()  # type: ignore[union-attr]
                exceptions: list[str] = []
                conditions: list[str] = []
                # Split exception clauses like "喜欢喝咖啡，但晚上不喝" or "除了..."
                parts = re.split(r"[，,]?\s*(?:但是|但|不过|只是|除了)", rest, maxsplit=1)
                target = parts[0].strip(" ，,。")
                if len(parts) > 1 and parts[1].strip():
                    exc_text = parts[1].strip(" ，,。")
                    exceptions.append(exc_text)
                if "时" in target or "的时候" in clause:
                    m_cond = re.search(r"([^，,]+的时候)", clause)
                    if m_cond:
                        conditions.append(m_cond.group(1))
                # Normalize target noun (e.g., "喝美式咖啡" / "钻研向量检索算法")
                norm_target = re.sub(r"^(钻研|研究|学习|探索|关注|喝|吃|用|看|写)", "", target).strip() or target
                ckey = f"pref:{slugify_permalink('preference', norm_target)}"
                summary_parts = [f"{'偏好' if polarity == 'positive' else '排斥'}：{target}"]
                if exceptions:
                    summary_parts.append(f"（例外/限制：{'; '.join(exceptions)}）")
                candidates.append(
                    ExtractedCandidate(
                        memory_type="preference",
                        title=f"关于{norm_target}的偏好",
                        summary="".join(summary_parts),
                        canonical_key=ckey,
                        payload={
                            "target": norm_target,
                            "polarity": polarity,
                            "conditions": conditions,
                            "exceptions": exceptions,
                            "details": clause,
                        },
                        tags=("preference", norm_target[:12]),
                        relations=tuple(raw_relations),
                        event_time=ev_time,
                        quote=clause,
                    )
                )
                continue

            # 2.5 Check Past-Tense Episodic Events before future Task cues
            if any(k in clause for k in ("参加了", "经历了", "去了", "开完", "举办了", "出差")) and not any(
                t in clause for t in ("待办", "提醒我", "必须", "明天")
            ):
                candidates.append(
                    ExtractedCandidate(
                        memory_type="episodic",
                        title=clause[:28],
                        summary=clause,
                        canonical_key=f"episode:{slugify_permalink('episodic', clause[:24])}",
                        payload={
                            "event_summary": clause,
                            "occurred_at": ev_time,
                        },
                        tags=("episode",),
                        relations=tuple(raw_relations),
                        event_time=ev_time,
                        quote=clause,
                    )
                )
                continue

            # 3. Check Task creation cues
            task_cues = (
                "待办",
                "任务",
                "需要",
                "必须",
                "记得",
                "提醒我",
                "周报",
                "修复",
                "修好",
                "完成",
                "整理",
                "准备",
                "转发",
                "帮同事",
                "重构",
                "实现",
                "搭建",
                "撰写",
                "提交",
                "复习",
                "负责",
                "规划",
                "设计",
                "评审",
                "跟进",
                "处理",
                "排查",
                "解决",
                "交付",
                "上线",
                "制作",
                "开发",
                "编写",
                "采购",
                "购买",
                "学习",
                "汇报",
            )
            if any(k in clause for k in task_cues):
                title, assignee, round_num = _clean_task_core_title(clause)
                due_at = _infer_due_at(clause, default_event_time)
                imp, urg, p_reason = _score_task_importance_urgency(clause, due_at)
                if assignee != "self":
                    imp = min(imp, 3)  # Delegated / other person's task
                ckey = f"task:{assignee}:r{round_num}:{slugify_permalink('task', title)}"
                display_title = f"[{assignee}] {title}" if assignee != "self" else title
                if round_num > 0 and f"第{round_num}" not in display_title and "轮" not in display_title:
                    display_title = f"{display_title} (第{round_num}轮)"
                candidates.append(
                    ExtractedCandidate(
                        memory_type="task",
                        title=display_title,
                        summary=clause,
                        canonical_key=ckey,
                        payload={
                            "task_status": "todo",
                            "importance": imp,
                            "urgency": urg,
                            "priority_reason": p_reason,
                            "due_at": due_at,
                            "assignee": assignee,
                        },
                        tags=("task",),
                        relations=tuple(raw_relations),
                        event_time=ev_time,
                        quote=clause,
                    )
                )
                continue

            # 4. Check Semantic Facts (residence, workplace, general knowledge)
            m_res = _RESIDENCE_RE.search(clause)
            if m_res:
                city = m_res.group("city")
                candidates.append(
                    ExtractedCandidate(
                        memory_type="semantic",
                        title="当前居住城市",
                        summary=f"居住城市：{city}",
                        canonical_key="fact:user:residence",
                        payload={
                            "subject": "user",
                            "predicate": "residence",
                            "value": city,
                            "event_time": ev_time,
                        },
                        tags=("profile", "location"),
                        relations=tuple(raw_relations),
                        event_time=ev_time,
                        quote=clause,
                    )
                )
                continue

            m_work = _WORKPLACE_RE.search(clause)
            if m_work:
                org = m_work.group("org")
                role = m_work.group("role") or "成员"
                val = f"{org} {role}".strip()
                candidates.append(
                    ExtractedCandidate(
                        memory_type="semantic",
                        title="工作单位与职责",
                        summary=f"就职于 {val}",
                        canonical_key="fact:user:workplace",
                        payload={
                            "subject": "user",
                            "predicate": "workplace",
                            "value": val,
                            "event_time": ev_time,
                        },
                        tags=("profile", "career"),
                        relations=tuple(raw_relations),
                        event_time=ev_time,
                        quote=clause,
                    )
                )
                continue

            # 5. Check Episodic Events
            if any(k in clause for k in ("参加了", "经历了", "去了", "开完", "举办了", "出差", "复盘会")):
                candidates.append(
                    ExtractedCandidate(
                        memory_type="episodic",
                        title=clause[:28],
                        summary=clause,
                        canonical_key=f"episode:{slugify_permalink('episodic', clause[:24])}",
                        payload={
                            "event_summary": clause,
                            "occurred_at": ev_time,
                        },
                        tags=("episode",),
                        relations=tuple(raw_relations),
                        event_time=ev_time,
                        quote=clause,
                    )
                )
                continue

            # 6. Generic factual statement fallback (only when informative >= 6 chars and not pure greeting/noise)
            noise_words = {"你好", "测试", "哈哈", "嗯嗯", "好的", "收到", "无聊", "随便说说"}
            cleaned_plain = re.sub(r"[^\w\u4e00-\u9fff]", "", clause)
            if len(cleaned_plain) >= 6 and cleaned_plain not in noise_words:
                candidates.append(
                    ExtractedCandidate(
                        memory_type="semantic",
                        title=clause[:26],
                        summary=clause,
                        canonical_key=f"fact:{slugify_permalink('semantic', clause[:24])}",
                        payload={
                            "subject": "context",
                            "predicate": "statement",
                            "value": clause,
                            "event_time": ev_time,
                        },
                        tags=("fact",),
                        relations=tuple(raw_relations),
                        event_time=ev_time,
                        quote=clause,
                    )
                )

        tokens_est = max(16, len(note_text) * 2) if candidates else 8
        return ExtractionBatch(
            candidates=tuple(candidates),
            llm_calls=1 if note_text.strip() else 0,
            tokens_used=tokens_est,
        )

    def synthesize_dream_cluster(
        self, topic: str, memories: list[MemoryItem]
    ) -> DreamBatch:
        if len(memories) < 2:
            return DreamBatch(insights=(), llm_calls=0, tokens_used=0)

        summaries = [m.summary for m in memories[:6]]
        joined = "；".join(summaries)
        rels = tuple(
            RelationSpec(relation_type="derived_from", target=m.permalink, context="Dream 归纳来源")
            for m in memories[:6]
        )
        insight = DreamInsight(
            title=f"【归纳洞察】{topic}长期规律",
            summary=f"基于 {len(memories)} 条记录归纳（{topic}）：{joined[:160]}",
            canonical_key=f"dream:insight:{slugify_permalink('semantic', topic)}",
            payload={
                "subject": topic,
                "predicate": "dream_consolidated_pattern",
                "value": joined[:240],
            },
            source_memory_ids=tuple(m.memory_id for m in memories[:6]),
            tags=("dream_insight", topic),
            relations=rels,
        )
        tokens_est = 120 + sum(len(m.summary) * 2 for m in memories[:6])
        return DreamBatch(insights=(insight,), llm_calls=1, tokens_used=tokens_est)

    def synthesize_answer(
        self,
        question: str,
        query_mode: str,
        memories: list[MemoryItem],
        notes: list[RawNote],
        history_versions: list[dict[str, Any]],
    ) -> AskSynthesis:
        if not memories and not notes:
            return AskSynthesis(
                answer="抱歉，在当前记忆库和原始记录中均未找到与您问题相关的可靠证据，无法作答（不编造未记录的信息）。",
                cited_memory_ids=(),
                cited_note_ids=(),
                has_sufficient_evidence=False,
                llm_calls=1,
                tokens_used=40,
            )

        lines: list[str] = []
        cited_mems: list[str] = []
        cited_notes: list[str] = []

        if query_mode == "history" and history_versions:
            lines.append("根据历史版本与原文追溯，状态变迁如下：")
            for hv in history_versions[:5]:
                lines.append(
                    f"- [v{hv['version']} | {hv['created_at'][:10]}] {hv['summary']}（原因：{hv['change_reason']}）"
                )

        if memories:
            header = "相关记忆状态：" if not lines else "当前最新生效记忆："
            lines.append(header)
            for m in memories[:5]:
                cited_mems.append(m.memory_id)
                tag_kind = "推断" if m.assertion_kind == "inferred" else "陈述"
                extra = ""
                if m.memory_type == "task":
                    q_label = m.payload.get("quadrant_label", m.payload.get("quadrant", ""))
                    t_status = m.payload.get("task_status", "todo")
                    extra = f" [{t_status} | {q_label}]"
                lines.append(f"- 【{m.title}】({tag_kind}){extra}：{m.summary} (`{m.uri}`)")

        if notes:
            lines.append("原始记录证据摘录：")
            for n in notes[:3]:
                cited_notes.append(n.note_id)
                lines.append(f"- ({n.event_time[:10]}) “{n.visible_content()}” [`{n.note_id}`]")

        answer_text = "\n".join(lines)
        tokens_est = 80 + len(answer_text)
        return AskSynthesis(
            answer=answer_text,
            cited_memory_ids=tuple(cited_mems),
            cited_note_ids=tuple(cited_notes),
            has_sufficient_evidence=True,
            llm_calls=1,
            tokens_used=tokens_est,
        )

    def decompose_task(
        self,
        task_title: str,
        task_summary: str = "",
        context_memories: list[MemoryItem] | None = None,
    ) -> DecomposeBatch:
        combined = f"{task_title} {task_summary}".strip()
        ctx_list = list(context_memories or [])
        used_ctx: list[str] = []

        steps: list[SubtaskSuggestion] = []
        if any(k in combined for k in ("婚", "婚礼", "婚庆", "结婚")):
            steps = [
                SubtaskSuggestion("列出必备婚庆物料清单与预算上限", 15),
                SubtaskSuggestion("核对核心礼服、喜糖及伴手礼数量", 20),
                SubtaskSuggestion("比价并下单采购，确认送达时间", 30),
                SubtaskSuggestion("收货验收并按使用场景分箱打包", 20),
            ]
        elif any(k in combined for k in ("论文", "汇报", "答辩", "ppt", "slides", "报告")):
            steps = [
                SubtaskSuggestion("提炼核心论点与实验对比结论", 20),
                SubtaskSuggestion("制作汇报大纲与关键图表页", 35),
                SubtaskSuggestion("补充细节数据与问答 Q&A 备忘", 20),
                SubtaskSuggestion("计时演练一遍并压缩超时部分", 15),
            ]
        elif any(k in combined for k in ("bug", "故障", "超时", "报错", "告警", "修复", "线上")):
            steps = [
                SubtaskSuggestion("查看监控日志与最近变更，复现定位根因", 15),
                SubtaskSuggestion("编写修复补丁并补充边界回归用例", 25),
                SubtaskSuggestion("预发环境验证接口响应与超时阈值", 15),
                SubtaskSuggestion("灰度发布并持续观察 15 分钟告警指标", 15),
            ]
        elif any(k in combined for k in ("架构", "方案", "设计", "重构", "向量", "算法")):
            steps = [
                SubtaskSuggestion("明确目标指标、约束边界与现有痛点", 20),
                SubtaskSuggestion("对比 2 套备选技术方案并记录权衡", 30),
                SubtaskSuggestion("画出核心数据流图与接口契约", 25),
                SubtaskSuggestion("输出可执行里程碑拆解并评审确认", 15),
            ]
        else:
            short = task_title[:18]
            steps = [
                SubtaskSuggestion(f"明确「{short}」的完成标准与所需资料", 10),
                SubtaskSuggestion("完成前置准备与环境/工具检查", 15),
                SubtaskSuggestion(f"专注推进核心执行：{short}", 30),
                SubtaskSuggestion("自检结果并整理收尾记录", 10),
            ]

        # Weave retrieved personal memories (preferences/facts) into steps & audit trail
        for mem in ctx_list[:3]:
            if mem.memory_type == "preference":
                target = str(mem.payload.get("target") or mem.title)
                exc = ", ".join(mem.payload.get("exceptions") or [])
                note = f"偏好「{target}」" + (f"（注意：{exc}）" if exc else "")
                used_ctx.append(note)
                if "咖啡" in target and any(k in combined for k in ("加班", "夜", "复习", "汇报")):
                    steps.append(SubtaskSuggestion(f"注意个人偏好约束：{note}", 5))
            elif mem.memory_type == "semantic":
                used_ctx.append(f"{mem.title}：{mem.payload.get('value') or mem.summary}")
            else:
                used_ctx.append(mem.summary[:40])

        if ctx_list and len(steps) < 5:
            first_hint = used_ctx[0]
            steps.insert(1, SubtaskSuggestion(f"对齐已知背景（{first_hint[:28]}）", 10))

        return DecomposeBatch(
            subtasks=tuple(steps[:6]),
            memory_context_used=tuple(used_ctx[:4]),
            llm_calls=1,
            tokens_used=64 + len(combined),
        )
