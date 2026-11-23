"""Reproduce grouped Bitext routing experiment and immutable local model artifact."""
from __future__ import annotations

import collections
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import joblib
import numpy as np
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix, f1_score, log_loss
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import FeatureUnion, Pipeline

from resolvelab.ml import keyword_predict, normalize

SEED = 42
LABELS = ["cancel_request", "human_handoff", "out_of_scope", "track_order"]
LABEL_MAP = {"track_order": "track_order", "cancel_order": "cancel_request", "contact_human_agent": "human_handoff"}
GROUPING_VERSION = "char4-minhash32-bands8-jaccard078-v1"
VERSION = "tfidf-logistic-v1"


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def groups_for(texts: list[str]) -> list[str]:
    """Label-blind approximate near-duplicate components, fixed before splitting.

    4-character shingles, 32 deterministic minhashes, 8 bands of 4. Candidate
    pairs require true Jaccard >= .78. Transitive union keeps detected variants
    together. This does not establish that every semantic paraphrase is found.
    """
    parent = list(range(len(texts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    rng = np.random.default_rng(SEED)
    multipliers = rng.integers(1, 2**31, size=32, dtype=np.uint64) * 2 + 1
    offsets = rng.integers(0, 2**32, size=32, dtype=np.uint64)
    buckets = collections.defaultdict(list)
    all_shingles = []
    exact = {}
    for index, value in enumerate(texts):
        # Normalization is label-blind and never uses dataset responses.
        skeleton = re.sub(r"(.)\1{2,}", r"\1\1", value)
        padded = " " + skeleton + " "
        shingles = frozenset(zlib.crc32(padded[i:i+4].encode()) for i in range(max(1, len(padded)-3)))
        all_shingles.append(shingles)
        if skeleton in exact:
            union(index, exact[skeleton])
        else:
            exact[skeleton] = index
        hashes = np.asarray(list(shingles), dtype=np.uint64)
        signature = np.min((hashes[:, None] * multipliers[None, :] + offsets[None, :]) % np.uint64(4294967311), axis=0)
        candidates = set()
        keys = []
        for band in range(8):
            key = (band, tuple(int(x) for x in signature[band*4:(band+1)*4]))
            keys.append(key)
            candidates.update(buckets[key])
        for other in candidates:
            prior = all_shingles[other]
            if min(len(prior), len(shingles)) / max(len(prior), len(shingles)) < .78:
                continue
            if len(prior & shingles) / len(prior | shingles) >= .78:
                union(index, other)
        for key in keys:
            buckets[key].append(index)
    return [sha(texts[find(i)].encode())[:20] for i in range(len(texts))]


def metrics(y, pred, probabilities=None, classes=None, threshold=0.0):
    y, pred = np.asarray(y), np.asarray(pred)
    result = {"N": len(y), "macro_f1": float(f1_score(y, pred, labels=LABELS, average="macro", zero_division=0)),
              "accuracy": float((y == pred).mean()), "labels": LABELS,
              "class_support": dict(collections.Counter(str(x) for x in y)),
              "confusion_matrix": confusion_matrix(y, pred, labels=LABELS).tolist(),
              "classification_report": classification_report(y, pred, labels=LABELS, output_dict=True, zero_division=0)}
    if probabilities is not None:
        probabilities = np.asarray(probabilities, dtype=np.float64)
        probabilities = probabilities / probabilities.sum(axis=1, keepdims=True)
        scores = probabilities.max(axis=1)
        accepted = (scores >= threshold) & (pred != "out_of_scope")
        wrong = accepted & (y != pred)
        ood = y == "out_of_scope"
        result.update({"log_loss": float(log_loss(y, probabilities, labels=list(classes))),
                       "accepted_count": int(accepted.sum()), "accepted_coverage": float(accepted.mean()),
                       "accepted_routing_error_count": int(wrong.sum()),
                       "accepted_routing_error": float(wrong.sum()/accepted.sum()) if accepted.sum() else None,
                       "ood_count": int(ood.sum()), "ood_false_acceptance_count": int((accepted & ood).sum()),
                       "ood_false_acceptance": float((accepted & ood).sum()/ood.sum()) if ood.sum() else None})
        reliability = []
        for low in np.arange(0, 1, .1):
            mask = (scores >= low) & (scores < low+.1 if low < .9 else scores <= 1)
            reliability.append({"bin_low": round(float(low), 1), "N": int(mask.sum()),
                                "mean_score": float(scores[mask].mean()) if mask.any() else None,
                                "accuracy": float((pred[mask] == y[mask]).mean()) if mask.any() else None})
        result["reliability_bins"] = reliability
    return result


def main():
    started = time.perf_counter()
    raw = ROOT / "work/raw/Bitext_Sample_Customer_Support_Training_Dataset_27K_responses-v11.csv"
    if not raw.exists():
        raise SystemExit("Run python scripts/fetch_dataset.py first")
    source_rows = list(csv.DictReader(raw.open(encoding="utf-8-sig")))
    source_intents = set(row["intent"] for row in source_rows)
    assert set(LABEL_MAP) <= source_intents, "Source labels changed"
    assert len(source_intents) == 27, "Inspect a changed source instead of silently training"
    seen, conflicts, records = {}, [], []
    for source_index, row in enumerate(source_rows):
        value = normalize(row["instruction"])
        label = LABEL_MAP.get(row["intent"], "out_of_scope")
        if value in seen:
            if seen[value] != label:
                conflicts.append(value)
            continue
        seen[value] = label
        records.append({"source_row": source_index, "text": value, "label": label,
                        "source_intent": row["intent"], "text_sha256": sha(value.encode())})
    if conflicts:
        raise RuntimeError("Conflicting labels after deduplication require review")
    texts = [r["text"] for r in records]
    labels = np.asarray([r["label"] for r in records])
    groups = groups_for(texts)
    # Fixed 60/20/20 grouped folds. Labels balance folds; features fit train only.
    folds = list(StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=SEED).split(texts, labels, groups))
    test_indices, validation_indices = folds[0][1], folds[1][1]
    test_set, validation_set = set(test_indices), set(validation_indices)
    train_indices = np.asarray([i for i in range(len(records)) if i not in test_set and i not in validation_set])
    splits = {"train": train_indices, "validation": validation_indices, "test": test_indices}
    assigned = {}
    for name, indices in splits.items():
        for i in indices:
            assert groups[i] not in assigned or assigned[groups[i]] == name
            assigned[groups[i]] = name
        assert set(labels[indices]) == set(LABELS), f"Missing class in {name}"
    artifact = ROOT / "artifacts"
    artifact.mkdir(exist_ok=True)
    membership = artifact / "split_membership.jsonl"
    with membership.open("w", encoding="utf-8", newline="\n") as stream:
        for name, indices in splits.items():
            for index in indices:
                record = records[int(index)]
                stream.write(json.dumps({k:v for k,v in record.items() if k != "text"} |
                                        {"group_id": groups[int(index)], "split": name}, sort_keys=True) + "\n")
    split_manifest = {"seed": SEED, "grouping_version": GROUPING_VERSION,
                      "groups": len(set(groups)), "normalized_rows": len(records),
                      "dropped_exact_duplicates": len(source_rows)-len(records),
                      "source_rows": len(source_rows), "source_intents": sorted(source_intents),
                      "label_map": LABEL_MAP | {"all_other_source_intents": "out_of_scope"},
                      "membership_sha256": sha(membership.read_bytes()),
                      "counts": {name: {"N": len(indices), "groups": len({groups[i] for i in indices}),
                                        "labels": dict(collections.Counter(str(x) for x in labels[indices]))}
                                 for name,indices in splits.items()},
                      "cross_split_group_count": 0, "cross_split_normalized_duplicate_count": 0,
                      "limitation": "Approximate lexical grouping cannot identify all semantic paraphrases. Results are synthetic source-distribution measurements."}
    write_json(artifact / "split_manifest.json", split_manifest)
    pipeline = Pipeline([("features", FeatureUnion([
        ("word", TfidfVectorizer(ngram_range=(1,2), min_df=2, max_features=35000, sublinear_tf=True)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3,5), min_df=2, max_features=50000, sublinear_tf=True))])),
        ("classifier", LogisticRegression(C=4.0, max_iter=500, class_weight="balanced", random_state=SEED, solver="lbfgs"))])
    pipeline.fit([texts[i] for i in train_indices], labels[train_indices])
    validation_prob = pipeline.predict_proba([texts[i] for i in validation_indices])
    validation_pred = pipeline.classes_[validation_prob.argmax(axis=1)]
    threshold_results = []
    for threshold in np.arange(.25, .951, .025):
        measure = metrics(labels[validation_indices], validation_pred, validation_prob, pipeline.classes_, float(threshold))
        threshold_results.append({"threshold": round(float(threshold),3), **{key:measure[key] for key in
                                  ("accepted_count","accepted_coverage","accepted_routing_error","ood_false_acceptance")}})
    # Predeclared rule: maximize supported coverage with <=2% validation accepted
    # routing errors and <=1% OOD false acceptance; otherwise abstain at .95.
    feasible = [row for row in threshold_results if row["accepted_count"] > 0
                and row["accepted_routing_error"] <= .02 and row["ood_false_acceptance"] <= .01]
    selected = max(feasible, key=lambda row: (row["accepted_coverage"], -row["threshold"]))["threshold"] if feasible else .95
    bundle = {"pipeline": pipeline, "version": VERSION, "threshold": selected,
              "normalization_version": "ascii-lower-order-placeholder-v1", "labels": LABELS}
    target = artifact / "router.joblib"
    joblib.dump(bundle, target, compress=3)
    report = {"version": VERSION, "seed": SEED, "threshold": selected,
              "threshold_selection": "Validation only; maximize supported accepted coverage with <=2% accepted route error and <=1% OOD false acceptance.",
              "threshold_candidates": threshold_results, "calibration": "Raw logistic probabilities; reliability bins and log loss reported. No probability guarantee; no post-hoc recalibration selected.",
              "hyperparameters": {"C":4.0,"max_iter":500,"class_weight":"balanced","word_ngrams":[1,2],"char_wb_ngrams":[3,5]},
              "splits": {}}
    majority = collections.Counter(labels[train_indices]).most_common(1)[0][0]
    for name, indices in (("validation", validation_indices), ("test", test_indices)):
        samples = [texts[i] for i in indices]
        probabilities = pipeline.predict_proba(samples)
        predicted = pipeline.classes_[probabilities.argmax(axis=1)]
        report["splits"][name] = {"trained": metrics(labels[indices], predicted, probabilities, pipeline.classes_, selected),
                                  "majority": metrics(labels[indices], [majority]*len(indices)),
                                  "keyword": metrics(labels[indices], [keyword_predict(x) for x in samples])}
        report["splits"][name]["source_intent_slices"] = {}
        for source_intent in sorted(source_intents):
            mask = np.asarray([records[int(i)]["source_intent"] == source_intent for i in indices])
            if mask.any():
                accepted = (probabilities[mask].max(axis=1) >= selected) & (predicted[mask] != "out_of_scope")
                report["splits"][name]["source_intent_slices"][source_intent] = {
                    "N": int(mask.sum()), "accuracy": float((predicted[mask] == labels[indices][mask]).mean()),
                    "accepted_count": int(accepted.sum()),
                    "wrong_accepted_count": int((accepted & (predicted[mask] != labels[indices][mask])).sum())}
    independent_file = ROOT / "fixtures/independent_routing_cases.jsonl"
    independent = [json.loads(line) for line in independent_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    independent_prob = pipeline.predict_proba([normalize(r["message"]) for r in independent])
    independent_pred = pipeline.classes_[independent_prob.argmax(axis=1)]
    report["independent_synthetic"] = metrics([r["label"] for r in independent], independent_pred, independent_prob, pipeline.classes_, selected)
    report["independent_synthetic"]["dataset_sha256"] = sha(independent_file.read_bytes())
    report["independent_synthetic"]["authoring"] = "Coding-agent-authored synthetic cases frozen before training output; no independent human review; not real-customer performance."
    report["independent_synthetic"]["cases"] = [{"id":r["id"],"slice":r["slice"],"expected":r["label"],
                                                "predicted":str(p),"score":float(prob.max()),
                                                "accepted":bool(prob.max()>=selected and p!="out_of_scope")}
                                               for r,p,prob in zip(independent, independent_pred, independent_prob)]
    report["independent_baselines"] = {
        "majority": metrics([r["label"] for r in independent], [majority]*len(independent)),
        "keyword": metrics([r["label"] for r in independent], [keyword_predict(r["message"]) for r in independent])}
    latencies = []
    inference_cpu_start = time.process_time()
    for row in independent:
        tick = time.perf_counter()
        pipeline.predict_proba([normalize(row["message"])])
        latencies.append((time.perf_counter()-tick)*1000)
    report["routing_inference"] = {"N":len(latencies), "p50_ms":float(np.median(latencies)),
                                   "p95_ms":float(np.percentile(latencies,95)), "warm":True,
                                   "cpu_seconds":time.process_time()-inference_cpu_start,
                                   "scope":"Classifier only; excludes database, retrieval and generation; individual frozen independent synthetic inputs."}
    elapsed = time.perf_counter()-started
    report["training_elapsed_seconds"] = elapsed
    report["environment"] = {"python": platform.python_version(), "platform": platform.platform(),
                              "scikit_learn": sklearn.__version__, "cpu_count": os.cpu_count(), "numpy": np.__version__}
    write_json(artifact / "routing_report.json", report)
    manifest = {"version": VERSION, "artifact_uri": "artifacts/router.joblib", "artifact_path": "artifacts/router.joblib", "artifact_sha256": sha(target.read_bytes()),
                "dataset_sha256": sha(raw.read_bytes()), "split_manifest_sha256": sha((artifact/"split_manifest.json").read_bytes()),
                "algorithm": "word+character TF-IDF logistic regression", "threshold": selected,
                "validation_threshold": selected, "seed":SEED, "feature_pipeline": report["hyperparameters"],
                "metrics": {"source_test": report["splits"]["test"]["trained"],
                            "independent_synthetic": {k:v for k,v in report["independent_synthetic"].items() if k != "cases"}},
                "environment": report["environment"], "report_sha256": sha((artifact/"routing_report.json").read_bytes())}
    write_json(artifact / "model_manifest.json", manifest)
    print(json.dumps({"elapsed_seconds":round(elapsed,2),"rows":len(records),"groups":len(set(groups)),"threshold":selected,
                      "test_macro_f1":report["splits"]["test"]["trained"]["macro_f1"],
                      "independent_macro_f1":report["independent_synthetic"]["macro_f1"],"artifact_sha256":manifest["artifact_sha256"]}))


if __name__ == "__main__":
    main()
