f = open('agent_runner.py', 'r', encoding='utf-8')
content = f.read()
f.close()

# Add the voice_engine import near the top, alongside the other imports
old_imports = "from config import SAMPLE_UNIVERSE"
new_imports = '''from config import SAMPLE_UNIVERSE

try:
    import voice_engine
    VOICE_AVAILABLE = True
except Exception:
    VOICE_AVAILABLE = False


def announce(text):
    """Speak a message out loud if voice is available. Never blocks or
    crashes the agent if voice fails for any reason."""
    if not VOICE_AVAILABLE:
        return
    try:
        voice_engine.speak(text, ignore_toggle=True)
        import time
        time.sleep(min(len(text) * 0.06, 8))  # rough pause so it has time to play before next print
    except Exception:
        pass'''

if old_imports in content:
    content = content.replace(old_imports, new_imports, 1)
    print("Voice helper added.")
else:
    print("ERROR: import marker not found")

# Announce when a trade closes
old_close = '''        append_postmortem(postmortem)
        print(f"[Check] CLOSED {ticker}: {exit_reason} at {exit_price} "
              f"(P&L {pnl_pct}%). {ideal_note}")'''

new_close = '''        append_postmortem(postmortem)
        print(f"[Check] CLOSED {ticker}: {exit_reason} at {exit_price} "
              f"(P&L {pnl_pct}%). {ideal_note}")
        pnl_word = "gain" if pnl_pct and pnl_pct > 0 else "loss"
        announce(f"Closed paper trade on {ticker}. {exit_reason.replace('_', ' ')}. "
                 f"{abs(pnl_pct):.1f} percent {pnl_word}.")'''

if old_close in content:
    content = content.replace(old_close, new_close)
    print("Close announcement added.")
else:
    print("ERROR: close marker not found")

# Announce when a new trade opens
old_open = '''        open_trades[ticker] = trade
        print(f"[Tier 3] Opened paper trade: {ticker} @ {trade['entry']} "
              f"(target {trade['target']}, stop {trade['stop_loss']})")'''

new_open = '''        open_trades[ticker] = trade
        print(f"[Tier 3] Opened paper trade: {ticker} @ {trade['entry']} "
              f"(target {trade['target']}, stop {trade['stop_loss']})")
        announce(f"Agent opened a new paper trade on {ticker}. "
                 f"{trade['signal']} signal. Entry {trade['entry']:.2f}.")'''

if old_open in content:
    content = content.replace(old_open, new_open)
    print("Open announcement added.")
else:
    print("ERROR: open marker not found")

f = open('agent_runner.py', 'w', encoding='utf-8')
f.write(content)
f.close()