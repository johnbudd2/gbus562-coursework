"""The four Brookfield specialists, shared by both coordinators.

Each specialist reads its system prompt from week-05/brookfield-bikes/prompts
on every run, so you can edit a prompt between runs without touching code.

- run_analyst(email): an agent. It uses the tools of supply_mcp_server.py
  (get_part, get_orders_for_part, check_coverage), stops after 10 tool calls,
  and returns its brief as a dict (the JSON the analyst prompt asks for).
- write_supplier_reply(email, brief): one model call. Customer names are
  removed from the brief in code before the writer sees it.
- write_customer_notice(order): one model call, given only that one order's
  facts (built in code from orders.csv, never from other customers' rows).
- review(draft, facts, recipient): one model call. Returns APPROVE or REVISE
  with the reviewer's reasons.

The review step (prepare_supplier_reply, prepare_customer_notice) runs a writer,
then the reviewer, and lets the writer revise at most 2 times. It then checks
in code that a supplier draft names no customer and a customer draft names no
supplier (or other customer), and saves the draft in week-05/drafts. A draft is
marked for a person if the reviewer never approved it, the name check failed,
or its order has a late penalty. Nothing is ever sent.

Every handoff between the coordinator and a specialist is printed, as are the
tokens each specialist used (see print_token_report).

The API key is read at runtime from API-Key.txt (or course_API.txt) in the
folder above the repository. It is never printed or written to a file.
"""

import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path

import anthropic
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DATA_DIR = HERE / "brookfield-bikes"
PROMPTS_DIR = DATA_DIR / "prompts"
DRAFTS_DIR = HERE / "drafts"
SERVER = HERE / "supply_mcp_server.py"
KEY_FILES = [REPO.parent / "API-Key.txt", REPO.parent / "course_API.txt"]

MODEL = "claude-sonnet-5"
MAX_TOKENS = 16000
MAX_TOOL_CALLS = 10
MAX_REVISIONS = 2
PREVIEW_CHARS = 300

NO_REPLY = "NO REPLY NEEDED"
BRIEF_FIELDS = ("part_id", "what_changed", "orders_at_risk", "options",
                "decisions_for_a_person", "supplier_questions")

# Display names for printing handoffs and tokens.
ANALYST = "supply analyst"
SUPPLIER_WRITER = "supplier writer"
CUSTOMER_WRITER = "customer writer"
REVIEWER = "reviewer"


class SpecialistError(Exception):
    """A specialist could not finish (API error, refusal, unreadable output)."""


# ---------------------------------------------------------------------------
# Printing: handoffs, previews, and the token ledger
# ---------------------------------------------------------------------------

def preview(text, limit=PREVIEW_CHARS):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "..."


def handoff(sender, receiver, what, payload=None):
    """Print one handoff, e.g.  >> coordinator -> supply analyst: the email"""
    print(f"\n>> HANDOFF  {sender} -> {receiver}: {what}")
    if payload is not None:
        print(f"   {preview(payload)}")


class TokenLedger:
    """Adds up the tokens each specialist uses across all its model calls."""

    def __init__(self):
        self.rows = {}

    def add(self, who, usage):
        tokens_in = (usage.input_tokens
                     + (usage.cache_read_input_tokens or 0)
                     + (usage.cache_creation_input_tokens or 0))
        row = self.rows.setdefault(who, {"calls": 0, "input": 0, "output": 0})
        row["calls"] += 1
        row["input"] += tokens_in
        row["output"] += usage.output_tokens
        print(f"   [{who}] tokens: {tokens_in:,} in, {usage.output_tokens:,} out")

    def total(self):
        return (sum(r["input"] for r in self.rows.values()),
                sum(r["output"] for r in self.rows.values()))

    def reset(self):
        self.rows.clear()


TOKENS = TokenLedger()


def print_token_report(ledger=TOKENS):
    print("\nTokens by specialist")
    print(f"  {'specialist':<18}{'calls':>6}{'input':>10}{'output':>10}")
    for who, r in ledger.rows.items():
        print(f"  {who:<18}{r['calls']:>6}{r['input']:>10,}{r['output']:>10,}")
    tin, tout = ledger.total()
    print(f"  {'total':<18}{'':>6}{tin:>10,}{tout:>10,}")


# ---------------------------------------------------------------------------
# API key, prompts, data files, emails
# ---------------------------------------------------------------------------

def load_api_key():
    for path in KEY_FILES:
        if path.is_file():
            key = path.read_text(encoding="utf-8").strip()
            if key:
                return key
    names = " or ".join(str(p) for p in KEY_FILES)
    raise SpecialistError(f"No API key found. Put it in {names}.")


_client = None


def get_client():
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=load_api_key())
    return _client


def load_prompt(name):
    """Read a system prompt fresh on every run, e.g. load_prompt('analyst')."""
    return (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


def read_csv(name):
    with (DATA_DIR / name).open(newline="", encoding="utf-8-sig") as f:
        return [{k.strip(): (v or "").strip() for k, v in row.items() if k}
                for row in csv.DictReader(f)]


@dataclass
class Email:
    path: Path
    sender: str
    sender_name: str
    subject: str
    body: str

    def as_text(self):
        who = f"{self.sender_name} <{self.sender}>" if self.sender_name else self.sender
        return f"From: {who}\nSubject: {self.subject}\n\n{self.body}"


def load_email(path):
    """Read one .eml file into an Email."""
    path = Path(path).resolve()
    with path.open("rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)
    name, sender = parseaddr(str(msg.get("From", "")))
    if "@" not in sender:
        raise SpecialistError(f"Could not read a sender address from {path}.")
    part = msg.get_body(preferencelist=("plain",))
    body = part.get_content().strip() if part else ""
    return Email(path, sender, name, str(msg.get("Subject", "")).strip(), body)


# ---------------------------------------------------------------------------
# Names: removing customer names, and checking drafts for names
# ---------------------------------------------------------------------------

CORPORATE_WORDS = {"co", "co.", "inc", "inc.", "llc", "company", "corp", "corp.",
                   "supply", "systems", "&"}
# First words too common to count as a name on their own.
GENERIC_WORDS = {"city", "main", "blue", "pine", "the", "bike", "bikes", "street"}


def name_variants(name, contact_name="", contact_email=""):
    """Ways a draft might name a company: full name, name without 'Co.' or
    'Supply', its distinctive first word, its contact person, and its email."""
    variants = {name}
    words = name.split()
    trimmed = list(words)
    while trimmed and trimmed[-1].lower() in CORPORATE_WORDS:
        trimmed.pop()
    if len(trimmed) >= 1:
        variants.add(" ".join(trimmed))
    if len(words) > 2 and words[0].lower() in ("city", "town", "county") \
            and words[1].lower() == "of":
        variants.add(words[2])                      # City of Ashford -> Ashford
    elif len(words) > 1 and words[0].lower() in GENERIC_WORDS:
        variants.add(" ".join(words[:2]))           # Blue Ridge, Pine Valley
    elif len(words) > 1 and len(words[0]) >= 4:
        variants.add(words[0])                      # Riverside, Northgate
    if contact_name:
        variants.add(contact_name)
    if contact_email:
        variants.add(contact_email)
        variants.add(contact_email.split("@")[1].split(".")[0])  # e.g. riversidecycles
    return {v for v in variants if v}


def customer_names():
    """{customer: variants} for every customer in orders.csv."""
    out = {}
    for r in read_csv("orders.csv"):
        out.setdefault(r["customer"], set()).update(
            name_variants(r["customer"], contact_email=r.get("contact_email", "")))
    return out


def supplier_names():
    """{supplier: variants} for every supplier in suppliers.csv and parts.csv."""
    out = {}
    for r in read_csv("suppliers.csv"):
        out.setdefault(r["supplier"], set()).update(name_variants(
            r["supplier"], r.get("contact_name", ""), r.get("contact_email", "")))
    for r in read_csv("parts.csv"):
        out.setdefault(r["supplier"], set()).update(name_variants(r["supplier"]))
    return out


def _pattern(variant):
    return re.compile(r"(?<![\w@.])" + re.escape(variant) + r"(?![\w@])", re.IGNORECASE)


def find_names(text, names):
    """Return the names (keys of `names`) that appear in text, with what matched."""
    found = {}
    for name, variants in names.items():
        for v in sorted(variants, key=len, reverse=True):
            if _pattern(v).search(text):
                found[name] = v
                break
    return found


def scrub_text(text, names, replacement):
    for variants in names.values():
        for v in sorted(variants, key=len, reverse=True):
            text = _pattern(v).sub(replacement, text)
    return text


PENALTY_PHRASE = re.compile(
    r"[\s,;]*(?:with|without|and|but|which|that)?\s*(?:has|carries|carrying|"
    r"having)?\s*(?:a|no|any)?\s*late[- ]penalt\w*(?:\s+(?:clause|terms?))?",
    re.IGNORECASE)
PENALTY_WORD = re.compile(r"penalt", re.IGNORECASE)


def remove_customer_names(brief):
    """A copy of the brief for the supplier side: every customer name and
    contact removed, and late-penalty details (customer contract terms) too."""
    names = customer_names()

    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()
                    if k not in ("customer", "contact_email", "late_penalty")}
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, str):
            return PENALTY_PHRASE.sub("", scrub_text(value, names, "a customer"))
        return value

    return clean(brief)


# ---------------------------------------------------------------------------
# One model call (shared by every specialist)
# ---------------------------------------------------------------------------

async def call_model(who, system, messages, tools=None, tool_choice=None):
    """Make one Messages API call for a specialist and record its tokens."""
    kwargs = {"model": MODEL, "max_tokens": MAX_TOKENS, "system": system,
              "messages": messages}
    if tools:
        kwargs["tools"] = tools
    if tool_choice:
        kwargs["tool_choice"] = tool_choice
    try:
        response = await get_client().messages.create(**kwargs)
    except anthropic.AuthenticationError:
        raise SpecialistError("The API rejected the key (authentication error).") from None
    except anthropic.RateLimitError:
        raise SpecialistError("Rate limited by the API; try again shortly.") from None
    except anthropic.APIStatusError as e:
        raise SpecialistError(f"API error {e.status_code}: {e.message}") from None
    except anthropic.APIConnectionError:
        raise SpecialistError("Could not connect to the Anthropic API.") from None

    TOKENS.add(who, response.usage)
    if response.stop_reason == "refusal":
        raise SpecialistError(f"The {who} declined the request (refusal).")
    if response.stop_reason == "max_tokens":
        raise SpecialistError(f"The {who}'s answer hit the max_tokens limit.")
    return response


def text_of(response):
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


async def one_call(who, prompt_name, user_text):
    response = await call_model(who, load_prompt(prompt_name),
                                [{"role": "user", "content": user_text}])
    text = text_of(response)
    if not text:
        raise SpecialistError(f"The {who} returned no text.")
    return text


# ---------------------------------------------------------------------------
# Specialist 1: the supply analyst (an agent with the MCP tools)
# ---------------------------------------------------------------------------

def parse_brief(text):
    """Pull the JSON brief out of the analyst's final answer and check its fields."""
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = fenced[::-1] + ([text[text.find("{"):text.rfind("}") + 1]]
                                 if "{" in text else [])
    for candidate in candidates:
        try:
            brief = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(brief, dict):
            missing = [f for f in BRIEF_FIELDS if f not in brief]
            if missing:
                raise SpecialistError(f"The brief is missing: {', '.join(missing)}")
            for f in BRIEF_FIELDS[2:]:
                if not isinstance(brief[f], list):
                    raise SpecialistError(f"The brief's {f} is not a list.")
            return brief
    raise SpecialistError("The analyst's answer had no JSON brief.")


async def call_mcp_tool(session, name, arguments):
    """Run one MCP tool. Returns (result text, is_error)."""
    try:
        result = await session.call_tool(name, arguments)
    except Exception as e:  # server crashed or protocol error
        return f"Error running {name}: {e}", True
    text = "\n".join(getattr(c, "text", "") for c in (result.content or []))
    return text or "(no output)", bool(getattr(result, "is_error", False))


async def run_analyst(email, sender="coordinator"):
    """Work out how the email affects customer orders. Returns the brief (dict)."""
    handoff(sender, ANALYST, f"supplier email {email.path.name}",
            f"{email.subject} | {email.body}")
    system = load_prompt("analyst")
    server = StdioServerParameters(
        command=sys.executable, args=[str(SERVER), "--data", str(DATA_DIR)],
        cwd=str(REPO))

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = (await session.list_tools()).tools
            tools = [{"name": t.name, "description": t.description or "",
                      "input_schema": t.input_schema} for t in mcp_tools]
            names = {t["name"] for t in tools}
            print(f"   [{ANALYST}] tools: {', '.join(sorted(names))}")

            messages = [{"role": "user", "content": (
                f"Today's date: {datetime.now():%Y-%m-%d}\n\n"
                "Work out how this supplier email affects Brookfield's customer "
                "orders, then give your brief as JSON.\n\n"
                f"<email>\n{email.as_text()}\n</email>")}]
            tool_calls = 0
            out_of_calls = False

            while True:
                response = await call_model(
                    ANALYST, system, messages, tools,
                    tool_choice={"type": "none"} if out_of_calls else None)
                messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason != "tool_use":
                    text = text_of(response)
                    try:
                        brief = parse_brief(text)
                        break
                    except SpecialistError as e:
                        if out_of_calls or "JSON" not in str(e):
                            raise
                        # One chance to restate the answer as the JSON brief.
                        print(f"   [{ANALYST}] no JSON brief yet; asking for it")
                        out_of_calls = True
                        messages.append({"role": "user", "content":
                                         "Give your brief now as JSON only, with "
                                         "exactly the fields your instructions list."})
                        continue

                results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    if tool_calls >= MAX_TOOL_CALLS:
                        text, is_error = (f"Tool limit of {MAX_TOOL_CALLS} calls reached. "
                                          "Write your brief now from what you have."), True
                        out_of_calls = True
                    else:
                        tool_calls += 1
                        print(f"   [{ANALYST}] tool {tool_calls}: {block.name} "
                              f"{json.dumps(block.input, ensure_ascii=False)}")
                        if block.name in names:
                            text, is_error = await call_mcp_tool(session, block.name,
                                                                 block.input)
                        else:
                            text, is_error = f"Unknown tool: {block.name}", True
                        print(f"      {'error' if is_error else 'result'}: {preview(text, 160)}")
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": text, "is_error": is_error})
                if tool_calls >= MAX_TOOL_CALLS and not out_of_calls:
                    out_of_calls = True
                    print(f"   [{ANALYST}] reached {MAX_TOOL_CALLS} tool calls; "
                          "no more tools")
                messages.append({"role": "user", "content": results})

    print(f"   [{ANALYST}] tool calls: {tool_calls}")
    handoff(ANALYST, sender,
            f"brief ({len(brief['orders_at_risk'])} order(s) at risk, "
            f"{len(brief['options'])} option(s), "
            f"{len(brief['decisions_for_a_person'])} decision(s) for a person)",
            json.dumps(brief, ensure_ascii=False))
    return brief


# ---------------------------------------------------------------------------
# Specialists 2-4: the writers and the reviewer (one model call each)
# ---------------------------------------------------------------------------

def revision_block(revision):
    """Text asking a writer to fix a draft. revision = (previous draft, notes)."""
    if not revision:
        return ""
    previous, notes = revision
    return (f"\n\n<previous_draft>\n{previous}\n</previous_draft>\n"
            f"<reviewer_notes>\n{notes}\n</reviewer_notes>\n"
            "Revise the previous draft so it fixes every point in the reviewer's "
            "notes, and change nothing else.")


async def write_supplier_reply(email, brief, revision=None):
    """Draft a reply to the supplier. Customer names are removed from the brief first."""
    clean_brief = remove_customer_names(brief)
    user = (f"<supplier_email>\n{email.as_text()}\n</supplier_email>\n"
            f"<analyst_brief>\n{json.dumps(clean_brief, indent=2, ensure_ascii=False)}"
            "\n</analyst_brief>\n\n"
            "Write the reply. Answer with only the email text, starting with the "
            f"greeting, or with only {NO_REPLY}." + revision_block(revision))
    return await one_call(SUPPLIER_WRITER, "supplier-writer", user)


async def write_customer_notice(order, revision=None):
    """Draft a notice to one customer, from that one order's facts only."""
    user = (f"<order_facts>\n{json.dumps(order, indent=2, ensure_ascii=False)}\n"
            "</order_facts>\n\nWrite the notice. Answer with only the email text, "
            "starting with the greeting." + revision_block(revision))
    return await one_call(CUSTOMER_WRITER, "customer-writer", user)


async def review(draft, facts, recipient):
    """Review one draft. Returns (verdict, full answer); verdict is APPROVE or REVISE."""
    user = (f"<recipient>{recipient}</recipient>\n"
            f"<facts>\n{facts}\n</facts>\n"
            f"<draft>\n{draft}\n</draft>")
    answer = await one_call(REVIEWER, "reviewer", user)
    first = next((line.strip().strip("*#: ").upper()
                  for line in answer.splitlines() if line.strip()), "")
    if first.startswith("APPROVE"):
        return "APPROVE", answer
    if first.startswith("REVISE"):
        return "REVISE", answer
    return "REVISE", ("Your answer must start with APPROVE or REVISE.\n" + answer)


# ---------------------------------------------------------------------------
# The review step: write, review, revise (at most twice), check names, save
# ---------------------------------------------------------------------------

@dataclass
class DraftResult:
    recipient: str            # "supplier" or "customer"
    to: str
    subject: str
    text: str
    verdict: str              # final reviewer verdict
    revisions: int
    reviewer_notes: str
    names_found: dict = field(default_factory=dict)
    order_id: str = ""
    late_penalty: bool = False
    needs_person: bool = False
    reasons: list = field(default_factory=list)
    path: Path = None


async def write_and_review(writer_name, write, recipient, facts_text, first_draft=None,
                           sender="coordinator"):
    """Run a writer, then the reviewer; revise at most MAX_REVISIONS times.

    write(revision) makes one draft; pass first_draft if it is already written.
    Returns (draft, verdict, revisions, notes).
    """
    draft = first_draft if first_draft is not None else await write(None)
    revisions = 0
    while True:
        handoff(writer_name, REVIEWER, f"draft for the {recipient}"
                + (f" (revision {revisions})" if revisions else ""), draft)
        verdict, notes = await review(draft, facts_text, recipient)
        if verdict == "APPROVE" or revisions >= MAX_REVISIONS:
            break
        revisions += 1
        handoff(REVIEWER, writer_name, f"REVISE (revision {revisions} of "
                f"{MAX_REVISIONS})", notes)
        draft = await write((draft, notes))
    handoff(REVIEWER, sender, f"{verdict} after {revisions} revision(s)", notes)
    return draft, verdict, revisions, notes


def finish_draft(result, run_dir, filename):
    """Decide whether a person must look at the draft, then save it."""
    if result.verdict != "APPROVE":
        result.reasons.append(f"the reviewer still says REVISE after "
                              f"{result.revisions} revision(s)")
    if result.names_found:
        kind = "customer" if result.recipient == "supplier" else "supplier or other customer"
        found = ", ".join(f"{n} (matched '{v}')" for n, v in result.names_found.items())
        result.reasons.append(f"it names a {kind}: {found}")
    if PENALTY_WORD.search(result.text):
        result.reasons.append("it mentions a late penalty (customer contract terms)")
    if result.late_penalty:
        result.reasons.append(f"order {result.order_id} has a late penalty")
    result.needs_person = bool(result.reasons)
    result.path = save_draft(result, run_dir, filename)
    shown = (result.path.relative_to(REPO) if result.path.is_relative_to(REPO)
             else result.path)
    print(f"   saved {shown}"
          + (f"  ** NEEDS A PERSON: {'; '.join(result.reasons)}" if result.needs_person
             else ""))
    return result


def save_draft(result, run_dir, filename):
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / filename
    names = ("none found" if not result.names_found
             else "FAILED - " + ", ".join(result.names_found))
    lines = [
        f"To: {result.to}",
        f"Subject: {result.subject}",
        "Status: not sent",
        f"Reviewer: {result.verdict} after {result.revisions} revision(s)",
        f"Name check: {names}",
        f"Needs a person: {'yes - ' + '; '.join(result.reasons) if result.needs_person else 'no'}",
        "",
        "DRAFT",
        result.text.strip(),
        "",
        "REVIEWER'S LAST ANSWER",
        result.reviewer_notes.strip(),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def new_run_dir(email, coordinator):
    """A fresh folder in week-05/drafts for one coordinator run on one email."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return DRAFTS_DIR / f"{email.path.stem}-{coordinator}-{stamp}"


def reply_subject(subject):
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


async def prepare_supplier_reply(email, brief, run_dir, sender="coordinator"):
    """Supplier reply through the review step. Returns a DraftResult, or None
    if the writer says no reply is needed."""
    clean_brief = remove_customer_names(brief)
    handoff(sender, SUPPLIER_WRITER, "supplier email + brief (customer names removed)",
            json.dumps(clean_brief, ensure_ascii=False))

    first = await write_supplier_reply(email, brief)
    if first.strip().upper().rstrip(".") == NO_REPLY:
        handoff(SUPPLIER_WRITER, sender, NO_REPLY)
        return None

    async def write(revision):
        return await write_supplier_reply(email, brief, revision)

    facts = (f"<supplier_email>\n{email.as_text()}\n</supplier_email>\n"
             f"<analyst_brief>\n{json.dumps(clean_brief, indent=2, ensure_ascii=False)}"
             "\n</analyst_brief>")
    text, verdict, revisions, notes = await write_and_review(
        SUPPLIER_WRITER, write, "supplier", facts, first_draft=first, sender=sender)
    result = DraftResult(
        recipient="supplier", to=email.sender, subject=reply_subject(email.subject),
        text=text, verdict=verdict, revisions=revisions, reviewer_notes=notes,
        names_found=find_names(text, customer_names()))
    return finish_draft(result, run_dir, "supplier-reply.txt")


def customer_order_facts(order_at_risk):
    """The facts for one customer notice, built from orders.csv and the brief's
    row for that order. Returns (facts for the writer, orders.csv row)."""
    order_id = str(order_at_risk.get("order_id", "")).strip()
    row = next((r for r in read_csv("orders.csv") if r["order_id"] == order_id), None)
    if row is None:
        raise SpecialistError(f"Order {order_id!r} is not in orders.csv.")
    ship_day = int(row["ship_in_days"])
    ready_day = order_at_risk.get("ready_day")
    facts = {
        "order_id": order_id,
        "customer": row["customer"],
        "model": row["model"],
        "quantity": int(row["qty"]),
        "planned_ship_day": ship_day,
        "expected_ship_day": ready_day,
        "days_later_than_planned": (ready_day - ship_day
                                    if isinstance(ready_day, int) else "not yet known"),
        "days_are_counted_from": f"today, {datetime.now():%Y-%m-%d}",
    }
    return facts, row


async def prepare_customer_notice(order_at_risk, run_dir, sender="coordinator"):
    """Customer notice for one order at risk, through the review step."""
    facts, row = customer_order_facts(order_at_risk)
    handoff(sender, CUSTOMER_WRITER, f"facts for order {facts['order_id']} only",
            json.dumps(facts, ensure_ascii=False))

    async def write(revision):
        return await write_customer_notice(facts, revision)

    text, verdict, revisions, notes = await write_and_review(
        CUSTOMER_WRITER, write, "customer",
        json.dumps(facts, indent=2, ensure_ascii=False), sender=sender)

    # A customer draft must name no supplier and no other customer.
    others = {n: v for n, v in customer_names().items() if n != row["customer"]}
    found = find_names(text, supplier_names()) | find_names(text, others)
    result = DraftResult(
        recipient="customer", to=row.get("contact_email", ""),
        subject=f"Your Brookfield order {facts['order_id']} - updated ship date",
        text=text, verdict=verdict, revisions=revisions, reviewer_notes=notes,
        names_found=found, order_id=facts["order_id"],
        late_penalty=row.get("late_penalty", "").lower() in ("yes", "y", "true", "1"))
    return finish_draft(result, run_dir, f"customer-{facts['order_id']}.txt")
