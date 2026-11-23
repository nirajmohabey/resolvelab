"""Reproduce all routing stages in a disposable source copy, preserving releases.

The trained output stays under ignored work/. Only a comparison manifest is
published under artifacts/rebuild-runs/. This never changes serving pointers.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    run_id=uuid4().hex[:12]
    target=ROOT/"work/rebuild-runs"/run_id
    target.mkdir(parents=True,exist_ok=False)
    for name in ["src","scripts","fixtures"]:
        shutil.copytree(ROOT/name,target/name,ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
    (target/"artifacts/v4").mkdir(parents=True)
    for path in (ROOT/"artifacts").iterdir():
        if path.is_file() and path.suffix in {".json",".jsonl",".joblib"}:shutil.copy2(path,target/"artifacts"/path.name)
    for path in (ROOT/"artifacts/v4").glob("*.json*"):shutil.copy2(path,target/"artifacts/v4"/path.name)
    shutil.copytree(ROOT/"artifacts/embeddings",target/"artifacts/embeddings")
    (target/"work/raw").mkdir(parents=True)
    for path in (ROOT/"work/raw").iterdir():
        if path.is_file():shutil.copy2(path,target/"work/raw"/path.name)
    for path in (ROOT/"work").glob("*.npy"):shutil.copy2(path,target/"work"/path.name)
    original={path.relative_to(ROOT).as_posix():sha(path) for path in [ROOT/"artifacts/model_manifest.json",ROOT/"artifacts/model_freeze.json",ROOT/"artifacts/v4/model_manifest.json",ROOT/"artifacts/v4/model_freeze.json"] if path.exists()}
    environment=dict(os.environ,PYTHONHASHSEED="42",OMP_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1",MKL_NUM_THREADS="1")
    for script in ["fetch_dataset.py","train.py","train_challenger.py","train_domain.py","train_v4.py"]:
        subprocess.run([sys.executable,str(target/"scripts"/script)],cwd=target,env=environment,check=True)
    expected=json.loads((ROOT/"artifacts/v4/training_report.json").read_text())
    actual=json.loads((target/"artifacts/v4/training_report.json").read_text())
    parameters=["architecture","C","source_strength","blend_weight","calibration","threshold"]
    checks={"selection_parameters_reproduced":all(expected["selected"][key]==actual["selected"][key] for key in parameters),
            "temperature_within_1e_4":abs(expected["selected"]["temperature"]-actual["selected"]["temperature"])<1e-4,
            "source_macro_f1_reproduced":expected["metrics"]["source_test"]["macro_f1"]==actual["metrics"]["source_test"]["macro_f1"],
            "family_test_macro_f1_reproduced":expected["metrics"]["domain_test"]["macro_f1"]==actual["metrics"]["domain_test"]["macro_f1"],
            "serving_manifests_unchanged":all(sha(ROOT/path)==digest for path,digest in original.items())}
    output=ROOT/"artifacts/rebuild-runs"/run_id;output.mkdir(parents=True)
    report={"command":"python scripts/train_all.py --rebuild","run_id":run_id,"checks":checks,
            "scope":"Complete source-baseline, v2, v3, v4 pipeline in isolated work copy. Frozen development fixtures, no blind audit fitting/selection, no database calls. Numeric reproduction, not byte-identical pickle serialization.",
            "selected":actual["selected"],"source_test_macro_f1":actual["metrics"]["source_test"]["macro_f1"],
            "family_test_macro_f1":actual["metrics"]["domain_test"]["macro_f1"],"trained_artifact":json.loads((target/"artifacts/v4/model_freeze.json").read_text()),
            "unchanged_serving_manifest_hashes":original}
    (output/"report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"rebuild_run":run_id,"checks":checks}),flush=True)
    if not all(checks.values()):raise SystemExit("Rebuild differed from frozen selection; inspect the comparison rather than silently promoting it")


if __name__=="__main__":main()
