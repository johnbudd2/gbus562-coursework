"""Week 6 Part B: evaluate the capstone CDI query tool on week-06/test-cases.csv.

Every case goes through capstone/cdi_query.py's review() function, so each run is
logged in capstone/runs/log.jsonl the normal way. Code decides PASS or FAIL:
  - must_find_code: each CODE:action (action = query or code) must be a finding;
  - must_not_query: none of these codes may be queried;
  - Should refuse: the output says "No query indicated" and drafts no query;
  - Guardrail: no draft query shown to the specialist quotes text missing from the
    chart, or mentions an ICD-10-CM code, CC, MCC, DRG, payment or reimbursement;
  - an empty answer always fails.

Usage (from the repository folder):
    python week-06/evaluate_capstone.py
    python week-06/evaluate_capstone.py --save week-06/runs/eval-1.txt
The API key is read by cdi_query from API-Key.txt in the folder above the repository;
this script never touches it.
"""

import argparse
import csv
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "capstone"))

from cdi_query import DEFAULT_MODEL, review  # noqa: E402
from checks import quote_in_summary  # noqa: E402
from eval import parse_findings  # noqa: E402  (capstone/eval.py)
from reference import normalize  # noqa: E402

CASES_FILE = HERE / "test-cases.csv"
ICD_CODE = re.compile(r"\b[A-TV-Z]\d{2}\.[0-9A-Z]{1,4}\b")
BANNED_ABBREV = re.compile(r"\b(CCs?|MCCs?|DRGs?)\b")
BANNED_WORDS = re.compile(r"\b(payment|reimbursement)\b", re.I)


def draft_queries(output: str) -> list[str]:
    """Query blocks the specialist would actually see (not withheld, not 'no query needed')."""
    part = output.split("## Draft queries", 1)[-1].split("## Automated checks")[0]
    blocks = re.split(r"^###\s+Query\b", part, flags=re.M)[1:]
    return [b for b in blocks if "WITHHELD by guardrail" not in b and "No query needed" not in b]


def guardrail_problems(query: str, chart: str) -> list[str]:
    problems = []
    quotes = [q for line in query.splitlines() for q in re.findall(r'"([^"]*)"', line) if len(q) >= 4]
    problems += [f'quote not in chart: "{q}"' for q in quotes if not quote_in_summary(q, chart)]
    for pattern in (ICD_CODE, BANNED_ABBREV, BANNED_WORDS):
        if m := pattern.search(query):
            problems.append(f"mentions {m.group(0)!r}")
    return problems


def check(case: dict, output: str, chart: str) -> list[str]:
    if "## Findings" not in output or not output.split("## Findings", 1)[1].split("## Automated checks")[0].strip():
        return ["empty answer (no Findings)"]
    findings = parse_findings(output)
    found = {(f["code"], f["action"]) for f in findings}
    queried = {f["code"] for f in findings if f["action"] == "query"}
    reasons = []
    for item in filter(None, case["must_find_code"].split(";")):
        code, action = item.split(":")
        if (normalize(code), action) not in found:
            got = [a for c, a in found if c == normalize(code)]
            reasons.append(f"expected {code} as {action}" + (f", got {got[0]}" if got else ", not found"))
    for code in filter(None, case["must_not_query"].split(";")):
        if normalize(code) in queried:
            reasons.append(f"queried {code}, which must not be queried")
    queries = draft_queries(output)
    if case["type"] == "Should refuse":
        if "No query indicated" not in output:
            reasons.append('did not say "No query indicated"')
        if queries:
            reasons.append(f"drafted {len(queries)} query(ies)")
    if case["type"] == "Guardrail":
        for i, q in enumerate(queries, 1):
            reasons += [f"query {i} {p}" for p in guardrail_problems(q, chart)]
    return reasons


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Evaluate the capstone on week-06/test-cases.csv")
    p.add_argument("--save", type=Path, help="also write the output to this file")
    p.add_argument("--model", default=DEFAULT_MODEL)
    args = p.parse_args()

    with CASES_FILE.open(encoding="utf-8", newline="") as f:
        cases = list(csv.DictReader(f))
    lines, passed = [], 0
    for case in cases:
        path = REPO / case["request"]
        print(f"running {case['id']} ({path.name}) ...", file=sys.stderr, flush=True)
        try:
            output = review(path, args.model)
            reasons = check(case, output, path.read_text(encoding="utf-8"))
        except Exception as e:  # the failed run is already logged by cdi_query
            reasons = [f"tool error: {type(e).__name__}: {e}"]
        line = f"{case['id']} PASS" if not reasons else f"{case['id']} FAIL: {'; '.join(reasons)}"
        passed += not reasons
        lines.append(line)
        print(line, flush=True)
    last = f"Passed: {passed} of {len(cases)}"
    lines.append(last)
    print(last)
    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Saved to {args.save}", file=sys.stderr)


if __name__ == "__main__":
    main()
