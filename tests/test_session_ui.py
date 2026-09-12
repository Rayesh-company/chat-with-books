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


def test_session_ui_splits_answers_into_three_switchable_sections():
    # PM brief, 2026-09-11: each phase generates into its own section
    # with a distinct separation and tabs to switch between them, while
    # a line names the phase being generated.
    html = UI.read_text(encoding="utf-8")
    assert 'role="tablist"' in html
    for tab in ("tab-1", "tab-2", "tab-3"):
        assert f'id="{tab}"' in html
    for panel in ("panel-1", "panel-2", "panel-3"):
        assert f'id="{panel}"' in html
    assert "پاسخ اول" in html
    assert "پاسخ استنادی" in html
    # Phase 3 is the Deep dive now (CONTEXT.md: a second study) — the
    # superseded «جست‌وجوی سطح بعدی» name is gone with the auto-start.
    assert "مطالعۀ عمیق" in html
    assert "جست‌وجوی سطح بعدی" not in html
    assert "selectPhase(" in html
    assert 'id="phase-now"' in html


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
    assert 'id="ttft"' in html
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


def test_session_ui_never_serves_from_stale_cache():
    # A browser that heuristically cached the page kept rendering the
    # pre-tab sheet after the container switchover (2026-09-11): every
    # response must carry Cache-Control: no-cache so loads revalidate.
    text = SERVE.read_text(encoding="utf-8")
    assert "Cache-Control" in text
    assert 'send_header("Cache-Control", "no-cache")' in text
