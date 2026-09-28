"""The Quote-selection picker: one composer call over the Evidence
pool — with one floor-miss repair — producing the guarded verbatim
selections with their Citation labels, the sheet's first answer. The
upstream seam is this module's `urlopen` attribute (the tests patch
ui.picker.urlopen); the call itself is ui.composer's one call shape,
run through that seam."""

from __future__ import annotations

from urllib.request import urlopen

try:
    from ui.composer import _composer_content, _composer_reply
    from ui.guard import (
        _citation_labels,
        _count_word,
        _json_object,
        _numbered_passages,
        guard_sentences,
    )
except ImportError:  # the container runs serve.py as a script beside the modules
    from composer import _composer_content, _composer_reply
    from guard import (
        _citation_labels,
        _count_word,
        _json_object,
        _numbered_passages,
        guard_sentences,
    )

# Quote selection (ADR-0006, issue #28): the first answer rendered on
# the sheet is the Evidence pool's Quote selection — verbatim Book
# sentences, no AI prose — picked by EXACTLY ONE composer call. The
# model is the existing COMPOSER_MODEL pin (glm-5.3-flash, ADR-0002);
# thinking disabled for the phase-2 writer's measured reason:
# copy-matching, not reasoning. The pool itself is untouched — the same
# (reference, passage) pairs stay phase 2's exact input.
QUOTE_SELECTION_AIM = 10
QUOTE_SELECTION_CEILING = 12
QUOTE_SELECTION_FLOOR = 4
# The picker reads a bounded window of each passage (ADR-0014): the
# only_context pool carries full Book chunks — multi-thousand-char runs
# with front-matter and page furniture — and the live 2026-09-23 smoke
# showed the picker degenerating on that bulk (echoing the question
# back instead of selecting). The old Evidence-snippet pool was ~600
# chars a bullet and selected fine; a per-passage window restores that
# input size while the verbatim guard and phase 2 keep the FULL
# passages — a window sentence is still a passage substring, so the
# guard's letter-stream check is untouched.
PICKER_PASSAGE_WINDOW = 1200


def build_picker_prompt(question: str, sources) -> str:
    """The picker's brief: select, don't write.

    Each passage is shown through PICKER_PASSAGE_WINDOW chars of its
    head — the selection input stays the size the Evidence-snippet pool
    always was. The aim wording is derived from QUOTE_SELECTION_AIM —
    the two cannot drift — and the reply shape is the selection list
    guard_sentences already consumes.
    """
    windowed = [
        {**source, "passage": source["passage"][:PICKER_PASSAGE_WINDOW]}
        for source in sources
    ]
    passages = _numbered_passages(windowed)
    return (
        "You are selecting the Quote selection for a Farsi Q&A sheet "
        "over the Books.\n\n"
        f"Question: {question}\n\n"
        "Passages (numbered, from the Books' retrieved Evidence; "
        "text-layer noise like \\b backspaces may appear between "
        "words):\n"
        f"{passages}\n\n"
        "Task: select the Farsi sentences that together best answer "
        f"the question — aim for {_count_word(QUOTE_SELECTION_AIM)}; "
        "fewer only when the passages hold fewer. Each selection is a "
        "complete Farsi sentence copied VERBATIM from exactly ONE "
        "passage (ignore the \\b noise; write proper Farsi). Do not "
        "paraphrase, do not merge, do not shorten. Never invent a "
        "sentence.\n\n"
        "Reply with ONLY a JSON object, no prose, no code fence:\n"
        '{"selections": [{"text": "<verbatim sentence>", '
        '"source": <passage index>}]}'
    )


def parse_picker_reply(content):
    """Pull the selections list out of the picker's reply; [] when
    malformed.

    The parse conventions of parse_quoted_reply — code fence stripped,
    json.loads with one brace-scoped retry for prose-wrapped JSON, the
    shared _json_object — without the salvage: a picker reply is tiny,
    and a truncated one would not survive the floor anyway.
    """
    parsed = _json_object(content)
    selections = parsed.get("selections") if isinstance(parsed, dict) else None
    return selections if isinstance(selections, list) else []


def _pick_once(question: str, sources):
    """One picker call over the pool; the guarded survivors when they
    clear the floor, [] when the guard kept fewer than the floor, None
    when the call itself failed. The three outcomes read differently
    because only one of them is worth a repair call."""
    try:
        reply = _composer_reply(
            build_picker_prompt(question, sources),
            "disabled",
            urlopen_fn=urlopen,
        )
        content = _composer_content(reply)
    except (KeyError, ValueError, OSError):
        return None
    kept = guard_sentences(parse_picker_reply(content), sources)
    kept = kept[:QUOTE_SELECTION_CEILING]
    return kept if len(kept) >= QUOTE_SELECTION_FLOOR else []


def pick_quote_selection(question: str, sources):
    """The guarded selections, or [].

    The call shape is the composer's one shape (glm-5.3-flash, thinking
    disabled, COMPOSER_MAX_TOKENS, endpoint-default temperature). The
    reply runs the existing verbatim letter-stream guard — a
    paraphrase, or a verbatim sentence claiming the wrong index, drops;
    the survivors cap at QUOTE_SELECTION_CEILING, and fewer than
    QUOTE_SELECTION_FLOOR of them means the picker missed the floor.

    ONE floor-miss repair (the phase-2 writer's repair shape, ADR-0010;
    the live 2026-09-23 smoke missed the floor on back-to-back asks):
    a below-floor first read gets exactly one more call — the guard is
    strict and the model's verbatim copies drift between reads — and
    the better attempt rides. A failing call is never retried: the
    recorded one-call failure shape stays, so a dead endpoint costs one
    attempt, not two. Every kept sentence carries the labels of exactly
    the passage it claims, attached server-side like guard_blocks does.
    """
    kept = _pick_once(question, sources)
    if kept is None:
        return []
    if not kept:
        kept = _pick_once(question, sources)
        if kept is None:
            return []
    return [
        {
            "text": item["text"],
            "reference": item["reference"],
            **_citation_labels(item["reference"], item["text"]),
        }
        for item in kept
    ]
