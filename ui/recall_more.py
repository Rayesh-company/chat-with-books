"""The «جست‌وجوی بیشتر» operation (ADR-0010): ONE broaden call over the
question and the pool the ask already gathered — reasoning out up to
TWO short Farsi queries for adjacent, not-yet-covered facets — then one
pinned searcher per query (the dive kernel's `dive_recall`, HYBRID
pinned server-side), merged into the pool's NEW passages only. The
upstream seam is this module's `urlopen` attribute (the picker's
shape); the searchers run through ui.dive's own seam."""

from __future__ import annotations

import json
import re
from urllib.request import urlopen

try:
    from ui.composer import _composer_content, _composer_reply
    from ui.dive import dive_recall
    from ui.guard import _count_word, _json_object, normalize_for_match
except ImportError:  # the container runs serve.py as a script beside the modules
    from composer import _composer_content, _composer_reply
    from dive import dive_recall
    from guard import _count_word, _json_object, normalize_for_match

# The widen's bounds: at most this many facet queries per click, and
# each existing passage rides into the broaden prompt only as a short
# digest — the prompt stays small however full the pool.
RECALL_MORE_MAX_QUERIES = 2
RECALL_MORE_DIGEST = 120


def build_broaden_prompt(question: str, sources) -> str:
    """The broaden call's brief: find the ADJACENT ground the pool has
    not covered — never restate the question, never repeat a covered
    facet. Queries only; the searcher answers them, not the model."""
    digest = "\n".join(
        f"- {str(source.get('passage', ''))[:RECALL_MORE_DIGEST]}…"
        for source in list(sources)[:40]
        if isinstance(source, dict)
    ) or "- (the pool is empty)"
    return (
        "You are widening the search of a Farsi Q&A over a fixed set of "
        "Books.\n\n"
        f"Question: {question}\n\n"
        "The passages already retrieved (digests — the ground already "
        f"covered):\n{digest}\n\n"
        "Task: write up to "
        f"{_count_word(RECALL_MORE_MAX_QUERIES)} SHORT Farsi search "
        "queries for the question's adjacent facets the digests do NOT "
        "already cover — related themes, opposing sides, neighboring "
        "concepts a fuller answer needs. Each query one line, phrased "
        "for a book search. Never restate the question itself; never "
        "repeat a covered facet.\n\n"
        "Reply with ONLY a JSON array of Farsi strings, no prose, no "
        "code fence:\n"
        '["پرسش جست‌وجوی اول؟", "پرسش جست‌وجوی دوم؟"]'
    )


def parse_broaden_reply(content) -> list:
    """The broaden reply's queries, guarded: strings only, capped,
    deduped; [] on any junk — a failed widen contributes nothing."""
    stripped = _json_object_array(content)
    queries = []
    seen = set()
    for item in stripped:
        if not isinstance(item, str) or not item.strip():
            continue
        key = normalize_for_match(item)
        if key in seen:
            continue
        seen.add(key)
        queries.append(item.strip())
    return queries[:RECALL_MORE_MAX_QUERIES]


def _json_object_array(content):
    """A JSON array out of a model reply, or [] — the picker's parse
    conventions (fence strip, bracket-scoped retry)."""
    if not isinstance(content, str) or not content.strip():
        return []
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    try:
        parsed = json.loads(stripped)
    except ValueError:
        match = re.search(r"\[.*\]", stripped, re.DOTALL)
        if not match:
            return []
        try:
            parsed = json.loads(match.group(0))
        except ValueError:
            return []
    return parsed if isinstance(parsed, list) else []


def broaden_queries(question: str, sources) -> list:
    """One broaden call (thinking on — coverage reasoning); the guarded
    facet queries, [] on any failure."""
    try:
        reply = _composer_reply(
            build_broaden_prompt(question, sources),
            "enabled",
            urlopen_fn=urlopen,
        )
        content = _composer_content(reply)
    except (KeyError, ValueError, OSError):
        return []
    return parse_broaden_reply(content)


def recall_more(question: str, sources, datasets=None) -> list:
    """The whole operation: broaden, search each facet once (the dive
    kernel's pinned searcher), and return only the pool's NEW passages
    — deduped on the guard's normalized letter stream against what the
    ask already holds. A facet whose searcher returns nothing (or a
    broaden that fails) contributes nothing; [] is the honest shape."""
    existing = {
        normalize_for_match(source.get("passage", ""))
        for source in sources
        if isinstance(source, dict)
    }
    queries = broaden_queries(question, sources)
    if not queries:
        return []
    fresh = []
    for query in queries:
        for source in dive_recall(query, datasets):
            key = normalize_for_match(source["passage"])
            if key in existing:
                continue
            existing.add(key)
            fresh.append(source)
    return fresh
