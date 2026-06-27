lines = open('agent_runner.py', encoding='utf-8').readlines()

# Replace lines 206-221 (0-indexed: 205-220) with multi-mode scan
new_block = '''
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
'''

print("Line 206:", repr(lines[205]))
print("Line 221:", repr(lines[220]))

new_lines = lines[:205] + [new_block] + lines[221:]

with open('agent_runner.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print("SUCCESS - multi-mode scan applied")