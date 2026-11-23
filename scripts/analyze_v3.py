"""Audit the disclosed v3 result; does not fit or select a new model."""
import csv
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"artifacts/v3-analysis"


def wilson(success,total):
    if not total:return None
    z=1.959963984540054;p=success/total;den=1+z*z/total
    center=(p+z*z/(2*total))/den
    radius=z*((p*(1-p)/total+z*z/(4*total*total))**.5)/den
    return [max(0,center-radius),min(1,center+radius)]


def main():
    OUT.mkdir(exist_ok=True)
    path=ROOT/"artifacts/v3-blind-final/workflow-evaluation.json"
    frozen=json.loads(path.read_text())
    cases=[row for row in frozen["cases"] if row.get("routing_result")]
    expected=np.asarray([row["routing_result"]["expected"] for row in cases])
    predicted=np.asarray([row["routing_result"]["predicted"] for row in cases])
    scores=np.asarray([row["routing_result"]["score"] for row in cases])
    correct=expected==predicted;supported=predicted!="out_of_scope";ood=expected=="out_of_scope"
    accepted=supported&(scores>=.725)
    confusion=frozen["routing_metrics"]["confusion_matrix"]
    taxonomy={"supported_predicted_ood":int(((expected!="out_of_scope")&(predicted=="out_of_scope")).sum()),
              "wrong_supported_route":int(((expected!=predicted)&supported).sum()),
              "correct_supported_below_threshold":int((correct&supported&~accepted).sum()),
              "unsupported_false_acceptance":int((ood&accepted).sum())}
    curve=[]
    for threshold in np.arange(.25,.976,.025):
        accepted_at=supported&(scores>=threshold);wrong=accepted_at&~correct
        curve.append({"threshold":round(float(threshold),3),"accepted":int(accepted_at.sum()),"wrong":int(wrong.sum()),
                      "coverage":float(accepted_at.mean()),"accepted_error":float(wrong.sum()/accepted_at.sum()) if accepted_at.sum() else None})
    bins=[]
    for low in np.arange(0,1,.1):
        mask=(scores>=low)&(scores<(low+.1) if low<.9 else scores<=1)
        if mask.any():bins.append({"N":int(mask.sum()),"mean_score":float(scores[mask].mean()),"accuracy":float(correct[mask].mean())})
    ece=sum(row["N"]/len(cases)*abs(row["mean_score"]-row["accuracy"]) for row in bins)
    report={"source_report_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"N":len(cases),
            "status":"Disclosed historical diagnostic for v4 planning. No new blind case or label is read.",
            "taxonomy":taxonomy,"coverage_threshold_curve":curve,"top_label_reliability_bins":bins,
            "top_label_ece_10_bins":ece,"probability_limit":"Only maximum scores stored in actual-service report; full multiclass log loss/Brier unavailable from that report.",
            "observed_accepted_error":{"count":int((accepted&~correct).sum()),"N":int(accepted.sum()),"wilson95":wilson(int((accepted&~correct).sum()),int(accepted.sum()))},
            "observed_ood_acceptance":{"count":int((accepted&ood).sum()),"N":int(ood.sum()),"wilson95":wilson(int((accepted&ood).sum()),int(ood.sum()))},
            "coverage":{"count":int(accepted.sum()),"N":len(cases),"wilson95":wilson(int(accepted.sum()),len(cases))},
            "interpretation":"Threshold reduction cannot recover false-OOD predictions. Wrong supported routes were gated, but useful correctly routed requests were also rejected. Synthetic-family validation overestimated fresh linguistic generalization. Zero observed errors is not a production error bound."}
    (OUT/"analysis.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    plt.rcParams.update({"font.size":11,"axes.spines.top":False,"axes.spines.right":False})
    fig,axes=plt.subplots(1,3,figsize=(14,4.5),constrained_layout=True)
    image=axes[0].imshow(confusion,cmap="Blues",vmin=0,vmax=25)
    names=["Track","Cancel","Human","Unsupported"]
    axes[0].set(xticks=range(4),yticks=range(4),xticklabels=names,yticklabels=names,xlabel="Predicted",ylabel="Expected",title="V3 fresh-audit confusion (N=100)")
    axes[0].tick_params(axis="x",rotation=35)
    for i in range(4):
        for j in range(4):axes[0].text(j,i,str(confusion[i][j]),ha="center",va="center",color="white" if confusion[i][j]>13 else "#172554")
    axes[1].plot([row["coverage"] for row in curve],[row["accepted_error"] for row in curve],"o-",color="#7c3aed")
    axes[1].scatter([accepted.mean()],[0],s=70,color="#dc2626",zorder=4,label="Shipped threshold .725")
    axes[1].set(xlabel="Fraction accepted as supported",ylabel="Wrong routes / accepted routes",title="Counterfactual threshold tradeoff")
    axes[1].legend(fontsize=8)
    axes[2].plot([0,1],[0,1],"--",color="#64748b")
    axes[2].plot([row["mean_score"] for row in bins],[row["accuracy"] for row in bins],"o-",color="#047857")
    axes[2].set(xlim=(0,1.02),ylim=(0,1.02),xlabel="Mean routing score in bin",ylabel="Observed routing accuracy",title="Top-label reliability")
    fig.suptitle("ResolveLab v3 diagnostic audit: coverage and uncertainty, not a customer-performance claim",fontsize=13)
    fig.savefig(OUT/"routing_error_analysis.png",dpi=180);fig.savefig(OUT/"routing_error_analysis.svg")
    # A review instrument is not a completed human review or an automatic judge.
    with (OUT/"claim_review_queue.csv").open("w",encoding="utf-8",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=["case_id","answer_to_review","disposition","citation_ids","required_review","reviewer_1","reviewer_2","adjudication"])
        writer.writeheader()
        for row in cases:
            observed=row.get("observed",{})
            writer.writerow({"case_id":row["id"],"answer_to_review":observed.get("answer",""),"disposition":observed.get("disposition",""),
                             "citation_ids":";".join(c["chunk_id"] for c in observed.get("citations",[])),
                             "required_review":"Each material claim: supported/contradicted/insufficient. Check authorized order tool facts and policy citations separately; review uncited claims and missing required facts."})
    print(json.dumps({"taxonomy":taxonomy,"top_label_ece":ece,"accepted_error95":report["observed_accepted_error"]["wilson95"],"ood95":report["observed_ood_acceptance"]["wilson95"]}))


if __name__=="__main__":main()
