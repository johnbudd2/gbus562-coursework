"""Evaluate the CDI tool on every case in capstone/data/cases.

Each case runs through the full tool (so each run is also logged in
runs/log.jsonl), then the result is checked in code against the case's
"expected" block, which Claude never sees:
  - every expected candidate is found, with one of its accepted codes and the
    right status (documented_not_coded or needs_query);
  - no other candidate survives the code checks (an extra query is a failure:
    over-querying burdens physicians and creates compliance risk);
  - no "forbidden" code is proposed at all, even if the code checks dropped it;
  - every drafted query passes the compliance check.

Usage:
    python capstone/evaluate.py
    python capstone/evaluate.py --save capstone/runs/eval-1.txt
    python capstone/evaluate.py --only case-03 case-07
"""

import argparse
import json
import sys
from pathlib import Path

import anthropic

from cdi import MODEL, load_api_key, load_case, review
from reference import normalize

HERE = Path(__file__).resolve().parent
CASES = HERE / "data" / "cases"


def check(expected: dict, result: dict) -> list[str]:
    reasons = []
    kept = result["coding_opportunities"] + result["query_candidates"]
    proposed = kept + result["dropped_candidates"]
    matched = set()

    for exp in expected.get("candidates", []):
        codes = {normalize(c) for c in exp["codes"]}
        hit = next((c for c in kept if normalize(c["icd10_code"]) in codes), None)
        if hit is None:
            dropped = next((c for c in result["dropped_candidates"]
                            if normalize(c["icd10_code"]) in codes), None)
            why = f" (proposed but dropped: {dropped['dropped_because']})" if dropped else ""
            reasons.append(f"missed {exp['codes'][0]} {exp['status']}{why}")
            continue
        matched.add(id(hit))
        if hit["status"] != exp["status"]:
            reasons.append(f"{hit['icd10_code']} was {hit['status']}, expected {exp['status']}")

    allowed = {normalize(c) for c in expected.get("allowed_extra", [])}
    for c in kept:
        if id(c) not in matched and normalize(c["icd10_code"]) not in allowed:
            reasons.append(f"unexpected candidate {c['icd10_code']} {c['diagnosis']} ({c['status']})")

    forbidden = {normalize(c) for c in expected.get("forbidden", [])}
    for c in proposed:
        if normalize(c["icd10_code"]) in forbidden:
            reasons.append(f"proposed forbidden code {c['icd10_code']} {c['diagnosis']}")

    for c in result["query_candidates"]:
        d = c["query_draft"]
        if not d["passed_compliance_check"]:
            reasons.append(f"query for {c['icd10_code']} failed compliance: "
                           + "; ".join(d["compliance_problems"]))
    return reasons


def found_summary(result: dict) -> str:
    parts = [f"{c['icd10_code']} coding" for c in result["coding_opportunities"]]
    parts += [f"{c['icd10_code']} query"
              + ("" if c["query_draft"]["passed_compliance_check"] else " (non-compliant)")
              + (f", {c['query_draft']['revisions']} revision" if c["query_draft"]["revisions"] else "")
              for c in result["query_candidates"]]
    dropped = [c["icd10_code"] for c in result["dropped_candidates"]]
    text = "found: " + (", ".join(parts) if parts else "nothing")
    return text + (f"; dropped: {', '.join(dropped)}" if dropped else "")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Evaluate the CDI tool")
    parser.add_argument("--save", type=Path, metavar="FILE", help="also write the output to this file")
    parser.add_argument("--only", nargs="+", metavar="CASE_ID", help="run only these cases")
    args = parser.parse_args()

    files = sorted(CASES.glob("*.json"))
    if args.only:
        files = [f for f in files if f.stem in args.only]
    client = anthropic.Anthropic(api_key=load_api_key())
    lines = []

    def out(line=""):
        print(line, flush=True)
        lines.append(line)

    out(f"CDI tool evaluation - model {MODEL}, {len(files)} cases")
    out()
    passed = 0
    totals = {"input_tokens": 0, "output_tokens": 0, "seconds": 0.0, "calls": 0}
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        label = f"{data['id']} ({data.get('title', '')})"
        try:
            result, t = review(load_case(path), client)
            reasons = check(data.get("expected", {}), result)
            for k in totals:
                totals[k] += t[k]
            detail = f"   {found_summary(result)} | {t['calls']} calls, " \
                     f"{t['input_tokens'] + t['output_tokens']:,} tokens, {t['seconds']:.0f}s"
        except (anthropic.APIError, RuntimeError, ValueError, KeyError) as e:
            reasons = [f"tool error: {type(e).__name__}: {e}"]
            detail = ""
        if reasons:
            out(f"FAIL {label}: " + "; ".join(reasons))
        else:
            passed += 1
            out(f"PASS {label}")
        if detail:
            out(detail)

    out()
    out(f"{passed}/{len(files)} passed")
    out(f"Totals: {totals['calls']} Claude calls, {totals['input_tokens']:,} input tokens, "
        f"{totals['output_tokens']:,} output tokens, {totals['seconds']:.0f} seconds")

    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Saved to {args.save}")


if __name__ == "__main__":
    main()
