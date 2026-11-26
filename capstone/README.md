# Capstone Markdown

## About

GBUS 562 capstone: a command-line tool that helps Clinical Documentation Integrity (CDI) specialists at acute-care hospitals review inpatient charts.

**The problem.** CDI specialists read inpatient charts looking for clinical documentation that supports a CC (complication or comorbidity) or MCC (major CC) that the current coding doesn't capture. When they find one, they send the physician a query. Doing this by hand is slow and inconsistent from one specialist to the next. A leading query (one that suggests the answer, or mentions payment) also creates compliance risk.

**What the tool does.** It takes a discharge summary and the current code list, then:

1. finds candidate CC/MCC diagnoses that the chart's evidence supports but the coding misses, using the ICD-10-CM Official Guidelines for Coding and Reporting and the CMS MS-DRG CC/MCC tables;
2. drafts a compliant, non-leading physician query for each candidate.

A CDI specialist reviews every draft and decides whether to send it. The tool never sends anything.

**Data.** All charts are synthetic (made up). The reference material is public: the ICD-10-CM guidelines (CDC) and the CC/MCC tables (CMS). Everything is kept in this folder.

## Requirements (kept in every version)

- Model: `claude-sonnet-5`, unless the project owner chooses another.
- The API key is read at runtime from `API-Key.txt` in the folder above the repository. It is never written to any file. (As a fallback, the tool reads `course_API.txt` in that folder, then the `ANTHROPIC_API_KEY` environment variable.)
- Every request appends one line of JSON to `runs/log.jsonl` with: `timestamp`, `model`, `input`, `output`, `input_tokens`, `output_tokens`, `seconds`. Tokens and seconds are totals across every Claude call made for that request. A failed request is logged too, with an `error` in `output`.
- Only made-up or public data, kept in the `capstone` folder.
- Packages are listed in `requirements.txt`.

## How to run

From the repository folder, using the repository's `.venv`:

```
.venv\Scripts\python.exe -m pip install -r capstone\requirements.txt
.venv\Scripts\python.exe capstone\reference.py --check                       # check the tables, no API call
.venv\Scripts\python.exe capstone\cdi.py --case capstone\data\cases\case-01.json
.venv\Scripts\python.exe capstone\cdi.py --summary my-summary.txt --codes "J18.9,E11.9,I10"
.venv\Scripts\python.exe capstone\evaluate.py --save capstone\runs\eval-1.txt
```

`cdi.py` options:
- `--codes` lists the current codes with the principal diagnosis first.
- `--save FILE` also writes the printed report to a file.
- `--json` prints the full result as JSON.

Each query draft is also saved to `runs/drafts/<case>-query-<n>.txt`.

The guideline index (`capstone/.chroma/`, not in git) is built automatically the first time. Rebuild it with `reference.py --rebuild`.

## How it works

One request is one case. The pipeline is fixed: three steps run in code and two are Claude calls, plus at most one revision call per query.

| Step | Done by | What happens |
|---|---|---|
| 1. Current codes | code | Looks up each code in the FY 2027 ICD-10-CM code file and its CC/MCC level in CMS Tables 6I/6J, applies the Table 6K CC exclusions for the principal diagnosis, and works out the case's current CC/MCC level. |
| 2. Find candidates | Claude (`prompts/find-candidates.txt`, effort high) | Gets the discharge summary, the codes, and guideline sections. The sections are the core inpatient rules (Section III, I.B.14, I.B.19) plus up to 8 sections that a Chroma search finds closest to the chart's lines (Section IV, outpatient, is left out). Returns JSON candidates: diagnosis, suggested code, status (`documented_not_coded` or `needs_query`), exact evidence quotes, and the guideline sections relied on. |
| 3. Check candidates | code (`checks.py`) | Drops a candidate if any quote isn't in the summary, the code isn't valid, it's already coded, it isn't a CC/MCC, or the principal diagnosis excludes it. Adds the case level before and after. Claude's opinion of CC/MCC status is never used. |
| 4. Draft query | Claude (`prompts/draft-query.txt` + `prompts/query-rules.md`, effort medium) | For each `needs_query` candidate. The drafter gets the summary, the indicators, and the condition being considered, but never the code or its CC/MCC status, so it can't mention them. |
| 5. Compliance check | code (`checks.py`) | Fails a draft if it: mentions payment, DRG, CC, MCC or severity; uses leading wording ("please document", "would you agree", "meets criteria"); contains an ICD-10-CM code; quotes an indicator that isn't in the summary; or, as multiple choice, has fewer than two clinical options, lacks "Other (please specify)" or "Clinically undetermined", or is yes/no. A failing draft goes back to Claude once with the problems listed, and is checked again. |

The query text itself (header, indicators, question, options, closing line) is assembled in code, so every query has the same compliant frame.

### Course techniques used

| Technique | Where | Why |
|---|---|---|
| Structured prompting with few-shot examples and JSON schema output (week 2) | both prompts | gives a consistent output format; the examples show a good candidate, a non-candidate, and leading vs. compliant query wording |
| RAG over the guidelines with Chroma, split by section (week 3) | step 2 | the guidelines are 121 pages; Claude gets only the relevant sections and cites them |
| Exact lookups in code instead of asking the model (week 4) | steps 1 and 3 | CC/MCC status and exclusions are exact table facts |
| Fixed pipeline instead of an agent (weeks 4–5) | overall | the steps are known in advance and a mistake is a compliance problem, so cost, repeatability and auditability matter more than flexibility |
| Information limits between steps (week 5) | step 4 | the query drafter never sees the code or CC/MCC status |
| Code guardrails, one revision, and an evaluation in code (weeks 5–6) | step 5, `evaluate.py` | compliance and accuracy are checked by code against cases with known answers |

## Files

| Path | What it is |
|---|---|
| `cdi.py` | command line and pipeline; writes `runs/log.jsonl` and `runs/drafts/` |
| `reference.py` | loads the ICD-10-CM and CMS tables, looks up codes, builds and searches the guideline index; `--check`, `--rebuild`, `--search` |
| `checks.py` | evidence-quote check, CC/MCC and exclusion filter, query compliance check, query text |
| `evaluate.py` | runs every case and checks results in code; `--save`, `--only` |
| `requirements.txt` | `anthropic`, `chromadb`, `pypdf` |
| `prompts/` | `find-candidates.txt`, `draft-query.txt`, `query-rules.md` (used by the tool); `query_standards.md` (the project owner's draft standards, under review, not yet used by the tool) |
| `reference/` | FY 2027 guidelines PDF, ICD-10-CM code file, CMS Tables 6I, 6J and 6K; sources in `reference/SOURCES.md` |
| `data/cases/` | first set: 10 synthetic cases (`case-01.json` … `case-10.json`) with expected results inside each file; evaluation set: 24 synthetic discharge summaries (`case_01.md` … `case_24.md`) |
| `data/answer_key.json` | answer key for the 24-case set (gaps, should-not-query items, case type, dev/test split) |
| `validate_cases.py` | checks the 24-case set and answer key against `reference/cc_mcc.csv` and Table 6K, no API calls; fails loudly on any mismatch |
| `reference/cc_mcc.csv` | every CC/MCC code with severity and PDX exclusion collection, generated from CMS Tables 6I/6J/6K by `reference.py --export-csv` |
| `runs/` | `log.jsonl` (made by the tool), evaluation runs, `drafts/` |
| `docs/` | design notes and writeup |

## Test cases

Every case is synthetic. Each file holds the discharge summary, the current codes, and an `expected` block that only `evaluate.py` reads.

| Case | What it tests | Expected |
|---|---|---|
| case-01 | pneumonia; creatinine 2.3 from a baseline of 1.0, treated, but AKI never named | query: AKI (N17.9, CC) |
| case-02 | COPD exacerbation; heart failure without type or acuity, EF 25–30%, IV diuresis | query: HF type/acuity (I50.2x, MCC) |
| case-03 | UTI; severe malnutrition documented only by the dietitian (Guideline I.B.14) | query: malnutrition (E43, MCC) |
| case-04 | pneumonia; acute hypoxic respiratory failure documented but not coded | coding: J96.01 (MCC), no query |
| case-05 | uncomplicated appendicitis | nothing |
| case-06 | severe sepsis, all captured; one potassium of 3.4 that corrected without treatment (Section III.B) | nothing |
| case-07 | dehydration; treated hyponatremia, but E87.1 is excluded as a CC with principal E86.0 | nothing (dropped by the exclusion check) |
| case-08 | cystitis; brief fever and tachycardia, normal lactate, no organ dysfunction | nothing; sepsis must not be proposed |
| case-09 | knee replacement; "likely acute blood loss anemia" at discharge (Section III.C) | coding: D62 (CC), no query |
| case-10 | UTI; "acute confusion" with hyponatremia, back to baseline after treatment | query: encephalopathy or delirium (G93.41/F05) |

## Evaluation set (24 cases)

`data/cases/case_01.md` … `case_24.md` are written in realistic, terse discharge-summary style. Each has: the synthetic-case line, age and sex, admitting diagnosis, HPI, hospital course by Hospital Day, a labs table with reference ranges, vitals, treatments, discharge diagnoses as written, and `## Current codes`. There are no names, MRNs, dates of birth, calendar dates, or real facilities. They were written by hand, with no API calls.

| Type | Count | Cases |
|---|---|---|
| gap (one true gap) | 10 | 01 hyponatremia, 02 acute blood loss anemia, 03 severe PCM, 04 acute hypoxic respiratory failure, 05 metabolic encephalopathy, 06 AKI, 15 pressure ulcer stage 3, 16 heart failure type/acuity, 17 hypernatremia, 18 chemo-induced pancytopenia |
| double_gap | 4 | 07 HF type/acuity + AKI, 08 severe PCM + pressure ulcer stage 4 (coding fix), 19 metabolic encephalopathy + hyponatremia, 20 acute blood loss anemia + AKI |
| negative | 5 | 09, 11, 21 fully coded; 10, 22 abnormal labs that were not evaluated, treated or monitored |
| trap | 5 | 12 albumin-only "malnutrition" (a query would be leading), 13 conflicting respiratory failure documentation (query), 14 ATN documented but coded N17.9 (coding fix), 23 "history of" PE and CVA, 24 NSTEMI ruled out |

Split: `dev` = cases 01–14, `test` = 15–24. The 20 expected gaps are 8 CC and 12 MCC. 18 need a query, and 2 need a coding fix with no query. Each `expected` item has `action` (`query` or `code`) and `replaces_code`, so a gap like "I50.9 → I50.33" can be scored. The answer key also lists 39 should-not-query items with reasons.

```
.venv\Scripts\python.exe capstone\validate_cases.py
```

`cdi.py` and `evaluate.py` still read the first 10 JSON cases. Running the tool on the `.md` set is a later step.

## Status

The first full version is built. It includes the reference data, 10 cases, the pipeline, the compliance checks and the evaluation. Evaluation results are below.

## Evaluation runs

| Run | Change | Result |
|---|---|---|
| `runs/eval-1.txt` | first version | 9/10. Case-10 found the encephalopathy and drafted a compliant query, but suggested G92.9 (toxic encephalopathy, MCC), which the test didn't accept. 14 calls, 119,338 tokens, 127 s. |
| `runs/eval-2.txt` | case-10 also accepts G92.8/G92.9: the code is only used for the CC/MCC lookup, and the physician chooses the diagnosis. Tool unchanged. | 10/10. 14 calls, 121,743 tokens, 151 s. |

## Decisions

| Date | Decision |
|---|---|
| 2026-11-26 | Reference files: download the official public guideline and CC/MCC files into `reference/`, recording source URLs and fiscal year. FY 2027 (MS-DRG v44). |
| 2026-11-26 | Test cases: 10 synthetic cases, each with an expected result for evaluation. |
| 2026-11-26 | Interface: command line, like the weekly projects. |
| 2026-11-26 | This readme is `capstone/README.md`, titled "Capstone Markdown". |
| 2026-11-26 | Scope: secondary diagnoses that add a CC/MCC only. No principal diagnosis changes, POA, HAC, or MS-DRG assignment. |
| 2026-11-26 | Query rules are written in this project's own words, based on AHIMA/ACDIS compliant query practice (copyrighted text not copied). |
| 2026-11-26 | CC/MCC status, CC exclusions, code validity and evidence quotes are checked in code; Claude's claims about them are never trusted. |
| 2026-11-26 | Query drafter never sees the code or CC/MCC status, so a query cannot mention them. Failing drafts get one revision. |
| 2026-11-26 | Section IV (outpatient) guideline sections are left out of retrieval. |
| 2026-11-26 | An extra candidate counts as an evaluation failure, because over-querying burdens physicians and creates compliance risk. |
| 2026-11-26 | Drafted `prompts/query_standards.md` (physician query standards) for the project owner to review and edit. It is not wired into the tool yet. |
| 2026-11-26 | Built a 24-case evaluation set and `answer_key.json` by hand, with no API calls, plus `validate_cases.py`. Added `reference/cc_mcc.csv`, generated from the CMS tables. |
| 2026-11-26 | In FY 2027, E66.01 (morbid obesity) and pressure ulcer stages 1, 2 and unspecified are not CCs; stages 3 and 4 are MCCs. So the two gaps chosen freely are hypernatremia and chemo-induced pancytopenia, and the pressure-ulcer cases turn on stage. |
| 2026-11-26 | Answer key adds `action` (`query`/`code`) and `replaces_code` to each expected item. A pressure-ulcer stage from the WOC nurse (Guideline I.B.14) and an ATN diagnosis coded as unspecified AKI are coding fixes, not queries. |
