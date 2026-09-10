#!/usr/bin/env python3
"""Serve the Farsi Session sheet and proxy first-answer recall to Cognee."""

from __future__ import annotations

from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

UI_DIR = Path(__file__).resolve().parent
COGNEE_URL = os.environ.get("COGNEE_URL", "http://127.0.0.1:8000").rstrip("/")
PORT = int(os.environ.get("SESSION_UI_PORT", "8765"))
PROXY_TIMEOUT = 600
ALLOWED_PROXY = {"/health", "/api/v1/recall"}

# Quoted-answer composer: asks the chat model to write the interleaved
# document (paragraphs of AI text with embedded verbatim Book quotes)
# that replaces the streamed answer on the sheet (ADR-0003). glm-5.3-flash
# only — glm-5.3 stays reserved for Next-tier search (ADR-0002). The pin
# changes only here, after the README's /chat/completions smoke rule —
# never via env.
COMPOSER_MODEL = "glm-5.3-flash"
COMPOSER_URL = (
    os.environ.get("LLM_ENDPOINT", "https://api.z.ai/api/coding/paas/v4").rstrip("/")
    + "/chat/completions"
)
# Measured 2026-09-10: the composer call answers in ~17s with reasoning
# disabled (the default reasoning burned ~70s on a copy-matching task);
# 240s stays as headroom for the coding endpoint's queue variance.
COMPOSER_TIMEOUT = int(os.environ.get("COMPOSER_TIMEOUT", "240"))

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
# "تا" between the numbers, not a dash: digits are LTR-weak in Farsi text.
_PAGES_IN_REFERENCE = re.compile(r"\(pages (\d+)-(\d+)\)")
_PAGE_IN_REFERENCE = re.compile(r"\(page (\d+)\)")


def normalize_for_match(text: str) -> str:
    """Reduce Farsi text to a comparable letter stream (AC-3 normalized comparison)."""
    text = text.translate(_ARABIC_TO_FARSI)
    text = _STRIPPED_MARKS.sub("", text)
    return _NON_WORD.sub("", text).lower()


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
            kept.append({"text": text.strip(), "reference": sources[index]["reference"]})
    return kept


def guard_blocks(blocks, sources):
    """Turn composer blocks into renderable Quoted answer blocks.

    Every paragraph is one unit — AI text with embedded verbatim quotes,
    several passages allowed (PM call, 2026-09-10). A quote part drops
    alone under the verbatim guard; a paragraph left with no surviving
    quote (it would be pure AI text) or no AI text (bare quotes) drops
    whole, and so does any malformed block or claim on a missing passage.
    Each kept quote part carries the pages label of exactly the passage
    it claims. The document itself drops to [] below the swap threshold —
    at least two quoting paragraphs, or one plus a heading — so the sheet
    swaps the streamed answer only for a real Quoted answer (ADR-0003).
    """
    kept = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "heading":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                kept.append({"type": "heading", "text": text.strip()})
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
                    kept_parts.append({"text": text.strip()})
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
                        "quote": quote.strip(),
                        "source": index,
                        "pages_label": pages_label(sources[index]["reference"]),
                    }
                )
                has_quote = True
            if has_quote and has_text:
                kept.append({"type": "paragraph", "parts": kept_parts})
    paragraphs = sum(1 for block in kept if block["type"] == "paragraph")
    headings = sum(1 for block in kept if block["type"] == "heading")
    return kept if paragraphs and (paragraphs >= 2 or headings) else []


def pages_label(reference: str) -> str:
    """Farsi page label for an Evidence locator; '' when it carries no pages.

    A quote whose passage has no page markers cites the Book alone on the
    sheet — never an invented page.
    """
    pages = _PAGES_IN_REFERENCE.search(reference)
    if pages:
        return f"صفحات {pages.group(1)} تا {pages.group(2)}"
    page = _PAGE_IN_REFERENCE.search(reference)
    if page:
        return f"صفحه {page.group(1)}"
    return ""


def parse_quoted_reply(content):
    """Pull the blocks list out of the composer's reply; [] when malformed."""
    if not isinstance(content, str) or not content.strip():
        return []
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    try:
        parsed = json.loads(stripped)
    except ValueError:
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if not match:
            return []
        try:
            parsed = json.loads(match.group(0))
        except ValueError:
            return []
    blocks = parsed.get("blocks") if isinstance(parsed, dict) else None
    return blocks if isinstance(blocks, list) else []


def build_quoted_prompt(question: str, answer: str, sources) -> str:
    passages = "\n".join(
        f"[{i}] ({source['reference']}) {source['passage']}"
        for i, source in enumerate(sources)
    )
    return (
        "You are writing a Farsi Quoted answer for a Q&A sheet over one "
        "Book.\n\n"
        f"Question: {question}\n\n"
        "A faster model's draft answer (context for framing and coverage "
        "only — its claims about the Book are unverified; ground every Book "
        f"claim in the passages below):\n{answer}\n\n"
        "Passages (numbered, from the Book's retrieved Evidence; text-layer "
        "noise like \\b backspaces may appear between words):\n"
        f"{passages}\n\n"
        "Task: write a document that answers the question in interleaved "
        "paragraphs.\n"
        "Every paragraph is one unit: your own Farsi text with quoted "
        "sentences embedded inside it — your text, then a quoted sentence, "
        "then more of your text, as the argument needs. Introduce the topic, "
        "connect the quotes, and summarize what they establish; never state "
        "a Book claim the passages do not support. A paragraph may quote "
        "from more than one passage. Each quoted sentence is a complete "
        "Farsi sentence copied VERBATIM from exactly ONE passage (ignore "
        "the \\b noise; write proper Farsi). Do not paraphrase, do not "
        "merge, do not shorten. Never write a paragraph without at least "
        "one quoted sentence, and never a paragraph of bare quotes without "
        "your connective text.\n"
        "Aim for at least five quote paragraphs across the document when "
        "the passages support them; never invent or paraphrase a quote to "
        "reach the count.\n"
        "You may write short section headings.\n\n"
        "Reply with ONLY a JSON object, no prose, no code fence:\n"
        '{"blocks": [{"type": "heading", "text": "..."}, '
        '{"type": "paragraph", "parts": [{"text": "..."}, '
        '{"quote": "<verbatim sentence>", "source": <passage index>}, '
        '{"text": "..."}]}]}'
    )


def compose_quoted_answer(question: str, answer: str, sources):
    """Write the Quoted answer blocks; [] on any composer failure or when
    the document misses the swap threshold (AC-4)."""
    try:
        api_key = os.environ["LLM_API_KEY"]
        request = Request(
            COMPOSER_URL,
            data=json.dumps(
                {
                    "model": COMPOSER_MODEL,
                    "temperature": 0,
                    # Interleaving AI text with verbatim quotes is light
                    # writing plus copy-matching, not reasoning;
                    # glm-5.3-flash's default thinking adds ~70s for
                    # identical output (measured 2026-09-10).
                    "thinking": {"type": "disabled"},
                    "messages": [
                        {
                            "role": "user",
                            "content": build_quoted_prompt(question, answer, sources),
                        }
                    ],
                }
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urlopen(request, timeout=COMPOSER_TIMEOUT) as response:
            reply = json.load(response)
        content = reply["choices"][0]["message"]["content"]
        return guard_blocks(parse_quoted_reply(content), sources)
    except (KeyError, ValueError, OSError):
        return []


class SessionHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    def log_message(self, format, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/health":
            self._proxy("GET")
            return
        super().do_GET()

    def do_POST(self):
        if self.path.split("?", 1)[0] == "/api/v1/recall":
            self._proxy("POST")
            return
        if self.path.split("?", 1)[0] == "/quoted-answer":
            self._quoted_answer()
            return
        self.send_error(404, "Not found")

    def _quoted_answer(self) -> None:
        """Compose the Quoted answer; empty blocks = fallback."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            question = payload["question"]
            answer = payload.get("answer")
            sources = [
                {
                    "reference": source["reference"],
                    "passage": source["passage"],
                }
                for source in payload["sources"]
                if isinstance(source, dict)
                and isinstance(source.get("reference"), str)
                and isinstance(source.get("passage"), str)
            ]
            if not isinstance(question, str) or not question.strip() or not sources:
                raise ValueError("question and sources are required")
            if not isinstance(answer, str):
                answer = ""
        except (ValueError, KeyError, TypeError):
            self.send_error(400, "Bad request")
            return
        blocks = compose_quoted_answer(question, answer, sources)
        body = json.dumps({"blocks": blocks}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        sys.stderr.write("%s - proxy %s %s\n" % (self.address_string(), method, path))
        sys.stderr.flush()
        if path not in ALLOWED_PROXY:
            self.send_error(404, "Not found")
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else None
        headers = {}
        content_type = self.headers.get("Content-Type")
        if content_type:
            headers["Content-Type"] = content_type
        req = Request(
            f"{COGNEE_URL}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(req, timeout=PROXY_TIMEOUT) as resp:
                content_type = resp.headers.get("Content-Type", "application/json")
                if "text/event-stream" in content_type:
                    # Close-delimited relay (HTTP/1.0): no Content-Length, lines
                    # hit the socket as they arrive. readline() because read(n)
                    # would block until n bytes collect.
                    self.send_response(resp.status)
                    self.send_header("Content-Type", content_type)
                    self.end_headers()
                    try:
                        while True:
                            line = resp.readline()
                            if not line:
                                break
                            self.wfile.write(line)
                    except OSError:
                        sys.stderr.write(
                            "%s - stream client went away\n" % self.address_string()
                        )
                    return
                payload = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except HTTPError as exc:
            payload = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", exc.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            message = json.dumps(
                {"detail": f"cognee unreachable: {reason}"},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(504)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), SessionHandler)
    print(f"Session sheet http://localhost:{PORT}", flush=True)
    print(f"Proxying /api/v1/recall and /health to {COGNEE_URL}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
