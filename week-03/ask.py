"""Ask questions about Copperline Manufacturing's finance documents (RAG).

Usage (from the top of the repository):
    .venv\\Scripts\\python.exe week-03\\ask.py "How much can I spend on dinner in Boston?"
    .venv\\Scripts\\python.exe week-03\\ask.py              # interactive: ask several questions
    .venv\\Scripts\\python.exe week-03\\ask.py --rebuild    # re-index after adding/removing documents

How it works:
1. Every document in copperline-docs/ (HTML, PDF, Markdown, or plain text) is split
   into one chunk per section. Each chunk starts with the document title and section
   heading so it makes sense on its own, and records the file it came from.
2. The chunks are embedded with Chroma's built-in model (all-MiniLM-L6-v2) and stored
   in week-03/chroma_db/ (kept out of Git).
3. For each question, the 4 most similar chunks are retrieved and printed, then sent
   to Claude with instructions to answer only from them and cite the source file.
"""

import argparse
import os
import re
import shutil
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS_DIR = HERE / "copperline-docs"
DB_DIR = HERE / "chroma_db"
COLLECTION_NAME = "copperline_docs"

# The API key lives outside the repo, in the folder that contains it.
KEY_FOLDER = HERE.parent.parent
KEY_FILES = [KEY_FOLDER / "API-Key.txt", KEY_FOLDER / "course_API.txt"]

MODEL = "claude-sonnet-5"
TOP_K = 4
PREVIEW_CHARS = 150
MAX_CHUNK_WORDS = 400  # longer sections are split at paragraph breaks

SUPPORTED_TYPES = {".html", ".htm", ".pdf", ".md", ".txt"}

# Definitions that a document gives once but its other sections rely on. They are
# repeated in every chunk from that file so each chunk can be understood alone.
DOCUMENT_NOTES = {
    "month-end-close-calendar.html": (
        "Business day 1 is the first business day of the new month. Business day "
        "minus 1 is the last business day of the month being closed."
    ),
}

SYSTEM_PROMPT = """You answer questions from Copperline Manufacturing employees about \
the company's finance policies. You are given numbered source excerpts retrieved from \
Copperline's finance documents.

Rules:
- Answer only from the provided sources. Do not use outside knowledge or assumptions \
about typical company policies.
- After each fact, cite the source file it came from in square brackets, for example \
[travel-expense-policy.html].
- If the sources do not contain the answer, say clearly that the provided documents \
don't answer the question. If they answer only part of it, answer that part and say \
what is missing.
- Keep the answer short and direct, and include the specific amounts, limits, \
deadlines, and approvers that the sources give."""


# ---------------------------------------------------------------------------
# Loading documents into sections
# ---------------------------------------------------------------------------

def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def load_html(path):
    """Split an HTML page at its <h2> headings."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    for tag in soup(["style", "script", "nav", "header", "footer"]):
        tag.decompose()
    for tag in soup.select(".bar"):  # intranet banner repeated on every page
        tag.decompose()

    h1 = soup.find("h1")
    title = clean(h1.get_text()) if h1 else (
        clean(soup.title.get_text()).split("|")[0].strip() if soup.title else path.stem
    )
    meta_tag = soup.select_one(".meta")
    meta = clean(meta_tag.get_text()) if meta_tag else ""

    root = soup.find("main") or soup.body or soup
    sections = []
    heading, paragraphs = "Introduction", []

    def flush():
        if paragraphs:
            sections.append((heading, paragraphs))

    for el in root.find_all(["h2", "h3", "p", "ul", "ol", "table"]):
        if el is h1 or el is meta_tag:
            continue
        # Skip elements nested inside a list/table we already captured.
        if el.find_parent(["ul", "ol", "table"]):
            continue
        if el.name == "h2":
            flush()
            heading, paragraphs = clean(el.get_text()), []
        elif el.name == "h3":
            paragraphs.append(clean(el.get_text()) + ":")
        elif el.name in ("ul", "ol"):
            items = [clean(li.get_text()) for li in el.find_all("li")]
            paragraphs.append("\n".join(f"- {item}" for item in items if item))
        elif el.name == "table":
            rows = []
            for tr in el.find_all("tr"):
                cells = [clean(c.get_text()) for c in tr.find_all(["th", "td"])]
                rows.append(" | ".join(cells))
            paragraphs.append("\n".join(rows))
        else:
            text = clean(el.get_text())
            if text:
                paragraphs.append(text)
    flush()
    return title, meta, sections


def load_pdf(path):
    """Split a text-based PDF at its headings, found by font size.

    Body text is the most common font size. Lines noticeably smaller (page
    footers) are dropped. The most common larger size is the section-heading
    size; anything larger than that before the first heading is the title area.
    """
    import pdfplumber

    lines = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for line in page.extract_text_lines():
                chars = [c for c in line["chars"] if c["text"].strip()]
                if not chars:
                    continue
                size = round(statistics.median(c["size"] for c in chars), 1)
                lines.append({"text": clean(line["text"]), "size": size,
                              "top": line["top"], "bottom": line["bottom"],
                              "page": page.page_number, "n_chars": len(chars)})

    if not lines:
        print(f"  Skipping {path.name}: no extractable text (scanned PDF?)")
        return path.stem, "", []

    sizes = {}
    for line in lines:
        sizes[line["size"]] = sizes.get(line["size"], 0) + line["n_chars"]
    body_size = max(sizes, key=sizes.get)
    lines = [l for l in lines if l["size"] >= body_size - 1]  # drop footers

    larger = [l["size"] for l in lines if l["size"] > body_size + 0.5]
    heading_size = statistics.mode(larger) if larger else None

    title, meta_lines, sections = path.stem, [], []
    heading, paragraphs, current = None, [], ""
    prev = None
    for line in lines:
        if heading_size and abs(line["size"] - heading_size) < 0.3:
            if current:
                paragraphs.append(current)
            if heading is not None and paragraphs:
                sections.append((heading, paragraphs))
            heading, paragraphs, current = line["text"], [], ""
        elif heading is None:
            # Title area: the last line larger than a section heading is the
            # title; body-size lines after it are the version/owner line.
            if heading_size and line["size"] > heading_size:
                title, meta_lines = line["text"], []
            elif title != path.stem:
                meta_lines.append(line["text"])
        else:
            # A gap bigger than normal line spacing starts a new paragraph.
            new_para = (prev is None or line["page"] != prev["page"]
                        or line["top"] - prev["bottom"] > 0.6 * line["size"])
            if new_para and current:
                paragraphs.append(current)
                current = ""
            current = f"{current} {line['text']}".strip()
        prev = line
    if current:
        paragraphs.append(current)
    if heading is not None and paragraphs:
        sections.append((heading, paragraphs))
    elif heading is None:  # no headings found: the whole document is one section
        body = " ".join(l["text"] for l in lines)
        sections.append(("Full document", [body]))
    return title, " ".join(meta_lines), sections


def load_markdown(path):
    """Split Markdown / plain text at '## ' headings; '# ' is the title."""
    title, sections = path.stem, []
    heading, paragraphs, current = "Introduction", [], []

    def end_paragraph():
        if current:
            paragraphs.append(" ".join(current))
            current.clear()

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("# ") and title == path.stem:
            title = line[2:].strip()
        elif re.match(r"#{2,3} ", line):
            end_paragraph()
            if paragraphs:
                sections.append((heading, paragraphs))
            heading, paragraphs = line.lstrip("#").strip(), []
        elif not line:
            end_paragraph()
        else:
            current.append(line)
    end_paragraph()
    if paragraphs:
        sections.append((heading, paragraphs))
    return title, "", sections


LOADERS = {".html": load_html, ".htm": load_html, ".pdf": load_pdf,
           ".md": load_markdown, ".txt": load_markdown}


# ---------------------------------------------------------------------------
# Building chunks and the Chroma database
# ---------------------------------------------------------------------------

def split_long(paragraphs):
    """Group paragraphs into parts of at most MAX_CHUNK_WORDS words."""
    parts, current, words = [], [], 0
    for para in paragraphs:
        n = len(para.split())
        if current and words + n > MAX_CHUNK_WORDS:
            parts.append(current)
            current, words = [], 0
        current.append(para)
        words += n
    if current:
        parts.append(current)
    return parts


def make_chunks(path):
    title, meta, sections = LOADERS[path.suffix.lower()](path)
    note = DOCUMENT_NOTES.get(path.name)
    chunks = []
    for heading, paragraphs in sections:
        parts = split_long(paragraphs)
        for i, part in enumerate(parts, 1):
            section = heading if len(parts) == 1 else f"{heading} (part {i} of {len(parts)})"
            header = [f"Document: {title}", f"Section: {section}"]
            if meta:
                header.append(f"Document status: {meta}")
            if note:
                header.append(f"Note: {note}")
            chunks.append({
                "text": "\n".join(header) + "\n\n" + "\n\n".join(part),
                "metadata": {"source": path.name, "title": title,
                             "section": section, "status": meta},
            })
    return chunks


def open_collection(create=False):
    import chromadb
    from chromadb.config import Settings
    from chromadb.utils import embedding_functions

    client = chromadb.PersistentClient(
        path=str(DB_DIR), settings=Settings(anonymized_telemetry=False))
    embed = embedding_functions.DefaultEmbeddingFunction()
    if create:
        return client.create_collection(
            COLLECTION_NAME, embedding_function=embed,
            configuration={"hnsw": {"space": "cosine"}})
    return client.get_collection(COLLECTION_NAME, embedding_function=embed)


def build_database():
    files = sorted(p for p in DOCS_DIR.iterdir() if p.is_file())
    if DB_DIR.exists():
        shutil.rmtree(DB_DIR)
    print(f"Building the document database from {DOCS_DIR.name}/ ...")
    collection = open_collection(create=True)

    total = 0
    for path in files:
        if path.suffix.lower() not in SUPPORTED_TYPES:
            print(f"  Skipping {path.name}: unsupported file type")
            continue
        chunks = make_chunks(path)
        if not chunks:
            continue
        collection.add(
            ids=[f"{path.name}#{i}" for i in range(len(chunks))],
            documents=[c["text"] for c in chunks],
            metadatas=[c["metadata"] for c in chunks],
        )
        print(f"  {path.name}: {len(chunks)} chunks")
        total += len(chunks)
    print(f"Done: {total} chunks stored in {DB_DIR.relative_to(HERE.parent)}\n")
    return collection


def get_collection():
    try:
        collection = open_collection()
        if collection.count() > 0:
            return collection
    except Exception:
        pass  # no database yet
    return build_database()


# ---------------------------------------------------------------------------
# Retrieval and answering
# ---------------------------------------------------------------------------

def retrieve(collection, question):
    result = collection.query(query_texts=[question], n_results=TOP_K,
                              include=["documents", "metadatas", "distances"])
    hits = []
    for text, meta, distance in zip(result["documents"][0], result["metadatas"][0],
                                    result["distances"][0]):
        hits.append({"text": text, "source": meta["source"],
                     "similarity": 1 - distance})  # cosine distance -> similarity
    return hits


def print_hits(hits):
    print(f"Top {len(hits)} retrieved chunks:")
    for rank, hit in enumerate(hits, 1):
        preview = clean(hit["text"])[:PREVIEW_CHARS]
        print(f"  {rank}. similarity {hit['similarity']:.3f} | {hit['source']}")
        print(f"     {preview}...")
    print()


def load_api_key():
    # The key file comes first; the environment variable is only a fallback.
    for path in KEY_FILES:
        if path.exists():
            key = path.read_text(encoding="utf-8").strip()
            if key:
                return key
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    sys.exit(f"No API key found. Put it in {KEY_FILES[0]} "
             "or set the ANTHROPIC_API_KEY environment variable.")


def answer(client, question, hits):
    import anthropic

    sources = "\n\n".join(
        f'<source number="{i}" file="{hit["source"]}">\n{hit["text"]}\n</source>'
        for i, hit in enumerate(hits, 1))
    prompt = f"{sources}\n\nQuestion: {question}"

    print("Answer:")
    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
            final = stream.get_final_message()
        print()
        if final.stop_reason == "refusal":
            print("(Claude declined to answer this question.)")
        elif final.stop_reason == "max_tokens":
            print("(The answer was cut off because it reached the length limit.)")
    except anthropic.AuthenticationError:
        print("\nError: the API key was rejected. Check the key file.")
    except anthropic.RateLimitError:
        print("\nError: rate limit reached. Wait a moment and try again.")
    except anthropic.APIStatusError as e:
        print(f"\nAPI error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        print("\nError: could not connect to the Claude API. Check your internet connection.")
    print()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(
        description="Answer questions from Copperline Manufacturing's finance documents.")
    parser.add_argument("question", nargs="*", help="your question (omit for interactive mode)")
    parser.add_argument("--rebuild", action="store_true",
                        help="rebuild the database after adding or removing documents")
    args = parser.parse_args()

    collection = build_database() if args.rebuild else get_collection()
    question = " ".join(args.question).strip()
    if args.rebuild and not question:
        return

    import anthropic
    client = anthropic.Anthropic(api_key=load_api_key())

    def ask(q):
        hits = retrieve(collection, q)
        print_hits(hits)
        answer(client, q, hits)

    if question:
        ask(question)
        return
    print("Ask a question about Copperline's finance documents (blank line to quit).")
    while True:
        try:
            q = input("\nQuestion: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            break
        print()
        ask(q)


if __name__ == "__main__":
    main()
