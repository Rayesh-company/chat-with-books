import json
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


def test_normalize_collapses_pdf_text_noise_to_a_word_stream():
    # Backspaces, kashida, ZWNJ, punctuation, and ellipsis disappear.
    assert serve.normalize_for_match(NOISY_PASSAGE).split() == [
        "سخن",
        "در",
        "این",
        "است",
        "اگرچه",
        "میگویند",
        "قرآن",
        "کتابی",
        "است",
        "برای",
        "زندگی",
        "جمعی",
        "انسانها",
        "و",
    ]
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
