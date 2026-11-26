"""Evaluate the Alder Creek assistant against alder-creek/test-cases.csv.

Runs every test case through assistant.ask() and checks each reply in code:
  - priority matches expected_priority;
  - reply contains at least one must_say phrase (| separated) and no must_not_say
    phrase, ignoring capital letters;
  - needs_person is true where the needs_person column says yes;
  - every dollar amount in the reply is $0, $89, $149, or $129.

Usage:
    python week-06/evaluate.py
    python week-06/evaluate.py --save week-06/runs/eval-1.txt
"""

import argparse
import csv
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import anthropic

from assistant import ORG_DIR, ask, load_api_key

CASES_FILE = ORG_DIR / "test-cases.csv"
ALLOWED_AMOUNTS = {Decimal("0"), Decimal("89"), Decimal("149"), Decimal("129")}
DOLLAR_RE = re.compile(r"\$\s*(\d[\d,]*(?:\.\d+)?)")


def phrases(cell: str) -> list[str]:
    return [p.strip() for p in (cell or "").split("|") if p.strip()]


def dollar_amounts(text: str) -> list[tuple[str, Decimal | None]]:
    """Return (as written, value) for every $ amount in the text."""
    found = []
    for m in DOLLAR_RE.finditer(text):
        try:
            value = Decimal(m.group(1).replace(",", ""))
        except InvalidOperation:
            value = None
        found.append((m.group(0), value))
    return found


def check(case: dict, result: dict) -> list[str]:
    """Return the reasons a case fails (empty list means PASS)."""
    reasons = []
    reply = str(result.get("reply", ""))
    reply_lower = reply.lower()

    expected = case["expected_priority"].strip()
    if result.get("priority") != expected:
        reasons.append(f"priority was {result.get('priority')!r}, expected {expected!r}")

    must_say = phrases(case["must_say"])
    if must_say and not any(p.lower() in reply_lower for p in must_say):
        reasons.append(f"reply has none of: {' | '.join(must_say)}")

    said = [p for p in phrases(case["must_not_say"]) if p.lower() in reply_lower]
    if said:
        reasons.append(f"reply says forbidden phrase(s): {' | '.join(said)}")

    if case["needs_person"].strip().lower() == "yes" and result.get("needs_person") is not True:
        reasons.append(f"needs_person was {result.get('needs_person')!r}, expected true")

    bad = [written for written, value in dollar_amounts(reply) if value not in ALLOWED_AMOUNTS]
    if bad:
        reasons.append(f"dollar amount(s) not in the guide: {', '.join(bad)}")

    return reasons


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the Alder Creek assistant")
    parser.add_argument("--save", metavar="FILE", help="also write the output to this file")
    args = parser.parse_args()

    with CASES_FILE.open(newline="", encoding="utf-8") as f:
        cases = list(csv.DictReader(f))

    client = anthropic.Anthropic(api_key=load_api_key())
    lines = []

    def out(line: str = "") -> None:
        print(line, flush=True)
        lines.append(line)

    passed = 0
    for case in cases:
        label = f"{case['id']} ({case['type']})"
        try:
            reasons = check(case, ask(case["request"], client))
        except (anthropic.APIError, RuntimeError, ValueError) as e:
            reasons = [f"assistant error: {e}"]
        if reasons:
            out(f"FAIL {label}: " + "; ".join(reasons))
        else:
            passed += 1
            out(f"PASS {label}")

    out()
    out(f"{passed}/{len(cases)} passed")

    if args.save:
        path = Path(args.save)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Saved to {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
