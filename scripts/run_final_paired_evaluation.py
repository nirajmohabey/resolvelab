"""Refresh exact-source final reports; preserves pre-intake-binding evidence.

Runs existing frozen weights/thresholds on the same frozen cases. No training,
label editing, threshold selection, or serving-database mutation occurs.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    base=ROOT/"artifacts/v4"
    archive=base/"before-intake-binding";archive.mkdir(exist_ok=True)
    for name in ["blind-final","paired-v3","paired-baseline","baseline-fixture-final"]:
        if (base/name).exists() and not (archive/name).exists():shutil.copytree(base/name,archive/name)
    runs=[("blind-final","v4-blind-v1","84f8a907-04de-5257-af61-98f317392d01",139,"domain-router-v4"),
          ("paired-v3","v4-blind-v1","70ca5649-301c-540a-a93d-abfcf24703e3",95,"domain-hybrid-v3"),
          ("paired-baseline","v4-blind-v1","b6ea31ed-03ed-516c-9e72-f83b7ee4c8fa",76,"tfidf-logistic-v1"),
          ("baseline-fixture-final","fixture-v1","b6ea31ed-03ed-516c-9e72-f83b7ee4c8fa",19,"tfidf-logistic-v1")]
    checks=[]
    for folder,dataset,release,expected_count,version in runs:
        command=[sys.executable,str(ROOT/"scripts/evaluate.py"),"--mode","live_model","--dataset",dataset,"--release-id",release,"--output",str(base/folder)]
        completed=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
        if completed.returncode not in [0,1]:raise RuntimeError(completed.stderr)
        report=json.loads((base/folder/"workflow-evaluation.json").read_text())
        assert report["metrics"]["workflow_success"]["numerator"]==expected_count,"Frozen behavior changed; inspect rather than tuning"
        assert report["model_version"]==version and report["release_id"]==release
        assert not report["code_changed_during_run"] and report["release_provenance"]["artifact_hash_matches_database"]
        assert report["metrics"]["runner_errors"]==0 and report["metrics"]["unauthorized_effect_count"]==0
        checks.append({"folder":folder,"model_version":version,"workflow":report["metrics"]["workflow_success"],"code_tree_sha256":report["code_tree_sha256"],"artifact_sha256":report["model_artifact_sha256"]})
        print(json.dumps(checks[-1]),flush=True)
    assert len({row["code_tree_sha256"] for row in checks})==1,"Paired final source trees differ"
    (base/"final_source_verification.json").write_text(json.dumps({"runs":checks,"same_source_tree_for_all_runs":True,"weights_and_thresholds_unchanged":True},indent=2)+"\n",encoding="utf-8")
    subprocess.run([sys.executable,str(ROOT/"scripts/analyze_blind_v4.py")],cwd=ROOT,check=True)


if __name__=="__main__":main()
