"""MCP server for one company's supply data (Brookfield Bicycle Company by default).

It reads the CSV files in a company folder: week-05/brookfield-bikes (the
default) or the folder given with --org, such as copperline-coffee. Each
company names its columns its own way, so org.py works out from each header
row which column holds the product, the quantity, the parts per unit, the ship
day, and so on (see org.py):

    parts.csv              part number, description, stock on hand, supplier
    inbound.csv            purchase order, part number, supplier, quantity, arrival day
    orders.csv             order number, customer, product, quantity, ship day, late penalty
    bill-of-materials.csv  product, part number, parts per unit
    suppliers.csv          (not used by these tools)

It offers three read-only tools:

- get_part(part_id): stock on hand, scheduled shipments, and the products that
  use the part.
- get_orders_for_part(part_id): the open orders that need the part, with units
  needed (units ordered x parts per unit), ship day, and late penalty.
- check_coverage(part_id, arrivals): replaces the part's scheduled shipments
  with a new list of arrivals and works out, for each order, whether it is
  covered, or how many units it is short and the first day it can ship.

All days are counted from today (day 0). Stock on hand is available on day 0;
an arrival on day N can be used by an order shipping on day N.

How check_coverage fills orders: orders are filled complete (never part of an
order), one at a time in ship-day order (ties by order_id). An order can't ship
until every order before it has shipped, so a short order holds up the ones
after it. In practice, order k can ship on the first day when stock plus
arrivals so far covers the units of orders 1..k together.

Usage (from the top of the repository):
    .venv\\Scripts\\python.exe week-05\\supply_mcp_server.py                       # stdio
    .venv\\Scripts\\python.exe week-05\\supply_mcp_server.py --org copperline-coffee
    .venv\\Scripts\\python.exe week-05\\supply_mcp_server.py --org path\\to\\folder

--data is an older name for --org and still works.

Normally an AI app starts this server for you (see `claude mcp add` in the
week-05 notes). The server talks to the app over stdin/stdout, so nothing in
this file may print to stdout: all messages go to stderr. It reads no API key.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field

import org
from org import DataError

HERE = Path(__file__).resolve().parent
DEFAULT_DATA = org.DEFAULT_ORG

# Set by set_data_dir().
DATA_DIR = HERE / DEFAULT_DATA

TOOL_FILES = ("parts.csv", "inbound.csv", "orders.csv", "bill-of-materials.csv")

log = logging.getLogger("supply_mcp_server")


# ---------------------------------------------------------------------------
# Reading the CSV files (re-read on every call, so edits show up at once)
# ---------------------------------------------------------------------------

def set_data_dir(data):
    """Point the server at one company folder. Returns an error message or None."""
    global DATA_DIR
    DATA_DIR = org.resolve_org_dir(data)
    if not DATA_DIR.is_dir():
        return f"company folder not found: {data}"
    try:
        for name in TOOL_FILES:
            read_csv(name)
    except DataError as e:
        return str(e)
    return None


def read_csv(name):
    """Rows of one CSV file, keyed by role (product, quantity, ship_day, ...)."""
    return org.read_table(DATA_DIR, name)


def to_int(value, name, column):
    try:
        return int(value)
    except ValueError:
        raise DataError(f"{name}: {column} must be a whole number, got {value!r}") from None


def norm(part_id):
    return part_id.strip().upper()


def find_part(part_id):
    """Return the parts.csv row for part_id, or raise ToolError listing known parts."""
    from mcp.server.mcpserver.exceptions import ToolError

    parts = read_csv("parts.csv")
    for row in parts:
        if norm(row["part_id"]) == norm(part_id):
            return row
    known = ", ".join(r["part_id"] for r in parts)
    raise ToolError(f"Unknown part_id {part_id!r}. Known parts: {known}.")


def scheduled_shipments(part_id):
    rows = [r for r in read_csv("inbound.csv") if norm(r["part_id"]) == norm(part_id)]
    shipments = [{
        "po_number": r["po_number"],
        "supplier": r["supplier"],
        "qty": to_int(r["quantity"], "inbound.csv", "quantity"),
        "arrives_in_days": to_int(r["arrival_day"], "inbound.csv", "arrival day"),
    } for r in rows]
    return sorted(shipments, key=lambda s: (s["arrives_in_days"], s["po_number"]))


def parts_per_unit(part_id):
    """Return {product: parts per unit} for the products that use part_id."""
    usage = {}
    for r in read_csv("bill-of-materials.csv"):
        if norm(r["part_id"]) == norm(part_id):
            usage[r["product"]] = usage.get(r["product"], 0) + to_int(
                r["per_unit"], "bill-of-materials.csv", "parts per unit")
    return usage


def orders_for_part(part_id):
    """Open orders that need part_id, in fill order (ship day, then order_id)."""
    usage = parts_per_unit(part_id)
    orders = []
    for r in read_csv("orders.csv"):
        if r["product"] not in usage:
            continue
        ordered = to_int(r["quantity"], "orders.csv", "quantity")
        orders.append({
            "order_id": r["order_id"],
            "customer": r["customer"],
            "product": r["product"],
            "units_ordered": ordered,
            "parts_per_unit": usage[r["product"]],
            "units_needed": ordered * usage[r["product"]],
            "ship_day": to_int(r["ship_day"], "orders.csv", "ship day"),
            "late_penalty": r["late_penalty"].lower() in ("yes", "y", "true", "1"),
        })
    return sorted(orders, key=lambda o: (o["ship_day"], o["order_id"]))


def coverage(on_hand, arrivals, orders):
    """Fill orders complete, in the given order. Returns one result per order.

    Order k ships on the first day when on_hand plus arrivals up to that day
    covers orders 1..k together; that day is never earlier than order k-1's,
    so a short order holds up the ones after it.
    """
    arrivals = sorted(arrivals, key=lambda a: a["day"])

    def supply_by(day):
        return on_hand + sum(a["qty"] for a in arrivals if a["day"] <= day)

    # Days on which the total supply changes; the earliest ship day is one of them.
    supply_days = [0] + [a["day"] for a in arrivals]

    results = []
    needed_so_far = 0
    for o in orders:
        needed_so_far += o["units_needed"]
        ready_day = next((d for d in sorted(set(supply_days))
                          if supply_by(d) >= needed_so_far), None)
        short = min(o["units_needed"], max(0, needed_so_far - supply_by(o["ship_day"])))
        row = {
            "order_id": o["order_id"],
            "customer": o["customer"],
            "units_needed": o["units_needed"],
            "ship_day": o["ship_day"],
            "late_penalty": o["late_penalty"],
        }
        if short == 0:
            row["status"] = "covered"
        else:
            row["status"] = "short"
            row["units_short"] = short
            if ready_day is None:
                row["ready_day"] = None
                row["note"] = "These arrivals never cover this order."
            else:
                row["ready_day"] = ready_day
                row["days_late"] = ready_day - o["ship_day"]
        results.append(row)
    return results


def as_json(data):
    return json.dumps(data, indent=2, ensure_ascii=False)


def guarded(fn):
    """Turn a CSV problem into a tool error the model can read."""
    from functools import wraps

    from mcp.server.mcpserver.exceptions import ToolError

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DataError as e:
            raise ToolError(f"Data problem: {e}") from e
    return wrapper


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

PartId = Annotated[str, Field(description="The part number, as written in the "
                                          "supplier's email or in parts.csv.")]


class Arrival(BaseModel):
    qty: int = Field(gt=0, description="Number of units in this shipment.")
    day: int = Field(ge=0, description="Day it arrives, counted from today (today is 0).")


@guarded
def get_part(part_id: PartId) -> str:
    """Look up one part: stock on hand, scheduled shipments, and the products that use it.

    Returns JSON with the part's description, supplier, on_hand (units in stock
    today), scheduled_shipments (purchase order, quantity, and arrives_in_days,
    counted from today), and used_in (each product and how many of the part
    one unit of it needs).
    """
    row = find_part(part_id)
    pid = row["part_id"]
    return as_json({
        "part_id": pid,
        "description": row["description"],
        "supplier": row["supplier"],
        "on_hand": to_int(row["on_hand"], "parts.csv", "on_hand"),
        "scheduled_shipments": scheduled_shipments(pid),
        "used_in": [{"product": p, "parts_per_unit": n}
                    for p, n in parts_per_unit(pid).items()],
    })


@guarded
def get_orders_for_part(part_id: PartId) -> str:
    """List the open customer orders that need a part, earliest ship day first.

    Returns JSON with, for each order: order_id, customer, product,
    units_ordered, parts_per_unit, units_needed (units ordered times parts per
    unit), ship_day
    (counted from today), and late_penalty (true if the order carries a
    penalty for shipping late).
    """
    row = find_part(part_id)
    orders = orders_for_part(row["part_id"])
    return as_json({
        "part_id": row["part_id"],
        "total_units_needed": sum(o["units_needed"] for o in orders),
        "orders": orders,
    })


@guarded
def check_coverage(
    part_id: PartId,
    arrivals: Annotated[list[Arrival], Field(
        description="The new arrival schedule for this part. It replaces the "
                    "part's scheduled shipments; stock on hand still counts. "
                    "Use an empty list if nothing more will arrive.")],
) -> str:
    """Check which orders a part's stock plus a new arrival schedule can cover.

    The arrivals replace the part's scheduled shipments in inbound.csv; stock on
    hand is still counted, available today (day 0). Orders are filled complete,
    one at a time, earliest ship day first, so an order that is short holds up
    every order after it.

    Returns JSON with one result per order. status is "covered" or "short". A
    short order also has units_short (units missing on its ship day),
    ready_day (first day it can ship, counted from today, or null if these
    arrivals never cover it), and days_late. Use this tool for coverage; never
    work it out yourself.
    """
    row = find_part(part_id)
    pid = row["part_id"]
    on_hand = to_int(row["on_hand"], "parts.csv", "on_hand")
    plan = [{"qty": a.qty, "day": a.day} for a in arrivals]
    results = coverage(on_hand, plan, orders_for_part(pid))
    short = [r for r in results if r["status"] == "short"]
    return as_json({
        "part_id": pid,
        "on_hand": on_hand,
        "arrivals_used": sorted(plan, key=lambda a: a["day"]),
        "orders_short": len(short),
        "units_short_total": sum(r["units_short"] for r in short),
        "orders": results,
    })


def create_server():
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    read_only = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    server = MCPServer(
        f"{org.short_name(DATA_DIR).lower()}-supply",
        instructions=f"Supply tools for {org.company_name(DATA_DIR)}: look up a part's "
                     "stock and scheduled shipments, the customer orders that need "
                     "it, and which orders a new arrival schedule covers. All days "
                     "are counted from today.")
    for tool in (get_part, get_orders_for_part, check_coverage):
        server.tool(annotations=read_only, structured_output=False)(tool)
    return server


def main():
    # Our own messages at INFO; the mcp library's per-request chatter only at WARNING.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING,
                        format="%(name)s: %(message)s")
    log.setLevel(logging.INFO)
    parser = argparse.ArgumentParser(
        description="MCP server with one company's supply tools (parts, orders, coverage).")
    parser.add_argument("--org", "--data", dest="org", metavar="FOLDER",
                        default=DEFAULT_DATA,
                        help="company folder with the CSV files: a name inside week-05 "
                             f"or a path (default: {DEFAULT_DATA})")
    args = parser.parse_args()

    problem = set_data_dir(args.org)
    if problem:
        parser.error(problem)
    log.info("Company folder: %s (%s)", DATA_DIR, org.company_name(DATA_DIR))
    for line in org.describe_columns(DATA_DIR):
        log.info("Columns  %s", line)
    create_server().run()  # stdio


if __name__ == "__main__":
    main()
