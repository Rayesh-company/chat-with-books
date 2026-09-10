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

# Citation-paragraph composer: asks the chat model to pick verbatim Book
# sentences for the sheet's hover-citation paragraph. glm-5.3-flash only —
# glm-5.3 stays reserved for Next-tier search (ADR-0002). The pin changes
# only here, after the README's /chat/completions smoke rule — never via env.
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


def parse_composer_reply(content):
    """Pull the sentences list out of the composer's reply; [] when malformed."""
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
    sentences = parsed.get("sentences") if isinstance(parsed, dict) else None
    return sentences if isinstance(sentences, list) else []


def build_composer_prompt(question: str, sources) -> str:
    passages = "\n".join(
        f"[{i}] ({source['reference']}) {source['passage']}"
        for i, source in enumerate(sources)
    )
    return (
        "You are building a citation paragraph for a Farsi Q&A sheet.\n\n"
        f"Question: {question}\n\n"
        "Passages (numbered, from the Book's retrieved Evidence; text-layer "
        "noise like \\b backspaces may appear between words):\n"
        f"{passages}\n\n"
        "Task: select complete Farsi sentences copied VERBATIM from the "
        "passages above (ignore the \\b noise; copy the words exactly), "
        "ordered so they best support answering the question. Do not "
        "paraphrase, do not merge, do not shorten.\n\n"
        "Reply with ONLY a JSON object, no prose, no code fence:\n"
        '{"sentences": [{"text": "<verbatim sentence>", "source": <passage index>}]}'
    )


def compose_citation_paragraph(question: str, sources):
    """Select verbatim citation sentences; [] on any composer failure (AC-4)."""
    try:
        api_key = os.environ["LLM_API_KEY"]
        request = Request(
            COMPOSER_URL,
            data=json.dumps(
                {
                    "model": COMPOSER_MODEL,
                    "temperature": 0,
                    # Selecting verbatim sentences is copy-matching, not
                    # reasoning; glm-5.3-flash's default thinking adds ~70s
                    # for identical output (measured 2026-09-10).
                    "thinking": {"type": "disabled"},
                    "messages": [
                        {
                            "role": "user",
                            "content": build_composer_prompt(question, sources),
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
        return guard_sentences(parse_composer_reply(content), sources)
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
        if self.path.split("?", 1)[0] == "/citation-paragraph":
            self._citation_paragraph()
            return
        self.send_error(404, "Not found")

    def _citation_paragraph(self) -> None:
        """Compose the hover-citation paragraph; empty sentences = fallback."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            question = payload["question"]
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
        except (ValueError, KeyError, TypeError):
            self.send_error(400, "Bad request")
            return
        sentences = compose_citation_paragraph(question, sources)
        body = json.dumps({"sentences": sentences}, ensure_ascii=False).encode("utf-8")
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
