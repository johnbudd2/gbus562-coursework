## Evaluation prompt

I want an evaluation for my capstone CDI query tool so I can see which kinds of charts it handles correctly, before and after I add a guardrail. Read capstone/cdi_query.py and call its review() function for each case, so every run is logged in capstone/runs/log.jsonl the normal way. Also read capstone/data/answer_key.json and the case files in capstone/data/cases/.

Create week-06/test-cases.csv with the header id,type,request,expected, plus two extra columns, must_find_code and must_not_query. Write 8 cases, and write the expected behavior before running anything:
- T1–T2 Typical: case_01 and case_17.
- T3–T4 Edge case: case_13 (conflicting providers) and case_08 (a coding correction, not a query).
- T5–T6 Should refuse: case_24 (NSTEMI ruled out) and case_22 (an expected post-op drop). The tool must answer "No query indicated" and draft no query.
- T7–T8 Guardrail: case_02 and case_07, where earlier runs quoted text that isn't in the chart.

The request is the case file path.

Then create week-06/evaluate_capstone.py. It runs every row through the tool and decides PASS or FAIL in code only:
- The must_find_code must appear as a finding.
- Should refuse cases must say "No query indicated" and contain no draft query.
- Guardrail cases must not show any draft query that quotes text missing from the chart, or that mentions an ICD-10-CM code, CC, MCC, DRG or payment.

The output format is pinned: one line per case starting with the id, then PASS, or FAIL followed by the reason, and a last line "Passed: N of M". Add --save so I can save the output to week-06/runs/eval-1.txt.

Read my API key from API-Key.txt in the folder above my repository, and never write it to any file. Tell me your plan before you write code.

## Guardrail prompt

Add one output guardrail to capstone/cdi_query.py, enforced in code, not in the prompt.

After the model answers, and before the output is printed, returned or logged, check each draft query in the "Draft queries" section:
- every quoted clinical indicator must appear word for word in the case file;
- the query must not contain an ICD-10-CM code or the words CC, MCC, DRG, payment or reimbursement.

If a query fails, replace only that query with "WITHHELD by guardrail: [reasons]. A CDI specialist must rewrite this query." Keep the Findings and the other queries unchanged.

Put the guardrail inside review() so the log records the guarded output, and don't change the system prompt. Then run week-06/evaluate_capstone.py again and save its output to week-06/runs/eval-2.txt, without touching eval-1.txt. Keep reading the API key from API-Key.txt in the folder above my repository, and never write it to a file. Tell me your plan before you write code.
