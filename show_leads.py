#!/usr/bin/env python3
"""Print the last run's leads, best first. Usage: python show_leads.py [N]"""
import glob, json, sys
from collections import Counter

n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
rows = []
for f in glob.glob("storage/datasets/default/*.json"):
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception:
        continue
    if isinstance(d, dict) and "lead_score" in d:
        rows.append(d)

if not rows:
    print("No leads found. Did the run finish? Check storage/datasets/default/")
    raise SystemExit(1)

rows.sort(key=lambda r: -r["lead_score"])
print(f"\n{len(rows)} leads total.  ~ = estimated valuation\n")
print(f'{"SCORE":>5}  {"TRADE":17} {"VALUE":>10}  {"ISSUED":10}  {"ZIP":6} DESCRIPTION')
print("-" * 100)
for r in rows[:n]:
    est = "~" if r.get("valuation_is_estimated") else " "
    print(f'{r["lead_score"]:>5}  {r["trade"]:17} {est}${r["valuation"]:>8,}  '
          f'{(r.get("issued_date") or "")[:10]:10}  {(r.get("zip") or ""):6} '
          f'{(r.get("work_description") or "")[:42]}')
print(f'\ntrade mix: {dict(Counter(r["trade"] for r in rows).most_common())}')
hot = [r for r in rows if r["lead_score"] >= 75]
print(f'call-today leads (score 75+): {len(hot)}')
