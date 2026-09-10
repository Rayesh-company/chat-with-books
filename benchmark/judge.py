#!/usr/bin/env python
"""Score a benchmark run with an LLM judge: glm-5.3 on the Z.AI coding endpoint.

The judge is deliberately a different, fixed model across variants so scores are
comparable between runs. It scores each answer against the question's gold points
and traps using the weights in scoring.json, applies hallucination penalties, and
writes scores.json into the run directory.

Usage:
  python benchmark/judge.py benchmark/runs/baseline/20260910T...Z
  python benchmark/judge.py <run_dir> --ids TKI-06,TKI-12 --model glm-5.3
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent

PROMPT_TEMPLATE = """You are a strict RAG benchmark judge. You are scoring one answer produced by a question-answering agent over a Farsi book (طرح کلی اندیشۀ اسلامی در قرآن).

Score the answer on each component from 0 to 100:
- answer_correctness: how many of the gold points are correctly covered?
- groundedness: do the claims actually come from the retrieved context (the Evidence block), not from outside knowledge?
- evidence_quality: did it cite the best passages/sessions for this question?
- retrieval_completeness: did it surface all the chunks/sections needed?
- reasoning_quality: did it infer relations between concepts, or only repeat text?
- contradiction_handling: does it detect the false premise / false contradiction, if the question has one?
- citation_accuracy: does each citation actually support the claim next to it? (invented page/verse = 0 plus penalty)
- conciseness_relevance: free of padding and irrelevant outside information?

Then apply hallucination penalties (deductions out of the final score):
minor_unsupported_claim -5, major_unsupported_claim -15, wrong_attribution -20,
invented_verse_page_chapter -25, contradiction_with_source -30, confident_fabricated_quote -40.

Do not reward outside knowledge: this agent must answer from the book only.

## QUESTION
{question}

## GOLD POINTS (the answer must cover these)
{gold_points}

## KNOWN TRAPS (the answer must not fall into)
{traps}

## AGENT ANSWER (including its Evidence block)
{answer}

## OUTPUT
Return STRICT JSON only, no markdown fences, no commentary:
{{"scores": {{"answer_correctness": 0, "groundedness": 0, "evidence_quality": 0, "retrieval_completeness": 0, "reasoning_quality": 0, "contradiction_handling": 0, "citation_accuracy": 0, "conciseness_relevance": 0}}, "penalties": [{{"type": "wrong_attribution", "deduction": 20, "excerpt": "..."}}], "rationale": "one short paragraph in English"}}
"""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run_dir", type=Path)
    p.add_argument("--ids", default=None, help="Comma-separated question ids (default: all in the run)")
    p.add_argument("--model", default="glm-5.3", help="Judge model on the Z.AI coding endpoint")
    p.add_argument("--base-url", default="https://api.z.ai/api/coding/paas/v4")
    p.add_argument("--api-key", default=None, help="Defaults to LLM_API_KEY from the repo .env or host env")
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--limit", type=int, default=None)
    return p.parse_args()


def api_key_from_env_file() -> str | None:
    import os

    if os.environ.get("LLM_API_KEY"):
        return os.environ["LLM_API_KEY"]
    env = REPO_ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("LLM_API_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value and value != "your_z_ai_api_key":
                    return value
    return None


def extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in judge reply")
    return json.loads(text[start : end + 1])


def weighted_final(scores: dict, scoring: dict, penalties: list) -> int:
    total = 0.0
    for c in scoring["criteria"]:
        total += (c["weight"] / 100.0) * float(scores.get(c["name"], 0))
    deduction = sum(float(p.get("deduction", 0)) for p in penalties)
    return max(0, round(total - deduction))


def main() -> int:
    args = parse_args()
    run_dir = args.run_dir.resolve()
    scoring = json.loads((BENCH_DIR / "scoring.json").read_text(encoding="utf-8"))
    questions = {q["id"]: q for q in json.loads((BENCH_DIR / "questions.json").read_text(encoding="utf-8"))["questions"]}

    recs = sorted(run_dir.glob("TKI-*.json"))
    if args.ids:
        wanted = {x.strip() for x in args.ids.split(",")}
        recs = [r for r in recs if r.stem in wanted]
    if args.limit:
        recs = recs[: args.limit]
    if not recs:
        print(f"no TKI-*.json records in {run_dir}")
        return 2

    key = args.api_key or api_key_from_env_file()
    if not key:
        print("no API key: pass --api-key or set LLM_API_KEY (host env or repo .env)")
        return 2

    scores_path = run_dir / "scores.json"
    scores_doc = json.loads(scores_path.read_text(encoding="utf-8")) if scores_path.exists() else {"judge_model": args.model, "judged_utc": None, "results": {}}
    scores_doc["judge_model"] = args.model

    for rec_path in recs:
        qid = rec_path.stem
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
        if rec.get("status") != "ok":
            print(f"  {qid}: skipped ({rec.get('status')})")
            continue
        q = questions[qid]
        prompt = PROMPT_TEMPLATE.format(
            question=q["question"],
            gold_points="\n".join(f"- {gp}" for gp in q["gold_points"]),
            traps="\n".join(f"- {t}" for t in q["traps"]) or "- (none)",
            answer=(rec.get("answer_text", "") + "\n\n" + rec.get("evidence_text", "")).strip() or "(empty answer)",
        )
        payload = {
            "model": args.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 8192,
        }
        req = urllib.request.Request(
            f"{args.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        content = None
        try:
            started = time.perf_counter()
            with urllib.request.urlopen(req, timeout=args.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            verdict = extract_json(content)
            penalties = verdict.get("penalties", [])
            entry = {
                "scores": verdict["scores"],
                "penalties": penalties,
                "final_score": weighted_final(verdict["scores"], scoring, penalties),
                "rationale": verdict.get("rationale", ""),
                "judge_wall_seconds": round(time.perf_counter() - started, 2),
            }
            scores_doc["results"][qid] = entry
            print(f"  {qid}: final {entry['final_score']:3d}  ({entry['judge_wall_seconds']}s)")
        except Exception as exc:
            scores_doc["results"][qid] = {"error": f"{type(exc).__name__}: {exc}", "raw_reply": content}
            print(f"  {qid}: JUDGE ERROR {exc}")

        scores_doc["judged_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        finals = [v["final_score"] for v in scores_doc["results"].values() if "final_score" in v]
        scores_doc["mean_final_score"] = round(sum(finals) / len(finals), 2) if finals else None
        scores_path.write_text(json.dumps(scores_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"judge done: mean final {scores_doc.get('mean_final_score')} -> {scores_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
