from tests.conftest import REPO_ROOT

README = REPO_ROOT / "README.md"
UI = REPO_ROOT / "ui" / "index.html"


def test_readme_records_the_retrieval_only_ask_contract():
    # ADR-0014: the first answer's pool comes from the sheet's own /ask
    # endpoint — one only_context HYBRID_COMPLETION search per selected
    # Book on the main Cognee service, no LLM completion in the loop.
    text = README.read_text(encoding="utf-8")
    section = text.split("## First answer", 1)[1].split("## Tests", 1)[0]
    assert "/ask" in section
    assert "only_context" in section
    assert "HYBRID_COMPLETION" in section
    assert "retrieval" in section


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
    ask_block = text.split('fetch("/ask"', 1)
    assert len(ask_block) > 1
    ask_json = ask_block[1].split("}", 1)[0]
    assert "sessionId" not in ask_json
    assert "GRAPH_COMPLETION_COT" not in ask_json


def test_readme_records_the_quote_selection_as_the_first_answer():
    # Issue #28 (ADR-0006) under ADR-0014: the rendered first answer is
    # the Quote selection over the ask's retrieval-only pool — verbatim
    # Book sentences, and the ONLY first answer there is.
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
    assert "/quoted-answer" in section
    # The picker's gate: a chat today, never counted.
    assert "never records or counts" in section
    # ADR-0014: no streamed prose exists at all — an empty selection
    # lands the honest note, never a model-written essay.
    assert "NO LLM completion" in section
    assert "conclusive" in section


def test_readme_records_the_reload_restore():
    # ADR-0014: the ask's row — pool, selection, quoted document — is
    # persisted in the chat store and re-fetched on reload.
    text = README.read_text(encoding="utf-8")
    assert "/chat/latest" in text
    assert "chats.sqlite3" in text


def test_sheet_calls_the_quote_selection_picker_with_the_pool():
    # The sheet POSTs the pool exactly as the ask fetched it to the
    # picker, under a pulsing Farsi status, keyed by the ask's chat_id.
    html = UI.read_text(encoding="utf-8")
    assert "function renderQuoteSelection" in html
    assert 'fetch("/quote-selection"' in html
    assert "body: JSON.stringify({ question, sources, chat_id: chatId })" in html
    assert "در حال انتخاب نقل‌قول‌ها" in html


def test_sheet_renders_no_model_prose_as_the_phase1_answer():
    # ADR-0014: there is no streamed prose and no markdown renderer —
    # the selection (or its honest missed note) is the only first
    # answer the sheet can show.
    html = UI.read_text(encoding="utf-8")
    assert "renderMarkdown(answerEl, preview)" not in html
    assert "preview" not in html
    assert "function renderMarkdown" not in html
    assert "renderQuoteSelection(query, chatId, lines)" in html


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


def test_sheet_keeps_the_honest_note_when_the_selection_cannot_be_prepared():
    # ADR-0014: an empty selection reply (picker failed or below the
    # floor) lands the honest note — the pool's own citations stay
    # visible below — and no model-written answer ever takes its place.
    html = UI.read_text(encoding="utf-8")
    assert "انتخاب نقل‌قول‌ها آماده نشد" in html
    assert "renderMarkdown(answerEl, prose)" not in html
