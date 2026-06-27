lines = open('agent_runner.py', encoding='utf-8').readlines()

print("Line 204:", repr(lines[203]))
print("Line 205:", repr(lines[204]))
print("Line 220:", repr(lines[219]))
print("Line 221:", repr(lines[220]))

new_block = [
    '    all_candidates = []\n',
    '    seen = set()\n',
    '    for scan_mode in ["Breakout", "Momentum", "Swing Trading"]:\n',
    '        df = run_scanner(\n',
    '            mode=scan_mode,\n',
    '            universe=universe,\n',
    '            require_above_sma50=True,\n',
    '        )\n',
    '        if df.empty:\n',
    '            continue\n',
    '        score_col = "Score" if "Score" in df.columns else None\n',
    '        if score_col:\n',
    '            df = df.sort_values(score_col, ascending=False)\n',
    '        for ticker in df["Ticker"].head(top_n).tolist():\n',
    '            if ticker not in seen:\n',
    '                seen.add(ticker)\n',
    '                all_candidates.append(ticker)\n',
    '    if not all_candidates:\n',
    '        print("[Tier 1] No candidates passed the health check.")\n',
    '        return []\n',
    '    candidates = all_candidates[:top_n]\n',
    '    print(f"[Tier 1] {len(candidates)} candidates promoted to Tier 2: {candidates}")\n',
    '    return candidates\n',
]

new_lines = lines[:203] + new_block + lines[220:]

with open('agent_runner.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print("SUCCESS")