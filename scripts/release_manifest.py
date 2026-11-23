"""Bind final release claims to actual files; fail on stale or failed evidence."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
def read(name):
    return json.loads((ROOT/name).read_text(encoding="utf-8"))
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def evidence(name):
    return {"path":name,"sha256":sha(ROOT/name)}

tests = ET.parse(ROOT/"artifacts/checks/production-acceptance.xml").getroot().find("testsuite").attrib
assert int(tests["tests"]) == 95 and int(tests["errors"]) == int(tests["failures"]) == 0
browser = read("artifacts/browser/browser-results.json")
oidc = read("artifacts/security/oidc-browser-results.json")
ui_races = read("artifacts/browser/ui-race-results.json")
accessibility = read("artifacts/accessibility/axe-results.json")
assert browser["passed"] == 9 and not browser["errors"] and browser["source_stable"]
assert oidc["passed"] == oidc["total"] == 9 and oidc["source_stable"]
assert ui_races["passed"] == ui_races["total"] == 10 and ui_races["source_stable"]
assert ui_races["app_sha256"] == sha(ROOT/"web/app.js")
assert len(accessibility["scans"]) == 9 and not any(s["violations"] for s in accessibility["scans"])
for report in (browser,oidc):
    assert all(sha(ROOT/name)==digest for name,digest in report["source_hashes"].items()), "Stale browser evidence"
model = read("artifacts/model_manifest.json")
assert sha(ROOT/model["artifact_uri"]) == model["artifact_sha256"]
code = hashlib.sha256()
for file in sorted((ROOT/"src/resolvelab").glob("*.py")):
    code.update(file.name.encode());code.update(file.read_bytes())
python_digest = code.hexdigest()
reports = {}
for label, folder in {
    "deterministic":"artifacts/hardening-final-invariants",
    "v4_final":"artifacts/v4/blind-final",
    "v1_paired":"artifacts/v4/paired-baseline",
    "v3_paired":"artifacts/v4/paired-v3",
}.items():
    name=folder+"/workflow-evaluation.json"
    data=read(name)
    assert data["code_tree_sha256"] == python_digest and not data["code_changed_during_run"]
    assert data["metrics"]["runner_errors"] == data["metrics"]["unauthorized_effect_count"] == 0
    reports[label]={**evidence(name),"metrics":data["metrics"],"routing_metrics":data.get("routing_metrics"),"model_version":data["model_version"],"dataset_sha256":data["dataset_sha256"]}
assert reports["v4_final"]["metrics"]["workflow_success"]["numerator"] == 139
audit=read("artifacts/security/audit-summary.json")
assert audit["osv"]["unique_advisories"] == audit["npm"]["reported_vulnerabilities"] == 0
assert not read("artifacts/security/source-exposure-check.json")["findings"]
files=[*ROOT.glob("src/resolvelab/*.py"),*ROOT.glob("web/*.*"),*ROOT.glob("migrations/versions/*.py")]
manifest={
    "release":"v0.1.0",
    "generated_at":datetime.now(timezone.utc).isoformat(),
    "environment":{"python":platform.python_version(),"platform":platform.platform()},
    "python_source_tree_sha256":python_digest,
    "application_file_sha256":{p.relative_to(ROOT).as_posix():sha(p) for p in sorted(files)},
    "locks":{name:sha(ROOT/name) for name in ("uv.lock","requirements.lock","package-lock.json")},
    "model":{"version":model["version"],"sha256":model["artifact_sha256"],"threshold":model["threshold"]},
    "tests":tests,
    "browser":browser,
    "oidc":oidc,
    "ui_races":ui_races,
    "accessibility":{"states":len(accessibility["scans"]),"violations":0,"artifact":evidence("artifacts/accessibility/axe-results.json")},
    "evaluations":reports,
    "backup_restore":read("artifacts/backup/restore-report.json"),
    "model_rollback":read("artifacts/rollback/verification.json"),
    "live_worker":read("artifacts/live_worker/verification.json"),
    "dependency_audit":audit,
    "evidence_files":[evidence(name) for name in [
        "artifacts/checks/clean-install.json",
        "artifacts/security/production-hardening.json",
        "artifacts/security/compose-schema-validation.json",
        "artifacts/security/source-exposure-check.json",
        "artifacts/v4/final-analysis/analysis.json",
        "artifacts/rebuild-runs/390355c7da28/report.json",
        "artifacts/retrieval-v2/report.json",
    ]],
    "limits":[
        "Synthetic agent-authored cases, not real-customer accuracy or independent human validation.",
        "V4 retained21/160 workflow failures,4/104 wrong accepted routes and1/40 unsupported false acceptances.",
        "Zero observed unauthorized effects is a bounded test result, not a reliability guarantee.",
        "Mocked HTTPS IdP with real signatures; no real provider account/callback integration.",
        "Automated accessibility scans and limited keyboard/zoom checks do not establish complete WCAG conformance.",
        "No hosted backend, executed production containers, paid-provider benchmark, merchant/payment/email effects or cloud backup schedule.",
        "GitHub previously prevented acceptance runners from starting because of an account restriction; inspect Actions for subsequent status.",
    ],
}
(ROOT/"artifacts/release_manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"release":manifest["release"],"tests":tests["tests"],"browser":browser["passed"],"oidc":oidc["passed"],"accessibility_states":len(accessibility["scans"]),"python_source_tree_sha256":python_digest},indent=2))
