"""Checks done in code, never by Claude.

- quote_in_summary: an evidence quote must appear in the discharge summary.
- review_candidates: keep a candidate only if its quotes are real, its code is a
  valid FY 2027 code, it isn't already coded, and the CMS tables make it a CC or
  MCC with this principal diagnosis (after CC exclusions).
- query_problems: the compliance checks for a drafted physician query.
"""

import re
import unicodedata

from reference import dotted, normalize, tables

LEVEL_RANK = {None: 0, "CC": 1, "MCC": 2}


# ---------------------------------------------------------------------------
# Evidence quotes
# ---------------------------------------------------------------------------

def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[‐-―]", "-", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_in_summary(quote: str, summary: str) -> bool:
    """True if the quote appears in the summary (ignoring case, spacing, curly quotes,
    and a trailing period)."""
    q = _plain(quote).rstrip(".")
    return bool(q) and q in _plain(summary)


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

def review_candidates(candidates: list[dict], principal: str, secondary: list[str],
                      summary: str) -> tuple[list[dict], list[dict]]:
    """Split Claude's candidates into (kept, dropped). Adds CMS facts to each."""
    t = tables()
    coded = {normalize(c) for c in [principal, *secondary]}
    current_level = t.case_level(principal, secondary)
    kept, dropped = [], []

    for cand in candidates:
        c = dict(cand)
        code = normalize(c.get("icd10_code", ""))
        c["icd10_code"] = dotted(code)
        c["description"] = t.describe(code)
        c["cms_level"] = t.level_of(code)
        c["excluded_by_principal"] = t.excluded_by(code, principal)
        bad_quotes = [e["quote"] for e in c.get("evidence", [])
                      if not quote_in_summary(e.get("quote", ""), summary)]

        reason = None
        if not c.get("evidence"):
            reason = "no evidence quotes"
        elif bad_quotes:
            reason = f"evidence not found in the summary: {bad_quotes[0]!r}"
        elif not t.is_valid(code):
            reason = f"{c['icd10_code']} is not a valid FY 2027 ICD-10-CM code"
        elif code in coded:
            reason = f"{c['icd10_code']} is already coded"
        elif code == normalize(principal):
            reason = "this is the principal diagnosis"
        elif c["cms_level"] is None:
            reason = f"{c['icd10_code']} is not a CC or MCC (CMS Tables 6I/6J)"
        elif c["excluded_by_principal"]:
            reason = (f"{c['icd10_code']} is a {c['cms_level']}, but the CC exclusion list "
                      f"(Table 6K) makes it a non-CC with principal diagnosis {dotted(principal)}")

        if reason:
            c["dropped_because"] = reason
            dropped.append(c)
            continue

        # Severity the case would have if this candidate were added (or replaced a code).
        replaces = normalize(c.get("replaces_code") or "")
        new_secondary = [s for s in secondary if normalize(s) != replaces] + [code]
        new_level = t.case_level(principal, new_secondary)
        c["case_level_now"] = current_level or "none"
        c["case_level_if_added"] = new_level or "none"
        c["raises_case_level"] = LEVEL_RANK[new_level] > LEVEL_RANK[current_level]
        kept.append(c)

    return kept, dropped


# ---------------------------------------------------------------------------
# Query compliance
# ---------------------------------------------------------------------------

FINANCIAL = re.compile(
    r"\b(reimburse\w*|payments?|pay|paid|revenue|financial\w*|dollars?|"
    r"complications? or comorbidit\w*|case[- ]mix|severity (?:level|of illness)|"
    r"risk of mortality|quality scores?|audits?|denials?)\b|\$", re.I)
# Abbreviations are matched in capitals only, so "500 cc" is not flagged.
FINANCIAL_ABBREV = re.compile(r"\b(DRGs?|MS-DRGs?|CCs?|MCCs?|SOI|ROM|HCCs?)\b")
LEADING = re.compile(
    r"please (?:document|add|include|update|chart|note)|consider documenting|"
    r"(?:in order )?to (?:support|capture)|would you agree|do(?:n't| not) you (?:think|agree)|"
    r"(?:can|could) you confirm that|(?:meets|met) (?:the )?criteria|"
    r"(?:has|had) been diagnosed|for coding purposes", re.I)
ICD_CODE = re.compile(r"\b[A-TV-Z]\d{2}(?:\.\d{1,4}[A-Z]?|\.[0-9A-Z]{1,4})\b")
OTHER = re.compile(r"^other\b", re.I)
UNDETERMINED = re.compile(r"clinically undetermined|unable to determine|undetermined", re.I)


def query_problems(query: dict, summary: str) -> list[str]:
    """Return the compliance problems in a drafted query (empty list = passes)."""
    problems = []
    options = [str(o).strip() for o in query.get("options", [])]
    indicators = [str(i) for i in query.get("clinical_indicators", [])]
    wording = " ".join([query.get("title", ""), query.get("question", ""), *options])

    if m := (FINANCIAL.search(wording) or FINANCIAL_ABBREV.search(wording)):
        problems.append(f"mentions payment or CC/MCC terms: {m.group(0)!r}")
    if m := LEADING.search(wording):
        problems.append(f"leading wording: {m.group(0).strip()!r}")
    if m := ICD_CODE.search(" ".join([wording, *indicators])):
        problems.append(f"contains an ICD-10-CM code: {m.group(0)!r}")

    if not indicators:
        problems.append("no clinical indicators")
    for ind in indicators:
        if not quote_in_summary(ind, summary):
            problems.append(f"clinical indicator not found in the summary: {ind!r}")

    if query.get("query_type") == "multiple_choice":
        clinical = [o for o in options if not OTHER.match(o) and not UNDETERMINED.search(o)]
        if len(clinical) < 2:
            problems.append("multiple choice offers fewer than two clinical options")
        if not any(OTHER.match(o) for o in options):
            problems.append('multiple choice has no "Other (please specify)" option')
        if not any(UNDETERMINED.search(o) for o in options):
            problems.append('multiple choice has no "Clinically undetermined" option')
        if {o.lower().rstrip(".") for o in clinical} <= {"yes", "no"} and clinical:
            problems.append("yes/no question used to ask for a diagnosis")
    elif query.get("query_type") == "open_ended":
        if options:
            problems.append("open-ended query should not list options")
    else:
        problems.append(f"unknown query_type {query.get('query_type')!r}")

    return problems


def query_text(query: dict, case_label: str) -> str:
    """The query as the specialist would send it, assembled in code so every query
    has the same compliant frame."""
    lines = [
        f"PHYSICIAN QUERY - DOCUMENTATION CLARIFICATION (DRAFT FOR CDI REVIEW)",
        f"Patient: {case_label}",
        f"Subject: {query.get('title', '')}",
        "",
        "Clinical indicators from the medical record:",
        *[f"  - \"{i}\"" for i in query.get("clinical_indicators", [])],
        "",
        query.get("question", ""),
    ]
    if query.get("query_type") == "multiple_choice":
        lines += ["", *[f"  [ ] {o}" for o in query.get("options", [])]]
    else:
        lines += ["", "  Response: ______________________________"]
    lines += [
        "",
        "Please respond based on your independent clinical judgment. Any response, including",
        "\"clinically undetermined\", is acceptable. Please record your response in the medical record.",
    ]
    return "\n".join(lines)
