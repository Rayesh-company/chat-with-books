import json
import sys

from tests.conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))

from ui import composer, serve  # noqa: E402
from tests.helpers import run_call_with_replies  # noqa: E402

SERVE = REPO_ROOT / "ui" / "serve.py"
COMPOSER = REPO_ROOT / "ui" / "composer.py"
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


def test_stream_with_offsets_mirrors_normalize_for_match():
    # display_text locates a kept quote inside the RAW passage by walking
    # the letter stream with per-letter offsets; the mirror must agree
    # with the guard's own stream exactly, or the verbatim slice would
    # mis-cut. The noisy layer text and clean proper Farsi both hold.
    for text in (NOISY_PASSAGE, OTHER_PASSAGE, "انسان می‌تواند ـ باشـد؛"):
        stream, _ = serve._stream_with_offsets(text)
        assert stream == serve.normalize_for_match(text)


def test_display_text_repairs_a_noisy_copy():
    # The composer copied the passage's \b separators into the quote: the
    # guard passes (letter streams compare equal) and the display text
    # carries the separators as spaces — no control character may reach
    # the sheet fused inside a quoted sentence.
    noisy_copy = "قرآن\bکتابی\bاسـت\bبرای\bزندگی\bجمعی\bانسان‌ها"
    kept = serve.guard_sentences([{"text": noisy_copy, "source": 0}], SOURCES)
    assert kept and "\b" not in kept[0]["text"]
    assert kept[0]["text"] == "قرآن کتابی اسـت برای زندگی جمعی انسان‌ها"


def test_display_text_repairs_a_fused_copy():
    # Worse: the composer dropped the separators outright. The passage's
    # own slice — spacing and trailing punctuation included — is what
    # the reader gets; the model's fused stream is never shown.
    fused = "سخندرایناست"
    kept = serve.guard_sentences([{"text": fused, "source": 0}], SOURCES)
    assert kept and kept[0]["text"] == "سـخن در این اسـت؛"


def test_guard_blocks_repairs_a_noisy_quote_end_to_end():
    # The paragraph path repairs exactly like the sentence path: a quote
    # part copied with the layer's backspaces renders spaced, while the
    # proper-Farsi quotes and AI text around it are untouched.
    blocks = [
        {"type": "heading", "text": "۱. مفهوم‌شناسی"},
        {
            "type": "paragraph",
            "parts": [
                {"text": "پیش از هر چیز باید معنای واژه را روشن کرد: "},
                {"quote": "قرآن\bکتابی\bاسـت\bبرای\bزندگی\bجمعی\bانسان‌ها", "source": 0},
                {"text": " بر این اساس، ادامه می‌دهیم."},
            ],
        },
    ]
    kept = serve.guard_blocks(blocks, SOURCES)
    assert kept[1]["parts"][1]["quote"] == "قرآن کتابی اسـت برای زندگی جمعی انسان‌ها"
    assert kept[1]["parts"][0]["text"] == "پیش از هر چیز باید معنای واژه را روشن کرد:"


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


def test_book_label_resolves_the_dataset_name_to_its_farsi_title():
    # With two Books in the set, the tooltip's Book identity is resolved
    # from the Evidence locator's document name — never implied.
    assert (
        serve.book_label("chunk 101 of document tarhe-kolli (pages 740-745)")
        == "طرح کلی اندیشۀ اسلامی در قرآن"
    )
    assert serve.book_label("chunk 3 of document 70143-336 (pages 12-13)") == "انسان ۲۵۰ ساله"
    # An unmapped Book passes through raw; no document name yields ''.
    assert serve.book_label("chunk 3 of document some-new-book") == "some-new-book"
    assert serve.book_label("chunk 3") == ""


def test_parse_quoted_reply_accepts_plain_and_fenced_json():
    payload = {"blocks": [{"type": "paragraph", "parts": [{"text": "مقدمه"}]}]}
    assert serve.parse_quoted_reply(json.dumps(payload)) == payload["blocks"]
    fenced = f"```json\n{json.dumps(payload)}\n```"
    assert serve.parse_quoted_reply(fenced) == payload["blocks"]


def test_parse_quoted_reply_rejects_malformed_content():
    assert serve.parse_quoted_reply("here is the document you asked for") == []
    assert serve.parse_quoted_reply('{"blocks": "not a list"}') == []
    assert serve.parse_quoted_reply("") == []


def test_parse_quoted_reply_salvages_the_prefix_of_a_truncated_document():
    # A reply stopped by the output ceiling dies mid-JSON with no closing
    # braces; the complete block prefix must survive — a long document
    # loses only its tail (live run 2026-09-10: the Quoted answer ended
    # unfinished at its final section).
    blocks = [
        {"type": "heading", "text": "عنوان"},
        {"type": "paragraph", "parts": [{"text": "متن"}, {"quote": "سخن در این است؛", "source": 0}]},
    ]
    cut = json.dumps({"blocks": blocks}, ensure_ascii=False)[:-2] + ', {"type": "par'
    assert serve.parse_quoted_reply(cut) == blocks


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
                {"quote": "سخن در این است؛", "source": 0, "pages_label": "صفحات 740 تا 745", "first_page_label": "صفحه 740", "book_label": "طرح کلی اندیشۀ اسلامی در قرآن"},
                {"quote": OTHER_PASSAGE, "source": 1, "pages_label": "", "first_page_label": "", "book_label": "طرح کلی اندیشۀ اسلامی در قرآن"},
                {"text": "بر این اساس، ادامه می‌دهیم."},
            ],
        },
    ]


def test_guard_blocks_drops_a_paragraph_whose_only_quote_fails_the_guard():
    # With no surviving quote the paragraph would be pure AI text, which
    # the sheet never swaps in (PM call, 2026-09-10) — it drops whole.
    # A heading whose section kept no paragraph drops with it (the
    # empty-section fix, ADR-0010): the trailing «عنوان» here has no
    # body after it, so it never renders — and with only one surviving
    # paragraph and no heading left, the document falls below the swap
    # threshold and lands [].
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
    assert serve.guard_blocks(blocks, SOURCES) == []


def test_guard_blocks_prunes_a_dead_heading_but_keeps_a_fed_section():
    # The section-prune (ADR-0010): a heading with a surviving paragraph
    # after it stays; a heading whose paragraphs all dropped goes — and
    # the swap threshold reads the PRUNED document (two paragraphs, no
    # heading, still swaps).
    good = {
        "type": "paragraph",
        "parts": [
            {"text": "مقدمه‌ای کوتاه."},
            {"quote": "سخن در این است؛", "source": 0, "pages_label": "صفحات 740 تا 745", "first_page_label": "صفحه 740", "book_label": "طرح کلی اندیشۀ اسلامی در قرآن"},
        ],
    }
    other = {
        "type": "paragraph",
        "parts": [
            {"text": "گواه دوم:"},
            {"quote": OTHER_PASSAGE, "source": 1, "pages_label": "", "first_page_label": "", "book_label": "طرح کلی اندیشۀ اسلامی در قرآن"},
        ],
    }
    blocks = [
        {"type": "heading", "text": "بخش زنده"},
        good,
        other,
        {"type": "heading", "text": "بخش مرده"},
        {
            "type": "paragraph",
            "parts": [
                {"text": "بر پایهٔ این نگاه،"},
                {"quote": "بازگویی وارونهٔ بی‌منبع", "source": 0},
            ],
        },
    ]
    assert serve.guard_blocks(blocks, SOURCES) == [
        {"type": "heading", "text": "بخش زنده"},
        good,
        other,
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

    original = composer.urlopen
    composer.urlopen = boom
    try:
        assert serve.compose_quoted_answer("پرسش؟", "پاسخ", SOURCES) == ([], False)
    finally:
        composer.urlopen = original


# The writer's guarded reply, shared by the two-call tests below.
WRITER_BLOCKS = [
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
]
WRITER_KEPT = [
    {"type": "heading", "text": "۱. طرح کلی"},
    {
        "type": "paragraph",
        "parts": [
            {"text": "پیش از هر چیز باید معنای واژه را روشن کرد:"},
            {"quote": "سخن در این است؛", "source": 0, "pages_label": "صفحات 740 تا 745", "first_page_label": "صفحه 740", "book_label": "طرح کلی اندیشۀ اسلامی در قرآن"},
        ],
    },
    {
        "type": "paragraph",
        "parts": [
            {"text": "و در قطعه‌ای دیگر می‌خوانیم:"},
            {"quote": OTHER_PASSAGE, "source": 1, "pages_label": "", "first_page_label": "", "book_label": "طرح کلی اندیشۀ اسلامی در قرآن"},
        ],
    },
]
PLAN_MARKER = "طرح: بندها و بافت‌دهی میان قطعه‌ها"


def writer_reply():
    content = json.dumps({"blocks": WRITER_BLOCKS}, ensure_ascii=False)
    return json.dumps({"choices": [{"message": {"content": content}}]})


def planner_reply(plan):
    return json.dumps({"choices": [{"message": {"content": plan}}]})


def run_compose_with_replies(replies):
    """Run compose_quoted_answer against per-call canned replies; return
    (kept blocks, truncated flag, captured payloads, captured timeouts)."""
    (kept, truncated), captured = run_call_with_replies(
        lambda: serve.compose_quoted_answer("پرسش؟", "پیش‌نویس پاسخ", SOURCES),
        replies,
    )
    return kept, truncated, captured


def test_compose_plans_with_reasoning_then_writes_without_it():
    # Two sequential composer calls (PM call, 2026-09-10): the planner
    # reasons — thinking enabled — over structure and cross-passage
    # weaving, and the writer copies verbatim with thinking disabled at
    # the endpoint's default temperature; no call pins a temperature.
    kept, truncated, captured = run_compose_with_replies(
        [planner_reply(PLAN_MARKER), writer_reply()]
    )
    assert [p["model"] for p in captured["payloads"]] == [
        "glm-5.3-flash",
        "glm-5.3-flash",
    ]
    assert captured["payloads"][0]["thinking"] == {"type": "enabled"}
    assert captured["payloads"][1]["thinking"] == {"type": "disabled"}
    assert "temperature" not in captured["payloads"][0]
    assert "temperature" not in captured["payloads"][1]
    assert captured["timeouts"] == [serve.COMPOSER_TIMEOUT, serve.COMPOSER_TIMEOUT]
    # An explicit output ceiling rides on both payloads (PM call,
    # 2026-09-10): left unpinned, a long document ran out of room
    # mid-write — and reasoning tokens count inside the ceiling (measured:
    # 128 of 173 completion tokens on a trivial reply, thinking
    # disabled), so the reasoning planner carries the same headroom.
    assert [p["max_tokens"] for p in captured["payloads"]] == [
        serve.COMPOSER_MAX_TOKENS,
        serve.COMPOSER_MAX_TOKENS,
    ]
    assert serve.COMPOSER_MAX_TOKENS >= 8192
    # The plan rides into the writer's prompt as its framing context.
    assert PLAN_MARKER in captured["payloads"][1]["messages"][0]["content"]
    assert truncated is False
    assert kept == WRITER_KEPT


def test_compose_falls_back_when_the_planner_call_fails():
    # A planner failure must never empty the sheet (AC-4): the writer
    # still runs, on the no-plan prompt with the draft answer back in.
    kept, _, captured = run_compose_with_replies(
        [OSError("planner down"), writer_reply()]
    )
    # The planner was attempted (payload 0, thinking enabled) and the
    # writer still ran (payload 1, thinking disabled).
    assert len(captured["payloads"]) == 2
    assert captured["payloads"][0]["thinking"] == {"type": "enabled"}
    assert captured["payloads"][1]["thinking"] == {"type": "disabled"}
    content = captured["payloads"][1]["messages"][0]["content"]
    assert "پیش‌نویس پاسخ" in content
    assert PLAN_MARKER not in content
    assert kept == WRITER_KEPT


def test_compose_falls_back_when_the_planner_reply_is_empty():
    # Empty or non-string planner content is planner failure, not a
    # plan — the writer gets the no-plan prompt.
    for empty in ("", None):
        kept, _, captured = run_compose_with_replies(
            [planner_reply(empty), writer_reply()]
        )
        assert len(captured["payloads"]) == 2
        content = captured["payloads"][1]["messages"][0]["content"]
        assert "پیش‌نویس پاسخ" in content
        assert PLAN_MARKER not in content
        assert kept == WRITER_KEPT


# A writer reply the output ceiling cut: finish_reason "length", the JSON
# dead mid-array — no closing braces, exactly the live 2026-09-10 shape.
def truncated_writer_reply():
    prefix = json.dumps({"blocks": WRITER_BLOCKS[:2]}, ensure_ascii=False)
    content = prefix[:-2] + ", "
    return json.dumps(
        {"choices": [{"message": {"content": content}, "finish_reason": "length"}]}
    )


def continuation_reply(finish_reason="stop"):
    content = json.dumps({"blocks": WRITER_BLOCKS[2:]}, ensure_ascii=False)
    return json.dumps(
        {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}
    )


def test_compose_continues_once_past_a_length_cut():
    # A reply stopped by the output ceiling used to lose the whole
    # document (the JSON never parsed); now the complete prefix is
    # salvaged and ONE continuation call — thinking disabled, same
    # ceiling — writes only the remaining blocks.
    kept, truncated, captured = run_compose_with_replies(
        [planner_reply(PLAN_MARKER), truncated_writer_reply(), continuation_reply()]
    )
    assert len(captured["payloads"]) == 3
    assert captured["timeouts"] == [
        serve.COMPOSER_TIMEOUT,
        serve.COMPOSER_TIMEOUT,
        serve.COMPOSER_TIMEOUT,
    ]
    assert captured["payloads"][2]["thinking"] == {"type": "disabled"}
    assert captured["payloads"][2]["max_tokens"] == serve.COMPOSER_MAX_TOKENS
    # The continuation reads the same framing (the plan) and the blocks
    # that survived the cut.
    continuation_brief = captured["payloads"][2]["messages"][0]["content"]
    assert PLAN_MARKER in continuation_brief
    assert "سخن در این است؛" in continuation_brief
    assert "AFTER the last block" in continuation_brief
    assert "copied VERBATIM" in continuation_brief
    assert truncated is False
    assert kept == WRITER_KEPT


def test_compose_flags_truncation_when_the_continuation_is_cut_too():
    # One continuation only — bounded latency. If it comes back cut as
    # well, the guarded prefix still lands and the sheet is told the
    # document ended at the ceiling.
    kept, truncated, captured = run_compose_with_replies(
        [
            planner_reply(PLAN_MARKER),
            truncated_writer_reply(),
            continuation_reply("length"),
        ]
    )
    assert len(captured["payloads"]) == 3
    assert truncated is True
    assert kept == WRITER_KEPT


def test_compose_keeps_the_salvaged_prefix_when_the_continuation_fails():
    # A continuation failure must never empty the sheet: the salvaged
    # prefix rides on, flagged as cut.
    kept, truncated, captured = run_compose_with_replies(
        [
            planner_reply(PLAN_MARKER),
            truncated_writer_reply(),
            OSError("continuation down"),
        ]
    )
    assert len(captured["payloads"]) == 3
    assert truncated is True
    assert kept == WRITER_KEPT[:2]


def junk_writer_reply():
    return json.dumps({"choices": [{"message": {"content": "not json at all"}}]})


def test_compose_retries_the_writer_once_when_the_first_keeps_nothing():
    # The intermittent «پاسخ استنادی آماده نشد» (ADR-0010): a junk or
    # paraphrasing writer attempt used to land [] with no recourse —
    # the guarded result now earns ONE full writer retry (same plan,
    # no planner re-run), and the retry's document lands.
    kept, truncated, captured = run_compose_with_replies(
        [planner_reply(PLAN_MARKER), junk_writer_reply(), writer_reply()]
    )
    assert len(captured["payloads"]) == 3
    # The planner ran once; both writer attempts are thinking-disabled
    # and the retry still carries the plan in its framing.
    assert captured["payloads"][0]["thinking"] == {"type": "enabled"}
    assert [p["thinking"] for p in captured["payloads"][1:]] == [
        {"type": "disabled"},
        {"type": "disabled"},
    ]
    assert PLAN_MARKER in captured["payloads"][2]["messages"][0]["content"]
    assert truncated is False
    assert kept == WRITER_KEPT


def test_compose_retries_once_and_then_lands_the_honest_empty():
    # Both writer attempts useless: [] after exactly two attempts —
    # never a loop.
    kept, truncated, captured = run_compose_with_replies(
        [planner_reply(PLAN_MARKER), junk_writer_reply(), junk_writer_reply()]
    )
    assert len(captured["payloads"]) == 3
    assert kept == []
    assert truncated is False


def test_compose_retries_after_a_failed_writer_call():
    # A writer TIMEOUT (OSError) is the same repair: one retry.
    kept, _, captured = run_compose_with_replies(
        [planner_reply(PLAN_MARKER), OSError("writer timed out"), writer_reply()]
    )
    assert len(captured["payloads"]) == 3
    assert kept == WRITER_KEPT


def test_continuation_prompt_carries_the_salvaged_blocks_and_the_rules():
    prompt = serve.build_continuation_prompt(
        "پرسش؟", "پیش‌نویس پاسخ", SOURCES, PLAN_MARKER, WRITER_BLOCKS[:1]
    )
    assert "پرسش؟" in prompt
    assert PLAN_MARKER in prompt
    assert "chunk 101 of document tarhe-kolli (pages 740-745)" in prompt
    assert json.dumps({"blocks": WRITER_BLOCKS[:1]}, ensure_ascii=False) in prompt
    assert "copied VERBATIM" in prompt
    assert "Never repeat a block" in prompt
    assert '"blocks"' in prompt


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
    # PM call 2026-09-10 (raised from five the same night): the writer
    # aims for at least eight quote paragraphs; the swap threshold below
    # still needs only two.
    assert "at least eight quote paragraphs" in prompt


def test_planner_prompt_keeps_a_short_question_passages_brief():
    # Live phase 2 measured ~295s against the ~100s estimate (PM call,
    # 2026-09-10): reasoning time scales with what the planner reads,
    # so the draft answer is held back — the planner plans from the
    # question and passages alone — and the plan itself is capped short.
    prompt = serve.build_planner_prompt("پرسش؟", SOURCES)
    assert "پرسش؟" in prompt
    assert "پیش‌نویس پاسخ" not in prompt
    assert "chunk 101 of document tarhe-kolli (pages 740-745)" in prompt
    assert NOISY_PASSAGE in prompt
    assert "plain-text plan" in prompt
    assert "section headings" in prompt
    assert "more than one passage" in prompt
    assert "not JSON" in prompt
    assert "at least eight quote paragraphs" in prompt
    assert "under 150 words" in prompt


def test_quoted_prompt_slots_the_plan_in_place_of_the_draft_answer():
    # With a plan, the writer's framing context is the plan — not the
    # draft answer — while every verbatim rule stays byte-identical.
    planned = serve.build_quoted_prompt(
        "پرسش؟", "پیش‌نویس پاسخ", SOURCES, "طرح آزمایشی: بند یک از قطعهٔ ۱"
    )
    assert "طرح آزمایشی: بند یک از قطعهٔ ۱" in planned
    assert "پیش‌نویس پاسخ" not in planned
    assert "copied VERBATIM" in planned
    assert "Do not paraphrase" in planned
    assert "at least eight quote paragraphs" in planned
    assert '"blocks"' in planned


def test_serve_pins_the_composer_endpoint_and_model():
    text = SERVE.read_text(encoding="utf-8")
    assert "/quoted-answer" in text
    assert "/citation-paragraph" not in text
    # The endpoint pins live in the composer module now — still pinned in
    # source (smoke rule first), never through an env override.
    composer_text = COMPOSER.read_text(encoding="utf-8")
    assert "glm-5.3-flash" in composer_text
    assert "api.z.ai/api/coding/paas/v4" in composer_text
    assert "LLM_API_KEY" in composer_text
    assert 'environ.get("COMPOSER_MODEL"' not in composer_text


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
    # The guarded document lands in phase 2's own section only when
    # non-empty blocks arrive; phase 1's section is never touched.
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "if (!blocks.length)" in html
    assert "quotedDocEl.replaceChildren(doc)" in html


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
    assert "انسان ۲۵۰ ساله" in html
    # Book identity arrives per quote (resolved server-side from the
    # Evidence locator's document name) — with two Books it is never implied.
    assert "book_label" in html
    assert ".cite-sent:hover" in html
    assert ".cite-sent:focus-visible" in html


def test_session_ui_quotes_carry_a_resting_highlight():
    # PM call, 2026-09-10: an embedded quote must read as Book text at a
    # glance — a clay tint plus solid underline at rest, deepening on
    # hover/focus — not only the hover tooltip. The tint reads the
    # --clay-soft token (ADR-0014) so it survives both themes.
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "background: var(--clay-soft)" in html
    assert "border-bottom: 1px solid var(--clay)" in html


def test_session_ui_keeps_citations_in_the_first_phase_section():
    # One place per phase in the ask's article (ADR-0014's thread shape,
    # superseding the tabbed brief): the Evidence list stays in phase 1's
    # own section — the swap no longer needs to hide it, because the
    # article's sections separate the phases. A new question resets the
    # section as before.
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'className = "citations-heading"' in html
    assert 'className = "citations"' in html
    assert 'className = "evidence-pool"' in html
    assert 'className = "quoted-doc"' in html
    # The Evidence markup builds before the Quoted answer's section.
    assert html.index('className = "citations"') < html.index(
        'className = "quoted-doc"'
    )
    assert "citationsEl.hidden" not in html


def test_session_ui_renders_the_streamed_answer_as_markdown():
    # Phase 1 (the streamed answer) arrives as markdown — headings, bold,
    # bullets — and the sheet renders that structure during the live
    # preview and at final, never the literal #/** characters (PM call,
    # 2026-09-10).
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "function renderMarkdown" in html
    assert "function markBold" in html


def test_session_ui_shows_the_composer_phase_and_times_the_whole_pipeline():
    # PM call, 2026-09-10: a pulsing status line marks phase 2 while it
    # runs. PM brief, 2026-09-11: each phase gets its own switchable
    # section, phases 2 and 3 get a timer that freezes at their completion
    # time, and a line names the phase being generated. Issue #25: phase 2
    # is the pipeline's end — the whole-pipeline elapsed clock stops when
    # it settles; the dive is operator-timed by its own chip.
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "در حال نوشتن پاسخ استنادی" in html
    assert 'className = "composer-status"' in html
    assert "composer-pulse" in html
    assert "const stopTimer" in html
    assert "clearInterval(tick)" in html
    # Phase 2 settles and the pipeline clock stops — no phase-3 auto-start.
    assert "renderQuotedAnswer(query, answer, citations, stopTimer)" in html
    # Per-phase machinery: the running-phase line, and the timer on the
    # phase marks (the tabs' replacement, ADR-0014).
    assert 'className = "phase-now"' in html
    assert "در حال تولید:" in html
    assert 'className = "phase-timer"' in html
    assert "startPhaseTimer(2)" in html
    assert "startPhaseTimer(3)" in html
    assert "stopPhaseTimer(2)" in html
    assert "stopPhaseTimer(3)" in html


def test_session_ui_runs_research_mode_as_the_phase_3_chat():
    # Phase 3 is Research Mode (ADR 0008): no ask auto-starts it — the
    # operator's message does, starting the phase timer on send and
    # POSTing /research/message with only the text (plus the ask and its
    # pool on the creating call; every search payload stays pinned
    # server-side). The recorded COT relay stays reachable server-side as
    # the operator probe; the sheet never calls it.
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "/research/message" in html
    assert "/research/turn" in html
    assert "/research/decide" in html
    assert "/next-tier-recall" not in html
    assert "/deep-dive" not in html
    assert 'id="research-form"' in html
    assert 'id="research-input"' in html
    assert "حالت پژوهش" in html  # the tab's own name
    assert "مطالعۀ عمیق" not in html  # the superseded dive name is gone
    assert "startPhaseTimer(3)" in html
    assert "AbortController" in html  # a new ask aborts in-flight phases
    # Friendly Farsi failure in the panel; the replies render like phase
    # 2's document, plus the closing server-built references list.
    assert "پیام پژوهش فرستاده نشد" in html
    assert 'block.type === "references"' in html
    assert "منابع" in html
    # The checkpoint chips resolve through the decide endpoint; the
    # session identity survives a refresh via sessionStorage.
    assert "می‌پذیرم" in html
    assert "رد می‌کنم" in html
    assert "sessionResearch" in html


def test_session_ui_keeps_the_llm_key_off_the_sheet():
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "LLM_API_KEY" not in html


def test_the_sheet_carries_the_round_two_contract():
    # ADR-0010: the widen chip, the citation-landing machinery, and the
    # Book scoping all live on the sheet. The round-two toggles are
    # retired by the chat shell (ADR-0014) — the Book scoping rides the
    # Session's pick into the ask's payloads instead.
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "/recall-more" in html
    assert "جست‌وجوی بیشتر" in html
    assert "book-card" in html
    assert "selectedDatasets" in html
    assert "locatorPassages" in html


def test_the_sheet_carries_the_round_three_contract():
    # ADR-0011: the single-pick Book gate (persisted, the ask bound to
    # one Book — now picked from the empty state's cards at Session
    # creation, ADR-0014), the phase-1 evidence fallback, and the
    # transcript re-fetch.
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'localStorage.setItem("selectedBook"' in html
    assert "برای نشست تازه، یک کتاب برگزینید" in html
    assert "/evidence-fallback" in html
    assert "استنادی از کتاب‌ها پیدا نشد" in html
    assert "/research/messages" in html


def test_the_round_two_adr_records_the_decisions():
    text = (REPO_ROOT / "docs" / "adr" / "0010-round-two-reliability-widen-selection.md").read_text(
        encoding="utf-8"
    )
    assert "One writer repair" in text
    assert "prune_empty_sections" in text
    assert "/recall-more" in text
    assert "HYBRID_COMPLETION" in text
    assert "BOOK_DATASETS" in text
    assert "Considered options" in text
    assert "Consequences" in text


def test_the_round_three_adr_records_the_decisions():
    text = (REPO_ROOT / "docs" / "adr" / "0011-work-over-chart-editing.md").read_text(
        encoding="utf-8"
    )
    assert "Chart mode vs work mode" in text
    assert "PROPOSAL_COOLDOWN_TURNS" in text
    assert "/evidence-fallback" in text
    assert "PAGE_RESOLVER" in text
    assert "localStorage.selectedBook" in text
    assert "/research/messages" in text
    assert "Considered options" in text
    assert "Consequences" in text

    # The engine carries the recorded constants and the sheet the
    # recorded surfaces.
    research_text = (REPO_ROOT / "ui" / "research.py").read_text(encoding="utf-8")
    assert "PROPOSAL_COOLDOWN_TURNS = 2" in research_text
    assert 'may_propose = classified.get("intent") == "research_exploration"' in research_text
    resolver_text = (REPO_ROOT / "ui" / "page_resolver.py").read_text(encoding="utf-8")
    assert "resolve_first_page" in resolver_text


def test_readme_records_the_quoted_answer_contract():
    text = README.read_text(encoding="utf-8")
    assert "Quoted answer" in text
    assert "/quoted-answer" in text
    assert "verbatim" in text
    assert "glm-5.3-flash" in text
    assert "ADR-0003" in text
    assert "at least eight quote paragraphs" in text
    # The two-call composer contract (PM call, 2026-09-10): reasoning
    # plans, non-reasoning writes.
    assert "two sequential" in text
    assert "thinking enabled" in text
    assert "thinking disabled" in text
    assert "output ceiling" in text
    # Phase 1 is the Quote selection (issue #28); the stale "streamed
    # answer plus its Evidence list" description is retired — CONTEXT.md
    # retires the phrase (full-spec review, 2026-09-12).
    assert "streamed answer plus its Evidence list" not in text
    assert "the Quote selection — ten verbatim sentences" in text
    # The threaded sheet never swaps (ADR-0014): the guarded document
    # lands in phase 2's own section or not at all — phase 1's answer
    # stays.
    assert "the sheet can swap it" not in text
    assert "the streamed answer stays put" not in text
    assert "the streamed answer and the Evidence citations stay" not in text
    assert "lands in phase 2's section" in text
