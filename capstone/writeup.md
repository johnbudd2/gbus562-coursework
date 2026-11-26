## What I Built

I built a review assistant for hospital Clinical Documentation Integrity (CDI) work. You give it one inpatient discharge summary and the ICD-10-CM codes already assigned. It reports any secondary diagnosis the chart supports that would count as a CC or MCC but isn't captured yet, and it drafts a physician query for each one. The core is `cdi_query.py`, a Claude agent with three local tools:
- a search over the FY 2027 ICD-10-CM Official Guidelines;
- a keyword search of the CMS FY 2027 CC/MCC table;
- an exact lookup of one code's severity and its CC exclusions.

Code then checks every answer: each quote must be in the chart, and each stated severity must have come from a lookup in that run. Every request is written as one line to `runs/log.jsonl`. Around the tool I built:
- 24 synthetic discharge summaries with an answer key (14 dev cases, 10 held-out test cases);
- a validator that checks every expected code against the CMS table;
- `eval.py` for recall, precision and false queries;
- `cost.py` for cost estimates;
- a small Streamlit page.

All patient data is made up. The reference data is public CDC and CMS files.

## Why This Approach

Techniques: RAG, Agent — RAG because the 121-page guidelines decide whether a condition is reportable at all (an untreated abnormal lab is not, per III.B; a dietitian alone cannot establish malnutrition, per I.B.14), and the model has to cite the actual rule it relied on instead of paraphrasing from memory; an agent because what needs checking depends on what is in the chart, so the model decides which guideline sections to read and which codes to look up, and the CC/MCC severity always comes from an exact table lookup rather than the model's recall.

Human oversight: Draft-only — a physician query is a compliance-sensitive message, and my own testing showed the model can still be confidently wrong (it queried acute blood loss anemia on a negative case where the surgeon called the drop expected and untreated), so a CDI specialist must review every draft and nothing is ever sent automatically.

## Business Case

Who uses it: CDI specialists at acute-care hospitals who review inpatient charts for undocumented CC/MCC conditions and write physician queries, and the CDI manager who checks those queries for compliance.

Problem: Reviewing a chart by hand takes a specialist about [FILL: minutes per chart] minutes, results vary from one specialist to the next, and a poorly worded query that suggests the answer creates compliance risk for the hospital.

Model: claude-sonnet-5

Prices used: $2.00 per million input tokens and $10.00 per million output tokens (Anthropic list price for claude-sonnet-5)

Cost per run: $0.0557 (from cost.py: average of 44 logged claude-sonnet-5 runs, 17,219 input and 2,122 output tokens)

Monthly volume: 1,000 chart reviews per month [FILL: replace with your hospital's expected CDI review volume, then rerun cost.py with that --volume]

Monthly cost: $55.65 (from cost.py at 1,000 runs per month)

## Example Run

`runs/log.jsonl` line 37, copied exactly:

```
{"timestamp": "2026-11-26T18:06:55+00:00", "model": "claude-sonnet-5", "input": "SYNTHETIC CASE — fictional patient, not real data\n\n# Discharge Summary — Case 17\n\n**Patient:** 90-year-old female\n**Admitting diagnosis:** UTI, dehydration\n\n## HPI\n90F NH resident, moderate Alzheimer dementia, sent for lethargy, decreased PO x4d, foul urine. Per NH, needs cueing to drink; recent heat in building.\n\n## Hospital course\n**Hospital Day 1:** Dry MM, skin tenting. Na 157. UA pyuria, nitrite+. Ceftriaxone. Free water deficit calculated ~3.4 L. D5W 100 mL/h, Na q6h, target correction <=10 mmol/L/24h.\n**Hospital Day 2:** Na 151 AM, 149 PM. More alert. UCx E coli, ceftriaxone-S. D5W cont, encouraged PO.\n**Hospital Day 3:** Na 146. Eating ~50% w/ assist. D5W stopped. Free water flushes not needed — drinking w/ cueing.\n**Hospital Day 4:** Na 143. Back to NH w/ scheduled fluids q2h while awake.\n\n## Labs\n| Test | HD1 (adm) | HD1 (6h) | HD2 | HD3 | HD4 | Ref range |\n|---|---|---|---|---|---|---|\n| Na (mmol/L) | 157 | 155 | 151 | 146 | 143 | 135-145 |\n| Serum osm (mOsm/kg) | 328 | — | — | — | — | 275-295 |\n| BUN (mg/dL) | 46 | — | 34 | 24 | 19 | 7-20 |\n| Cr (mg/dL) | 1.0 | — | 0.9 | 0.8 | 0.8 | 0.6-1.1 |\n| WBC (K/uL) | 12.9 | — | 10.4 | 8.6 | — | 4.0-11.0 |\n| Glucose (mg/dL) | 128 | — | 186 | 140 | 110 | 70-99 |\n\n## Vitals highlights\nHD1: T 37.9, HR 102, BP 108/60. HD2 onward HR 80s, BP 120s/60s.\n\n## Medications and treatments\nCeftriaxone 1 g IV daily HD1-3 -> cephalexin PO; D5W 100 mL/h HD1-3; Na checks q6h HD1-2 then daily; scheduled oral fluids with cueing; aspiration/fall precautions.\n\n## Discharge diagnoses (as written by physician)\n1. E coli UTI\n2. Dehydration — IVF, Na trending down, 143 at d/c\n3. Alzheimer dementia, moderate\n\n## Current codes\n- PDX: N39.0 Urinary tract infection, site not specified\n- SDX: B96.20 Unspecified Escherichia coli [E. coli] as the cause of diseases classified elsewhere\n- SDX: E86.0 Dehydration\n- SDX: G30.9 Alzheimer's disease, unspecified\n- SDX: F02.B0 Dementia in other diseases classified elsewhere, moderate, without behavioral disturbance, psychotic disturbance, mood disturbance, and anxiety\n", "output": "## Findings\n\n### 1. Hypernatremia\n- Action: query\n- Candidate code: E87.0 Hyperosmolality and hypernatremia\n- Severity (from lookup_code): CC [excluded with principal N39.0: no]\n- Evidence: \"Na 157\" ; \"Serum osm (mOsm/kg) | 328\" ; \"Free water deficit calculated ~3.4 L. D5W 100 mL/h, Na q6h, target correction <=10 mmol/L/24h.\" ; \"Dehydration — IVF, Na trending down, 143 at d/c\"\n- Guideline section: III, this condition required clinical evaluation (serum osmolality, serial Na checks), therapeutic treatment (calculated free-water deficit, D5W infusion titrated to a specific correction rate), and increased monitoring (Na q6h HD1-2), but the physician's discharge diagnosis only names \"Dehydration\" without specifying the hypernatremia/hyperosmolar state that the treatment plan was actually directed at.\n\n## Draft queries\n\n### Query 1: Hypernatremia\nDRAFT — for CDI specialist review\n\nClinical indicators:\n- \"Na 157\" (Hospital Day 1, admission)\n- \"Serum osm (mOsm/kg) | 328\" (Hospital Day 1 reference range 275-295)\n- \"Free water deficit calculated ~3.4 L. D5W 100 mL/h, Na q6h, target correction <=10 mmol/L/24h.\" (Hospital Day 1)\n- \"Na 151 AM, 149 PM\" (Hospital Day 2); \"Na 146\" (Hospital Day 3); \"Na 143\" (Hospital Day 4)\n\nQuestion: Based on your clinical judgment, can the sodium/osmolality abnormality documented and treated during this admission be further specified?\n\nOptions:\n- [ ] Hypernatremia\n- [ ] Dehydration without associated electrolyte abnormality\n- [ ] Both hypernatremia and dehydration\n- [ ] Other (please specify)\n- [ ] Unable to determine\n\n## Automated checks\n- All quotes found in the chart; every severity verified with lookup_code.", "input_tokens": 16834, "output_tokens": 2174, "seconds": 21.4}
```

## Reflection

Failed run: 2026-11-26T17:45:31+00:00 (runs/log.jsonl line 22, data/cases/case_03.md, claude-sonnet-5)

What went wrong: The tool correctly found the severe malnutrition, but its draft query listed the dietitian's conclusion "Meets ASPEN/AND criteria for severe protein-calorie malnutrition" as a clinical indicator, handing the physician the answer, which makes it a leading query.

How I fixed it: I added one rule to the system prompt saying a query's indicators must be objective data only (values, exam findings, treatments, monitoring) and that any text naming the condition or its severity stays in the Findings, then reran the byte-identical case file with the same command.

Fixed run: 2026-11-26T18:03:48+00:00 (runs/log.jsonl line 34, same input; the indicators now list only measurements and treatments)

[FILL: one or two sentences in your own words on what this taught you, e.g. why a correct finding can still produce a non-compliant query]
