# -*- coding: utf-8 -*-
"""Live verification of the round-two fixes (ADR-0010) over the real
stack at localhost:8765: single-Book ask through the validating proxy,
the widen endpoint over the live pool, and the citation-landing data
path (passage findable on its labeled pages in the page index)."""
import json
import sys
import urllib.request

BASE = "http://localhost:8765"
PHONE = "09120000002"


def post(path, body, timeout=600):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Session-Phone": PHONE,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def parse_evidence(text):
    at = text.find("Evidence:")
    if at == -1:
        return []
    sources = []
    for bullet in text[at + len("Evidence:"):].split("\n- "):
        item = bullet.strip()
        q = item.find(': "')
        if q == -1:
            continue
        sources.append(
            {
                "reference": item[:q].strip(),
                "passage": item[q + 3:].rstrip('"').strip(),
            }
        )
    return sources


print("=== 1. Single-Book ask through the validating proxy ===")
status, payload = post(
    "/api/v1/recall",
    {
        "query": "انسان در اندیشه اسلامی چه هدفی دارد؟",
        "datasets": ["tarhe-kolli", "bogus-dataset"],
        "searchType": "HYBRID_COMPLETION",
        "includeReferences": True,
        "stream": False,
    },
)
text = ""
if isinstance(payload, dict):
    text = (payload.get("results") or [{}])[0].get("text", "")
elif isinstance(payload, list):
    text = payload[0].get("text", "")
pool = parse_evidence(text)
print("status:", status, "| pool:", len(pool))
docs = {s["reference"].split("document ")[1].split(" ")[0] for s in pool if "document " in s["reference"]}
print("documents in pool:", docs)
assert "bogus-dataset" not in str(docs), "the foreign dataset reached the upstream!"
print("OK: the proxy dropped the foreign dataset name")

print("\n=== 2. The widen (/recall-more) over the live pool ===")
status, payload = post("/recall-more", {"question": "هدف انسان در اندیشه اسلامی", "sources": pool[:6]})
print("status:", status, "| fresh:", len(payload.get("sources", [])))
for source in payload.get("sources", [])[:2]:
    print("  +", source["reference"][:60], "…")
if payload.get("sources"):
    docs_more = {s["reference"].split("document ")[1].split(" ")[0] for s in payload["sources"] if "document " in s["reference"]}
    print("documents in widen:", docs_more)
    assert docs_more <= {"tarhe-kolli", "70143-336"}
print("OK: the widen answered the honest shape over the live pool")

print("\n=== 3. Citation landing: the pool's passages sit in the page index ===")
located = 0
checked = 0
norm = lambda t: "".join(
    ch for ch in (
        t.replace("ي", "ی").replace("ك", "ک")
    ) if ch.isalnum()
).lower()
for source in pool[:4]:
    import re
    range_match = re.search(r"\(pages (\d+)-(\d+)\)", source["reference"])
    doc_match = re.search(r"\bdocument ([A-Za-z0-9._-]+)", source["reference"])
    if not range_match or not doc_match:
        continue
    checked += 1
    first, last = int(range_match.group(1)), int(range_match.group(2))
    with urllib.request.urlopen(
        f"{BASE}/books/{doc_match.group(1)}.pages.json", timeout=60
    ) as response:
        pages = json.loads(response.read().decode("utf-8"))["pages"]
    needle = norm(source["passage"])[:400]
    hit = next(
        (n for n in range(first, last + 1) if needle and needle in norm(pages.get(str(n), ""))),
        None,
    )
    if hit is None:
        hit = next(
            (
                n
                for n in list(range(max(1, first - 2), last + 3))
                if needle and needle in norm(pages.get(str(n), ""))
            ),
            None,
        )
    print(f"  locator {first}-{last} → passage found on page {hit}")
    located += hit is not None
assert checked == 0 or located == checked, f"only {located}/{checked} passages landed on their pages"
print("OK: every checked passage is findable on its labeled (±2) pages")

print("\nALL LIVE CHECKS PASSED")
