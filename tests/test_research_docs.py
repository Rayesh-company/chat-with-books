"""Doc locks for Research Mode (ADR-0008 + the ADR-0009 journey layer):
the README's Research Mode section, ARCHITECTURE §8, the CONTEXT.md
glossary additions, and the ADR files themselves — the same
contract-locking shape the dive docs had, retargeted at the
Wayfinder. The T10 locks (GitLab #11) ride here too: the map side
rail, the plain-Persian naming table, and the sheet's stage labels."""

import sys  # noqa: E402

from tests.conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))

from ui import research  # noqa: E402

README = REPO_ROOT / "README.md"
ARCHITECTURE = REPO_ROOT / "ARCHITECTURE.md"
CONTEXT = REPO_ROOT / "CONTEXT.md"
ADR = REPO_ROOT / "docs" / "adr" / "0008-research-mode-wayfinder.md"
ADR9 = REPO_ROOT / "docs" / "adr" / "0009-research-mode-journey-layer.md"
ADR12 = REPO_ROOT / "docs" / "adr" / "0012-research-skill-architecture.md"
SERVE = REPO_ROOT / "ui" / "serve.py"
INDEX = REPO_ROOT / "ui" / "index.html"


def research_section():
    return (
        README.read_text(encoding="utf-8")
        .split("## Research Mode", 1)[1]
        .split("\n## ", 1)[0]
    )


def test_readme_research_section_locks_the_two_layers_and_the_commands():
    section = research_section()
    # The two layers, by name.
    assert "conversation layer" in section
    assert "research state layer" in section
    # The four deterministic chip commands.
    assert "شواهد بیشتری از کتاب‌ها پیدا کن" in section
    assert "شواهد را تحلیل و جمع‌بندی کن" in section
    assert "خلاصۀ پژوهش را بنویس" in section
    assert "ادعاها و استنادها را بازبینی کن" in section


def test_readme_research_section_locks_the_checkpoint_rule():
    section = research_section()
    assert "NEVER applies itself" in section
    assert "/research/decide" in section
    assert "می‌پذیرم" in section
    assert "appends a version with its reason" in section


def test_readme_research_section_locks_the_reuse_of_the_dive_caps():
    section = research_section()
    # The gather is the old fan-out with its recorded caps and pins.
    assert "HYBRID_COMPLETION" in section
    assert "at most two retrieval rounds" in section
    assert "quote-starved" in section
    # The honest gap — never a hallucinated fill.
    assert "نمی‌توان از همین کتاب‌ها اثبات کرد" in section
    # Claim statuses are code-derived, never model-claimed.
    assert "direct_support" in section
    assert "supported_synthesis" in section


def test_readme_research_section_locks_the_registry_and_the_store():
    section = research_section()
    assert '202 {"turn_id", "session_id"}' in section
    assert "/research/turn?turn=..." in section
    assert "/research/state?session=" in section
    assert "one in-flight turn per phone" in section
    assert "three globally" in section
    # The session survives a restart; only the in-flight turn does not.
    assert "ui/research.sqlite3" in section
    assert "persists in SQLite" in section
    # The abort rule: a new ask aborts the turn and closes the session.
    assert "closes its session" in section


def test_readme_records_the_research_gate_as_free():
    section = (
        README.read_text(encoding="utf-8")
        .split("### Accounts & login (ADR-0013)", 1)[1]
        .split("\n### ", 1)[0]
    )
    assert "/research/message" in section
    assert (
        "the fifth chat's own Quoted answer and research conversation must pass"
        in section
    )


def test_architecture_section_8_is_research_mode():
    text = ARCHITECTURE.read_text(encoding="utf-8")
    section = text.split("## 8. Research Mode", 1)[1].split("\n## 9.", 1)[0]
    assert "POST /research/message" in section
    assert "Checkpoint rule" in section
    assert "ui/research.sqlite3" in section
    # The old section title and its operator button never return.
    assert "a background job, not a request" not in text
    assert "شروع مطالعۀ عمیق" not in text


def test_architecture_adr_index_lists_0008():
    text = ARCHITECTURE.read_text(encoding="utf-8")
    assert "0008-research-mode-wayfinder.md" in text


def test_architecture_section_8_locks_the_per_turn_budget():
    # T3 (GitLab #4): the hard per-turn budget — deadline plus call cap
    # through an injected clock, the honest Farsi stop, the poll's
    # budget state.
    section = ARCHITECTURE.read_text(encoding="utf-8").split(
        "## 8. Research Mode", 1
    )[1].split("\n## 9.", 1)[0]
    assert "hard budget" in section
    assert "RESEARCH_TURN_DEADLINE_SECONDS" in section
    assert "RESEARCH_TURN_CALL_CAP" in section
    assert "injected clock" in section
    assert "pre-paid" in section
    assert "an in-flight call is never interrupted" in section
    assert "بودجۀ این پیام پژوهش تمام شد" in section
    assert '"budget": {calls, cap, exhausted,' in section
    # The budget is worker configuration, pinned in source like every cap.
    assert "budget_seconds" in (
        (REPO_ROOT / "ui" / "research.py").read_text(encoding="utf-8")
    )


def test_readme_locks_the_journey_layer():
    section = research_section()
    # The journey layer, by name, with its safety bounds.
    assert "journey layer" in section
    assert "stage machine" in section
    assert "NEVER answers its own question" in section
    assert "فعلاً همین کافی است؛ ادامه بده" in section
    assert "نقشۀ پژوهش" in section
    # The deterministic targeted chip resolves by pattern.
    assert "را پیدا کن" in section


def test_architecture_section_8_carries_the_journey_layer():
    text = ARCHITECTURE.read_text(encoding="utf-8")
    section = text.split("## 8. Research Mode", 1)[1].split("\n## 9.", 1)[0]
    assert "JOURNEY LAYER" in section
    assert "Stage machine" in section
    assert "Guided questions" in section
    assert "Fog of war" in section
    assert "code on observable state" in section


def test_architecture_adr_index_lists_0009():
    text = ARCHITECTURE.read_text(encoding="utf-8")
    assert "0009-research-mode-journey-layer.md" in text


def test_context_glossary_adds_the_journey_vocabulary():
    text = CONTEXT.read_text(encoding="utf-8")
    for term in (
        "**Journey layer**",
        "**Research map**",
        "**Guided question**",
        "**Frontier**",
        "**Fog**",
    ):
        assert term in text


def test_the_journey_adr_records_the_decision():
    text = ADR9.read_text(encoding="utf-8")
    assert "Amends: ADR-0008" in text
    assert "NEVER answers its own" in text or "never answers its own" in text
    assert "Considered options" in text
    assert "Consequences" in text
    assert "stage machine" in text


def test_context_glossary_adds_the_research_vocabulary():
    text = CONTEXT.read_text(encoding="utf-8")
    for term in (
        "**Research Mode**",
        "**Research state**",
        "**Claim ledger**",
        "**Gap**",
    ):
        assert term in text
    # The superseded dive name stays retired from the live vocabulary.
    assert "**Deep dive**" not in text


def test_the_adr_file_records_the_decision():
    text = ADR.read_text(encoding="utf-8")
    assert "Supersedes the phase-3 half of ADR 0006" in text
    assert "never applies itself" in text
    assert "Considered options" in text
    assert "Consequences" in text


def test_the_sheet_carries_the_research_ui_contract():
    html = INDEX.read_text(encoding="utf-8")
    # The map is the persistent side rail (T10, GitLab #11): an always
    # open rail beside the chat, never a collapsible details the chat
    # scrolls away.
    assert 'id="research-rail"' in html
    assert 'id="research-state-body"' in html
    assert '<details id="research-state"' not in html
    assert 'id="research-journey"' in html
    assert 'id="research-chat"' in html
    assert 'id="research-suggest"' in html
    assert 'id="research-form"' in html
    # The map, not a status strip (ADR-0009).
    assert "نقشۀ پژوهش" in html
    assert "مراحل سفر پژوهش" in html
    assert "sessionResearch" in html


def test_the_sheet_stage_labels_carry_the_plain_persian_table():
    # T10 (GitLab #11): the sheet's five-stage strip names the stages
    # exactly as the engine's approved table does — one vocabulary, no
    # drift between the map rail and the state projection.
    html = INDEX.read_text(encoding="utf-8")
    for label in research.STAGE_LABELS.values():
        assert label in html


def test_context_records_the_approved_persian_display_names():
    # T10 (GitLab #11): the naming table lives in CONTEXT.md (ADR-0012 —
    # the vocabulary home), the PM approved the roster there, and the
    # skill table's rows carry exactly those display names — the
    # skill-to-name mapping pinned, not just present.
    text = CONTEXT.read_text(encoding="utf-8")
    for name in (
        "راهنما",
        "جست‌وجوگر",
        "نقشه‌کش",
        "کاوشگر",
        "تحلیل‌گر",
        "نویسنده",
        "بازبین",
        "میزبان",
        "نقشه‌بان",
        "تشخیص‌گر",
    ):
        assert name in text
    expected = {
        "casual_question": "میزبان",
        "concept_learning": "میزبان",
        "source_lookup": "میزبان",
        "research_exploration": "راهنما",
        "active_research": "جست‌وجوگر",
        "drafting": "نویسنده",
        # The Closing review owns بازبین (T9, the ticket's approved
        # name); the claim-ledger audit is named for its own ledger.
        "closing_review": "بازبین",
        "evidence_audit": "بازبینِ دفتر ادعاها",
        # The map keeper (T12, GitLab #13): the survey that proposes the
        # map's cleanups as decisions.
        "map_keeper": "نقشه‌بان",
        # The fog probe (T13, GitLab #14): the bounded search that makes
        # a fog graduation earn itself.
        "fog_probe": "کاوشگر",
    }
    assert {row["name"] for row in research.SKILL_TABLE} == set(expected)
    for row in research.SKILL_TABLE:
        assert row["display_name"] == expected[row["name"]]


def test_the_adr_records_the_sheet_and_skip_decisions():
    text = ADR12.read_text(encoding="utf-8")
    assert "The map sticks; skip is universal" in text
    assert "persistent side rail" in text


def test_serve_pins_the_research_routes():
    text = SERVE.read_text(encoding="utf-8")
    for route in (
        "/research/message",
        "/research/turn",
        "/research/state",
        "/research/decide",
    ):
        assert route in text
    # The superseded dive routes never return.
    assert "/deep-dive" not in text

def test_readme_and_architecture_lock_the_per_section_brief():
    # T8 (GitLab #9): the Brief assembly line — sections written one
    # bounded op each against their accepted Section contracts, guarded,
    # exactly one retry, honest-gap fallback; the plan's own headings.
    readme = research_section()
    assert "assembled section by section" in readme
    assert "Section contracts" in readme
    assert "exactly one retry" in readme
    assert "cannot drift from it" in readme
    assert "without an accepted plan second" in readme
    section = ARCHITECTURE.read_text(encoding="utf-8").split(
        "## 8. Research Mode", 1
    )[1].split("\n## 9.", 1)[0]
    assert "SECTION BY SECTION" in section
    assert "ONE bounded writer op" in section
    assert "exactly ONE retry" in section
    assert "honest-gap fallback" in section
    assert "section contracts" in section


def test_context_glossary_holds_the_section_contract():
    text = CONTEXT.read_text(encoding="utf-8")
    assert "**Section contract**" in text
    assert (
        "which claims (by identity) it must carry, which question it "
        "answers, and which scope lines it must not cross" in text
    )


def test_readme_and_architecture_lock_the_closing_review():
    # T9 (GitLab #10): the finished Brief faces the Closing review
    # before it is done — code traceability first, then the ONE
    # destination-judgment call; the verdict lands with accept/revise
    # chips and revise reruns only the failing sections.
    readme = research_section()
    assert "Closing review" in readme
    assert "destination-judgment" in readme
    assert "accept/revise chips" in readme
    assert "ONLY the flagged sections" in readme
    assert "بازنویسی بخش‌های ناکام خلاصه" in readme
    assert "`unjudged`" in readme
    section = ARCHITECTURE.read_text(encoding="utf-8").split(
        "## 8. Research Mode", 1
    )[1].split("\n## 9.", 1)[0]
    assert "Closing review" in section
    assert "destination-judgment call" in section
    assert "brief_document" in section
    assert "closing_review ledger" in section
    assert "closing_review" in section.split("intent ∈ {", 1)[1].split("}", 1)[0]


def test_context_names_the_closing_review_and_the_audit():
    # The naming table: the Closing review owns بازبین (the ticket's
    # approved name); the claim-ledger audit is named for its own
    # ledger so the router prompt never lists two skills of one name.
    text = CONTEXT.read_text(encoding="utf-8")
    assert "| Closing review | بازبین |" in text
    assert "| claim-ledger audit (evidence_audit) | بازبینِ دفتر ادعاها |" in text
    assert "بازنویسی بخش‌های ناکام خلاصه" in text
    assert "the closing review's revise (T9)" in text


def test_readme_and_architecture_lock_the_state_caps_and_the_map_keeper():
    # T12 (GitLab #13): the working ledgers cap newest-kept so the
    # prompts stay bounded; the map's rows clean only through the
    # map-keeper's proposed decisions — never silently.
    readme = research_section()
    assert "state caps" in readme
    assert "RESEARCH_MAX_EVIDENCE" in readme
    assert "keep their NEWEST entries" in readme
    assert "map keeper" in readme
    assert "نقشه‌بان" in readme
    assert "نقشه را مرتب کن" in readme
    assert "never re-parks" in readme
    assert "provenance stays append-only" in readme
    section = ARCHITECTURE.read_text(encoding="utf-8").split(
        "## 8. Research Mode", 1
    )[1].split("\n## 9.", 1)[0]
    assert "STATE CAPS" in section
    assert "RESEARCH_MAX_DECISIONS" in section
    assert "re-anchors" in section
    assert "carried by its own append-only record" in section
    assert "map_keeper" in section.split("intent ∈ {", 1)[1].split("}", 1)[0]
    assert "map_keeper ──" in section


def test_context_names_the_state_caps_and_the_keeper_chip():
    # T12 (GitLab #13): the vocabulary home records the caps and the
    # keeper's fixed chip beside the naming table's own row.
    text = CONTEXT.read_text(encoding="utf-8")
    assert "**State caps (T12)**" in text
    assert "| map keeper | نقشه‌بان |" in text
    assert "نقشه را مرتب کن" in text
    assert "the map keeper's survey (T12)" in text


def test_readme_and_architecture_lock_the_fog_probe():
    # T13 (GitLab #14): the fog probe (کاوشگر) — one bounded search over
    # the oldest unprobed fog note, graduation on evidence, the early
    # Gap prediction, and the out-of-scope veto that never graduates.
    readme = research_section()
    assert "fog probe" in readme
    assert "کاوشگر" in readme
    assert "مه را کاوش کن" in readme
    assert "Diagnoser's early Gap prediction" in readme
    assert "out-of-scope items never graduate" in readme
    section = ARCHITECTURE.read_text(encoding="utf-8").split(
        "## 8. Research Mode", 1
    )[1].split("\n## 9.", 1)[0]
    assert "fog_probe" in section.split("intent ∈ {", 1)[1].split("}", 1)[0]
    assert "fog_probe ──" in section
    assert "NOT SPECIFIABLE" in section
    assert "EARLY Gap prediction" in section


def test_context_glossary_holds_the_fog_probe():
    # T13 (GitLab #14): the vocabulary home extends the Fog entry with
    # the probe and records the fixed chip beside the naming table's
    # own row.
    text = CONTEXT.read_text(encoding="utf-8")
    assert "| fog probe | کاوشگر |" in text
    assert "مه را کاوش کن" in text
    assert "the fog probe (T13)" in text
    assert (
        "the fog probe (کاوشگر) makes that graduation earn itself" in text
    )
