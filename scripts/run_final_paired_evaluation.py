"""Refresh exact-source final reports; preserve the pre-v0.2 evidence.

Runs existing frozen weights/thresholds on the same frozen cases. No training,
label editing, threshold selection, or serving-database mutation occurs.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    base=ROOT/"artifacts/v4"
    archive=base/"before-v0.2-hardening";archive.mkdir(exist_ok=True)
    for name in ["blind-final","paired-v3","paired-baseline","baseline-fixture-final","final-analysis"]:
        if (base/name).exists() and not (archive/name).exists():shutil.copytree(base/name,archive/name)
    for name in ["final_source_verification.json","model_manifest.json","model_freeze.json"]:
        if (base/name).exists() and not (archive/name).exists():shutil.copy2(base/name,archive/name)
    invariants=ROOT/"artifacts/hardening-final-invariants"
    if invariants.exists() and not (archive/invariants.name).exists():shutil.copytree(invariants,archive/invariants.name)
    archive_index=archive/"archive_manifest.json"
    if not archive_index.exists():
        files={str(path.relative_to(archive)).replace("\\","/"):hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted(archive.rglob("*")) if path.is_file()}
        archive_index.write_text(json.dumps({"archived_at":datetime.now(timezone.utc).isoformat(),
            "reason":"Preserve the v0.1 reports before source-only v0.2 hardening verification; no model tuning.",
            "files_sha256":files},indent=2)+"\n",encoding="utf-8")
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
        assert report["metrics"]["all_tenant_effect_checks_completed"]==report["metrics"]["workflow_success"]["denominator"]
        assert report["all_observed_case_releases_match_selected"]
        checks.append({"folder":folder,"model_version":version,"workflow":report["metrics"]["workflow_success"],"code_tree_sha256":report["code_tree_sha256"],"artifact_sha256":report["model_artifact_sha256"],
                       "report_sha256":hashlib.sha256((base/folder/"workflow-evaluation.json").read_bytes()).hexdigest(),
                       "dataset_sha256":report["dataset_sha256"],"release_id":report["release_id"],
                       "unauthorized_effect_count":report["metrics"]["unauthorized_effect_count"],
                       "effect_audits_completed":report["metrics"]["all_tenant_effect_checks_completed"],
                       "runner_errors":report["metrics"]["runner_errors"]})
        print(json.dumps(checks[-1]),flush=True)
    assert len({row["code_tree_sha256"] for row in checks})==1,"Paired final source trees differ"
    (base/"final_source_verification.json").write_text(json.dumps({"runs":checks,"same_source_tree_for_all_runs":True,"weights_and_thresholds_unchanged":True,
        "previous_reports_archive":str(archive.relative_to(ROOT)).replace("\\","/"),
        "scope":"Source-only hardening verification on the same frozen synthetic cases; not new independent model validation."},indent=2)+"\n",encoding="utf-8")
    subprocess.run([sys.executable,str(ROOT/"scripts/analyze_blind_v4.py")],cwd=ROOT,check=True)
    command=[sys.executable,str(ROOT/"scripts/evaluate.py"),"--mode","deterministic_invariants","--dataset","fixture-v1",
             "--release-id",runs[0][2],"--output",str(invariants)]
    completed=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
    if completed.returncode:raise RuntimeError(completed.stderr or completed.stdout)
    report=json.loads((invariants/"workflow-evaluation.json").read_text())
    assert report["metrics"]["workflow_success"]=={"numerator":20,"denominator":20,"rate":1.0}
    assert report["metrics"]["unauthorized_effect_count"]==report["metrics"]["runner_errors"]==0
    assert report["metrics"]["all_tenant_effect_checks_completed"]==20
    assert not report["code_changed_during_run"] and report["code_tree_sha256"]==checks[0]["code_tree_sha256"]
    verification=json.loads((base/"final_source_verification.json").read_text())
    verification["deterministic_invariants"]={"source_report":str(invariants.relative_to(ROOT)).replace("\\","/"),
        "workflow":report["metrics"]["workflow_success"],"code_tree_sha256":report["code_tree_sha256"],
        "report_sha256":hashlib.sha256((invariants/"workflow-evaluation.json").read_bytes()).hexdigest(),
        "unauthorized_effect_count":0,"runner_errors":0,"effect_audits_completed":20}
    source_files={};source_digest=hashlib.sha256()
    for path in sorted((ROOT/"src/resolvelab").glob("*.py")):
        payload=path.read_bytes();source_files[path.name]=hashlib.sha256(payload).hexdigest()
        source_digest.update(path.name.encode());source_digest.update(payload)
    assert source_digest.hexdigest()==checks[0]["code_tree_sha256"],"Source changed after final verification"
    verification["source_files_sha256"]=source_files
    verification["migration_files_sha256"]={str(path.relative_to(ROOT)).replace("\\","/"):hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT/"migrations/versions").glob("*.py"))}
    for index_name in ["archive_manifest.json","additional-invariants-docs-manifest.json"]:
        if (archive/index_name).exists():
            index=json.loads((archive/index_name).read_text())
            assert all(hashlib.sha256((archive/name).read_bytes()).hexdigest()==value for name,value in index["files_sha256"].items()),"Archived evidence changed"
    verification["archive_hashes_verified"]=True
    (base/"final_source_verification.json").write_text(json.dumps(verification,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(verification["deterministic_invariants"]),flush=True)


if __name__=="__main__":main()
