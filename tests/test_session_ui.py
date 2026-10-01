from tests.conftest import REPO_ROOT

README = REPO_ROOT / "README.md"
UI = REPO_ROOT / "ui" / "index.html"
SERVE = REPO_ROOT / "ui" / "serve.py"
COMPOSE = REPO_ROOT / "compose.yaml"


def test_selection_popover_pins_the_notebook_draft_strings():
    # The selection map's variant A toolbar (2026-10-01): a live selection
    # inside a chat message or the book's text layer opens the one door to
    # the notebook; the note quick-saves with defaults and the toast tells
    # so. The ask verb is staged disabled — it joins with the in-book chat
    # drawer (ticket 09) — and reads on the book only.
    html = UI.read_text(encoding="utf-8")
    assert 'id="selection-pop"' in html
    assert 'id="note-toast"' in html
    assert 'id="selection-add"' in html
    assert 'id="selection-ask"' in html
    assert 'ADD_TO_NOTE_LABEL = "افزودن به یادداشت"' in html
    assert 'ASK_FROM_SELECTION_LABEL = "پرسش از این متن"' in html
    assert 'NOTE_SAVED_TOAST = "به دفتر یادداشت اضافه شد"' in html
    assert 'NOTE_VIEW_ACTION = "مشاهده"' in html
    assert "selectionAskBtn.disabled = true" in html
    assert "selectionAskBtn.hidden" in html


def test_selection_to_note_maps_through_the_normalized_stream():
    # The note's durable shape: selected spans re-read in the page's own
    # geometric order (the layer's DOM order is extraction scatter), text
    # via cleanFarsi, and a text-ref per page (at/len) in the page's
    # normalized stream — the coordinate matchOnPage/paintMatch consume,
    # never raw DOM order; a chat-surface note snapshots the sitting's
    # store id as its source.
    html = UI.read_text(encoding="utf-8")
    assert "function selectionToNote()" in html
    assert "noteStaging" in html
    assert "p.top - q.top || q.left - p.left" in html
    assert "normStream(pageRaw)" in html
    assert "matchOnPage(rec, needle)" in html
    assert "note.refs.push(" in html
    assert 'kind: "book"' in html
    assert 'kind: "session"' in html
    assert "sessionState.storeId" in html
    assert "cleanFarsi(pageRaw)" in html
    # The buttons must not collapse the selection they are about to capture.
    assert 'selectionPopEl.addEventListener("mousedown"' in html


def test_notebook_panel_pins_the_pm_batch_strings():
    # Ticket 08: the notebook's surface — every visible string rides a
    # pinned constant (the CONTEXT.md DRAFT row, 2026-10-01); the
    # category list is ONE constant the PM batch fills.
    html = UI.read_text(encoding="utf-8")
    assert 'NOTEBOOK_LABEL = "دفتر یادداشت"' in html
    assert 'NOTE_UNCATEGORIZED = "بدون دسته"' in html
    assert 'NOTE_OPINION_LABEL = "نظر من"' in html
    assert 'NOTE_DELETE_CONFIRM_LABEL = "حذف یادداشت؟"' in html
    assert 'NOTE_COPY_SELECTED = "کپی منتخب‌ها"' in html
    assert 'NOTE_EXPORT_PDF = "خروجی PDF"' in html
    assert 'NOTE_DELETE_SELECTED = "حذف منتخب‌ها"' in html
    assert 'NB_DELETE_MANY_CONFIRM = "حذف منتخب‌ها؟"' in html
    assert 'NOTE_SOURCE_JUMP = "پرش به منبع"' in html
    assert 'NB_SEARCH_PLACEHOLDER = "جست‌وجو در یادداشت‌ها"' in html
    assert 'NB_FILTER_ALL = "همهٔ منابع"' in html
    assert 'NB_SORT_NEW = "تازه‌ترین"' in html
    assert 'NB_SORT_OLD = "کهنه‌ترین"' in html
    assert 'NB_EMPTY = "هنوز یادداشتی نیست."' in html
    assert 'NB_EDIT = "ویرایش"' in html
    assert 'NB_SAVE = "ذخیره"' in html
    assert 'NB_COPIED = "منتخب‌ها کپی شد."' in html
    assert "NOTE_CATEGORIES = [NOTE_UNCATEGORIZED]" in html
    # The topbar door's markup is string-free — the constant labels it.
    assert 'id="notebook-open"' in html


def test_notebook_panel_is_a_real_surface():
    # The ticket-02 shape: slide-over with tools (search / source filter /
    # sort), a bulk bar behind the checkboxes, grouped cards with the
    # fixed quote, the editable editor, the two-step delete (the sheet's
    # own pinned «حذف» arms it), and the store's endpoints — plus the
    # zero-dependency print view as the exporter.
    html = UI.read_text(encoding="utf-8")
    for marker in (
        'id="notebook"',
        'id="nb-search"',
        'id="nb-filter"',
        'id="nb-sort"',
        'id="nb-bulk"',
        'id="nb-copy"',
        'id="nb-export"',
        'id="nb-del-many"',
        'id="nb-list"',
        "async function loadNotes()",
        "function renderNotes()",
        "function noteCard(",
        "function selectedNotes()",
        "function buildNotesPrintHtml(",
        "nbDelManyEl.textContent = NB_DELETE_MANY_CONFIRM",
        "del.textContent = NOTE_DELETE_CONFIRM_LABEL",
        "SESSION_DELETE_LABEL;",
        "openCitation(",
        "openStoreSession(sid);",
        'note.source.dead = true;',
    ):
        assert marker in html, marker


def test_notebook_store_serves_the_panel():
    # The server side: the store module wired in, the five routes, the
    # ownership language, and the popover's POST feeding the create.
    serve_py = SERVE.read_text(encoding="utf-8")
    assert "from ui import note_store" in serve_py
    assert 'path == "/notes"' in serve_py
    assert 'path == "/notes/bulk-delete"' in serve_py
    assert "def _notes_list" in serve_py
    assert "def _note_create" in serve_py
    assert "def _note_update" in serve_py
    assert "def _note_delete" in serve_py
    assert "def _note_bulk_delete" in serve_py
    assert "یادداشت پیدا نشد." in serve_py
    html = UI.read_text(encoding="utf-8")
    assert 'fetch("/notes", {' in html
    assert 'fetch(`/notes/${note.id}`, { method: "DELETE" }' in html
    assert 'fetch("/notes/bulk-delete", {' in html
    store_py = (REPO_ROOT / "ui" / "note_store.py").read_text(encoding="utf-8")
    assert "NOTES_DB" in store_py
    assert "DELETE FROM notes WHERE account = ?" in store_py


def test_session_ui_is_farsi_first_rtl():
    html = UI.read_text(encoding="utf-8")
    assert 'lang="fa"' in html
    assert 'dir="rtl"' in html
    assert "طرح کلی اندیشۀ اسلامی در قرآن" in html


def test_session_ui_pins_first_answer_recall_contract():
    html = UI.read_text(encoding="utf-8")
    assert "/api/v1/recall" in html
    assert "HYBRID_COMPLETION" in html
    assert "includeReferences" in html
    assert "tarhe-kolli" in html
    # The browser names no Cognee search type at all — the dive payloads
    # are pinned server-side (issue #25). Tightened from the COT-only
    # assertion: any GRAPH_COMPLETION* string must stay out of the sheet.
    assert "GRAPH_COMPLETION" not in html
    assert "sessionId" not in html


def test_session_ui_shows_citation_not_a_second_summary():
    html = UI.read_text(encoding="utf-8")
    assert "استناد" in html
    assert "دقیقه‌سازی" not in html


def test_session_ui_threads_the_three_phases():
    # ADR-0014 (supersedes the tabbed PM brief of 2026-09-11): the ask
    # becomes a user bubble and one assistant article carries the
    # phases in place — quiet phase markers, no tab strip anywhere,
    # while a line names the phase being generated.
    html = UI.read_text(encoding="utf-8")
    assert 'role="tablist"' not in html
    assert "msg-user" in html
    assert "msg-assistant" in html
    assert "phase-mark" in html
    assert "پاسخ اول" in html
    assert "پاسخ استنادی" in html
    # Phase 3 is Research Mode now (ADR-0008): the guided research
    # conversation — the superseded dive and auto-start names are gone.
    assert "حالت پژوهش" in html
    assert "مطالعۀ عمیق" not in html
    assert "جست‌وجوی سطح بعدی" not in html
    assert "selectPhase(" in html
    assert 'className = "phase-now"' in html


def test_session_ui_is_a_chat_shell():
    # ADR-0014, T27 stage 1: the document sheet becomes the chat shell —
    # the «فهرست نشست‌ها» sidebar (DRAFT names, PM-gated constants), the
    # thread, the composer with its honest stop, and the theme toggle.
    html = UI.read_text(encoding="utf-8")
    assert 'id="session-list"' in html
    assert "فهرست نشست‌ها" in html
    assert 'id="new-session"' in html
    assert "نشست تازه" in html
    assert 'id="thread-list"' in html
    assert 'id="ask"' in html
    assert 'id="stop-btn"' in html
    assert 'id="theme-toggle"' in html
    # The empty state binds one Book per Session (the Book pick
    # glossary row): two Book cards with starter questions.
    assert html.count('class="book-card"') == 2
    assert 'data-doc="tarhe-kolli"' in html
    assert 'data-doc="70143-336"' in html
    assert "starter-chip" in html
    assert "انتخاب کتاب" in html
    # The legacy phone field is finished off (ADR-0013 retired its
    # authority; the shell retired the field — the header is gone too).
    assert 'id="phone"' not in html
    assert "شمارهٔ تلفن" not in html
    assert "X-Session-Phone" not in html


def test_session_ui_server_proxies_recall_to_cognee():
    text = SERVE.read_text(encoding="utf-8")
    assert "/api/v1/recall" in text
    assert "/health" in text
    assert "8000" in text


def test_readme_records_session_ui_command():
    text = README.read_text(encoding="utf-8")
    assert "python ui/serve.py" in text
    assert "http://localhost:8765" in text


def test_session_ui_streams_the_first_answer():
    html = UI.read_text(encoding="utf-8")
    assert "stream: true" in html
    assert "text/event-stream" in html
    assert "TextDecoder" in html
    assert '"delta"' in html
    assert '"reset"' in html
    assert '"stage"' in html
    assert '"final"' in html


def test_session_ui_shows_ttft_readout():
    html = UI.read_text(encoding="utf-8")
    # The readout rides the ask's meta row now (a per-ask element, no
    # shared id since ADR-0014) — the pin is the wiring, not an id.
    assert "markFirstToken" in html
    assert "نخستین توکن" in html


def test_session_ui_server_relays_sse_without_buffering():
    text = SERVE.read_text(encoding="utf-8")
    assert "text/event-stream" in text
    assert "readline()" in text


def test_compose_enables_answer_streaming():
    text = COMPOSE.read_text(encoding="utf-8")
    assert 'LLM_ANSWER_STREAMING: "true"' in text


def test_readme_records_streaming_seam():
    text = README.read_text(encoding="utf-8")
    assert "LLM_ANSWER_STREAMING" in text
    assert "نخستین توکن" in text
    # Nothing accumulates the prose any more (the dead preview
    # accumulator is gone, full-spec review 2026-09-12), so a reset
    # frame has no accumulated prose to clear.
    assert "accumulated prose" not in text


def test_readme_records_the_missing_key_fallbacks_truthfully():
    # Round-2 review (2026-09-12): "without it the Quoted answer falls
    # back to the Evidence list" was false on both ends — the Evidence
    # pool is never rendered as an answer (CONTEXT.md retires the name
    # too), and the live contract is the picker's prose fallback in
    # phase 1 (its recorded note) and an empty phase 2 whose section
    # lands nothing while phase 1's answer stands. The sentence's true
    # half — start-ui.ps1 reads the key into the process env, serve.py
    # takes it from the host env — stays locked.
    text = README.read_text(encoding="utf-8")
    assert (
        "`start-ui.ps1` reads `LLM_API_KEY` from `.env` into the process env"
        in text
    )
    assert (
        "takes the key from the host env, never from `.env` "
        "(that file is compose-only)" in text
    )
    assert "neither the Quote-selection picker nor the phase-2 composer can run" in text
    assert "falls back to the streamed prose with its recorded note" in text
    assert "land nothing in phase 2's section" in text
    assert "The Evidence pool is never rendered as a fallback" in text
    # The retired false claim never returns.
    assert "falls back to the Evidence list" not in text


def test_session_ui_never_serves_from_stale_cache():
    # A browser that heuristically cached the page kept rendering the
    # pre-tab sheet after the container switchover (2026-09-11): every
    # response must carry Cache-Control: no-cache so loads revalidate.
    # Only the page rasters override it, with an explicit per-response
    # policy (_cache_policy — an immutable cache key of dataset, page,
    # and width bucket); the default stays no-cache.
    text = SERVE.read_text(encoding="utf-8")
    assert "Cache-Control" in text
    assert 'getattr(self, "_cache_policy", None) or "no-cache"' in text
    assert '"max-age=604800, immutable"' in text


def test_switching_a_sitting_is_instant_and_guarded():
    # The 2026-09-29 smoothness pass: a sidebar click used to freeze the
    # old thread through the fetch, swap it in one frame, then swoosh
    # the view to the bottom — with the highlight waiting for the whole
    # render and a fast click-walk letting the slowest fetch win.
    html = UI.read_text(encoding="utf-8")
    # The highlight moves on click, before the fetch; the active row
    # rides the row's own dataset.
    assert "item.dataset.storeId" in html
    assert "function paintSessionListActive" in html
    assert html.index("paintSessionListActive(id);") < html.index(
        "fetch(`/sessions/${id}`"
    )
    # A superseded load is abandoned, not merged.
    assert "sessionSwitchSeq" in html
    assert "if (token !== sessionSwitchSeq) return;" in html
    # Re-clicking the open sitting refetches nothing.
    assert "sessionState.active && sessionState.storeId === id" in html
    # The swap dims the outgoing thread (a crossfade, not a freeze-pop)
    # and lands at the end with a cut, not a glide.
    assert "thread-switching" in html
    assert "scrollThreadEnd(false);" in html


def test_the_draft_belongs_to_the_sitting():
    # Switching sittings never trades words behind the operator's back:
    # the half-asked question is stored under the sitting's own key and
    # restored on resume; the anonymous new-Session draft keeps the
    # legacy key.
    html = UI.read_text(encoding="utf-8")
    assert "function draftKey()" in html
    assert "`composerDraft:s:${sessionState.storeId}`" in html
    assert "localStorage.setItem(draftKey(), questionEl.value)" in html
    assert "localStorage.removeItem(draftKey())" in html
    assert "questionEl.value = localStorage.getItem(draftKey())" in html


def test_the_profile_is_a_card_at_its_own_button():
    # The old in-flow section sat at the page's end: a top-row toggle
    # answered with a panel rows below the fold. The card anchors to
    # its own button (physical offsets from the rect), focuses in, and
    # closes on Escape, an outside click, or a resize.
    html = UI.read_text(encoding="utf-8")
    assert 'aria-label="پروفایل" tabindex="-1"' in html
    assert "#profile:not([hidden])" in html
    assert "function place()" in html
    assert "outsideClose" in html
    assert "escapeClose" in html
    # The card is named by its toggle; the redundant visible heading is
    # gone with the old section.
    assert "<h2>پروفایل</h2>" not in html


def test_the_cost_chip_speaks_credit():
    # 2026-09-29: absolute Toman alone says nothing about how much of
    # the credit a sitting burns. The chip carries a gold ribbon meter
    # and the sitting's share of the Account's Balance — the percentage
    # exists only while a balance is known (an unknown credit renders
    # no share, never a made-up one), and the phone keeps the compact
    # body: meter, percentage, bare Toman; the long label and the
    # credit note wait for the desktop's room.
    html = UI.read_text(encoding="utf-8")
    assert 'id="usage-meter-fill"' in html
    assert "usage-cost-short" in html
    assert "USAGE_CREDIT_NOTE" in html
    assert "has-credit" in html
    assert 'fetch("/usage/live")' in html
    assert "balance_toman" in html
    # The pinned credit note and its DRAFT row travel together.
    assert "از اعتبار" in html
