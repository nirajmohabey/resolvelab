"""Plot the frozen v3 source/domain-family measurements, without training."""
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
report=json.loads((ROOT/"artifacts/domain_training_report.json").read_text())
plt.rcParams.update({"font.size":11,"axes.spines.top":False,"axes.spines.right":False})
fig,ax=plt.subplots(figsize=(6,5),constrained_layout=True)
for key,label,color in [("source_test","Grouped source test","#2563eb"),("domain_family_test","Held-out domain families","#047857")]:
    bins=[row for row in report["results"][key]["reliability_bins"] if row["N"]]
    ax.plot([row["mean_score"] for row in bins],[row["accuracy"] for row in bins],"o-",label=label,color=color)
ax.plot([0,1],[0,1],"--",color="#64748b",label="Perfect calibration")
ax.set(xlim=(0,1.02),ylim=(0,1.02),xlabel="Mean routing score in bin",ylabel="Observed routing accuracy",
       title="ResolveLab v3: synthetic reliability measurements")
ax.legend(loc="lower right",fontsize=9)
fig.savefig(ROOT/"artifacts/domain_calibration.png",dpi=170)
fig.savefig(ROOT/"artifacts/domain_calibration.svg")
print("Saved artifacts/domain_calibration.png and .svg")
