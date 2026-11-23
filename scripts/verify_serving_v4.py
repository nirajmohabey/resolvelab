"""Verify offline/serving feature consistency without fitting/promoting models."""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from resolvelab.ml import Router


def main():
    manifest=json.loads((ROOT/"artifacts/v4/model_manifest.json").read_text())
    router=Router(str(ROOT/manifest["artifact_uri"]))
    domain={row["id"]:row for row in [json.loads(line) for line in (ROOT/"fixtures/domain_development_v2.jsonl").read_text().splitlines()]}
    saved=[json.loads(line) for line in (ROOT/"artifacts/v4/predictions.jsonl").read_text().splitlines() if json.loads(line)["partition"]=="domain_test"]
    bad_intent=[];bad_acceptance=[];differences=[]
    for row in saved:
        actual=router.predict(domain[row["id"]]["message"])
        if actual["intent"]!=row["predicted"]:bad_intent.append(row["id"])
        expected_accept=max(row["probabilities"])>=router.threshold and row["predicted"]!="out_of_scope"
        actual_accept=actual["score"]>=router.threshold and actual["intent"]!="out_of_scope"
        if expected_accept!=actual_accept:bad_acceptance.append(row["id"])
        differences.append(abs(max(row["probabilities"])-actual["score"]))
    report={"N":len(saved),"intent_mismatches":bad_intent,"accepted_decision_mismatches":bad_acceptance,
            "max_absolute_score_difference":max(differences),"score_tolerance":1e-6,"threshold":router.threshold,
            "artifact_sha256":hashlib.sha256((ROOT/manifest["artifact_uri"]).read_bytes()).hexdigest(),
            "scope":"Actual one-message Router serving path versus saved untuned domain-family test predictions. Float reductions can differ at about1e-7; no decision difference is allowed."}
    (ROOT/"artifacts/v4/serving_consistency.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report))
    if bad_intent or bad_acceptance or max(differences)>1e-6:raise SystemExit("Serving/offline predictions disagree")


if __name__=="__main__":main()
