"""Domain-adapt using source+new development data; never tune on audit sets."""
from __future__ import annotations
import collections
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"));sys.path.insert(0,str(ROOT/"scripts"))
import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion,Pipeline
from resolvelab.ml import normalize
from resolvelab.retrieval import embed,EMBEDDING_MODEL
from train import metrics,SEED
from model_artifacts import save_immutable


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def dump(path,value):Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+"\n",encoding="utf-8")


def main():
    started=time.perf_counter();artifact=ROOT/"artifacts"
    if not (artifact/"model_manifest-v2.json").exists():
        shutil.copy2(artifact/"model_manifest.json",artifact/"model_manifest-v2.json")
        shutil.copy2(artifact/"model_freeze.json",artifact/"model_freeze-v2.json")
    source=list(csv.DictReader((ROOT/"work/raw/Bitext_Sample_Customer_Support_Training_Dataset_27K_responses-v11.csv").open(encoding="utf-8-sig")))
    membership=[json.loads(x) for x in (artifact/"split_membership.jsonl").read_text().splitlines()]
    source_text=[normalize(source[r["source_row"]]["instruction"]) for r in membership]
    source_y=np.asarray([r["label"] for r in membership]);source_split=np.asarray([r["split"] for r in membership])
    source_vec=np.load(ROOT/"work/minilm_source_features.npy")
    assert source_vec.shape==(len(source_y),384)
    domain_file=ROOT/"fixtures/domain_development_v1.jsonl"
    domain=[json.loads(x) for x in domain_file.read_text().splitlines()]
    domain_text=[normalize(r["message"]) for r in domain]
    domain_y=np.asarray([r["label"] for r in domain]);domain_split=np.asarray([r["split"] for r in domain])
    seen={};group_splits={}
    for row,value in zip(domain,domain_text):
        assert value not in seen or seen[value]==row["split"],"Normalized duplicate crosses domain splits"
        seen[value]=row["split"]
        assert row["group_id"] not in group_splits or group_splits[row["group_id"]]==row["split"]
        group_splits[row["group_id"]]=row["split"]
    domain_vec=np.vstack([embed([r["message"] for r in domain[start:start+128]]) for start in range(0,len(domain),128)])
    source_ix={name:np.flatnonzero(source_split==name) for name in ["train","validation","test"]}
    domain_ix={name:np.flatnonzero(domain_split==name) for name in ["train","validation","test"]}
    train_text=[source_text[i] for i in source_ix["train"]]+[domain_text[i] for i in domain_ix["train"]]
    train_y=np.concatenate([source_y[source_ix["train"]],domain_y[domain_ix["train"]]])
    features=FeatureUnion([("word",TfidfVectorizer(ngram_range=(1,2),min_df=2,max_features=35000,sublinear_tf=True)),
                           ("char",TfidfVectorizer(analyzer="char_wb",ngram_range=(3,5),min_df=2,max_features=50000,sublinear_tf=True))])
    train_x=features.fit_transform(train_text)
    train_v=np.vstack([source_vec[source_ix["train"]],domain_vec[domain_ix["train"]]])
    validation_x={"source":features.transform([source_text[i] for i in source_ix["validation"]]),
                  "domain":features.transform([domain_text[i] for i in domain_ix["validation"]])}
    validation_v={"source":source_vec[source_ix["validation"]],"domain":domain_vec[domain_ix["validation"]]}
    validation_y={"source":source_y[source_ix["validation"]],"domain":domain_y[domain_ix["validation"]]}
    candidates=[];winner=None;winning_models=None
    for source_strength in [.005,.02,.1]:
        source_counts=collections.Counter(source_y[source_ix["train"]]);domain_counts=collections.Counter(domain_y[domain_ix["train"]])
        source_weights=np.asarray([source_strength*len(source_ix["train"])/(4*source_counts[label]) for label in source_y[source_ix["train"]]])
        domain_weights=np.asarray([len(domain_ix["train"])/(4*domain_counts[label]) for label in domain_y[domain_ix["train"]]])
        weights=np.concatenate([source_weights,domain_weights])
        for regularization in [1.,10.,100.]:
            word=LogisticRegression(C=regularization,max_iter=700,random_state=SEED)
            dense=LogisticRegression(C=regularization,max_iter=700,random_state=SEED)
            word.fit(train_x,train_y,sample_weight=weights);dense.fit(train_v,train_y,sample_weight=weights)
            assert list(word.classes_)==list(dense.classes_)
            classes=word.classes_
            word_probs={name:word.predict_proba(values) for name,values in validation_x.items()}
            dense_probs={name:dense.predict_proba(values) for name,values in validation_v.items()}
            for blend in [0.,.5,.75,1.]:
                probs={name:(1-blend)*word_probs[name]+blend*dense_probs[name] for name in validation_y}
                predictions={name:classes[values.argmax(axis=1)] for name,values in probs.items()}
                for threshold in np.arange(.25,.951,.025):
                    measures={}
                    for name,y in validation_y.items():
                        accepted=(probs[name].max(axis=1)>=threshold)&(predictions[name]!="out_of_scope")
                        wrong=accepted&(predictions[name]!=y);ood=y=="out_of_scope"
                        measures[name]={"correct_supported":int((accepted&~wrong).sum()),"wrong_accepted":int(wrong.sum()),
                                        "accepted_count":int(accepted.sum()),"ood_accepted":int((accepted&ood).sum()),
                                        "accepted_error":float(wrong.sum()/accepted.sum()) if accepted.sum() else 0.,
                                        "ood_false_acceptance":float((accepted&ood).sum()/ood.sum())}
                    d,s=measures["domain"],measures["source"]
                    if d["accepted_error"]>.05 or d["ood_false_acceptance"]>.05 or s["ood_false_acceptance"]>.05:
                        continue
                    row={"source_strength":source_strength,"C":regularization,"blend_weight":blend,"threshold":round(float(threshold),3),
                         "validation":measures}
                    # Domain correctness/coverage first; source coverage breaks
                    # ties after safety. This rule precedes all new audit output.
                    objective=(d["correct_supported"],-d["wrong_accepted"],s["correct_supported"],-s["wrong_accepted"],-blend,-source_strength,-regularization,-threshold)
                    candidates.append(row)
                    if winner is None or objective>winner[0]:
                        winner=(objective,row);winning_models=(word,dense)
    if winner is None:raise RuntimeError("No candidate meets predeclared validation safety limits")
    selected=winner[1];word,dense=winning_models
    pipeline=Pipeline([("features",features),("classifier",word)])
    bundle={"pipeline":pipeline,"dense_classifier":dense if selected["blend_weight"] else None,
            "blend_weight":selected["blend_weight"],"version":"domain-hybrid-v3","threshold":selected["threshold"],
            "normalization_version":"ascii-lower-order-placeholder-v1","labels":list(word.classes_)}
    path=save_immutable(bundle,artifact/"router-domain-v3.joblib")
    results={}
    for name,ix,texts,vectors,labels in [("source_test",source_ix["test"],source_text,source_vec,source_y),
                                        ("domain_family_test",domain_ix["test"],domain_text,domain_vec,domain_y)]:
        probs=pipeline.predict_proba([texts[i] for i in ix])
        if bundle["dense_classifier"] is not None:
            probs=(1-selected["blend_weight"])*probs+selected["blend_weight"]*dense.predict_proba(vectors[ix])
        pred=word.classes_[probs.argmax(axis=1)]
        results[name]=metrics(labels[ix],pred,probs,word.classes_,selected["threshold"])
    report={"version":bundle["version"],"selected":selected,"selection_protocol":"Domain validation correct-supported coverage first, then fewer wrong domain accepts, then source correct-supported coverage/fewer wrong source accepts, then simpler model. Domain accepted error <=5%, domain OOD false acceptance <=5%, source OOD false acceptance <=5%. No observed audit is fitted or used for selection.",
            "candidates":candidates,"domain_dataset_sha256":sha(domain_file),"source_split_sha256":sha(artifact/"split_manifest.json"),
            "embedding_model":EMBEDDING_MODEL,"results":results,"elapsed_seconds":time.perf_counter()-started,
            "historical_audit_policy":"Both observed80 sets are retired diagnostics for v3. New final audit must remain hidden until the artifact and threshold are frozen.",
            "domain_split_check":{"cross_split_normalized_duplicates":0,"cross_split_authored_families":0}}
    dump(artifact/"domain_training_report.json",report)
    manifest=json.loads((artifact/"model_manifest-v2.json").read_text())
    manifest.update({"version":bundle["version"],"artifact_uri":str(path.relative_to(ROOT)).replace("\\","/"),"artifact_path":str(path.relative_to(ROOT)).replace("\\","/"),
                     "artifact_sha256":sha(path),"algorithm":"Domain-adapted word/char TF-IDF and frozen MiniLM dense logistic probability blend",
                     "threshold":selected["threshold"],"validation_threshold":selected["threshold"],
                     "domain_dataset_sha256":sha(domain_file),"metrics":results,"selection_report_sha256":sha(artifact/"domain_training_report.json")})
    manifest["feature_pipeline"]={"word_ngrams":[1,2],"char_wb_ngrams":[3,5],"word_max_features":35000,"char_max_features":50000,
                                  "logistic_C":selected["C"],"max_iter":700,"class_weight":None,
                                  "sample_weight":"Class-balanced separately within source and domain; source scaled by source_strength",
                                  "source_strength":selected["source_strength"],"dense_probability_weight":selected["blend_weight"],
                                  "dense_embedding_model":EMBEDDING_MODEL,"dense_embedding_dimension":384}
    dump(artifact/"model_manifest.json",manifest)
    shutil.copy2(path,artifact/"router.joblib")
    freeze={"version":bundle["version"],"artifact_uri":manifest["artifact_uri"],"artifact_sha256":manifest["artifact_sha256"],
            "model_manifest_sha256":sha(artifact/"model_manifest.json"),"threshold":selected["threshold"],
            "blind_final_evaluation":"not opened; a different agent must evaluate after this freeze"}
    dump(artifact/"model_freeze.json",freeze)
    print(json.dumps({"selected":selected,"freeze":freeze,"domain_test_macro_f1":results["domain_family_test"]["macro_f1"],
                      "source_test_macro_f1":results["source_test"]["macro_f1"],"elapsed_seconds":report["elapsed_seconds"]}),flush=True)


if __name__=="__main__":main()
