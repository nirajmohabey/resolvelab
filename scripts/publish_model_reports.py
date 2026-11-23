"""Build static report snapshots from measured, provenance-pinned artifacts."""
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/"web/reports"


def load(relative):return json.loads((ROOT/relative).read_text(encoding="utf-8"))
def write(path,value):
    temporary=path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(value,indent=2)+"\n",encoding="utf-8");temporary.replace(path)


def main():
    DEST.mkdir(exist_ok=True);archive=DEST/"archive-v3"
    if not archive.exists():
        archive.mkdir()
        for name in ["summary.json","model_report.json","independent-evaluation.html","workflow-evaluation.html"]:
            if (DEST/name).exists():shutil.copy2(DEST/name,archive/name)
    manifest=load("artifacts/model_manifest.json")
    if manifest["version"]!="domain-router-v4":raise RuntimeError("Default release is not the evaluated v4")
    final=load("artifacts/v4/blind-final/workflow-evaluation.json")
    analysis=load("artifacts/v4/final-analysis/analysis.json")
    if final["model_artifact_sha256"]!=manifest["artifact_sha256"]:raise RuntimeError("Static data would mismatch actual evaluated model")
    invariant_options=[]
    for path in (ROOT/"artifacts").glob("**/workflow-evaluation.json"):
        report=json.loads(path.read_text(encoding="utf-8"))
        if report.get("mode")=="deterministic_invariants" and report.get("metrics",{}).get("workflow_success",{}).get("denominator")==20:
            invariant_options.append((report.get("code_tree_sha256")==final["code_tree_sha256"],report.get("generated_at",""),path,report))
    if not invariant_options:raise RuntimeError("No measured invariant report exists")
    _,_,invariant_path,invariant=max(invariant_options,key=lambda item:(item[0],item[1]))
    summary={"workflow":{"metrics":invariant["metrics"],"mode":invariant["mode"],"generated_at":invariant["generated_at"],"source_report":str(invariant_path.relative_to(ROOT)).replace("\\","/")},
             "model":{"version":manifest["version"],"artifact_sha256":manifest["artifact_sha256"],"source_macro_f1":manifest["metrics"]["source_test"]["macro_f1"],
                      "source_count":manifest["metrics"]["source_test"]["N"],"independent_macro_f1":final["routing_metrics"]["macro_f1"],"independent_count":160,
                      "independent_workflow_success":final["metrics"]["workflow_success"],"accepted_coverage":final["routing_metrics"]["accepted_coverage"],
                      "accepted_routing_error":final["routing_metrics"]["accepted_routing_error"],"ood_false_acceptance":final["routing_metrics"]["ood_false_acceptance"],
                      "scope_rule_decisions":final["routing_metrics"]["scope_rule_decisions"],"semantic_groups":87,"claim_support_human_review":None},
             "paired_final":analysis["points"],"group_bootstrap95":analysis["group_bootstrap95"],"source_code_sha256":final["code_tree_sha256"]}
    model_report={"active_model":manifest,"independent_final":{"dataset_sha256":final["dataset_sha256"],"routing_metrics":final["routing_metrics"],"workflow_metrics":final["metrics"],
                  "release_provenance":final["release_provenance"],"code_tree_sha256":final["code_tree_sha256"]},
                  "paired_final_analysis":analysis,"development_analysis":load("artifacts/v4/analysis.json"),"split_audit":load("artifacts/v4/split_audit.json"),
                  "retrieval_benchmark":load("artifacts/retrieval-v2/report.json"),"cpu_resources":load("artifacts/v4/resources.json"),
                  "source_baseline":load("artifacts/routing_report.json"),"human_claim_support_review":None,"paid_provider_quality_and_cost":None,
                  "interpretation":"V4 primarily improves accepted workflow coverage versus v3, while raw paired macro-F1 is similar. Accepted errors 4/104 and unsupported acceptance 1/40 remain. Scope-rule decisions are not calibrated probabilities. Synthetic agent-authored groups do not establish real-customer or unattended-production quality."}
    write(DEST/"summary.json",summary);write(DEST/"model_report.json",model_report)
    shutil.copy2(ROOT/"artifacts/v4/blind-final/workflow-evaluation.html",DEST/"independent-evaluation.html")
    shutil.copy2(invariant_path.with_suffix(".html"),DEST/"workflow-evaluation.html")
    assets=DEST/"assets";assets.mkdir(exist_ok=True)
    for source,target in [("artifacts/v3-analysis/routing_error_analysis","routing-error-v3"),("artifacts/v4/development_analysis","routing-development-v4"),
                          ("artifacts/v4/resource_comparison","routing-resources-v4"),("artifacts/retrieval-v2/retrieval_comparison","retrieval-benchmark-v2"),
                          ("artifacts/v4/final-analysis/paired_comparison","routing-blind-comparison-v4")]:
        for extension in [".png",".svg"]:shutil.copy2(ROOT/(source+extension),assets/(target+extension))
    write(DEST/"analysis.json",{"final":analysis,"resources":load("artifacts/v4/resources.json"),"retrieval":load("artifacts/retrieval-v2/report.json")})
    print(json.dumps({"static_model":manifest["version"],"workflow":final["metrics"]["workflow_success"],"macro_f1":final["routing_metrics"]["macro_f1"],"semantic_groups":87,"invariant_source":summary["workflow"]["source_report"]}))


if __name__=="__main__":main()
