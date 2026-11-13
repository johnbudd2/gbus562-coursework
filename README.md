# gbus562-coursework

## About

This repository holds the materials created in UMW's GBUS 562 course (AI Language Models). It is organized by weekly folders.

## Weekly Work

1. week-01
   - [Connect 4](week-01/connect4.html): a single-page browser game built with HTML and JavaScript. Play against a computer opponent that looks several moves ahead, with three strength levels, or switch to two-player mode to take turns with a friend on the same screen. It keeps a running score.
2. week-02
   - [Prompt Lab](week-02/prompt-lab.html): a web app for testing prompts against Claude, built with Claude Code.
   - [Exported runs](week-02/runs/): four Prompt Lab runs for the Wildframe tasks, one per prompting technique (zero-shot, one-shot, few-shot, chain-of-thought).
   - [Writeup](week-02/writeup.md): why each technique fit its task, one full example run, and a reflection comparing the results.
3. week-03
   - [ask.py](week-03/ask.py): a RAG tool that answers questions from Copperline Manufacturing's finance documents, built with Claude Code.
   - [Larkfield runs](week-03/runs/): the same overdraft-fee question asked about Larkfield Credit Union's documents [before](week-03/runs/q1-before.txt) and [after](week-03/runs/q1-after.txt) removing a superseded version of the fee schedule, plus five more test questions covering card fees, fraud liability, multi-document refund approval, funds availability, and an out-of-scope question.
   - [Writeup](week-03/writeup.md): what I built, why I chose section-based chunking, one full example run, and a reflection on the superseded fee schedule and the out-of-scope question.
4. week-04
   - Week 4: an AI agent that prepares replies to HR emails using tools from an MCP server, built with Claude Code.
   - [hr_mcp_server.py](week-04/hr_mcp_server.py): the MCP server, with two tools for Hartwell Foods' HR data: `search_policy` (the 4 closest HR document sections, with source files) and `get_leave_balance` (from `employees.csv`, by work email for HR staff, or limited to one employee when started with `--as-employee`).
   - [agent.py](week-04/agent.py): a command-line agent that drafts a reply to one employee email. It starts the MCP server limited to the sender, lets Claude (`claude-sonnet-5`) use its tools, and saves the reply to `week-04/drafts/` with a needs-human-review flag. It never sends anything, and it stops after 10 tool calls. Both scripts take `--org` to point them at another organization's folder (default `hartwell-hr`); the employee file's email and balance columns are worked out from its header row, and each organization gets its own search database. `--save` writes the agent's output to a text file.
   - [ridgeline-hotels](week-04/ridgeline-hotels/): a second organization (Ridgeline Hotels) with its own HR documents, employee file, sample emails, and system prompt, used to test the agent on a different company.
   - [Ridgeline runs](week-04/runs/): the agent's saved output for all seven Ridgeline sample emails, covering a PTO question, bereavement leave, a honeymoon longer than the PTO balance (run [before](week-04/runs/ridgeline-03.txt) and [after](week-04/runs/ridgeline-03-after.txt) adding a carry-over rule to the system prompt), a request for a coworker's balance, an email with an injected "admin override", a harassment report, and a sender with no employee record.
   - [Writeup](week-04/writeup.md): what I built, whether three Ridgeline tasks need a script, one model call, or an agent, one full example run, and a reflection on the Email 3, 6, and 7 runs.
