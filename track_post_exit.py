# track_post_exit.py
# EXIT-EFFICIENCY tracker. For every CLOSED paper trade, follow the ticker AFTER we
# exited to measure whether we left money on the table (or dodged a drop). Broken
# down by exit reason: target_hit / stop_hit / time_exit.
#
# CRITICAL: post-exit moves are measured vs SPY over the SAME window. In a rising
# market everything drifts up, so raw "it went higher" is meaningless -- only the
# move RELATIVE to the market tells you if the exit was actually early/late.

import os
import json
import numpy as np
import pandas as pd
from collections import defaultdict

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
POSTMORTEM_FILE = os.path.join(LOG_DIR, "postmortems.jsonl")
HORIZONS = [5, 10, 20]   # trading days after exit


def load_closed():
    if not os.path.exists(POSTMORTEM_FILE):
        return []
    rows = []
    for line in open(POSTMORTEM_FILE):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("valid", True) and r.get("exit_price") and r.get("closed_at"):
            rows.append(r)
    return rows


def main():
    import yfinance as yf
    closed = load_closed()
    print("=" * 74)
    print("  POST-EXIT TRACKING — did the ticker keep going after we closed?")
    print("=" * 74)
    print(f"Closed trades on file: {len(closed)}")
    if not closed:
        print("No closed trades yet — tracker is ready and will populate as trades close.")
        return

    tickers = sorted(set(r["ticker"] for r in closed) | {"SPY"})
    px = yf.download(tickers, period="1y", auto_adjust=True, progress=False)["Close"]
    if isinstance(px, pd.Series):
        px = px.to_frame()

    def fwd(tk, start_date, n):
        try:
            s = px[tk].dropna()
            pos = s.index.searchsorted(pd.Timestamp(start_date), side="right")  # first bar AFTER exit
            if pos + n >= len(s):
                return None
            return (s.iloc[pos + n] / s.iloc[pos] - 1) * 100
        except Exception:
            return None

    by_reason = defaultdict(lambda: {h: [] for h in HORIZONS})
    for r in closed:
        for h in HORIZONS:
            stock = fwd(r["ticker"], r["closed_at"], h)
            spy = fwd("SPY", r["closed_at"], h)
            if stock is not None and spy is not None:
                by_reason[r.get("exit_reason", "unknown")][h].append(stock - spy)  # excess vs SPY

    print("\nPost-exit move vs SPY (positive = ticker kept beating market after we sold):")
    for reason in sorted(by_reason):
        print(f"\n  {reason}:")
        for h in HORIZONS:
            a = np.array(by_reason[reason][h])
            if len(a):
                tag = ("  <- exits look EARLY (left gains)" if a.mean() > 1 and reason in ("target_hit", "time_exit")
                       else "  <- stops too tight?" if a.mean() > 1 and reason == "stop_hit" else "")
                print(f"    +{h:>2}d after exit: {a.mean():+6.2f}% vs SPY  (n={len(a)}){tag}")
            else:
                print(f"    +{h:>2}d after exit: no data yet")
    print("\n" + "=" * 74)
    print("  Read: large POSITIVE = we exited too early; near-zero/negative = exit was fine.")
    print("  Need ~30+ closed trades per exit-reason before trusting these.")
    print("=" * 74)


if __name__ == "__main__":
    main()
