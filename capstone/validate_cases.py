"""Validate the synthetic evaluation set (data/case_NN.md + data/answer_key.json)
against the CMS tables. No Claude API calls.

Fails loudly (exit code 1) if any expected_code's severity doesn't match
reference/cc_mcc.csv, or if any other check fails:
  - every expected_code is in cc_mcc.csv with the same severity (CC/MCC);
  - it is a valid FY 2027 ICD-10-CM code, not already in the case's current
    codes, and not excluded as a CC/MCC by the case's principal diagnosis (Table 6K);
  - replaces_code, if given, is one of the current codes;
  - every current code is a valid FY 2027 ICD-10-CM code;
  - each case file starts with the synthetic-case line, has the required sections,
    and contains no calendar dates, MRNs, DOBs, or names;
  - gap_count matches the expected list and the case_type; splits are
    dev = cases 01-14, test = cases 15-24; the mix is 10 gap, 4 double_gap,
    5 negative, 5 trap.

Usage:
    python capstone/validate_cases.py
"""

import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

from reference import dotted, normalize, tables

HERE = Path(__file__).resolve().parent
CASES = HERE / "data" / "cases"
KEY_FILE = HERE / "data" / "answer_key.json"
CSV_FILE = HERE / "reference" / "cc_mcc.csv"

FIRST_LINE = "SYNTHETIC CASE — fictional patient, not real data"
SECTIONS = ["## HPI", "## Hospital course", "## Labs", "## Vitals", "## Medications",
            "## Discharge diagnoses", "## Current codes"]
GAPS_BY_TYPE = {"gap": 1, "double_gap": 2, "negative": 0}
EXPECTED_MIX = {"gap": 10, "double_gap": 4, "negative": 5, "trap": 5}
# Dates: month/day/year, or month/day with a two-digit part ("03/02", "11/5").
# Not flagged: clinical shorthand such as "2/2" (secondary to), BP "84/50",
# ABG "7.47/31/54", "1/4 strength", or a "2/7" test score.
MONTH, DAY = r"(?:0?[1-9]|1[0-2])", r"(?:0?[1-9]|[12]\d|3[01])"
DATE = re.compile(
    rf"(?<![\d./]){MONTH}/{DAY}/(?:\d{{2}}|\d{{4}})(?![\d/])"
    rf"|(?<![\d./])(?:0[1-9]|1[0-2])/{DAY}(?![\d/])"
    rf"|(?<![\d./]){MONTH}/(?:0[1-9]|[12]\d|3[01])(?![\d/])")
PRIVACY = [
    (DATE, "calendar date"),
    (re.compile(r"\b(?:19|20)\d{2}\b"), "calendar year"),
    (re.compile(r"\b(?:MRN|DOB|date of birth|SSN)\b", re.I), "identifier label"),
    (re.compile(r"^\s*\**(?:Patient )?Name\**\s*:", re.I | re.M), "patient name"),
]
CODE_LINE = re.compile(r"^-\s*(PDX|SDX):\s*([A-Z][0-9A-Z]{1,2}(?:\.[0-9A-Z]{1,4})?)\b")


def load_csv() -> dict:
    with CSV_FILE.open(encoding="utf-8", newline="") as f:
        return {normalize(r["code"]): r for r in csv.DictReader(f)}


def parse_case(path: Path) -> tuple[str, list[str], list[str]]:
    """Return (text, current codes with principal first, problems)."""
    text = path.read_text(encoding="utf-8")
    problems = []
    if text.splitlines()[0].strip() != FIRST_LINE:
        problems.append(f"first line is not {FIRST_LINE!r}")
    for s in SECTIONS:
        if not re.search(rf"^{re.escape(s)}", text, re.M):
            problems.append(f"missing section {s!r}")
    for pattern, what in PRIVACY:
        if m := pattern.search(text):
            problems.append(f"possible {what}: {m.group(0)!r}")

    codes_part = text.split("## Current codes", 1)[-1]
    principal, secondary = [], []
    for line in codes_part.splitlines():
        if m := CODE_LINE.match(line.strip()):
            (principal if m.group(1) == "PDX" else secondary).append(m.group(2))
    if len(principal) != 1:
        problems.append(f"expected exactly one PDX line, found {len(principal)}")
    return text, principal + secondary, problems


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cc_mcc = load_csv()
    t = tables()
    key = json.loads(KEY_FILE.read_text(encoding="utf-8"))
    failures = []
    rows = []

    files = {p.stem for p in CASES.glob("case_*.md")}
    keyed = {e["case_id"] for e in key}
    for missing in sorted(files - keyed):
        failures.append(f"{missing}: case file has no answer key entry")
    for missing in sorted(keyed - files):
        failures.append(f"{missing}: answer key entry has no case file")

    for entry in key:
        cid = entry["case_id"]
        fail = lambda msg: failures.append(f"{cid}: {msg}")
        path = CASES / f"{cid}.md"
        if not path.exists():
            continue
        _, codes, problems = parse_case(path)
        for p in problems:
            fail(p)
        principal = codes[0] if codes else ""
        coded = {normalize(c) for c in codes}

        for c in codes:
            if not t.is_valid(c):
                fail(f"current code {c} is not a valid FY 2027 ICD-10-CM code")

        # Answer key structure
        n = int(re.search(r"\d+", cid).group())
        want_split = "dev" if n <= 14 else "test"
        if entry.get("split") != want_split:
            fail(f"split is {entry.get('split')!r}, expected {want_split!r}")
        ctype = entry.get("case_type")
        if ctype not in EXPECTED_MIX:
            fail(f"unknown case_type {ctype!r}")
        if entry["gap_count"] != len(entry["expected"]):
            fail(f"gap_count {entry['gap_count']} but {len(entry['expected'])} expected item(s)")
        if ctype in GAPS_BY_TYPE and entry["gap_count"] != GAPS_BY_TYPE[ctype]:
            fail(f"case_type {ctype} should have gap_count {GAPS_BY_TYPE[ctype]}")
        if not entry.get("should_not_query"):
            fail("should_not_query is empty")

        # Expected codes against the CMS table
        results = []
        for exp in entry["expected"]:
            code = normalize(exp["expected_code"])
            label = dotted(code)
            row = cc_mcc.get(code)
            if row is None:
                fail(f"{label} ({exp['condition']}) is NOT a CC or MCC in cc_mcc.csv "
                     f"(answer key says {exp['severity']})")
                results.append(f"{label} NOT CC/MCC")
                continue
            if row["severity"] != exp["severity"]:
                fail(f"{label} severity MISMATCH: answer key {exp['severity']}, "
                     f"CMS table {row['severity']}")
            if not t.is_valid(code):
                fail(f"{label} is not a valid FY 2027 ICD-10-CM code")
            if code in coded:
                fail(f"{label} is already in the current codes")
            if principal and t.excluded_by(code, principal):
                fail(f"{label} is excluded as a CC/MCC by principal {dotted(principal)} (Table 6K)")
            rep = exp.get("replaces_code")
            if rep and normalize(rep) not in coded:
                fail(f"replaces_code {rep} is not in the current codes")
            if exp.get("action") not in ("query", "code"):
                fail(f"{label} action must be 'query' or 'code'")
            results.append(f"{label} {row['severity']} {exp.get('action', '?')}")

        rows.append((cid, entry.get("split"), ctype, entry["gap_count"],
                     dotted(principal) if principal else "?", "; ".join(results) or "-",
                     len(entry.get("should_not_query", []))))

    mix = Counter(e.get("case_type") for e in key)
    for ctype, want in EXPECTED_MIX.items():
        if mix.get(ctype, 0) != want:
            failures.append(f"case mix: {mix.get(ctype, 0)} {ctype} cases, expected {want}")

    # Summary
    print(f"Validated {len(key)} cases against {CSV_FILE.relative_to(HERE.parent)} "
          f"({len(cc_mcc):,} CC/MCC codes, CMS FY 2027)\n")
    print(f"{'case':8} {'split':5} {'type':10} {'gaps':4} {'PDX':9} {'expected (code severity action)':45} not-query")
    for cid, split, ctype, gaps, pdx, res, nq in rows:
        print(f"{cid:8} {split:5} {ctype:10} {gaps:<4} {pdx:9} {res:45} {nq}")
    exp_items = [x for e in key for x in e["expected"]]
    print(f"\nMix: {dict(mix)}")
    print(f"Splits: {dict(Counter(e.get('split') for e in key))}")
    print(f"Expected gaps: {len(exp_items)} total — "
          f"{dict(Counter(x['severity'] for x in exp_items))}, "
          f"actions {dict(Counter(x.get('action') for x in exp_items))}")
    print(f"should_not_query items: {sum(len(e.get('should_not_query', [])) for e in key)}")

    if failures:
        print("\n" + "!" * 70)
        print(f"VALIDATION FAILED: {len(failures)} problem(s)")
        print("!" * 70)
        for f in failures:
            print(f"  FAIL {f}")
        sys.exit(1)
    print("\nALL CHECKS PASSED: every expected_code matches its CMS severity.")


if __name__ == "__main__":
    main()
