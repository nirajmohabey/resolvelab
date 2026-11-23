"""Plot the frozen actual-database retrieval comparison without changing it."""
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"artifacts/retrieval-v2"
report=json.loads((OUT/"report.json").read_text())
names=list(report["configurations"])
labels=["Lexical AND\nlegacy","Lexical OR\nnew","MiniLM\ndense","Hybrid AND\nlegacy","Hybrid OR\nnew"]
test=[report["configurations"][name]["summary"]["test"] for name in names]
plt.rcParams.update({"font.size":11,"axes.spines.top":False,"axes.spines.right":False})
fig,axes=plt.subplots(1,3,figsize=(13,4.5),constrained_layout=True)
for axis,key,title in [(axes[0],"recall","Relevant-section recall@3"),(axes[1],"mrr","Reciprocal rank@10"),(axes[2],"latency","Warm retrieval p95")]:
    values=[row["mean_recall_at_k"]["3"] if key=="recall" else row["mrr_at_10"] if key=="mrr" else row["p95_ms"] for row in test]
    axis.bar(range(len(names)),values,color=["#64748b","#7c3aed","#2563eb","#94a3b8","#047857"])
    axis.set(xticks=range(len(names)),xticklabels=labels,title=title,ylabel="Milliseconds" if key=="latency" else "Mean over 16 held-out queries")
    axis.tick_params(axis="x",labelsize=8)
    if key!="latency":axis.set_ylim(0,1.08)
    for i,value in enumerate(values):axis.text(i,value+(0.03 if key!="latency" else .2),f"{value:.3f}" if key!="latency" else f"{value:.1f}",ha="center",fontsize=9)
fig.suptitle("ResolveLab retrieval: 32 permitted sections, 48 filtered sections, 16 held-out synthetic queries",fontsize=13)
fig.savefig(OUT/"retrieval_comparison.png",dpi=180);fig.savefig(OUT/"retrieval_comparison.svg")
print("Saved artifacts/retrieval-v2/retrieval_comparison.png and .svg")
