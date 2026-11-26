"""Evaluate cdi_query.py against data/answer_key.json.

Each case goes through cdi_query.review(), the tool's normal request function, so
every case is logged in runs/log.jsonl exactly like a normal run. This script only
reads the tool's output; it never writes the log.

Usage (from the capstone folder):
    python eval.py --split dev
    python eval.py --split all --model claude-sonnet-5 claude-opus-5-5
    python eval.py --split dev --cases case_10 case_12      # a subset

Scoring per case:
  - found codes: the "Candidate code" of every finding in the output;
  - recall / precision against expected_code, by exact code and by 3-character category;
  - false queries: a finding with "Action: query" that matches no expected gap and is
    either on a negative case or matches a should_not_query condition;
  - unbacked severity: a finding whose code was not passed to lookup_code in that run,
    or whose stated severity differs from reference/cc_mcc.csv.
The report is printed and saved to runs/eval_<UTC timestamp>.md.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from cdi_query import CC_MCC, DEFAULT_MODEL, review
from reference import dotted, normalize

HERE = Path(__file__).resolve().parent
KEY_FILE = HERE / "data" / "answer_key.json"
CASES = HERE / "data" / "cases"
RUNS = HERE / "runs"
CODE = re.compile(r"\b([A-TV-Z]\d{2}(?:\.[0-9A-Z]{1,4})?)\b")
STOP = {"acute", "chronic", "with", "without", "type", "due", "and", "the", "that", "query",
        "states", "present", "history", "stage", "level", "only", "from", "this"}


def parse_findings(output: str) -> list[dict]:
    """Findings blocks ("### 1. condition" ...) from the tool's output."""
    part = output.split("## Draft queries")[0]
    findings = []
    for block in re.split(r"^###\s+\d+\.\s*", part, flags=re.M)[1:]:
        lines = block.splitlines()
        get = lambda label: next((ln.split(":", 1)[1].strip() for ln in lines
                                  if ln.strip().lower().startswith(f"- {label}")), "")
        code = CODE.search(get("candidate code"))
        sev = re.search(r"\b(MCC|CC|NONE)\b", get("severity"))
        findings.append({
            "condition": lines[0].strip(),
            "action": "query" if "query" in get("action").lower() else "code",
            "code": normalize(code.group(1)) if code else "",
            "description": get("candidate code"),
            "severity": sev.group(1) if sev else "",
        })
    return findings


def matches_condition(finding: dict, condition: str) -> bool:
    words = {w for w in re.findall(r"[a-z]+", condition.lower()) if len(w) >= 4 and w not in STOP}
    text = (finding["condition"] + " " + finding["description"]).lower()
    return bool(words) and sum(w in text for w in words) >= max(1, round(0.6 * len(words)))


def score(entry: dict, output: str, looked_up: set[str]) -> dict:
    expected = [normalize(e["expected_code"]) for e in entry["expected"]]
    findings = parse_findings(output)
    found = [f["code"] for f in findings if f["code"]]
    exp_cats = {c[:3] for c in expected}

    false_queries = []
    for f in findings:
        if f["action"] != "query" or f["code"][:3] in exp_cats:
            continue
        if entry["case_type"] == "negative":
            false_queries.append(f"{f['condition']} (negative case)")
        elif any(matches_condition(f, s["condition"]) for s in entry["should_not_query"]):
            false_queries.append(f"{f['condition']} (should_not_query)")

    unbacked = []
    for f in findings:
        if not f["code"]:
            continue
        table = CC_MCC.get(f["code"], {}).get("severity", "NONE")
        if f["code"] not in looked_up:
            unbacked.append(f"{dotted(f['code'])}: no lookup_code call")
        elif f["severity"] and f["severity"] != table:
            unbacked.append(f"{dotted(f['code'])}: stated {f['severity']}, table {table}")

    return {
        "expected": expected, "found": found,
        "found_desc": [f"{dotted(f['code'])} {f['action']}" for f in findings if f["code"]],
        "exact_hits": len(set(expected) & set(found)),
        "cat_hits": sum(c[:3] in {x[:3] for x in found} for c in expected),
        "found_exact_ok": sum(c in expected for c in found),
        "found_cat_ok": sum(c[:3] in exp_cats for c in found),
        "false_queries": false_queries, "unbacked": unbacked,
    }


def pct(num: int, den: int) -> str:
    return f"{100 * num / den:.0f}% ({num}/{den})" if den else "n/a"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Evaluate cdi_query.py against the answer key")
    p.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    p.add_argument("--model", nargs="+", default=[DEFAULT_MODEL])
    p.add_argument("--cases", nargs="+", metavar="CASE_ID", help="only these case ids")
    args = p.parse_args()

    key = [e for e in json.loads(KEY_FILE.read_text(encoding="utf-8"))
           if args.split == "all" or e["split"] == args.split]
    if args.cases:
        key = [e for e in key if e["case_id"] in args.cases]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    lines = [f"# Evaluation {stamp} UTC", "",
             f"Split: {args.split}; cases: {len(key)}; models: {', '.join(args.model)}", ""]
    totals = {}
    for model in args.model:
        lines += [f"## {model}", "",
                  "| case | type | expected | found | exact | category | false queries | unbacked severity | in tok | out tok | sec |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        t = dict(exp=0, exact=0, cat=0, found=0, f_exact=0, f_cat=0, fq=0, unbacked=0,
                 inp=0, out=0, sec=0.0, n=0, errors=0)
        for entry in key:
            print(f"{model}: {entry['case_id']} ...", file=sys.stderr, flush=True)
            stats = {}
            try:
                output = review(CASES / f"{entry['case_id']}.md", model, stats)
                s = score(entry, output, stats["looked_up"])
            except Exception as e:  # already logged by the tool
                t["errors"] += 1
                lines.append(f"| {entry['case_id']} | {entry['case_type']} | ERROR: {type(e).__name__}: {e} "
                             "| | | | | | | | |")
                continue
            t["n"] += 1
            for k, v in [("exp", len(s["expected"])), ("exact", s["exact_hits"]), ("cat", s["cat_hits"]),
                         ("found", len(s["found"])), ("f_exact", s["found_exact_ok"]),
                         ("f_cat", s["found_cat_ok"]), ("fq", len(s["false_queries"])),
                         ("unbacked", len(s["unbacked"])), ("inp", stats["input_tokens"]),
                         ("out", stats["output_tokens"]), ("sec", stats["seconds"])]:
                t[k] += v
            lines.append(
                f"| {entry['case_id']} | {entry['case_type']} "
                f"| {', '.join(dotted(c) for c in s['expected']) or '-'} "
                f"| {', '.join(s['found_desc']) or '-'} "
                f"| {s['exact_hits']}/{len(s['expected'])} | {s['cat_hits']}/{len(s['expected'])} "
                f"| {'; '.join(s['false_queries']) or '0'} | {'; '.join(s['unbacked']) or '0'} "
                f"| {stats['input_tokens']:,} | {stats['output_tokens']:,} | {stats['seconds']:.1f} |")
        totals[model] = t
        lines.append("")

    lines += ["## Totals per model", "",
              "| model | cases | recall exact | recall category | precision exact | precision category "
              "| false queries | unbacked severity | avg in tok | avg out tok | avg sec | errors |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for model, t in totals.items():
        n = t["n"] or 1
        lines.append(
            f"| {model} | {t['n']} | {pct(t['exact'], t['exp'])} | {pct(t['cat'], t['exp'])} "
            f"| {pct(t['f_exact'], t['found'])} | {pct(t['f_cat'], t['found'])} | {t['fq']} | {t['unbacked']} "
            f"| {t['inp'] / n:,.0f} | {t['out'] / n:,.0f} | {t['sec'] / n:.1f} | {t['errors']} |")

    report = "\n".join(lines) + "\n"
    print(report)
    path = RUNS / f"eval_{stamp}.md"
    path.write_text(report, encoding="utf-8")
    print(f"Saved {path.relative_to(HERE.parent)}")


if __name__ == "__main__":
    main()
