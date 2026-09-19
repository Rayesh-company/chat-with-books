"""The sheet integration of the Session report (T19, GitLab #22): the
Brief-section signal the chip's lock rides on, the report chip in the
suggestions payload — present EXACTLY when a Brief section stands,
beside the closing review's accept chips, never a turn command — the
Markdown twin's shape and its absences, the md endpoint's attachment
headers against the inline html default, and the README's honest
fetch-on-load record (the stale V0.1 sentence is gone). External
behavior only: state in, suggestions/markdown/HTTP out; no network —
the endpoint test runs the real sheet server over the patched research
store with no upstream behind it at all."""

import sys  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

from tests.conftest import REPO_ROOT  # noqa: E402
from tests.helpers import stop_gate, with_gate  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))

from ui import research, research_store  # noqa: E402
from ui.report import report_markdown  # noqa: E402
from ui.research import (  # noqa: E402
    research_state_summary,
    research_suggestions,
)

README = REPO_ROOT / "README.md"
INDEX = REPO_ROOT / "ui" / "index.html"
RESEARCH = REPO_ROOT / "ui" / "research.py"

PHONE = "09120000000"
REPORT_CHIP = research.RESEARCH_REPORT_CHIP
QUESTION = "رابطۀ شهود و وحی در اندیشۀ اسلامی چیست؟"
TRANSCRIPT_MARKER = "نشانهٔ منحصربه‌فردِ گفتگوی خام ۹۹۱۲"


def report_state(sections: int = 2) -> dict:
    """A fabricated session state in tests/test_report.py's make_state
    shape: the question, the map, the ledgers, the standing Brief's
    sections with source-indexed quotes (the same two evidence entries
    the golden report reads), and a transcript marker that must never
    reach the artifact. `sections` trims the Brief to 1 or 0 — the
    chip's and the twins' lock."""
    state = research.new_research_state(QUESTION)
    state["stage"] = "drafting"
    state["map"]["destination"] = "فهم روشنی از جایگاه شهود نزد مصطفی ملکیان"
    state["subquestions"] = [
        {"id": "q1", "name": "شهود", "text": "شهود در قرآن", "status": "searched"},
        {"id": "q2", "name": "وحی", "text": "ساحت وحی", "status": "pending"},
    ]
    state["decisions"] = [{"text": "پرسش به شهود محدود شد"}]
    state["gaps"] = [{"text": "کتاب‌ها دربارهٔ ساحت وحی کم می‌آورند"}]
    state["evidence"] = [
        {
            "id": "e1",
            "reference": "طرح کلی اندیشۀ اسلامی در قرآن، صفحات ۷۴۰ تا ۷۴۵",
            "passage": "قرآن کتابی است برای زندگی",
            "found_for": "شهود در قرآن",
        },
        {
            "id": "e2",
            "reference": "انسان ۲۵۰ ساله، صفحهٔ ۱۲",
            "passage": "انسان در جامعه می‌زیید",
            "found_for": "ساحت وحی",
        },
    ]
    state["claims"] = [
        {"id": "c1", "text": "قرآن کتابی است برای زندگی", "status": "direct_support"}
    ]
    written = {
        "title": "شهود و ساحت",
        "paragraphs": [
            {
                "type": "paragraph",
                "parts": [
                    {"text": "جملهٔ پیوند "},
                    {"quote": "قرآن کتابی است برای زندگی", "source": 0},
                    {"text": " و باز "},
                    {"quote": "انسان در جامعه می‌زیید", "source": 1},
                ],
            }
        ],
        "gap": False,
    }
    starving = {"title": "بخش گرسنه", "paragraphs": [], "gap": True}
    state["brief_document"] = {
        "complete": sections > 0,
        "sections": [written, starving][:sections],
    }
    state["messages"] = [TRANSCRIPT_MARKER]
    state["turns"] = 7
    return state


def fetch_raw(base: str, path: str, phone: str = PHONE):
    """One raw GET with the session phone: (status, headers, text) — the
    report read answers documents, not JSON, so the headers themselves
    are the contract under test."""
    headers = {"X-Session-Phone": phone} if phone else {}
    request = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.headers, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read().decode("utf-8")


# --- the Brief-section signal -------------------------------------------------


def test_brief_sections_rides_the_state_summary():
    # The chip's condition lives on the state summary the sheet already
    # reads — the count of the standing Brief's sections, zero when none.
    assert research_state_summary(report_state())["brief_sections"] == 2
    assert research_state_summary(report_state(sections=1))["brief_sections"] == 1
    assert research_state_summary(report_state(sections=0))["brief_sections"] == 0

    summary = research_state_summary(report_state())
    # Every standing key keeps its place — the sheet's map and the poll
    # contract read the V0.1 keys and the journey's rows alike.
    for key in (
        "research_question",
        "rq_versions",
        "scope_in",
        "scope_out",
        "concepts",
        "subquestions",
        "evidence_count",
        "claims",
        "gaps",
        "phase",
        "pending_proposals",
        "turns",
        "stage",
        "stage_label",
        "destination",
        "frontier",
        "open_questions",
        "decisions",
        "diagnoses",
        "fog",
        "out_of_scope",
        "grilling",
        "brief_plan",
        "brief_sections",
        "closing_review",
    ):
        assert key in summary, f"the state summary lost {key}"


# --- the report chip ----------------------------------------------------------


def test_the_report_chip_applies_exactly_when_a_section_stands():
    chips = research_suggestions(report_state())
    assert {"kind": "report", "id": "report", "label": REPORT_CHIP} in chips
    # Exactly one: the verdict's ride and the general ride never double.
    assert [chip["id"] for chip in chips].count("report") == 1
    # One section is enough — a starved session still walks away with a
    # partial report.
    assert any(
        chip["id"] == "report" for chip in research_suggestions(report_state(sections=1))
    )
    # Zero sections: absent — the lock. The artifact does not exist, so
    # the sheet must not offer it.
    assert not any(
        chip["id"] == "report"
        for chip in research_suggestions(report_state(sections=0))
    )
    # The chip is a DOWNLOAD, not a turn command: the deterministic
    # command resolver must never know its label — a tap sends no
    # message and spends no turn.
    assert research.resolve_command(REPORT_CHIP) is None


def test_the_chip_rides_beside_the_closing_reviews_accept_chips():
    state = report_state()
    state["subquestions"] = [
        {"id": "q1", "name": "شهود", "text": "شهود در قرآن", "status": "searched"},
        {"id": "q2", "name": "وحی", "text": "ساحت وحی", "status": "searched"},
    ]
    state["pending_proposals"] = [
        {
            "id": "p1",
            "kind": "closing_review",
            "text": "بازبینی پایانی انجام شد؛ سند مقصد را می‌رساند.",
            "verdict": "delivers",
            "failing": [],
        }
    ]
    chips = research_suggestions(state)
    assert chips[0]["kind"] == "proposal" and chips[0]["id"] == "p1"
    assert chips[0]["accept"] is True
    # The walk-away moment: the chip sits DIRECTLY beside the review's
    # accept chip, before any move.
    assert chips[1]["id"] == "report" and chips[1]["kind"] == "report"

    # A crowded row (a pending question pulls the gather chips in) —
    # the cap never cuts the chip away from the accept pair.
    state["subquestions"][1]["status"] = "pending"
    crowded = research_suggestions(state)
    assert crowded[0]["id"] == "p1" and crowded[1]["id"] == "report"


# --- the Markdown twin --------------------------------------------------------


def test_the_markdown_twin_carries_the_brief_body_in_order():
    md = report_markdown(report_state())
    assert md is not None
    lines = md.splitlines()
    # No destination, no map, no question preamble: the body STARTS at
    # the first section title.
    assert lines[0] == "## شهود و ساحت"
    assert lines.index("## شهود و ساحت") < lines.index("## بخش گرسنه")
    assert lines.index("## بخش گرسنه") < lines.index("## منابع")
    # The quotes stand apart as blockquote lines with their true
    # references — resolved through the same source-index rule as the
    # HTML report.
    assert (
        "> «قرآن کتابی است برای زندگی» — طرح کلی اندیشۀ اسلامی در قرآن، صفحات ۷۴۰ تا ۷۴۵"
        in md
    )
    assert "> «انسان در جامعه می‌زیید» — انسان ۲۵۰ ساله، صفحهٔ ۱۲" in md
    # The paragraphs' own text rides as text, not lost.
    assert "جملهٔ پیوند" in md and " و باز " in md
    # An honest-gap section renders as its diagnosed Farsi note.
    assert "بخش «بخش گرسنه»: کتاب‌ها شواهد کافی ندارند — شکاف، نه ساختگی." in md


def test_the_manabe_list_is_the_unique_references_in_first_use_order():
    md = report_markdown(report_state())
    tail = md[md.index("## منابع") :]
    assert "1. طرح کلی اندیشۀ اسلامی در قرآن، صفحات ۷۴۰ تا ۷۴۵" in tail
    assert "2. انسان ۲۵۰ ساله، صفحهٔ ۱۲" in tail
    # First use, not ledger order: the source indexes already decide the
    # order — this state quotes e1 before e2.
    assert tail.index("طرح کلی اندیشۀ اسلامی در قرآن") < tail.index("انسان ۲۵۰ ساله")


def test_the_markdown_twin_holds_no_map_no_question_no_transcript():
    md = report_markdown(report_state())
    # No question preamble, no destination, no map rows, no decisions.
    assert QUESTION not in md
    assert "فهم روشنی از جایگاه شهود" not in md
    assert "پرسش‌های باز" not in md and "تصمیم‌ها" not in md
    assert "شکاف‌های اعلام‌شده" not in md
    assert "پرسش به شهود محدود شد" not in md
    # The transcript is process, not deliverable — never in the twin.
    assert TRANSCRIPT_MARKER not in md


def test_zero_sections_refuses_the_twin_too():
    assert report_markdown(report_state(sections=0)) is None


# --- the endpoint: md is the attachment, html stays inline --------------------


def test_the_md_endpoint_serves_the_attachment_headers(tmp_path):
    base, server, originals = with_gate(tmp_path, None)
    try:
        research_store.create_session("s-twin-1", PHONE, report_state())

        status, headers, body = fetch_raw(
            base, "/research/report?session=s-twin-1&format=md"
        )
        assert status == 200
        assert headers.get("Content-Type") == "text/markdown; charset=utf-8"
        assert (
            headers.get("Content-Disposition")
            == 'attachment; filename="gonzarsh-seshat-s-twin-1.md"'
        )
        assert body.startswith("## شهود و ساحت")

        # The default stays the inline RTL document — the chip's fetch
        # and the print-to-PDF path — with no attachment header at all.
        status, headers, body = fetch_raw(base, "/research/report?session=s-twin-1")
        assert status == 200
        assert headers.get("Content-Type") == "text/html; charset=utf-8"
        assert headers.get("Content-Disposition") is None
        assert 'dir="rtl"' in body and "گزارش نشست" in body
    finally:
        stop_gate(server, originals)


# --- the honest README ---------------------------------------------------------


def test_the_readme_records_the_fetch_on_load_truth():
    text = README.read_text(encoding="utf-8")
    section = text.split("## Research Mode", 1)[1].split("\n## ", 1)[0]
    # The stale V0.1 claim is gone from the whole README — the sheet has
    # re-fetched on load since ADR-0011.
    assert "not re-fetched in V0.1" not in text
    assert "not re-fetched" not in section
    # The truth recorded: the transcript and the map both ride the
    # load, the refresh restores the visible conversation, and the
    # session continues server-side.
    assert "re-fetches the whole visible conversation on load" in section
    assert "/research/messages" in section
    assert "/research/state" in section
    assert "continues server-side" in section
    # The report chip and the md twin are recorded where the sheet's
    # behavior lives.
    assert REPORT_CHIP in section
    assert "format=md" in section


# --- the sheet's wiring (the light source lock) --------------------------------


def test_the_sheet_wires_the_report_chip_as_a_download():
    html = INDEX.read_text(encoding="utf-8")
    # The render branch and the one download action exist.
    assert 'item.kind === "report"' in html
    assert "downloadResearchReport" in html
    # The blob saves under the artifact's own name.
    assert "gonzarsh-seshat-${researchSessionId}.html" in html
    # The DRAFT marker stands in the sheet, naming the approval it
    # awaits and the one constant a rename touches.
    assert "DRAFT" in html and "RESEARCH_REPORT_CHIP" in html
    # The label lives in ONE server constant — the sheet carries no
    # copy of the text outside the DRAFT comment.
    assert RESEARCH.read_text(encoding="utf-8").count(REPORT_CHIP) == 1
