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
    # as it parsed it to the picker, under a pulsing Farsi status. The
    # vanishing-content fix (2026-09-24) rides the sitting's id and the
    # ask's key along, so the picker's snapshot settles SERVER-SIDE.
    html = UI.read_text(encoding="utf-8")
    assert "function renderQuoteSelection" in html
    assert 'fetch("/quote-selection"' in html
    assert "session_id: sessionState.storeId || undefined" in html
    assert "ask_key: currentAskKey || undefined" in html
    assert "در حال انتخاب نقل‌قول‌ها" in html


def test_sheet_paints_the_streamed_prose_as_a_draft_the_settle_retires():
    # Issue #28 revisited (impeccable critique, 2026-09-28): the frozen
    # sheet read as a hang while the run billed, so the deltas paint —
    # but as a visibly subordinate draft (.answer.draft-stream), never
    # as the answer. The #28 contract stands unchanged: the settle paths
    # (Quote selection / quoted-answer / prose fallback) own the answer,
    # the draft retires when `final` lands, and TTFT still comes from
    # markFirstToken on arrival.
    html = UI.read_text(encoding="utf-8")
    # The delta branch accumulates the prose and paints it throttled.
    assert "preview += JSON.parse(data).text" in html
    assert "answerEl.classList.add(\"draft-stream\")" in html
    assert "renderMarkdown(answerEl, preview)" in html
    assert "markFirstToken" in html
    # The subordinate styling exists — the draft must never read as the
    # finished answer.
    assert ".answer.draft-stream" in html
    # The retire points: `final` drops the class before the settle
    # renderers run, and the SSE tail retires it unconditionally.
    assert "retireDraft" in html
    assert html.index("retireDraft") < html.index(
        "renderFinal(textOf(JSON.parse(data).results))"
    )
    # The selection still lands through the picker call; phase 2 keeps
    # its exact payload (locked verbatim in test_quoted_answer.py).
    assert "renderQuoteSelection(query, prose, citations)" in html


def test_sheet_keeps_the_evidence_pool_collapsed_in_the_ask_article():
    # Issue #28, re-homed by ADR-0014: the «استناد» heading and list
    # stay visible inside a collapsed <details> under the selection —
    # inspectable, and still phase 2's input. The pool builds per ask
    # inside the ask's article, before the Quoted answer's section.
    html = UI.read_text(encoding="utf-8")
    assert 'className = "evidence-pool"' in html
    assert "همۀ نقل‌قول‌های بازیابی‌شده" in html
    assert 'className = "citations-heading"' in html
    assert 'className = "citations"' in html
    assert html.index('className = "evidence-pool"') < html.index(
        'className = "quoted-doc"'
    )


def test_sheet_falls_back_to_the_prose_when_the_selection_cannot_be_prepared():
    # Issue #28: an empty selection reply (picker failed or below the
    # floor) falls back to the streamed prose — the sheet is never left
    # empty — with a small note saying the selection could not be made.
    html = UI.read_text(encoding="utf-8")
    assert "renderMarkdown(answerEl, prose)" in html
    assert "انتخاب نقل‌قول‌ها آماده نشد" in html
