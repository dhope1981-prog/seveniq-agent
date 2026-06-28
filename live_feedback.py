# live_feedback.py
# The self-correcting loop's MEASUREMENT half. Reads the agent's closed paper
# trades (logs/postmortems.jsonl, each now carrying its entry decision context)
# and reports live win rates per pattern / regime / confidence bucket / signal /
# base-quality -- WITH sample sizes, and refusing to draw conclusions from thin
# cells. It also tests whether the Brain's multi-signal blend actually separates
# winners from losers better than the old base confidence.
#
# This MEASURES and SURFACES. It does NOT auto-adjust anything. Weight tuning is a
# human decision until a cell has earned a real sample.

import os
import json
import statistics
from collections import defaultdict

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
POSTMORTEM_FILE = os.path.join(LOG_DIR, "postmortems.jsonl")
# Optional: historical walk-forward numbers to compare live results against.
WF_VALIDATION = r"C:\Users\Dustin\stock_intel_v5\backtest_data_cache\walk_forward_validation.json"

MIN_N_TRUST = 20   # a cell needs this many closed trades before we treat it as real
MIN_N_EARLY = 5    # below this, don't even show an "early read"


def load_closed_trades():
    if not os.path.exists(POSTMORTEM_FILE):
        return []
    rows = []
    with open(POSTMORTEM_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("valid", True) and r.get("pnl_pct") is not None:
                rows.append(r)
    return rows


def _stats(pnls):
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    return {
        "n": n,
        "win_rate": round(wins / n * 100, 1) if n else None,
        "avg_pnl": round(statistics.mean(pnls), 2) if n else None,
    }


def _tag(n):
    if n >= MIN_N_TRUST:
        return "TRUSTED"
    if n >= MIN_N_EARLY:
        return "early read"
    return "too few"


def _conf_bucket(c):
    if c is None:
        return "unknown"
    if c >= 80:
        return "80+"
    if c >= 70:
        return "70-79"
    if c >= 60:
        return "60-69"
    return "<60"


def _report_group(rows, keyfn, title):
    groups = defaultdict(list)
    for r in rows:
        k = keyfn(r)
        if k is None:
            continue
        groups[k].append(r["pnl_pct"])
    print(f"\n-- {title} --")
    if not groups:
        print("   (no data)")
        return
    for k in sorted(groups, key=lambda x: -len(groups[x])):
        s = _stats(groups[k])
        line = f"   {str(k):22} n={s['n']:<4} win={s['win_rate']}%  avg={s['avg_pnl']}%  [{_tag(s['n'])}]"
        print(line)


def _load_wf():
    try:
        return json.load(open(WF_VALIDATION))["validation_summary"]
    except Exception:
        return {}


def main():
    rows = load_closed_trades()
    print("=" * 74)
    print("  LIVE FEEDBACK -- closed paper trades (measure-only, sample-size-honest)")
    print("=" * 74)
    print(f"\nClosed valid trades on file: {len(rows)}")
    if not rows:
        print("No closed trades yet. The loop starts measuring once trades close.")
        return

    overall = _stats([r["pnl_pct"] for r in rows])
    print(f"OVERALL: n={overall['n']}  win={overall['win_rate']}%  avg={overall['avg_pnl']}%")
    if overall["n"] < MIN_N_TRUST:
        print(f"NOTE: under {MIN_N_TRUST} trades total -- everything below is provisional. "
              f"Do not change weights yet.")

    _report_group(rows, lambda r: r.get("entry_pattern") or "none", "BY ENTRY PATTERN")
    _report_group(rows, lambda r: r.get("regime_at_entry") or "unknown", "BY MARKET REGIME AT ENTRY")
    _report_group(rows, lambda r: r.get("base_quality") or "unknown", "BY BASE QUALITY")
    _report_group(rows, lambda r: _conf_bucket(r.get("confidence")), "BY BLENDED CONFIDENCE BUCKET")

    # Does the blend separate winners better than the base score?
    print("\n-- BLEND vs BASE CONFIDENCE (does the blend add discrimination?) --")
    paired = [(r.get("confidence"), r.get("confidence_base"), r["pnl_pct"])
              for r in rows if r.get("confidence") is not None and r.get("confidence_base") is not None]
    if len(paired) < MIN_N_EARLY:
        print(f"   Need >= {MIN_N_EARLY} trades with confidence context; have {len(paired)}.")
    else:
        for label, idx in [("blended", 0), ("base", 1)]:
            hi = [p[2] for p in paired if p[idx] >= 70]
            lo = [p[2] for p in paired if p[idx] < 70]
            hs, ls = _stats(hi), _stats(lo)
            print(f"   {label:8} >=70: win={hs['win_rate']}% (n={hs['n']})   "
                  f"<70: win={ls['win_rate']}% (n={ls['n']})")
        print("   (If 'blended' shows a bigger gap between >=70 and <70 than 'base', the blend is helping.)")

    # Live vs historical, only for cells with a real sample.
    wf = _load_wf()
    if wf:
        print("\n-- LIVE vs HISTORICAL win rate (patterns with a real live sample) --")
        bypat = defaultdict(list)
        for r in rows:
            if r.get("entry_pattern"):
                bypat[r["entry_pattern"]].append(r["pnl_pct"])
        shown = False
        for pat, pnls in bypat.items():
            if len(pnls) < MIN_N_TRUST:
                continue
            shown = True
            live = _stats(pnls)
            hist = wf.get(pat, {}).get("avg_win_rate_across_windows")
            print(f"   {pat:22} live={live['win_rate']}% (n={live['n']})  historical={hist}%")
        if not shown:
            print(f"   No pattern has >= {MIN_N_TRUST} live trades yet -- nothing trustworthy to compare.")

    print("\n" + "=" * 74)
    print("  Reminder: surface only. Weight changes are a human call until cells reach")
    print(f"  >= {MIN_N_TRUST} trades. Noise masquerades as signal below that.")
    print("=" * 74)


if __name__ == "__main__":
    main()
