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
        }
        append_postmortem(postmortem)

        validity_tag = " [INVALID - ghost trade]" if is_invalid else ""
        print(f"[Check] CLOSED {ticker}: {exit_reason} at {exit_price} (P&L {pnl_pct}%).{validity_tag}")

        if not is_invalid:
            pnl_word = "gain" if pnl_pct and pnl_pct > 0 else "loss"
            announce(f"Closed paper trade on {ticker}. {exit_reason.replace('_', ' ')}. "
                     f"{abs(pnl_pct):.1f} percent {pnl_word}.")

    save_open_trades(still_open)

def tier1_scan(broad: bool = False, top_n: int = 25) -> list:
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

    all_candidates = []
    seen = set()
    for scan_mode in ["Breakout", "Momentum", "Swing Trading"]:
        df = run_scanner(
            mode=scan_mode,
            universe=universe,
            require_above_sma50=True,
        )
        if df.empty:
            continue
        score_col = "Score" if "Score" in df.columns else None
        if score_col:
            df = df.sort_values(score_col, ascending=False)
        for ticker in df["Ticker"].head(top_n).tolist():
            if ticker not in seen:
                seen.add(ticker)
                all_candidates.append(ticker)

    if not all_candidates:
        print("[Tier 1] No candidates passed the health check.")
        return []

    candidates = all_candidates[:top_n]
    print(f"[Tier 1] {len(candidates)} candidates promoted to Tier 2: {candidates}")
    return candidates

def tier2_analyze(candidates: list) -> list:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results_map = {}

    def analyze_one(ticker):
        try:
            result = analyze_stock(ticker)
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

def tier3_open_paper_trades(actionable: list):
    if not is_market_open():
        print(f"[Tier 3] Market not open. No new trades will be opened.")
        announce("Agent check complete. Market is closed so no new trades were opened.")
        return

    open_trades  = load_open_trades()
    closed_today = get_tickers_closed_today()
    opened_count = 0

    for result in actionable:
        ticker = result["ticker"]

        if ticker in open_trades:
            print(f"[Tier 3] {ticker} already has an open paper trade, skipping.")
            continue

        if ticker in closed_today:
            print(f"[Tier 3] {ticker} was already closed today, skipping re-entry.")
            continue

        trade = {
            "ticker":    ticker,
            "signal":    result["signal"],
            "entry":     result.get("entry"),
            "target":    result.get("exit"),
            "stop_loss": result.get("stop_loss"),
            "hold_time": result.get("hold_time"),
            "hold_days": result.get("hold_days"),
            "exit_note": result.get("exit_note"),
            "opened_at": datetime.now().isoformat(),
        }
        open_trades[ticker] = trade
        opened_count += 1
        print(f"[Tier 3] Opened paper trade: {ticker} @ {trade['entry']} "
              f"(target {trade['target']}, stop {trade['stop_loss']})")
        announce(f"Agent opened a new paper trade on {ticker}. "
                 f"{trade['signal']} signal. Entry {trade['entry']:.2f}.")

    save_open_trades(open_trades)
    print(f"[Tier 3] {opened_count} new paper trades opened.")

def run(broad: bool = False, top_n: int = 25):
    now_et = datetime.now(ET)
    print(f"=== SEVENIQ Agent run started: {datetime.now().isoformat()} ===")
    print(f"=== Current ET time: {now_et.strftime('%Y-%m-%d %H:%M %Z')} ===")

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
    run(broad=False, top_n=100)