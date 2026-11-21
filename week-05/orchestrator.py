"""Coordinator 2: an orchestrator agent for Brookfield's response to one supplier email.

Here Claude decides which specialists to use. An agent (claude-sonnet-5) gets
the system prompt in brookfield-bikes/prompts/orchestrator.txt and four tools:

- run_supply_analyst: runs the supply analyst on the email; returns its brief.
- draft_supplier_reply: drafts a reply to the supplier from the latest brief.
- draft_customer_notice(order_id): drafts a notice for one order at risk.
- finish(summary, needs_person_decision): ends the job.

The three specialist tools run the same code as pipeline.py (specialists.py),
and every draft goes through the same review step (writer, reviewer, at most 2
revisions, name and penalty checks in code) and is saved in week-05/drafts.
The orchestrator never sees a writer's input: the code hands each writer the
analyst's brief or one order's facts, so the agent can't pass on invented
numbers. Nothing is ever sent.

It stops when finish is called, after 10 tool calls, or on any reply that isn't
a tool request. It prints each choice the agent makes and ends with the same
summary as the pipeline, plus the agent's own summary and decision flag.

Usage (from the top of the repository):
    .venv\\Scripts\\python.exe week-05\\orchestrator.py --email week-05\\brookfield-bikes\\supplier-emails\\02-kendall-tire-options.eml
    .venv\\Scripts\\python.exe week-05\\orchestrator.py --email <file.eml> --save week-05\\runs\\orchestrator-02.txt
"""

import json
from datetime import datetime

import specialists as sp
from pipeline import print_summary, run_cli

COORDINATOR = "orchestrator"
MAX_TOOL_CALLS = 10


def tool(name, description, properties=None, required=()):
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": properties or {},
                         "required": list(required), "additionalProperties": False},
        "strict": True,
    }


TOOLS = [
    tool("run_supply_analyst",
         "Run the supply analyst on the supplier email. It uses Brookfield's stock, "
         "shipment, and order data to work out which customer orders the email "
         "puts at risk, and returns its brief as JSON: what changed, orders at risk "
         "(with units short, ship day, and the day each can ship), the supplier's "
         "options, decisions for a person, and questions the supplier asked. It "
         "takes no inputs; it reads the email itself."),
    tool("draft_supplier_reply",
         "Draft a reply to the supplier from the analyst's latest brief (customer "
         "names are removed before the writer sees it). The draft is reviewed, "
         "revised if needed, checked, and saved for a person; it is never sent. "
         "Returns the saved draft and the review result, or says that no reply is "
         "needed. Run the supply analyst first. Takes no inputs."),
    tool("draft_customer_notice",
         "Draft a notice to the customer of one order at risk, from that order's "
         "facts only. The draft is reviewed, revised if needed, checked, and saved "
         "for a person; it is never sent. The order must be in the orders_at_risk "
         "list of the analyst's latest brief. Call once per order.",
         {"order_id": {"type": "string",
                       "description": "The order number, for example O-1043."}},
         ["order_id"]),
    tool("finish",
         "End the job. Call this exactly once, last.",
         {"summary": {"type": "string",
                      "description": "A short summary for Brookfield's purchasing "
                                     "manager: what the email means, what was "
                                     "drafted, and what still needs doing."},
          "needs_person_decision": {"type": "boolean",
                                    "description": "true if a person must make a "
                                                   "decision, such as choosing a "
                                                   "supplier's option."}},
         ["summary", "needs_person_decision"]),
]


class Job:
    """What the orchestrator's tools have done so far in this run."""

    def __init__(self, email):
        self.email = email
        self.run_dir = sp.new_run_dir(email, COORDINATOR)
        self.brief = None
        self.drafts = []
        self.failures = []
        self.noticed = set()
        self.supplier_status = "not drafted (the orchestrator didn't ask for one)"
        self.finish = None

    # Each tool returns (text for the agent, is_error).

    async def run_supply_analyst(self, _):
        self.brief = await sp.run_analyst(self.email, sender=COORDINATOR)
        return json.dumps(self.brief, indent=2, ensure_ascii=False), False

    async def draft_supplier_reply(self, _):
        if self.brief is None:
            return "Run the supply analyst first; the writer needs its brief.", True
        result = await sp.prepare_supplier_reply(self.email, self.brief, self.run_dir,
                                                 sender=COORDINATOR)
        if result is None:
            self.supplier_status = f"writer said {sp.NO_REPLY}; nothing saved"
            return f"The supplier writer says: {sp.NO_REPLY}. Nothing was saved.", False
        self.drafts.append(result)
        self.supplier_status = "drafted"
        return draft_report(result), False

    async def draft_customer_notice(self, args):
        if self.brief is None:
            return "Run the supply analyst first; there is no list of orders at risk.", True
        order_id = str(args.get("order_id", "")).strip().upper()
        at_risk = {str(o.get("order_id", "")).strip().upper(): o
                   for o in self.brief["orders_at_risk"]}
        if order_id not in at_risk:
            listed = ", ".join(at_risk) or "none"
            return (f"{order_id or 'That order'} is not in the analyst's orders at risk "
                    f"(orders at risk: {listed}). No notice was drafted."), True
        if order_id in self.noticed:
            return f"A notice for {order_id} was already drafted in this run.", True
        result = await sp.prepare_customer_notice(at_risk[order_id], self.run_dir,
                                                  sender=COORDINATOR)
        self.noticed.add(order_id)
        self.drafts.append(result)
        return draft_report(result), False

    async def finish_job(self, args):
        self.finish = args
        return "Finished.", False


def draft_report(result):
    lines = [
        f"Saved {result.path.name} (to {result.recipient}, not sent).",
        f"Reviewer: {result.verdict} after {result.revisions} revision(s).",
        "Needs a person: " + ("yes - " + "; ".join(result.reasons)
                              if result.needs_person else "no"),
        "Draft:",
        result.text.strip(),
    ]
    return "\n".join(lines)


async def run(email_path):
    sp.TOKENS.reset()
    email = sp.load_email(email_path)
    job = Job(email)
    handlers = {
        "run_supply_analyst": job.run_supply_analyst,
        "draft_supplier_reply": job.draft_supplier_reply,
        "draft_customer_notice": job.draft_customer_notice,
        "finish": job.finish_job,
    }

    print(f"Coordinator: {COORDINATOR} (Claude chooses the specialists)")
    print(f"Email file:  {email.path.name}")
    print(f"From:        {email.sender}")
    print(f"Subject:     {email.subject}")
    print(f"Model:       {sp.MODEL}")
    print(f"Tools:       {', '.join(t['name'] for t in TOOLS)}")

    system = sp.load_prompt("orchestrator")
    messages = [{"role": "user", "content": (
        f"Today's date: {datetime.now():%Y-%m-%d}\n\n"
        "Coordinate Brookfield's response to this supplier email.\n\n"
        f"<email>\n{email.as_text()}\n</email>")}]
    tool_calls = 0
    stopped = None

    while job.finish is None and stopped is None:
        try:
            response = await sp.call_model(COORDINATOR, system, messages, TOOLS)
        except sp.SpecialistError as e:
            stopped = f"The orchestrator could not continue: {e}"
            break

        thought = sp.text_of(response)
        if thought:
            print(f"\n[{COORDINATOR}] says: {sp.preview(thought)}")
        if response.stop_reason != "tool_use":
            stopped = "The orchestrator stopped without calling finish."
            break

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if tool_calls >= MAX_TOOL_CALLS:
                stopped = f"Reached the limit of {MAX_TOOL_CALLS} tool calls before finish."
                break
            tool_calls += 1
            args = json.dumps(block.input, ensure_ascii=False) if block.input else ""
            print(f"\n{'=' * 70}\nChoice {tool_calls}: {block.name} {args}\n{'=' * 70}")

            handler = handlers.get(block.name)
            if handler is None:
                text, is_error = f"Unknown tool: {block.name}", True
            else:
                try:
                    text, is_error = await handler(block.input)
                except sp.SpecialistError as e:
                    text, is_error = f"{block.name} failed: {e}", True
                    job.failures.append((f"{block.name} {args}".strip(), str(e)))
            if is_error:
                print(f"\n!! tool error returned to the orchestrator: {sp.preview(text)}")
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": text, "is_error": is_error})
        if job.finish is None and stopped is None:
            messages.append({"role": "user", "content": results})

    extra = [f"Tool calls by the orchestrator: {tool_calls} of {MAX_TOOL_CALLS}"]
    if job.finish is not None:
        decide = "yes" if job.finish["needs_person_decision"] else "no"
        extra += [f"Orchestrator's summary: {job.finish['summary']}",
                  f"Orchestrator says a person must decide: {decide}"]
    print_summary(COORDINATOR, email, job.brief, job.drafts, job.failures,
                  job.supplier_status, stopped, job.run_dir, extra)


def main():
    run_cli(run, "Prepare Brookfield's response to one supplier email with an "
                 "orchestrator agent that chooses the specialists. Saves drafts "
                 "only; never sends.")


if __name__ == "__main__":
    main()
