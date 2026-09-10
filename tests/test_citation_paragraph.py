import json
import os
import sys

from tests.conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))

from ui import serve  # noqa: E402

SERVE = REPO_ROOT / "ui" / "serve.py"

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
        "reference": "chunk 29 of document tarhe-kolli (pages 214-223)",
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


def test_parse_composer_reply_accepts_plain_and_fenced_json():
    payload = {"sentences": [{"text": "سخن در این است؛", "source": 0}]}
    assert serve.parse_composer_reply(json.dumps(payload)) == payload["sentences"]
    fenced = f"```json\n{json.dumps(payload)}\n```"
    assert serve.parse_composer_reply(fenced) == payload["sentences"]


def test_parse_composer_reply_rejects_malformed_content():
    assert serve.parse_composer_reply("here are the sentences you asked for") == []
    assert serve.parse_composer_reply('{"sentences": "not a list"}') == []
    assert serve.parse_composer_reply("") == []


def test_compose_returns_empty_list_when_the_composer_is_unreachable():
    def boom(request, timeout=None):
        raise OSError("composer down")

    original = serve.urlopen
    serve.urlopen = boom
    try:
        assert serve.compose_citation_paragraph("پرسش؟", SOURCES) == []
    finally:
        serve.urlopen = original


def test_compose_request_disables_reasoning():
    # glm-5.3-flash reasons by default on the coding endpoint; the verbatim
    # copy task needs none, and reasoning cost ~70s per call (measured
    # 2026-09-10: 84.2s -> 16.5s with identical kept sentences).
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
            {"sentences": [{"text": "سخن در این است؛", "source": 0}]}
        )}}]}
    )

    def fake_urlopen(request, timeout=None):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(reply.encode("utf-8"))

    original = serve.urlopen
    serve.urlopen = fake_urlopen
    os.environ["LLM_API_KEY"] = "test-key"
    try:
        kept = serve.compose_citation_paragraph("پرسش؟", SOURCES)
    finally:
        serve.urlopen = original
        del os.environ["LLM_API_KEY"]
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert kept == [
        {"text": "سخن در این است؛", "reference": SOURCES[0]["reference"]}
    ]


def test_composer_prompt_carries_question_and_locators():
    prompt = serve.build_composer_prompt("پرسش؟", SOURCES)
    assert "پرسش؟" in prompt
    assert "chunk 101 of document tarhe-kolli (pages 740-745)" in prompt
    assert NOISY_PASSAGE in prompt


def test_serve_pins_the_composer_endpoint_and_model():
    text = SERVE.read_text(encoding="utf-8")
    assert "/citation-paragraph" in text
    assert "glm-5.3-flash" in text
    assert "api.z.ai/api/coding/paas/v4" in text
    assert "LLM_API_KEY" in text
    # The model pin changes only by editing this file (smoke rule first),
    # never through an env override.
    assert 'environ.get("COMPOSER_MODEL"' not in text


def test_session_ui_renders_the_hover_citation_paragraph():
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "/citation-paragraph" in html
    assert "renderCitationParagraph" in html
    assert "evidenceSources" in html
    assert 'className = "cite-para"' in html
    assert 'className = "cite-sent"' in html


def test_session_ui_drops_a_stale_paragraph_when_a_new_question_starts():
    # The composer call can outlive the answer it belongs to (minutes on the
    # Z.AI queue). A paragraph for an older question must never overwrite a
    # newer answer's citations.
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


def test_session_ui_keeps_the_llm_key_off_the_sheet():
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "LLM_API_KEY" not in html


def test_readme_records_the_citation_paragraph_contract():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "citation paragraph" in text
    assert "/citation-paragraph" in text
    assert "verbatim" in text
    assert "glm-5.3-flash" in text
