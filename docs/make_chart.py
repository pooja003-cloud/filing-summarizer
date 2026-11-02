"""Build docs/answer.png from the project's own outputs.

For each company, re-scores the planted (corrupted) quote against the filing
and plots its similarity next to common fuzzy-matching thresholds.
Run from the project root: python docs/make_chart.py
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from check import closest_passage, load_source  # noqa: E402
from textnorm import normalize  # noqa: E402

labels, scores = [], []
for ticker in ("HPQ", "DELL"):
    src, blocks, sentences = load_source(ticker)
    planted = json.loads((ROOT / "outputs" / f"planted_{ticker}.json").read_text(encoding="utf-8"))
    quote = normalize(planted["risks"][0]["exact_quote"])
    assert quote not in src, "planted quote unexpectedly matches the filing"
    score, _, diff = closest_passage(quote, sentences)
    labels.append(f"{ticker} planted misquote\n({diff[0]})")
    scores.append(score)
    print(ticker, round(score, 3), diff)

fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
bars = ax.bar(labels, scores, color="#c0504d", width=0.5, zorder=3)
for b, s in zip(bars, scores):
    ax.text(b.get_x() + b.get_width() / 2, s + 0.003, f"{s:.2f}", ha="center", fontsize=11, fontweight="bold")
for t, style in ((0.95, "--"), (0.90, ":")):
    ax.axhline(t, color="#444", linestyle=style, linewidth=1.2, zorder=2)
    ax.text(1.32, t + 0.002, f"  fuzzy match at {t:.0%}\n  would PASS it", va="bottom", fontsize=8.5, color="#444")
ax.set_ylim(0.85, 1.01)
ax.set_xlim(-0.5, 1.9)
ax.set_ylabel("Similarity to the real filing text")
ax.set_title("One wrong word still scores 98-99% similar.\nExact matching (what check.py uses) caught both.", fontsize=11)
ax.grid(axis="y", alpha=0.3, zorder=0)
for side in ("top", "right"):
    ax.spines[side].set_visible(False)
fig.tight_layout()
fig.savefig(ROOT / "docs" / "answer.png")
print("saved docs/answer.png")
