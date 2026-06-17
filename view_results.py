# view_results.py
# Reads logs/open_trades.json and logs/postmortems.jsonl and prints a
# clean, human-readable summary. Run any time to see what the agent
# is currently holding and what it's learned from closed trades so far.

import json
import os
from datetime import datetime

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
OPEN_TRADES_FILE = os.path.join(LOG_DIR, "open_trades.json")
POSTMORTEM_FILE = os.path.join(LOG_DIR, "postmortems.jsonl")


def show_open_trades():
    print("=" * 60)
    print("CURRENTLY OPEN PAPER TRADES")
    print("=" * 60)

    if not os.path.exists(OPEN_TRADES_FILE):
        print("None yet -- no trades have been opened.")
        return

    with open(OPEN_TRADES_FILE, "r", encoding="utf-8") as f:
        trades = json.load(f)

    if not trades:
        print("None currently open.")
        return

    for ticker, t in trades.items():
        opened = t.get("opened_at", "")[:10]
        print(f"\n{ticker} -- {t.get('signal')}")
        print(f"  Entry:  ${t.get('entry')}")
        print(f"  Target: ${t.get('target')}")
        print(f"  Stop:   ${t.get('stop_loss')}")
        print(f"  Hold:   {t.get('hold_time')} ({t.get('hold_days')} trading days)")
        print(f"  Opened: {opened}")
        print(f"  Basis:  {t.get('exit_note')}")


def show_postmortems():
    print()
    print("=" * 60)
    print("CLOSED TRADE POSTMORTEMS")
    print("=" * 60)

    if not os.path.exists(POSTMORTEM_FILE):
        print("None yet -- no trades have closed.")
        return

    records = []
    with open(POSTMORTEM_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    if not records:
        print("None yet -- no trades have closed.")
        return

    wins = 0
    losses = 0
    total_pnl = 0.0

    for r in records:
        pnl = r.get("pnl_pct", 0) or 0
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
        total_pnl += pnl

        print(f"\n{r.get('ticker')} -- {r.get('exit_reason', '').upper()}")
        print(f"  Entry: ${r.get('entry')}  ->  Exit: ${r.get('exit_price')}  "
              f"(P&L: {pnl:+.2f}%)")
        print(f"  Held {r.get('days_held')} days (planned: {r.get('planned_hold_time')})")
        print(f"  Range since open: ${r.get('low_since_open')} - ${r.get('high_since_open')}")
        if r.get("ideal_outcome_note"):
            print(f"  Lesson: {r['ideal_outcome_note']}")

    print()
    print("-" * 60)
    print(f"SUMMARY: {len(records)} closed trades | {wins} wins | {losses} losses "
          f"| Total P&L: {total_pnl:+.2f}%")
    if len(records) > 0:
        print(f"Win rate: {wins / len(records) * 100:.1f}%")
    print("-" * 60)


if __name__ == "__main__":
    show_open_trades()
    show_postmortems()