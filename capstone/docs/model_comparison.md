# Model comparison: `claude-sonnet-5` vs `claude-opus-5-5` on the test split

- **Command:** `python capstone/eval.py --split test --model claude-sonnet-5 claude-opus-5-5`
- **Report:** `runs/eval_20261126-180504.md`
- **Runs:** `runs/log.jsonl` lines 35–44 (Sonnet 5) and 45–54 (Opus 5.5), each logged by the tool's normal request function.
- **Cases:** the 10 held-out test cases (`case_15` … `case_24`): 4 gaps, 2 double gaps, 2 negatives and 2 traps, 8 expected gaps in all.
- **Same setup for both:** the same tool, the same prompt (including rule 7a, the case_03 fix) and one run per case.

## Headline numbers

| | claude-sonnet-5 | claude-opus-5-5 |
|---|---|---|
| Recall (exact code) | 75% (6/8) | **100% (8/8)** |
| Recall (3-character category) | 75% (6/8) | **100% (8/8)** |
| Precision (exact / category) | 67% (6/9) | **80% (8/10)** |
| False queries (negative cases or `should_not_query`) | 2 | **1** |
| Severity stated without a `lookup_code` call | 0 | 0 |
| Avg input tokens per run | 23,392 | **19,768** |
| Avg output tokens per run | 3,641 | **2,460** |
| Avg seconds per run | 36.8 | **22.4** |
| Est. cost per run (list price)\* | **$0.083** | $0.128 |
| Est. cost per 1,000 runs\* | **$83** | $128 |

\*The estimate uses list prices of $2/$10 per million input/output tokens for Sonnet 5 and $4/$20 for Opus 5.5 (`cost.py`). It prices every input token at the full rate. Prompt-cache reads are billed at about a tenth of that, so both figures overstate the true cost by a similar amount.

On this set, Opus 5.5 found every expected gap, made fewer false queries, and was faster (22 s vs 37 s). It used fewer tokens per run, but cost about 1.5× as much per run because its per-token price is twice Sonnet 5's.

## What is behind the numbers

Every miss and false query below was checked against the logged output, not just the score.

| Case | Sonnet 5 | Opus 5.5 | Notes |
|---|---|---|---|
| case_18 (chemo pancytopenia, D61.810) | **Empty output** (log line 38) | Found, query | Sonnet spent all 12 tool calls looking up codes (D70.1, D70.9, D69.59, D64.81, D61.818, D69.6, D61.810), and its forced final answer had no text. The tool printed only "All quotes found… every severity verified". **This is a harness bug as much as a model miss:** an empty answer is treated as a success, and the checks pass vacuously. |
| case_22 (negative: expected post-op Hgb drop) | **False query for D62** (log line 42) | Correctly no query | Sonnet read "expected postop blood loss, asymptomatic, no transfusion" as "clinically evaluated" under Section III and queried acute blood loss anemia. That is the lab-only pattern the system prompt forbids, and a query a CDI manager must reject. This is the single most serious error in either run. |
| case_16 (HF type/acuity) | I50.21 ✓, plus a J96.01 query | I50.21 ✓, plus a J96.01 query | Both models also queried acute respiratory failure (SpO2 89% on room air → 4 L, crackles, edema). The answer key lists it as `should_not_query` ("borderline"), so each model gets 1 false query. Because both models made the same call independently, the answer key may be too strict here. Review it rather than treating it as a clear model error. |
| case_19 (encephalopathy + hyponatremia) | Hyponatremia ✓; encephalopathy scored as missed | G93.41 ✓, E87.1 ✓, plus an E22.2 (SIADH) query | Sonnet chose F05 (delirium) as the candidate and listed G93.41 as the alternative. The answer key accepts only G93.41, so this scores as a miss although the query covers it, which is the same narrowness the old case-10 had. Opus's extra SIADH query cites a real conflict ("thiazide + SIADH picture" on HD1 vs. "thiazide related" at discharge). It lowers precision but is defensible. |

**Both models disagreed with the answer key on case_19's action.** Each treated hyponatremia as a **coding correction**, on the grounds that the physician wrote "Low Na — thiazide related". The key says "query". `eval.py` doesn't score actions, so this doesn't show in the numbers. It's worth deciding whether "Low Na" counts as provider documentation of hyponatremia.

**What a CDI manager would conclude:**
- Opus 5.5 produced no output that would have to be rejected outright.
- Sonnet 5 produced one false query on a negative case (case_22) and one silent empty answer (case_18).
- Once the answer key's questionable items are taken out (case_16's J96.01 and case_19's F05 vs. G93.41), the gap between the models is smaller than the headline. It still favors Opus.

## Caveats

1. **Small sample, one run each.** 10 cases with 8 expected gaps means each miss moves recall by 12.5 points, and model outputs vary from run to run. Treat this as directional, not as a measurement.
2. **The models ran at different effort levels.** `cdi_query.py` doesn't set `effort`, so each model used its default: `high` for Sonnet 5 and `medium` for Opus 5.5. That probably explains part of Opus's lower token use and shorter time. A fair comparison would pin `effort` for both.
3. **The test split has now been used.** These numbers are the first and only look at the held-out cases. Further tuning should be done on `dev`, or the test results stop meaning "unseen".
4. **`eval.py` doesn't measure query quality** (unsupported options, leading wording, verbatim quotes) **or the action** (query vs. coding correction). Those still need the manual review described in `dev-run-review.md`.

## Recommendation

Use **Opus 5.5** if accuracy and compliance risk matter more than cost. At about $128 per 1,000 reviews versus $83, the extra $45 buys roughly one more caught gap and one fewer false query per 10 charts on this set. One wrongful query is a compliance problem, and one missed MCC is lost documentation accuracy, so both cost more than the difference.

Before deciding, fix the harness bug from case_18: an empty final answer should be an error, not a pass. Then re-run with `effort` set to the same level for both models.
