#!/usr/bin/env python
"""Run the TKI question set against the fast chat mode (first answer) of chat-with-books.

Fast chat mode = Cognee recall() with searchType HYBRID_COMPLETION on port 8000.
This runner never touches the next-tier service (8001) or the UI quoted-answer composer.

Usage:
  python benchmark/run_benchmark.py                      # all 18 questions, variant from setup.json
  python benchmark/run_benchmark.py --variant my-tweak   # label the run for comparison
  python benchmark/run_benchmark.py --ids TKI-06,TKI-12  # subset
  python benchmark/run_benchmark.py --probe-chunks       # also record a CHUNKS top-10 retrieval probe per question

Results land in benchmark/runs/<variant>/<UTC timestamp>/ — one JSON per question plus
summary.json / summary.md. setup.json is snapshotted into the run so every run carries
the full setup metadata it was produced under.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent
RUNS_DIR = BENCH_DIR / "runs"

EVIDENCE_MARKER = "Evidence:"
PAGE_MARKER = re.compile(r"Page (\d+)")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default="http://localhost:8000", help="Fast chat service base URL")
    p.add_argument("--variant", default=None, help="Variant label (default: setup.json variant_id)")
    p.add_argument("--ids", default=None, help="Comma-separated question ids to run (default: all)")
    p.add_argument("--limit", type=int, default=None, help="Run only the first N questions")
    p.add_argument("--timeout", type=int, default=600, help="Per-question HTTP timeout in seconds")
    p.add_argument("--probe-chunks", action="store_true", help="Also POST a CHUNKS top-10 retrieval probe per question")
    return p.parse_args()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def post_json(url: str, payload: dict, timeout: int):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body, time.perf_counter() - started


def git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return None


def split_answer(text: str) -> tuple[str, str]:
    idx = text.find(EVIDENCE_MARKER)
    if idx == -1:
        return text, ""
    return text[:idx].strip(), text[idx:].strip()


def main() -> int:
    args = parse_args()
    setup = load_json(BENCH_DIR / "setup.json")
    qset = load_json(BENCH_DIR / "questions.json")
    questions = qset["questions"]

    if args.ids:
        wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
        questions = [q for q in questions if q["id"] in wanted]
        missing = wanted - {q["id"] for q in questions}
        if missing:
            print(f"unknown ids: {sorted(missing)}")
            return 2
    if args.limit:
        questions = questions[: args.limit]
    if not questions:
        print("no questions selected")
        return 2

    variant = args.variant or setup["variant_id"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_DIR / variant / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    # Live-capture what setup.json leaves open, then snapshot setup + questions into the run.
    setup = json.loads(json.dumps(setup))  # deep copy; never mutate the source file
    setup["variant_id"] = variant
    setup["git_commit_captured_live"] = git_commit()
    try:
        with urllib.request.urlopen(f"{args.base_url}/health", timeout=10) as resp:
            setup["service_version_captured_live"] = json.loads(resp.read().decode("utf-8")).get("version")
    except Exception as exc:
        setup["service_version_captured_live"] = f"unavailable: {exc}"

    meta = {
        "run_dir": str(run_dir),
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "timeout_seconds": args.timeout,
        "probe_chunks": args.probe_chunks,
        "question_count": len(questions),
        "question_set_source_sha256": __import__("hashlib").sha256(
            (BENCH_DIR / "questions.json").read_bytes()
        ).hexdigest(),
        "setup": setup,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"run: {variant} @ {stamp}  ({len(questions)} questions, base {args.base_url})")

    rows = []
    for q in questions:
        rec = {
            "id": q["id"],
            "question": q["question"],
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "request": {
                "searchType": "HYBRID_COMPLETION",
                "query": q["question"],
                "datasets": ["tarhe-kolli"],
                "includeReferences": True,
            },
        }
        try:
            body, wall = post_json(f"{args.base_url}/api/v1/recall", rec["request"], args.timeout)
            text = body[0]["text"] if isinstance(body, list) and body else ""
            answer, evidence = split_answer(text)
            pages = sorted({int(m) for m in PAGE_MARKER.findall(evidence)})
            rec.update({
                "status": "ok",
                "wall_seconds": round(wall, 2),
                "response": body,
                "answer_text": answer,
                "evidence_text": evidence,
                "evidence_page_markers": pages,
                "answer_chars": len(answer),
                "finished_utc": datetime.now(timezone.utc).isoformat(),
            })
            print(f"  {q['id']}: ok  {wall:6.1f}s  {len(answer):5d} chars  pages {pages[:8]}{'…' if len(pages) > 8 else ''}")
        except Exception as exc:
            rec.update({
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "wall_seconds": None,
                "finished_utc": datetime.now(timezone.utc).isoformat(),
            })
            print(f"  {q['id']}: ERROR {rec['error']}")

        if args.probe_chunks:
            try:
                probe_body, probe_wall = post_json(
                    f"{args.base_url}/api/v1/search",
                    {"searchType": "CHUNKS", "query": q["question"], "datasets": ["tarhe-kolli"], "topK": 10},
                    args.timeout,
                )
                rec["retrieval_probe"] = {"status": "ok", "wall_seconds": round(probe_wall, 2), "response": probe_body}
            except Exception as exc:
                rec["retrieval_probe"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

        (run_dir / f"{q['id']}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        rows.append(rec)

    ok = [r for r in rows if r["status"] == "ok"]
    walls = sorted(r["wall_seconds"] for r in ok)
    summary = {
        "variant": variant,
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "question_count": len(rows),
        "ok_count": len(ok),
        "error_ids": [r["id"] for r in rows if r["status"] != "ok"],
        "wall_seconds": {
            "mean": round(sum(walls) / len(walls), 2) if walls else None,
            "median": walls[len(walls) // 2] if walls else None,
            "min": walls[0] if walls else None,
            "max": walls[-1] if walls else None,
        },
        "mean_answer_chars": round(sum(r["answer_chars"] for r in ok) / len(ok), 1) if ok else None,
        "mean_evidence_page_count": round(sum(len(r["evidence_page_markers"]) for r in ok) / len(ok), 2) if ok else None,
        "answers_without_evidence": [r["id"] for r in ok if not r["evidence_text"]],
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Benchmark run — {variant} @ {stamp}",
        "",
        f"- Questions: {len(rows)} ({len(summary['error_ids'])} errored: {summary['error_ids'] or 'none'})",
        f"- Wall seconds: mean {summary['wall_seconds']['mean']}, median {summary['wall_seconds']['median']}, "
        f"min {summary['wall_seconds']['min']}, max {summary['wall_seconds']['max']}",
        f"- Mean answer length: {summary['mean_answer_chars']} chars; "
        f"mean distinct cited pages: {summary['mean_evidence_page_count']}",
        f"- Answers missing the Evidence block: {summary['answers_without_evidence'] or 'none'}",
        "",
        "| id | wall s | answer chars | cited pages | status |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r.get('wall_seconds') or '—'} | {r.get('answer_chars', '—')} | "
            f"{len(r.get('evidence_page_markers', []))} | {r['status']} |"
        )
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"done: {len(ok)}/{len(rows)} ok, mean {summary['wall_seconds']['mean']}s -> {run_dir}")
    return 0 if not summary["error_ids"] else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
