import json
import os
import sys

from tests.conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))

from ui import serve  # noqa: E402

SERVE = REPO_ROOT / "ui" / "serve.py"
README = REPO_ROOT / "README.md"

# The Book's text layer separates words with real backspace characters and
# carries kashida, ZWNJ, Arabic variants, and a trailing ellipsis. Recorded
# from the live Evidence block, 2026-09-09.
BS = "\b"
NOISY_PASSAGE = (
    f"سـخن{BS}در{BS}این{BS}اسـت؛{BS}اگرچه می‌گویند "
    f"قرآن{BS}کتابی{BS}اسـت{BS}برای{BS}زندگی{BS}جمعی{BS}انسان‌ها و …"
)
OTHER_PASSAGE = "این جمله از قطعهٔ دیگری است و ربطی به قطعهٔ نخست ندارد."

SOURCES = [
    {
        "reference": "chunk 101 of document tarhe-kolli (pages 740-745)",
        "passage": NOISY_PASSAGE,
    },
    {
        "reference": "chunk 29 of document tarhe-kolli",
        "passage": OTHER_PASSAGE,
    },
]


def test_normalize_collapses_pdf_text_noise_to_a_letter_stream():
    # Backspaces, kashida, ZWNJ, punctuation, ellipsis, and spaces all
    # vanish: only the letter sequence survives, so word-separator
    # disagreements between the PDF and the composer cannot mismatch.
    assert serve.normalize_for_match(NOISY_PASSAGE) == (
        "سخندرایناستاگرچهمیگویندقرآنکتابیاستبرایزندگیجمعیانسانهاو"
    )
    # Arabic variants map to Farsi so ي/ك quotes still match.
    assert serve.normalize_for_match("كتابي") == serve.normalize_for_match("کتابی")


def test_guard_keeps_verbatim_sentence_from_noisy_passage():
    sentence = "سخن در این است؛"
    kept = serve.guard_sentences(
        [{"text": sentence, "source": 0}], SOURCES
    )
    assert kept == [
        {
            "text": sentence,
            "reference": "chunk 101 of document tarhe-kolli (pages 740-745)",
        }
    ]


def test_guard_matches_when_the_composer_rejoins_backspace_split_words():
    # The PDF text layer separates words with backspaces (می). The
    # composer writes proper Farsi with ZWNJ (می‌تواند) or spaces; the
    # word sequence is identical, so the guard must match it.
    passage = "چنین\bمی\bگویند:\b انسان\bمی\bتواند\b در\bحوزۀ\bمؤمنین\bباشـد؛"
    sources = [{"reference": "chunk 1 of document tarhe-kolli", "passage": passage}]
    sentence = "انسان می‌تواند در حوزۀ مؤمنین باشد؛"
    kept = serve.guard_sentences([{"text": sentence, "source": 0}], sources)
    assert kept == [{"text": sentence, "reference": "chunk 1 of document tarhe-kolli"}]


def test_guard_drops_paraphrased_sentence():
    paraphrase = "قرآن برنامه‌ای برای زندگی شخصی انسان‌ها ارائه می‌دهد"
    assert serve.guard_sentences([{"text": paraphrase, "source": 0}], SOURCES) == []


def test_guard_drops_sentence_claiming_the_wrong_source():
    # Verbatim from source 0 but labelled source 1: the tooltip would cite
    # the wrong passage, so the guard drops it.
    sentence = "سخن در این است؛"
    assert serve.guard_sentences([{"text": sentence, "source": 1}], SOURCES) == []


def test_guard_drops_malformed_selections():
    sentence = "سخن در این است؛"
    selections = [
        {"text": sentence, "source": 7},
        {"text": sentence, "source": "0"},
        {"text": sentence, "source": 0.0},
        {"text": sentence},
        {"text": "   ", "source": 0},
        {"text": sentence, "source": True},
        {"text": "!!!", "source": 0},
        "not a dict",
    ]
    assert serve.guard_sentences(selections, SOURCES) == []


def test_pages_label_from_the_evidence_locator():
    # The paragraph-end Citation carries the full chunk range — «تا»
    # between the numbers, never a dash (digits are LTR-weak in Farsi
    # text) — while the per-sentence tooltip stays on the first page
    # (PM call + same-evening reversal, 2026-09-10).
    assert serve.pages_label(SOURCES[0]["reference"]) == "صفحات 740 تا 745"
    assert serve.first_page_label(SOURCES[0]["reference"]) == "صفحه 740"
    assert serve.pages_label("chunk 5 of document tarhe-kolli (page 401)") == "صفحه 401"
    assert serve.first_page_label("chunk 5 of document tarhe-kolli (page 401)") == "صفحه 401"
    # No text-layer page markers: the Book alone, never an invented page.
    assert serve.pages_label(SOURCES[1]["reference"]) == ""
    assert serve.first_page_label(SOURCES[1]["reference"]) == ""


def test_parse_quoted_reply_accepts_plain_and_fenced_json():
    payload = {"blocks": [{"type": "paragraph", "parts": [{"text": "مقدمه"}]}]}
    assert serve.parse_quoted_reply(json.dumps(payload)) == payload["blocks"]
    fenced = f"```json\n{json.dumps(payload)}\n```"
    assert serve.parse_quoted_reply(fenced) == payload["blocks"]


def test_parse_quoted_reply_rejects_malformed_content():
    assert serve.parse_quoted_reply("here is the document you asked for") == []
    assert serve.parse_quoted_reply('{"blocks": "not a list"}') == []
    assert serve.parse_quoted_reply("") == []


def test_guard_blocks_keeps_embedded_quotes_and_drops_one_sentence():
    # PM call 2026-09-10 (format): a paragraph is one unit — AI text with
    # verbatim quotes embedded inside it, several passages allowed. A quote
    # sentence that fails the verbatim guard drops alone; the paragraph
    # survives on its other quotes, and every kept quote carries the pages
    # of exactly the passage it claims.
    blocks = [
        {"type": "heading", "text": "۱. مفهوم‌شناسی"},
        {
            "type": "paragraph",
            "parts": [
                {"text": "پیش از هر چیز باید معنای واژه را روشن کرد: "},
                {"quote": "سخن در این است؛", "source": 0},
                {"quote": "قرآن برنامه‌ای برای زندگی شخصی انسان‌ها ارائه می‌دهد", "source": 0},
                {"quote": OTHER_PASSAGE, "source": 1},
                {"text": " بر این اساس، ادامه می‌دهیم."},
            ],
        },
    ]
    assert serve.guard_blocks(blocks, SOURCES) == [
        {"type": "heading", "text": "۱. مفهوم‌شناسی"},
        {
            "type": "paragraph",
            "parts": [
                {"text": "پیش از هر چیز باید معنای واژه را روشن کرد:"},
                {"quote": "سخن در این است؛", "source": 0, "pages_label": "صفحات 740 تا 745", "first_page_label": "صفحه 740"},
                {"quote": OTHER_PASSAGE, "source": 1, "pages_label": "", "first_page_label": ""},
                {"text": "بر این اساس، ادامه می‌دهیم."},
            ],
        },
    ]


def test_guard_blocks_drops_a_paragraph_whose_only_quote_fails_the_guard():
    # With no surviving quote the paragraph would be pure AI text, which
    # the sheet never swaps in (PM call, 2026-09-10) — it drops whole.
    good = {
        "type": "paragraph",
        "parts": [
            {"text": "مقدمه‌ای کوتاه."},
            {"quote": "سخن در این است؛", "source": 0},
        ],
    }
    blocks = [
        {
            "type": "paragraph",
            "parts": [
                {"text": "بر پایهٔ این نگاه،"},
                {"quote": "قرآن برنامه‌ای برای زندگی شخصی انسان‌ها ارائه می‌دهد", "source": 0},
            ],
        },
        good,
        {"type": "heading", "text": "عنوان"},
    ]
    assert serve.guard_blocks(blocks, SOURCES) == [
        {
            "type": "paragraph",
            "parts": [
                {"text": "مقدمه‌ای کوتاه."},
                {"quote": "سخن در این است؛", "source": 0, "pages_label": "صفحات 740 تا 745", "first_page_label": "صفحه 740"},
            ],
        },
        {"type": "heading", "text": "عنوان"},
    ]


def test_guard_blocks_drops_pure_ai_and_bare_quote_paragraphs():
    heading = {"type": "heading", "text": "عنوان"}
    voice_only = {"type": "paragraph", "parts": [{"text": "فقط متن هوش مصنوعی، بدون نقل."}]}
    quotes_only = {"type": "paragraph", "parts": [{"quote": "سخن در این است؛", "source": 0}]}
    assert serve.guard_blocks([heading, voice_only], SOURCES) == []
    assert serve.guard_blocks([heading, quotes_only], SOURCES) == []


def test_guard_blocks_enforces_the_swap_threshold():
    def paragraph():
        return {
            "type": "paragraph",
            "parts": [
                {"text": "مقدمه‌ای کوتاه."},
                {"quote": "سخن در این است؛", "source": 0},
            ],
        }

    # One quoting paragraph is not a document — two quoting paragraphs,
    # or one plus a heading, earn the swap (ADR-0003 threshold, restated
    # for the paragraph-unit format 2026-09-10).
    assert serve.guard_blocks([paragraph()], SOURCES) == []
    assert serve.guard_blocks([paragraph(), paragraph()], SOURCES)
    assert serve.guard_blocks([{"type": "heading", "text": "عنوان"}, paragraph()], SOURCES)
    # Headings alone never earn the swap.
    assert serve.guard_blocks([{"type": "heading", "text": "عنوان"}], SOURCES) == []


def test_guard_blocks_drops_malformed_blocks():
    sentence = "سخن در این است؛"
    blocks = [
        "not a dict",
        {"type": "mystery", "text": "؟"},
        {"type": "paragraph"},
        {"type": "paragraph", "parts": "not a list"},
        {"type": "paragraph", "parts": ["not a dict"]},
        {"type": "paragraph", "parts": [{"text": "   "}, {"quote": sentence, "source": 0}]},
        {"type": "paragraph", "parts": [{"text": "متن"}, {"quote": sentence, "source": True}]},
        {"type": "paragraph", "parts": [{"text": "متن"}, {"quote": sentence, "source": 0.0}]},
        {"type": "paragraph", "parts": [{"text": "متن"}, {"quote": sentence, "source": "0"}]},
        {"type": "paragraph", "parts": [{"text": "متن"}, {"quote": sentence, "source": 7}]},
        {"type": "paragraph", "parts": [{"text": "متن"}, {"quote": 123, "source": 0}]},
        {"type": "paragraph", "parts": [{"text": "متن"}, {"quote": "   ", "source": 0}]},
        {"type": "heading"},
        {"type": "heading", "text": "   "},
    ]
    assert serve.guard_blocks(blocks, SOURCES) == []


def test_compose_returns_empty_list_when_the_composer_is_unreachable():
    def boom(request, timeout=None):
        raise OSError("composer down")

    original = serve.urlopen
    serve.urlopen = boom
    try:
        assert serve.compose_quoted_answer("پرسش؟", "پاسخ", SOURCES) == []
    finally:
        serve.urlopen = original


def test_compose_request_disables_reasoning():
    # glm-5.3-flash reasons by default on the coding endpoint; writing
    # fillers plus copy-matching quotes needs none, and reasoning cost
    # ~70s per call (measured 2026-09-10: 84.2s -> 16.5s with identical
    # kept sentences on the sentence-picking task).
    captured = {}

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    reply = json.dumps(
        {"choices": [{"message": {"content": json.dumps(
            {"blocks": [
                {"type": "heading", "text": "۱. طرح کلی"},
                {
                    "type": "paragraph",
                    "parts": [
                        {"text": "پیش از هر چیز باید معنای واژه را روشن کرد: "},
                        {"quote": "سخن در این است؛", "source": 0},
                    ],
                },
                {
                    "type": "paragraph",
                    "parts": [
                        {"text": "و در قطعه‌ای دیگر می‌خوانیم: "},
                        {"quote": OTHER_PASSAGE, "source": 1},
                    ],
                },
            ]}
        )}}]}
    )

    def fake_urlopen(request, timeout=None):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(reply.encode("utf-8"))

    original = serve.urlopen
    serve.urlopen = fake_urlopen
    os.environ["LLM_API_KEY"] = "test-key"
    try:
        kept = serve.compose_quoted_answer("پرسش؟", "پاسخ", SOURCES)
    finally:
        serve.urlopen = original
        del os.environ["LLM_API_KEY"]
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert kept == [
        {"type": "heading", "text": "۱. طرح کلی"},
        {
            "type": "paragraph",
            "parts": [
                {"text": "پیش از هر چیز باید معنای واژه را روشن کرد:"},
                {"quote": "سخن در این است؛", "source": 0, "pages_label": "صفحات 740 تا 745", "first_page_label": "صفحه 740"},
            ],
        },
        {
            "type": "paragraph",
            "parts": [
                {"text": "و در قطعه‌ای دیگر می‌خوانیم:"},
                {"quote": OTHER_PASSAGE, "source": 1, "pages_label": "", "first_page_label": ""},
            ],
        },
    ]


def test_quoted_prompt_carries_question_answer_and_locators():
    prompt = serve.build_quoted_prompt("پرسش؟", "پیش‌نویس پاسخ", SOURCES)
    assert "پرسش؟" in prompt
    assert "پیش‌نویس پاسخ" in prompt
    assert "chunk 101 of document tarhe-kolli (pages 740-745)" in prompt
    assert NOISY_PASSAGE in prompt
    # PM call 2026-09-10 (format): every paragraph is one unit — AI text
    # with embedded verbatim quotes; several passages per paragraph.
    assert "Every paragraph is one unit" in prompt
    assert "parts" in prompt
    # PM call 2026-09-10: the composer aims for at least five quote
    # paragraphs; the swap threshold below still needs only two.
    assert "at least five quote paragraphs" in prompt


def test_serve_pins_the_composer_endpoint_and_model():
    text = SERVE.read_text(encoding="utf-8")
    assert "/quoted-answer" in text
    assert "/citation-paragraph" not in text
    assert "glm-5.3-flash" in text
    assert "api.z.ai/api/coding/paas/v4" in text
    assert "LLM_API_KEY" in text
    # The model pin changes only by editing this file (smoke rule first),
    # never through an env override.
    assert 'environ.get("COMPOSER_MODEL"' not in text


def test_session_ui_renders_the_quoted_answer():
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "/quoted-answer" in html
    assert "renderQuotedAnswer" in html
    assert "evidenceSources" in html
    assert "paragraphNode" in html
    assert 'className = "doc-heading"' in html
    assert 'className = "doc-para"' in html
    assert 'className = "cite-sent"' in html
    assert 'className = "cite-pages"' in html


def test_session_ui_swaps_the_answer_only_when_blocks_arrive():
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "if (!blocks.length)" in html
    assert "answerEl.replaceChildren(doc)" in html


def test_session_ui_drops_a_stale_document_when_a_new_question_starts():
    # The composer call can outlive the answer it belongs to (minutes on the
    # Z.AI queue). A document for an older question must never overwrite a
    # newer answer or its citations.
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "let citationRun = 0" in html
    assert "citationRun += 1" in html
    assert "const run = citationRun" in html
    assert "run !== citationRun" in html


def test_session_ui_sentences_are_focusable_with_farsi_tooltip():
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "tabIndex = 0" in html
    assert "attr(data-ref)" in html
    assert "طرح کلی اندیشۀ اسلامی در قرآن" in html
    assert ".cite-sent:hover" in html
    assert ".cite-sent:focus-visible" in html


def test_session_ui_quotes_carry_a_resting_highlight():
    # PM call, 2026-09-10: an embedded quote must read as Book text at a
    # glance — a clay tint plus solid underline at rest, deepening on
    # hover/focus — not only the hover tooltip.
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "background: rgba(196, 92, 38, 0.12)" in html
    assert "border-bottom: 1px solid var(--clay)" in html


def test_session_ui_hides_the_citations_section_after_the_swap():
    # PM call, 2026-09-10: once the answer itself carries the citations,
    # the whole Evidence section — heading and list — goes away; a new
    # question brings it back for the fallback path.
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "استنادها در متن پاسخ‌اند" not in html
    assert "citationsHeadingEl.hidden = true" in html
    assert "citationsEl.hidden = true" in html
    assert "citationsHeadingEl.hidden = false" in html


def test_session_ui_keeps_the_llm_key_off_the_sheet():
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "LLM_API_KEY" not in html


def test_readme_records_the_quoted_answer_contract():
    text = README.read_text(encoding="utf-8")
    assert "Quoted answer" in text
    assert "/quoted-answer" in text
    assert "verbatim" in text
    assert "glm-5.3-flash" in text
    assert "ADR-0003" in text
    assert "at least five quote paragraphs" in text
