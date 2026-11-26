# Reference sources

All files in this folder are public U.S. government publications, downloaded on
2026-11-26. They are used as published (only renamed). The tool reads them at
run time; CC/MCC status and exclusions always come from these tables.

| File here | Original file | Source |
|---|---|---|
| `icd10cm-guidelines-fy2027.pdf` | `ICD-10-CM-October-1-2026-FY27-Guidelines.pdf` | CDC/NCHS, ICD-10-CM FY 2027 release: https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/ICD10CM/2027/ |
| `icd10cm-codes-fy2027.txt` | `icd10cm-codes-2027.txt` (inside `icd10cm-code-descriptions-2027.zip`) | same CDC/NCHS folder |
| `cms-table-6i-mcc-fy2027.txt` | `CMS-1849-F Table 6I - Complete MCC List - FY 2027.txt` | CMS FY 2027 IPPS Final Rule (CMS-1849-F), Tables 6A–6K: https://www.cms.gov/files/zip/fy2027-ipps-fr-tables-6a-6k.zip |
| `cms-table-6j-cc-fy2027.txt` | `CMS-1849-F Table 6J - Complete CC List - FY 2027.txt` | same CMS zip |
| `cms-table-6k-cc-exclusions-fy2027.txt` | `CMS-1849-F Table 6K - Complete List of CC Exclusions - FY 2027.txt` | same CMS zip |

The two files with their original CMS names (`CMS-1849-F Table 6I …` and
`CMS-1849-F Table 6J …`) were already in this folder. They are byte-for-byte
identical to the renamed copies (same SHA-256). The tool uses the renamed copies.

These are FY 2027 files (ICD-10-CM effective October 1, 2026; MS-DRG v44).

## What the tool uses from Table 6K

- **Part 1:** each CC/MCC code with its PDX exclusion collection, and the
  collections themselves (principal diagnoses that make the CC/MCC a non-CC).
- **Part 2:** the 8 codes that are a CC/MCC only if the patient is discharged
  alive. These are loaded but not yet used, because the cases have no discharge
  status.
- **Part 3** (severity exclusions in select MS-DRGs) is not used: the tool does
  not assign MS-DRGs.

Check that the tables load and agree with each other (no API call):

```
.venv\Scripts\python.exe capstone\reference.py --check
```

The query rules in `../prompts/query-rules.md` are this project's own summary
of the principles in the AHIMA/ACDIS *Guidelines for Achieving a Compliant Query
Practice* (2022 update). That document is copyrighted and is not copied here.
