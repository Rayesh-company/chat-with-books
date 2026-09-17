# -*- coding: utf-8 -*-
"""Live verification of round three (ADR-0011) over the real stack:
single-book ask, true-page labels, the planning-loop scenario (decide
accept → explicit gather EXECUTES), and the transcript read."""
import json
import sys
import time
import urllib.request

BASE = "http://localhost:8765"
PHONE = "09120000003"


def request_json(path, body=None):
    headers = {"X-Session-Phone": PHONE}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=600) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


print("=== 1. Single-book ask (proxy validation live) ===")
status, payload = request_json(
    "/api/v1/recall",
    {
        "query": "پیامبر و حضرت علی چه نسبتی با هم داشتند؟",
        "datasets": ["70143-336"],
        "searchType": "HYBRID_COMPLETION",
        "includeReferences": True,
        "stream": False,
    },
)
text = (payload.get("results") or [{}])[0].get("text", "") if isinstance(payload, dict) else ""
docs = {
    line.split("document ")[1].split(" ")[0]
    for line in text.splitlines()
    if "document " in line
}
print("status:", status, "| documents seen:", docs or "(no Evidence block — fallback territory)")
assert docs <= {"70143-336"}, f"foreign book leaked: {docs}"
print("OK: only the picked book answered")

print("\n=== 2. True-page labels (resolver live) ===")
status, payload = request_json(
    "/quote-selection",
    {
        "question": "پیامبر و حضرت علی",
        "sources": [
            {
                "reference": "chunk 482 of document tarhe-kolli (pages 482-484)",
                "passage": "",
            }
        ],
    },
)
# The real check: pick any real locator from the ask's text and resolve.
import re
locators = re.findall(r"(chunk \d+ of document [a-z0-9._-]+ \(pages \d+-\d+\)): \"([^\"]{40,})\"", text)
print("locators parsed from the live answer:", len(locators))
for reference, passage in locators[:3]:
    sys.path.insert(0, ".")
    from ui import page_resolver
    page_resolver.install()
    true_page = page_resolver.resolve_first_page(reference, passage)
    label_first = re.search(r"\(pages (\d+)", reference).group(1)
    print(f"  label {label_first} → resolved page {true_page}")

print("\n=== 3. The planning-loop scenario (the Persian test) ===")
status, body = request_json(
    "/research/message",
    {
        "text": "رابطه پیامبر با حضرت علی چگونه بود و چه دلالتی بر جانشینی دارد؟",
        "question": "رابطه پیامبر و حضرت علی و مسئله جانشینی",
        "sources": [],
        "datasets": ["tarhe-kolli"],
    },
)
session_id, turn_id = body["session_id"], body["turn_id"]


def poll(turn_id):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        _, p = request_json(f"/research/turn?turn={turn_id}")
        if p["state"] in ("done", "failed", "aborted"):
            return p
        time.sleep(4)
    return {"state": "timeout"}


first = poll(turn_id)
state = first["research_state"]
print("turn 1:", first["state"], "| proposals parked:", [p["kind"] for p in state["pending_proposals"]])

# Decide every parked proposal (accepted) — then the explicit gather MUST run.
for proposal in state["pending_proposals"]:
    request_json(
        "/research/decide",
        {"session_id": session_id, "proposal_id": proposal["id"], "accept": True},
    )
    print("  decided:", proposal["kind"], "→ accept")

status, body = request_json(
    "/research/message",
    {"text": "شواهد را گردآوری کن", "session_id": session_id},
)
gathered = poll(body["turn_id"])
gstate = gathered["research_state"]
notes = " ".join(b.get("text", "") for b in gathered["reply"] if b["type"] == "note")
print("gather turn:", gathered["state"], "| evidence:", gstate["evidence_count"])
assert "به‌روز شد" not in notes, "the gather turn answered with a proposal update — the loop survives!"
assert gstate["evidence_count"] > 0, "the explicit gather gathered nothing"
print("OK: the explicit gather EXECUTED — no rewrite, no re-approve")

print("\n=== 4. The transcript read ===")
status, payload = request_json(f"/research/messages?session={session_id}")
print("status:", status, "| messages:", len(payload["messages"]))
assert len(payload["messages"]) >= 4, "the transcript did not survive"
print("OK: the chat is re-fetchable after a refresh")

print("\nALL ROUND-THREE LIVE CHECKS PASSED")
