"""CDI review tool: find evidence-supported CC/MCC candidates in a discharge summary
and draft compliant, non-leading physician queries for a CDI specialist to review.

Usage:
    python capstone/cdi.py --case capstone/data/cases/case-01.json
    python capstone/cdi.py --summary my-summary.txt --codes "J18.9,E11.9,I10"
    python capstone/cdi.py --case ... --save capstone/runs/case-01.txt
    python capstone/cdi.py --case ... --json          # print the result as JSON

Pipeline (one request = one case):
  1. code:   look up the current codes and the case's CC/MCC level (CMS tables)
  2. Claude: find candidates, with relevant ICD-10-CM guideline sections (RAG)
  3. code:   keep only candidates with real quotes, valid codes, not already
             coded, and CC/MCC with this principal diagnosis after exclusions
  4. Claude: draft a non-leading query for each "needs_query" candidate
  5. code:   compliance check; if it fails, Claude revises once, then re-check

Every request appends one JSON line to capstone/runs/log.jsonl. Nothing is ever
sent to a physician: every draft is for the CDI specialist to review.

The API key is read at runtime from API-Key.txt in the folder above the
repository (or course_API.txt there, or ANTHROPIC_API_KEY). It is never written
to any file or printed.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic

import reference
from checks import query_problems, query_text, review_candidates
from reference import dotted, tables

MODEL = "claude-sonnet-5"

HERE = Path(__file__).resolve().parent
PROMPTS = HERE / "prompts"
RUNS = HERE / "runs"
LOG_FILE = RUNS / "log.jsonl"
DRAFTS = RUNS / "drafts"
KEY_DIR = HERE.parent.parent  # the folder above the repository
KEY_FILES = ["API-Key.txt", "course_API.txt"]

MAX_REVISIONS = 1


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def load_api_key() -> str:
    """Read the key at runtime; it is only held in memory."""
    for name in KEY_FILES:
        path = KEY_DIR / name
        if path.is_file() and (key := path.read_text(encoding="utf-8").strip()):
            return key
    if key := os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return key
    sys.exit(f"No API key found. Put it in {KEY_DIR / KEY_FILES[0]} or set ANTHROPIC_API_KEY.")


def prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8").strip()


def load_case(path: Path) -> dict:
    """Read a case file. The 'expected' answer, if present, is never sent to Claude."""
    data = json.loads(path.read_text(encoding="utf-8"))
    summary = data["discharge_summary"]
    if isinstance(summary, list):
        summary = "\n".join(summary)
    return {
        "id": data.get("id", path.stem),
        "source": str(path),
        "principal": data["codes"]["principal"],
        "secondary": list(data["codes"].get("secondary", [])),
        "discharge_summary": summary,
    }


def case_from_args(summary_file: Path, codes: str) -> dict:
    code_list = [c.strip() for c in re.split(r"[,\s]+", codes) if c.strip()]
    if not code_list:
        sys.exit("--codes needs at least the principal diagnosis.")
    return {
        "id": summary_file.stem,
        "source": str(summary_file),
        "principal": code_list[0],
        "secondary": code_list[1:],
        "discharge_summary": summary_file.read_text(encoding="utf-8"),
    }


# ---------------------------------------------------------------------------
# Claude calls (tokens and seconds are added up across every call)
# ---------------------------------------------------------------------------

class Claude:
    def __init__(self, client: anthropic.Anthropic):
        self.client = client
        self.input_tokens = 0
        self.output_tokens = 0
        self.seconds = 0.0
        self.calls = 0

    def json(self, system: str, user: str, schema: dict, effort: str) -> dict:
        start = time.perf_counter()
        try:
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                thinking={"type": "adaptive"},
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            )
        finally:
            self.seconds += time.perf_counter() - start
        self.calls += 1
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined this request.")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Claude's reply was cut off (max_tokens).")
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)


NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}

CANDIDATES_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "diagnosis": {"type": "string"},
                    "icd10_code": {"type": "string"},
                    "status": {"type": "string", "enum": ["documented_not_coded", "needs_query"]},
                    "replaces_code": NULLABLE_STRING,
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"quote": {"type": "string"}, "why": {"type": "string"}},
                            "required": ["quote", "why"],
                            "additionalProperties": False,
                        },
                    },
                    "guideline_sections": {"type": "array", "items": {"type": "string"}},
                    "reasoning": {"type": "string"},
                },
                "required": ["diagnosis", "icd10_code", "status", "replaces_code", "evidence",
                             "guideline_sections", "reasoning"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["candidates", "notes"],
    "additionalProperties": False,
}

QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "clinical_indicators": {"type": "array", "items": {"type": "string"}},
        "question": {"type": "string"},
        "query_type": {"type": "string", "enum": ["multiple_choice", "open_ended"]},
        "options": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "clinical_indicators", "question", "query_type", "options"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def current_codes(case: dict) -> list[dict]:
    t = tables()
    rows = []
    for i, code in enumerate([case["principal"], *case["secondary"]]):
        level = None if i == 0 else t.effective_level(code, case["principal"])
        rows.append({
            "code": dotted(code),
            "role": "principal" if i == 0 else "secondary",
            "description": t.describe(code),
            "valid": t.is_valid(code),
            "level": level or ("-" if i == 0 else "non-CC"),
            "excluded_by_principal": i > 0 and t.excluded_by(code, case["principal"]),
        })
    return rows


def guideline_context(summary: str) -> list[dict]:
    """Core sections every review needs, plus the sections closest to the chart (RAG)."""
    core = reference.core_sections()
    core_text = {c["text"] for c in core}
    queries = [ln for ln in summary.splitlines() if len(ln.split()) >= 6]
    related = [s for s in reference.search(queries, per_query=2, limit=8) if s["text"] not in core_text]
    return core + related


def find_candidates(claude: Claude, case: dict, codes: list[dict], sections: list[dict]) -> dict:
    code_lines = "\n".join(
        f"- {c['role']}: {c['code']} {c['description']}" for c in codes)
    user = (
        f"<guidelines>\n{reference.format_sections(sections)}\n</guidelines>\n\n"
        f"<current_codes>\n{code_lines}\n</current_codes>\n\n"
        f"<discharge_summary>\n{case['discharge_summary']}\n</discharge_summary>"
    )
    return claude.json(prompt("find-candidates.txt"), user, CANDIDATES_SCHEMA, effort="high")


def draft_query(claude: Claude, case: dict, cand: dict) -> dict:
    """Draft, check in code, and revise at most once. The drafter is never told the
    code or its CC/MCC status, so the query can't mention them."""
    indicators = "\n".join(f'- "{e["quote"]}"' for e in cand["evidence"])
    base = (
        f"<query_rules>\n{prompt('query-rules.md')}\n</query_rules>\n\n"
        f"<discharge_summary>\n{case['discharge_summary']}\n</discharge_summary>\n\n"
        f"<clinical_question>\nThe CDI reviewer is considering whether the indicators below "
        f"reflect {cand['diagnosis']}. Other conditions may also fit; list every clinically "
        f"reasonable option.\n</clinical_question>\n\n"
        f"<indicators>\n{indicators}\n</indicators>"
    )
    system = prompt("draft-query.txt")
    query = claude.json(system, base, QUERY_SCHEMA, effort="medium")
    problems = query_problems(query, case["discharge_summary"])
    revisions = 0
    while problems and revisions < MAX_REVISIONS:
        revisions += 1
        fix = (
            f"{base}\n\n<previous_draft>\n{json.dumps(query, indent=2)}\n</previous_draft>\n\n"
            f"<problems>\n" + "\n".join(f"- {p}" for p in problems) + "\n</problems>\n\n"
            "Rewrite the query so it fixes every problem and still follows all the rules."
        )
        query = claude.json(system, fix, QUERY_SCHEMA, effort="medium")
        problems = query_problems(query, case["discharge_summary"])
    return {"query": query, "revisions": revisions, "compliance_problems": problems,
            "passed_compliance_check": not problems}


def run(case: dict, claude: Claude) -> dict:
    codes = current_codes(case)
    t = tables()
    principal, secondary = case["principal"], case["secondary"]
    sections = guideline_context(case["discharge_summary"])
    found = find_candidates(claude, case, codes, sections)
    kept, dropped = review_candidates(found["candidates"], principal, secondary,
                                      case["discharge_summary"])

    for cand in (c for c in kept if c["status"] == "needs_query"):
        draft = draft_query(claude, case, cand)
        draft["text"] = query_text(draft["query"], case["id"])
        cand["query_draft"] = draft

    return {
        "case_id": case["id"],
        "fiscal_year": reference.FISCAL_YEAR,
        "current_codes": codes,
        "invalid_codes": [c["code"] for c in codes if not c["valid"]],
        "case_level": t.case_level(principal, secondary) or "none",
        "guideline_sections_used": list(dict.fromkeys(s["label"] for s in sections)),
        "coding_opportunities": [c for c in kept if c["status"] == "documented_not_coded"],
        "query_candidates": [c for c in kept if c["status"] == "needs_query"],
        "dropped_candidates": dropped,
        "reviewer_notes": found.get("notes", ""),
        "needs_specialist_review": True,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def report(result: dict, totals: dict) -> str:
    out = [f"CDI review: {result['case_id']}   ({result['fiscal_year']}, model {MODEL})", ""]
    out.append("Current codes:")
    for c in result["current_codes"]:
        flag = "" if c["valid"] else "  <-- NOT A VALID CODE"
        excl = " (excluded by principal)" if c["excluded_by_principal"] else ""
        out.append(f"  {c['role']:9} {c['code']:8} {c['level']:6}{excl} {c['description']}{flag}")
    out.append(f"Case CC/MCC level now: {result['case_level']}")
    out.append(f"Guideline sections given to Claude: {', '.join(result['guideline_sections_used'])}")

    def candidate_lines(c):
        lines = [f"  {c['diagnosis']} -> {c['icd10_code']} {c['description']} [{c['cms_level']}]"]
        if c.get("replaces_code"):
            lines.append(f"    would replace {dotted(c['replaces_code'])}")
        lines.append(f"    case level {c['case_level_now']} -> {c['case_level_if_added']}"
                     + ("" if c["raises_case_level"] else " (no change)"))
        lines += [f"    evidence: \"{e['quote']}\"" for e in c["evidence"]]
        if c.get("guideline_sections"):
            lines.append(f"    guidelines: {', '.join(c['guideline_sections'])}")
        lines.append(f"    why: {c['reasoning']}")
        return lines

    out += ["", f"Coding opportunities - documented but not coded ({len(result['coding_opportunities'])}):"]
    for c in result["coding_opportunities"]:
        out += candidate_lines(c)
    if not result["coding_opportunities"]:
        out.append("  none")

    out += ["", f"Query candidates ({len(result['query_candidates'])}):"]
    for c in result["query_candidates"]:
        out += candidate_lines(c)
        d = c["query_draft"]
        status = "passed" if d["passed_compliance_check"] else "FAILED - fix before sending"
        out.append(f"    compliance check: {status} (revisions: {d['revisions']})")
        out += [f"      problem: {p}" for p in d["compliance_problems"]]
        out += ["", *("    | " + ln for ln in d["text"].splitlines()), ""]
    if not result["query_candidates"]:
        out.append("  none")

    out += ["", f"Dropped by the code checks ({len(result['dropped_candidates'])}):"]
    for c in result["dropped_candidates"]:
        out.append(f"  {c['diagnosis']} ({c['icd10_code']}): {c['dropped_because']}")
    if not result["dropped_candidates"]:
        out.append("  none")

    if result["reviewer_notes"]:
        out += ["", f"Notes from the review: {result['reviewer_notes']}"]
    out += ["", "Every draft needs CDI specialist review. Nothing has been sent.",
            f"Claude calls: {totals['calls']}, input tokens: {totals['input_tokens']:,}, "
            f"output tokens: {totals['output_tokens']:,}, seconds: {totals['seconds']:.1f}"]
    return "\n".join(out)


def save_drafts(result: dict) -> list[Path]:
    paths = []
    DRAFTS.mkdir(parents=True, exist_ok=True)
    for n, c in enumerate(result["query_candidates"], start=1):
        path = DRAFTS / f"{result['case_id']}-query-{n}.txt"
        path.write_text(c["query_draft"]["text"] + "\n", encoding="utf-8")
        paths.append(path)
    return paths


def write_log(case: dict, output, claude: Claude):
    RUNS.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": MODEL,
        "input": {k: case[k] for k in ("id", "source", "principal", "secondary", "discharge_summary")},
        "output": output,
        "input_tokens": claude.input_tokens,
        "output_tokens": claude.output_tokens,
        "seconds": round(claude.seconds, 2),
    }
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def review(case: dict, client: anthropic.Anthropic | None = None) -> tuple[dict, dict]:
    """Run one request and log it (also when it fails). Returns (result, totals)."""
    claude = Claude(client or anthropic.Anthropic(api_key=load_api_key()))
    output = None
    try:
        output = run(case, claude)
        return output, totals_of(claude)
    except Exception as e:
        output = {"error": f"{type(e).__name__}: {e}"}
        raise
    finally:
        write_log(case, output, claude)


def totals_of(claude: Claude) -> dict:
    return {"calls": claude.calls, "input_tokens": claude.input_tokens,
            "output_tokens": claude.output_tokens, "seconds": claude.seconds}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="CDI CC/MCC review and physician query drafts")
    parser.add_argument("--case", type=Path, help="a case file (JSON) from capstone/data/cases")
    parser.add_argument("--summary", type=Path, help="a discharge summary text file")
    parser.add_argument("--codes", help='current codes, principal first: "J18.9,E11.9,I10"')
    parser.add_argument("--save", type=Path, help="also write the printed report to this file")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args()

    if args.case:
        case = load_case(args.case)
    elif args.summary and args.codes:
        case = case_from_args(args.summary, args.codes)
    else:
        parser.error("give --case, or --summary with --codes")

    try:
        result, totals = review(case)
    except anthropic.AuthenticationError:
        sys.exit("The API key was rejected (401).")
    except anthropic.RateLimitError:
        sys.exit("Rate limited by the API (429). Try again shortly.")
    except anthropic.APIStatusError as e:
        sys.exit(f"API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        sys.exit("Could not connect to the Anthropic API.")
    except RuntimeError as e:
        sys.exit(str(e))

    text = json.dumps(result, indent=2, ensure_ascii=False) if args.json else report(result, totals)
    print(text)
    drafts = save_drafts(result)
    if drafts:
        print(f"\nQuery drafts saved: {', '.join(str(p.relative_to(HERE.parent)) for p in drafts)}")
    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(text + "\n", encoding="utf-8")
        print(f"Saved to {args.save}")


if __name__ == "__main__":
    main()
