"""MCP server for Hartwell Foods HR: policy search and leave balance lookup.

It offers two tools to an AI app (Claude Code, or your own agent):

- search_policy(query): returns the 4 HR-document chunks closest to the query,
  each labeled with its source file and section.
- get_leave_balance: returns an employee's vacation, sick, and personal days left
  from hartwell-hr/employees.csv. It has two modes:
    * HR staff mode (default): takes an employee's work email.
    * Employee mode (--as-employee EMAIL): takes no inputs and can return only
      that one employee's balance.

Usage (from the top of the repository):
    .venv\\Scripts\\python.exe week-04\\hr_mcp_server.py                      # HR staff mode (stdio)
    .venv\\Scripts\\python.exe week-04\\hr_mcp_server.py --as-employee dana.whitfield@hartwellfoods.example
    .venv\\Scripts\\python.exe week-04\\hr_mcp_server.py --rebuild            # re-index the HR docs, then exit

Normally an AI app starts this server for you; see the claude mcp add command in
the week-04 notes. The server talks to the app over stdin/stdout, so nothing in
this file may print to stdout: all messages go to stderr.

How search works (adapted from week-03/ask.py):
1. Each HTML page in hartwell-hr/hr-docs/ is split into one chunk per <h2>
   section. Each chunk starts with the document title, section heading, and
   version line, so it makes sense on its own.
2. The chunks are embedded with Chroma's built-in model (all-MiniLM-L6-v2) and
   stored in week-04/chroma_db/ (kept out of Git). The database is built the first
   time it is needed; after changing the documents, run with --rebuild.
3. A search returns the 4 most similar chunks. Unlike ask.py, the server doesn't
   call Claude itself: the AI app that called the tool writes the answer, so the
   server never needs an API key.
"""

import argparse
import contextlib
import csv
import logging
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "hartwell-hr"
DOCS_DIR = DATA_DIR / "hr-docs"
EMPLOYEES_CSV = DATA_DIR / "employees.csv"
DB_DIR = HERE / "chroma_db"
COLLECTION_NAME = "hr_docs"
TOP_K = 4

log = logging.getLogger("hr_mcp_server")


# ---------------------------------------------------------------------------
# Loading the HR documents into chunks (from week-03/ask.py)
# ---------------------------------------------------------------------------

def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def load_html(path):
    """Split an HTML page at its <h2> headings. Returns (title, meta, sections)."""
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
        if el.find_parent(["ul", "ol", "table"]):  # already captured with its parent
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


def make_chunks(path):
    title, meta, sections = load_html(path)
    chunks = []
    for heading, paragraphs in sections:
        header = [f"Document: {title}", f"Section: {heading}"]
        if meta:
            header.append(f"Document status: {meta}")
        chunks.append({
            "text": "\n".join(header) + "\n\n" + "\n\n".join(paragraphs),
            "metadata": {"source": path.name, "title": title,
                         "section": heading, "status": meta},
        })
    return chunks


# ---------------------------------------------------------------------------
# The Chroma database
# ---------------------------------------------------------------------------

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
    """(Re)build week-04/chroma_db/ from every HTML page in hr-docs/."""
    if DB_DIR.exists():
        shutil.rmtree(DB_DIR)
    log.info("Building the HR document database from %s ...", DOCS_DIR)
    # Keep anything a library prints off stdout, which carries the MCP messages.
    with contextlib.redirect_stdout(sys.stderr):
        collection = open_collection(create=True)
        total = 0
        for path in sorted(DOCS_DIR.glob("*.htm*")):
            chunks = make_chunks(path)
            if not chunks:
                continue
            collection.add(
                ids=[f"{path.name}#{i}" for i in range(len(chunks))],
                documents=[c["text"] for c in chunks],
                metadatas=[c["metadata"] for c in chunks],
            )
            log.info("  %s: %d chunks", path.name, len(chunks))
            total += len(chunks)
    log.info("Done: %d chunks stored in %s", total, DB_DIR)
    return collection


_collection = None


def get_collection():
    """Open the database once per server run, building it if it doesn't exist yet."""
    global _collection
    if _collection is None:
        try:
            with contextlib.redirect_stdout(sys.stderr):
                collection = open_collection()
                if collection.count() == 0:
                    raise ValueError("empty database")
            _collection = collection
        except Exception:
            _collection = build_database()
    return _collection


# ---------------------------------------------------------------------------
# Tool logic
# ---------------------------------------------------------------------------

def search_policy(query: str) -> str:
    """Search Hartwell Foods' HR policy documents.

    Returns the 4 document sections most similar to the query, each with its
    source file, section heading, and similarity score (0 to 1, higher is closer).
    Use only these sections for policy facts, and cite the source file for each
    fact. The closest sections may still not answer the question: if none of
    them do, say the policies don't cover it rather than guessing.

    Args:
        query: What to look for, in plain words (for example "carry over unused vacation days").
    """
    from mcp.server.mcpserver.exceptions import ToolError

    query = query.strip()
    if not query:
        raise ToolError("The search query is empty. Describe the policy topic to search for.")
    with contextlib.redirect_stdout(sys.stderr):
        result = get_collection().query(
            query_texts=[query], n_results=TOP_K,
            include=["documents", "metadatas", "distances"])

    parts = [f'Top {TOP_K} HR document sections for "{query}":']
    for rank, (text, meta, distance) in enumerate(zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0]), 1):
        similarity = 1 - distance  # cosine distance -> similarity
        parts.append(f"[{rank}] Source file: {meta['source']} | "
                     f"similarity {similarity:.3f}\n{text}")
    return "\n\n".join(parts)


def find_employee(email):
    """Return the employees.csv row for this email (any capitalization), or None."""
    wanted = email.strip().lower()
    with EMPLOYEES_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["email"].strip().lower() == wanted:
                return row
    return None


def leave_balance(email):
    from mcp.server.mcpserver.exceptions import ToolError

    row = find_employee(email) if email.strip() else None
    if row is None:
        raise ToolError(
            f'No employee record matches "{email.strip()}". Do not state, estimate, '
            "or guess any leave balance for this person. Say that HR could not find "
            "their record and a person from HR will follow up.")
    return (
        f"Leave balance for {row['name']} ({row['email']})\n"
        f"Department: {row['department']}\n"
        f"Manager: {row['manager'] or 'none listed'}\n"
        f"Vacation days left: {row['vacation_days_left']}\n"
        f"Sick days left: {row['sick_days_left']}\n"
        f"Personal days left: {row['personal_days_left']}\n"
        f"Source: employees.csv"
    )


# ---------------------------------------------------------------------------
# The MCP server
# ---------------------------------------------------------------------------

def create_server(as_employee=None):
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    read_only = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    server = MCPServer(
        "hartwell-hr",
        instructions="Hartwell Foods HR tools: search the HR policy documents and "
                     "look up leave balances from the employee records.")
    server.tool(annotations=read_only, structured_output=False)(search_policy)

    if as_employee:
        # Employee mode: the tool has no inputs, so it can only ever return this
        # one employee's balance, whatever the model asks for.
        @server.tool(name="get_leave_balance", annotations=read_only,
                     structured_output=False)
        def get_own_leave_balance() -> str:
            """Look up the leave balance of the employee this session is for.

            Returns their vacation, sick, and personal days left, with their
            department and manager. It takes no inputs and cannot look up any
            other employee. Never state a balance that didn't come from this tool.
            """
            return leave_balance(as_employee)
    else:
        @server.tool(annotations=read_only, structured_output=False)
        def get_leave_balance(email: str) -> str:
            """Look up an employee's leave balance by their work email (for HR staff).

            Returns their vacation, sick, and personal days left, with their
            department and manager. Never state a balance that didn't come from
            this tool.

            Args:
                email: The employee's work email, for example dana.whitfield@hartwellfoods.example.
            """
            return leave_balance(email)

    return server


def main():
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(name)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="MCP server with Hartwell Foods HR tools (policy search and "
                    "leave balances).")
    parser.add_argument("--as-employee", metavar="EMAIL",
                        help="employee mode: get_leave_balance takes no inputs and "
                             "returns only this employee's balance")
    parser.add_argument("--rebuild", action="store_true",
                        help="rebuild the HR document database in week-04/chroma_db, "
                             "then exit")
    args = parser.parse_args()

    if args.rebuild:
        build_database()
        return
    if args.as_employee is not None:
        if not args.as_employee.strip():
            parser.error("--as-employee needs an email address")
        if find_employee(args.as_employee) is None:
            # Keep running: the tool itself reports the missing record.
            log.warning("No employee record matches %s; get_leave_balance will "
                        "return an error.", args.as_employee)
        log.info("Employee mode: leave balances limited to %s", args.as_employee)

    create_server(args.as_employee).run()  # stdio


if __name__ == "__main__":
    main()
