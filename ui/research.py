"""The Research Mode engine (ADR-0008, ADR-0009): the Wayfinder as a
guided journey. Phase 3 of the sheet is a multi-turn chat that walks
the user from a loose ask to a citation-backed Brief — not by running
operations at them, but by WAYFINDING: naming the destination, mapping
the ground the Books actually hold, working the open questions one at
a time, and keeping the whole route visible on a shared map.

Three layers:

- the CONVERSATION layer: every message is intent-classified first; a
  casual question, a concept to learn, or a source lookup is answered
  normally through one searcher and one guarded writer call — no
  research formalism;

- the JOURNEY layer (ADR-0009, from the Wayfinder skill): a stage
  machine — orientation, mapping, investigating, synthesizing,
  drafting — moved ONLY by code on observable state; guided-question
  turns (the agent asks one sharp question and NEVER answers it
  itself); a visible map (destination, decisions index, named open
  questions, fog of war, out of scope); and a narrator that opens
  every operation's reply with what just changed and what is next;

- the RESEARCH STATE layer: research-oriented turns update a persistent
  state (versioned research question, scope, named open questions,
  evidence ledger, claims, gaps) and execute at most ONE bounded
  operation — evidence gathering (the dive's fan-out: up to six
  searchers, two rounds), synthesis over the accumulated pool, the
  closing Research Brief built FROM the state, or a pure-code evidence
  audit.

Every reply that states a Book fact is written over real passages and
runs the SAME verbatim guard as phases 2 and 3; references are appended
server-side from the passages actually quoted; a sub-question the Books
cannot feed becomes an honest gap entry, never a hallucinated fill. The
guided questions and narrations are process speech — they never state
Book content, so there is nothing for the guard to check and nothing to
hallucinate. A turn is a job in an in-process registry (the dive's
shape: 202 + poll, one in-flight turn per phone, three globally,
cooperative abort on a new ask); the SESSION itself persists in
ui.research_store's SQLite, so a restart loses only the in-flight turn,
never the investigation.

The engine talks to the composer endpoint through this module's ONE
`urlopen` attribute (the dive's seam shape) and to the second Cognee
service through ui.dive's searchers — the scripted-upstream tests patch
ui.research.urlopen and ui.dive.urlopen."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from urllib.request import urlopen

try:
    from ui.composer import _composer_content, _composer_reply
    from ui.dive import (
        BOOK_DATASETS,
        DIVE_MAX_SUB_QUESTIONS,
        dive_recall,
        dive_retrieve,
    )
    from ui.guard import (
        _count_word,
        _json_object,
        _numbered_passages,
        _strip_code_fence,
        guard_blocks,
        normalize_for_match,
        parse_quoted_reply,
    )
    from ui import research_store
except ImportError:  # the container runs serve.py as a script beside the modules
    from composer import _composer_content, _composer_reply
    from dive import (
        BOOK_DATASETS,
        DIVE_MAX_SUB_QUESTIONS,
        dive_recall,
        dive_retrieve,
    )
    from guard import (
        _count_word,
        _json_object,
        _numbered_passages,
        _strip_code_fence,
        guard_blocks,
        normalize_for_match,
        parse_quoted_reply,
    )
    import research_store

# The Wayfinder's model: the same flash pin the dive ran on (both tiers
# moved to glm-5.3-flash 2026-09-13, operator call — deep-dive latency
# was the pain). Pinned in source like every model pin — never via env.
RESEARCH_MODEL = "glm-5.3-flash"
# Research behavior strengthens with intent, never applies uniformly: a
# turn runs AT MOST one operation, and the fixed chip commands below map
# to one deterministically — the chips the sheet offers are exactly
# these texts, so a chip press needs no classification luck.
COMMAND_GATHER = "شواهد بیشتری از کتاب‌ها پیدا کن"
COMMAND_SYNTHESIZE = "شواهد را تحلیل و جمع‌بندی کن"
COMMAND_BRIEF = "خلاصۀ پژوهش را بنویس"
COMMAND_AUDIT = "ادعاها و استنادها را بازبینی کن"
COMMAND_MOVES = {
    COMMAND_GATHER: "gather",
    COMMAND_SYNTHESIZE: "synthesize",
    COMMAND_BRIEF: "brief",
    COMMAND_AUDIT: "audit",
}
# The journey layer's own deterministic texts (ADR-0009): the
# gather-everything chip, the skip that ends a guided question round
# without an answer, and the guide chip that pulls the journey back on
# track after a conversational detour. All resolve server-side — no
# classification luck.
COMMAND_GATHER_ALL = "همهٔ پرسش‌های باز را جست‌وجو کن"
GRILLING_SKIP = "فعلاً همین کافی است؛ ادامه بده"
COMMAND_GUIDE = "ادامهٔ سفر پژوهش"
# A per-question gather chip: «شواهدِ «نام» را پیدا کن» targets exactly
# that named open question — the deterministic frontier move.
TARGETED_GATHER_RE = re.compile(r"^شواهدِ «(.+)» را پیدا کن$")
# The journey's five stages (ADR-0009). Transitions are CODE-decided on
# observable state — the model proposes content, never moves stages.
RESEARCH_STAGES = ("orientation", "mapping", "investigating", "synthesizing", "drafting")
STAGE_LABELS = {
    "orientation": "نام‌گذاری مقصد",
    "mapping": "نقشه‌برداری",
    "investigating": "گردآوری شواهد",
    "synthesizing": "تحلیل و جمع‌بندی",
    "drafting": "نوشتن خلاصه",
}
# A guided-question (grilling) round asks ONE thing and waits; the stage
# gives up asking after this many rounds and proceeds on what it has.
RESEARCH_GRILLING_STAGE_CAP = 3
# The decision cooldown (ADR-0011): after an accepted or rejected
# proposal of a kind, this many turns park no NEW proposal of that kind
# — the damper that breaks the rewrite→approve planning loop.
PROPOSAL_COOLDOWN_TURNS = 2
# The Brief plan (ADR-0012, T7) — the proposed section plan of the
# closing Brief, each section tied to its named open questions and the
# claims that will support them — is a THIRD proposal kind in the
# decide flow: parked like every chart edit only on an exploration
# turn, damped by its own `brief_plan` cooldown key, and accepted
# APPEND-ONLY (a version per acceptance, never a rewrite). Accepting
# it is what unlocks section writing (T8).
# The strangler switch: the one-shot Brief of today keeps running
# until the per-section writer (T8) lands — then the Brief demands an
# accepted plan and this flips to True.
BRIEF_PLANS_REQUIRED = False
# The open questions' statuses: pending (the frontier), searched (fed),
# gap (the Books could not feed it — the honest outcome).
QUESTION_STATUS_LABELS = {
    "pending": "در انتظار",
    "searched": "جست‌وجو شد",
    "gap": "شکاف",
}
# The intent vocabulary (Wayfinder §4). Unknown or missing intents fall
# back to the first — the conversational path — so a malformed classify
# reply can never push a casual chat into research machinery.
RESEARCH_INTENTS = (
    "casual_question",
    "concept_learning",
    "source_lookup",
    "research_exploration",
    "active_research",
    "drafting",
    "evidence_audit",
)

# The dispatch table (ADR-0012): one row per Research skill, each a
# declared contract — purpose (the router prompt prints it), display
# name (the approved plain-Persian naming table, CONTEXT.md — T10,
# GitLab #11), kind (chat / chart / work), the allowed stages (None =
# any; the seam where stage validation runs), its caps, the Tools it
# may search with, the state it reads and writes, and whether its
# output passes the verbatim guard. Adding a skill touches this table
# and nothing else: the router's prompt and its dispatch are generated
# from it.
SKILL_TABLE = (
    {
        "name": "casual_question",
        "display_name": "میزبان",
        "purpose": "answer normally, no research step",
        "kind": "chat",
        "runner": "chat",
        "allowed_stages": None,
        "caps": "one searcher, one guarded writer",
        "tools": ("hybrid",),
        "state_reads": ("datasets",),
        "state_writes": ("diagnoses",),
        "guarded": True,
    },
    {
        "name": "concept_learning",
        "display_name": "میزبان",
        "purpose": "teach a concept from the Books, no research step",
        "kind": "chat",
        "runner": "chat",
        "allowed_stages": None,
        "caps": "one searcher, one guarded writer",
        "tools": ("hybrid",),
        "state_reads": ("datasets",),
        "state_writes": ("diagnoses",),
        "guarded": True,
    },
    {
        "name": "source_lookup",
        "display_name": "میزبان",
        "purpose": "locate where the Books say it, no research step",
        "kind": "chat",
        "runner": "chat",
        "allowed_stages": None,
        "caps": "one searcher, one guarded writer",
        "tools": ("hybrid",),
        "state_reads": ("datasets",),
        "state_writes": ("diagnoses",),
        "guarded": True,
    },
    {
        "name": "research_exploration",
        "display_name": "راهنما",
        "purpose": "discovering or adjusting the research direction",
        "kind": "chart",
        "runner": "research",
        "allowed_stages": None,
        "caps": "≤3 guided questions per stage; proposal cooldowns",
        "tools": ("hybrid",),
        "state_reads": ("research_question", "map", "grilling", "decisions"),
        "state_writes": (
            "research_question",
            "scope",
            "brief_plan",
            "subquestions",
            "concepts",
            "fog",
            "decisions",
            "map",
            "grilling",
            "stage",
            "phase",
            "diagnoses",
        ),
        "guarded": False,
    },
    {
        "name": "active_research",
        "display_name": "جست‌وجوگر",
        "purpose": "execute the investigation",
        "kind": "work",
        "runner": "research",
        "allowed_stages": None,
        "caps": "≤6 searchers × ≤2 rounds per gather",
        "tools": ("hybrid",),
        "state_reads": ("research_question", "map", "subquestions", "evidence", "datasets"),
        "state_writes": (
            "evidence",
            "gaps",
            "subquestions",
            "claims",
            "decisions",
            "map",
            "grilling",
            "stage",
            "phase",
            "diagnoses",
        ),
        "guarded": False,
    },
    {
        "name": "drafting",
        "display_name": "نویسنده",
        "purpose": "produce the closing research brief",
        "kind": "work",
        "runner": "brief",
        "allowed_stages": None,
        "caps": "one writer pass, one retry",
        "tools": ("hybrid",),
        "state_reads": ("research_question", "claims", "gaps", "evidence", "brief_plan"),
        "state_writes": ("stage", "phase", "diagnoses"),
        "guarded": True,
    },
    {
        "name": "evidence_audit",
        "display_name": "بازبین",
        "purpose": "review the claims and their citations",
        "kind": "work",
        "runner": "audit",
        "allowed_stages": None,
        "caps": "pure code, no upstream calls",
        "tools": (),
        "state_reads": ("claims", "gaps"),
        "state_writes": (),
        "guarded": False,
    },
)
SKILL_TABLE_BY_NAME = {row["name"]: row for row in SKILL_TABLE}


def validate_skill_pick(row: dict, state: dict):
    """The code-side validation of a model-picked skill (ADR-0012): the
    row's allowed stages against the state's stage — the seam where
    later tickets hang the budget and cooldown checks. (ok, detail)."""
    allowed = row.get("allowed_stages")
    if allowed and state.get("stage") not in allowed:
        stage = state.get("stage", "")
        return False, f"{row['name']} not allowed at stage {stage}"
    return True, ""


def record_diagnosis(state: dict, detail: str, fallback: str) -> None:
    """One diagnosable router event (ADR-0012): what broke and where
    the turn fell back — the record that retires the silent degrade."""
    state.setdefault("diagnoses", []).append(
        {
            "kind": "router",
            "detail": detail,
            "fallback": fallback,
            "turn": state.get("turns", 0),
        }
    )
CONVERSATIONAL_INTENTS = {"casual_question", "concept_learning", "source_lookup"}
# A gather pools under this many passages and keeps offering more — six
# searchers usually pool far past it in one round (the dive's sections
# wove pairs from two each).
RESEARCH_EVIDENCE_FLOOR = 6
# The state's working lists stay bounded: sub-questions and concepts cap
# here, newest kept, the classify call itself capped at six per turn.
RESEARCH_MAX_SUBQUESTIONS = 12
RESEARCH_MAX_CONCEPTS = 18
# The session's soft cost bound: at this many turns every reply suggests
# finalizing the Brief — a suggestion, never a refusal.
RESEARCH_SESSION_TURN_CAP = 40
# The turn's HARD cost bound (ADR-0012, T3): a wall-clock deadline plus
# an upstream-call cap, both worker configuration at the highest point
# (run_research_turn's arguments). The deadline is the spec's 10
# minutes; the cap sits above every legitimate single-operation turn
# today (worst gather ≈ 19 upstream calls) and below a runaway chain.
RESEARCH_TURN_DEADLINE_SECONDS = 600
RESEARCH_TURN_CALL_CAP = 24
# The claim ledger's statuses (Wayfinder §11, minus External Knowledge:
# this platform answers from the Books only, so out-of-corpus content is
# labeled commentary in notes and may never enter a claim). The first
# two are the code-derived ones; insufficient_support is the gaps'.
CLAIM_STATUSES = {
    "direct_support": "پشتوانهٔ مستقیم",
    "supported_synthesis": "ترکیب شواهد",
    "insufficient_support": "شواهد ناکافی",
}

# Farsi progress events, appended one per state transition — the turn
# poll's observable timeline.
RESEARCH_EVENT_CLASSIFYING = "برداشت گفتگو…"
RESEARCH_EVENT_PLANNING = "برنامه‌ریزی پژوهش…"
RESEARCH_EVENT_SEARCHING = "جست‌وجوی شواهد در کتاب‌ها…"
RESEARCH_EVENT_WRITING = "نوشتن پاسخ…"
RESEARCH_EVENT_ANALYZING = "نوشتن تحلیل…"
RESEARCH_EVENT_BRIEF = "نوشتن خلاصۀ پژوهش…"
RESEARCH_EVENT_GUIDING = "پرسیدن پرسشِ راهنما…"
RESEARCH_EVENT_MAPPING = "نگاه به زمینۀ پرسش…"
RESEARCH_EVENT_DONE = "پاسخ پژوهش آماده شد."
RESEARCH_EVENT_FAILED = "پاسخ پژوهش ناتمام ماند."
RESEARCH_EVENT_ABORTED = "پاسخ پژوهش لغو شد."

# The registry's Farsi failure details — the poll surface names why a
# turn did not land, never a bare 500.
RESEARCH_BUSY_PHONE_DETAIL = (
    "یک پیام پژوهش برای این شماره هم‌اکنون در جریان است؛ لطفاً صبور باشید."
)
RESEARCH_BUSY_GLOBAL_DETAIL = (
    "هم‌اکنون چند گفتگوی پژوهش در جریان است؛ کمی بعد دوباره تلاش کنید."
)
RESEARCH_TURN_NOT_FOUND_DETAIL = "چنین پیام پژوهشی پیدا نشد."
RESEARCH_SESSION_NOT_FOUND_DETAIL = "چنین گفتگوی پژوهشی پیدا نشد."
RESEARCH_SESSION_CLOSED_DETAIL = "این گفتگوی پژوهش بسته است؛ پرسش تازه‌ای بپرسید."
RESEARCH_PROPOSAL_NOT_FOUND_DETAIL = "چنین پیشنهادی پیدا نشد."
RESEARCH_SESSION_CAP_DETAIL = (
    "این گفتگوی پژوهش طولانی شده است؛ پیشنهاد می‌شود خلاصۀ پژوهش را "
    "بنویسید و جمع‌بندی کنید."
)
RESEARCH_FAILED_DETAIL = "پاسخ پژوهش ناتمام ماند؛ خطای غیرمنتظره."
RESEARCH_NO_EVIDENCE_DETAIL = "نقل‌قولی از کتاب‌ها برای این پیام پیدا نشد."
RESEARCH_EMPTY_REPLY_DETAIL = "پاسخ نگارنده قابل استفاده نبود."
# The plan checkpoint's honest refusal (T7): once plans are required, a
# Brief without an accepted plan is not written — the note says what to
# do first, never a fake start.
RESEARCH_BRIEF_NEEDS_PLAN_DETAIL = (
    "خلاصۀ پژوهش بدون برنامۀ پذیرفتۀ بخش‌ها نوشته نمی‌شود؛ اول برنامۀ "
    "بخش‌ها را بپذیرید."
)
# The budget's honest stop (T3): the timeline event and the note the
# transcript keeps. An over-budget turn names its stop — never a fake
# completion, never the generic unexpected-error failure.
RESEARCH_EVENT_BUDGET = "بودجۀ این پیام پژوهش تمام شد؛ کار در همین مرز متوقف شد."
RESEARCH_BUDGET_STOP_DETAIL = (
    "بودجۀ این پیام پژوهش تمام شد و کار در همین مرز متوقف شد؛ "
    "برای ادامه، دوباره بپرسید."
)

_FARSI_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _farsi_digits(value) -> str:
    return str(value).translate(_FARSI_DIGITS)


# --- the research state ----------------------------------------------------


def new_research_state(goal: str) -> dict:
    """The empty investigation, seeded by the ask's question. The
    research question starts AS the goal — v1 — and every later change
    appends a version with its reason; versions are never rewritten."""
    return {
        "goal": goal,
        "research_question": {
            "current": goal,
            "versions": [{"text": goal, "reason": "پرسش آغازین", "turn": 0}],
        },
        "scope": {"in": [], "out": [], "open_decisions": []},
        "subquestions": [],
        "concepts": [],
        "evidence": [],
        "claims": [],
        "gaps": [],
        "decisions": [],
        "diagnoses": [],
        "phase": "orientation",
        # The journey layer (ADR-0009): the stage machine, the visible
        # map (destination, fog, out-of-scope), and the live guided
        # question the user may be answering.
        "stage": "orientation",
        "map": {
            "destination": "",
            "fog": [],
            "out_of_scope": [],
            "landscape_done": False,
        },
        "grilling": {"asked_in_stage": 0, "current_question": "", "options": []},
        # The Brief plan's ledger (T7): the accepted plan rides as
        # `current`, every acceptance appends a version — append-only
        # provenance like the research question's.
        "brief_plan": {"current": None, "versions": []},
        "proposal_cooldowns": {"research_question": 0, "scope": 0, "brief_plan": 0},
        "pending_proposals": [],
        "turns": 0,
        "closed": False,
    }


def ensure_state_shape(state: dict) -> dict:
    """Fill the journey layer's defaults into a state written before it
    existed (a V0.1 session in the store): the stage derives from the
    old phase word, the map and grilling start empty. Mutates and
    returns the same dict — a pure upgrade, never a rewrite."""
    if not isinstance(state, dict):
        return state
    if "stage" not in state:
        phase = state.get("phase", "orientation")
        state["stage"] = phase if phase in RESEARCH_STAGES else "orientation"
    state.setdefault("map", {"destination": "", "fog": [], "out_of_scope": []})
    state["map"].setdefault("destination", "")
    state["map"].setdefault("fog", [])
    state["map"].setdefault("out_of_scope", [])
    state["map"].setdefault("landscape_done", False)
    state.setdefault(
        "grilling", {"asked_in_stage": 0, "current_question": "", "options": []}
    )
    state["grilling"].setdefault("asked_in_stage", 0)
    state["grilling"].setdefault("current_question", "")
    state["grilling"].setdefault("options", [])
    state.setdefault("datasets", list(BOOK_DATASETS))
    state.setdefault("diagnoses", [])
    # The plan ledger exists from the first session shape on (T7). A
    # fresh state seeds all three cooldown keys; this upgrade only tops
    # up the V0.1 pair — a kind's entry appears here when that kind is
    # first decided, and every read defaults to 0 either way.
    state.setdefault("brief_plan", {"current": None, "versions": []})
    cooldowns = state.setdefault("proposal_cooldowns", {})
    for kind in ("research_question", "scope"):
        cooldowns.setdefault(kind, 0)
    for item in state.get("subquestions", []):
        if isinstance(item, dict):
            item.setdefault("id", f"q{len(state['subquestions']) + 1}")
            item.setdefault("name", _short_name(item.get("text", "")))
    return state


def _short_name(text: str) -> str:
    """A question's short name — the first words of its text, the label
    chips and replies refer to it by (Wayfinder's refer-by-name)."""
    words = [word for word in str(text).split() if word.strip()]
    return " ".join(words[:5])


def _add_open_question(state: dict, text: str, name: str = "") -> bool:
    """Add one named open question, deduped on the normalized letter
    stream and capped; whether it landed."""
    text = str(text).strip()
    if not text:
        return False
    known = {
        normalize_for_match(item.get("text", ""))
        for item in state["subquestions"]
        if isinstance(item, dict)
    }
    if normalize_for_match(text) in known:
        return False
    state["subquestions"].append(
        {
            "id": f"q{len(state['subquestions']) + 1}",
            "name": (str(name).strip() or _short_name(text))[:60],
            "text": text,
            "status": "pending",
        }
    )
    state["subquestions"] = state["subquestions"][-RESEARCH_MAX_SUBQUESTIONS:]
    return True


def _add_fog(state: dict, text: str) -> bool:
    """Record one fog-of-war note — a question the investigation can
    see coming but cannot state sharply enough to ask yet. Deduped,
    capped; whether it landed."""
    text = str(text).strip()
    if not text:
        return False
    fog = state["map"]["fog"]
    known = {normalize_for_match(item.get("text", "")) for item in fog}
    if normalize_for_match(text) in known:
        return False
    fog.append({"id": f"f{len(fog) + 1}", "text": text})
    state["map"]["fog"] = fog[-8:]
    return True


def _graduate_fog(state: dict, question_text: str) -> None:
    """A fog note sharp enough to become an open question leaves the
    fog — it lives only as its question now (Wayfinder's graduation).
    The anchor is the fog note's opening: the words that name the topic
    the question just took over."""
    needle = normalize_for_match(question_text)
    if not needle:
        return
    state["map"]["fog"] = [
        item
        for item in state["map"]["fog"]
        if not _fog_anchor_taken(normalize_for_match(item.get("text", "")), needle)
    ]


def _fog_anchor_taken(fog_text: str, question_text: str) -> bool:
    """Whether the question's letter stream takes over the fog note's
    opening — the shared topic that made the note specifiable."""
    anchor = fog_text[:12]
    return bool(anchor) and anchor in question_text


def _pending_questions(state: dict) -> list:
    """The frontier's candidates: open questions still awaiting their
    evidence, oldest first."""
    return [
        item
        for item in state.get("subquestions", [])
        if isinstance(item, dict) and item.get("status") == "pending"
    ]


def frontier_question(state: dict):
    """The ONE question the journey works now: the oldest pending open
    question — the frontier, highlighted on the map and named by the
    chips."""
    pending = _pending_questions(state)
    return pending[0] if pending else None


def _merge_evidence(state: dict, sources, found_for: str) -> int:
    """Merge parsed (reference, passage) pairs into the ledger; how many
    landed. Dedupe is the guard's normalized letter stream — the same
    passage re-parsed by a different searcher never counts twice."""
    seen = {normalize_for_match(item["passage"]) for item in state["evidence"]}
    added = 0
    for source in sources:
        if not isinstance(source, dict):
            continue
        passage = source.get("passage")
        reference = source.get("reference")
        if not isinstance(passage, str) or not passage.strip():
            continue
        if not isinstance(reference, str) or not reference.strip():
            continue
        key = normalize_for_match(passage)
        if key in seen:
            continue
        seen.add(key)
        state["evidence"].append(
            {
                "id": f"e{len(state['evidence']) + 1}",
                "reference": reference,
                "passage": passage,
                "found_for": found_for,
            }
        )
        added += 1
    return added


def seed_evidence(state: dict, sources, found_for: str) -> int:
    """Merge the ask's phase-1 Evidence pool into the ledger as the
    session's founding evidence."""
    return _merge_evidence(state, sources, found_for)


def research_state_summary(state: dict) -> dict:
    """The compact projection the classify prompt reads and the sheet's
    map renders — every list trimmed to what a prompt or a panel needs,
    never the full passages. The V0.1 keys stay (the tests and the poll
    contract read them); the journey layer adds the map's own rows."""
    question = state.get("research_question", {})
    open_questions = [
        {
            "id": item.get("id", ""),
            "name": item.get("name", ""),
            "text": item.get("text", ""),
            "status": item.get("status", "pending"),
        }
        for item in state.get("subquestions", [])[:8]
    ]
    frontier = frontier_question(state)
    grilling = state.get("grilling", {})
    stage = state.get("stage", "orientation")
    plan = state.get("brief_plan") or {}
    plan_current = plan.get("current") or {}
    return {
        "research_question": question.get("current", ""),
        "rq_versions": len(question.get("versions", [])),
        "scope_in": list(state.get("scope", {}).get("in", []))[:6],
        "scope_out": list(state.get("scope", {}).get("out", []))[:6],
        "concepts": list(state.get("concepts", []))[:8],
        "subquestions": [
            {"text": item.get("text", ""), "status": item.get("status", "pending")}
            for item in state.get("subquestions", [])[:8]
        ],
        "evidence_count": len(state.get("evidence", [])),
        "claims": [
            {
                "id": item.get("id", ""),
                "text": item.get("text", ""),
                "status": item.get("status", ""),
            }
            for item in state.get("claims", [])[:10]
        ],
        "gaps": [item.get("text", "") for item in state.get("gaps", [])][:6],
        "phase": state.get("phase", "orientation"),
        "pending_proposals": [
            {
                "id": item.get("id", ""),
                "kind": item.get("kind", ""),
                "text": item.get("text", ""),
            }
            for item in state.get("pending_proposals", [])
        ],
        "turns": state.get("turns", 0),
        # The map projection (ADR-0009): the route at a glance — stage,
        # destination, the frontier, the named open questions, the
        # decisions index, the fog, and the live guided question.
        "stage": stage,
        "stage_label": STAGE_LABELS.get(stage, stage),
        "destination": state.get("map", {}).get("destination", ""),
        "frontier": frontier.get("name", "") if frontier else "",
        "open_questions": open_questions,
        "decisions": [
            item.get("text", "") for item in state.get("decisions", [])[-4:]
        ],
        "fog": [
            item.get("text", "") for item in state.get("map", {}).get("fog", [])[:6]
        ],
        "out_of_scope": list(state.get("scope", {}).get("out", []))[:6],
        "grilling": {
            "question": grilling.get("current_question", ""),
            "options": list(grilling.get("options", []))[:4],
        },
        # The Brief plan's projection (T7): whether an accepted plan
        # exists, how many acceptances accumulated, and the accepted
        # sections' titles — the router reads this before proposing or
        # writing against the plan.
        "brief_plan": {
            "accepted": bool(plan_current),
            "versions": len(plan.get("versions", [])),
            "sections": [
                section.get("title", "") for section in plan_current.get("sections", [])
            ][:6],
        },
    }


def _evidence_pool(state: dict):
    """The whole ledger as the writer's numbered pool, with the parallel
    evidence ids the claim recorder maps quote indices back through."""
    sources = [
        {"reference": item["reference"], "passage": item["passage"]}
        for item in state["evidence"]
    ]
    ids = [item["id"] for item in state["evidence"]]
    return sources, ids


def next_best_move(state: dict) -> str:
    """The deterministic wayfinder: which single operation most reduces
    the uncertainty in the current research question. Checkpoints come
    first (a pending decision blocks everything else), then evidence
    until the floor, then the first synthesis, then the Brief."""
    if state.get("pending_proposals"):
        return "checkpoint"
    if not state["evidence"]:
        return "gather"
    pending = [
        item
        for item in state.get("subquestions", [])
        if item.get("status") == "pending"
    ]
    if pending or len(state["evidence"]) < RESEARCH_EVIDENCE_FLOOR:
        return "gather"
    if not state.get("claims"):
        return "synthesize"
    return "brief"


def research_suggestions(state: dict) -> list:
    """The chip set the sheet renders under the latest reply — the
    journey's own moves, not a fixed row: checkpoint decisions first
    (the pending proposals, decided one pair each), then the guided
    question's options with the skip, then the stage's moves. The
    proposal chips carry SHORT labels; the proposal text rides in the
    note above, never inside the chip. The moves are NEVER hidden
    behind a waiting proposal (T7 #8, user story 19): deciding must not
    block working — an explicit command executes even while a proposal
    waits (ADR-0011), so its chip stays reachable."""
    suggestions = []
    if state.get("pending_proposals"):
        for proposal in state["pending_proposals"]:
            suggestions.append(
                {
                    "kind": "proposal",
                    "id": proposal["id"],
                    "label": "می‌پذیرم",
                    "text": proposal["text"],
                    "accept": True,
                }
            )
            suggestions.append(
                {
                    "kind": "proposal",
                    "id": proposal["id"],
                    "label": "رد می‌کنم",
                    "text": proposal["text"],
                    "accept": False,
                }
            )
    grilling = state.get("grilling", {})
    if grilling.get("current_question"):
        # The skip is never cut by the cap (the universal-skip rule):
        # the options take only the room left under it.
        for option in grilling.get("options", [])[: 6 - len(suggestions) - 1]:
            suggestions.append({"kind": "answer", "id": "answer", "text": option})
        suggestions.append({"kind": "skip", "id": "skip", "text": GRILLING_SKIP})
        return suggestions[:6]
    stage = state.get("stage", "orientation")
    if stage == "mapping":
        for concept in state.get("concepts", [])[:3]:
            suggestions.append({"kind": "answer", "id": "facet", "text": concept})
        suggestions.append({"kind": "skip", "id": "skip", "text": GRILLING_SKIP})
        return suggestions[:6]
    if stage == "orientation":
        # The journey's way back: after a conversational detour the
        # destination is still unnamed — one tap asks the guided
        # question again.
        suggestions.append({"kind": "move", "id": "guide", "text": COMMAND_GUIDE})
        return suggestions[:6]
    if stage in ("investigating", "synthesizing", "drafting"):
        pending = _pending_questions(state)
        eligible_synthesis = not pending and len(state["evidence"]) >= RESEARCH_EVIDENCE_FLOOR
        if pending or len(state["evidence"]) < RESEARCH_EVIDENCE_FLOOR:
            suggestions.append({"kind": "move", "id": "gather", "text": COMMAND_GATHER})
            for question in pending[:3]:
                suggestions.append(
                    {
                        "kind": "move",
                        "id": f"q:{question['name']}",
                        "text": f"شواهدِ «{question['name']}» را پیدا کن",
                    }
                )
        if eligible_synthesis and not state.get("claims"):
            suggestions.append(
                {"kind": "move", "id": "synthesize", "text": COMMAND_SYNTHESIZE}
            )
        if state.get("claims"):
            suggestions.append({"kind": "move", "id": "audit", "text": COMMAND_AUDIT})
            suggestions.append({"kind": "move", "id": "brief", "text": COMMAND_BRIEF})
    if state.get("turns", 0) >= RESEARCH_SESSION_TURN_CAP and not any(
        chip["id"] == "brief" for chip in suggestions
    ):
        suggestions.append({"kind": "move", "id": "brief", "text": COMMAND_BRIEF})
    return suggestions[:6]


def resolve_command(message: str):
    """Deterministic chip-text resolution — (move, target) or None. The
    fixed commands, the gather-all chip, the skip, and every
    «شواهدِ «نام» را پیدا کن» targeted gather resolve without the
    classifier; the name rides as the target."""
    stripped = message.strip()
    if stripped in COMMAND_MOVES:
        return COMMAND_MOVES[stripped], None
    if stripped == COMMAND_GATHER_ALL:
        return "gather", None
    if stripped == GRILLING_SKIP:
        return "skip", None
    if stripped == COMMAND_GUIDE:
        return "guide", None
    match = TARGETED_GATHER_RE.match(stripped)
    if match:
        return "gather", match.group(1).strip()
    return None


# --- the intent reader -----------------------------------------------------


def _recent_tail(messages, limit: int = 6) -> str:
    """The classify prompt's bounded conversation tail. User messages
    ride as their text; assistant replies are flattened to their own
    text parts (never the quotes — the passages sit in the state's
    ledger) and clipped, so the prompt stays small however long the
    investigation runs."""
    lines = []
    for message in list(messages)[-limit:]:
        role = message.get("role", "user")
        payload = message.get("payload")
        if role == "user":
            text = payload if isinstance(payload, str) else ""
        else:
            parts = []
            for block in payload if isinstance(payload, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "paragraph":
                    for part in block.get("parts", []):
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            parts.append(part["text"])
                elif block.get("type") in ("heading", "note", "references", "question"):
                    if block.get("type") in ("heading", "note", "question") and isinstance(
                        block.get("text"), str
                    ):
                        parts.append(block["text"])
            text = " ".join(parts)
        if isinstance(text, str) and text.strip():
            lines.append(f"{'کاربر' if role == 'user' else 'دستیار'}: {text.strip()[:400]}")
    return "\n".join(lines)


def build_classify_prompt(message: str, state: dict, tail: str) -> str:
    summary = json.dumps(research_state_summary(state), ensure_ascii=False)
    grilling = state.get("grilling", {})
    guided = grilling.get("current_question", "")
    guided_block = (
        f"\n\nThe user may be ANSWERING this guided question you asked "
        f"last turn: \"{guided}\" — if the message answers it, say so via "
        "answer_gist."
        if guided
        else ""
    )
    return (
        "You are the intent reader of a Farsi research conversation over "
        "a fixed set of Books. Classify the user's latest message and "
        "propose research-state updates. Research behavior strengthens "
        "with research-oriented intent; casual, learning, and lookup "
        "messages are answered normally.\n\n"
        f"Current research state (JSON, includes the journey's stage, "
        f"destination, open questions, and map):\n{summary}\n\n"
        f"Recent conversation:\n{tail or '(none)'}\n\n"
        f"Latest message: {message}{guided_block}\n\n"
        "Intents (choose exactly one) — the skill table, printed:\n"
        + "".join(
            f"- {row['name']} ({row['display_name']}): {row['purpose']}\n"
            for row in SKILL_TABLE
        )
        + "\n"
        "These fixed commands map deterministically — recognize them "
        f"exactly: \"{COMMAND_GATHER}\" -> active_research; "
        f"\"{COMMAND_SYNTHESIZE}\" -> active_research; "
        f"\"{COMMAND_BRIEF}\" -> drafting; \"{COMMAND_AUDIT}\" -> "
        "evidence_audit.\n\n"
        "Also propose, ONLY when the message gives real cause (empty or "
        "empty list otherwise). The map-mode rule: rq_proposal, "
        "scope_in/scope_out, and brief_plan are CHART edits — propose "
        "them ONLY when you chose intent research_exploration; on an "
        "active-research, drafting, or audit turn the user is WORKING, "
        "so leave them empty:\n"
        "- rq_proposal: a refined research question in Farsi, materially "
        "sharper than the current one — never a restatement\n"
        "- reason: one short Farsi sentence saying why the refinement "
        "helps\n"
        "- concepts: up to six Farsi concept names the message involves\n"
        f"- subquestions: up to {_count_word(DIVE_MAX_SUB_QUESTIONS)} "
        "Farsi sub-questions of the research question worth searching "
        "the Books for\n"
        "- scope_in, scope_out: short Farsi phrases naming what the "
        "investigation should include or exclude\n"
        "- brief_plan: when the message asks to PLAN the closing Brief "
        "— its shape before any writing — the proposed section plan as "
        "a JSON list of two to five objects "
        '{"title": "<short Farsi section title>", '
        '"question": "<the name of ONE open question from the map this '
        'section answers>", "claims": ["c1", ...]} — the claim ids from '
        "the state's claim ledger that will support the section; empty "
        "when the message is not about planning the Brief\n"
        "- answer_gist: when the message answers a guided question or "
        "states a direction, ONE short Farsi line gisting the decision "
        "the user just made\n"
        "- fog: up to four short Farsi notes of questions the "
        "investigation can see coming but cannot state sharply enough "
        "to ask yet\n"
        "- new_open_questions: up to four objects "
        '{"name": "<short Farsi label, at most five words>", '
        '"text": "<the full Farsi question>"} — sharp questions worth '
        "searching the Books for, named so the conversation can refer "
        "to them\n\n"
        "Reply with ONLY a JSON object, no prose, no code fence:\n"
        '{"intent": "casual_question", "rq_proposal": "", "reason": "", '
        '"concepts": [], "subquestions": [], "scope_in": [], '
        '"scope_out": [], "brief_plan": [], "answer_gist": "", "fog": [], '
        '"new_open_questions": []}'
    )


def parse_classify_strict(content):
    """The intent dict, or None when the reply is not a usable object —
    the caller owns the fallback (and records its diagnosis)."""
    parsed = _json_object(content)
    if not isinstance(parsed, dict):
        return None
    intent = parsed.get("intent")
    if intent not in RESEARCH_INTENTS:
        return None
    out = {"intent": intent}
    for key in ("rq_proposal", "reason", "answer_gist"):
        value = parsed.get(key)
        out[key] = value.strip() if isinstance(value, str) else ""
    for key in ("concepts", "subquestions", "scope_in", "scope_out", "fog"):
        value = parsed.get(key)
        out[key] = [
            item.strip()
            for item in (value if isinstance(value, list) else [])
            if isinstance(item, str) and item.strip()
        ]
    questions = []
    for item in parsed.get("new_open_questions") or []:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        name = item.get("name")
        questions.append(
            {
                "name": name.strip() if isinstance(name, str) else "",
                "text": text.strip(),
            }
        )
    out["new_open_questions"] = questions[:4]
    # The Brief plan's sections (T7): each keeps a title, the named
    # open question it answers, and its claim ids — junk entries and
    # titleless sections drop, the rest cap at five.
    sections = []
    for item in parsed.get("brief_plan") or []:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        raw_claims = item.get("claims")
        claims = (
            [
                claim.strip()
                for claim in raw_claims
                if isinstance(claim, str) and claim.strip()
            ]
            if isinstance(raw_claims, list)
            else []
        )
        question = item.get("question")
        sections.append(
            {
                "title": title.strip()[:80],
                "question": question.strip() if isinstance(question, str) else "",
                "claims": claims[:6],
            }
        )
    out["brief_plan"] = sections[:5]
    out["concepts"] = out["concepts"][:DIVE_MAX_SUB_QUESTIONS]
    out["subquestions"] = out["subquestions"][:DIVE_MAX_SUB_QUESTIONS]
    out["scope_in"] = out["scope_in"][:4]
    out["scope_out"] = out["scope_out"][:4]
    out["fog"] = out["fog"][:4]
    return out


def parse_classify_reply(content):
    """Guard the classify reply into the intent dict. Every field is
    optional-shaped; a reply that is not a usable object degrades to the
    conversational fallback — the same never-die shape as the dive's
    sub-question guard. (The strict form above is what the router uses,
    so the degrade can be recorded.)"""
    return parse_classify_strict(content) or {"intent": "casual_question"}


def classify_message(message: str, state: dict, messages) -> dict:
    """One classify call (thinking on — the reasoning pass); the guarded
    intent dict, conversational on any failure — with the failure NAMED
    on the dict (``router_anomaly``) so the worker records the
    diagnosis instead of degrading silently (ADR-0012)."""
    try:
        reply = _composer_reply(
            build_classify_prompt(message, state, _recent_tail(messages)),
            "enabled",
            RESEARCH_MODEL,
            urlopen_fn=urlopen,
        )
        content = _composer_content(reply)
    except (KeyError, ValueError, OSError) as exc:
        return {
            "intent": "casual_question",
            "router_anomaly": f"upstream_failure: {type(exc).__name__}",
        }
    parsed = parse_classify_strict(content)
    if parsed is None:
        return {"intent": "casual_question", "router_anomaly": "unparseable_reply"}
    return parsed


def build_subquestions_prompt(question: str) -> str:
    """The gather's planning brief: decompose, never answer — the dive
    Planner's shape, retargeted at the research question."""
    return (
        "You are planning the evidence search of a Farsi research "
        "conversation over a fixed set of Books.\n\n"
        f"Research question: {question}\n\n"
        "Task: decompose the question into distinct Farsi sub-questions "
        "whose evidence together covers it — the facets, sub-themes, and "
        "cross-checks the investigation needs. Up to "
        f"{_count_word(DIVE_MAX_SUB_QUESTIONS)} sub-questions; fewer when "
        "the question is narrow. Each sub-question must be answerable "
        "from the Books on its own. Never answer them yourself.\n\n"
        "Reply with ONLY a JSON array of Farsi strings, no prose, no "
        "code fence:\n"
        '["زیرپرسش اول؟", "زیرپرسش دوم؟"]'
    )


def subquestions_from_reply(content, question: str) -> list:
    """Guard the planning reply into sub-questions; [question] if
    unusable — a gather never dies at planning."""
    stripped = _strip_code_fence(content) if isinstance(content, str) else ""
    try:
        parsed = json.loads(stripped)
    except ValueError:
        return [question]
    if not isinstance(parsed, list):
        return [question]
    subs = [
        item.strip() for item in parsed if isinstance(item, str) and item.strip()
    ]
    return subs[:DIVE_MAX_SUB_QUESTIONS] or [question]


def plan_subquestions(question: str) -> list:
    """One planning call (thinking on); the sub-questions, or the raw
    question alone on any failure — a single search, never a dead
    gather."""
    try:
        reply = _composer_reply(
            build_subquestions_prompt(question),
            "enabled",
            RESEARCH_MODEL,
            urlopen_fn=urlopen,
        )
        content = _composer_content(reply)
    except (KeyError, ValueError, OSError):
        return [question]
    return subquestions_from_reply(content, question)


# --- the guided-question author and the journey narrator (ADR-0009) --------


def build_grilling_prompt(state: dict) -> str:
    """The guided question's brief. The author ASKS and never answers —
    a HITL turn: the agent may not stand in for the user's side of the
    conversation. Book grounding comes only from the concepts the
    investigation has actually seen (the classify pass's working list);
    the author never states a Book fact, quotes nothing, cites
    nothing."""
    summary = research_state_summary(state)
    concepts = "؛ ".join(summary["concepts"]) or "(none recorded yet)"
    stage = state.get("stage", "orientation")
    focus = {
        "orientation": (
            "Name the DESTINATION: what the user wants to walk away "
            "with from this research. Ask what they want the investigation "
            "to produce — an answer, a comparison, a map of a concept's "
            "sides, evidence for a position they hold."
        ),
        "mapping": (
            "Map breadth-first, never deep: ask which FACETS of the "
            "question matter to the user, so the open questions can be "
            "cut. Offer the concepts the Books' passages have actually "
            "shown as the facet candidates."
        ),
    }.get(stage, "Ask the one question that most unblocks the next step.")
    return (
        "You are the guide of a Farsi research conversation over a fixed "
        "set of Books. Your job this turn is ONE guided question — the "
        "kind a careful research partner asks before searching further.\n\n"
        f"Research state (JSON):\n{json.dumps(summary, ensure_ascii=False)}\n\n"
        f"Concepts seen in the Books so far: {concepts}\n\n"
        f"This stage's focus: {focus}\n\n"
        "Rules:\n"
        "- Ask EXACTLY ONE question, in Farsi, one or two sentences.\n"
        "- NEVER answer your own question and never hint the preferred "
        "answer.\n"
        "- Offer two to four SHORT Farsi option labels the user may "
        "tap instead of typing; each at most four words. When a facet "
        "concerns the Books' content, derive the options from the "
        "concepts listed above — never invent Book content.\n"
        "- No numbers, no counts, no quotes, no citations, no Book "
        "claims — you are steering the journey, not testifying.\n\n"
        "Reply with ONLY a JSON object, no prose, no code fence:\n"
        '{"question": "<the Farsi question>", "options": '
        '["<گزینهٔ اول>", "<گزینهٔ دوم>"]}'
    )


def parse_grilling_reply(content):
    """Guard the author's reply; the empty authoring on any junk — a
    failed question never blocks the journey (the worker falls through
    to the stage's ordinary move)."""
    parsed = _json_object(content)
    if not isinstance(parsed, dict):
        return {"question": "", "options": []}
    question = parsed.get("question")
    options = parsed.get("options")
    options = [
        item.strip()
        for item in (options if isinstance(options, list) else [])
        if isinstance(item, str) and item.strip()
    ]
    return {
        "question": question.strip() if isinstance(question, str) else "",
        "options": options[:4],
    }


def author_grilling(state: dict, budget=None) -> dict:
    """One authoring call (thinking on — the reasoning pass); the
    guarded question dict, empty on any failure. The authoring call is
    pre-paid from the turn's budget FIRST — a refusal propagates to the
    worker's honest stop, never into the author's own failure guard."""
    if budget is not None:
        budget.require(1)
    try:
        reply = _composer_reply(
            build_grilling_prompt(state),
            "enabled",
            RESEARCH_MODEL,
            urlopen_fn=urlopen,
        )
        content = _composer_content(reply)
    except Exception:
        # The author is cosmetic to the journey's safety and cost shape:
        # any failure (network, quota, shape) simply yields no question
        # and the worker falls through to the stage's ordinary move.
        return {"question": "", "options": []}
    return parse_grilling_reply(content)


def build_narration_prompt(state: dict, facts: dict) -> str:
    """The journey narrator's brief: one short note explaining what the
    operation just did to the MAP and what the next step is — process
    speech only. The narrator never states counts (the server's own
    fact line carries them), never states a Book claim, never quotes:
    the verbatim guard's work is for the writers over passages, and
    this is not that."""
    fact_lines = "\n".join(
        f"- {key}: {value}" for key, value in facts.items() if value != ""
    )
    return (
        "You are the narrator of a Farsi research conversation over a "
        "fixed set of Books. An operation just finished; write the ONE "
        "short note that opens the reply — what changed on the research "
        "map, and what the journey does next.\n\n"
        f"Research state (JSON):\n"
        f"{json.dumps(research_state_summary(state), ensure_ascii=False)}\n\n"
        f"Operation facts (server-computed, authoritative):\n{fact_lines}\n\n"
        "Rules:\n"
        "- At most three short Farsi sentences.\n"
        "- Qualitative only: NO digits, NO counts, NO percentages — the "
        "sheet prints the numbers itself.\n"
        "- Never state or imply a Book claim, never quote a passage.\n"
        "- You MAY name the research's open questions by their recorded "
        "names, and MAY name the next step (گرفتن شواهد، تحلیل، نوشتن "
        "خلاصه).\n"
        "- Farsi only; plain prose, no headings, no lists.\n\n"
        "Reply with ONLY the note's text, no JSON, no code fence."
    )


def parse_narration_reply(content) -> str:
    """The narrator's note, or '': a junk reply simply narrates
    nothing. JSON-shaped content (a misrouted reply, a code fence) is
    junk, not prose. Clipped hard — a narrator that rambles is worse
    than one that stays quiet."""
    if not isinstance(content, str):
        return ""
    text = _strip_code_fence(content).strip()
    if not text or text[0] in "{[":
        return ""
    sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?۔])\s+", text) if sentence.strip()]
    return " ".join(sentences[:3])[:600]


def narrate(state: dict, facts: dict, budget=None) -> str:
    """One narration call (thinking off); '' on any failure or junk —
    the operation's reply stands perfectly well without its opening
    note. An unaffordable narration call is the same silence: the
    budget's refusal is recorded, and the worker's boundary check stops
    the turn after the operation's own reply lands."""
    if budget is not None and not budget.afford(1):
        return ""
    try:
        reply = _composer_reply(
            build_narration_prompt(state, facts),
            "disabled",
            RESEARCH_MODEL,
            urlopen_fn=urlopen,
        )
        content = _composer_content(reply)
    except Exception:
        return ""
    if budget is not None:
        budget.charge(1)
    return parse_narration_reply(content)


# --- the guarded writers ---------------------------------------------------


def build_conversational_prompt(message: str, sources) -> str:
    passages = _numbered_passages(sources)
    return (
        "You are answering a Farsi message inside a research conversation "
        "over a fixed set of Books.\n\n"
        f"Message: {message}\n\n"
        "Passages (numbered, retrieved from the Books' Evidence; "
        "text-layer noise like \\b backspaces may appear between words):\n"
        f"{passages}\n\n"
        "Task: reply in two to four interleaved paragraphs — "
        "conversational, a direct answer first, no section headings. "
        "Every paragraph is one unit: your own Farsi text with quoted "
        "sentences embedded inside it. Each quoted sentence is a "
        "complete Farsi sentence copied VERBATIM from exactly ONE "
        "passage (ignore the \\b noise; write proper Farsi). Do not "
        "paraphrase, do not merge, do not shorten. Never state a Book "
        "claim the passages do not support; never write a paragraph "
        "without at least one quoted sentence; never invent a quote.\n"
        "Do NOT write a references list — the sheet appends the "
        "references itself.\n\n"
        "Reply with ONLY a JSON object, no prose, no code fence:\n"
        '{"blocks": [{"type": "paragraph", "parts": [{"text": "..."}, '
        '{"quote": "<verbatim sentence>", "source": <passage index>}, '
        '{"text": "..."}]}]}'
    )


def build_mapping_prompt(question: str, sources) -> str:
    """The mapping stage's lay-of-the-land brief: what the Books around
    the question look like — a guarded writer pass over the recall pool,
    closing with the breadth-first question in the writer's OWN text
    (never answered). The guard's every rule applies: quoted sentences
    are verbatim, paragraphs interleave, no invented content."""
    passages = _numbered_passages(sources)
    return (
        "You are writing the landscape step of a Farsi research "
        "conversation over a fixed set of Books — the breadth-first look "
        "before the investigation commits to its open questions.\n\n"
        f"Research question: {question}\n\n"
        "Passages (numbered, retrieved from the Books' Evidence; "
        "text-layer noise like \\b backspaces may appear between words):\n"
        f"{passages}\n\n"
        "Task: write two to four interleaved paragraphs surveying what "
        "the passages show AROUND the question — which concepts, which "
        "sides, which threads the Books actually hold. Every paragraph "
        "is one unit: your own Farsi text with quoted sentences embedded "
        "inside it. Each quoted sentence is a complete Farsi sentence "
        "copied VERBATIM from exactly ONE passage (ignore the \\b noise; "
        "write proper Farsi). Do not paraphrase, do not merge, do not "
        "shorten. Never state a Book claim the passages do not "
        "support; never write a paragraph without at least one quoted "
        "sentence; never invent a quote.\n"
        "CLOSE the last paragraph with ONE question of your own to the "
        "researcher, asking which of the threads you named matter most "
        "to them — ask, never answer.\n"
        "Do NOT write a references list — the sheet appends the "
        "references itself.\n\n"
        "Reply with ONLY a JSON object, no prose, no code fence:\n"
        '{"blocks": [{"type": "paragraph", "parts": [{"text": "..."}, '
        '{"quote": "<verbatim sentence>", "source": <passage index>}, '
        '{"text": "..."}]}]}'
    )


def build_synthesis_prompt(state: dict, sources) -> str:
    """The analysis step's brief: what the accumulated evidence
    establishes per sub-question — and what it does not."""
    question = state["research_question"]["current"]
    subs = [
        item["text"]
        for item in state.get("subquestions", [])
        if item.get("status") in ("pending", "searched")
    ][:DIVE_MAX_SUB_QUESTIONS]
    sub_lines = "\n".join(f"- {text}" for text in subs) or "- (none recorded)"
    passages = _numbered_passages(sources)
    return (
        "You are writing the analysis step of a Farsi research "
        "conversation over a fixed set of Books.\n\n"
        f"Research question: {question}\n\n"
        f"Sub-questions:\n{sub_lines}\n\n"
        "Passages (numbered, the investigation's accumulated Evidence; "
        "text-layer noise like \\b backspaces may appear between words):\n"
        f"{passages}\n\n"
        "Task: write what the evidence establishes, in four to eight "
        "interleaved paragraphs. Address each sub-question the passages "
        "speak to, quoting the passages that carry the answer; when a "
        "sub-question is NOT answered by the passages, say so plainly "
        "in your own words rather than guessing. Every paragraph is one "
        "unit: your own Farsi text with quoted sentences embedded inside "
        "it. Each quoted sentence is a complete Farsi sentence copied "
        "VERBATIM from exactly ONE passage (ignore the \\b noise; write "
        "proper Farsi). Do not paraphrase, do not merge, do not shorten. "
        "Never state a Book claim the passages do not support; never "
        "write a paragraph without at least one quoted sentence; never "
        "invent a quote.\n"
        "Do NOT write a references list — the sheet appends the "
        "references itself.\n\n"
        "Reply with ONLY a JSON object, no prose, no code fence:\n"
        '{"blocks": [{"type": "paragraph", "parts": [{"text": "..."}, '
        '{"quote": "<verbatim sentence>", "source": <passage index>}, '
        '{"text": "..."}]}]}'
    )


def build_brief_prompt(state: dict, sources) -> str:
    """The closing Brief's brief: built FROM the research state — the
    versioned question, the scope, the claim ledger, the gaps — never
    reconstructed from the chat history (Wayfinder §19)."""
    question = state["research_question"]
    history = "\n".join(
        f"- v{n + 1}: {version['text']}"
        + (f" ({version.get('reason', '')})" if version.get("reason") else "")
        for n, version in enumerate(question["versions"])
    )
    scope = state.get("scope", {})
    scope_lines = []
    if scope.get("in"):
        scope_lines.append("داخل دامنه: " + "؛ ".join(scope["in"]))
    if scope.get("out"):
        scope_lines.append("خارج دامنه: " + "؛ ".join(scope["out"]))
    claims = "\n".join(
        f"- {claim['text']} [{CLAIM_STATUSES.get(claim['status'], claim['status'])}]"
        for claim in state.get("claims", [])
    ) or "- (none yet)"
    gaps = (
        "\n".join(f"- {gap['text']}" for gap in state.get("gaps", []))
        or "- (none)"
    )
    passages = _numbered_passages(sources)
    scope_block = "\n".join(scope_lines) or "- (unset)"
    return (
        "You are writing the Research Brief that closes a Farsi research "
        "conversation over a fixed set of Books.\n\n"
        f"Research question (current): {question['current']}\n\n"
        f"Question history:\n{history}\n\n"
        f"Scope:\n{scope_block}\n\n"
        f"Claims established so far:\n{claims}\n\n"
        f"Gaps (the Books could not establish):\n{gaps}\n\n"
        "Passages (numbered, the investigation's accumulated Evidence; "
        "text-layer noise like \\b backspaces may appear between words):\n"
        f"{passages}\n\n"
        "Task: write the Brief in three to six sections with headings: "
        "the refined question and why it changed; what the Books "
        "establish about it, weaving the passages' verbatim sentences "
        "into your own Farsi text; what the Books do NOT establish (the "
        "gaps above, in your own words); and suggested next steps for "
        "the researcher. Each quoted sentence is a complete Farsi "
        "sentence copied VERBATIM from exactly ONE passage (ignore the "
        "\\b noise; write proper Farsi). Do not paraphrase, do not "
        "merge, do not shorten. Never state a Book claim the passages "
        "do not support; never a paragraph of bare quotes; never invent "
        "a quote.\n"
        "Do NOT write a references list — the sheet appends the "
        "references itself.\n\n"
        "Reply with ONLY a JSON object, no prose, no code fence:\n"
        '{"blocks": [{"type": "heading", "text": "..."}, '
        '{"type": "paragraph", "parts": [{"text": "..."}, '
        '{"quote": "<verbatim sentence>", "source": <passage index>}, '
        '{"text": "..."}]}]}'
    )


def _continue_once(prompt: str, blocks: list, budget=None):
    """One continuation call past a length-cut reply; (blocks,
    truncated). A failed or doubly-cut continuation rides on as the
    salvaged prefix, still cut. The continuation call is pre-paid from
    the turn's budget like every bounded step."""
    written = json.dumps({"blocks": blocks}, ensure_ascii=False)
    continuation = (
        prompt
        + "\n\nA previous write was cut by a reply length limit. The "
        "blocks that completed before the cut (JSON):\n"
        f"{written}\n\n"
        "Task: continue that SAME document. Write ONLY the blocks that "
        "come AFTER the last block above, in the same JSON object shape "
        "and under the same verbatim rules. Never repeat a block that "
        "is already written; never invent or paraphrase a quote. If "
        'nothing is missing, reply with an empty list: {"blocks": []}.'
    )
    if budget is not None:
        budget.require(1)
    try:
        reply = _composer_reply(
            continuation, "disabled", RESEARCH_MODEL, urlopen_fn=urlopen
        )
        content = _composer_content(reply)
    except (KeyError, ValueError, OSError):
        return blocks, True
    extra = parse_quoted_reply(content)
    return blocks + extra, reply["choices"][0].get("finish_reason") == "length"


def _write_once(prompt: str, sources, budget=None):
    """One writer attempt over a pool: (guarded blocks, truncated) — the
    dive's _write_dive_once shape: call, parse, ONE length-cut
    continuation, guard. The writer call is pre-paid from the turn's
    budget first; a refusal propagates to the worker's honest stop."""
    if budget is not None:
        budget.require(1)
    try:
        reply = _composer_reply(prompt, "disabled", RESEARCH_MODEL, urlopen_fn=urlopen)
        content = _composer_content(reply)
    except (KeyError, ValueError, OSError):
        return [], False
    blocks = parse_quoted_reply(content)
    if reply["choices"][0].get("finish_reason") != "length":
        return guard_blocks(blocks, sources), False
    blocks, truncated = _continue_once(prompt, blocks, budget)
    return guard_blocks(blocks, sources), truncated


def compose_guarded_reply(prompt: str, sources, budget=None):
    """One guarded writer pass with the dive's bounded repair: a reply "
    whose guard keeps NOTHING gets ONE writer retry over the same pool —
    never a loop. The turn's budget pre-pays every writer call."""
    blocks, truncated = _write_once(prompt, sources, budget)
    if blocks:
        return blocks, truncated
    return _write_once(prompt, sources, budget)


def with_references(blocks: list, sources) -> list:
    """Append the closing references block, built server-side from the
    passages actually quoted — the dive's with_dive_references shape:
    guaranteed real, never model-invented. Notes and counts carry no
    quotes, so a gather or checkpoint reply simply gets none."""
    used = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "paragraph":
            continue
        for part in block.get("parts", []):
            index = part.get("source")
            if (
                isinstance(index, int)
                and not isinstance(index, bool)
                and 0 <= index < len(sources)
            ):
                reference = sources[index]["reference"]
                if reference not in used:
                    used.append(reference)
    if not used:
        return blocks
    return blocks + [{"type": "references", "items": used}]


# --- the claims ledger -----------------------------------------------------


def record_claims(state: dict, blocks: list, evidence_ids) -> int:
    """Derive claim entries from a guarded synthesis reply: one per kept
    quoting paragraph — its own text is the claim, its quotes' evidence
    ids the support. Status is code-derived, never model-claimed: one
    distinct passage is direct support, more is supported synthesis."""
    existing = {normalize_for_match(claim["text"]) for claim in state["claims"]}
    added = 0
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "paragraph":
            continue
        text = " ".join(
            part["text"]
            for part in block.get("parts", [])
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ).strip()
        ids = []
        for part in block.get("parts", []):
            if not isinstance(part, dict) or isinstance(part.get("text"), str):
                continue
            index = part.get("source")
            if not isinstance(index, int) or isinstance(index, bool):
                continue
            if 0 <= index < len(evidence_ids):
                evidence_id = evidence_ids[index]
                if evidence_id not in ids:
                    ids.append(evidence_id)
        if not text or not ids:
            continue
        key = normalize_for_match(text)
        if key in existing:
            continue
        existing.add(key)
        state["claims"].append(
            {
                "id": f"c{len(state['claims']) + 1}",
                "text": text,
                "status": (
                    "direct_support" if len(ids) == 1 else "supported_synthesis"
                ),
                "evidence_ids": ids,
                "turn": state["turns"],
            }
        )
        added += 1
    return added


# --- the per-turn budget (ADR-0012, T3) --------------------------------------


class BudgetExhausted(Exception):
    """Raised at a chain boundary when the turn's budget cannot afford
    the next bounded step (`reason`: "deadline" or "call_cap"). It
    subclasses Exception directly on purpose: no operation's
    upstream-failure guard (KeyError/ValueError/OSError) may swallow it
    — the worker is its only catcher, and the stop must stay honest."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class TurnBudget:
    """One turn's hard cost bound (ADR-0012, T3): a wall-clock deadline
    (default RESEARCH_TURN_DEADLINE_SECONDS) plus an upstream-call cap
    (default RESEARCH_TURN_CALL_CAP), read through an injected clock so
    tests exhaust either instantly — no waiting on real time.

    The cap's teeth are afford-before-start: a bounded step is started
    only when its full known call cost fits (`require` pre-pays it), so
    a recorded cap can never be exceeded mid-call. The deadline is
    cooperative like the cancel flag — checked at every chain boundary;
    an in-flight upstream call is never interrupted, so a turn
    overshoots its deadline by at most the one call already in flight.
    The first refusal records `reason`; every later check refuses on
    it, so the stop's cause is the budget's own word."""

    def __init__(self, clock=time.monotonic, deadline_seconds=None, call_cap=None):
        self.clock = clock
        self.deadline_at = self.clock() + (
            RESEARCH_TURN_DEADLINE_SECONDS
            if deadline_seconds is None
            else deadline_seconds
        )
        self.call_cap = RESEARCH_TURN_CALL_CAP if call_cap is None else call_cap
        self.calls = 0
        self.reason = None

    def expired(self) -> bool:
        return self.clock() >= self.deadline_at

    def afford(self, calls: int) -> bool:
        """Whether `calls` more upstream calls fit; a refusal records
        its reason once and refuses everything after."""
        if self.reason is not None:
            return False
        if self.expired():
            self.reason = "deadline"
            return False
        if self.calls + calls > self.call_cap:
            self.reason = "call_cap"
            return False
        return True

    def charge(self, calls: int) -> None:
        self.calls += calls

    def require(self, calls: int) -> None:
        """Pre-pay the next bounded step's known call cost — afford it
        or raise, never start a step the budget cannot pay for."""
        if not self.afford(calls):
            raise BudgetExhausted(self.reason)
        self.charge(calls)

    def checkpoint(self) -> None:
        """The chain-boundary check: raise when the deadline has passed
        or a refusal already happened — the turn stops HERE, between
        bounded steps, never mid-call."""
        if self.reason is not None:
            raise BudgetExhausted(self.reason)
        if self.expired():
            self.reason = "deadline"
            raise BudgetExhausted(self.reason)


def turn_status_payload(turn) -> dict:
    """The poll surface's payload (serve's /research/turn body), built
    under the registry lock: state, Farsi events, elapsed seconds — and
    the turn's budget state (T3): the calls charged so far, the cap, and
    whether the budget stopped the turn (its reason)."""
    payload = {
        "state": turn.state,
        "events": list(turn.events),
        "elapsed": round(time.monotonic() - turn.started_at, 1),
    }
    if turn.budget is not None:
        payload["budget"] = {
            "calls": turn.budget.calls,
            "cap": turn.budget.call_cap,
            "exhausted": turn.budget.reason is not None,
            "reason": turn.budget.reason,
        }
    if turn.state == "done":
        payload.update(turn.result)
    elif turn.state == "failed":
        payload["detail"] = turn.error
    return payload


# --- the turn registry -----------------------------------------------------

# A turn is a job in an in-process registry (the dive's shape, ADR-0006
# issue #26): the message endpoint answers a turn identity immediately,
# the sheet polls, and multiple users get fair, independent turns. A
# server restart empties the registry — an unknown turn id after a
# restart IS the recorded failure surface — while the session itself
# reloads from the store. Terminal turns are never reaped (the dive's
# recorded YAGNI limit): growth is bounded by one restart cycle.
TURN_TERMINAL_STATES = {"done", "failed", "aborted"}
RESEARCH_MAX_CONCURRENT = 3
RESEARCH_REGISTRY = {}
RESEARCH_REGISTRY_LOCK = threading.Lock()


class ResearchTurn:
    """One in-flight turn: identity, ownership, the observable timeline
    (state + Farsi events), the outcome, and the cooperative cancel
    flag. `done` is set when the worker thread has fully exited."""

    def __init__(self, phone: str, session_id: str, message: str):
        self.id = uuid.uuid4().hex
        self.phone = phone
        self.session_id = session_id
        self.message = message
        self.state = "classifying"
        self.events = [RESEARCH_EVENT_CLASSIFYING]
        self.result = None
        self.error = None
        self.started_at = time.monotonic()
        self.cancel = threading.Event()
        self.done = threading.Event()
        # The worker installs the turn's TurnBudget here (T3); until
        # then the poll simply omits the budget field.
        self.budget = None


def _turn_write(turn, state=None, event=None, error=None) -> bool:
    """One atomic non-terminal write under the registry lock; a settled
    turn is never overwritten (the dive's settle-guard — an abort that
    landed mid-write must not be clobbered back to live)."""
    with RESEARCH_REGISTRY_LOCK:
        if turn.state in TURN_TERMINAL_STATES:
            return False
        if state is not None:
            turn.state = state
        if error is not None:
            turn.error = error
        if event is not None:
            turn.events.append(event)
        return True


def _turn_fail(turn, detail: str) -> None:
    _turn_write(turn, "failed", RESEARCH_EVENT_FAILED, error=detail)


def abort_research_turn(turn: ResearchTurn) -> bool:
    """Abort one turn: the cancel flag and the `aborted` state land
    together under the lock; a settled turn is never re-marked."""
    with RESEARCH_REGISTRY_LOCK:
        if turn.state in TURN_TERMINAL_STATES:
            return False
        turn.cancel.set()
        turn.state = "aborted"
        turn.events.append(RESEARCH_EVENT_ABORTED)
        return True


def ensure_session(
    phone: str, session_id, text: str, question, sources, datasets=None
):
    """Load or create the session a message belongs to, appending the
    user's message to its transcript; (session, None) or (None, (status,
    Farsi detail)) — unknown sessions 404, closed ones 409. The creating
    call names the ask's question and its phase-1 Evidence pool — the
    session's founding goal and evidence — and the ask's Book selection
    (ADR-0010), which every later gather and recall of this session
    searches (the server-resolved list, never the browser's raw)."""
    if session_id:
        session = research_store.load_session(session_id)
        if session is None or session["phone"] != phone:
            return None, (404, RESEARCH_SESSION_NOT_FOUND_DETAIL)
        if session["state"].get("closed"):
            return None, (409, RESEARCH_SESSION_CLOSED_DETAIL)
        ensure_state_shape(session["state"])
        research_store.append_message(session_id, "user", text)
        session["messages"].append({"role": "user", "payload": text})
        return session, None
    goal = (question or "").strip() or text.strip()
    state = new_research_state(goal)
    state["datasets"] = (
        list(datasets) if datasets else list(BOOK_DATASETS)
    )
    if sources:
        seed_evidence(state, sources, goal)
    session_id = uuid.uuid4().hex
    research_store.create_session(session_id, phone, state)
    research_store.append_message(session_id, "user", text)
    return (
        {
            "id": session_id,
            "phone": phone,
            "state": state,
            "messages": [{"role": "user", "payload": text}],
        },
        None,
    )


def research_session_state(phone: str, session_id: str):
    """The state panel's read: the summary projection under
    ``research_state`` plus the chip set under ``suggestions`` — a
    browser refresh re-renders both, so a live guided question's
    options and its skip survive the reload (T10, GitLab #11);
    (payload, None) or (None, (status, Farsi detail))."""
    session = research_store.load_session(session_id)
    if session is None or session["phone"] != phone:
        return None, (404, RESEARCH_SESSION_NOT_FOUND_DETAIL)
    if session["state"].get("closed"):
        return None, (409, RESEARCH_SESSION_CLOSED_DETAIL)
    ensure_state_shape(session["state"])
    return {
        "research_state": research_state_summary(session["state"]),
        "suggestions": research_suggestions(session["state"]),
    }, None


def research_session_messages(phone: str, session_id: str):
    """The transcript read (ADR-0011): one session's messages in order,
    phone matched — a browser refresh re-fetches what was said instead
    of showing an empty chat; ({messages}, None) or (None, (status,
    Farsi detail)). The payload rides as-is: strings for user turns,
    block lists for assistant replies."""
    session = research_store.load_session(session_id)
    if session is None or session["phone"] != phone:
        return None, (404, RESEARCH_SESSION_NOT_FOUND_DETAIL)
    return {"messages": session["messages"]}, None


def decide_proposal(phone: str, session_id: str, proposal_id: str, accept: bool):
    """Resolve one pending checkpoint — the ONLY path by which a
    research question or scope change lands (a classify proposal never
    applies itself). Synchronous, no LLM; (result, None) or (None,
    (status, Farsi detail))."""
    session = research_store.load_session(session_id)
    if session is None or session["phone"] != phone:
        return None, (404, RESEARCH_SESSION_NOT_FOUND_DETAIL)
    state = ensure_state_shape(session["state"])
    if state.get("closed"):
        return None, (409, RESEARCH_SESSION_CLOSED_DETAIL)
    proposal = next(
        (
            item
            for item in state.get("pending_proposals", [])
            if item.get("id") == proposal_id
        ),
        None,
    )
    if proposal is None:
        return None, (404, RESEARCH_PROPOSAL_NOT_FOUND_DETAIL)
    state["pending_proposals"] = [
        item
        for item in state["pending_proposals"]
        if item.get("id") != proposal_id
    ]
    # A decided question or scope cools the map down (ADR-0011): the
    # next two turns park no NEW proposal of the same kind, so the
    # journey moves instead of looping rewrite→approve.
    cooldowns = state.setdefault(
        "proposal_cooldowns", {"research_question": 0, "scope": 0}
    )
    cooldowns[proposal["kind"]] = PROPOSAL_COOLDOWN_TURNS
    if not accept:
        decision = f"پیشنهاد رد شد: «{proposal['text']}»"
    elif proposal["kind"] == "research_question":
        state["research_question"]["versions"].append(
            {
                "text": proposal["text"],
                "reason": proposal.get("reason", ""),
                "turn": state["turns"],
            }
        )
        state["research_question"]["current"] = proposal["text"]
        decision = f"پرسش پژوهش به‌روز شد: «{proposal['text']}»"
    elif proposal["kind"] == "scope":
        for item in proposal.get("scope_in", []):
            if item not in state["scope"]["in"]:
                state["scope"]["in"].append(item)
        for item in proposal.get("scope_out", []):
            if item not in state["scope"]["out"]:
                state["scope"]["out"].append(item)
        decision = f"دامنۀ پژوهش به‌روز شد: {proposal['text']}"
    elif proposal["kind"] == "brief_plan":
        # The plan's acceptance is APPEND-ONLY (T7): each acceptance
        # adds a version to the plan's ledger — v1 stays intact
        # underneath — and the accepted plan becomes the current one,
        # the gate the (required) Brief writes against.
        sections = proposal.get("sections", [])
        plan = state.setdefault("brief_plan", {"current": None, "versions": []})
        plan["versions"].append({"sections": sections, "turn": state["turns"]})
        plan["current"] = {"sections": sections}
        decision = f"برنامۀ خلاصۀ پژوهش پذیرفته شد: {proposal['text']}"
    else:
        decision = proposal["text"]
    state["decisions"].append({"text": decision, "turn": state["turns"]})
    research_store.save_session(session_id, state)
    blocks = [{"type": "note", "text": decision}]
    research_store.append_message(session_id, "assistant", blocks)
    return (
        {
            "reply": blocks,
            "suggestions": research_suggestions(state),
            "research_state": research_state_summary(state),
        },
        None,
    )


def abort_phone_research(phone: str) -> None:
    """A new ask owns the sheet: the phone's in-flight research turns
    abort cooperatively and their sessions close — a later message to a
    closed session answers the closed detail, never resurrects the old
    investigation beside the new ask."""
    with RESEARCH_REGISTRY_LOCK:
        own = [
            turn
            for turn in RESEARCH_REGISTRY.values()
            if turn.phone == phone and turn.state not in TURN_TERMINAL_STATES
        ]
    for turn in own:
        if not abort_research_turn(turn):
            continue
        session = research_store.load_session(turn.session_id)
        if session is not None and not session["state"].get("closed"):
            session["state"]["closed"] = True
            research_store.save_session(turn.session_id, session["state"])


def start_research_turn(phone: str, session: dict, message: str):
    """Create the registry turn under the caps — at most one
    non-terminal turn per phone and RESEARCH_MAX_CONCURRENT globally —
    or return the Farsi busy detail. The check and the creation are
    atomic under the lock; a rejected start is never queued."""
    with RESEARCH_REGISTRY_LOCK:
        non_terminal = [
            turn
            for turn in RESEARCH_REGISTRY.values()
            if turn.state not in TURN_TERMINAL_STATES
        ]
        if any(turn.phone == phone for turn in non_terminal):
            return None, RESEARCH_BUSY_PHONE_DETAIL
        if len(non_terminal) >= RESEARCH_MAX_CONCURRENT:
            return None, RESEARCH_BUSY_GLOBAL_DETAIL
        turn = ResearchTurn(phone, session["id"], message)
        RESEARCH_REGISTRY[turn.id] = turn
    threading.Thread(
        target=run_research_turn, args=(turn, session), daemon=True
    ).start()
    return turn, None


# --- the turn's operations -------------------------------------------------


def _apply_classify_updates(state: dict, classified: dict) -> None:
    """Fold the classify pass's NON-consequential updates into the state
    (working concepts, named open questions, fog) and park the
    CONSEQUENTIAL ones (research question, scope) as pending proposals —
    the checkpoints the user must approve before they land. A gist of
    the user's just-answered guided question lands as a decision, the
    map's index of the route walked.

    The chart-mode gate (ADR-0011, the recorded planning loop): only an
    EXPLORATION turn may park an RQ or scope proposal — an
    active-research/drafting/audit turn is WORK on the map, not map
    editing, and its classify suggestions never become checkpoints.
    Even an exploration turn is damped: nothing parks inside the
    decision cooldown (two turns after an accepted or rejected proposal
    of that kind), and nothing parks that restates a past decision or
    the current question."""
    for concept in classified.get("concepts", []):
        if concept not in state["concepts"]:
            state["concepts"].append(concept)
    state["concepts"] = state["concepts"][-RESEARCH_MAX_CONCEPTS:]
    for note in classified.get("fog", []):
        _add_fog(state, note)
    for question in classified.get("new_open_questions", []):
        if _add_open_question(state, question["text"], question["name"]):
            _graduate_fog(state, question["text"])
    for sub in classified.get("subquestions", []):
        _add_open_question(state, sub)
    state["subquestions"] = state["subquestions"][-RESEARCH_MAX_SUBQUESTIONS:]
    grilling = state.get("grilling", {})
    gist = classified.get("answer_gist", "")
    if grilling.get("current_question") and gist:
        state["decisions"].append({"text": gist, "turn": state["turns"]})
        state["grilling"] = {
            "asked_in_stage": grilling.get("asked_in_stage", 0),
            "current_question": "",
            "options": [],
        }
        # The orientation question names the destination: the user's own
        # gist is the destination's words (never a Book claim — the
        # checkpoint rule guards the research question separately).
        if state.get("stage") == "orientation" and not state["map"]["destination"]:
            state["map"]["destination"] = gist
    may_propose = classified.get("intent") == "research_exploration"
    current = state["research_question"]["current"]
    rq = classified.get("rq_proposal", "")
    rq_key = normalize_for_match(rq)
    pending_kinds = {item["kind"] for item in state["pending_proposals"]}
    # A decision's text narrates the proposal inside prose («پیشنهاد رد
    # شد: «…»», «پرسش پژوهش به‌روز شد: «…»») — containment, not equality.
    already_decided = any(
        rq_key and rq_key in normalize_for_match(item.get("text", ""))
        for item in state["decisions"]
    )
    cooldowns = state.setdefault(
        "proposal_cooldowns", {"research_question": 0, "scope": 0}
    )
    if (
        may_propose
        and rq
        and cooldowns.get("research_question", 0) <= 0
        and rq_key != normalize_for_match(current)
        and not already_decided
        and "research_question" not in pending_kinds
    ):
        state["pending_proposals"].append(
            {
                "id": f"p{len(state['pending_proposals']) + 1}",
                "kind": "research_question",
                "text": rq,
                "reason": classified.get("reason", ""),
            }
        )
    scope_in = classified.get("scope_in", [])
    scope_out = classified.get("scope_out", [])
    if (
        may_propose
        and (scope_in or scope_out)
        and cooldowns.get("scope", 0) <= 0
        and "scope" not in pending_kinds
    ):
        parts = []
        if scope_in:
            parts.append("داخل دامنه: " + "؛ ".join(scope_in))
        if scope_out:
            parts.append("خارج دامنه: " + "؛ ".join(scope_out))
        state["pending_proposals"].append(
            {
                "id": f"p{len(state['pending_proposals']) + 1}",
                "kind": "scope",
                "text": "، ".join(parts),
                "scope_in": scope_in,
                "scope_out": scope_out,
            }
        )
    # The Brief plan (T7): the THIRD proposal kind, under the same
    # chart-mode gate — only an exploration turn parks it, its own
    # cooldown damps it, one plan waits at a time, and a plan whose
    # summary restates a past decision can never re-park.
    plan_sections = classified.get("brief_plan", [])
    if (
        may_propose
        and plan_sections
        and cooldowns.get("brief_plan", 0) <= 0
        and "brief_plan" not in pending_kinds
    ):
        plan_text = _plan_proposal_text(plan_sections)
        plan_key = normalize_for_match(plan_text)
        plan_decided = any(
            plan_key and plan_key in normalize_for_match(item.get("text", ""))
            for item in state["decisions"]
        )
        if not plan_decided:
            state["pending_proposals"].append(
                {
                    "id": f"p{len(state['pending_proposals']) + 1}",
                    "kind": "brief_plan",
                    "text": plan_text,
                    "sections": plan_sections,
                }
            )


def _plan_proposal_text(sections: list) -> str:
    """The parked plan's display text — section count and titles, the
    short line the chips' note, the checkpoint reply, and the decisions
    index all share (the containment dedupe keys on it)."""
    titles = "، ".join(
        f"«{section.get('title', '')}»" for section in sections[:4]
    )
    more = "…" if len(sections) > 4 else ""
    return f"برنامۀ خلاصه در {_farsi_digits(len(sections))} بخش: {titles}{more}"


def advance_stage(state: dict, to: str) -> None:
    """Move the journey forward — code's decision only, never the
    model's. A stage never moves backward; the phase word mirrors the
    stage for the V0.1 readers."""
    order = list(RESEARCH_STAGES)
    current = state.get("stage", "orientation")
    if to in order and order.index(to) > order.index(current):
        state["stage"] = to
        state["phase"] = to


def _stage_should_ask(state: dict, intent: str = None) -> bool:
    """Whether this turn is a guided question: the early stages with
    their question unanswered, under the per-stage grilling cap, with no
    pending checkpoint (a decision blocks everything else) — and NEVER
    on a work intent (ADR-0011): when the classifier read the message as
    an investigation command, in any wording, the journey works instead
    of asking."""
    if intent in ("active_research", "drafting", "evidence_audit"):
        return False
    stage = state.get("stage", "orientation")
    if stage not in ("orientation", "mapping"):
        return False
    if state.get("pending_proposals"):
        return False
    if stage == "orientation" and state["map"].get("destination"):
        return False
    asked = state.get("grilling", {}).get("asked_in_stage", 0)
    return asked < RESEARCH_GRILLING_STAGE_CAP


def _checkpoint_reply(state: dict) -> list:
    """The checkpoint turn's reply: server-composed notes presenting
    every pending proposal as a compact diff — the current question
    beside the proposed one — never a wall of restated state. The chips
    above carry the decision; a checkpoint makes no Book claim, so no
    guard applies — the references list's own exemption, as-is."""
    blocks = [
        {
            "type": "note",
            "text": (
                "یک تصمیم پیش روی شماست؛ با دکمه‌های «می‌پذیرم» یا "
                "«رد می‌کنم» زیر همین پیام انتخاب کنید."
            ),
        }
    ]
    for proposal in state["pending_proposals"]:
        if proposal["kind"] == "research_question":
            diff = (
                f"پرسش پژوهش از «{state['research_question']['current']}» "
                f"به «{proposal['text']}»"
            )
            blocks.append({"type": "note", "text": diff})
            if proposal.get("reason"):
                blocks.append(
                    {"type": "note", "text": f"چرا: {proposal['reason']}"}
                )
        elif proposal["kind"] == "scope":
            blocks.append(
                {"type": "note", "text": f"دامنۀ پژوهش: {proposal['text']}"}
            )
        elif proposal["kind"] == "brief_plan":
            # The plan reads section by section: each note ties a
            # section to its named open question and the claims that
            # will support it — the diff the user decides on.
            blocks.append(
                {"type": "note", "text": f"برنامۀ خلاصۀ پژوهش: {proposal['text']}"}
            )
            for section in proposal.get("sections", []):
                claims = "، ".join(section.get("claims", []))
                suffix = f" — ادعاها: {claims}" if claims else ""
                blocks.append(
                    {
                        "type": "note",
                        "text": (
                            f"بخش «{section.get('title', '')}» برای پرسشِ "
                            f"«{section.get('question', '')}»{suffix}"
                        ),
                    }
                )
    return blocks


def _gap_event(starved: int) -> str:
    digits = _farsi_digits(starved)
    verb = "می‌شود" if starved == 1 else "می‌شوند"
    return f"{digits} زیرپرسشِ کم‌شواهد دوباره جست‌وجو {verb}…"


def _gather(state: dict, turn: ResearchTurn, target: str = None) -> list:
    """The evidence-gathering operation: run the dive's bounded fan-out
    over the frontier — the targeted open question when a chip named
    one, else every still-pending sub-question (or a decomposition of
    the research question when none) — merge the pool into the ledger,
    and record honest gaps for the sub-questions the Books could not
    feed. The reply is server-composed notes — counts and gaps — so a
    gather makes no claim a guard would have to check."""
    pending = []
    if target:
        pending = [
            item["text"]
            for item in state["subquestions"]
            if item.get("status") == "pending"
            and (
                item.get("name", "") == target or item.get("text", "") == target
            )
        ]
    if not pending:
        pending = [
            item["text"]
            for item in state["subquestions"]
            if item.get("status") == "pending"
        ]
    if not pending:
        turn.budget.require(1)  # the planning call
        _turn_write(turn, "planning", RESEARCH_EVENT_PLANNING)
        pending = plan_subquestions(state["research_question"]["current"])
        known = {item["text"] for item in state["subquestions"]}
        for sub in pending:
            if sub not in known:
                _add_open_question(state, sub)
                known.add(sub)
    _turn_write(turn, "searching", RESEARCH_EVENT_SEARCHING)
    sources, rounds, starved = dive_retrieve(
        pending,
        cancel=turn.cancel,
        on_gap=lambda count: _turn_write(turn, event=_gap_event(count)),
        datasets=state.get("datasets"),
        budget=turn.budget,
    )
    if turn.cancel.is_set():
        return []
    added = _merge_evidence(state, sources, state["research_question"]["current"])
    for item in state["subquestions"]:
        if item["text"] in pending:
            item["status"] = "searched"
    blocks = []
    if added:
        blocks.append(
            {
                "type": "note",
                "text": (
                    f"{_farsi_digits(added)} نقل‌قول تازه به شواهد افزوده شد "
                    f"(مجموع {_farsi_digits(len(state['evidence']))} نقل‌قول، "
                    f"{_farsi_digits(rounds)} دور جست‌وجو)."
                ),
            }
        )
    known_gaps = {normalize_for_match(gap["text"]) for gap in state["gaps"]}
    for sub in starved:
        gap_text = (
            f"کتاب‌ها برای «{sub}» شواهد کافی ندارند؛ این بخش را نمی‌توان "
            "از همین کتاب‌ها اثبات کرد."
        )
        if normalize_for_match(gap_text) not in known_gaps:
            known_gaps.add(normalize_for_match(gap_text))
            state["gaps"].append(
                {
                    "id": f"g{len(state['gaps']) + 1}",
                    "text": gap_text,
                    "subquestion": sub,
                    "turn": state["turns"],
                }
            )
        for item in state["subquestions"]:
            if item["text"] == sub:
                item["status"] = "gap"
    if not added:
        blocks.append(
            {
                "type": "note",
                "text": (
                    "نقل‌قول تازه‌ای پیدا نشد. می‌توانید پرسش پژوهش را محدودتر "
                    "کنید یا همین را به‌عنوان شکاف پژوهش بپذیرید."
                ),
            }
        )
    elif state["gaps"]:
        blocks.append(
            {
                "type": "note",
                "text": (
                    "شکاف‌های پژوهش تا اینجا: "
                    f"{_farsi_digits(len(state['gaps']))} مورد."
                ),
            }
        )
    return blocks


def _synthesize(turn: ResearchTurn, state: dict) -> list:
    """The analysis operation: one guarded writer pass over the whole
    evidence pool, its paragraphs recorded as claims. A pool the guard
    cannot feed lands the honest note, never an unguarded claim."""
    sources, ids = _evidence_pool(state)
    if not sources:
        return [
            {
                "type": "note",
                "text": "هنوز شاهدی در دفتر شواهد نیست؛ اول شواهد را جمع کنیم.",
            }
        ]
    _turn_write(turn, "writing", RESEARCH_EVENT_ANALYZING)
    blocks, _ = compose_guarded_reply(
        build_synthesis_prompt(state, sources), sources, budget=turn.budget
    )
    if not blocks:
        return [{"type": "note", "text": RESEARCH_EMPTY_REPLY_DETAIL}]
    record_claims(state, blocks, ids)
    return blocks


def _brief(turn: ResearchTurn, state: dict) -> list:
    """The drafting operation: the Research Brief written FROM the state
    — question history, scope, claims, gaps — over the evidence pool,
    guarded like every writer pass. Once plans are required (the T7
    checkpoint), a session with no ACCEPTED plan refuses before any
    writer call — the note names what to do first, the same honest
    shape as the no-claims refusal."""
    sources, _ = _evidence_pool(state)
    if not state.get("claims"):
        return [
            {
                "type": "note",
                "text": (
                    "هنوز ادعای مستندی برای خلاصه ثبت نشده؛ اول شواهد را "
                    "تحلیل و جمع‌بندی کنیم."
                ),
            }
        ]
    if BRIEF_PLANS_REQUIRED and not (state.get("brief_plan") or {}).get("current"):
        return [{"type": "note", "text": RESEARCH_BRIEF_NEEDS_PLAN_DETAIL}]
    _turn_write(turn, "writing", RESEARCH_EVENT_BRIEF)
    blocks, _ = compose_guarded_reply(
        build_brief_prompt(state, sources), sources, budget=turn.budget
    )
    if not blocks:
        return [{"type": "note", "text": RESEARCH_EMPTY_REPLY_DETAIL}]
    state["phase"] = "drafting"
    return blocks


def _audit(state: dict) -> list:
    """The evidence-audit operation: pure code, no LLM — the claim
    ledger re-reported with its code-derived statuses and the gaps, the
    numbers a researcher checks a Brief against."""
    if not state.get("claims") and not state.get("gaps"):
        return [
            {
                "type": "note",
                "text": "هنوز ادعا یا شکافی برای بازبینی ثبت نشده است.",
            }
        ]
    blocks = [{"type": "heading", "text": "دفتر ادعاها"}]
    for claim in state["claims"]:
        label = CLAIM_STATUSES.get(claim["status"], claim["status"])
        blocks.append({"type": "note", "text": f"[{label}] {claim['text']}"})
    if state["gaps"]:
        blocks.append({"type": "heading", "text": "شکاف‌های پژوهش"})
        for gap in state["gaps"]:
            blocks.append({"type": "note", "text": gap["text"]})
    return blocks


def _conversational(turn: ResearchTurn, state: dict, message: str):
    """The conversation layer's answer: one searcher over the message
    and one guarded writer pass over its pool; (blocks, pool) — the pool
    rides back so the references cite exactly the passages that reply
    quoted, not the whole ledger. No pool — or a guard that keeps
    nothing after its one retry — lands the honest no-evidence note."""
    _turn_write(turn, "searching", RESEARCH_EVENT_SEARCHING)
    if turn.cancel.is_set():
        return [], []
    turn.budget.require(1)  # the searcher call
    pool = dive_recall(message, state.get("datasets"))
    if turn.cancel.is_set():
        return [], []
    if not pool:
        return [{"type": "note", "text": RESEARCH_NO_EVIDENCE_DETAIL}], []
    _turn_write(turn, "writing", RESEARCH_EVENT_WRITING)
    blocks, _ = compose_guarded_reply(
        build_conversational_prompt(message, pool), pool, budget=turn.budget
    )
    if not blocks:
        return [{"type": "note", "text": RESEARCH_NO_EVIDENCE_DETAIL}], []
    return blocks, pool


# --- the journey layer's turns (ADR-0009) -----------------------------------


def _grilling_turn(state: dict, turn: ResearchTurn):
    """The guided-question turn: author ONE question, park it as the
    live grilling (the chips render its options and the skip), reply
    with the question alone — the agent never answers it. None when the
    author came back empty: the worker falls through to the stage's
    ordinary move, never a dead end."""
    _turn_write(turn, "guiding", RESEARCH_EVENT_GUIDING)
    authored = author_grilling(state, budget=turn.budget)
    if not authored["question"]:
        return None
    grilling = state["grilling"]
    grilling["asked_in_stage"] = grilling.get("asked_in_stage", 0) + 1
    grilling["current_question"] = authored["question"]
    grilling["options"] = authored["options"]
    return [{"type": "question", "text": authored["question"]}]


def _mapping_turn(turn: ResearchTurn, state: dict):
    """The mapping stage's turn: one searcher over the research
    question and one guarded writer pass surveying the landscape,
    closing with the breadth-first question in the writer's own text.
    (blocks, pool); the pool rides back for this reply's references. A
    Books-empty pool skips the stage outright — nothing to survey."""
    question = state["research_question"]["current"]
    state["map"]["landscape_done"] = True
    _turn_write(turn, "searching", RESEARCH_EVENT_MAPPING)
    if turn.cancel.is_set():
        return [], []
    turn.budget.require(1)  # the searcher call
    pool = dive_recall(question, state.get("datasets"))
    if turn.cancel.is_set():
        return [], []
    if not pool:
        advance_stage(state, "investigating")
        return [
            {
                "type": "note",
                "text": (
                    "کتاب‌ها برای این پرسش چیزی برای نقشه‌برداری ندادند؛ "
                    "مستقیم وارد گردآوری شواهد می‌شویم."
                ),
            }
        ], []
    _turn_write(turn, "writing", RESEARCH_EVENT_WRITING)
    blocks, _ = compose_guarded_reply(
        build_mapping_prompt(question, pool), pool, budget=turn.budget
    )
    if not blocks:
        return [{"type": "note", "text": RESEARCH_EMPTY_REPLY_DETAIL}], []
    return blocks, pool


def _skip_reply(state: dict) -> list:
    """The skip's deterministic landing: the stage's questioning ends
    without an answer and the journey proceeds on what it has — the
    destination falls back to the current research question, the map
    notes the decision, and the stage moves forward."""
    stage = state.get("stage", "orientation")
    state["grilling"] = {
        "asked_in_stage": RESEARCH_GRILLING_STAGE_CAP,
        "current_question": "",
        "options": [],
    }
    state["decisions"].append(
        {"text": "کاربر پاسخ دادن به پرسش راهنما را رد کرد؛ ادامه با وضع موجود.", "turn": state["turns"]}
    )
    if stage == "orientation":
        if not state["map"]["destination"]:
            state["map"]["destination"] = state["research_question"]["current"]
        advance_stage(state, "mapping")
        return [
            {
                "type": "note",
                "text": (
                    "خب؛ با همین پرسش آغاز می‌کنیم و در میانهٔ راه، مقصد را "
                    "دقیق‌تر می‌کنیم."
                ),
            }
        ]
    if stage == "mapping":
        advance_stage(state, "investigating")
        return [
            {
                "type": "note",
                "text": "نقشه همین است؛ وارد گردآوری شواهد می‌شویم.",
            }
        ]
    return [{"type": "note", "text": "ادامه می‌دهیم."}]


def _map_ready_reply(state: dict) -> list:
    """The mapping exit's reply: the open questions read back BY NAME —
    the map the rest of the journey walks. Server-composed; no claim,
    no guard needed."""
    names = "، ".join(
        f"«{item.get('name', '')}»" for item in _pending_questions(state)[:6]
    )
    return [
        {"type": "note", "text": f"نقشۀ پژوهش آماده شد؛ پرسش‌های باز: {names}."},
        {
            "type": "note",
            "text": (
                "هر پرسش را می‌توان جداگانه جست‌وجو کرد — دکمه‌های زیر همین "
                "پیام."
            ),
        },
    ]


def _narration_facts(state: dict, move: str, searched: list, starved: list) -> dict:
    """The server-computed facts the narrator may lean on (and must
    never contradict — it is told to carry no numbers at all)."""
    next_moves = {
        "gather": "ادامهٔ گردآوری شواهد",
        "synthesize": "تحلیل و جمع‌بندی شواهد",
        "brief": "نوشتن خلاصۀ پژوهش",
    }
    return {
        "finished_move": {
            "gather": "گردآوری شواهد",
            "synthesize": "تحلیل و جمع‌بندی شواهد",
            "brief": "نوشتن خلاصۀ پژوهش",
        }.get(move, move),
        "stage": STAGE_LABELS.get(state.get("stage", ""), ""),
        "questions_searched": "، ".join(searched[:6]),
        "gaps_found": "، ".join(starved[:4]),
        "next_step": next_moves.get(next_best_move(state), ""),
    }


# --- the skill runners (ADR-0012): one bounded skill per dispatch -----------


def _run_chat_skill(turn, state, classified, message, resolved, intent):
    """The conversational skills (casual, concept, lookup): one searcher
    over the message and one guarded writer over its pool."""
    blocks, pool = _conversational(turn, state, message)
    return blocks, pool, ""


def _run_audit_skill(turn, state, classified, message, resolved, intent):
    """The audit: pure code over the ledgers — no writer, no searcher."""
    return _audit(state), None, ""


def _run_brief_skill(turn, state, classified, message, resolved, intent):
    """The Brief: one guarded writer pass written FROM the state; a
    reply with surviving paragraphs narrates and lands the drafting
    stage."""
    blocks = _brief(turn, state)
    narration = ""
    if blocks and any(b["type"] == "paragraph" for b in blocks):
        narration = narrate(
            state, _narration_facts(state, "brief", [], []), budget=turn.budget
        )
        advance_stage(state, "drafting")
    return blocks, None, narration


def _run_research_skill(turn, state, classified, message, resolved, intent):
    """The journey branch (chart and work intents alike): the stage
    machine decides, the model only proposes content — the checkpoints,
    the skip, the landscape survey, the guided questions, and the
    deterministic ladder of moves."""
    _apply_classify_updates(state, classified)
    # The decision cooldown ticks down AFTER this turn's parking
    # check — a fresh cooldown of PROPOSAL_COOLDOWN_TURNS blocks
    # exactly that many full turns after the decision.
    cooldowns = state.setdefault(
        "proposal_cooldowns", {"research_question": 0, "scope": 0}
    )
    for kind in cooldowns:
        if cooldowns[kind] > 0:
            cooldowns[kind] -= 1
    _fold_grilling_answer(state, classified, message, bool(resolved))
    # The destination's answer ends the orientation stage: the
    # journey moves to mapping THIS turn (code's decision, on
    # the observable destination).
    if state.get("stage") == "orientation" and state["map"].get("destination"):
        advance_stage(state, "mapping")
    skip = bool(resolved and resolved[0] == "skip")
    # The work-mode rule (ADR-0011, the recorded planning
    # loop): a pending checkpoint shows on a FREE turn — an
    # explicit command (or the skip) is the user steering, and
    # it EXECUTES; the parked proposals survive for the next
    # free turn and their chips still render.
    if state["pending_proposals"] and resolved is None and not skip:
        state["phase"] = "scoping"
        return _checkpoint_reply(state), None, ""
    if skip:
        return _skip_reply(state), None, ""
    if (
        resolved is None
        and state.get("stage") == "mapping"
        and not state["map"].get("landscape_done")
    ):
        blocks, pool = _mapping_turn(turn, state)
        if pool is not None and not pool:
            pool = None
        return blocks, pool, ""
    if (
        resolved is None
        and state.get("stage") == "mapping"
        and len(_pending_questions(state)) >= 2
    ):
        advance_stage(state, "investigating")
        return _map_ready_reply(state), None, ""
    if _stage_should_ask(state, intent) and (
        resolved is None or resolved[0] == "guide"
    ):
        blocks = _grilling_turn(state, turn)
        if blocks is None:
            blocks = _journey_fallback(state, turn, resolved)
        return blocks, None, ""
    return _journey_fallback(state, turn, resolved), None, ""


SKILL_RUNNERS = {
    "chat": _run_chat_skill,
    "research": _run_research_skill,
    "brief": _run_brief_skill,
    "audit": _run_audit_skill,
}


# --- the turn worker -------------------------------------------------------


def run_research_turn(
    turn: ResearchTurn,
    session: dict,
    budget_seconds=None,
    call_cap=None,
    clock=None,
) -> None:
    """The turn worker: classify, route through the JOURNEY — the
    stage machine decides, the model only proposes content — execute at
    most ONE bounded operation, compose the reply with the journey's
    narration note and server-built references, and persist. The stages
    (ADR-0009): a guided question names the destination, a guarded
    landscape maps the ground, the open questions drive the gathers,
    and the closing moves stay exactly the V0.1 operations. A
    checkpoint still blocks everything; cancellation is cooperative —
    checked at every stage boundary, an in-flight upstream call is
    never interrupted. Any worker failure marks the turn failed with a
    short Farsi detail; the endpoint never 500s from this thread. Each
    boundary prints one terse line so the journal diagnoses a live turn
    without the sheet.

    The turn runs under a hard budget (ADR-0012, T3): a wall-clock
    deadline plus an upstream-call cap, both configurable HERE — the
    worker is the seam's highest point — and `clock` (default real
    monotonic) is the injected clock tests exhaust deterministically.
    Every bounded step is pre-paid from the budget before it starts, so
    the cap can never be exceeded mid-call; the deadline is checked at
    every chain boundary, an in-flight call never interrupted. An
    over-budget turn stops at that boundary with the honest Farsi note
    and settles `done` — never a fabricated completion, never the
    generic failure."""
    state = ensure_state_shape(session["state"])
    budget = TurnBudget(
        clock=clock if clock is not None else time.monotonic,
        deadline_seconds=budget_seconds,
        call_cap=call_cap,
    )
    turn.budget = budget
    try:
        state["turns"] = state.get("turns", 0) + 1
        message = turn.message
        budget.require(1)  # the classify call
        resolved = resolve_command(message)
        classified = classify_message(message, state, session["messages"])
        anomaly = classified.pop("router_anomaly", None)
        if anomaly:
            record_diagnosis(state, anomaly, classified["intent"])
        budget.checkpoint()  # the first chain boundary
        if turn.cancel.is_set():
            return
        intent = classified["intent"]
        if resolved:
            intent = {
                "gather": "active_research",
                "synthesize": "active_research",
                "brief": "drafting",
                "audit": "evidence_audit",
                "skip": "research_exploration",
                "guide": "research_exploration",
            }[resolved[0]]
        # The code disposes (ADR-0012): the picked row is validated
        # against the state — a rejected pick is a recorded diagnosis
        # falling back to the conversational skill, never a crash,
        # never a silence.
        row = SKILL_TABLE_BY_NAME.get(
            intent, SKILL_TABLE_BY_NAME["casual_question"]
        )
        ok, reject_detail = validate_skill_pick(row, state)
        if not ok:
            record_diagnosis(
                state, f"rejected: {reject_detail}", "casual_question"
            )
            intent = "casual_question"
            row = SKILL_TABLE_BY_NAME["casual_question"]
        blocks, conversational_pool, narration = SKILL_RUNNERS[row["runner"]](
            turn, state, classified, message, resolved, intent
        )
        if turn.cancel.is_set():
            return
        if budget.reason is not None:
            # The budget ran out mid-skill (a dive round was refused,
            # the narrator unaffordable): the partial blocks stand and
            # the honest stop note closes the reply.
            _turn_write(turn, event=RESEARCH_EVENT_BUDGET)
            blocks = blocks + [
                {"type": "note", "text": RESEARCH_BUDGET_STOP_DETAIL}
            ]
        else:
            if narration:
                blocks = [{"type": "note", "text": narration}] + blocks
            if state.get("turns", 0) >= RESEARCH_SESSION_TURN_CAP:
                blocks = blocks + [
                    {"type": "note", "text": RESEARCH_SESSION_CAP_DETAIL}
                ]
        if conversational_pool is not None:
            citation_pool = conversational_pool
        else:
            citation_pool, _ = _evidence_pool(state)
        blocks = with_references(blocks, citation_pool)
        result = {
            "reply": blocks,
            "suggestions": research_suggestions(state),
            "research_state": research_state_summary(state),
        }
        with RESEARCH_REGISTRY_LOCK:
            if turn.state in TURN_TERMINAL_STATES:
                return
            turn.result = result
            turn.state = "done"
            turn.events.append(RESEARCH_EVENT_DONE)
        research_store.save_session(session["id"], state)
        research_store.append_message(session["id"], "assistant", blocks)
        print(
            f"research {turn.id}: turn settled, "
            f"stage {state.get('stage')}, "
            f"{len(state['evidence'])} evidence, "
            f"{len(state['claims'])} claims, "
            f"{len(state['gaps'])} gaps, "
            f"budget {budget.calls}/{budget.call_cap}",
            flush=True,
        )
    except BudgetExhausted:
        # The honest stop: the budget refused a bounded step, so the
        # turn settles here with the stop note as its whole reply — the
        # state mutated so far is saved and the transcript tells the
        # truth (never the generic unexpected-error failure).
        _turn_write(turn, event=RESEARCH_EVENT_BUDGET)
        blocks = [{"type": "note", "text": RESEARCH_BUDGET_STOP_DETAIL}]
        result = {
            "reply": blocks,
            "suggestions": research_suggestions(state),
            "research_state": research_state_summary(state),
        }
        with RESEARCH_REGISTRY_LOCK:
            if turn.state in TURN_TERMINAL_STATES:
                return
            turn.result = result
            turn.state = "done"
        research_store.save_session(session["id"], state)
        research_store.append_message(session["id"], "assistant", blocks)
        print(
            f"research {turn.id}: turn stopped on budget "
            f"({budget.reason}) at {budget.calls}/{budget.call_cap} calls, "
            f"stage {state.get('stage')}",
            flush=True,
        )
    except Exception:
        _turn_fail(turn, RESEARCH_FAILED_DETAIL)
        try:
            # The turn's diagnoses were recorded before the crash — a
            # failed turn keeps its record (ADR-0012: never a silence).
            research_store.save_session(session["id"], state)
        except Exception:
            pass
        try:
            research_store.append_message(
                session["id"],
                "assistant",
                [{"type": "note", "text": RESEARCH_FAILED_DETAIL}],
            )
        except Exception:
            pass
    finally:
        turn.done.set()


def _fold_grilling_answer(
    state: dict, classified: dict, message: str, is_command: bool
) -> None:
    """Land the user's answer to the live guided question. A gist from
    the classify pass is best; without one, the message's own words are
    the decision (the question was asked and answered — a live grilling
    must never dead-end). Conversational turns keep the question
    alive; commands resolve it server-side and never reach here."""
    grilling = state.get("grilling", {})
    if not grilling.get("current_question") or is_command:
        return
    gist = classified.get("answer_gist", "") or message.strip()[:160]
    state["decisions"].append({"text": gist, "turn": state["turns"]})
    state["grilling"] = {
        "asked_in_stage": grilling.get("asked_in_stage", 0),
        "current_question": "",
        "options": [],
    }
    if state.get("stage") == "orientation" and not state["map"]["destination"]:
        state["map"]["destination"] = gist


def _journey_fallback(state: dict, turn: ResearchTurn, resolved):
    """The ordinary move when no journey turn applies — the V0.1
    deterministic ladder, now stage-aware: a gather with an optional
    targeted question, the synthesis, or the Brief. Only operation
    resolutions (gather/synthesize/brief) force a move; a guide or skip
    that fell through runs the ladder like any free message. Returns
    blocks and advances the stage a material operation earns."""
    move = resolved[0] if resolved and resolved[0] in (
        "gather",
        "synthesize",
        "brief",
    ) else None
    if move is None:
        move = next_best_move(state)
    if move == "gather":
        target = resolved[1] if resolved else None
        before = len(state["evidence"])
        blocks = _gather(state, turn, target)
        if state["evidence"]:
            advance_stage(state, "investigating")
        searched = [
            item.get("name", item.get("text", ""))
            for item in state["subquestions"]
            if item.get("status") == "searched"
        ]
        if len(state["evidence"]) > before:
            narration = narrate(
                state, _narration_facts(state, "gather", searched, []), budget=turn.budget
            )
            if narration:
                blocks = [{"type": "note", "text": narration}] + blocks
        return blocks
    if move == "synthesize":
        blocks = _synthesize(turn, state)
        if state.get("claims"):
            advance_stage(state, "synthesizing")
            narration = narrate(
                state, _narration_facts(state, "synthesize", [], []), budget=turn.budget
            )
            if narration:
                blocks = [{"type": "note", "text": narration}] + blocks
        return blocks
    blocks = _brief(turn, state)
    if blocks and any(b["type"] == "paragraph" for b in blocks):
        narration = narrate(
            state, _narration_facts(state, "brief", [], []), budget=turn.budget
        )
        if narration:
            blocks = [{"type": "note", "text": narration}] + blocks
        advance_stage(state, "drafting")
    return blocks
