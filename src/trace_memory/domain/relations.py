"""Knowledge graph relations, permalink generation, and `memory://` URI helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any


_SLUG_CLEAN_RE = re.compile(r"[^\w\u4e00-\u9fff\-]+", re.UNICODE)
_MULTI_DASH_RE = re.compile(r"-{2,}")
_EXPLICIT_REL_RE = re.compile(
    r"^\s*-\s*(?:(?P<rel>[a-zA-Z0-9_\-\u4e00-\u9fff]+)\s+)?\[\[(?P<target>[^\]\n]+)\]\](?:\s*\((?P<ctx>[^\)\n]*)\))?\s*$"
)
_INLINE_WIKILINK_RE = re.compile(r"\[\[([^\]\n]+)\]\]")


def slugify_permalink(memory_type: str, title: str, fallback_suffix: str = "") -> str:
    """Generate a human- and LLM-readable semantic permalink (supports CJK + ASCII)."""
    prefix_map = {
        "task": "tasks",
        "preference": "preferences",
        "semantic": "facts",
        "episodic": "episodes",
    }
    folder = prefix_map.get(memory_type, "notes")
    raw = _SLUG_CLEAN_RE.sub("-", title.strip().lower()).strip("-")
    raw = _MULTI_DASH_RE.sub("-", raw)
    if not raw:
        digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
        raw = f"item-{digest}"
    if len(raw) > 48:
        raw = raw[:48].rstrip("-")
    if fallback_suffix:
        raw = f"{raw}-{fallback_suffix}"
    return f"{folder}/{raw}"


def parse_memory_uri(identifier: str) -> str:
    """Normalize a `memory://foo/bar`, `[[foo/bar]]`, or bare permalink/ID."""
    cleaned = identifier.strip()
    if cleaned.startswith("memory://"):
        cleaned = cleaned[len("memory://") :]
    if cleaned.startswith("[[") and cleaned.endswith("]]"):
        cleaned = cleaned[2:-2]
    return cleaned.strip().strip("/")


def extract_relations_from_markdown(body: str) -> list[tuple[str, str, str]]:
    """Extract `(relation_type, target, context)` triples from Markdown body.

    Follows Basic Memory's syntax:
    - Explicit list item: `- depends_on [[Target]] (context)` -> `("depends_on", "Target", "context")`
    - Bare list wikilink: `- [[Target]]` -> `("relates_to", "Target", "")`
    - Inline prose `[[Target]]` -> `("links_to", "Target", "")`
    """
    results: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    for line in body.splitlines():
        m = _EXPLICIT_REL_RE.match(line)
        if m:
            rel_type = (m.group("rel") or "relates_to").strip()
            target = parse_memory_uri(m.group("target"))
            ctx = (m.group("ctx") or "").strip()
            key = (rel_type, target)
            if target and key not in seen:
                seen.add(key)
                results.append((rel_type, target, ctx))
            continue

        for inline_target in _INLINE_WIKILINK_RE.findall(line):
            target = parse_memory_uri(inline_target)
            key = ("links_to", target)
            if target and key not in seen:
                seen.add(key)
                results.append(("links_to", target, ""))

    return results


@dataclass(frozen=True)
class MemoryRelation:
    """Directed semantic edge owned by `from_memory_id`.

    Supports Basic Memory's forward-reference contract:
    When target doesn't exist yet (or is later deleted), `to_memory_id` is empty while
    `to_target_text` stays intact so forward-reference resolution can link it later.
    """

    relation_id: str
    space_id: str
    from_memory_id: str
    relation_type: str
    to_target_text: str
    to_memory_id: str = ""
    context: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "space_id": self.space_id,
            "from_memory_id": self.from_memory_id,
            "relation_type": self.relation_type,
            "to_target_text": self.to_target_text,
            "to_memory_id": self.to_memory_id or None,
            "resolved": bool(self.to_memory_id),
            "context": self.context,
        }
