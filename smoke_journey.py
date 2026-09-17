# -*- coding: utf-8 -*-
"""Live smoke of the Research Mode journey layer (ADR-0009) over the
real stack at localhost:8765. Sends real UTF-8 (Git Bash's curl mangles
Farsi), decides the checkpoints the classifier parks (a pending
decision blocks the journey — by design), and walks the guided
journey."""
import json
import sys
import time
import urllib.request

BASE = "http://localhost:8765"
PHONE = "09120000001"


def request_json(path, body=None):
    headers = {"X-Session-Phone": PHONE}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def poll(turn_id, patience=720):
    deadline = time.monotonic() + patience
    while time.monotonic() < deadline:
        _, payload = request_json(f"/research/turn?turn={turn_id}")
        if payload["state"] in ("done", "failed", "aborted"):
            return payload
        print("  ...", payload["state"], flush=True)
        time.sleep(5)
    return {"state": "timeout"}


def show(payload):
    print("STATE:", payload["state"])
    if payload["state"] != "done":
        print("detail:", payload.get("detail"))
        return
    for block in payload["reply"]:
        if block["type"] == "paragraph":
            text = " ".join(
                part.get("text", "") or ("«" + part.get("quote", "")[:40] + "»")
                for part in block.get("parts", [])
            )
            print("  ¶", text[:220])
        elif block["type"] == "references":
            print("  منابع:", len(block["items"]), "items")
        else:
            print(" ", block["type"], "—", block.get("text", "")[:220])
    print("  CHIPS:", [c.get("label") or c.get("text", "") for c in payload["suggestions"]])
    state = payload["research_state"]
    print(
        "  MAP: stage=%s | destination=%s | frontier=%s | open=%d | evidence=%d"
        % (
            state["stage"],
            (state["destination"] or "—")[:50],
            state["frontier"] or "—",
            len(state["open_questions"]),
            state["evidence_count"],
        )
    )
    if state["fog"]:
        print("  FOG:", state["fog"][:2])


def clear_proposals(session_id, accept=True):
    """A pending decision blocks the journey — decide every parked
    proposal before walking on."""
    while True:
        _, state = request_json(f"/research/state?session={session_id}")
        proposals = state.get("pending_proposals", [])
        if not proposals:
            return
        proposal = proposals[0]
        status, body = request_json(
            "/research/decide",
            {"session_id": session_id, "proposal_id": proposal["id"], "accept": accept},
        )
        print(
            "  DECIDED", proposal["kind"], "accept" if accept else "reject",
            "—", body["reply"][0].get("text", "")[:90],
        )


def turn(session_id, payload, label, decide=True):
    print(f"\n=== {label} ===")
    if isinstance(payload, str):
        payload = {"text": payload}
    if session_id:
        payload["session_id"] = session_id
    status, body = request_json("/research/message", payload)
    if status != 202:
        print("REJECTED", status, body)
        return session_id, None
    result = poll(body["turn_id"])
    show(result)
    if decide and result.get("suggestions"):
        if any(c["kind"] == "proposal" for c in result["suggestions"]):
            clear_proposals(body["session_id"])
    return body["session_id"], result


session_id, first = turn(
    None,
    {
        "text": "می‌خواهم بدانم شهود در معرفت دینی چه جایگاهی دارد و چه تفاوتی با عقل دارد",
        "question": "جایگاه شهود در معرفت دینی چیست؟",
        "sources": [],
    },
    "TURN 1 (creating) — checkpoint parked, then decided",
)
if not session_id or first is None or first["state"] != "done":
    sys.exit("turn 1 did not land")

session_id, guided = turn(
    session_id, "ادامهٔ سفر پژوهش", "TURN 2 (guide) — expect the destination question"
)
if guided is None or guided["state"] != "done":
    sys.exit("turn 2 did not land")

options = [c["text"] for c in guided["suggestions"] if c["kind"] == "answer"]
answer = options[0] if options else "نقشهٔ روشن از جایگاه شهود و تفاوتش با عقل"
session_id, landscape = turn(
    session_id, {"text": answer},
    "TURN 3 (answer the destination) — expect the mapping landscape",
)
if landscape is None or landscape["state"] != "done":
    sys.exit("turn 3 did not land")

session_id, skipped = turn(
    session_id,
    {"text": "فعلاً همین کافی است؛ ادامه بده"},
    "TURN 4 (skip the mapping question) — expect investigating + per-question chips",
)
if skipped is None or skipped["state"] != "done":
    sys.exit("turn 4 did not land")

targeted = [
    c["text"]
    for c in skipped["suggestions"]
    if c["kind"] == "move" and c["id"].startswith("q:")
]
move = targeted[0] if targeted else "همهٔ پرسش‌های باز را جست‌وجو کن"
session_id, gathered = turn(
    session_id, {"text": move},
    "TURN 5 (%s) — expect narration + counts" % ("targeted" if targeted else "gather-all"),
)
print("\nSMOKE COMPLETE")
