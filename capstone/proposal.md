Problem: CDI specialists review inpatient charts by hand to find conditions the evidence supports but the physician never clearly documented, especially CCs and MCCs. This is slow and inconsistent between specialists. Missed documentation means the record understates how sick the patient was, which affects the hospital's payment and quality scores. And a poorly worded query that suggests the answer creates compliance risk.

Who uses it: CDI specialists at an acute-care hospital, on every inpatient chart they review each working day, plus the CDI manager who audits their queries weekly.

Techniques: RAG and an agent. RAG because whether a condition is reportable depends on specific ICD-10-CM guideline sections (such as III.B, abnormal findings, and I.B.14, documentation by other clinicians) that the tool must retrieve and cite rather than recall. An agent because what needs checking depends on what is in each chart, so the model decides which guideline sections to search and which codes to look up in the CMS CC/MCC table.

Human oversight: Draft-only, because a physician query is a compliance-sensitive message. The model can be confidently wrong, as when it once queried an expected, untreated post-operative hemoglobin drop. So a CDI specialist reviews every draft, and nothing is ever sent automatically.
