"""
COBRA Administration Tool
-------------------------
A simple command-line tool for tracking COBRA continuation coverage:
qualifying events, election deadlines, coverage periods, premiums and
payment grace periods. Uses only the Python standard library (SQLite).

Commands:
    add       Register a qualified beneficiary after a qualifying event
    elect     Record COBRA election (sets coverage period)
    waive     Record that the beneficiary waived COBRA
    pay       Record a monthly premium payment
    list      List all beneficiaries
    status    Show full detail for one beneficiary
    overdue   Show beneficiaries with late elections / unpaid premiums
    report    Summary counts and monthly premium totals

Example:
    python cobra_admin.py add --name "Asha Rao" --employee "Asha Rao" \
        --relationship employee --event termination --event-date 2026-10-31 \
        --notice-date 2026-11-10 --base-premium 650
    python cobra_admin.py elect 1 --date 2026-12-01
    python cobra_admin.py pay 1 --period 2026-11 --amount 663 --date 2026-12-15
"""

import argparse
import calendar
import sqlite3
from datetime import date, timedelta

DB_FILE = "cobra.db"

# Qualifying event -> maximum months of COBRA coverage
EVENT_RULES = {
    "termination": 18,
    "reduced_hours": 18,
    "divorce": 36,
    "legal_separation": 36,
    "death": 36,
    "medicare_entitlement": 36,
    "dependent_aging_out": 36,
}

ELECTION_WINDOW_DAYS = 60    # time to elect after notice / loss of coverage
FIRST_PAYMENT_DAYS = 45      # first premium due after election
GRACE_DAYS = 30              # grace period for monthly premiums
ADMIN_FEE = 0.02             # COBRA premium = 102% of plan cost
RELATIONSHIPS = ["employee", "spouse", "child"]


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------
def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def month_start(period: str) -> date:
    """'2026-11' -> date(2026, 11, 1)"""
    y, m = period.split("-")
    return date(int(y), int(m), 1)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS beneficiaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            employee_name TEXT NOT NULL,
            relationship TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_date TEXT NOT NULL,
            notice_date TEXT NOT NULL,
            election_deadline TEXT NOT NULL,
            election_status TEXT NOT NULL DEFAULT 'pending',
            election_date TEXT,
            coverage_start TEXT,
            coverage_end TEXT,
            base_premium REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            beneficiary_id INTEGER NOT NULL REFERENCES beneficiaries(id),
            period TEXT NOT NULL,
            amount REAL NOT NULL,
            paid_date TEXT NOT NULL
        );
    """)
    return conn


def cobra_premium(base: float) -> float:
    return round(base * (1 + ADMIN_FEE), 2)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_add(args, conn):
    event_date = parse_date(args.event_date)
    notice_date = parse_date(args.notice_date)
    # Election window runs 60 days from the later of notice date / loss of coverage
    deadline = max(event_date, notice_date) + timedelta(days=ELECTION_WINDOW_DAYS)
    cur = conn.execute(
        """INSERT INTO beneficiaries
           (name, employee_name, relationship, event_type, event_date,
            notice_date, election_deadline, base_premium)
           VALUES (?,?,?,?,?,?,?,?)""",
        (args.name, args.employee, args.relationship, args.event,
         event_date.isoformat(), notice_date.isoformat(),
         deadline.isoformat(), args.base_premium),
    )
    conn.commit()
    print(f"Added beneficiary #{cur.lastrowid}: {args.name}")
    print(f"  Max coverage : {EVENT_RULES[args.event]} months")
    print(f"  Election due : {deadline}")
    print(f"  Monthly COBRA premium: {cobra_premium(args.base_premium):.2f}")


def get_beneficiary(conn, bid):
    row = conn.execute("SELECT * FROM beneficiaries WHERE id=?", (bid,)).fetchone()
    if not row:
        raise SystemExit(f"Beneficiary #{bid} not found.")
    return row


def cmd_elect(args, conn):
    b = get_beneficiary(conn, args.id)
    elect_date = parse_date(args.date) if args.date else date.today()
    if elect_date > parse_date(b["election_deadline"]):
        raise SystemExit(f"Election date {elect_date} is after deadline "
                         f"{b['election_deadline']}. Election not accepted.")
    # Coverage is retroactive to the day after prior coverage ended
    start = parse_date(b["event_date"]) + timedelta(days=1)
    end = add_months(start, EVENT_RULES[b["event_type"]]) - timedelta(days=1)
    conn.execute(
        """UPDATE beneficiaries SET election_status='elected', election_date=?,
           coverage_start=?, coverage_end=? WHERE id=?""",
        (elect_date.isoformat(), start.isoformat(), end.isoformat(), args.id),
    )
    conn.commit()
    first_due = elect_date + timedelta(days=FIRST_PAYMENT_DAYS)
    print(f"#{args.id} elected COBRA. Coverage {start} to {end}.")
    print(f"  First premium due by {first_due}")


def cmd_waive(args, conn):
    get_beneficiary(conn, args.id)
    conn.execute("UPDATE beneficiaries SET election_status='waived' WHERE id=?",
                 (args.id,))
    conn.commit()
    print(f"#{args.id} marked as waived.")


def cmd_pay(args, conn):
    b = get_beneficiary(conn, args.id)
    if b["election_status"] != "elected":
        raise SystemExit("Payments can only be recorded for elected beneficiaries.")
    paid = parse_date(args.date) if args.date else date.today()
    conn.execute(
        "INSERT INTO payments (beneficiary_id, period, amount, paid_date) VALUES (?,?,?,?)",
        (args.id, args.period, args.amount, paid.isoformat()),
    )
    conn.commit()
    due = cobra_premium(b["base_premium"])
    note = "" if args.amount >= due else f" (short by {due - args.amount:.2f})"
    print(f"Recorded payment of {args.amount:.2f} for {args.period}{note}.")


def coverage_periods(b, as_of: date):
    """Yield 'YYYY-MM' periods from coverage start up to as_of (or coverage end)."""
    start = parse_date(b["coverage_start"])
    end = min(parse_date(b["coverage_end"]), as_of)
    d = date(start.year, start.month, 1)
    while d <= end:
        yield d.strftime("%Y-%m")
        d = add_months(d, 1)


def unpaid_periods(conn, b, as_of: date):
    """Periods whose premium is past due (beyond grace) and not fully paid."""
    due = cobra_premium(b["base_premium"])
    paid = {r["period"]: r["total"] for r in conn.execute(
        "SELECT period, SUM(amount) AS total FROM payments "
        "WHERE beneficiary_id=? GROUP BY period", (b["id"],))}
    late = []
    for p in coverage_periods(b, as_of):
        if as_of > month_start(p) + timedelta(days=GRACE_DAYS) and paid.get(p, 0) < due:
            late.append(p)
    return late


def cmd_list(args, conn):
    rows = conn.execute("SELECT * FROM beneficiaries ORDER BY id").fetchall()
    if not rows:
        print("No beneficiaries yet.")
        return
    print(f"{'ID':<4}{'Name':<20}{'Rel':<10}{'Event':<22}{'Status':<10}Deadline")
    for r in rows:
        print(f"{r['id']:<4}{r['name']:<20}{r['relationship']:<10}"
              f"{r['event_type']:<22}{r['election_status']:<10}{r['election_deadline']}")


def cmd_status(args, conn):
    b = get_beneficiary(conn, args.id)
    for key in b.keys():
        print(f"{key:<18}: {b[key]}")
    print(f"{'monthly_premium':<18}: {cobra_premium(b['base_premium']):.2f}")
    pays = conn.execute("SELECT period, amount, paid_date FROM payments "
                        "WHERE beneficiary_id=? ORDER BY period", (args.id,)).fetchall()
    print("\nPayments:")
    for p in pays:
        print(f"  {p['period']}  {p['amount']:.2f}  paid {p['paid_date']}")
    if not pays:
        print("  (none)")


def cmd_overdue(args, conn):
    today = parse_date(args.as_of) if args.as_of else date.today()
    found = False
    for b in conn.execute("SELECT * FROM beneficiaries").fetchall():
        if b["election_status"] == "pending" and today > parse_date(b["election_deadline"]):
            print(f"#{b['id']} {b['name']}: election deadline "
                  f"{b['election_deadline']} passed - COBRA rights lapsed.")
            found = True
        elif b["election_status"] == "elected":
            late = unpaid_periods(conn, b, today)
            if late:
                print(f"#{b['id']} {b['name']}: unpaid periods {', '.join(late)}")
                found = True
    if not found:
        print("No overdue items.")


def cmd_report(args, conn):
    rows = conn.execute("SELECT * FROM beneficiaries").fetchall()
    counts = {}
    monthly_total = 0.0
    for r in rows:
        counts[r["election_status"]] = counts.get(r["election_status"], 0) + 1
        if r["election_status"] == "elected":
            monthly_total += cobra_premium(r["base_premium"])
    print("=== COBRA Summary ===")
    print(f"Total beneficiaries : {len(rows)}")
    for status in ("pending", "elected", "waived"):
        print(f"  {status:<8}: {counts.get(status, 0)}")
    print(f"Expected monthly premium (elected): {monthly_total:.2f}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(description="COBRA Administration Tool")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("add", help="Add a qualified beneficiary")
    a.add_argument("--name", required=True)
    a.add_argument("--employee", required=True, help="Covered employee's name")
    a.add_argument("--relationship", choices=RELATIONSHIPS, required=True)
    a.add_argument("--event", choices=EVENT_RULES.keys(), required=True)
    a.add_argument("--event-date", required=True, help="YYYY-MM-DD (loss of coverage)")
    a.add_argument("--notice-date", required=True, help="YYYY-MM-DD election notice sent")
    a.add_argument("--base-premium", type=float, required=True,
                   help="Monthly plan cost before the 2%% admin fee")
    a.set_defaults(func=cmd_add)

    e = sub.add_parser("elect", help="Record COBRA election")
    e.add_argument("id", type=int)
    e.add_argument("--date", help="Election date (default today)")
    e.set_defaults(func=cmd_elect)

    w = sub.add_parser("waive", help="Record COBRA waiver")
    w.add_argument("id", type=int)
    w.set_defaults(func=cmd_waive)

    pay = sub.add_parser("pay", help="Record a premium payment")
    pay.add_argument("id", type=int)
    pay.add_argument("--period", required=True, help="Coverage month, YYYY-MM")
    pay.add_argument("--amount", type=float, required=True)
    pay.add_argument("--date", help="Payment date (default today)")
    pay.set_defaults(func=cmd_pay)

    sub.add_parser("list", help="List beneficiaries").set_defaults(func=cmd_list)

    s = sub.add_parser("status", help="Show beneficiary detail")
    s.add_argument("id", type=int)
    s.set_defaults(func=cmd_status)

    o = sub.add_parser("overdue", help="Show late elections and unpaid premiums")
    o.add_argument("--as-of", help="Check as of date (default today)")
    o.set_defaults(func=cmd_overdue)

    sub.add_parser("report", help="Summary report").set_defaults(func=cmd_report)
    return p


def main():
    args = build_parser().parse_args()
    with connect() as conn:
        args.func(args, conn)


if __name__ == "__main__":
    main()
