"""Alder Creek service desk assistant.

Sends one customer service request to Claude with the Alder Creek system prompt
(alder-creek/assistant-prompt.txt followed by alder-creek/service-guide.md) and
prints the JSON reply: {"priority", "reply", "needs_person"}.

Usage:
    python week-06/assistant.py --request "Our heat isn't working."

The API key is read at runtime from API-Key.txt in the folder above the repository
(falling back to course_API.txt there, or the ANTHROPIC_API_KEY environment
variable). It is only held in memory and is never written to a file or printed.
"""

import argparse
import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import anthropic

MODEL = "claude-opus-5-5"

HERE = Path(__file__).resolve().parent
ORG_DIR = HERE / "alder-creek"
REPO_DIR = HERE.parent.parent  # week-06/alder-creek-lecture -> repository root
KEY_DIR = REPO_DIR.parent  # folder above the repository
KEY_FILES = ["API-Key.txt", "course_API.txt"]

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "priority": {
            "type": "string",
            "enum": ["Safety", "Emergency", "Urgent", "Routine"],
        },
        "reply": {"type": "string"},
        "needs_person": {"type": "boolean"},
    },
    "required": ["priority", "reply", "needs_person"],
    "additionalProperties": False,
}


def load_api_key() -> str:
    """Read the key from the file above the repo; never write or print it."""
    for name in KEY_FILES:
        path = KEY_DIR / name
        if path.is_file():
            key = path.read_text(encoding="utf-8").strip()
            if key:
                return key
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    sys.exit(
        f"No API key found. Put it in {KEY_DIR / KEY_FILES[0]} "
        "or set ANTHROPIC_API_KEY."
    )


def build_system_prompt() -> str:
    prompt = (ORG_DIR / "assistant-prompt.txt").read_text(encoding="utf-8").strip()
    guide = (ORG_DIR / "service-guide.md").read_text(encoding="utf-8").strip()
    return f"{prompt}\n\n{guide}"


# --- Guardrail 1: safety requests never reach Claude -------------------------

SAFETY_REPLY = (
    "Please leave the building now and call the gas company or 911 from outside. "
    "Someone from Alder Creek will call you right away."
)
SAFETY_PATTERNS = [
    re.compile(r"\bgas\b", re.I),
    re.compile(r"\bcarbon\s+monoxide\b", re.I),
    re.compile(r"\bsmok(e|es|ed|ing|y)\b", re.I),
    re.compile(r"\bburning\s+(smell|odou?r)\b", re.I),
    re.compile(r"\bsmell(s|ed|ing)?\s+(of|like)\s+(something\s+)?burning\b", re.I),
]


def safety_match(request: str) -> str | None:
    """Return the safety word found in the request, or None."""
    for pattern in SAFETY_PATTERNS:
        m = pattern.search(request)
        if m:
            return m.group(0)
    return None


# --- Guardrail 2: hold replies that say things the desk may not --------------

ALLOWED_AMOUNTS = {Decimal("0"), Decimal("89"), Decimal("149"), Decimal("129")}
DOLLAR_RE = re.compile(r"\$\s*(\d[\d,]*(?:\.\d+)?)")
BOOKING_RE = re.compile(r"\b(booked|confirmed)\b", re.I)
CLOCK_RE = re.compile(
    r"\b\d{1,2}:\d{2}\b(\s*[ap]\.?\s?m\b\.?)?"   # 10:00, 2:30 pm
    r"|\b\d{1,2}\s*[ap]\.?\s?m\b\.?"             # 10 am, 6 p.m.
    r"|\b(noon|midnight)\b",
    re.I,
)
# The guide's own fee rule ("evenings after 6 p.m.") is not a promised time.
GUIDE_TIME_RE = re.compile(r"\bafter\s+6\s*p\.?\s?m\b\.?", re.I)
COVERAGE_RE = re.compile(r"\b(covered|under\s+warranty)\b", re.I)


def hold_reasons(reply: str) -> list[str]:
    """Return why a reply must be held for a person (empty list means OK)."""
    reasons = []

    bad_amounts = []
    for m in DOLLAR_RE.finditer(reply):
        try:
            value = Decimal(m.group(1).replace(",", ""))
        except InvalidOperation:
            value = None
        if value not in ALLOWED_AMOUNTS:
            bad_amounts.append(m.group(0))
    if bad_amounts:
        reasons.append(f"dollar amount not in the guide: {', '.join(bad_amounts)}")

    words = sorted({m.group(0).lower() for m in BOOKING_RE.finditer(reply)})
    if words:
        reasons.append(f"says {' / '.join(words)}")

    times = [m.group(0) for m in CLOCK_RE.finditer(GUIDE_TIME_RE.sub("", reply))]
    if times:
        reasons.append(f"gives a clock time: {', '.join(times)}")

    words = sorted({m.group(0).lower() for m in COVERAGE_RE.finditer(reply)})
    if words:
        reasons.append(f"talks about coverage: {' / '.join(words)}")

    return reasons


def ask(request: str, client: anthropic.Anthropic | None = None) -> dict:
    """Return the JSON reply for one service request, with both guardrails applied."""
    # Guardrail 1: don't call Claude at all for a safety request.
    if safety_match(request):
        return {"priority": "Safety", "reply": SAFETY_REPLY, "needs_person": True}

    result = ask_claude(request, client)

    # Guardrail 2: hold the reply for a person if it says something it may not.
    reasons = hold_reasons(str(result.get("reply", "")))
    if reasons:
        result["needs_person"] = True
        result["held_because"] = "; ".join(reasons)
    return result


def ask_claude(request: str, client: anthropic.Anthropic | None = None) -> dict:
    """Send one service request to Claude and return the parsed JSON reply."""
    client = client or anthropic.Anthropic(api_key=load_api_key())
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # if the model declines, the API retries on a fallback model
        system=build_system_prompt(),
        messages=[{"role": "user", "content": request}],
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": REPLY_SCHEMA},
        },
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to answer this request.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("The reply was cut off (max_tokens reached).")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Alder Creek service desk assistant")
    parser.add_argument("--request", required=True, help="the customer's service request")
    args = parser.parse_args()

    try:
        reply = ask(args.request)
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

    print(json.dumps(reply, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
