"""The verbatim guard and its parsing: pure text-in/text-out — the
normalization tables, the letter-stream sentence/block guards, the
Citation labels, and the model-reply JSON parse conventions shared by
every composer conversation. No I/O."""

from __future__ import annotations

import json
import re

_ARABIC_TO_FARSI = str.maketrans({"ي": "ی", "ك": "ک"})
# Tashkeel, superscript alef, tatweel/kashida.
_STRIPPED_MARKS = re.compile(r"[ً-ٰٟـ]")
# Every separator — the text layer's backspaces, ZWNJ/ZWJ, spaces,
# punctuation, the truncating ellipsis — is deleted, not spaced: the PDF
# splits words with \b (می) where the composer writes می‌تواند or
# می تواند, so only the letter stream compares equal across all three.
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)

# Evidence locators end in the Book text layer's page range, e.g.
# "chunk 101 of document tarhe-kolli (pages 740-745)" (enable_farsi_evidence.py).
_PAGES_IN_REFERENCE = re.compile(r"\(pages (\d+)-(\d+)\)")
_PAGE_IN_REFERENCE = re.compile(r"\(page (\d+)\)")
_DOCUMENT_IN_REFERENCE = re.compile(r"\bdocument ([A-Za-z0-9._-]+)")

# The Book set's dataset names resolve to their Farsi titles for a quote's
# Book identity (Citation = Book identity plus pages). An unmapped name
# passes through raw so a Book is still attributable; a locator without a
# document name yields "" and the sheet shows a generic label rather than
# guessing a Book.
_BOOK_TITLES = {
    "tarhe-kolli": "طرح کلی اندیشۀ اسلامی در قرآن",
    "70143-336": "انسان ۲۵۰ ساله",
}


def normalize_for_match(text: str) -> str:
    """Reduce Farsi text to a comparable letter stream (AC-3 normalized comparison)."""
    text = text.translate(_ARABIC_TO_FARSI)
    text = _STRIPPED_MARKS.sub("", text)
    return _NON_WORD.sub("", text).lower()


# The Book text layer's backspace word separators (\b, recorded live
# 2026-09-09) render as nothing in the browser, fusing the words. Any
# non-whitespace C0 control gets the space the layer meant; \t\n\r ride
# the whitespace collapse instead. ZWNJ is not whitespace, so it
# survives — the composer's می‌تواند keeps its join.
_CONTROL_MARKS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _display_clean(text: str) -> str:
    """Make text layer noise readable: controls become spaces, every
    whitespace run becomes one space."""
    return " ".join(_CONTROL_MARKS.sub(" ", text).split())


def _stream_with_offsets(text: str) -> tuple:
    """normalize_for_match's letter stream over the RAW text, with each
    kept letter's raw index beside it — the bridge from a matched stream
    position back to the passage's own characters (display_text's
    verbatim slice)."""
    stream = []
    offsets = []
    for index, char in enumerate(text):
        char = _ARABIC_TO_FARSI.get(ord(char), char)
        if _STRIPPED_MARKS.match(char):
            continue
        if _NON_WORD.fullmatch(char):
            continue
        lowered = char.lower()
        # A lowering that changes length cannot ride a 1:1 offset map;
        # Farsi is caseless, so keeping the char changes nothing real.
        if len(lowered) != 1:
            lowered = char
        stream.append(lowered)
        offsets.append(index)
    return "".join(stream), offsets


def _honors_word_gaps(model: str, source: str) -> bool:
    """Whether the model's text puts a separator wherever the passage
    slice has one. Both already display-cleaned, letter streams equal
    (the guard proved containment); a gap holding only ZWNJ is still a
    join the layer cannot distinguish from a word space, so any
    separator in the model's gap — space or ZWNJ — honors it. Tashkeel
    and kashida are intra-word decoration, never gaps."""
    def gaps(text):
        runs = []
        gap = ""
        for char in text:
            if _STRIPPED_MARKS.match(char):
                continue
            if _NON_WORD.fullmatch(char):
                gap += char
            else:
                runs.append(gap)
                gap = ""
        return runs

    model_gaps, source_gaps = gaps(model), gaps(source)
    if len(model_gaps) != len(source_gaps):
        return False
    return all(
        not source_gap or model_gap
        for source_gap, model_gap in zip(source_gaps, model_gaps)
    )


def display_text(text: str, passage: str) -> str:
    """The text to SHOW for a guard-kept quote: the model's own writing
    when it honors every word gap of the passage, else the passage's
    verbatim slice — both display-cleaned. The composer is told to write
    proper Farsi over the \b noise and usually does; when it copies the
    noise (or deletes the separators outright) the guard still passes —
    letter streams compare equal — so the passage itself is the spacing
    the reader gets."""
    cleaned = _display_clean(text)
    if not cleaned or not isinstance(passage, str) or not passage:
        return cleaned or text.strip()
    stream, offsets = _stream_with_offsets(passage)
    needle = normalize_for_match(text)
    at = stream.find(needle)
    if at < 0 or at + len(needle) > len(offsets):
        return cleaned
    end = offsets[at + len(needle) - 1] + 1
    # The model may also have dropped the sentence's trailing
    # punctuation; the slice takes the passage's own, stopping at the
    # next word's first letter.
    while end < len(passage) and _NON_WORD.fullmatch(passage[end]):
        end += 1
    source = _display_clean(passage[offsets[at] : end])
    return cleaned if _honors_word_gaps(cleaned, source) else source


def guard_sentences(selections, sources):
    """Keep only sentences that occur verbatim in their claimed source passage.

    A sentence failing the check is dropped, never shown as quoted; a sentence
    claiming the wrong passage is dropped too, or its tooltip would cite a
    passage it did not come from.
    """
    normalized = [normalize_for_match(source["passage"]) for source in sources]
    kept = []
    for item in selections:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        index = item.get("source")
        if not isinstance(text, str) or not text.strip():
            continue
        if not isinstance(index, int) or isinstance(index, bool):
            continue
        if not 0 <= index < len(sources):
            continue
        needle = normalize_for_match(text)
        if needle and needle in normalized[index]:
            kept.append(
                {
                    "text": display_text(text, sources[index]["passage"]),
                    "reference": sources[index]["reference"],
                }
            )
    return kept


def guard_blocks(blocks, sources, commentary=False):
    """Turn composer blocks into renderable Quoted answer blocks.

    Every paragraph is one unit — AI text with embedded verbatim quotes,
    several passages allowed (PM call, 2026-09-10). A quote part drops
    alone under the verbatim guard; a paragraph left with no surviving
    quote (it would be pure AI text) or no AI text (bare quotes) drops
    whole, and so does any malformed block or claim on a missing passage.
    Each kept quote part carries the range and first-page labels of
    exactly the passage it claims. The document itself drops to [] below
    the swap threshold —
    at least two quoting paragraphs, or one plus a heading — so the sheet
    swaps the streamed answer only for a real Quoted answer (ADR-0003).

    With `commentary` on (the Host's side answers, ADR-0012 T5) the
    guard gains a visibly distinct channel and nothing else changes: a
    paragraph whose quotes all die keeps its own AI text as a
    `commentary` block — reasoning the Books cannot support, rendered as
    the inference it is, never as a claim — and an explicit model
    `commentary` block is normalized to text alone (a commentary block
    carries no quote, by definition). The threshold still counts
    quoting paragraphs only: commentary never props up a Quoted answer.
    """
    kept = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "references":
            # The dive's closing references list is built server-side and
            # rides in guarded output untouched — no passage claims to
            # guard, and the sheet renders it as the «منابع» list. An
            # empty one drops: a bare «منابع» heading is not a list.
            if isinstance(block.get("items"), list) and block["items"]:
                kept.append(block)
        elif kind == "heading":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                kept.append({"type": "heading", "text": _display_clean(text)})
        elif kind == "commentary":
            if commentary:
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    kept.append({"type": "commentary", "text": _display_clean(text)})
        elif kind == "paragraph":
            parts = block.get("parts")
            if not isinstance(parts, list):
                continue
            kept_parts = []
            has_text = False
            has_quote = False
            for part in parts:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    kept_parts.append({"text": _display_clean(text)})
                    has_text = True
                    continue
                quote = part.get("quote")
                index = part.get("source")
                if not isinstance(quote, str) or not quote.strip():
                    continue
                if not isinstance(index, int) or isinstance(index, bool):
                    continue
                if not 0 <= index < len(sources):
                    continue
                kept_sentence = guard_sentences(
                    [{"text": quote, "source": index}], sources
                )
                if not kept_sentence:
                    continue
                kept_parts.append(
                    {
                        "quote": display_text(quote, sources[index]["passage"]),
                        "source": index,
                        **_citation_labels(
                            sources[index]["reference"], sources[index]["passage"]
                        ),
                    }
                )
                has_quote = True
            if has_quote and has_text:
                kept.append({"type": "paragraph", "parts": kept_parts})
            elif commentary and has_text:
                # The Host's reasoning channel: the paragraph's quotes
                # all died under the guard, so its own text lands as the
                # commentary it is — visible, distinct, never a claim.
                kept.append(
                    {
                        "type": "commentary",
                        "text": " ".join(
                            part["text"] for part in kept_parts if "text" in part
                        ),
                    }
                )
    kept = prune_empty_sections(kept)
    paragraphs = sum(1 for block in kept if block["type"] == "paragraph")
    headings = sum(1 for block in kept if block["type"] == "heading")
    return kept if paragraphs and (paragraphs >= 2 or headings) else []


def prune_empty_sections(blocks: list) -> list:
    """Drop a heading whose section kept no paragraph. The guard keeps
    headings unconditionally but paragraphs only with a surviving quote
    — a section whose quotes all failed would render as a bare heading
    (the recorded live complaint: empty sections in the Quoted answer).
    A heading survives only when a kept paragraph follows it before the
    next heading or the end. Pure code, order-preserving; the document
    tail (references) is not a heading, so it rides untouched."""
    kept = []
    pending_headings = []
    for block in blocks:
        if block.get("type") == "heading":
            pending_headings.append(block)
            continue
        kept.extend(pending_headings)
        pending_headings = []
        kept.append(block)
    return kept


# The true-page resolver seam (ADR-0011): None by default — labels come
# from the locator's estimate. serve.py installs
# ui.page_resolver.resolve_first_page at startup so every kept quote's
# labels name the passage's ACTUAL page (the recorded drift: every
# sampled passage sat at label-1). A resolver that returns None leaves
# the estimate — never an invented page.
PAGE_RESOLVER = None


def _resolved_first_page(reference: str, passage: str) -> int:
    """The passage's actual first page via the installed resolver; 0
    when none installed, none found, or the shape is unusable."""
    if PAGE_RESOLVER is None or not passage:
        return 0
    try:
        page = PAGE_RESOLVER(reference, passage)
    except Exception:
        # A labeling lookup must never fail a reply: the estimate
        # stands.
        return 0
    return page if isinstance(page, int) and page > 0 else 0


def pages_label(reference: str, passage: str = "") -> str:
    """Farsi chunk-range label for an Evidence locator; '' with no pages.

    The paragraph-end Citation carries the whole range — "تا" between the
    numbers, not a dash: digits are LTR-weak in Farsi text. With a
    resolver installed, the range's FIRST page is the passage's actual
    page (the locator's estimate rides on when resolution fails). A
    passage without page markers cites the Book alone on the sheet — never an
    invented page.
    """
    pages = _PAGES_IN_REFERENCE.search(reference)
    single = _PAGE_IN_REFERENCE.search(reference)
    resolved = _resolved_first_page(reference, passage)
    if pages:
        first, last = int(pages.group(1)), int(pages.group(2))
        if resolved:
            return (
                f"صفحات {resolved} تا {last}" if resolved < last else f"صفحه {resolved}"
            )
        return f"صفحات {first} تا {last}"
    if single:
        return f"صفحه {resolved or int(single.group(1))}"
    return f"صفحه {resolved}" if resolved else ""


def first_page_label(reference: str, passage: str = "") -> str:
    """Farsi label for the page a passage STARTS on; '' with no pages.

    The per-sentence tooltip stays on this first page (PM call,
    2026-09-10) while the paragraph end carries the full range — the
    true page when the resolver knows it, the estimate otherwise.
    """
    resolved = _resolved_first_page(reference, passage)
    if resolved:
        return f"صفحه {resolved}"
    pages = _PAGES_IN_REFERENCE.search(reference)
    if pages:
        return f"صفحه {pages.group(1)}"
    page = _PAGE_IN_REFERENCE.search(reference)
    if page:
        return f"صفحه {page.group(1)}"
    return ""


def book_label(reference: str) -> str:
    """Farsi Book title for an Evidence locator; '' when it names no Book.

    With two Books in the set, a quote's attribution can no longer be
    implied — the tooltip names the Book the passage actually came from.
    """
    document = _DOCUMENT_IN_REFERENCE.search(reference)
    if not document:
        return ""
    return _BOOK_TITLES.get(document.group(1), document.group(1))


def _citation_labels(reference: str, passage: str = "") -> dict:
    """The three Farsi labels a kept quote carries for exactly the
    passage it claims — the chunk range, first page, and Book. The
    first page and the range's opening are the passage's TRUE page when
    the resolver is installed (ADR-0011)."""
    return {
        "pages_label": pages_label(reference, passage),
        "first_page_label": first_page_label(reference, passage),
        "book_label": book_label(reference),
    }


def salvage_blocks(content: str) -> list:
    """Decode the complete prefix of a blocks document cut mid-JSON.

    A reply stopped at the output ceiling dies without its closing
    braces, so json.loads loses the whole document (live run 2026-09-10:
    the Quoted answer ended unfinished at its final section). raw_decode
    walks the blocks array and keeps every block that parsed; the verbatim
    guard still applies to the salvage downstream, exactly as to a whole
    reply.
    """
    match = re.search(r'"blocks"\s*:\s*\[', content)
    if not match:
        return []
    decoder = json.JSONDecoder()
    pos = match.end()
    blocks = []
    while pos < len(content):
        while pos < len(content) and content[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(content) or content[pos] in "]}":
            break
        try:
            item, pos = decoder.raw_decode(content, pos)
        except ValueError:
            break
        blocks.append(item)
    return blocks


def _strip_code_fence(content: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())


def _json_object(content):
    """A model reply's JSON object, or None: code fence stripped,
    json.loads with one brace-scoped retry for prose-wrapped JSON — the
    parse conventions parse_quoted_reply and parse_picker_reply share;
    the key check stays each caller's own."""
    if not isinstance(content, str) or not content.strip():
        return None
    stripped = _strip_code_fence(content)
    try:
        parsed = json.loads(stripped)
    except ValueError:
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except ValueError:
            return None
    return parsed if isinstance(parsed, dict) else None


def parse_quoted_reply(content):
    """Pull the blocks list out of the composer's reply; [] when malformed.

    A reply cut by the output ceiling dies mid-JSON without closing
    braces; its complete block prefix is salvaged so a long document
    loses only its tail — compose_quoted_answer reads the cut off
    finish_reason and continues the write past the salvage. The fence
    strip and the brace-scoped retry are _json_object's, shared with
    the picker's parse.
    """
    if not isinstance(content, str) or not content.strip():
        return []
    parsed = _json_object(content)
    if isinstance(parsed, dict) and isinstance(parsed.get("blocks"), list):
        return parsed["blocks"]
    salvaged = salvage_blocks(_strip_code_fence(content))
    if salvaged:
        return salvaged
    return []


def _numbered_passages(sources) -> str:
    """The pool as the numbered `[i] (reference) passage` lines every
    composer prompt shares."""
    return "\n".join(
        f"[{i}] ({source['reference']}) {source['passage']}"
        for i, source in enumerate(sources)
    )


# Small counts as English words, for the prompt wording derived from the
# caps above; anything past the table reads as digits.
_COUNT_WORDS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
}


def _count_word(count: int) -> str:
    return _COUNT_WORDS.get(count, str(count))
