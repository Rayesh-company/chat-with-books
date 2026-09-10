#!/usr/bin/env python
"""Compare benchmark runs across variants.

Reads every run directory under benchmark/runs/ (or the ones passed as arguments)
and prints one comparison table: latency, answer shape, and judge score when the
run has been scored.

Usage:
  python benchmark/compare.py
  python benchmark/compare.py benchmark/runs/baseline/20260910T...Z benchmark/runs/no-farsi-patch/...
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
RUNS_DIR = BENCH_DIR / "runs"


def find_runs(paths: list[str]) -> list[Path]:
    if paths:
        return [Path(p).resolve() for p in paths]
    return sorted(p.parent for p in RUNS_DIR.glob("*/*/summary.json"))


def main(argv: list[str]) -> int:
    runs = find_runs(argv)
    if not runs:
        print("no runs found under benchmark/runs/")
        return 2

    rows = []
    for run in runs:
        summary_path = run / "summary.json"
        if not summary_path.exists():
            continue
        s = json.loads(summary_path.read_text(encoding="utf-8"))
        scores = {}
        if (run / "scores.json").exists():
            sc = json.loads((run / "scores.json").read_text(encoding="utf-8"))
            scores = sc.get("results", {})
        rows.append({
            "variant": s["variant"],
            "stamp": run.name,
            "ok": f"{s['ok_count']}/{s['question_count']}",
            "mean_s": s["wall_seconds"]["mean"],
            "median_s": s["wall_seconds"]["median"],
            "max_s": s["wall_seconds"]["max"],
            "answer_chars": s["mean_answer_chars"],
            "pages": s["mean_evidence_page_count"],
            "no_evidence": len(s["answers_without_evidence"]),
            "scored": sum(1 for v in scores.values() if "final_score" in v),
            "score_ids": scores,
        })

    # Judge mean per run (mean over questions that carry a final_score).
    for row in rows:
        finals = [v["final_score"] for v in row.pop("score_ids").values() if "final_score" in v]
        row["judge_mean"] = round(sum(finals) / len(finals), 1) if finals else None

    header = ["variant", "run", "ok", "mean s", "median s", "max s", "mean chars", "mean pages", "no-evidence", "judge mean (n)"]
    print(" | ".join(header))
    print("-" * 100)
    for r in rows:
        judge = f"{r['judge_mean']} ({r['scored']})" if r["scored"] else "—"
        print(
            f"{r['variant']} | {r['stamp']} | {r['ok']} | {r['mean_s']} | {r['median_s']} | {r['max_s']} | "
            f"{r['answer_chars']} | {r['pages']} | {r['no_evidence']} | {judge}"
        )
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main(sys.argv[1:]))
