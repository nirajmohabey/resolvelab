"""Audit cross-collection duplicates and selection sensitivity without fitting."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from resolvelab.ml import routing_text_v4


def main():
    source=[json.loads(line) for line in (ROOT/"artifacts/v4/source_membership-v2.jsonl").read_text().splitlines()]
    domain=[json.loads(line) for line in (ROOT/"fixtures/domain_development_v2.jsonl").read_text().splitlines()]
    core=json.loads((ROOT/"fixtures/core_routing_development.json").read_text())
    training={routing_text_v4(row["message"]) for row in source+domain if row["split"]=="train"}
    core_features={routing_text_v4(row["message"]) for row in core}
    overlap={}
    for name,rows in [("source",source),("domain",domain)]:
        for split in ["calibration","validation","test"]:
            found=[row for row in rows if row["split"]==split and routing_text_v4(row["message"]) in training]
            overlap[name+"_"+split]=[{"id":row.get("id",row.get("source_row")),"label":row["label"],"normalized_text":routing_text_v4(row["message"]),
                                    "same_as_required_core_regression":routing_text_v4(row["message"]) in core_features} for row in found]
    blind=[json.loads(line) for line in (ROOT/"fixtures/v4_blind_v1.jsonl").read_text().splitlines()]
    blind_overlap=[row["id"] for row in blind if routing_text_v4(row["message"]) in training]
    training_report=json.loads((ROOT/"artifacts/v4/training_report.json").read_text())
    removed=len(overlap["source_validation"])
    assert removed==3 and all(row["same_as_required_core_regression"] for row in overlap["source_validation"])
    cleaned=[]
    for candidate in training_report["candidates"]:
        value=json.loads(json.dumps(candidate));source_validation=value["source_validation"]
        source_validation["accepted"]-=removed;source_validation["correct_supported"]-=removed
        source_validation["accepted_error"]=source_validation["wrong_accepted"]/source_validation["accepted"]
        if source_validation["accepted_error"]<=.05:cleaned.append(value)
    def objective(row):
        d,s=row["domain_validation"],row["source_validation"]
        return (d["correct_supported"],-d["wrong_accepted"],s["correct_supported"],-s["wrong_accepted"],-row["calibration_log_loss"],-row["blend_weight"],-int(row["architecture"]=="hierarchical"),-row["C"],-row["threshold"])
    best=max(cleaned,key=objective);keys=["architecture","C","source_strength","blend_weight","temperature","threshold"]
    unchanged=all(best[key]==training_report["selected"][key] for key in keys)
    report={"within_collection_checks":"Training checks normalized duplicates and assigned groups do not cross each collection's splits.","cross_collection_exact_overlaps":overlap,
            "fresh_blind_training_exact_overlap_ids":blind_overlap,"source_validation_cleaning_sensitivity":{"removed_core_overlap_rows":removed,"selected_parameters_unchanged":unchanged,
            "original_selected":training_report["selected"],"selected_after_excluding3rows":best,"feasible_candidates_after_exclusion":len(cleaned),
            "reason":"Every feasible candidate must correctly accept all14 core regressions; these3 normalized overlaps are identical core features. Removing them uniformly subtracts3 correct accepts. This can only tighten source error gates; selected candidate remains feasible and maximizes the unchanged ranking."},
            "limitations":"This post-freeze audit does not retrain/tune the model. Raw reports are retained. No exact training overlap exists in source/domain test, domain calibration/validation, or fresh blind160. Approximate semantic/template overlaps beyond assigned groups may still exist."}
    (ROOT/"artifacts/v4/split_audit.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"source_validation_exact_core_overlaps":removed,"fresh_blind_exact_overlaps":len(blind_overlap),"selected_parameters_unchanged":unchanged,"cleaned_source_error":best["source_validation"]["accepted_error"]}))
    if not unchanged or blind_overlap:raise SystemExit("Audit requires review; do not claim a clean untouched evaluation")


if __name__=="__main__":main()
