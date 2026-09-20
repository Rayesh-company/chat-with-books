from tests.conftest import REPO_ROOT

README = REPO_ROOT / "README.md"
UI = REPO_ROOT / "ui" / "index.html"
SERVE = REPO_ROOT / "ui" / "serve.py"
COMPOSE = REPO_ROOT / "compose.yaml"


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
