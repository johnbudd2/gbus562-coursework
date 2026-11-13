"""HR email agent: drafts a reply to one employee email using Claude's tool use.

It works for any organization folder laid out like week-04/hartwell-hr (the
default) or week-04/ridgeline-hotels: hr-docs/, employees.csv, sample-emails/,
and system_prompt.txt.

For one email file it:
1. Reads the sender's address and subject from the email.
2. Starts hr_mcp_server.py for the organization in employee mode
   (--as-employee <sender>), so get_leave_balance can only ever return the
   sender's own balance.
3. Offers Claude the server's tools plus save_reply, which writes a draft to
   week-04/drafts/. Nothing is ever sent.
4. Runs the tool loop, printing each step, and stops when the draft is saved,
   after 10 tool calls, or on any reply that isn't a tool request.

Usage (from the top of the repository):
    .venv\\Scripts\\python.exe week-04\\agent.py --email week-04\\hartwell-hr\\sample-emails\\03-megan-time-off.eml
    .venv\\Scripts\\python.exe week-04\\agent.py --org ridgeline-hotels --email week-04\\ridgeline-hotels\\sample-emails\\01-jenna-pto.eml --save week-04\\runs\\ridgeline-01.txt

--org takes a folder name inside week-04 or a path to the folder. --save also
writes everything the agent prints to that text file (UTF-8).

The API key is read at runtime from API-Key.txt (or course_API.txt) in the folder
above the repository. It is never printed or written to a file.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path

import anthropic
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from hr_mcp_server import DEFAULT_ORG, resolve_org

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SERVER = HERE / "hr_mcp_server.py"
DRAFTS_DIR = HERE / "drafts"
KEY_FILES = [REPO.parent / "API-Key.txt", REPO.parent / "course_API.txt"]

MODEL = "claude-sonnet-5"
MAX_TOOL_CALLS = 10
PREVIEW_CHARS = 300

SAVE_REPLY_TOOL = {
    "name": "save_reply",
    "description": (
        "Save the finished reply to the employee as a draft for the HR team to "
        "review. This only saves the draft; it never sends anything. Call it "
        "exactly once, when the reply is complete."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reply_text": {
                "type": "string",
                "description": "The full text of the reply to the employee.",
            },
            "needs_human_review": {
                "type": "boolean",
                "description": "true if a person from HR must handle or check this "
                               "email before anything is sent, otherwise false.",
            },
        },
        "required": ["reply_text", "needs_human_review"],
        "additionalProperties": False,
    },
    "strict": True,
}


class Tee:
    """Write to the terminal and to a file at the same time (for --save)."""

    def __init__(self, terminal, file):
        self.terminal, self.file = terminal, file

    def write(self, text):
        self.terminal.write(text)
        self.file.write(text)
        return len(text)

    def flush(self):
        self.terminal.flush()
        self.file.flush()


def load_api_key():
    for path in KEY_FILES:
        if path.is_file():
            key = path.read_text(encoding="utf-8").strip()
            if key:
                return key
    names = " or ".join(str(p) for p in KEY_FILES)
    sys.exit(f"No API key found. Put it in {names}.")


def read_email(path):
    """Return (sender address, subject, body text) from an .eml file."""
    with path.open("rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)
    _, sender = parseaddr(str(msg.get("From", "")))
    if "@" not in sender:
        sys.exit(f"Could not read a sender address from the From: line of {path}.")
    subject = str(msg.get("Subject", "")).strip()
    part = msg.get_body(preferencelist=("plain",))
    body = part.get_content().strip() if part else ""
    return sender, subject, body


def preview(text):
    text = str(text)
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "..."


def review_line(needs_human_review):
    return f"Needs human review: {'true' if needs_human_review else 'false'}"


def save_draft(org_dir, email_path, sender, subject, reply_text, needs_human_review):
    DRAFTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = DRAFTS_DIR / f"{org_dir.name}-{email_path.stem}-{stamp}.txt"
    reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    path.write_text(
        f"To: {sender}\n"
        f"Subject: {reply_subject}\n"
        f"Status: not sent\n"
        f"\nDRAFT\n{reply_text.strip()}\n{review_line(needs_human_review)}\n",
        encoding="utf-8",
    )
    return path


async def call_mcp_tool(session, name, arguments):
    """Run one MCP tool. Returns (result text, is_error)."""
    try:
        result = await session.call_tool(name, arguments)
    except Exception as e:  # server crashed or protocol error
        return f"Error running {name}: {e}", True
    text = "\n".join(getattr(c, "text", "") for c in (result.content or []))
    return text or "(no output)", bool(getattr(result, "is_error", False))


async def run(org_dir, email_path):
    sender, subject, body = read_email(email_path)
    system_prompt = (org_dir / "system_prompt.txt").read_text(encoding="utf-8")
    client = anthropic.AsyncAnthropic(api_key=load_api_key())

    print(f"Sender:  {sender}")
    print(f"Subject: {subject}")
    print(f"Organization: {org_dir.name}")
    print(f"Email file:   {email_path.name}")
    print(f"Model:        {MODEL}\n")

    server = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER), "--org", str(org_dir), "--as-employee", sender],
        cwd=str(REPO),
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = (await session.list_tools()).tools
            mcp_names = {t.name for t in mcp_tools}
            tools = [
                {"name": t.name, "description": t.description or "",
                 "input_schema": t.input_schema}
                for t in mcp_tools
            ] + [SAVE_REPLY_TOOL]
            print("Tools: " + ", ".join(t["name"] for t in tools) + "\n")

            messages = [{
                "role": "user",
                "content": (
                    f"Today's date: {datetime.now():%Y-%m-%d}\n\n"
                    "Prepare a reply to this employee email.\n\n"
                    f"<email>\nFrom: {sender}\nSubject: {subject}\n\n{body}\n</email>"
                ),
            }]
            tokens_in = tokens_out = 0
            tool_calls = 0
            draft_path = None
            draft = None
            stop_reason_text = None

            while stop_reason_text is None:
                try:
                    response = await client.messages.create(
                        model=MODEL,
                        max_tokens=16000,
                        system=system_prompt,
                        tools=tools,
                        messages=messages,
                    )
                except anthropic.AuthenticationError:
                    stop_reason_text = "The API rejected the key (authentication error)."
                    break
                except anthropic.RateLimitError:
                    stop_reason_text = "Rate limited by the API; try again shortly."
                    break
                except anthropic.APIStatusError as e:
                    stop_reason_text = f"API error {e.status_code}: {e.message}"
                    break
                except anthropic.APIConnectionError:
                    stop_reason_text = "Could not connect to the Anthropic API."
                    break

                usage = response.usage
                tokens_in += (usage.input_tokens
                              + (usage.cache_read_input_tokens or 0)
                              + (usage.cache_creation_input_tokens or 0))
                tokens_out += usage.output_tokens

                if response.stop_reason != "tool_use":
                    reasons = {
                        "end_turn": "Claude finished without calling save_reply.",
                        "max_tokens": "Claude's reply hit the max_tokens limit.",
                        "refusal": "Claude declined the request (refusal).",
                    }
                    stop_reason_text = reasons.get(
                        response.stop_reason,
                        f"Claude stopped with stop_reason '{response.stop_reason}'.")
                    text = " ".join(b.text for b in response.content if b.type == "text")
                    if text.strip():
                        print(f"Claude said: {preview(text)}\n")
                    break

                messages.append({"role": "assistant", "content": response.content})
                results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    if tool_calls >= MAX_TOOL_CALLS:
                        stop_reason_text = (f"Reached the limit of {MAX_TOOL_CALLS} "
                                            "tool calls before a draft was saved.")
                        break
                    tool_calls += 1
                    print(f"Step {tool_calls}: {block.name}")
                    print(f"  input:  {json.dumps(block.input, ensure_ascii=False)}")

                    if block.name == "save_reply":
                        draft = block.input
                        draft_path = save_draft(
                            org_dir, email_path, sender, subject,
                            draft["reply_text"], draft["needs_human_review"])
                        text, is_error = f"Draft saved to {draft_path.name}. Not sent.", False
                    elif block.name in mcp_names:
                        text, is_error = await call_mcp_tool(session, block.name, block.input)
                    else:
                        text, is_error = f"Unknown tool: {block.name}", True

                    label = "error" if is_error else "result"
                    print(f"  {label}: {preview(text)}\n")
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": text, "is_error": is_error})

                if draft_path is not None:
                    stop_reason_text = "The draft was saved."
                elif stop_reason_text is None:
                    messages.append({"role": "user", "content": results})

    print("=" * 70)
    print(f"Stopped: {stop_reason_text}")
    print(f"Tool calls: {tool_calls}\n")
    if draft is not None:
        print("DRAFT")
        print(draft["reply_text"].strip())
        print(review_line(draft["needs_human_review"]))
        print(f"\nDraft file: {draft_path.relative_to(REPO)} (not sent)")
    else:
        print("No draft was saved.")
    print(f"Total tokens: {tokens_in:,} input, {tokens_out:,} output")


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Draft a reply to one employee email. Saves a draft only; "
                    "never sends.")
    parser.add_argument("--email", required=True, type=Path,
                        help="path of one email file (.eml)")
    parser.add_argument("--org", metavar="FOLDER", default=DEFAULT_ORG,
                        help="organization folder: a name inside week-04 or a path "
                             f"(default: {DEFAULT_ORG})")
    parser.add_argument("--save", metavar="FILE", type=Path,
                        help="also write everything printed to this text file (UTF-8)")
    args = parser.parse_args()

    if not args.email.is_file():
        parser.error(f"email file not found: {args.email}")
    org_dir = resolve_org(args.org)
    if not org_dir.is_dir():
        parser.error(f"organization folder not found: {args.org}")
    for needed in ("hr-docs", "employees.csv", "system_prompt.txt"):
        if not (org_dir / needed).exists():
            parser.error(f"{org_dir} has no {needed}")

    if args.save is None:
        asyncio.run(run(org_dir, args.email.resolve()))
        return
    args.save.parent.mkdir(parents=True, exist_ok=True)
    with args.save.open("w", encoding="utf-8") as f:
        terminal = sys.stdout
        sys.stdout = Tee(terminal, f)
        try:
            asyncio.run(run(org_dir, args.email.resolve()))
        finally:
            sys.stdout = terminal
    print(f"Output saved to {args.save}")


if __name__ == "__main__":
    main()
