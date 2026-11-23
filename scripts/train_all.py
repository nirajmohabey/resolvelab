"""One reproducible build command for all immutable routing comparisons."""
import os
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--rebuild",action="store_true",help="Fit all model stages again; default validates the shipped frozen artifacts")
    args=parser.parse_args()
    environment=dict(os.environ, PYTHONHASHSEED="42", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    # Development family data is already frozen in the checkout. It is not
    # regenerated or edited in response to any final evaluation.
    required=ROOT/"fixtures/domain_development_v1.jsonl"
    if not required.exists():
        raise SystemExit("Frozen domain development fixture is missing; restore it from version control.")
    if args.rebuild:
        subprocess.run([sys.executable,str(ROOT/"scripts/rebuild_models.py")],cwd=ROOT,env=environment,check=True)
        return
    if not args.rebuild and (ROOT/"artifacts/model_manifest.json").exists():
        manifest=json.loads((ROOT/"artifacts/model_manifest.json").read_text())
        artifact=ROOT/manifest["artifact_uri"]
        if not artifact.exists() or hashlib.sha256(artifact.read_bytes()).hexdigest()!=manifest["artifact_sha256"]:
            raise SystemExit("Frozen router artifact missing or corrupt. Restore the checkout or explicitly --rebuild; never silently replace a historical artifact.")
        frozen=json.loads((ROOT/"artifacts/model_freeze.json").read_text())
        if frozen["artifact_sha256"]!=manifest["artifact_sha256"]:
            raise SystemExit("Model manifest/freeze disagree")
        subprocess.run([sys.executable,str(ROOT/"scripts/fetch_dataset.py")],cwd=ROOT,env=environment,check=True)
        print("Verified shipped frozen model. Use --rebuild to reproduce every training stage without using final audit data.")
        return
    for script in ["fetch_dataset.py","train.py","train_challenger.py","train_domain.py","benchmark_routing.py","plot_model.py","plot_domain.py"]:
        subprocess.run([sys.executable,str(ROOT/"scripts"/script)],cwd=ROOT,env=environment,check=True)
    print("Training complete. Active artifact is pinned in artifacts/model_manifest.json; run seed to register it.")


if __name__=="__main__":main()
