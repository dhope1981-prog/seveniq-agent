# agent_runner.py
# SEVENIQ Paper-Trading Agent
# Standalone script. Imports real SEVENIQ modules from stock_intel_v5
# rather than copying them, so there is only one source of truth.
#
# Tier 1: cheap scan (SMA50 health check + pattern presence), rank by score
# Tier 2: AI Brain deep analysis on top candidates only
# Tier 3: open paper trades on BUY / STRONG BUY signals using calculated exit data
#
# Every run also checks existing open trades first: if price has hit the
# target or the stop since opening, the trade is closed and a postmortem
# is written comparing what was planned against what actually happened.
# Stop checks use CLOSING price only -- intraday wicks do not trigger stops.
# Target checks use intraday High -- locking in gains when price touches target.

import sys
import json
import os
from datetime import datetime, date
import pytz

sys.path.insert(0, r'C:\Users\Dustin\stock_intel_v5')
from scanner import run_scanner
from ai_brain import analyze_stock
from data_engine import get_stock_data
from config import SAMPLE_UNIVERSE
from health_filter import get_health   # "avoid financially sick companies" risk filter

try:
    import voice_engine
    VOICE_AVAILABLE = True
except Exception:
    VOICE_AVAILABLE = False

def announce(text):
    if not VOICE_AVAILABLE:
        return
    try:
        voice_engine.speak(text, ignore_toggle=True)
        import time
        time.sleep(min(len(text) * 0.06, 8))
    except Exception:
        pass

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
OPEN_TRADES_FILE  = os.path.join(LOG_DIR, "open_trades.json")
POSTMORTEM_FILE   = os.path.join(LOG_DIR, "postmortems.jsonl")
HEALTH_SKIP_FILE  = os.path.join(LOG_DIR, "health_skips.jsonl")   # trades skipped as sick


def _log_health_skip(ticker, result, health):
    """Record a trade the health filter blocked, so we can later measure whether the
    skipped (distressed) names really would have underperformed -- validating the filter live."""
    try:
        with open(HEALTH_SKIP_FILE, "a") as f:
            f.write(json.dumps({
                "ticker": ticker,
                "skipped_at": datetime.now().isoformat(),
                "signal": result.get("signal"),
                "would_be_entry": result.get("entry"),
                "flags": health.get("flags"),
                "as_of": health.get("as_of"),
            }, default=str) + "\n")
    except Exception:
        pass

ET = pytz.timezone("America/New_York")

US_MARKET_HOLIDAYS_2026 = {
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 4, 3),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),
    date(2026, 9, 7),
    date(2026, 11, 26),
    date(2026, 11, 27),
    date(2026, 12, 25),
}

def is_market_open() -> bool:
    now_et = datetime.now(ET)
    today  = now_et.date()
    if now_et.weekday() >= 5:
        print(f"[Market] Weekend -- market closed.")
        return False
    if today in US_MARKET_HOLIDAYS_2026:
        print(f"[Market] Market holiday -- market closed.")
        return False
    market_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    if not (market_open <= now_et <= market_close):
        print(f"[Market] Outside market hours ({now_et.strftime('%H:%M ET')}). "
              f"Market opens 9:30 AM, closes 4:00 PM ET.")
        return False
    return True

def get_tickers_closed_today() -> set:
    today_str = datetime.now(ET).strftime("%Y-%m-%d")
    closed_today = set()
    if not os.path.exists(POSTMORTEM_FILE):
        return closed_today
    with open(POSTMORTEM_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                closed_at = record.get("closed_at", "")
                if closed_at.startswith(today_str):
                    closed_today.add(record["ticker"])
            except Exception:
                pass
    return closed_today

def load_open_trades() -> dict:
    if os.path.exists(OPEN_TRADES_FILE):
        with open(OPEN_TRADES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_open_trades(trades: dict):
    with open(OPEN_TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2, default=str)

def append_postmortem(record: dict):
    with open(POSTMORTEM_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")

def check_open_trades():
    open_trades = load_open_trades()
    if not open_trades:
        print("[Check] No open trades to review.")
        return

    still_open = {}

    for ticker, trade in open_trades.items():
        print(f"[Check] Reviewing open trade: {ticker}")
        try:
            df = get_stock_data(ticker, period="3mo")
        except Exception as e:
            print(f"[Check] Could not fetch data for {ticker}: {e}")
            still_open[ticker] = trade
            continue

        if df.empty:
            print(f"[Check] No data for {ticker}, leaving open.")
            still_open[ticker] = trade
            continue

        opened_at = datetime.fromisoformat(trade["opened_at"])
        df_since  = df[df.index >= opened_at.strftime("%Y-%m-%d")]
        if df_since.empty:
            df_since = df.tail(5)

        target    = trade.get("target")
        stop      = trade.get("stop_loss")
        hold_days = trade.get("hold_days") or 10

        days_open     = (datetime.now() - opened_at).days
        current_price = float(df["Close"].iloc[-1])

        # Target: use intraday High -- lock in gains when price touches target
        high_since = float(df_since["High"].max())
        # Stop: use daily CLOSE only -- intraday wicks do not trigger stops
        # This is standard swing trading practice: a stock can wick below
        # the stop intraday and recover; only a closing break matters
        close_since_min = float(df_since["Close"].min())
        low_since = float(df_since["Low"].min())  # kept for postmortem info only

        opened_date = opened_at.strftime("%Y-%m-%d")
        today_str   = datetime.now(ET).strftime("%Y-%m-%d")
        is_invalid  = (opened_date == today_str and days_open == 0)

        exit_reason = None
        exit_price  = None

        if target and high_since >= target:
            exit_reason = "target_hit"
            exit_price  = target
        elif stop and close_since_min <= stop:
            # Only close if a daily CLOSE was at or below the stop
            exit_reason = "stop_hit"
            exit_price  = stop
        elif days_open >= hold_days:
            exit_reason = "time_exit"
            exit_price  = current_price

        if exit_reason is None:
            print(f"[Check] {ticker} still open, no exit condition met yet. "
                  f"Close: ${current_price:.2f} | Stop: ${stop} | Target: ${target}")
            still_open[ticker] = trade
            continue

        entry   = trade.get("entry") or current_price
        pnl_pct = round((exit_price - entry) / entry * 100, 2) if entry else None

        ideal_note = ""
        if exit_reason == "stop_hit" and target:
            if high_since >= target:
                ideal_note = "Price also touched the original target during this window -- the stop may have been too tight."
            else:
                ideal_note = f"Price reached a high of {high_since:.2f} after the stop, target of {target} was not reached."
        elif exit_reason == "time_exit":
            if target and high_since >= target:
                ideal_note = "Target was actually reached within the window but trade was force-closed by time -- hold window may be too short for this setup."
            elif stop and low_since <= stop:
                ideal_note = "Intraday price touched stop but never closed below it -- stop held correctly."
            else:
                ideal_note = f"Neither target ({target}) nor stop ({stop}) was reached. Highest: {high_since:.2f}, Lowest close: {close_since_min:.2f}."

        postmortem = {
            "ticker":             ticker,
            "signal":             trade.get("signal"),
            "entry":              entry,
            "target":             target,
            "stop_loss":          stop,
            "exit_reason":        exit_reason,
            "exit_price":         exit_price,
            "pnl_pct":            pnl_pct,
            "opened_at":          trade.get("opened_at"),
            "closed_at":          datetime.now().isoformat(),
            "days_held":          days_open,
            "planned_hold_time":  trade.get("hold_time"),
            "original_exit_note": trade.get("exit_note"),
            "ideal_outcome_note": ideal_note,
            "high_since_open":    high_since,
            "low_since_open":     low_since,
            "min_close_since_open": close_since_min,
            "valid":              not is_invalid,
            # Decision context carried from entry, so the live ledger can attribute
            # this outcome to the signals that drove it.
            "confidence":            trade.get("confidence"),
            "confidence_base":       trade.get("confidence_base"),
            "confidence_components": trade.get("confidence_components"),
            "entry_pattern":         trade.get("entry_pattern"),
            "regime_at_entry":       trade.get("regime_at_entry"),
            "rsi_at_entry":          trade.get("rsi_at_entry"),
            "base_quality":          trade.get("base_quality"),
        }
        append_postmortem(postmortem)

        validity_tag = " [INVALID - ghost trade]" if is_invalid else ""
        print(f"[Check] CLOSED {ticker}: {exit_reason} at {exit_price} (P&L {pnl_pct}%).{validity_tag}")

        if not is_invalid:
            pnl_word = "gain" if pnl_pct and pnl_pct > 0 else "loss"
            announce(f"Closed paper trade on {ticker}. {exit_reason.replace('_', ' ')}. "
                     f"{abs(pnl_pct):.1f} percent {pnl_word}.")

    save_open_trades(still_open)

def tier1_scan(broad: bool = False, top_n: int = 30) -> list:
    universe = None
    if broad:
        try:
            from ticker_universe_full import load_universe
            universe = load_universe()
            print(f"[Tier 1] Using full universe: {len(universe)} tickers")
        except Exception as e:
            print(f"[Tier 1] Could not load full universe ({e}), using sample list instead")
            universe = SAMPLE_UNIVERSE
    else:
        universe = SAMPLE_UNIVERSE
        print(f"[Tier 1] Using sample universe: {len(universe)} tickers")

    # Run ALL four scan modes and combine via PER-MODE QUOTAS. Each mode fills a
    # fixed number of slots from its OWN ranked list -- no cross-mode score
    # comparison, because the scorers are on different scales (Breakout pins at
    # the 100 cap for many names, which would otherwise crowd out everything).
    # Swing Trading is the validated primary mode (positive 20-day alpha vs SPY
    # in 3/4 backtested regimes incl. recovery), so it gets half the slots.
    MODE_QUOTAS = [
        ("Swing Trading", 15),   # 50% -- validated primary mode
        ("Momentum",       8),   # 27%
        ("Hidden Gems",    4),   # 13%
        ("Breakout",       3),   # 10% -- weakest validated mode
    ]

    candidates = []
    seen = set()
    pick_mode = {}   # ticker -> mode that contributed it (for logging)
    pick_score = {}  # ticker -> that mode's score (for logging)

    for scan_mode, quota in MODE_QUOTAS:
        df = run_scanner(
            mode=scan_mode,
            universe=universe,
            require_above_sma50=True,
        )
        if df.empty:
            print(f"[Tier 1] {scan_mode}: 0 scored, 0/{quota} slots filled")
            continue
        score_col = ("Seveniq_Score" if "Seveniq_Score" in df.columns
                     else "Score" if "Score" in df.columns else None)
        if score_col:
            df = df.sort_values(score_col, ascending=False)
        # Fill this mode's quota from its own ranked list, skipping tickers
        # already taken by a higher-priority mode.
        filled = 0
        for _, row in df.iterrows():
            if filled >= quota:
                break
            ticker = row["Ticker"]
            if ticker in seen:
                continue
            seen.add(ticker)
            candidates.append(ticker)
            pick_mode[ticker] = scan_mode
            pick_score[ticker] = float(row[score_col]) if score_col else 0.0
            filled += 1
        print(f"[Tier 1] {scan_mode}: {len(df)} scored, {filled}/{quota} slots filled")

    if not candidates:
        print("[Tier 1] No candidates passed the health check.")
        return []

    candidates = candidates[:top_n]
    print(f"[Tier 1] {len(candidates)} candidates promoted to Tier 2 "
          f"(per-mode quota across {len(MODE_QUOTAS)} modes):")
    for t in candidates:
        print(f"         {t:<6} score={pick_score[t]:.1f}  via {pick_mode[t]}")
    return candidates

def tier2_analyze(candidates: list) -> list:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results_map = {}

    def analyze_one(ticker):
        try:
            result = analyze_stock(ticker, include_news=True, include_eightk=True)
            return ticker, result
        except Exception as e:
            return ticker, {"signal": "HOLD", "confidence": 0}

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(analyze_one, t): t for t in candidates}
        for future in as_completed(futures):
            ticker, result = future.result()
            signal = result.get("signal", "HOLD")
            print(f"[Tier 2] {ticker}: {signal} (confidence {result.get('confidence')}%)")
            results_map[ticker] = result

    actionable = []
    for ticker in candidates:
        result = results_map.get(ticker, {})
        if result.get("signal") in ["BUY", "STRONG BUY"]:
            actionable.append(result)

    print(f"[Tier 2] {len(actionable)} actionable signals found")
    return actionable

def base_tightness(ticker: str):
    """Base-tightness risk overlay. Measures the 20-day price range as a % of
    price (tight base = small range = lower-volatility, lower-drawdown setup).

    Backtest evidence (4 regimes, 12,600 picks): base tightness does NOT predict
    higher returns, but tight bases cut loss size ~35% (-7.0% vs -10.6%; and
    -7.8% vs -12.2% in the 2022 bear) with a marginally higher win rate. So it is
    used purely for RISK SIZING, not stock selection. Thresholds anchored to the
    observed depth distribution (median ~16%, tightest-30% <= ~12%).

    Returns (depth_pct, tier, size_multiplier). On any failure returns
    (None, "UNKNOWN", 1.0) so it never blocks a trade.
    """
    try:
        df = get_stock_data(ticker, period="3mo")
        if df is None or df.empty or len(df) < 20:
            return None, "UNKNOWN", 1.0
        win = df.tail(20)
        price = float(df["Close"].iloc[-1])
        if price <= 0:
            return None, "UNKNOWN", 1.0
        depth = (float(win["High"].max()) - float(win["Low"].min())) / price * 100.0
        if depth <= 12.0:
            return round(depth, 1), "TIGHT", 1.5    # smaller drawdowns -> size up
        if depth >= 22.0:
            return round(depth, 1), "LOOSE", 0.5    # wide/high-beta -> size down
        return round(depth, 1), "NORMAL", 1.0
    except Exception:
        return None, "UNKNOWN", 1.0


def _brain_context(result: dict) -> dict:
    """Capture the decision context at entry so that when the trade later closes,
    its outcome can be attributed to the signals that drove it. This is the raw
    material for the live feedback ledger (live_feedback.py)."""
    layers = result.get("layers", {}) or {}
    mr = layers.get("market_regime", {}) or {}
    sd = layers.get("stock_data", {}) or {}
    note = str(result.get("exit_note") or "")
    pattern = None
    if "based on" in note:
        seg = note.split("based on", 1)[1].strip()
        for s in (" (", ".", " --", ","):
            i = seg.find(s)
            if i != -1:
                seg = seg[:i]
        pattern = seg.strip().replace(" and ", " & ") or None
    return {
        "confidence":            result.get("confidence"),
        "confidence_base":       result.get("confidence_base"),
        "confidence_components": result.get("confidence_components"),
        "entry_pattern":         pattern,
        "regime_at_entry":       mr.get("regime"),
        "rsi_at_entry":          sd.get("rsi"),
    }


def tier3_open_paper_trades(actionable: list):
    if not is_market_open():
        print(f"[Tier 3] Market not open. No new trades will be opened.")
        announce("Agent check complete. Market is closed so no new trades were opened.")
        return

    open_trades  = load_open_trades()
    closed_today = get_tickers_closed_today()
    opened_count = 0

    # No hard cap on positions during paper trading phase
    # Goal is maximum data collection to prove the system works
    # Revisit when moving to real money

    for result in actionable:
        ticker = result["ticker"]

        if ticker in open_trades:
            print(f"[Tier 3] {ticker} already has an open paper trade, skipping.")
            continue

        if ticker in closed_today:
            print(f"[Tier 3] {ticker} was already closed today, skipping re-entry.")
            continue

        # Price floor: $2, set by DATA not convention. Bucketed our own 16yr history by price:
        # $2-5 stocks actually had the HIGHEST win rate (56%) -- NOT junk. But below ~$2 the
        # crash rate climbs and it's a lottery under $1 (+262% avg = a few moonshots + 5.8%
        # crash), and survivorship bias flatters cheap-stock numbers most (the ones that died
        # to zero are missing). So we cut only the genuine danger zone (<$2) and keep the good
        # $2-5 names. (An earlier $5 floor was an untested assumption that threw out good trades.)
        _entry = result.get("entry") or 0
        if _entry and _entry < 2.0:
            print(f"[Tier 3] {ticker} SKIPPED -- price ${_entry:.2f} below $2 floor (lottery zone).")
            continue

        # Health filter (validated: distressed-company dips crash 2-4x more often and win
        # ~4 pts less -- fundamentals_edgar/validate_distress_dip.py). Skip the financially
        # SICK (insolvent / death-spiral / >=2 red flags); this only removes risk. Names we
        # have no fundamentals for return status "unknown" and pass through untouched.
        health = get_health(ticker)
        if health["distressed"]:
            print(f"[Tier 3] {ticker} SKIPPED -- financially distressed "
                  f"({', '.join(health['flags'])}). Dodging the falling knife.")
            _log_health_skip(ticker, result, health)
            continue

        # Base-tightness risk overlay: derive a position-size multiplier from how
        # tight the recent base is (tight = smaller drawdowns -> size up).
        base_depth, base_tier, size_mult = base_tightness(ticker)

        trade = {
            "ticker":    ticker,
            "signal":    result["signal"],
            "entry":     result.get("entry"),
            "target":    result.get("exit"),
            "stop_loss": result.get("stop_loss"),
            "hold_time": result.get("hold_time"),
            "hold_days": result.get("hold_days"),
            "exit_note": result.get("exit_note"),
            "base_depth_pct":   base_depth,   # 20d range as % of price
            "base_quality":     base_tier,    # TIGHT / NORMAL / LOOSE
            "size_multiplier":  size_mult,    # risk-overlay sizing (1.5 / 1.0 / 0.5)
            "health_status":    health["status"],      # healthy / watch / unknown
            "health_flags":     health["flags"],       # red flags present (for the ledger)
            "opened_at": datetime.now().isoformat(),
            **_brain_context(result),         # decision context for the live ledger
        }
        open_trades[ticker] = trade
        opened_count += 1
        print(f"[Tier 3] Opened paper trade: {ticker} @ {trade['entry']} "
              f"(target {trade['target']}, stop {trade['stop_loss']}) "
              f"| base {base_tier} {base_depth}% -> size x{size_mult}")
        announce(f"Agent opened a new paper trade on {ticker}. "
                 f"{trade['signal']} signal. Entry {trade['entry']:.2f}.")

    save_open_trades(open_trades)
    print(f"[Tier 3] {opened_count} new paper trades opened.")

def run(broad: bool = False, top_n: int = 30):
    now_et = datetime.now(ET)
    print(f"=== SEVENIQ Agent run started: {datetime.now().isoformat()} ===")
    print(f"=== Current ET time: {now_et.strftime('%Y-%m-%d %H:%M %Z')} ===")

    # Weekend guard: markets are closed Sat/Sun, so a scan can't open trades and
    # just wastes a full broad pass. Bail immediately however the run was launched.
    if now_et.weekday() >= 5:
        print("=== Weekend (markets closed) -- agent run skipped. ===")
        return

    print("\n--- Step 1: Checking existing open trades ---")
    check_open_trades()

    print("\n--- Step 2: Scanning for new candidates ---")
    candidates = tier1_scan(broad=broad, top_n=top_n)
    if not candidates:
        print("Nothing new to analyze this run.")
        print("=== Run complete ===")
        return

    actionable = tier2_analyze(candidates)
    if not actionable:
        print("No actionable signals this run.")
        print("=== Run complete ===")
        return

    tier3_open_paper_trades(actionable)
    print("=== Run complete ===")

if __name__ == "__main__":
    # Top 30 unique candidates (pooled across all 4 scan modes) go to the Brain.
    run(broad=True, top_n=30)