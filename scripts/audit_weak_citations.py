"""Lists eval questions with weak citations, worst first."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import sys
sys.stdout.reconfigure(encoding="utf-8")
import csv
from paths import PROJECT_ROOT

with open(os.path.join(PROJECT_ROOT, "eval", "results.csv"), newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

flagged = [r for r in rows if r["n_weak_citations"] and int(r["n_weak_citations"]) > 0]
flagged.sort(key=lambda r: float(r["faithfulness_rate"] or 1))

print(f"{len(flagged)} queries had at least one weak citation, sorted worst first:\n")
for r in flagged:
    print(f"[{r['faithfulness_rate']}] {r['query']}")
    print(f"   {r['n_weak_citations']}/{r['n_citations']} citations flagged weak\n")
