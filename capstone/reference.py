"""Reference data for the CDI tool: ICD-10-CM codes, CMS CC/MCC tables, and the
ICD-10-CM Official Guidelines (searchable with Chroma).

All files are public and live in capstone/reference/ (see reference/SOURCES.md).
CC/MCC status and CC exclusions always come from these tables, never from Claude.

Check the tables and build the guideline index without calling the API:
    python capstone/reference.py --check
    python capstone/reference.py --rebuild           # rebuild capstone/.chroma
    python capstone/reference.py --search "acute kidney injury"
"""

import argparse
import re
import shutil
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
REF_DIR = HERE / "reference"
CODES_FILE = REF_DIR / "icd10cm-codes-fy2027.txt"
MCC_FILE = REF_DIR / "cms-table-6i-mcc-fy2027.txt"
CC_FILE = REF_DIR / "cms-table-6j-cc-fy2027.txt"
EXCLUSIONS_FILE = REF_DIR / "cms-table-6k-cc-exclusions-fy2027.txt"
GUIDELINES_FILE = REF_DIR / "icd10cm-guidelines-fy2027.pdf"
FISCAL_YEAR = "FY 2027 (MS-DRG v44)"

DB_DIR = HERE / ".chroma"
COLLECTION_NAME = "icd10cm_guidelines_fy2027"


# ---------------------------------------------------------------------------
# ICD-10-CM codes
# ---------------------------------------------------------------------------

def normalize(code: str) -> str:
    """'n17.9 ' -> 'N179' (the tables store codes without the dot)."""
    return re.sub(r"[^A-Z0-9]", "", code.upper())


def dotted(code: str) -> str:
    """'N179' -> 'N17.9'."""
    code = normalize(code)
    return code if len(code) <= 3 else f"{code[:3]}.{code[3:]}"


class CodeTables:
    """ICD-10-CM billable codes plus the CMS CC/MCC and CC exclusion tables."""

    def __init__(self):
        self.descriptions = {}            # code -> description (billable codes only)
        self.level = {}                   # code -> "CC" | "MCC"
        self.pdx_collection = {}          # CC/MCC code -> collection number
        self.collections = {}             # collection number -> set of PDX codes
        self.alive_only = set()           # CC/MCC only if the patient is discharged alive
        self._load_codes()
        self._load_exclusions()

    def _load_codes(self):
        with CODES_FILE.open(encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split(None, 1)
                if len(parts) == 2:
                    self.descriptions[parts[0]] = parts[1].strip()

    def _load_exclusions(self):
        # " A419    MCC    0072:46 codes    ..." / "0126:1,070 codes" / "No Excl"
        part1 = re.compile(
            r"^\s([A-Z][0-9A-Z]{2,6})\s+(CC|MCC)\s+(?:\S+\s+)?(?:(\d{4}):[\d,]+ codes?\b|No Excl)")
        member = re.compile(r"^\s+([A-Z][0-9A-Z]{2,6})\s+\S")
        section = None
        collection = None
        with EXCLUSIONS_FILE.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("Appendix K Part 1"):
                    section = 1
                elif line.startswith("Appendix K Part 2"):
                    section, collection = 2, None
                elif line.startswith("Appendix K Part 3"):
                    break  # DRG-specific severity exclusions: outside this tool's scope
                elif line.startswith("PDX collection "):
                    collection = line.split()[-1]
                    self.collections[collection] = set()
                elif collection and (m := member.match(line)):
                    self.collections[collection].add(m.group(1))
                elif section == 1 and collection is None and (m := part1.match(line)):
                    code, lev, coll = m.groups()
                    self.level[code] = lev
                    if coll:
                        self.pdx_collection[code] = coll
                elif section == 2 and (m := member.match(line)):
                    self.alive_only.add(m.group(1))

    # --- lookups -----------------------------------------------------------

    def is_valid(self, code: str) -> bool:
        return normalize(code) in self.descriptions

    def describe(self, code: str) -> str:
        return self.descriptions.get(normalize(code), "(not a valid FY 2027 ICD-10-CM code)")

    def level_of(self, code: str) -> str | None:
        """'CC', 'MCC', or None (non-CC)."""
        return self.level.get(normalize(code))

    def excluded_by(self, code: str, principal: str) -> bool:
        """True if this CC/MCC counts as a non-CC when `principal` is the principal dx."""
        coll = self.pdx_collection.get(normalize(code))
        return bool(coll) and normalize(principal) in self.collections.get(coll, set())

    def effective_level(self, code: str, principal: str) -> str | None:
        """CC/MCC level as a secondary diagnosis with this principal diagnosis."""
        lev = self.level_of(code)
        if lev and self.excluded_by(code, principal):
            return None
        return lev

    def case_level(self, principal: str, secondary: list[str]) -> str | None:
        """Highest CC/MCC level among the secondary codes ('MCC', 'CC', or None)."""
        levels = {self.effective_level(c, principal) for c in secondary}
        return "MCC" if "MCC" in levels else "CC" if "CC" in levels else None


@lru_cache(maxsize=1)
def tables() -> CodeTables:
    return CodeTables()


def read_simple_list(path: Path) -> set[str]:
    """Codes listed in Table 6I or 6J (dotted codes in the first column)."""
    codes = set()
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            first = line.split("\t", 1)[0].strip()
            if re.fullmatch(r"[A-Z][0-9A-Z]{2}(\.[0-9A-Z]{1,4})?", first):
                codes.add(normalize(first))
    return codes


# ---------------------------------------------------------------------------
# ICD-10-CM Official Guidelines: section chunks in Chroma
# ---------------------------------------------------------------------------

PAGE_NOISE = re.compile(
    r"^(ICD-10-CM Official Guidelines for Coding and Reporting|FY 20\d\d|Page \d+ of \d+)\s*$")
HEADINGS = [  # (level, pattern) — level 0 = Section, 1 = A., 2 = 1., 3 = a.
    (0, re.compile(r"^Section (I|II|III|IV)\.\s+(.*)$")),
    (1, re.compile(r"^([A-Z])\.\s+(\S.*)$")),
    (2, re.compile(r"^(\d{1,2})\.\s+(\S.*)$")),
    (3, re.compile(r"^([a-z])\.\s+(\S.*)$")),
]
MAX_WORDS = 600
MIN_WORDS = 40


def guideline_lines() -> list[str]:
    from pypdf import PdfReader

    text = "\n".join(page.extract_text() or "" for page in PdfReader(GUIDELINES_FILE).pages)
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not PAGE_NOISE.match(ln)]
    # Skip the table of contents: the body starts at the second "Section I." heading.
    starts = [i for i, ln in enumerate(lines) if ln.startswith("Section I. ")]
    return lines[starts[1]:] if len(starts) > 1 else lines


def make_guideline_chunks() -> list[dict]:
    """Split the guidelines at headings down to the a./b. level, labeled like I.C.1.d."""
    path = ["", "", "", ""]
    titles = ["", "", "", ""]
    chunks, body = [], []
    label, title = "Intro", "Introduction"

    def flush():
        words = " ".join(body).split()
        if not words:
            return
        for start in range(0, len(words), MAX_WORDS):
            piece = " ".join(words[start:start + MAX_WORDS])
            chunks.append({"label": label, "title": title, "text": piece})

    for line in guideline_lines():
        heading = None
        for level, pattern in HEADINGS:
            m = pattern.match(line)
            # Headings are short and don't contain dotted TOC leaders.
            if m and len(line) < 110 and "...." not in line:
                heading = (level, m.group(1), m.group(2))
                break
        if heading and (heading[0] == 0 or path[0]):
            level, key, text = heading
            if level > 0 and not path[level - 1]:
                heading = None  # a numbered list item, not a heading
        if heading:
            flush()
            level, key, text = heading
            path[level], titles[level] = key, text.strip()
            for deeper in range(level + 1, 4):
                path[deeper], titles[deeper] = "", ""
            label = ".".join(p for p in path if p)
            title = titles[level]
            body = [line]
        else:
            body.append(line)
    flush()

    # Merge tiny chunks into the one before them.
    merged = []
    for c in chunks:
        if merged and len(c["text"].split()) < MIN_WORDS and len(merged[-1]["text"].split()) < MAX_WORDS:
            merged[-1]["text"] += " " + c["text"]
        else:
            merged.append(c)
    return merged


def _open_collection(create=False):
    import chromadb
    from chromadb.config import Settings
    from chromadb.utils import embedding_functions

    client = chromadb.PersistentClient(path=str(DB_DIR), settings=Settings(anonymized_telemetry=False))
    embed = embedding_functions.DefaultEmbeddingFunction()
    if create:
        return client.create_collection(COLLECTION_NAME, embedding_function=embed,
                                        configuration={"hnsw": {"space": "cosine"}})
    return client.get_collection(COLLECTION_NAME, embedding_function=embed)


def build_index():
    if DB_DIR.exists():
        shutil.rmtree(DB_DIR)
    chunks = make_guideline_chunks()
    collection = _open_collection(create=True)
    collection.add(
        ids=[f"g{i:04d}" for i in range(len(chunks))],
        documents=[c["text"] for c in chunks],
        metadatas=[{"label": c["label"], "title": c["title"], "order": i} for i, c in enumerate(chunks)],
    )
    print(f"Guideline index built: {len(chunks)} sections in {DB_DIR.relative_to(HERE.parent)}")
    return collection


@lru_cache(maxsize=1)
def guideline_collection():
    try:
        collection = _open_collection()
        if collection.count() > 0:
            return collection
    except Exception:
        pass  # no index yet
    return build_index()


# Sections every review needs: reporting additional diagnoses (Section III),
# code assignment and clinical criteria, documentation by other clinicians,
# and abnormal findings / uncertain diagnosis rules.
CORE_LABELS = ("III", "I.B.14", "I.B.19")


def core_sections() -> list[dict]:
    got = guideline_collection().get(include=["documents", "metadatas"])
    hits = [
        {"label": m["label"], "title": m["title"], "text": d, "order": m["order"]}
        for d, m in zip(got["documents"], got["metadatas"])
        if any(m["label"] == c or m["label"].startswith(c + ".") for c in CORE_LABELS)
    ]
    return sorted(hits, key=lambda h: h["order"])


def search(queries: list[str], per_query: int = 3, limit: int = 8,
           skip: set[str] | None = None, inpatient_only: bool = True) -> list[dict]:
    """Best-matching guideline sections across several queries, highest similarity first.
    Section IV (outpatient services) is left out unless inpatient_only is False."""
    skip = skip or set()
    best = {}
    collection = guideline_collection()
    for q in [q for q in queries if q.strip()]:
        r = collection.query(query_texts=[q], n_results=per_query + 2,
                             include=["documents", "metadatas", "distances"])
        for doc, meta, dist, id_ in list(zip(r["documents"][0], r["metadatas"][0],
                                             r["distances"][0], r["ids"][0])):
            if id_ in skip or (inpatient_only and meta["label"].split(".")[0] == "IV"):
                continue
            sim = 1 - dist
            if id_ not in best or sim > best[id_]["similarity"]:
                best[id_] = {"id": id_, "label": meta["label"], "title": meta["title"],
                             "text": doc, "order": meta["order"], "similarity": sim}
    return sorted(best.values(), key=lambda h: -h["similarity"])[:limit]


def format_sections(sections: list[dict]) -> str:
    return "\n\n".join(
        f'<guideline section="{s["label"]}" title="{s["title"]}">\n{s["text"]}\n</guideline>'
        for s in sections
    )


# ---------------------------------------------------------------------------
# Self-check
# ---------------------------------------------------------------------------

def check():
    t = tables()
    mcc_6i, cc_6j = read_simple_list(MCC_FILE), read_simple_list(CC_FILE)
    mcc_6k = {c for c, lev in t.level.items() if lev == "MCC"}
    cc_6k = {c for c, lev in t.level.items() if lev == "CC"}
    print(f"ICD-10-CM {FISCAL_YEAR}: {len(t.descriptions):,} billable codes")
    print(f"MCC codes: Table 6I {len(mcc_6i):,}, Table 6K {len(mcc_6k):,}, match: {mcc_6i == mcc_6k}")
    print(f"CC codes:  Table 6J {len(cc_6j):,}, Table 6K {len(cc_6k):,}, match: {cc_6j == cc_6k}")
    print(f"PDX exclusion collections: {len(t.collections):,}; "
          f"CC/MCC only if discharged alive: {len(t.alive_only)}")
    missing = [c for c in t.level if c not in t.descriptions]
    print(f"CC/MCC codes not in the code file: {len(missing)}")
    for code, pdx in [("N17.9", "J18.9"), ("E43", "J18.9"), ("A41.9", "A41.9"), ("I10", "J18.9")]:
        print(f"  {dotted(code):8} {t.describe(code)[:45]:45} level={t.level_of(code)} "
              f"with PDX {pdx}: {t.effective_level(code, pdx)}")
    chunks = make_guideline_chunks()
    print(f"Guideline sections: {len(chunks)}; core sections: "
          f"{[c['label'] for c in chunks if any(c['label'] == x or c['label'].startswith(x + '.') for x in CORE_LABELS)]}")


CSV_FILE = REF_DIR / "cc_mcc.csv"


def export_csv(path: Path = CSV_FILE):
    """Write every CC/MCC code (from CMS Tables 6I/6J/6K) to one CSV file."""
    import csv

    t = tables()
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["code", "description", "severity", "pdx_exclusion_collection",
                    "cc_mcc_only_if_discharged_alive"])
        for code in sorted(t.level):
            w.writerow([dotted(code), t.describe(code), t.level[code],
                        t.pdx_collection.get(code, ""), "yes" if code in t.alive_only else ""])
    print(f"Wrote {len(t.level):,} CC/MCC codes to {path.relative_to(HERE.parent)} "
          f"(source: CMS FY 2027 Tables 6I, 6J, 6K)")


def main():
    parser = argparse.ArgumentParser(description="CDI tool reference data")
    parser.add_argument("--export-csv", action="store_true",
                        help=f"write {CSV_FILE.name} (every CC/MCC code and its severity)")
    parser.add_argument("--check", action="store_true", help="check the tables and guideline sections")
    parser.add_argument("--rebuild", action="store_true", help="rebuild the guideline index")
    parser.add_argument("--search", help="search the guideline index")
    args = parser.parse_args()
    if args.check:
        check()
    if args.export_csv:
        export_csv()
    if args.rebuild:
        build_index()
    if args.search:
        for h in search([args.search], per_query=5, limit=5):
            print(f"{h['similarity']:.2f}  {h['label']:12} {h['title'][:60]}")
    if not (args.check or args.export_csv or args.rebuild or args.search):
        parser.print_help()


if __name__ == "__main__":
    main()
