"""Validation-only selection of a compact dense routing challenger.

The original independent 80-case set is a disclosed diagnostic audit after its
first evaluation. A different agent holds the new blind set until model freeze.
"""
from __future__ import annotations

import csv
import ctypes
import hashlib
import json
from pathlib import Path
import platform
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from resolvelab.ml import normalize, Router
from resolvelab.retrieval import embed, EMBEDDING_MODEL
from train import metrics, LABELS, SEED
from model_artifacts import save_immutable


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, data):
    Path(path).write_text(json.dumps(data, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def peak_rss_mb():
    if sys.platform == "win32":
        class Counters(ctypes.Structure):
            _fields_ = [("cb",ctypes.c_ulong),("PageFaultCount",ctypes.c_ulong),
                        ("PeakWorkingSetSize",ctypes.c_size_t),("WorkingSetSize",ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage",ctypes.c_size_t),("QuotaPagedPoolUsage",ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage",ctypes.c_size_t),("QuotaNonPagedPoolUsage",ctypes.c_size_t),
                        ("PagefileUsage",ctypes.c_size_t),("PeakPagefileUsage",ctypes.c_size_t)]
        value = Counters(); value.cb=ctypes.sizeof(value)
        current = ctypes.windll.kernel32.GetCurrentProcess
        current.restype = ctypes.c_void_p
        memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        memory_info.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        memory_info.restype = ctypes.c_int
        if not memory_info(current(),ctypes.byref(value),value.cb):
            return None
        return value.PeakWorkingSetSize/1024**2
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if sys.platform=="darwin" else 1024)


def select_threshold(labels, probabilities, classes):
    predicted = classes[probabilities.argmax(axis=1)]
    candidates=[]
    for threshold in np.arange(.25,.951,.025):
        measure=metrics(labels,predicted,probabilities,classes,float(threshold))
        if measure["accepted_count"] and measure["accepted_routing_error"]<=.02 and measure["ood_false_acceptance"]<=.01:
            candidates.append({"threshold":round(float(threshold),3),
                               "correct_supported_accepted":measure["accepted_count"]-measure["accepted_routing_error_count"],
                               **{key:measure[key] for key in ["accepted_count","accepted_routing_error_count","accepted_coverage","macro_f1","log_loss","ood_false_acceptance"]}})
    return max(candidates,key=lambda x:(x["correct_supported_accepted"],-x["accepted_routing_error_count"],x["macro_f1"],-x["threshold"])) if candidates else None


def main():
    started=time.perf_counter(); cpu_started=time.process_time()
    artifact=ROOT/"artifacts"
    baseline_path=artifact/"router-tfidf-v1.joblib"
    baseline_manifest=artifact/"model_manifest-tfidf-v1.json"
    if not baseline_path.exists():
        shutil.copy2(artifact/"router.joblib",baseline_path)
        old=json.loads((artifact/"model_manifest.json").read_text())
        old["artifact_uri"]=old["artifact_path"]="artifacts/router-tfidf-v1.joblib"
        old["artifact_sha256"]=sha(baseline_path)
        dump(baseline_manifest,old)
    baseline=joblib.load(baseline_path)
    raw=ROOT/"work/raw/Bitext_Sample_Customer_Support_Training_Dataset_27K_responses-v11.csv"
    rows=list(csv.DictReader(raw.open(encoding="utf-8-sig")))
    membership=[json.loads(x) for x in (artifact/"split_membership.jsonl").read_text().splitlines()]
    texts=[normalize(rows[row["source_row"]]["instruction"]) for row in membership]
    assert all(hashlib.sha256(t.encode()).hexdigest()==r["text_sha256"] for t,r in zip(texts,membership))
    labels=np.asarray([row["label"] for row in membership])
    split=np.asarray([row["split"] for row in membership])
    indices={name:np.flatnonzero(split==name) for name in ["train","validation","test"]}
    cache=ROOT/"work/minilm_source_features.npy"
    cache.parent.mkdir(exist_ok=True)
    if cache.exists():
        vectors=np.load(cache)
        assert vectors.shape==(len(texts),384)
    else:
        batches=[]
        for start in range(0,len(texts),128):
            batches.append(embed(texts[start:start+128]))
            if start%4096==0:
                print(json.dumps({"embedded":min(start+128,len(texts)),"N":len(texts)}),flush=True)
        vectors=np.vstack(batches)
        np.save(cache,vectors)
    train_ix,val_ix,test_ix=(indices[name] for name in ["train","validation","test"])
    tfidf=baseline["pipeline"]
    classes=tfidf.classes_
    val_tfidf=tfidf.predict_proba([texts[i] for i in val_ix])
    options=[]
    baseline_selection=select_threshold(labels[val_ix],val_tfidf,classes)
    options.append({"name":"tfidf-v1","blend_weight":0.0,"C":None,"class_weight":None,
                    **baseline_selection,"classifier":None})
    for regularization in [.1,1.0,10.0]:
        for weighting in [None,"balanced"]:
            classifier=LogisticRegression(C=regularization,class_weight=weighting,max_iter=500,random_state=SEED)
            classifier.fit(vectors[train_ix],labels[train_ix])
            val_dense=classifier.predict_proba(vectors[val_ix])
            assert list(classifier.classes_)==list(classes)
            for weight in [.25,.5,.75,1.0]:
                combined=(1-weight)*val_tfidf+weight*val_dense
                selection=select_threshold(labels[val_ix],combined,classes)
                if selection:
                    options.append({"name":"minilm-logistic-blend","blend_weight":weight,"C":regularization,
                                    "class_weight":weighting,**selection,"classifier":classifier})
    # The rule is declared before opening the blind set. Preference for the
    # simpler baseline breaks identical validation outcomes.
    winner=max(options,key=lambda x:(x["correct_supported_accepted"],-x["accepted_routing_error_count"],
                                     x["macro_f1"],-x["blend_weight"],-x["threshold"]))
    selected=dict(baseline)
    selected.update({"threshold":winner["threshold"],"blend_weight":winner["blend_weight"],
                     "dense_classifier":winner["classifier"],
                     "version":"tfidf-minilm-logistic-v2" if winner["blend_weight"] else "tfidf-logistic-v1"})
    selected_path=artifact/("router-hybrid-v2.joblib" if winner["blend_weight"] else "router-tfidf-v1.joblib")
    if winner["blend_weight"]:
        selected_path=save_immutable(selected,selected_path)
    # Keep the default serving pointer compatible; immutable named artifacts
    # remain available for historical release records and rollback.
    shutil.copy2(selected_path,artifact/"router.joblib")
    report={"selection_protocol":"Use source grouped validation only. Maximize correct supported accepted count with <=2% accepted error and <=1% OOD acceptance; ties prefer fewer wrong accepts, larger macro-F1, then simpler/lower dense weight. No blind final cases opened.",
            "selection_candidates":[{k:v for k,v in row.items() if k!="classifier"} for row in options],
            "selected":{k:v for k,v in winner.items() if k!="classifier"},
            "embedding_model":EMBEDDING_MODEL,"feature_cache_sha256":sha(cache),
            "source_test":{},"original80_diagnostic":{},"latency":{},
            "environment":{"python":platform.python_version(),"platform":platform.platform()}}
    for name,model in [("tfidf_v1",baseline),("selected",selected)]:
        for split_name,which in [("source_test",test_ix)]:
            probabilities=tfidf.predict_proba([texts[i] for i in which])
            if model.get("dense_classifier") is not None:
                probabilities=(1-model["blend_weight"])*probabilities+model["blend_weight"]*model["dense_classifier"].predict_proba(vectors[which])
            pred=classes[probabilities.argmax(axis=1)]
            report[split_name][name]=metrics(labels[which],pred,probabilities,classes,model["threshold"])
    # This first set's failures were already inspected. It is not relabeled
    # as an untouched holdout for the challenger, and it is not used to select.
    diagnostic=[json.loads(x) for x in (ROOT/"fixtures/independent_routing_cases.jsonl").read_text().splitlines()]
    diagnostic_vectors=embed([r["message"] for r in diagnostic])
    for name,model,path in [("tfidf_v1",baseline,baseline_path),("selected",selected,selected_path)]:
        probabilities=tfidf.predict_proba([normalize(r["message"]) for r in diagnostic])
        if model.get("dense_classifier") is not None:
            probabilities=(1-model["blend_weight"])*probabilities+model["blend_weight"]*model["dense_classifier"].predict_proba(diagnostic_vectors)
        pred=classes[probabilities.argmax(axis=1)]
        report["original80_diagnostic"][name]=metrics([r["label"] for r in diagnostic],pred,probabilities,classes,model["threshold"])
        router=Router(str(path)); router.predict(diagnostic[0]["message"])
        timings=[]; tick_cpu=time.process_time()
        for row in diagnostic:
            tick=time.perf_counter();router.predict(row["message"]);timings.append((time.perf_counter()-tick)*1000)
        report["latency"][name]={"N":len(timings),"warm":True,"p50_ms":float(np.median(timings)),
                                 "p95_ms":float(np.percentile(timings,95)),"cpu_seconds":time.process_time()-tick_cpu,
                                 "peak_process_rss_mb":peak_rss_mb(),"scope":"Routing only, original80 diagnostic inputs; shared training process high-water memory, not isolated serving RSS."}
    report["elapsed_seconds"]=time.perf_counter()-started
    report["cpu_seconds"]=time.process_time()-cpu_started
    dump(artifact/"challenger_report.json",report)
    manifest=json.loads(baseline_manifest.read_text())
    manifest.update({"version":selected["version"],"artifact_uri":str(selected_path.relative_to(ROOT)).replace("\\","/"),
                     "artifact_path":str(selected_path.relative_to(ROOT)).replace("\\","/"),"artifact_sha256":sha(selected_path),
                     "algorithm":"TF-IDF / frozen MiniLM dense-feature logistic probability blend" if winner["blend_weight"] else manifest["algorithm"],
                     "threshold":selected["threshold"],"validation_threshold":selected["threshold"],
                     "metrics":{"source_test":report["source_test"]["selected"],"original80_diagnostic":report["original80_diagnostic"]["selected"]},
                     "selection_report_sha256":sha(artifact/"challenger_report.json")})
    dump(artifact/"model_manifest.json",manifest)
    freeze={"version":manifest["version"],"artifact_uri":manifest["artifact_uri"],"artifact_sha256":manifest["artifact_sha256"],
            "model_manifest_sha256":sha(artifact/"model_manifest.json"),"threshold":manifest["threshold"],
            "blind_final_evaluation":"not opened; delegated agent must evaluate after this freeze"}
    dump(artifact/"model_freeze.json",freeze)
    print(json.dumps({"selected":{k:v for k,v in winner.items() if k!="classifier"},
                      "version":manifest["version"],"artifact_sha256":manifest["artifact_sha256"],
                      "original80_diagnostic_macro_f1":report["original80_diagnostic"]["selected"]["macro_f1"],
                      "elapsed_seconds":report["elapsed_seconds"]}),flush=True)


if __name__=="__main__":
    main()
