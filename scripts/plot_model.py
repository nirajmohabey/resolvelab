"""Plot saved routing measurements without fitting or changing the model."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
report = json.loads((ROOT / "artifacts/routing_report.json").read_text())
plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False,
                     "savefig.facecolor": "white"})
fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), constrained_layout=True)
for values, label, color in [(report["splits"]["test"]["trained"], "Grouped Bitext holdout", "#2563eb"),
                              (report["independent_synthetic"], "Independent synthetic cases", "#d97706")]:
    bins = [row for row in values["reliability_bins"] if row["N"]]
    axes[0].plot([row["mean_score"] for row in bins], [row["accuracy"] for row in bins],
                 "o-", color=color, label=label)
axes[0].plot([0, 1], [0, 1], "--", color="#64748b", label="Perfect calibration")
axes[0].set(xlim=(0, 1.02), ylim=(0, 1.02), xlabel="Mean routing score in bin", ylabel="Observed routing accuracy",
             title="Reliability by data distribution")
axes[0].legend(loc="upper left", fontsize=8)
candidates = report["threshold_candidates"]
axes[1].plot([row["accepted_coverage"] for row in candidates],
             [row["accepted_routing_error"] or 0 for row in candidates], "o-", color="#7c3aed")
axes[1].set(xlabel="Supported accepted coverage (all validation cases)", ylabel="Routing error among accepted cases",
             title="Validation threshold tradeoff")
axes[1].grid(alpha=.2)
fig.suptitle("ResolveLab routing v1: synthetic measurements, not customer accuracy", fontsize=13)
fig.savefig(ROOT / "artifacts/routing_calibration.png", dpi=170)
fig.savefig(ROOT / "artifacts/routing_calibration.svg")
print("Saved artifacts/routing_calibration.png and .svg")
