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
# No automatic learning or self-modification happens here -- this just
# writes the honest record for a human to review later.

import sys
import json
import os
from datetime import datetime

sys.path.insert(0, r'C:\Users\Dustin\stock_intel_v5')

from scanner import run_scanner
from ai_brain import analyze_stock
from data_engine import get_stock_data
from config import SAMPLE_UNIVERSE

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
OPEN_TRADES_FILE = os.path.join(LOG_DIR, "open_trades.json")
POSTMORTEM_FILE = os.path.join(LOG_DIR, "postmortems.jsonl")


# ── Persistence helpers ──────────────────────────────────────────────────

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


# ── Check existing open trades for target/stop hits ─────────────────────

def check_open_trades():
    """
    For every currently open paper trade, pull recent price history and
    check whether the target or stop has been hit since it opened.
    Closes the trade and writes a postmortem if so. Trades that have
    exceeded their hold_days window with neither hit get force-closed
    at the current price, also with a postmortem.
    """
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
        # Only look at price action since the trade was opened
        df_since = df[df.index >= opened_at.strftime("%Y-%m-%d")]
        if df_since.empty:
            df_since = df.tail(5)  # fallback, opened very recently

        target = trade.get("target")
        stop = trade.get("stop_loss")
        hold_days = trade.get("hold_days") or 10

        days_open = (datetime.now() - opened_at).days
        high_since = float(df_since["High"].max())
        low_since = float(df_since["Low"].min())
        current_price = float(df["Close"].iloc[-1])

        exit_reason = None
        exit_price = None

        if target and high_since >= target:
            exit_reason = "target_hit"
            exit_price = target
        elif stop and low_since <= stop:
            exit_reason = "stop_hit"
            exit_price = stop
        elif days_open >= hold_days:
            exit_reason = "time_exit"
            exit_price = current_price

        if exit_reason is None:
            print(f"[Check] {ticker} still open, no exit condition met yet.")
            still_open[ticker] = trade
            continue

        # Trade is closing -- write the postmortem
        entry = trade.get("entry") or current_price
        pnl_pct = round((exit_price - entry) / entry * 100, 2) if entry else None

        # What would the ideal outcome have been, looking at the full window since open?
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
                ideal_note = "Stop was touched within the window but not caught by this check -- review data granularity."
            else:
                ideal_note = f"Neither target ({target}) nor stop ({stop}) was reached. Highest: {high_since:.2f}, Lowest: {low_since:.2f}."

        postmortem = {
            "ticker": ticker,
            "signal": trade.get("signal"),
            "entry": entry,
            "target": target,
            "stop_loss": stop,
            "exit_reason": exit_reason,
            "exit_price": exit_price,
            "pnl_pct": pnl_pct,
            "opened_at": trade.get("opened_at"),
            "closed_at": datetime.now().isoformat(),
            "days_held": days_open,
            "planned_hold_time": trade.get("hold_time"),
            "original_exit_note": trade.get("exit_note"),
            "ideal_outcome_note": ideal_note,
            "high_since_open": high_since,
            "low_since_open": low_since,
        }
        append_postmortem(postmortem)
        print(f"[Check] CLOSED {ticker}: {exit_reason} at {exit_price} "
              f"(P&L {pnl_pct}%). {ideal_note}")

    save_open_trades(still_open)


# ── Tier 1: cheap scan ────────────────────────────────────────────────────

def tier1_scan(broad: bool = False, top_n: int = 25) -> list:
    """
    Cheap scan: health check (above SMA50), ranked by the scanner's own score.
    broad=True uses the full ticker universe; broad=False uses the small default
    sample list for a fast run.
    """
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

    df = run_scanner(
        mode="Breakout",
        universe=universe,
        require_above_sma50=True,
    )

    if df.empty:
        print("[Tier 1] No candidates passed the health check.")
        return []

    score_col = "Score" if "Score" in df.columns else None
    if score_col:
        df = df.sort_values(score_col, ascending=False)

    candidates = df["Ticker"].head(top_n).tolist()
    print(f"[Tier 1] {len(candidates)} candidates promoted to Tier 2: {candidates}")
    return candidates


# ── Tier 2: AI Brain deep analysis ───────────────────────────────────────

def tier2_analyze(candidates: list) -> list:
    """
    Run the full AI Brain analysis on Tier 1 survivors.
    Returns a list of result dicts for anything that came back BUY or STRONG BUY.
    """
    actionable = []
    for ticker in candidates:
        print(f"[Tier 2] Analyzing {ticker}...")
        try:
            result = analyze_stock(ticker)
        except Exception as e:
            print(f"[Tier 2] Error analyzing {ticker}: {e}")
            continue

        signal = result.get("signal", "HOLD")
        print(f"[Tier 2] {ticker}: {signal} (confidence {result.get('confidence')}%)")

        if signal in ["BUY", "STRONG BUY"]:
            actionable.append(result)

    print(f"[Tier 2] {len(actionable)} actionable signals found")
    return actionable


# ── Tier 3: open new paper trades ────────────────────────────────────────

def tier3_open_paper_trades(actionable: list):
    """
    Open a paper trade for each actionable signal, using the already-calculated
    entry/exit/stop from AI Brain. Does NOT place a real order -- just logs
    the position so it can be checked and closed on a future run.
    """
    open_trades = load_open_trades()

    for result in actionable:
        ticker = result["ticker"]
        if ticker in open_trades:
            print(f"[Tier 3] {ticker} already has an open paper trade, skipping.")
            continue

        trade = {
            "ticker": ticker,
            "signal": result["signal"],
            "entry": result.get("entry"),
            "target": result.get("exit"),
            "stop_loss": result.get("stop_loss"),
            "hold_time": result.get("hold_time"),
            "hold_days": result.get("hold_days"),
            "exit_note": result.get("exit_note"),
            "opened_at": datetime.now().isoformat(),
        }
        open_trades[ticker] = trade
        print(f"[Tier 3] Opened paper trade: {ticker} @ {trade['entry']} "
              f"(target {trade['target']}, stop {trade['stop_loss']})")

    save_open_trades(open_trades)


# ── Main run ──────────────────────────────────────────────────────────────

def run(broad: bool = False, top_n: int = 25):
    print(f"=== SEVENIQ Agent run started: {datetime.now().isoformat()} ===")

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
    # Default: quick run on the sample universe.
    # Run with broad=True for a full-universe sweep (slower).
    run(broad=False, top_n=25)