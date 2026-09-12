from tests.conftest import REPO_ROOT

README = REPO_ROOT / "README.md"
UI = REPO_ROOT / "ui" / "index.html"


def test_readme_records_recall_hybrid_first_answer_contract():
    text = README.read_text(encoding="utf-8")
    assert "/api/v1/recall" in text
    assert '"searchType":"HYBRID_COMPLETION"' in text
    assert '"includeReferences":true' in text
    assert '"datasets":["tarhe-kolli","70143-336"]' in text


def test_readme_records_farsi_citation_as_cognee_evidence():
    text = README.read_text(encoding="utf-8")
    section = text.split("## First answer", 1)[1].split("## Tests", 1)[0]
    assert "Evidence:" in section
    assert "Do not invent a citation formatter" in section
    assert "omitted for Farsi" not in section
    assert "CHUNKS payload" not in section
    assert "enable_farsi_evidence.py" in section


def test_readme_new_question_starts_a_new_first_answer():
    text = README.read_text(encoding="utf-8")
    assert "A new question starts a new first answer" in text
    recall_json = text.split("/api/v1/recall", 1)[1]
    recall_json = recall_json.split("--data-raw '", 1)[1].split("'", 1)[0]
    assert "sessionId" not in recall_json
    assert "GRAPH_COMPLETION_COT" not in recall_json


def test_readme_records_the_quote_selection_as_the_first_answer():
    # Issue #28 (ADR-0006): the rendered first answer is the Quote
    # selection, not the streamed prose. Recall and the Evidence pool
    # are untouched; the contract below is re-recorded.
    text = README.read_text(encoding="utf-8")
    section = text.split("## First answer", 1)[1].split("## Tests", 1)[0]
    assert "Quote selection" in section
    assert "/quote-selection" in section
    # Exactly one picker call, the existing composer pin, thinking
    # disabled — copy-matching, not reasoning.
    assert "glm-5.3-flash" in section
    assert "thinking disabled" in section
    assert "ONE" in section
    # Ten aimed, guarded 4-12 — the cap and floor pinned in words.
    assert "aim" in section
    assert "twelve" in section
    assert "four" in section
    # The reply records the pool size beside the selections.
    assert "pool_size" in section
    assert "VERBATIM" in section
    # The verbatim guard drops paraphrases and wrong-index claims.
    assert "wrong index" in section
    # Per-sentence Citation: Book identity plus first page.
    assert "first page" in section
    # The pool stays visible-collapsed and stays phase 2's exact payload.
    assert "collapsed" in section
    assert "exact payload" in section
    # The prose is no longer displayed but still feeds the Quoted answer.
    assert "no longer" in section
    assert "/quoted-answer" in section
    # The picker's gate: a chat today, never counted.
    assert "never records or counts" in section
    # TTFT still measures first token arrival without the painted preview.
    assert "first token" in section
    # The sheet is never left empty: prose fallback.
    assert "never left empty" in section


def test_sheet_calls_the_quote_selection_picker_with_the_pool():
    # Issue #28: with citations present the sheet POSTs the pool exactly
    # as it parsed it to the picker, under a pulsing Farsi status.
    html = UI.read_text(encoding="utf-8")
    assert "function renderQuoteSelection" in html
    assert 'fetch("/quote-selection"' in html
    assert "body: JSON.stringify({ question, sources })" in html
    assert "در حال انتخاب نقل‌قول‌ها" in html


def test_sheet_no_longer_paints_the_streamed_prose_as_the_phase1_answer():
    # Issue #28: deltas still arrive and still drive the TTFT readout,
    # but the prose preview is no longer painted; the prose is kept for
    # /quoted-answer's answer field, never rendered as the first answer.
    html = UI.read_text(encoding="utf-8")
    assert "renderMarkdown(answerEl, preview)" not in html
    # The write-only accumulator is gone entirely (full-spec review,
    # 2026-09-12): nothing accumulates the prose, TTFT comes from
    # markFirstToken alone.
    assert "preview" not in html
    assert "markFirstToken" in html
    # The selection lands through the picker call; phase 2 keeps its
    # exact payload (the raw split answer, locked verbatim in
    # test_quoted_answer.py).
    assert "renderQuoteSelection(query, prose, citations)" in html


def test_sheet_keeps_the_evidence_pool_collapsed_in_the_first_tab():
    # Issue #28: the «استناد» heading and list stay visible inside a
    # collapsed <details> under the selection — inspectable, and still
    # phase 2's input.
    html = UI.read_text(encoding="utf-8")
    assert '<details id="evidence-pool">' in html
    assert "همۀ نقل‌قول‌های بازیابی‌شده" in html
    assert 'id="citations-heading"' in html
    assert 'id="citations"' in html
    # The pool lives inside phase 1's panel, before phase 2's.
    assert html.index('id="evidence-pool"') < html.index('id="panel-2"')


def test_sheet_falls_back_to_the_prose_when_the_selection_cannot_be_prepared():
    # Issue #28: an empty selection reply (picker failed or below the
    # floor) falls back to the streamed prose — the sheet is never left
    # empty — with a small note saying the selection could not be made.
    html = UI.read_text(encoding="utf-8")
    assert "renderMarkdown(answerEl, prose)" in html
    assert "انتخاب نقل‌قول‌ها آماده نشد" in html
