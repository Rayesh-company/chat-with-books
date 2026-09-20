"""The Session report (T18, GitLab #19): golden shape over the six parts —
question with its version count, destination, map summary, the Brief
sections with their quotes and pages, the server-built «منابع» — the
transcript's absence, the ≥1-section rule, self-containment, and the
phone gate. The renderer is a pure function over the state; the store
read is faked, not SQLite."""

import pytest

from ui import report, research_store
from ui.report import report_html, report_references, research_session_report

REPORT = "گزارش نشست"


def make_state() -> dict:
    """A session state holding everything the six parts read — plus a
    transcript marker that must never appear in the document."""
    return {
        "research_question": {
            "current": "رابطۀ شهود و وحی در اندیشۀ اسلامی چیست؟",
            "versions": [{"text": "نخستین پرسش"}, {"text": "پرسش کنونی"}],
        },
        "map": {
            "destination": "فهم روشنی از جایگاه شهود نزد مصطفی ملکیان",
            "fog": [],
        },
        "subquestions": [
            {"text": "شهود در قرآن", "status": "searched"},
            {"text": "ساحت وحی", "status": "pending"},
        ],
        "decisions": [{"text": "پرسش به شهود محدود شد"}],
        "gaps": [{"text": "کتاب‌ها دربارهٔ ساحت وحی کم می‌آورند"}],
        "evidence": [
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
        ],
        "brief_document": {
            "complete": True,
            "sections": [
                {
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
                },
                {"title": "بخش گرسنه", "paragraphs": [], "gap": True},
            ],
        },
        "messages": ["نشانهٔ منحصربه‌فردِ گفتگوی خام ۹۹۱۱"],
        "turns": 7,
    }


def test_the_six_parts_render_in_order():
    state = make_state()
    document = report_html(state)

    assert document is not None
    for marker in (
        REPORT,
        "پرسش پژوهش",
        "رابطۀ شهود و وحی در اندیشۀ اسلامی چیست؟",
        "نسخۀ 2",
        "مقصد",
        "فهم روشنی از جایگاه شهود نزد مصطفی ملکیان",
        "پرسش‌های باز",
        "تصمیم‌ها",
        "شکاف‌های اعلام‌شده",
        "خلاصۀ پژوهش",
        "شهود و ساحت",
        "منابع",
    ):
        assert marker in document, f"the report never renders {marker}"


def test_the_map_summary_carries_statuses_and_counts():
    document = report_html(make_state())

    assert "در انتظار" in document, "a pending question renders its own status"
    assert "جست‌وجو شد" in document, "a searched question renders its own status"
    assert "کتاب‌ها دربارهٔ ساحت وحی کم می‌آورند" in document, "declared Gaps render"
    assert "7" in document and "2" in document, "the counts (turns, evidence) render"


def test_quotes_render_highlighted_with_their_true_pages():
    document = report_html(make_state())

    assert 'class="q"' in document
    assert "قرآن کتابی است برای زندگی" in document
    assert 'title="طرح کلی اندیشۀ اسلامی در قرآن، صفحات ۷۴۰ تا ۷۴۵"' in document
    assert 'title="انسان ۲۵۰ ساله، صفحهٔ ۱۲"' in document
    assert "بخش «بخش گرسنه»" in document and "شکاف" in document, "an honest-gap section renders as its note"


def test_references_are_server_built_in_first_use_order():
    state = make_state()
    references = report_references(state, report._brief_sections(state))

    assert references == [
        "طرح کلی اندیشۀ اسلامی در قرآن، صفحات ۷۴۰ تا ۷۴۵",
        "انسان ۲۵۰ ساله، صفحهٔ ۱۲",
    ]
    document = report_html(state)
    assert document.index("طرح کلی اندیشۀ اسلامی در قرآن، صفحات ۷۴۰ تا ۷۴۵") < document.index(
        "انسان ۲۵۰ ساله، صفحهٔ ۱۲"
    )


def test_the_transcript_never_enters_the_document():
    state = make_state()

    document = report_html(state)

    assert "نشانهٔ منحصربه‌فردِ گفتگوی خام ۹۹۱۱" not in document


def test_the_document_is_self_contained_and_rtl():
    state = make_state()
    state["map"]["destination"] = "<script>alert(1)</script> مقصد"

    document = report_html(state)

    assert 'dir="rtl"' in document and 'lang="fa"' in document
    assert "<script" not in document, "no script tag ever rides — styles are inline, nothing external"
    assert "<link" not in document and "http://" not in document and "https://" not in document
    assert "&lt;script&gt;" in document, "state text is escaped into inert text, never injected"


def test_zero_sections_refuses_in_farsi():
    state = make_state()
    state["brief_document"] = {"complete": False, "sections": []}

    assert report_html(state) is None


def test_the_read_is_phone_gated_and_closed_sessions_still_answer(monkeypatch):
    state = make_state()
    sessions = {
        "s-1": {"account": "09120000000@sheet.test", "state": state},
        "s-closed": {"account": "09120000000@sheet.test", "state": {**state, "closed": True}},
        "s-other": {"account": "other@sheet.test", "state": state},
    }
    monkeypatch.setattr(research_store, "load_session", lambda session_id: sessions.get(session_id))

    document, error = research_session_report("09120000000@sheet.test", "s-1")
    assert error is None and document is not None and REPORT in document

    closed, closed_error = research_session_report("09120000000@sheet.test", "s-closed")
    assert closed is not None and closed_error is None, "a finished Session's report is the deliverable — closed still answers"

    stranger, stranger_error = research_session_report("other@sheet.test", "s-1")
    assert stranger is None
    assert stranger_error[0] == 404

    missing, missing_error = research_session_report("09120000000@sheet.test", "s-nope")
    assert missing is None and missing_error[0] == 404


def test_a_session_without_sections_refuses_with_the_farsi_detail(monkeypatch):
    state = make_state()
    state["brief_document"] = {"complete": False, "sections": []}
    monkeypatch.setattr(research_store, "load_session", lambda session_id: {"account": "09120000000@sheet.test", "state": state})

    document, error = research_session_report("09120000000@sheet.test", "s-1")

    assert document is None
    assert error[0] == 409
    assert "خلاصۀ پژوهش" in error[1]


def test_the_route_is_wired():
    serve_source = (report.__file__ and "") or ""
    from pathlib import Path

    serve_text = (Path(report.__file__).parent / "serve.py").read_text(encoding="utf-8")
    assert '/research/report' in serve_text, "the sheet's server exposes the report read"
    assert "research_session_report" in serve_text, "the route reaches the report read"
