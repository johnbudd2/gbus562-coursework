"""Company folders for the week-05 supplier-response team.

A company folder (week-05/brookfield-bikes by default, or any folder given with
--org) holds:

    parts.csv              part number, description, stock on hand, supplier
    inbound.csv            purchase order, part number, supplier, quantity, arrival day
    orders.csv             order number, customer, contact email, product, quantity,
                           ship day, late penalty
    bill-of-materials.csv  product, part number, parts per unit
    suppliers.csv          supplier, contact name, contact email
    prompts/               analyst.txt, supplier-writer.txt, customer-writer.txt,
                           reviewer.txt, orchestrator.txt
    supplier-emails/       the .eml files to run

Column names differ between companies (Brookfield's orders.csv says model, qty,
ship_in_days; Copperline's says product, units, ship_by_day), so no column name
is assumed. Each file's header row is read, every header is split into words,
and each column is matched to the role it plays (see ROLES). If a role matches
more than one numeric column, only columns whose values are all whole numbers
are kept. A required role that matches no column, or still matches several, is
an error that names the file and its header. read_table() returns rows keyed by
role (product, quantity, per_unit, ship_day, ...), so the rest of the code never
sees a company's own column names.

The company's name is read from the "Sign as: <team>, <Company>" line in the
writer prompts (or made from the folder name if there is none).
"""

import csv
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ORG = "brookfield-bikes"

CSV_FILES = ("parts.csv", "inbound.csv", "orders.csv", "bill-of-materials.csv",
             "suppliers.csv")
PROMPT_NAMES = ("analyst", "supplier-writer", "customer-writer", "reviewer",
                "orchestrator")


class DataError(Exception):
    """A company folder, CSV file, or column is missing or can't be worked out."""


# ---------------------------------------------------------------------------
# Finding the folder
# ---------------------------------------------------------------------------

def resolve_org_dir(org):
    """Turn --org (a folder name inside week-05, or a path) into a folder path."""
    path = Path(org or DEFAULT_ORG)
    if not path.is_dir() and (HERE / path).is_dir():
        path = HERE / path
    return path.resolve()


def problems(org_dir, need_prompts=True):
    """Everything missing or unreadable in a company folder (empty list if fine)."""
    if not org_dir.is_dir():
        return [f"company folder not found: {org_dir}"]
    found = []
    for name in CSV_FILES:
        try:
            read_table(org_dir, name)
        except DataError as e:
            found.append(str(e))
    if need_prompts:
        for name in PROMPT_NAMES:
            if not (org_dir / "prompts" / f"{name}.txt").is_file():
                found.append(f"prompts/{name}.txt not found in {org_dir}")
    return found


# ---------------------------------------------------------------------------
# Working out the columns from a header row
# ---------------------------------------------------------------------------

def words(header):
    """'qty_per_bike' -> {'qty', 'per', 'bike'};  'shipByDay' -> {'ship', 'by', 'day'}."""
    header = re.sub(r"([a-z])([A-Z])", r"\1 \2", header.strip())
    return {w for w in re.split(r"[^a-z0-9]+", header.lower()) if w}


def has(w, *options):
    return any(o in w for o in options)


# role -> (test on the header's words, holds whole numbers?)
ROLES = {
    "contact_email": (lambda w: has(w, "email", "mail"), False),
    "order_id":      (lambda w: has(w, "order") and has(w, "id", "number", "no", "num", "ref")
                      or w == {"order"}, False),
    "po_number":     (lambda w: has(w, "po") or has(w, "purchase"), False),
    "part_id":       (lambda w: has(w, "part", "component") and not has(w, "per", "each")
                      and (has(w, "id", "number", "no", "num", "code", "sku")
                           or len(w) == 1), False),
    "per_unit":      (lambda w: has(w, "per", "each"), True),
    "late_penalty":  (lambda w: has(w, "penalty", "penalties"), False),
    "on_hand":       (lambda w: has(w, "hand", "onhand", "stock", "inventory"), True),
    "arrival_day":   (lambda w: has(w, "arrive", "arrives", "arrival", "arriving", "eta"),
                      True),
    "ship_day":      (lambda w: has(w, "ship", "ships", "shipping", "due", "deliver",
                                    "delivery", "dispatch"), True),
    "quantity":      (lambda w: has(w, "qty", "quantity", "units", "count", "amount",
                                    "ordered"), True),
    "product":       (lambda w: has(w, "product", "model", "machine", "item", "sku",
                                    "bike", "unit") and not has(w, "part", "component"),
                      False),
    "customer":      (lambda w: has(w, "customer", "client", "buyer", "account"), False),
    "supplier":      (lambda w: has(w, "supplier", "vendor"), False),
    "contact_name":  (lambda w: has(w, "contact", "name"), False),
    "description":   (lambda w: has(w, "description", "desc", "name", "title"), False),
}

# The roles each file needs (True) or may have (False). Roles are matched in
# the order of ROLES, so a specific role (per_unit) claims its column before a
# broader one (quantity) can.
TABLES = {
    "parts.csv": {"part_id": True, "description": False, "on_hand": True,
                  "supplier": True},
    "inbound.csv": {"po_number": False, "part_id": True, "supplier": False,
                    "quantity": True, "arrival_day": True},
    "orders.csv": {"order_id": True, "customer": True, "contact_email": False,
                   "product": True, "quantity": True, "ship_day": True,
                   "late_penalty": False},
    "bill-of-materials.csv": {"product": True, "part_id": True, "per_unit": True},
    "suppliers.csv": {"supplier": True, "contact_name": False, "contact_email": False},
}

ROLE_LABELS = {
    "per_unit": "parts per unit", "ship_day": "ship day", "arrival_day": "arrival day",
    "on_hand": "stock on hand", "part_id": "part number", "order_id": "order number",
    "po_number": "purchase order", "late_penalty": "late penalty",
    "contact_email": "contact email", "contact_name": "contact name",
}


def is_whole_number(value):
    return bool(re.fullmatch(r"\s*-?\d+\s*", value or ""))


def map_columns(name, header, rows):
    """Return {role: column name or None} for one file, from its header and rows."""
    wanted = TABLES[name]
    free = [h for h in header if h]
    mapping = {}
    for role, (test, numeric) in ROLES.items():
        if role not in wanted:
            continue
        matches = [h for h in free if test(words(h))]
        if len(matches) > 1 and numeric:
            whole = [h for h in matches if rows and all(is_whole_number(r.get(h)) for r in rows)]
            matches = whole or matches
        if len(matches) > 1:
            raise DataError(
                f"{name}: can't tell which column holds the {ROLE_LABELS.get(role, role)}: "
                f"{', '.join(matches)} (header: {', '.join(header)})")
        if not matches:
            if wanted[role]:
                raise DataError(
                    f"{name}: no column holds the {ROLE_LABELS.get(role, role)} "
                    f"(header: {', '.join(header)})")
            mapping[role] = None
            continue
        mapping[role] = matches[0]
        free.remove(matches[0])
    return mapping


def _read(org_dir, name):
    path = Path(org_dir) / name
    if not path.is_file():
        raise DataError(f"{path} not found")
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = [h.strip() for h in (reader.fieldnames or [])]
        rows = [{(k or "").strip(): (v or "").strip() for k, v in row.items() if k}
                for row in reader if any((v or "").strip() for v in row.values())]
    return header, rows


def columns(org_dir, name):
    """{role: the company's column name (or None)} for one file."""
    header, rows = _read(org_dir, name)
    return map_columns(name, header, rows)


def read_table(org_dir, name):
    """Read one CSV file as rows keyed by role. A missing optional column gives ''."""
    header, rows = _read(org_dir, name)
    mapping = map_columns(name, header, rows)
    return [{role: (row.get(col, "") if col else "") for role, col in mapping.items()}
            for row in rows]


def describe_columns(org_dir):
    """Lines showing which column was chosen for each role, file by file."""
    lines = []
    for name in CSV_FILES:
        mapping = columns(org_dir, name)
        shown = ", ".join(f"{ROLE_LABELS.get(r, r.replace('_', ' '))}={c}"
                          for r, c in mapping.items() if c)
        lines.append(f"{name}: {shown}")
    return lines


# ---------------------------------------------------------------------------
# The company's name
# ---------------------------------------------------------------------------

SIGN_AS = re.compile(r"Sign as:\s*[^,\n]+,\s*([^\n]+)", re.IGNORECASE)


def company_name(org_dir):
    """'Copperline Coffee Machines', from a writer prompt's 'Sign as:' line."""
    for prompt in ("customer-writer", "supplier-writer"):
        path = Path(org_dir) / "prompts" / f"{prompt}.txt"
        if path.is_file():
            match = SIGN_AS.search(path.read_text(encoding="utf-8"))
            if match:
                return match.group(1).strip().rstrip(".")
    return Path(org_dir).name.replace("-", " ").replace("_", " ").title()


def short_name(org_dir):
    """'Copperline', the first word of the company's name."""
    return company_name(org_dir).split()[0]
