"""CDI query agent: Claude reviews one synthetic case using three local tools and
drafts compliant physician queries for a CDI specialist to review.

Usage (from the capstone folder):
    python cdi_query.py data/cases/case_03.md [--model claude-sonnet-5]

Tools Claude can call (at most 12 calls per request):
    search_guidelines(query)          top 4 ICD-10-CM guideline sections (Chroma)
    search_codes(term)                up to 10 CC/MCC codes by description keyword
    lookup_code(code, principal_code) description and severity from reference/cc_mcc.csv

Every request appends exactly one JSON line to runs/log.jsonl (also on errors).
The API key is read from API-Key.txt in the folder above the repository (falling back
to course_API.txt there); it is never printed or written.
"""

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic

import reference
from checks import quote_in_summary

HERE = Path(__file__).resolve().parent
KEY_DIR = HERE.parent.parent                     # folder above the repository root
KEY_FILES = ["API-Key.txt", "course_API.txt"]
PROMPTS = HERE / "prompts"
CSV_FILE = HERE / "reference" / "cc_mcc.csv"
LOG_FILE = HERE / "runs" / "log.jsonl"
DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOOL_CALLS = 12

TOOLS = [
    {
        "name": "search_guidelines",
        "description": "Search the ICD-10-CM Official Guidelines for Coding and Reporting (FY 2027). "
                       "Returns the top 4 sections, each with its section identifier (e.g. III.B).",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What to look for, e.g. 'abnormal findings reportable'"}},
            "required": ["query"]},
    },
    {
        "name": "search_codes",
        "description": "Find up to 10 codes on the CMS FY 2027 CC/MCC list whose description contains "
                       "every word of the term (case-insensitive). Use it to find candidate codes.",
        "input_schema": {"type": "object", "properties": {
            "term": {"type": "string", "description": "Keywords, e.g. 'pressure ulcer sacral stage 3'"}},
            "required": ["term"]},
    },
    {
        "name": "lookup_code",
        "description": "Look up one ICD-10-CM code: its description and severity (MCC, CC, or NONE = not on "
                       "the CMS CC/MCC list). Pass principal_code to check the CMS CC exclusion list.",
        "input_schema": {"type": "object", "properties": {
            "code": {"type": "string", "description": "ICD-10-CM code, e.g. E87.1"},
            "principal_code": {"type": "string", "description": "The case's principal diagnosis (optional)"}},
            "required": ["code"]},
    },
]


# --- reference data and tools ------------------------------------------------

def load_cc_mcc() -> dict:
    with CSV_FILE.open(encoding="utf-8", newline="") as f:
        return {reference.normalize(r["code"]): r for r in csv.DictReader(f)}


CC_MCC = load_cc_mcc()


def search_guidelines(query: str) -> list[dict]:
    return [{"section": h["label"], "title": h["title"], "text": h["text"]}
            for h in reference.search([query], per_query=4, limit=4)]


def search_codes(term: str) -> list[dict]:
    words = term.lower().split()
    hits = [r for r in CC_MCC.values() if all(w in r["description"].lower() for w in words)]
    return [{"code": r["code"], "description": r["description"], "severity": r["severity"]}
            for r in hits[:10]]


def lookup_code(code: str, principal_code: str | None = None) -> dict:
    t = reference.tables()
    row = CC_MCC.get(reference.normalize(code))
    result = {
        "code": reference.dotted(code),
        "valid_icd10cm_fy2027": t.is_valid(code),
        "description": row["description"] if row else t.describe(code),
        "severity": row["severity"] if row else "NONE (not on CC/MCC list)",
    }
    if row and principal_code:
        result["excluded_with_principal"] = t.excluded_by(code, principal_code)
        result["principal_code"] = reference.dotted(principal_code)
    return result


TOOL_FUNCTIONS = {"search_guidelines": search_guidelines, "search_codes": search_codes,
                  "lookup_code": lookup_code}


# --- agent loop -----------------------------------------------------------------

def load_api_key() -> str:
    for name in KEY_FILES:
        path = KEY_DIR / name
        if path.is_file() and (key := path.read_text(encoding="utf-8").strip()):
            return key
    raise RuntimeError(f"No API key found in {KEY_DIR / KEY_FILES[0]}.")


def system_prompt() -> str:
    return ((PROMPTS / "system_prompt.md").read_text(encoding="utf-8").strip() + "\n\n"
            + (PROMPTS / "query_standards.md").read_text(encoding="utf-8"))


def run_agent(client, model: str, case_text: str, usage: dict) -> tuple[str, set[str]]:
    """Run the tool loop. Returns (final text, codes looked up with lookup_code)."""
    messages = [{"role": "user", "content": f"<case>\n{case_text}\n</case>\n\nReview this case."}]
    calls, looked_up = 0, set()
    while True:
        response = client.messages.create(
            model=model, max_tokens=16000, system=system_prompt(), tools=TOOLS,
            messages=messages, thinking={"type": "adaptive"},
            cache_control={"type": "ephemeral"},  # the system prompt and history repeat each turn
            tool_choice={"type": "none"} if calls >= MAX_TOOL_CALLS else {"type": "auto"},
        )
        u = response.usage
        usage["input_tokens"] += (u.input_tokens + (u.cache_creation_input_tokens or 0)
                                  + (u.cache_read_input_tokens or 0))
        usage["output_tokens"] += u.output_tokens
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined this request.")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Claude's reply was cut off (max_tokens).")
        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if b.type == "text").strip(), looked_up

        results = []
        for block in (b for b in response.content if b.type == "tool_use"):
            if calls >= MAX_TOOL_CALLS:
                content, is_error = f"Tool limit of {MAX_TOOL_CALLS} calls reached. Write your final answer now.", True
            else:
                calls += 1
                try:
                    out = TOOL_FUNCTIONS[block.name](**block.input)
                    if block.name == "lookup_code":
                        looked_up.add(reference.normalize(block.input["code"]))
                    content, is_error = json.dumps(out), False
                except Exception as e:
                    content, is_error = f"{type(e).__name__}: {e}", True
            print(f"  [tool {calls}/{MAX_TOOL_CALLS}] {block.name}({json.dumps(block.input)})", file=sys.stderr)
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": content, "is_error": is_error})
        messages.append({"role": "user", "content": results})


def check_output(text: str, case_text: str, looked_up: set[str]) -> list[str]:
    """Code checks on Claude's answer: every quote is in the chart, and every code given a
    severity in Findings was looked up in this run."""
    findings = text.split("## Draft queries")[0]
    # Pair every double quote in order (one line at a time), then skip very short ones.
    quotes = [q for line in text.splitlines() for q in re.findall(r'"([^"]*)"', line)]
    warnings = [f'quote not found in the chart: "{q}"'
                for q in quotes if len(q) >= 4 and not quote_in_summary(q, case_text)]
    for line in findings.splitlines():
        if line.strip().startswith("- Candidate code:"):
            m = re.search(r"\b([A-TV-Z]\d{2}(?:\.[0-9A-Z]{1,4})?)\b", line)
            if m and reference.normalize(m.group(1)) not in looked_up:
                warnings.append(f"severity for {m.group(1)} was not verified with lookup_code in this run")
    return warnings


def write_log(model: str, case_text: str, output: str, usage: dict, seconds: float):
    """The only place that writes the log: one JSON line per request."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model,
        "input": case_text,
        "output": output,
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "seconds": round(seconds, 2),
    }
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def review(case_path: Path, model: str = DEFAULT_MODEL, stats: dict | None = None) -> str:
    """Handle one request for a case file (command line, eval.py, app.py's case picker)."""
    return review_text(case_path.read_text(encoding="utf-8"), model, stats)


def review_text(case_text: str, model: str = DEFAULT_MODEL, stats: dict | None = None) -> str:
    """Handle one request: run the agent, check its answer, log, and return the output text.
    If `stats` is given, it is filled with input_tokens, output_tokens, seconds, and
    looked_up (codes passed to lookup_code), e.g. for eval.py."""
    usage = {"input_tokens": 0, "output_tokens": 0}
    looked_up: set[str] = set()
    start = time.perf_counter()
    output = ""
    try:
        client = anthropic.Anthropic(api_key=load_api_key())
        text, looked_up = run_agent(client, model, case_text, usage)
        warnings = check_output(text, case_text, looked_up)
        output = text + ("\n\n## Automated checks\n" + "\n".join(f"- WARNING: {w}" for w in warnings)
                         if warnings else "\n\n## Automated checks\n- All quotes found in the chart; "
                         "every severity verified with lookup_code.")
        return output
    except Exception as e:
        output = f"ERROR: {type(e).__name__}: {e}"
        raise
    finally:
        seconds = time.perf_counter() - start
        write_log(model, case_text, output, usage, seconds)
        if stats is not None:
            stats.update(usage, seconds=seconds, looked_up=looked_up)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="CDI query agent for one synthetic case")
    parser.add_argument("case", type=Path, help="case file, e.g. data/cases/case_03.md")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    try:
        print(review(args.case, args.model))
    except anthropic.APIError as e:
        sys.exit(f"API error: {e}")
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
