"""Score a disclosed historical audit; never use this output for selection."""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"));sys.path.insert(0,str(ROOT/"scripts"))
import numpy as np
from resolvelab.ml import Router,normalize
from resolvelab.retrieval import embed
from train import metrics


def main():
    path=ROOT/"fixtures/independent_routing_cases.jsonl"
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    router=Router();bundle=router.bundle;pipeline=bundle["pipeline"]
    probs=pipeline.predict_proba([normalize(r["message"]) for r in rows])
    if bundle.get("dense_classifier") is not None:
        probs=(1-bundle["blend_weight"])*probs+bundle["blend_weight"]*bundle["dense_classifier"].predict_proba(embed([r["message"] for r in rows]))
    predicted=pipeline.classes_[probs.argmax(axis=1)]
    report={"version":router.version,"dataset_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
            "status":"Historical audit. Its original failures motivated domain adaptation; it is not a fresh final holdout for v3. Neither its rows nor its labels select v3 parameters.",
            "metrics":metrics([r["label"] for r in rows],predicted,probs,pipeline.classes_,router.threshold),
            "cases":[{"id":row["id"],"expected":row["label"],"predicted":str(p),"score":float(prob.max()),
                      "accepted":bool(prob.max()>=router.threshold and p!="out_of_scope")} for row,p,prob in zip(rows,predicted,probs)]}
    output=ROOT/"artifacts"/("historical_routing_audit-"+router.version+".json")
    output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"output":str(output.relative_to(ROOT)),**{k:report["metrics"][k] for k in ["N","accuracy","macro_f1","accepted_count","accepted_routing_error_count","ood_false_acceptance_count"]}}))


if __name__=="__main__":main()
