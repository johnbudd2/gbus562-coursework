# CDI manager review: `cdi_query.py` dev-split runs

Reviewer stance: CDI manager signing off on each output before a specialist could send it. Nothing has been fixed. The runs are kept exactly as logged, as candidates for the writeup's "Failed run" section.

- Tool: `cdi_query.py` (agent loop, `claude-sonnet-5`), version of 2026-11-26.
- Runs: 9 dev cases: 01, 02, 03, 07, 08, 10, 12, 13, 14. Of these, 4 are gap or double-gap cases, 1 is a negative case, and 3 are traps (12, 13, 14).
- Log: `runs/log.jsonl` lines 22 and 24–31. Line 23 is the intentional bad-model error test. The SHA-256 of `runs/log.jsonl` at review time is `6da06f2c7145b24f4d985abdd11b7c2ec5a06b45b9bde7a644e8eea892600f94`.
- How the checks were done:
  - Each answer was compared with `data/answer_key.json`.
  - Every severity claim was compared with the `lookup_code` calls made in that run.
  - Every cited guideline section was compared with the sections that run's own `search_guidelines` queries return. The queries were re-run offline; the search is local and gives the same results each time.
  - Quotes were checked against the chart.

## Summary

| Problem type | Count | Where |
|---|---|---|
| Missed gap | 0 | All 9 runs matched the answer key, including the correct action (query vs. coding correction). |
| Query on a lab-only finding | 0 | Case 10 (negative) and case 12 (albumin-only trap) correctly gave "No query indicated". |
| Severity asserted without `lookup_code` | 0 | Every code given a severity was looked up in the same run, with the principal diagnosis. |
| Hallucinated guideline section | 0 | Every cited section was among the sections retrieved in that run, and exists. Case 13's "I.A.19" looked suspect but is correct for FY 2027, and its quoted sentence is verbatim. |
| Misapplied guideline section | 1 | case_14 |
| Answer option with no clinical support in the record | 4 | case_02 (×2), case_07, case_13 |
| Leading wording under `query_standards.md` §1 (states the desired diagnosis) | 1 | case_03 |
| Missing clinically reasonable option | 1 | case_01 |
| "Quote" that is not verbatim from the chart (spliced, reconstructed from a table, or ellipsis) | 6 | case_02, case_07 (×3), case_08, case_03* |
| Output format violation (text before `## Findings`) | 2 | case_08, case_10 |
| Wasted tool calls (lookups or searches for things never reported) | 3 runs | case_02, case_12, case_13 |

\*case_03's indicator quotes pass the check, but one of them quotes the dietitian's conclusion, which names the diagnosis. See case_03 below.

The model's findings were right every time. The problems are all in **how the queries are written**: answer options the record doesn't support, a desired diagnosis inside an indicator, and "quotes" that aren't really quotes. A CDI manager would send 4 of the 7 query drafts back for edits.

## Case by case

### case_01: gap (hyponatremia). Log line 24. Sign-off: approve with edit
- Finding: E87.1, CC (looked up with principal J44.1), "query". Matches the key. Cites III (retrieved).
- **Missing reasonable option.** The options are "Hyponatremia" and "SIADH". The chart says "HCTZ held", and the urine studies (urine osm 412, urine Na 38) fit thiazide-induced hyponatremia as well as SIADH. There's no "Hyponatremia due to thiazide diuretic" option, although `query_standards.md` §4 requires every clinically reasonable option.
- The automated-check output for this run has 2 false warnings, from a quote-pairing bug that has since been fixed. The log keeps the original output.

### case_02: gap (acute blood loss anemia). Log line 25. Sign-off: reject the query as drafted (finding is correct)
- Finding: D62, CC (looked up), "query", replacing D64.9. Matches the key. Cites III (retrieved).
- **Unsupported options.** The options include "Anemia of chronic disease" and "Iron deficiency anemia". The record has no iron studies, no chronic disease that causes anemia, and no microcytosis. It shows an acute Hgb drop from 12.6 to 7.4 during an active diverticular bleed. This breaks `query_standards.md` §5: "Never introduce a diagnosis, or an answer option, that has no clinical support in the record."
- **Non-verbatim "quote".** The indicator `"Hgb (g/dL) ... 12.6 [HD1 arrival] ... 9.8 [HD1 6h] ... 7.4 [HD2]"` was rebuilt from the labs table, with ellipses and bracketed labels, yet presented in quotation marks. The automated check flagged it.
- Some indicators have no Hospital Day, such as "serial H/H q6h HD1-2", which repeats the medication line. §1 asks for values with dates.
- **Wasted calls:** 8 tool calls, including `search_codes("orthostatic hypotension")` and `lookup_code("I95.1")` for a condition it never reported.
- Strongest "Failed run" candidate. The finding is right, but the query is one a CDI manager must not send.

### case_03: gap (severe protein-calorie malnutrition). Log line 22. Sign-off: approve with edit
- Finding: E43, MCC (looked up with principal J69.0), "query". Matches the key. Cites I.B.14 (retrieved).
- **Leading under §1.** One clinical indicator is the dietitian's conclusion: "Meets ASPEN/AND criteria for severe protein-calorie malnutrition in the context of chronic illness." That puts the desired diagnosis, and its severity, into the query. `query_standards.md` §1 says to present indicators "without stating or implying the desired diagnosis". The case_08 query handled the same situation correctly: it quoted only the RD's measurements.
- "Mild protein-calorie malnutrition" has weak support (15% weight loss, BMI 16.8, severe wasting). It's acceptable as a range option, but borderline under §5.

### case_07: double gap (HF type/acuity + AKI). Log line 26. Sign-off: approve with edits
- Findings: N17.9, CC and I50.33, MCC (both looked up). Both "query". Matches the key. Cites III and I.C.9.a (both retrieved).
- **Unsupported option (Query 2).** "Acute combined systolic and diastolic heart failure" isn't supported, since the echo this admission shows EF 55–60% and no wall-motion abnormality. Violates §5.
- **Non-verbatim "quotes".** `"BUN 24"`, `"BUN 44"` and `"BNP ... 410"` are table values rewritten as quotes. The numbers are right, but the text isn't in the chart. One HPI quote is mangled: `"diastolic dysfunction" on echo ~1 yr ago (EF 60%, grade II DD)"` has unbalanced quotation marks.
- Query 1 (AKI) is good: neutral, with "Prerenal azotemia / volume depletion" and "No acute renal diagnosis" as fair alternatives.

### case_08: double gap (severe PCM query + pressure ulcer stage 4 coding fix). Log line 27. Sign-off: approve
- Findings: L89.154, MCC as a **coding correction** (correct; the WOC stage is usable under I.B.14), and E43, MCC as a query. Both match the key, including the action. Cites I.B.14, I.C.12.a and III (all retrieved).
- **Format violation.** The output starts with "Now I have enough to compile the findings." before `## Findings`.
- **Spliced quote.** The coding-correction note quotes `"Stage 4 pressure injury... bone palpable at base"`, which joins two parts of the WOC note with an ellipsis.
- The malnutrition query quotes only the RD's measurements, not the RD's diagnosis. That is the right handling, and the contrast with case_03.

### case_10: negative (insignificant labs). Log line 28. Sign-off: approve
- "No query indicated". It correctly cites III.B for the single Na 133 and K 3.4 with no repeat test, and correctly calls the WBC 13.2 and T 38.0 expected with cholecystitis. Matches the key. Cites III.B (retrieved). 1 tool call.
- **Format violation.** A full reasoning paragraph comes before `## Findings`.

### case_12: trap (albumin-only "malnutrition"). Log line 29. Sign-off: approve
- "No query indicated". It correctly declined to query malnutrition on albumin and prealbumin alone, citing the RD's statement that criteria were not met. Matches the key. Cites III.B (retrieved).
- It refers to "Rule 5" of its own system prompt, which leaks prompt internals into a work product.
- **Wasted calls:** 4 of 6 calls went to a diabetes-with-hyperglycemia idea (E11.65, NONE), and it looked up E44.0 without reporting it.
- It calls malnutrition "ruled out". Strictly, the RD said criteria weren't met; no provider ruled anything out. The wording is a little loose, but the conclusion is right.

### case_13: trap (conflicting respiratory failure documentation). Log line 30. Sign-off: approve with edit
- Finding: J96.21, MCC (looked up; it also looked up the current J96.11, CC), "query" for conflicting documentation. Matches the key. Cites I.A.19 (retrieved; real; quote verbatim).
- **Unsupported option.** "Acute respiratory failure only" can't apply to a patient on continuous home O2 at 2 L with documented chronic respiratory failure. Violates §5.
- The query fairly presents both the hospitalist's and the pulmonologist's documentation, as a conflict query should. It does contain the hospitalist's diagnosis text. That is unavoidable for this type of query, but strictly it is in tension with §1, so policy should say whether conflict queries are an exception.
- The candidate code is J96.21 (hypoxia), but the hospitalist wrote "hypoxic **and hypercapnic**", which may support J96.22 or J96.20. The answer key has the same open question.
- **Wasted calls:** it looked up E09.65 (drug-induced diabetes) and R73.9 for nothing it reported.

### case_14: trap (ATN documented, coded as unspecified AKI). Log line 31. Sign-off: approve with correction to the rationale
- Finding: N17.0, MCC, as a **coding correction**, replacing N17.9. Matches the key, including the action. Cites I.B.14 (retrieved).
- **Misapplied guideline.** I.B.14 covers documentation by clinicians *other than the patient's provider* (BMI, pressure ulcer stage and similar). It is the wrong basis here: ATN is documented by the attending in the discharge diagnoses and by nephrology, a consulting provider. The right basis is the provider's own documentation (I.A.19 / Section III). The conclusion is right, but the citation would not survive an audit.
- **Wasted calls:** `search_codes("hypovolemia")` and `search_codes("hypotension")`, never used.

## Problems in my code (not the model's), found during review

1. **Automated quote check: false positives.** Of the 15 warnings across these runs, 9 are false:
   - quotes of guideline text ("other diagnoses", "If there is conflicting medical record documentation, query the provider.");
   - quotes of tool output ("NONE (not on CC/MCC list),") and of a code description;
   - quotes with a trailing comma inside the quotation marks;
   - and, in case_01, the old pairing bug.

   The other 6 warnings are real non-verbatim quotes (case_02, case_07 ×3, case_08, and one in case_08's notes). The check can't yet tell chart quotes from other quoted text.
2. **`reference.py` `CORE_LABELS` has the wrong section.** It lists `"I.B.19"` to mean "Code assignment and Clinical Criteria", but in FY 2027 that is **I.A.19**. `I.B.19.*` is disaster/external-cause guidance (hurricanes, Z codes). So the earlier `cdi.py` pipeline sent irrelevant sections to Claude on every request and never sent I.A.19. `cdi_query.py` doesn't use `CORE_LABELS` and isn't affected.
3. **The automated checks can't see answer-option quality.** All 4 unsupported options and the §1 leading indicator passed the checks. Today only a human reviewer catches these.

## Candidates for the writeup's "Failed run"

1. **case_02 (line 25):** correct finding, but a query offering anemia of chronic disease and iron deficiency anemia with no support, plus a reconstructed "quote". Clearest example of a query a CDI manager must reject.
2. **case_03 (line 22):** the desired diagnosis inside a clinical indicator (leading under §1). Good to pair with case_08 (line 27), which handled the same RD documentation correctly.
3. **case_14 (line 31):** right answer, wrong guideline rationale (I.B.14).
4. **case_13 (line 30)** or **case_07 (line 26):** an unsupported option in an otherwise good query.
