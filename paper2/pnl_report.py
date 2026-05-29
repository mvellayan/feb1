#!/usr/bin/env python3
"""
pnl_report.py — realized P&L report for the arbo702 paper-trading engine.

The engine logs NO P&L of its own, and the two obvious data files are traps:

  * data/transaction.csv logs every order *submission*, including stale-cancel
    re-issues, so one position can show 1 BUY + several SELL legs.  Summing it
    naively double-counts and produces absurd numbers.
  * trade.log records ENTRY fills (net_debit) but NOT exit fills — exits there
    appear only as "unfilled / cancel" warnings.

The authoritative fill price is in ops.log:
    [order] id=<oid> status=Filled filled=1.0 avg=<price>
where `avg` is the BAG combo net price/share (net_debit on BUY, net_credit on
SELL; for an expiry stock-leg it is the per-share stock price).

This script maps order_id -> position via transaction.csv, pulls the actual
filled `avg` for each leg from ops.log, and reports realized P&L for positions
whose EXIT filled within the requested date range.

Usage:
    python pnl_report.py                      # last 7 calendar days (by exit date)
    python pnl_report.py 2026-05-18 2026-05-22 # explicit inclusive range
    python pnl_report.py --csv out.csv         # also dump per-trade rows to CSV
"""
import csv
import glob
import os
import re
import sys
from collections import defaultdict
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(HERE, "logs")
TXN_CSV = os.path.join(HERE, "data", "transaction.csv")

FILL_RE = re.compile(
    r"\[order\]\s+id=(\d+)\s+status=Filled\s+filled=[\d.]+\s+avg=([\d.]+)"
)


def parse_args(argv):
    out_csv = None
    dates = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--csv":
            out_csv = argv[i + 1]
            i += 2
            continue
        dates.append(a)
        i += 1
    if len(dates) == 0:
        end = date.today()
        start = end - timedelta(days=7)
    elif len(dates) == 2:
        start = date.fromisoformat(dates[0])
        end = date.fromisoformat(dates[1])
    else:
        sys.exit("usage: pnl_report.py [START END] [--csv FILE]  (dates = YYYY-MM-DD)")
    return start.isoformat(), end.isoformat(), out_csv


def load_fills():
    """(date_dir, order_id) -> last positive filled avg seen."""
    fills = {}
    for path in glob.glob(os.path.join(LOGS_DIR, "*", "ops.log")):
        ddir = os.path.basename(os.path.dirname(path))
        with open(path) as fh:
            for line in fh:
                m = FILL_RE.search(line)
                if not m:
                    continue
                oid, avg = int(m.group(1)), float(m.group(2))
                if avg > 0:
                    fills[(ddir, oid)] = avg
    return fills


def load_positions():
    """position_id -> dict(buys=[...], sells=[...], strat)."""
    pos = defaultdict(lambda: {"buys": [], "sells": [], "strat": None})
    with open(TXN_CSV) as fh:
        for r in csv.DictReader(fh):
            pid = r["position_id"]
            if pid in ("", "UNKNOWN"):
                continue
            try:
                oid = int(r["order_id"])
                qty = float(r["quantity"])
            except (ValueError, KeyError):
                continue
            ddir = r["timestamp"][:10].replace("-", "")
            rec = (ddir, oid, r["timestamp"], r["sec_type"], qty, r.get("reason", ""))
            P = pos[pid]
            (P["buys"] if r["action"] == "BUY" else P["sells"]).append(rec)
            if P["strat"] is None:
                P["strat"] = r["strategy_id"]
    return pos


def build_rows(pos, fills, start, end):
    rows, unresolved = [], []
    for pid, P in pos.items():
        entry = next(
            ((fills[(d, oid)], qty, ts) for d, oid, ts, _, qty, _ in sorted(P["buys"])
             if (d, oid) in fills),
            None,
        )
        exitf = next(
            ((fills[(d, oid)], ts, sec, rs) for d, oid, ts, sec, _, rs in sorted(P["sells"])
             if (d, oid) in fills),
            None,
        )
        if not exitf:
            continue  # still open / never exited
        if not (start <= exitf[1][:10] <= end):
            continue
        if not entry:
            unresolved.append((exitf[1][:10], P["strat"], pid))
            continue
        shares = 100 * entry[1]
        pnl = (exitf[0] - entry[0]) * shares
        rows.append({
            "exit_date": exitf[1][:10],
            "entry_time": entry[2],
            "exit_time": exitf[1],
            "strat": P["strat"],
            "position_id": pid,
            "entry_px": round(entry[0], 4),
            "exit_px": round(exitf[0], 4),
            "shares": int(shares),
            "pnl": round(pnl, 2),
            "exit_reason": exitf[3],
        })
    rows.sort(key=lambda r: (r["exit_date"], str(r["strat"]), r["entry_time"]))
    return rows, unresolved


def main():
    start, end, out_csv = parse_args(sys.argv[1:])
    fills = load_fills()
    pos = load_positions()
    rows, unresolved = build_rows(pos, fills, start, end)

    print(f"\nRealized P&L — positions exited {start} .. {end} (inclusive, by exit fill date)\n")
    if not rows:
        print("  no closed positions in range")
        return
    print(f"  {'exit':10} {'st':>2} {'entry$/sh':>9} {'exit$/sh':>9} {'sh':>4} {'pnl$':>9}  reason")
    tot = 0.0
    byst = defaultdict(lambda: [0, 0.0])
    for r in rows:
        print(f"  {r['exit_date']:10} {str(r['strat']):>2} {r['entry_px']:9.2f} "
              f"{r['exit_px']:9.2f} {r['shares']:4d} {r['pnl']:9.2f}  {r['exit_reason']}")
        tot += r["pnl"]
        byst[r["strat"]][0] += 1
        byst[r["strat"]][1] += r["pnl"]

    n = len(rows)
    wins = sum(1 for r in rows if r["pnl"] > 0)
    print(f"\n  closed trades: {n}   winners: {wins}   losers: {n - wins}   "
          f"win rate: {100 * wins / n:.0f}%")
    print(f"  total realized P&L: ${tot:,.2f}    avg/trade: ${tot / n:,.2f}")
    print("  by strategy:")
    for s in sorted(byst, key=str):
        print(f"    strat {s}: {byst[s][0]:2d} trades  ${byst[s][1]:>10,.2f}")

    if unresolved:
        print(f"\n  [note] {len(unresolved)} position(s) exited in range but their entry "
              f"fill was not found in available logs (entry predates logs) — excluded:")
        for d, s, pid in unresolved:
            print(f"     {d} strat{s} {pid}")

    if out_csv:
        with open(out_csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\n  per-trade rows written to {out_csv}")


if __name__ == "__main__":
    main()
