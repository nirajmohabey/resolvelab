"""Saved development/test analysis and fresh-process resource measurements.

Does not fit/select/promote a model or read the new blind workflow dataset.
"""
import json
import argparse
from pathlib import Path
import subprocess
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"artifacts/v4"


def reliability(rows,key):
    selected=[row for row in rows if row["partition"]=="domain_test" and not row.get("scope_guard_reason")]
    labels=["cancel_request","human_handoff","out_of_scope","track_order"]
    probabilities=np.asarray([row[key] for row in selected]);truth=np.asarray([row["expected"] for row in selected])
    scores=probabilities.max(axis=1);correct=np.asarray(labels)[probabilities.argmax(axis=1)]==truth
    bins=[]
    for low in np.arange(0,1,.1):
        mask=(scores>=low)&(scores<low+.1 if low<.9 else scores<=1)
        if mask.any():bins.append({"N":int(mask.sum()),"score":float(scores[mask].mean()),"accuracy":float(correct[mask].mean())})
    return bins,sum(row["N"]/len(selected)*abs(row["score"]-row["accuracy"]) for row in bins),len(selected)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--profile",action="store_true",help="Refresh measured resources; default reuses the saved run when rendering plots")
    options=parser.parse_args()
    report=json.loads((OUT/"training_report.json").read_text());manifest=json.loads((OUT/"model_manifest.json").read_text())
    predictions=[json.loads(line) for line in (OUT/"predictions.jsonl").read_text().splitlines()]
    resources={"scope":"One fresh Python process per model, same80 historical diagnostic messages. Cold model load/first query after library imports; warm per-message latency. Peak process RSS includes interpreter/libraries. Single local CPU run, no cloud cost/throughput inference.","models":{}}
    if (OUT/"resources.json").exists() and not options.profile:
        resources=json.loads((OUT/"resources.json").read_text())
    else:
        for name,path in [("TF-IDF v1","artifacts/router-tfidf-v1.joblib"),("Domain v3","artifacts/router-domain-v3.joblib"),("Candidate v4",manifest["artifact_uri"])]:
            completed=subprocess.run([sys.executable,str(ROOT/"scripts/benchmark_routing.py"),"--artifact",str(ROOT/path)],cwd=ROOT,check=True,capture_output=True,text=True)
            resources["models"][name]=json.loads(completed.stdout)
        (OUT/"resources.json").write_text(json.dumps(resources,indent=2)+"\n",encoding="utf-8")
    ablations=[]
    for architecture in ["flat","hierarchical"]:
        for blend in [0.,.5,1.]:
            feasible=[row for row in report["candidates"] if row["architecture"]==architecture and row["blend_weight"]==blend]
            if not feasible:continue
            best=max(feasible,key=lambda row:(row["domain_validation"]["correct_supported"],-row["domain_validation"]["wrong_accepted"],row["source_validation"]["correct_supported"],-row["calibration_log_loss"]))
            ablations.append(best)
    raw_bins,raw_ece,cal_n=reliability(predictions,"raw_probabilities")
    cal_bins,cal_ece,_=reliability(predictions,"calibrated_model_probabilities")
    validation=[row for row in predictions if row["partition"]=="domain_validation"]
    curve=[]
    for threshold in np.arange(.25,.976,.025):
        accepted=[row for row in validation if row["predicted"]!="out_of_scope" and max(row["probabilities"])>=threshold]
        wrong=sum(row["predicted"]!=row["expected"] for row in accepted)
        curve.append({"threshold":round(float(threshold),3),"accepted":len(accepted),"wrong":wrong,"coverage":len(accepted)/len(validation),"accepted_error":wrong/len(accepted) if accepted else None})
    analysis={"version":"domain-router-v4","selection":report["selected"],"development_ablations":ablations,
              "heldout_family_test_model_only_calibration":{"N":cal_n,"raw_ece_10_bins":raw_ece,"temperature_ece_10_bins":cal_ece,"raw_bins":raw_bins,"temperature_bins":cal_bins},
              "development_coverage_curve":curve,"family_test_metrics":report["metrics"]["domain_test"],
              "calibration_excludes":"Deterministic scope-rule denials, which are not probabilities.",
              "claim_support_human_review":None,"fresh_blind_workflow":"not read by this analysis script",
              "ablation_scope":"Best feasible development configuration per architecture/blend; not a held-out comparison or independent generalization estimate."}
    (OUT/"analysis.json").write_text(json.dumps(analysis,indent=2)+"\n",encoding="utf-8")
    plt.rcParams.update({"font.size":11,"axes.spines.top":False,"axes.spines.right":False})
    fig,axes=plt.subplots(1,3,figsize=(14,4.6),constrained_layout=True)
    labels=[row["architecture"].replace("hierarchical","Scope + intent")+"\n"+{0.:"TF-IDF",.5:"50/50 blend",1.:"MiniLM"}[row["blend_weight"]] for row in ablations]
    correct=[row["domain_validation"]["correct_supported"] for row in ablations]
    axes[0].bar(range(len(labels)),correct,color=["#64748b" if row["architecture"]=="flat" else "#2563eb" for row in ablations])
    axes[0].set(xticks=range(len(labels)),xticklabels=labels,ylim=(0,200),ylabel="Correct supported requests accepted / 192",title="Development ablation after safety/core gates")
    axes[0].tick_params(axis="x",labelsize=8,rotation=25)
    for index,value in enumerate(correct):axes[0].text(index,value+2,str(value),ha="center",fontsize=9)
    axes[1].plot([0,1],[0,1],"--",color="#64748b")
    for bins,label,color in [(raw_bins,"Raw scores","#d97706"),(cal_bins,"Temperature calibrated","#047857")]:
        axes[1].plot([row["score"] for row in bins],[row["accuracy"] for row in bins],"o-",label=label,color=color)
    axes[1].set(xlim=(0,1.02),ylim=(0,1.02),xlabel="Mean model score in bin",ylabel="Observed routing accuracy",title=f"Untuned family-test reliability (N={cal_n})")
    axes[1].legend(fontsize=9,loc="upper left")
    axes[2].plot([row["coverage"] for row in curve],[row["accepted_error"] for row in curve],"o-",color="#7c3aed")
    selected=report["selected"]["domain_validation"]
    axes[2].scatter([selected["accepted"]/256],[selected["accepted_error"]],color="#dc2626",s=65,zorder=5,label=f"Frozen threshold {report['selected']['threshold']}")
    axes[2].set(xlabel="Accepted fraction of 256 validation messages",ylabel="Wrong routes / accepted routes",title="Development coverage/error tradeoff")
    axes[2].legend(fontsize=8)
    fig.suptitle("ResolveLab v4 development evidence: grouped splits, separate calibration, explicit scope denials",fontsize=13)
    fig.savefig(OUT/"development_analysis.png",dpi=180);fig.savefig(OUT/"development_analysis.svg")
    fig,axes=plt.subplots(1,2,figsize=(10,4.3),constrained_layout=True)
    names=list(resources["models"]);values=list(resources["models"].values())
    axes[0].bar(names,[row["warm_p95_ms"] for row in values],color=["#64748b","#7c3aed","#2563eb"])
    axes[0].set(ylabel="Warm routing p95 (ms)",title="Same 80 inputs, separate fresh processes")
    axes[1].bar(names,[row["peak_process_rss_mb"] for row in values],color=["#64748b","#7c3aed","#2563eb"])
    axes[1].set(ylabel="Peak process RSS (MiB)",title="Interpreter and libraries included")
    fig.suptitle("Local CPU resource tradeoff; one measured run, no cloud-capacity claim",fontsize=12)
    fig.savefig(OUT/"resource_comparison.png",dpi=180);fig.savefig(OUT/"resource_comparison.svg")
    print(json.dumps({"model_only_family_test_N":cal_n,"raw_ece":raw_ece,"temperature_ece":cal_ece,"resources":resources["models"]}))


if __name__=="__main__":main()
