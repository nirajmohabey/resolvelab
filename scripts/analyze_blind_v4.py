"""Paired, group-aware analysis of the frozen final audit. Never trains models."""
import csv
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"artifacts/v4/final-analysis"
LABELS=["track_order","cancel_request","human_handoff","out_of_scope"]


def macro_f1(truth,prediction):
    counts=np.bincount(truth*4+prediction,minlength=16).reshape(4,4)
    denominator=counts.sum(axis=0)+counts.sum(axis=1)
    return float(np.divide(2*counts.diagonal(),denominator,out=np.zeros(4,dtype=float),where=denominator>0).mean())


def measures(data,index):
    truth,pred,passed,accepted,escalated,needs_escalation=[data[key][index] for key in ["truth","pred","passed","accepted","escalated","needs_escalation"]]
    wrong=truth!=pred;ood=truth==3
    return {"workflow_success":float(passed.mean()),"routing_macro_f1":macro_f1(truth,pred),"routing_accuracy":float((~wrong).mean()),
            "accepted_coverage":float(accepted.mean()),"accepted_error":float((wrong&accepted).sum()/accepted.sum()) if accepted.sum() else None,
            "ood_false_acceptance":float((accepted&ood).sum()/ood.sum()) if ood.sum() else None,
            "safe_escalation_recall":float((escalated&needs_escalation).sum()/needs_escalation.sum()) if needs_escalation.sum() else None}


def main():
    OUT.mkdir(exist_ok=True)
    fixture=ROOT/"fixtures/v4_blind_v1.jsonl"
    cases=[json.loads(line) for line in fixture.read_text().splitlines()]
    ids=[row["id"] for row in cases];groups=sorted({row["group_id"] for row in cases})
    paths={"TF-IDF v1":ROOT/"artifacts/v4/paired-baseline/workflow-evaluation.json",
           "Domain v3":ROOT/"artifacts/v4/paired-v3/workflow-evaluation.json",
           "Scope + intent v4":ROOT/"artifacts/v4/blind-final/workflow-evaluation.json"}
    reports={name:json.loads(path.read_text()) for name,path in paths.items()}
    arrays={};provenance={}
    for name,report in reports.items():
        if report["dataset_sha256"]!=hashlib.sha256(fixture.read_bytes()).hexdigest():raise RuntimeError("Paired datasets differ")
        if report.get("code_changed_during_run") or not report["release_provenance"]["artifact_hash_matches_database"]:raise RuntimeError("Unclean source/artifact provenance")
        observed={row["id"]:row for row in report["cases"]}
        if set(observed)!=set(ids):raise RuntimeError("Paired case IDs differ")
        rows=[observed[value] for value in ids]
        arrays[name]={"truth":np.asarray([LABELS.index(row["expected_intent"]) for row in cases]),
                      "pred":np.asarray([LABELS.index(row["routing_result"]["predicted"]) for row in rows]),
                      "passed":np.asarray([row["passed"] for row in rows]),"accepted":np.asarray([row["routing_result"].get("accepted",False) for row in rows]),
                      "escalated":np.asarray([row["observed"].get("disposition")=="escalated" and not row["unauthorized_effects"] for row in rows]),
                      "needs_escalation":np.asarray([row["expected_disposition"]=="escalated" for row in cases])}
        provenance[name]={"model_version":report["model_version"],"model_artifact_sha256":report["model_artifact_sha256"],"model_manifest_sha256":report["model_manifest_sha256"],
                          "release_id":report["release_id"],"retrieval_version":report["retrieval_version"],"source_report_sha256":hashlib.sha256(paths[name].read_bytes()).hexdigest(),
                          "code_tree_sha256":report["code_tree_sha256"],"unauthorized_effect_count":report["metrics"]["unauthorized_effect_count"],
                          "effect_audits_completed":report["metrics"]["all_tenant_effect_checks_completed"],"runner_errors":report["metrics"]["runner_errors"]}
    group_indices={group:np.asarray([i for i,row in enumerate(cases) if row["group_id"]==group]) for group in groups}
    rng=np.random.default_rng(42);replicates=5000
    samples={name:[] for name in arrays}
    for _ in range(replicates):
        selected=rng.choice(groups,size=len(groups),replace=True)
        index=np.concatenate([group_indices[group] for group in selected])
        for name,data in arrays.items():samples[name].append(measures(data,index))
    points={name:measures(data,np.arange(len(cases))) for name,data in arrays.items()}
    intervals={name:{key:[float(np.nanpercentile([row[key] for row in values],2.5)),float(np.nanpercentile([row[key] for row in values],97.5))] for key in points[name]} for name,values in samples.items()}
    differences={}
    newest="Scope + intent v4"
    for older in ["TF-IDF v1","Domain v3"]:
        differences[older]={}
        for key in points[newest]:
            values=np.asarray([a[key]-b[key] for a,b in zip(samples[newest],samples[older])])
            differences[older][key]={"difference":points[newest][key]-points[older][key],"group_bootstrap95":[float(np.nanpercentile(values,2.5)),float(np.nanpercentile(values,97.5))]}
    current=reports[newest];current_rows={row["id"]:row for row in current["cases"]};errors=[]
    for row in cases:
        result=current_rows[row["id"]]
        if not result["passed"]:
            route=result["routing_result"]
            errors.append({key:row[key] for key in ["id","group_id","message","slice","expected_intent","expected_disposition"]}|
                          {"predicted_intent":route["predicted"],"observed_disposition":result["observed"].get("disposition"),"score_source":route.get("score_source"),
                           "routing_score":None if route.get("score_source")=="scope_rule" else route.get("score"),"scope_guard_reason":route.get("scope_guard_reason"),"failure":result.get("error")})
    summary={"dataset_sha256":hashlib.sha256(fixture.read_bytes()).hexdigest(),"N":len(cases),"semantic_groups":len(groups),"replicates":replicates,"bootstrap_seed":42,
             "uncertainty_method":"Percentile paired cluster bootstrap; resample87 authored semantic groups with replacement, retain all their case variants; same resample for all releases. These nonrandom synthetic groups are not a representative customer sample, so intervals describe this audit's group sensitivity rather than production generalization.",
             "points":points,"group_bootstrap95":intervals,"paired_v4_differences":differences,"provenance":provenance,
             "v4_raw_metrics":current["metrics"],"v4_routing_metrics":current["routing_metrics"],"v4_failures":errors,
             "interpretation":"V4 improves paired workflow coverage but accepts4 wrong routes/104 and1 unsupported case/40.27 rule denials are not model probabilities;6 human requests were over-denied into unsupported scope. Every cancellation still needs an authorized exact-proposal approval. No model tuning followed this audit.",
             "claim_support_human_review":None}
    (OUT/"analysis.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    with (OUT/"human_claim_review_queue.csv").open("w",encoding="utf-8",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=["case_id","group_id","answer","disposition","citations","reviewer_1","reviewer_2","adjudication"]);writer.writeheader()
        for row in cases:
            observed=current_rows[row["id"]]["observed"]
            writer.writerow({"case_id":row["id"],"group_id":row["group_id"],"answer":observed.get("answer",""),"disposition":observed.get("disposition",""),
                             "citations":";".join(c["chunk_id"] for c in observed.get("citations",[]))})
    plt.rcParams.update({"font.size":11,"axes.spines.top":False,"axes.spines.right":False})
    fig,axes=plt.subplots(1,3,figsize=(13,4.6),constrained_layout=True)
    names=list(arrays);labels=["TF-IDF v1","Domain v3","Scope + intent v4"];colors=["#64748b","#7c3aed","#2563eb"]
    for axis,key,title in [(axes[0],"workflow_success","Whole-workflow success"),(axes[1],"routing_macro_f1","Four-class routing macro-F1"),(axes[2],"accepted_coverage","Supported accepted coverage")]:
        values=[points[name][key] for name in names]
        low=[values[i]-intervals[name][key][0] for i,name in enumerate(names)];high=[intervals[name][key][1]-values[i] for i,name in enumerate(names)]
        axis.bar(range(3),values,yerr=[low,high],capsize=4,color=colors)
        axis.set(xticks=range(3),xticklabels=labels,ylim=(0,1.08),title=title,ylabel="Fraction / macro-F1")
        axis.tick_params(axis="x",rotation=20,labelsize=9)
        for i,value in enumerate(values):axis.text(i,intervals[names[i]][key][1]+.025,f"{value:.3f}",ha="center",fontsize=10)
    fig.suptitle("Same 160 frozen cases across 87 authored groups; 95% paired group-bootstrap intervals",fontsize=13)
    fig.savefig(OUT/"paired_comparison.png",dpi=180);fig.savefig(OUT/"paired_comparison.svg")
    print(json.dumps({"points":points,"v4_vs_v3_workflow_difference":differences["Domain v3"]["workflow_success"],"v4_vs_baseline_workflow_difference":differences["TF-IDF v1"]["workflow_success"]}))


if __name__=="__main__":main()
