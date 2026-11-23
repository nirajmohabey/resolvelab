"""Grouped development + separate calibration/selection + frozen candidate.

Writes only artifacts/v4 and a new immutable model. Never promotes the candidate
or reads an undisclosed final audit. Older model artifacts/pointers are retained.
"""
from __future__ import annotations

import collections
import csv
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"));sys.path.insert(0,str(ROOT/"scripts"))
import numpy as np
from scipy.optimize import minimize_scalar
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import FeatureUnion
from resolvelab.ml import normalize_v2,routing_text_v4,calibrated_probabilities,apply_scope_guards
from resolvelab.retrieval import embed_consistent,EMBEDDING_MODEL
from train import groups_for,metrics,SEED,LABELS
from model_artifacts import save_immutable

OUT=ROOT/"artifacts/v4"


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def dump(path,data):Path(path).write_text(json.dumps(data,indent=2,allow_nan=False)+"\n",encoding="utf-8")


def source_data():
    membership=OUT/"source_membership-v2.jsonl"
    if membership.exists():
        return [json.loads(line) for line in membership.read_text().splitlines()]
    raw=ROOT/"work/raw/Bitext_Sample_Customer_Support_Training_Dataset_27K_responses-v11.csv"
    mapping={"track_order":"track_order","cancel_order":"cancel_request","contact_human_agent":"human_handoff"}
    seen={};rows=[]
    for number,row in enumerate(csv.DictReader(raw.open(encoding="utf-8-sig"))):
        normalized=routing_text_v4(row["instruction"]);label=mapping.get(row["intent"],"out_of_scope")
        if normalized in seen:
            if seen[normalized]!=label:raise RuntimeError("Conflicting normalized source labels")
            continue
        seen[normalized]=label
        rows.append({"source_row":number,"text":normalized,"message":row["instruction"],"label":label,"source_intent":row["intent"]})
    groups=groups_for([row["text"] for row in rows]);y=np.asarray([row["label"] for row in rows])
    folds=list(StratifiedGroupKFold(n_splits=5,shuffle=True,random_state=SEED).split(rows,y,groups))
    test,validation=set(folds[0][1]),set(folds[1][1])
    for i,row in enumerate(rows):
        row["split"]="test" if i in test else "validation" if i in validation else "train"
        row["group_id"]=groups[i];row["text_sha256"]=hashlib.sha256(row["text"].encode()).hexdigest()
    membership.write_text("".join(json.dumps(row,sort_keys=True)+"\n" for row in rows),encoding="utf-8")
    return rows


def embeddings(rows,name):
    texts=[routing_text_v4(row.get("message",row.get("text",""))) for row in rows]
    text_hash=hashlib.sha256("\n".join(texts).encode()).hexdigest()
    cache=ROOT/"work"/f"v4-{name}-{text_hash[:12]}.npy"
    if cache.exists():return np.load(cache)
    parts=[]
    for index in range(0,len(texts),256):
        parts.append(embed_consistent(texts[index:index+256]))
        if index%4096==0:print(json.dumps({"embedding":name,"completed":min(index+256,len(texts)),"N":len(texts)}),flush=True)
    values=np.vstack(parts);np.save(cache,values)
    return values


def balanced_weights(labels,strength):
    counts=collections.Counter(labels)
    return np.asarray([strength*len(labels)/(len(counts)*counts[label]) for label in labels])


def raw_probabilities(bundle,word_x,dense_x):
    weight=bundle["blend_weight"]
    if bundle["architecture"]=="flat":
        values=bundle["flat_word"].predict_proba(word_x)
        if weight:values=(1-weight)*values+weight*bundle["flat_dense"].predict_proba(dense_x)
        return values
    scope=bundle["scope_word"].predict_proba(word_x)
    intent=bundle["intent_word"].predict_proba(word_x)
    if weight:
        scope=(1-weight)*scope+weight*bundle["scope_dense"].predict_proba(dense_x)
        intent=(1-weight)*intent+weight*bundle["intent_dense"].predict_proba(dense_x)
    support=scope[:,list(bundle["scope_word"].classes_).index(1)]
    values=np.zeros((len(support),4),dtype=np.float64)
    for index,label in enumerate(LABELS):
        values[:,index]=(1-support) if label=="out_of_scope" else support*intent[:,list(bundle["intent_word"].classes_).index(label)]
    return values


def acceptance(labels,probabilities,threshold):
    prediction=np.asarray(LABELS)[probabilities.argmax(axis=1)]
    accepted=(probabilities.max(axis=1)>=threshold)&(prediction!="out_of_scope")
    wrong=accepted&(prediction!=labels);ood=labels=="out_of_scope"
    return {"accepted":int(accepted.sum()),"correct_supported":int((accepted&~wrong).sum()),"wrong_accepted":int(wrong.sum()),
            "accepted_error":float(wrong.sum()/accepted.sum()) if accepted.sum() else None,
            "ood_accepted":int((accepted&ood).sum()),"ood_false_acceptance":float((accepted&ood).sum()/ood.sum())}


def main():
    started=time.perf_counter();OUT.mkdir(exist_ok=True)
    source=source_data()
    domain_path=ROOT/"fixtures/domain_development_v2.jsonl"
    domain=[json.loads(line) for line in domain_path.read_text().splitlines()]
    core=json.loads((ROOT/"fixtures/core_routing_development.json").read_text())
    for rows,name in [(source,"source"),(domain,"domain")]:
        texts={};groups={}
        for row in rows:
            value=routing_text_v4(row.get("message",row.get("text","")))
            texts.setdefault(value,set()).add(row["split"]);groups.setdefault(row["group_id"],set()).add(row["split"])
        if any(len(v)>1 for v in texts.values()) or any(len(v)>1 for v in groups.values()):raise RuntimeError(f"Cross-split duplicate/group in {name}")
    sx,dx,cx=embeddings(source,"source"),embeddings(domain,"domain"),embeddings(core,"core")
    sy=np.asarray([row["label"] for row in source]);dy=np.asarray([row["label"] for row in domain]);cy=np.asarray([row["label"] for row in core])
    si={name:np.asarray([i for i,row in enumerate(source) if row["split"]==name]) for name in ["train","validation","test"]}
    di={name:np.asarray([i for i,row in enumerate(domain) if row["split"]==name]) for name in ["train","calibration","validation","test"]}
    training_text=[source[i]["text"] for i in si["train"]]+[routing_text_v4(domain[i]["message"]) for i in di["train"]]
    y=np.concatenate([sy[si["train"]],dy[di["train"]]])
    dense_train=np.vstack([sx[si["train"]],dx[di["train"]]])
    features=FeatureUnion([("word",TfidfVectorizer(ngram_range=(1,2),min_df=1,max_features=40000,sublinear_tf=True)),
                           ("char",TfidfVectorizer(analyzer="char_wb",ngram_range=(3,5),min_df=2,max_features=55000,sublinear_tf=True))])
    word_train=features.fit_transform(training_text)
    partitions={"source_validation":(si["validation"],source,sx,sy),"domain_validation":(di["validation"],domain,dx,dy),
                "calibration":(di["calibration"],domain,dx,dy),"core":(np.arange(len(core)),core,cx,cy)}
    data={}
    for name,(indices,rows,vectors,labels) in partitions.items():
        messages=[rows[int(i)].get("message",rows[int(i)].get("text","")) for i in indices]
        data[name]={"word":features.transform([routing_text_v4(message) for message in messages]),
                    "dense":vectors[indices],"labels":labels[indices],"messages":messages}
    candidates=[];winner=None;selected_bundle=None;diagnostics=[]
    for strength in [.005,.02,.1]:
        weights=np.concatenate([balanced_weights(sy[si["train"]],strength),balanced_weights(dy[di["train"]],1.)])
        for regularization in [4.,20.,100.]:
            models={}
            supported=y!="out_of_scope";scope_y=supported.astype(int)
            for feature_name,values in [("word",word_train),("dense",dense_train)]:
                for task,target,mask in [("flat",y,np.ones(len(y),dtype=bool)),("scope",scope_y,np.ones(len(y),dtype=bool)),("intent",y,supported)]:
                    model=LogisticRegression(C=regularization,max_iter=700,random_state=SEED)
                    model.fit(values[mask],target[mask],sample_weight=weights[mask])
                    models[f"{task}_{feature_name}"]=model
            for architecture in ["flat","hierarchical"]:
                for blend in [0.,.5,1.]:
                    bundle={"version":"domain-router-v4","architecture":architecture,"features":features,"labels":LABELS,
                            "blend_weight":blend,"normalization_version":"semantic-scope-v1","scope_guard_version":"explicit-scope-v1",**models}
                    raw={name:raw_probabilities(bundle,value["word"],value["dense"]) for name,value in data.items()}
                    cal_y=data["calibration"]["labels"]
                    optimized=minimize_scalar(lambda temp:log_loss(cal_y,calibrated_probabilities(raw["calibration"],temp),labels=LABELS),bounds=(.3,3.),method="bounded")
                    for temperature,calibration in [(1.,"none"),(float(optimized.x),"heldout-temperature")]:
                        model_probs={name:calibrated_probabilities(value,temperature) for name,value in raw.items()}
                        cal_nll=float(log_loss(cal_y,model_probs["calibration"],labels=LABELS))
                        probs={name:apply_scope_guards(value,data[name]["messages"],LABELS)[0] for name,value in model_probs.items()}
                        core_prediction=np.asarray(LABELS)[probs["core"].argmax(axis=1)]
                        diagnostics.append({"architecture":architecture,"C":regularization,"source_strength":strength,"blend_weight":blend,"temperature":temperature,
                                            "core":[{"expected":str(cy[i]),"predicted":str(core_prediction[i]),"score":float(probs["core"][i].max()),"message":core[i]["message"]} for i in range(len(core))],
                                            "domain_at_035":acceptance(data["domain_validation"]["labels"],probs["domain_validation"],.35),
                                            "source_at_035":acceptance(data["source_validation"]["labels"],probs["source_validation"],.35),
                                            "domain_wrong":[{"message":domain[int(di['validation'][i])]['message'],"expected":str(data['domain_validation']['labels'][i]),
                                                              "predicted":str(LABELS[int(probs['domain_validation'][i].argmax())]),"score":float(probs['domain_validation'][i].max())}
                                                             for i in range(len(di['validation'])) if LABELS[int(probs['domain_validation'][i].argmax())]!=data['domain_validation']['labels'][i]]})
                        for threshold in np.arange(.35,.951,.025):
                            core_prediction=np.asarray(LABELS)[probs["core"].argmax(axis=1)]
                            core_accepted=(probs["core"].max(axis=1)>=threshold)&(core_prediction!="out_of_scope")
                            core_pass=bool(np.all(core_prediction==cy) and np.all(core_accepted[cy!="out_of_scope"]))
                            if not core_pass:continue
                            dm=acceptance(data["domain_validation"]["labels"],probs["domain_validation"],threshold)
                            sm=acceptance(data["source_validation"]["labels"],probs["source_validation"],threshold)
                            if not dm["accepted"] or dm["accepted_error"]>.02 or dm["ood_false_acceptance"]>.02 or sm["ood_false_acceptance"]>.02 or sm["accepted_error"]>.05:continue
                            row={"architecture":architecture,"C":regularization,"source_strength":strength,"blend_weight":blend,
                                 "temperature":temperature,"calibration":calibration,"threshold":round(float(threshold),3),
                                 "domain_validation":dm,"source_validation":sm,"calibration_log_loss":cal_nll,"core_pass":True}
                            candidates.append(row)
                            objective=(dm["correct_supported"],-dm["wrong_accepted"],sm["correct_supported"],-sm["wrong_accepted"],-cal_nll,
                                       -blend,-int(architecture=="hierarchical"),-regularization,-threshold)
                            if winner is None or objective>winner[0]:
                                winner=(objective,row);selected_bundle=dict(bundle,temperature=temperature,threshold=row["threshold"])
            print(json.dumps({"fit_C":regularization,"source_strength":strength,"feasible_threshold_candidates":len(candidates)}),flush=True)
    if winner is None:
        dump(OUT/"selection_failure.json",{"reason":"No candidate met validation/core gates","candidates":candidates,"diagnostics":diagnostics})
        raise RuntimeError("No candidate met predeclared development safety/core gates; active model remains unchanged")
    selected=winner[1]
    path=save_immutable(selected_bundle,ROOT/"artifacts/router-domain-v4.joblib")
    measurements={};prediction_rows=[]
    for name,indices,rows,vectors,labels in [("source_test",si["test"],source,sx,sy),("domain_test",di["test"],domain,dx,dy),
                                            ("calibration",di["calibration"],domain,dx,dy),("domain_validation",di["validation"],domain,dx,dy)]:
        messages=[rows[int(i)].get("message",rows[int(i)].get("text","")) for i in indices]
        wx=features.transform([routing_text_v4(message) for message in messages])
        raw=raw_probabilities(selected_bundle,wx,vectors[indices])
        model_probabilities=calibrated_probabilities(raw,selected_bundle["temperature"])
        probabilities,guard_reasons=apply_scope_guards(model_probabilities,messages,LABELS)
        prediction=np.asarray(LABELS)[probabilities.argmax(axis=1)]
        measurements[name]=metrics(labels[indices],prediction,probabilities,LABELS,selected["threshold"])
        learned=np.asarray([not reason for reason in guard_reasons])
        if learned.any():
            model_pred=np.asarray(LABELS)[model_probabilities[learned].argmax(axis=1)]
            learned_metrics=metrics(labels[indices][learned],model_pred,model_probabilities[learned],LABELS,selected["threshold"])
            measurements[name]["learned_route_calibration"]={key:learned_metrics[key] for key in ["N","log_loss","reliability_bins"]}
        measurements[name]["scope_rule_decisions"]=int((~learned).sum())
        measurements[name]["log_loss"]=None
        measurements[name]["reliability_bins"]=measurements[name].get("learned_route_calibration",{}).get("reliability_bins",[])
        measurements[name]["calibration_scope"]="Learned routes only. Deterministic scope-denial decisions are not probabilities and are excluded from calibration."
        for n,index in enumerate(indices):
            row=rows[int(index)]
            prediction_rows.append({"partition":name,"id":row.get("id",str(index)),"group_id":row["group_id"],"expected":str(labels[index]),
                                    "predicted":str(prediction[n]),"probabilities":probabilities[n].tolist(),"raw_probabilities":raw[n].tolist(),
                                    "calibrated_model_probabilities":model_probabilities[n].tolist(),"scope_guard_reason":guard_reasons[n]})
    (OUT/"predictions.jsonl").write_text("".join(json.dumps(row)+"\n" for row in prediction_rows),encoding="utf-8")
    report={"version":"domain-router-v4","selected":selected,"candidates":candidates,"metrics":measurements,
            "selection_protocol":"Separate train/calibration/selection/test families. Maximize correct supported validation acceptance with <=2% domain accepted error/OOD acceptance, <=2% source OOD acceptance and <=5% source accepted error. All14 preexisting core regressions must route correctly and supported ones pass confidence. No published audit selects parameters.",
            "source_membership_sha256":digest(OUT/"source_membership-v2.jsonl"),"domain_dataset_sha256":digest(domain_path),
            "core_development_sha256":digest(ROOT/"fixtures/core_routing_development.json"),"embedding_model":EMBEDDING_MODEL,
            "embedding_transform":"routing_text_v4 semantic features then independent one-example ONNX inference, same in fitting/serving",
            "scope_guard_version":"explicit-scope-v1; denies explicit unsupported topics/negated automatic actions, never authorizes an effect",
            "elapsed_seconds":time.perf_counter()-started,"human_claim_support_review":None,"promotion_status":"candidate; active v3 unchanged"}
    dump(OUT/"training_report.json",report)
    manifest={"version":"domain-router-v4","artifact_uri":str(path.relative_to(ROOT)).replace("\\","/"),"artifact_path":str(path.relative_to(ROOT)).replace("\\","/"),
              "artifact_sha256":digest(path),"dataset_sha256":digest(ROOT/"work/raw/Bitext_Sample_Customer_Support_Training_Dataset_27K_responses-v11.csv"),
              "domain_dataset_sha256":digest(domain_path),"split_manifest_sha256":digest(OUT/"source_membership-v2.jsonl"),
              "algorithm":selected["architecture"]+" word/char and frozen MiniLM routing; separate held-out temperature calibration",
              "threshold":selected["threshold"],"validation_threshold":selected["threshold"],"feature_pipeline":selected,
              "retrieval_config":{"mode":"dense","version":"dense-minilm-v1"},
              "metrics":measurements,"report_sha256":digest(OUT/"training_report.json")}
    dump(OUT/"model_manifest.json",manifest)
    freeze={"version":"domain-router-v4","artifact_uri":manifest["artifact_uri"],"artifact_sha256":manifest["artifact_sha256"],
            "threshold":manifest["threshold"],"model_manifest_sha256":digest(OUT/"model_manifest.json"),
            "new_blind_audit":"not opened; evaluate only after this freeze","active_pointer":"unchanged"}
    dump(OUT/"model_freeze.json",freeze)
    print(json.dumps({"freeze":freeze,"selected":selected,"domain_test":measurements["domain_test"]["macro_f1"],"source_test":measurements["source_test"]["macro_f1"]}),flush=True)


if __name__=="__main__":main()
