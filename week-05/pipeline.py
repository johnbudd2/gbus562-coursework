"""Coordinator 1: a fixed pipeline for a company's response to one supplier email.

It always runs the specialists from specialists.py in the same order:

1. The supply analyst works out which customer orders the email puts at risk.
2. A customer notice for each order at risk (skipped if there are none).
3. A reply to the supplier.

Every draft goes through the review step (writer, reviewer, at most 2
revisions, a name check in code) and is saved in
week-05/drafts/<company folder>/<run folder>/.
Nothing is ever sent. At the end it prints a summary: drafts saved, drafts
marked for a person, decisions for a person, and total tokens.

Usage (from the top of the repository):
    .venv\\Scripts\\python.exe week-05\\pipeline.py --email week-05\\brookfield-bikes\\supplier-emails\\01-northgate-controller-delay.eml
    .venv\\Scripts\\python.exe week-05\\pipeline.py --email <file.eml> --save week-05\\runs\\pipeline-01.txt

    .venv\\Scripts\\python.exe week-05\\pipeline.py --org copperline-coffee --email week-05\\copperline-coffee\\supplier-emails\\01-danforth-pump-delay.eml

--org names the company folder (default: brookfield-bikes; see org.py).
--save also writes everything printed to that text file (UTF-8).
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import specialists as sp

COORDINATOR = "pipeline"


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


def step(number, title):
    print(f"\n{'=' * 70}\nStep {number}: {title}\n{'=' * 70}")


async def run(email_path):
    sp.TOKENS.reset()
    email = sp.load_email(email_path)
    run_dir = sp.new_run_dir(email, COORDINATOR)

    print(f"Coordinator: {COORDINATOR} (fixed order)")
    print(f"Email file:  {email.path.name}")
    print(f"From:        {email.sender}")
    print(f"Subject:     {email.subject}")
    print(f"Model:       {sp.MODEL}")
    sp.print_org()

    drafts = []          # DraftResult for each saved draft
    failures = []        # (what, error) for drafts that could not be made
    brief = None
    supplier_status = "not run"
    stopped = None

    try:
        step(1, "supply analyst")
        brief = await sp.run_analyst(email, sender=COORDINATOR)
    except sp.SpecialistError as e:
        stopped = f"The supply analyst could not finish: {e}"
        print(f"\n!! {stopped}")

    if brief is not None:
        at_risk = brief["orders_at_risk"]
        step(2, f"customer notices ({len(at_risk)} order(s) at risk)")
        if not at_risk:
            print("No orders at risk; skipping customer notices.")
        for order in at_risk:
            order_id = order.get("order_id", "?")
            try:
                drafts.append(await sp.prepare_customer_notice(
                    order, run_dir, sender=COORDINATOR))
            except sp.SpecialistError as e:
                failures.append((f"customer notice for {order_id}", str(e)))
                print(f"\n!! Customer notice for {order_id} failed: {e}")

        step(3, "reply to the supplier")
        try:
            result = await sp.prepare_supplier_reply(email, brief, run_dir,
                                                     sender=COORDINATOR)
            if result is None:
                supplier_status = f"writer said {sp.NO_REPLY}; nothing saved"
                print(f"\nThe supplier writer says: {sp.NO_REPLY}")
            else:
                drafts.append(result)
                supplier_status = "drafted"
        except sp.SpecialistError as e:
            supplier_status = "failed"
            failures.append(("supplier reply", str(e)))
            print(f"\n!! Supplier reply failed: {e}")

    print_summary(COORDINATOR, email, brief, drafts, failures, supplier_status,
                  stopped, run_dir)


def print_summary(coordinator, email, brief, drafts, failures, supplier_status,
                  stopped, run_dir, extra_lines=()):
    """The end-of-run summary shared by pipeline.py and orchestrator.py."""
    print(f"\n{'=' * 70}\nSUMMARY ({coordinator}, {email.path.name})\n{'=' * 70}")
    if stopped:
        print(f"Stopped early: {stopped}")
    for line in extra_lines:
        print(line)
    if brief is not None:
        print(f"What changed: {brief.get('what_changed', '')}")
    print(f"Supplier reply: {supplier_status}")

    print(f"\nDrafts saved: {len(drafts)}"
          + (f"  (in {display(run_dir)})" if drafts else ""))
    for d in drafts:
        flag = "NEEDS A PERSON" if d.needs_person else "ok"
        print(f"  - {d.path.name:<24} to {d.to:<38} reviewer {d.verdict} "
              f"({d.revisions} revision(s)), {flag}")

    flagged = [d for d in drafts if d.needs_person]
    print(f"\nDrafts marked for a person: {len(flagged)}")
    for d in flagged:
        print(f"  - {d.path.name}: {'; '.join(d.reasons)}")

    decisions = brief.get("decisions_for_a_person", []) if brief else []
    print(f"\nDecisions for a person: {len(decisions)}")
    for item in decisions:
        print(f"  - {item if isinstance(item, str) else json.dumps(item)}")

    if failures:
        print(f"\nDrafts that could not be made: {len(failures)}")
        for what, error in failures:
            print(f"  - {what}: {error}")

    tokens_in, tokens_out = sp.TOKENS.total()
    sp.print_token_report()
    print(f"\nTotal tokens: {tokens_in:,} input, {tokens_out:,} output "
          f"({tokens_in + tokens_out:,} in all)")
    print("Nothing was sent.")


def display(path):
    return sp.display_path(path)


def main():
    run_cli(run, "Prepare a company's response to one supplier email with a fixed "
                 "pipeline of specialists. Saves drafts only; never sends.")


def run_cli(run_fn, description):
    """--org, --email and --save handling shared by pipeline.py and orchestrator.py."""
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=description)
    sp.add_org_argument(parser)
    parser.add_argument("--email", required=True, type=Path,
                        help="path of one supplier email file (.eml)")
    parser.add_argument("--save", metavar="FILE", type=Path,
                        help="also write everything printed to this text file (UTF-8)")
    args = parser.parse_args()
    if not args.email.is_file():
        parser.error(f"email file not found: {args.email}")
    try:
        sp.set_org(args.org)
    except sp.SpecialistError as e:
        parser.error(str(e))
    warn_if_other_company(args.email)

    def go():
        try:
            asyncio.run(run_fn(args.email.resolve()))
        except sp.SpecialistError as e:  # e.g. no API key, unreadable email
            print(f"\n!! {e}")
            return 1
        return 0

    if args.save is None:
        sys.exit(go())
    args.save.parent.mkdir(parents=True, exist_ok=True)
    with args.save.open("w", encoding="utf-8") as f:
        terminal = sys.stdout
        sys.stdout = Tee(terminal, f)
        try:
            code = go()
        finally:
            sys.stdout = terminal
    print(f"Output saved to {args.save}")
    sys.exit(code)


def warn_if_other_company(email_path):
    """Warn when the email sits in another company's supplier-emails folder."""
    folder = email_path.resolve().parent
    if folder.name == "supplier-emails" and folder.parent != sp.DATA_DIR:
        print(f"Warning: {email_path} is in the {folder.parent.name} folder, but the "
              f"company is {sp.DATA_DIR.name}. Add --org {folder.parent.name} to use "
              "that company's data.", file=sys.stderr)


if __name__ == "__main__":
    main()
