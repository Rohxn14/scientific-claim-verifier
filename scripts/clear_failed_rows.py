"""Drops failed rows from eval/results.csv so the harness re-scores them."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import csv
from paths import PROJECT_ROOT

RESULTS_PATH = os.path.join(PROJECT_ROOT, "eval", "results.csv")

with open(RESULTS_PATH, newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
    fieldnames = rows[0].keys()

# Keep only rows that got a real answer OR are legitimate zero-claim refusals.
# We can't fully distinguish those here, so: keep rows with n_claims > 0,
# and manually decide on the two known-correct refusals below.
KNOWN_CORRECT_REFUSALS = {
    "what is the current state-of-the-art accuracy on ImageNet as of 2026",
    "how much does it cost to train a GPT-4 scale model",
}

kept = [
    r for r in rows
    if int(r["n_claims"]) > 0 or r["query"] in KNOWN_CORRECT_REFUSALS
]
dropped = [r for r in rows if r not in kept]

with open(RESULTS_PATH, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(kept)

print(f"Kept {len(kept)} rows, dropped {len(dropped)} for re-scoring:")
for r in dropped:
    print(f"  - {r['query']}")