"""The Session report (T18, GitLab #19): the walk-away artifact of a
Session — the research question with its version count, the destination,
the map summary (open questions with statuses, decisions, declared Gaps,
the counts), the standing Brief's sections with their quotes and pages,
and the «منابع» built server-side from the passages actually quoted. The
chat transcript is process, not deliverable, and never enters it.

One self-contained RTL HTML document: every style inline, a print
stylesheet so the browser's print-to-PDF is the PDF path with zero
Farsi-shaping dependencies. Renders from the state's `brief_document`
with ≥1 section — a starved Session still finishes with its partial
report. The quote parts' `source` indexes resolve against the live
evidence ledger: the state cap's front-trim re-anchors the standing
document in the same mutation (T12), so the pages stay true here.

Beside the HTML document lives the Markdown twin (T19, GitLab #22):
`report_markdown` serves the Brief's BODY alone for text reuse —
section headings, the paragraphs' own words, the verbatim quotes as
blockquotes with their true references, the honest-gap notes, and the
same first-use «منابع» — with no map, no question preamble, and no
transcript. One artifact, two renders, one source-index resolution:
a citation cannot disagree between them."""

from __future__ import annotations

import html
from datetime import datetime

# The repo runs this module inside the ui package; the container runs it
# flat beside the other modules (the serve.py dual-import shape) — both
# must resolve, or the report endpoint dies only in the deployed image,
# exactly the failure the manual stack test caught (2026-09-19).
try:
    from ui.research import (
        RESEARCH_SESSION_NOT_FOUND_DETAIL,
        ensure_state_shape,
        research_state_summary,
    )
    from ui import research_store
except ImportError:  # the container runs this file flat beside the modules
    from research import (
        RESEARCH_SESSION_NOT_FOUND_DETAIL,
        ensure_state_shape,
        research_state_summary,
    )
    import research_store

RESEARCH_REPORT_EMPTY_DETAIL = (
    "خلاصۀ پژوهش هنوز بخشی ندارد؛ گزارش نشست پس از نخستین بخش آماده می‌شود."
)

REPORT_TITLE = "گزارش نشست"

# The map's own vocabulary (CONTEXT.md's approved rows): a question
# status as the report renders it.
_QUESTION_STATUS_LABELS = {
    "pending": "در انتظار",
    "searched": "جست‌وجو شد",
    "gap": "شکاف",
}


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def _status_label(status: str) -> str:
    return _QUESTION_STATUS_LABELS.get(status, status)


def _evidence_reference(state: dict, part: dict) -> str:
    """A quote part's `source` index resolved against the live evidence
    ledger — the ONE resolution the HTML report's tooltip and the
    Markdown twin's reference lines both read, so a citation can never
    drift between the twins. Same rule as `report_references`: a part
    pointing outside the standing ledger resolves to no reference at
    all (the cap's front-trim re-anchors positions in the same
    mutation, T12), never a guessed one."""
    index = part.get("source")
    evidence = state.get("evidence") or []
    if isinstance(index, int) and 0 <= index < len(evidence):
        return evidence[index].get("reference", "")
    return ""


def _gap_note(title: str) -> str:
    """The honest-gap section's diagnosed Farsi note (T8's writer
    fallback, the report's own rendering of it): a Gap, never a
    fabrication — the same sentence in both twins."""
    return f"بخش «{title}»: کتاب‌ها شواهد کافی ندارند — شکاف، نه ساختگی."


def _brief_sections(state: dict) -> list[dict]:
    document = state.get("brief_document") or {}
    return [
        item for item in document.get("sections", []) if isinstance(item, dict)
    ]


def report_references(state: dict, sections: list[dict]) -> list[str]:
    """The «منابع» of the report, built server-side from the passages
    actually quoted — `with_references`' shape against the live evidence
    ledger: guaranteed real, never model-invented, first-use order."""
    evidence = state.get("evidence", [])
    used: list[str] = []
    for entry in sections:
        for paragraph in entry.get("paragraphs", []):
            for part in paragraph.get("parts", []):
                index = part.get("source")
                if (
                    isinstance(index, int)
                    and not isinstance(index, bool)
                    and 0 <= index < len(evidence)
                ):
                    reference = evidence[index].get("reference", "")
                    if reference and reference not in used:
                        used.append(reference)
    return used


_STYLE = """
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { font-family: Vazirmatn, Tahoma, "Segoe UI", sans-serif; margin: 0;
         background: #f6f2ec; color: #2b2320; line-height: 2; }
  .page { max-width: 860px; margin: 0 auto; padding: 32px 40px 64px; }
  header.report { border-bottom: 3px solid #b4552d; padding-bottom: 12px;
                  margin-bottom: 24px; }
  header.report h1 { font-size: 28px; margin: 0 0 4px; color: #b4552d; }
  header.report .meta { font-size: 13px; color: #7a6a60; }
  h2 { font-size: 19px; color: #8a3d20; border-right: 4px solid #b4552d;
       padding-right: 10px; margin: 28px 0 10px; }
  h3 { font-size: 16px; margin: 18px 0 6px; }
  .question { font-size: 17px; font-weight: 600; }
  .versions { font-size: 13px; color: #7a6a60; font-weight: 400; }
  .destination { background: #fff; border: 1px solid #e3d7c8;
                 border-radius: 8px; padding: 10px 16px; }
  ul.map { list-style: none; padding: 0; margin: 6px 0; }
  ul.map li { padding: 4px 0; border-bottom: 1px dashed #e3d7c8; }
  .status { display: inline-block; font-size: 12px; background: #efe6d8;
            border-radius: 10px; padding: 0 10px; margin-left: 8px; }
  .counts { font-size: 13px; color: #7a6a60; }
  section.brief h3 { color: #4a3b33; }
  .q { background: #f3e2c7; border-bottom: 2px solid #b4552d;
       padding: 0 3px; }
  .gap-note { background: #fdf3f0; border-right: 3px solid #c0392b;
              padding: 8px 14px; border-radius: 6px; }
  ol.sources li { padding: 3px 0; font-size: 14px; }
  footer.note { margin-top: 36px; font-size: 12px; color: #7a6a60;
                border-top: 1px solid #e3d7c8; padding-top: 10px; }
  @media print {
    body { background: #fff; }
    .page { max-width: none; padding: 0 8mm; }
    h2 { break-after: avoid; }
    section.brief h3 { break-after: avoid; }
    .q { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  }
"""


def report_html(state: dict) -> str | None:
    """The report document, or None when the standing Brief has no
    section yet — the caller turns that into the Farsi refusal."""
    summary = research_state_summary(state)
    sections = _brief_sections(state)
    if not sections:
        return None

    question = summary.get("research_question", "")
    versions = summary.get("rq_versions", 0)
    destination = summary.get("destination", "")
    open_questions = summary.get("open_questions", [])
    decisions = summary.get("decisions", [])
    gaps = summary.get("gaps", [])
    evidence_count = summary.get("evidence_count", 0)
    turns = summary.get("turns", 0)
    references = report_references(state, sections)

    question_block = f'<p class="question">{_esc(question)}'
    if versions > 1:
        question_block += f' <span class="versions">(نسخۀ {versions})</span>'
    question_block += "</p>"

    def map_list(items, render):
        if not items:
            return '<li class="counts">—</li>'
        return "".join(f"<li>{render(item)}</li>" for item in items)

    questions_html = map_list(
        open_questions,
        lambda item: (
            f"{_esc(item.get('text', ''))}"
            f'<span class="status">{_esc(_status_label(item.get("status", "")))}</span>'
        ),
    )
    decisions_html = map_list(decisions, _esc)
    gaps_html = map_list(gaps, _esc)

    sections_html = []
    for entry in sections:
        title = entry.get("title", "")
        if entry.get("gap"):
            body = f'<p class="gap-note">{_esc(_gap_note(title))}</p>'
        else:
            paragraphs_html = []
            for paragraph in entry.get("paragraphs", []):
                parts_html = []
                for part in paragraph.get("parts", []):
                    if "quote" in part:
                        tooltip = _esc(_evidence_reference(state, part))
                        parts_html.append(
                            f'<span class="q" title="{tooltip}">{_esc(part.get("quote", ""))}</span>'
                        )
                    else:
                        parts_html.append(_esc(part.get("text", "")))
                paragraphs_html.append("<p>" + "".join(parts_html) + "</p>")
            body = "".join(paragraphs_html)
        sections_html.append(f'<section class="brief"><h3>{_esc(title)}</h3>{body}</section>')

    sources_html = (
        "<ol>" + "".join(f"<li>{_esc(item)}</li>" for item in references) + "</ol>"
        if references
        else '<p class="counts">—</p>'
    )
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(REPORT_TITLE)}</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="page">
<header class="report">
  <h1>{_esc(REPORT_TITLE)}</h1>
  <div class="meta">تهیه‌شده در {stamp} · {turns} نوبت گفتگو · همهٔ نقل‌قول‌ها واژه‌به‌واژه از کتاب‌ها</div>
</header>

<h2>پرسش پژوهش</h2>
{question_block}

<h2>مقصد</h2>
<p class="destination">{_esc(destination) if destination else "—"}</p>

<h2>نقشۀ پژوهش</h2>
<h3>پرسش‌های باز</h3>
<ul class="map">{questions_html}</ul>
<h3>تصمیم‌ها</h3>
<ul class="map">{decisions_html}</ul>
<h3>شکاف‌های اعلام‌شده</h3>
<ul class="map">{gaps_html}</ul>
<p class="counts">شواهد گردآوری‌شده: {evidence_count} · نوبت‌ها: {turns}</p>

<h2>خلاصۀ پژوهش</h2>
{''.join(sections_html)}

<h2>منابع</h2>
{sources_html}

<footer class="note">این گزارش از حالت پژوهش همان نشست ساخته شده است؛ گفتگوی خام آن بخشی از این سند نیست. چاپ این صفحه همان مسیر PDF است.</footer>
</div>
</body>
</html>"""


def report_markdown(state: dict) -> str | None:
    """The Session report's Markdown twin (T19, GitLab #22): the Brief's
    BODY only, for text reuse — the section titles as `##` headings in
    the plan's order, each paragraph's own text as plain lines with its
    verbatim quotes standing apart as `> «quote» — reference` lines
    (the reference from the evidence ledger through the same
    source-index resolution as the HTML report, so the twins can never
    disagree about a citation), an honest-gap section as its diagnosed
    Farsi note, and a closing «منابع» list of the unique references in
    first-use order (`report_references`, the server-built list —
    never model-invented). Deliberately nothing else: no destination,
    no map rows, no question preamble, and never the transcript — the
    twin exists so the Brief's words can be lifted into other
    documents, not so the Session's process travels with them."""
    sections = _brief_sections(state)
    if not sections:
        return None

    lines: list[str] = []
    for entry in sections:
        title = entry.get("title", "")
        lines.append(f"## {title}")
        lines.append("")
        if entry.get("gap"):
            lines.append(_gap_note(title))
            lines.append("")
            continue
        for paragraph in entry.get("paragraphs", []):
            text_parts = []
            quote_lines = []
            for part in paragraph.get("parts", []):
                if "quote" in part:
                    quote = part.get("quote", "")
                    quote_lines.append(
                        f"> «{quote}» — {_evidence_reference(state, part)}"
                    )
                else:
                    text_parts.append(str(part.get("text", "")))
            text = "".join(text_parts)
            if text.strip():
                lines.append(text)
                lines.append("")
            lines.extend(quote_lines)
            if quote_lines:
                lines.append("")
    references = report_references(state, sections)
    lines.append("## منابع")
    lines.append("")
    if references:
        for position, reference in enumerate(references, start=1):
            lines.append(f"{position}. {reference}")
    else:
        lines.append("—")
    return "\n".join(lines).strip() + "\n"


def research_session_report(phone: str, session_id: str, fmt: str = "html"):
    """The report read (T18, GitLab #19): phone-matched like every other
    session read — and, unlike the state panel, a CLOSED session still
    answers, because a finished Session's report is the deliverable.
    (document, None) or (None, (status, Farsi detail)). `fmt` picks the
    twin (T19): "html" — the default — is the self-contained RTL
    document; "md" is the Markdown twin of the Brief's body. Both twins
    answer only once a section stands (the Farsi refusal otherwise);
    anything but "md" reads as the default html, the caller never
    needs a third branch."""
    session = research_store.load_session(session_id)
    if session is None or session["phone"] != phone:
        return None, (404, RESEARCH_SESSION_NOT_FOUND_DETAIL)
    state = session["state"]
    ensure_state_shape(state)
    document = report_markdown(state) if fmt == "md" else report_html(state)
    if document is None:
        return None, (409, RESEARCH_REPORT_EMPTY_DETAIL)
    return document, None
