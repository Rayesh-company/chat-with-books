"""The deploy smoke (T17, GitLab #18): three checks a deploy must survive,
run by hand after every deploy — health probes alone have shipped a dead
embedder before (2026-09-12: probes green, every search dead).

1. GET  <sheet>/livez   — the sheet is up.
2. GET  <sheet>/health  — the proxy chain answers.
3. POST <cognee>/api/v1/search — ONE real UTF-8 Farsi query over CHUNKS. The
   query is embedded at search time, so a drifted embedder fails HERE, by
   name, with the incident's own signature (a dimensions mismatch) called
   out. The search rides Cognee's CHUNKS endpoint directly rather than the
   sheet's /recall: the embedder, pgvector, and the ingested data are proven
   without spending an LLM call or one of the phone's five daily chats.

Local stack note: Cognee publishes 18000 on the dev machine (the recorded
8000 remap); the VPS keeps 8000 loopback-bound — pass --cognee-url to match.

Usage:
    python scripts/smoke.py                                  # VPS defaults
    python scripts/smoke.py --cognee-url http://localhost:18000   # dev
"""

from __future__ import annotations

import argparse
import json
import sys
from functools import partial
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# The probe question is real Farsi on purpose: the 2026-09-12 verification
# lesson — mangled UTF-8 reaches the engine as an "unreadable message" and
# answers a fallback, which is a green lie. The Book set is the product's
# fixed two Books.
FASSI_PROBE = "اندیشه اسلامی در قرآن"
BOOK_DATASETS = ("tarhe-kolli", "70143-336")


def encode_payload(payload: dict) -> bytes:
    """The request body: real UTF-8, never \\u escapes — the smoke exists to
    prove the Farsi path, so its own probe must ride as real Farsi bytes."""
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def fetch(method: str, url: str, payload: dict | None = None, timeout: int = 120) -> tuple[int, str]:
    """One HTTP round trip — the seam the tests fake."""
    data = None if payload is None else encode_payload(payload)
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = Request(url, data=data, method=method, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def run_checks(sheet_url: str, cognee_url: str, fetch=fetch) -> tuple[bool, list[str]]:
    """The three checks, in order, against an injected fetcher. Returns
    (all green, the report lines). A connection refusal is a named failure
    for the step that hit it — never a crash, never a generic error. The
    first failure stops the run — a dead sheet tells the operator nothing
    about the embedder behind it."""
    lines: list[str] = []

    try:
        status, _ = fetch("GET", f"{sheet_url}/livez")
    except (URLError, OSError) as error:
        return False, [f"FAIL livez: connection — {error}"]
    if status != 200:
        lines.append(f"FAIL livez: HTTP {status} — the sheet is not up")
        return False, lines
    lines.append("ok   livez")

    try:
        status, body = fetch("GET", f"{sheet_url}/health")
    except (URLError, OSError) as error:
        return False, [f"FAIL health: connection — {error}"]
    if status != 200:
        lines.append(f"FAIL health: HTTP {status} — {body[:200]}")
        return False, lines
    lines.append("ok   health")

    payload = {
        "searchType": "CHUNKS",
        "query": FASSI_PROBE,
        "datasets": list(BOOK_DATASETS),
        "topK": 3,
    }
    try:
        status, body = fetch("POST", f"{cognee_url}/api/v1/search", payload)
    except (URLError, OSError) as error:
        return False, [f"FAIL book search: connection — {error}"]
    if status != 200:
        hint = ""
        if "dimension" in body.lower():
            hint = " — the incident's own signature: the embedding pins have drifted from the stored vectors"
        lines.append(f"FAIL book search: HTTP {status} — {body[:200]}{hint}")
        return False, lines
    try:
        hits = json.loads(body)
    except json.JSONDecodeError:
        lines.append(f"FAIL book search: the reply is not JSON — {body[:200]}")
        return False, lines
    if not isinstance(hits, list) or len(hits) == 0:
        lines.append("FAIL book search: 0 hits — the embedder or the index answered empty")
        return False, lines
    lines.append(f"ok   book search ({len(hits)} hits — the embedder, pgvector, and the data are alive)")
    return True, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The deploy smoke: livez, health, one real Farsi search.")
    parser.add_argument("--sheet-url", default="http://localhost:8765")
    parser.add_argument("--cognee-url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)

    ok, lines = run_checks(args.sheet_url, args.cognee_url, fetch=partial(fetch, timeout=args.timeout))
    for line in lines:
        print(line, flush=True)
    print("smoke ok" if ok else "smoke FAILED", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
