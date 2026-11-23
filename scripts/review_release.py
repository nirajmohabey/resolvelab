"""Collect source-bound v0.2.1 review evidence without reusing historical passes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
REVIEW = "recheck-2026-11-23"
OUT = ROOT / "artifacts" / REVIEW
WORK = ROOT.parents[1] / "work" / "recheck-resolvelab"
REQUIRED_TESTS = 173
FRONTEND = ("web/app.js", "web/app.css", "web/index.html", "web/auth-client-src.js", "web/auth-client.js")
EVALUATIONS = ("v4-blind-final", "v3-paired", "v1-paired", "v1-fixture", "deterministic-invariants")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def backend_digest():
    result = hashlib.sha256()
    for path in sorted((ROOT / "src/resolvelab").glob("*.py")):
        result.update(path.name.encode())
        result.update(path.read_bytes())
    return result.hexdigest()


def source_snapshot():
    """Application, tests, verification tools, configuration and immutable inputs."""
    paths = set()
    for folder in ("src", "web", "tests", "scripts", "migrations", "contracts", "fixtures", "infra", ".github", "artifacts/embeddings"):
        paths.update(p for p in (ROOT / folder).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    for pattern in ("artifacts/router*.joblib", "artifacts/model_manifest*.json", "artifacts/model_freeze*.json", "artifacts/v4/model_*.json"):
        paths.update(ROOT.glob(pattern))
    paths.add(ROOT / "artifacts/v4/source_membership-v2.jsonl")
    paths.add(ROOT / "artifacts/security/audit_dependencies.py")
    for path in ROOT.iterdir():
        if path.is_file() and not path.name.startswith(".env") and (
            path.suffix in {".toml", ".json", ".lock", ".ini", ".yml", ".yaml", ".ps1"}
            or path.name in {"Dockerfile", ".dockerignore", ".gitignore", ".gitattributes"}
        ):
            paths.add(path)
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(paths)}


def current_hashes(mapping, label, required=()):
    require(isinstance(mapping, dict) and bool(mapping), f"{label}: source hashes missing")
    require(set(required) <= set(mapping), f"{label}: required source hashes missing")
    for name, expected in mapping.items():
        path = (ROOT / name).resolve()
        require(path.is_relative_to(ROOT) and path.is_file(), f"{label}: invalid input path {name}")
        require(sha(path) == expected, f"{label}: stale input {name}")


def junit_counts(path):
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall(".//testsuite"))
    require(bool(suites), "Native JUnit has no suites")
    counts = {key: sum(int(s.attrib.get(key, 0)) for s in suites)
              for key in ("tests", "failures", "errors", "skipped")}
    cases = root.findall(".//testcase")
    require(counts == {"tests": REQUIRED_TESTS, "failures": 0, "errors": 0, "skipped": 0},
            f"Full native suite must pass all {REQUIRED_TESTS} cases without skips: {counts}")
    require(len(cases) == REQUIRED_TESTS, "JUnit test-case count does not match suite")
    require(not any(c.find(tag) is not None for c in cases for tag in ("failure", "error", "skipped")),
            "JUnit contains a failed or skipped case")
    return counts


def evidence(path):
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path), "bytes": path.stat().st_size}


def successful_browser(report, label, count, required=FRONTEND):
    require(report.get("passed") == report.get("total") == count, f"{label}: incomplete checks")
    require(report.get("source_stable") is True, f"{label}: source stability missing")
    require(not report.get("errors") and not report.get("failures"), f"{label}: reported failures")
    current_hashes(report.get("source_hashes"), label, required)
    if "initial_source_hashes" in report:
        require(report["initial_source_hashes"] == report["source_hashes"], f"{label}: sources changed during run")
    results = report.get("checks", report.get("results", []))
    require(len(results) == count and all(r.get("passed") is True and not r.get("page_errors") for r in results),
            f"{label}: individual check failure")


def build(work, out, ui=None):
    ui = ui or work
    require(out.resolve() == OUT, "This versioned review writes only artifacts/" + REVIEW)
    native = read(out / "native/verification.json")
    require(native.get("exit_code") == native.get("source_exposure_exit_code") == 0, "Native or exposure command failed")
    require(native.get("source_stable") is True, "Native source changed during run")
    snapshot = source_snapshot()
    require(native.get("source_hashes_before") == native.get("source_hashes_after") == snapshot,
            "Native proof is stale; rerun scripts/review_native.py after source edits")
    require(native.get("python_source_tree_sha256") == backend_digest(), "Native backend hash mismatch")
    require(native.get("version") == "0.2.1", "Native proof version mismatch")
    tests = junit_counts(out / "native/native-tests.xml")
    require(native.get("junit_sha256") == sha(out / "native/native-tests.xml"), "Native JUnit changed after run")
    exposure = read(out / "native/source-exposure-check.json")
    require(exposure.get("findings") == [] and exposure.get("text_files_scanned", 0) > 0, "Source exposure findings")
    require(native.get("source_exposure_sha256") == sha(out / "native/source-exposure-check.json"), "Source exposure proof changed")

    audit = read(work / "dependencies/audit-summary.json")
    osv = read(work / "dependencies/osv-audit.json")
    npm = read(work / "dependencies/npm-audit.json")
    require(set(audit["inputs"]) == {"uv.lock", "requirements.lock", "package-lock.json"}, "Audit lock inputs missing")
    current_hashes({name:entry["sha256"] for name, entry in audit["inputs"].items()}, "dependency audit locks")
    locked = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    expected_packages = [{"name":re.sub(r"[-_.]+", "-", item["name"]).lower(), "version":item["version"], "ecosystem":"PyPI"}
                         for item in locked["package"] if "registry" in item.get("source", {})]
    npm_lock = read(ROOT / "package-lock.json")
    expected_packages.extend({"name":name.split("node_modules/")[-1], "version":item["version"], "ecosystem":"npm"}
                             for name, item in npm_lock["packages"].items() if name)
    expected_packages.sort(key=lambda row:(row["ecosystem"],row["name"],row["version"]))
    require(osv["packages"] == expected_packages and osv["queried_package_versions"] == len(expected_packages), "Dependency query list does not cover current lock graph")
    require(len(osv["raw_results"]) == len(expected_packages) and all(not row.get("vulns") and not row.get("next_page_token") for row in osv["raw_results"]), "Incomplete or nonzero OSV result")
    require(osv["findings"] == [] and osv["advisories"] == {}, "OSV advisories present")
    require(audit["osv"]["packages_with_advisories"] == audit["osv"]["unique_advisories"] == 0, "Dependency summary findings")
    require(audit["osv"]["queried_python_versions"] == sum(p["ecosystem"] == "PyPI" for p in expected_packages) and audit["osv"]["queried_npm_versions"] == sum(p["ecosystem"] == "npm" for p in expected_packages), "Dependency summary count mismatch")
    require(audit["npm"]["exit_code"] == audit["npm"]["reported_vulnerabilities"] == npm["metadata"]["vulnerabilities"]["total"] == 0 and npm["vulnerabilities"] == {}, "npm audit error or vulnerability")
    require(audit["npm"]["registry"] == "https://registry.npmjs.org", "Unrecognized npm audit registry")

    # Validate fresh inputs in place before copying; no historical report is modified.
    browser_specs = {
        "live/browser/browser-results.json": 11,
        "live/security/oidc-browser-results.json": 9,
        "isolated-ui-css-final/ui-race-results.json": 10,
        "isolated-ui-css-final/workbench-feature-results.json": 8,
        "isolated-ui-css-final/operator-followup-results.json": 5,
        "isolated-ui-css-final/auth-callback-failure-results.json": 3,
        "action-races-css-final/fresh-ui-review-results.json": 8,
    }
    browser_reports = {}
    for name, count in browser_specs.items():
        report = read(ui / name)
        required = ("web/app.js", "web/auth-client-src.js", "web/auth-client.js", "package-lock.json") if "oidc-browser" in name else FRONTEND
        successful_browser(report, name, count, required)
        browser_reports[name] = report
    responsive = read(ui / "live/responsive/matrix-results.json")
    require(responsive.get("source_stable") is True and not responsive.get("errors"), "Responsive source/errors")
    require(len(responsive["checks"]) == 24 and all(c.get("passed") is True for c in responsive["checks"]), "Responsive matrix incomplete")
    require(responsive.get("app_sha256") == sha(ROOT / "web/app.js") and responsive.get("css_sha256") == sha(ROOT / "web/app.css"), "Stale responsive proof")
    require(responsive.get("case_mutations") == 0, "Responsive check mutated cases")
    axe = read(ui / "live/accessibility/axe-results.json")
    require(len(axe["scans"]) == 9 and all(s.get("violations") == [] for s in axe["scans"]), "Accessibility violation/incomplete scan")
    require(len(axe.get("keyboard_checks", [])) >= 3, "Keyboard checks missing")
    viewport = read(ui / "live/browser/viewport-results.json")
    require(viewport.get("passed") is True and viewport.get("read_only") is True and viewport.get("case_mutations") == 0, "Viewport failure")
    freeze = read(ui / "live/source-freeze.json")
    current_hashes(freeze["source_hashes"], "live/source-freeze", FRONTEND)
    require(freeze.get("api_version") == "0.2.1", "Live API version mismatch")
    postchecks = read(ui / "live/postchecks.json")
    require(postchecks.get("source_stable") is True, "Live postcheck source mutation")
    current_hashes(postchecks["after_source_hashes"], "live/postchecks", FRONTEND)
    require(postchecks["before_source_hashes"] == postchecks["after_source_hashes"], "Live pre/post hashes differ")
    require(all(postchecks["after_source_hashes"].get(name) == digest for name, digest in freeze["source_hashes"].items()), "Live source-freeze and postcheck hashes differ")
    for name in ("live/browser/browser-results.json", "live/accessibility/axe-results.json", "live/browser/viewport-results.json"):
        require(postchecks["reports"].get(name) == sha(ui / name), "Live postcheck missing/changed report " + name)
    isolation = read(ui / "live/owned-live-isolation.json")
    require(isolation.get("source_reset") is False and isolation.get("user_process_records_written") is False,
            "Live proof did not preserve existing runtime")
    require(isolation.get("phase") == "completed" and isolation.get("owned_api_stopped") is True and isolation.get("owned_database_removed") is True, "Owned live review did not complete cleanup")
    require(isolation.get("env_sha256_before") == isolation.get("env_sha256_after") and isolation.get("env_sha256_after"), "Existing environment changed during live review")
    require(isolation.get("user_process_record_hashes_before") == isolation.get("user_process_record_hashes_after") and isolation.get("user_process_record_hashes_after"), "Existing runtime records changed during live review")

    backup = read(work / "backup/restore-report.json")
    backup_proof = read(work / "backup/verification.json")
    require(all(backup.get(key) is True for key in ("destination_verified", "restored", "original_order_snapshot_verified", "synthetic_restore_probe_used", "tracking_verified", "approval_verified")), "New-copy backup restore did not complete")
    require(backup.get("source_database_written_by_check") is False, "Backup probe wrote source database")
    require(backup_proof.get("exit_code") == 0 and backup_proof.get("source_stable") is True and backup_proof.get("database_removed") is True and backup_proof.get("dump_removed") is True, "Backup source/cleanup verification failed")
    require(backup_proof.get("report_sha256") == sha(work / "backup/restore-report.json"), "Backup report changed after verification")
    current_hashes(backup_proof["source_hashes"], "backup proof", ("scripts/backup_check.py", "scripts/backup_guard.py", *[p.relative_to(ROOT).as_posix() for p in (ROOT / "src/resolvelab").glob("*.py")]))

    evaluation_verification = read(work / "evaluations/verification.json")
    input_hashes = read(work / "evaluations/input_hashes.json")
    current_hashes(input_hashes, "evaluation immutable inputs")
    require(evaluation_verification.get("changed_input_files") == [], "Evaluation input mutation")
    require(evaluation_verification.get("unchanged_input_files") == len(input_hashes), "Evaluation input count mismatch")
    require(evaluation_verification.get("source_tree_sha256") == backend_digest(), "Evaluation source mismatch")
    runs = {r["name"]: r for r in evaluation_verification["runs"]}
    require(set(runs) == set(EVALUATIONS), "Expected five frozen evaluation reruns")
    evaluations = {}
    for name in EVALUATIONS:
        path = work / "evaluations" / name / "workflow-evaluation.json"
        report = read(path)
        metrics = report["metrics"]
        require(report["code_tree_sha256"] == backend_digest() and report["code_changed_during_run"] is False, "Stale evaluation " + name)
        require(metrics["runner_errors"] == metrics["unauthorized_effect_count"] == 0, "Evaluation runner error/effect " + name)
        require(metrics["all_tenant_effect_checks_completed"] == metrics["workflow_success"]["denominator"] == len(report["cases"]), "Incomplete case effect audit " + name)
        require(report["release_provenance"]["artifact_hash_matches_database"] is True and report["all_observed_case_releases_match_selected"] is True, "Evaluation release provenance mismatch " + name)
        require(runs[name]["report_sha256"] == sha(path) and runs[name]["artifact_sha256"] == report["model_artifact_sha256"], "Evaluation summary hash mismatch " + name)
        require(report["model_artifact_sha256"] in input_hashes.values() and report["dataset_sha256"] in input_hashes.values(), "Unfrozen evaluation artifact/dataset " + name)
        evaluations[name] = {"metrics": metrics, "routing_metrics": report.get("routing_metrics"), "model_version": report["model_version"], "model_artifact_sha256": report["model_artifact_sha256"], "dataset_sha256": report["dataset_sha256"], "mode": report["mode"]}
    require(evaluations["v4-blind-final"]["metrics"]["workflow_success"] == {"numerator":139,"denominator":160,"rate":0.86875}, "Unexpected v4 frozen result; review before publishing")
    model = read(ROOT / "artifacts/model_manifest.json")
    require(sha(ROOT / model["artifact_uri"]) == model["artifact_sha256"], "Active frozen model hash mismatch")

    names = [*browser_specs, "live/responsive/matrix-results.json", "live/accessibility/axe-results.json", "live/browser/viewport-results.json", "live/source-freeze.json", "live/postchecks.json", "live/owned-live-isolation.json", "evaluations/verification.json", "evaluations/input_hashes.json", "backup/restore-report.json", "backup/verification.json", "dependencies/audit-summary.json", "dependencies/osv-audit.json", "dependencies/npm-audit.json"]
    names.extend("evaluations/" + name + "/workflow-evaluation." + suffix for name in EVALUATIONS for suffix in ("json", "html"))
    for name in names:
        source_base = work if name.startswith(("evaluations/", "backup/", "dependencies/")) else ui
        source, target = source_base / name, out / name
        digest = sha(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        require(sha(target) == digest == sha(source), "Evidence changed while copying " + name)
    write(out / "source-hashes.json", snapshot)
    # Scan the copied publishable evidence too; preserve the earlier frozen scan.
    final_scan = out / "source-exposure-check.json"
    scanned = subprocess.run([sys.executable, str(ROOT / "scripts/check_source_exposure.py"), "--output", str(final_scan)], cwd=ROOT, capture_output=True, text=True)
    require(scanned.returncode == 0 and read(final_scan).get("findings") == [], "Final source/evidence exposure scan failed")
    final_exposure = read(final_scan)
    historical_names = (
        "artifacts/release_manifest.json", "artifacts/checks/linux-acceptance.xml", "artifacts/checks/linux-environment.json",
        "artifacts/checks/linux-docker-smoke.json", "artifacts/checks/linux-serving-consistency.json",
        "artifacts/backup/linux-restore-report.json", "artifacts/backup/restore-report.json",
        "artifacts/security/audit-summary.json", "artifacts/checks/clean-install.json",
    )
    historical = [{**evidence(ROOT / name), "status": "historical_not_rechecked_for_v0.2.1"} for name in historical_names]
    historical.extend({**evidence(path), "status":"superseded_native_run_not_current"}
                      for path in sorted((out / "native-history").rglob("*")) if path.is_file())
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    require(version == "0.2.1", "This review manifest is versioned for v0.2.1")
    proof_paths = [out / name for name in names] + list((out / "native").glob("*")) + [out / "source-hashes.json", final_scan]
    manifest = {
        "schema_version": "1.0", "review_id": REVIEW, "release": "v" + version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head_at_collection": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "python_source_tree_sha256": backend_digest(), "application_file_sha256": {name: snapshot[name] for name in FRONTEND},
        "source_snapshot": evidence(out / "source-hashes.json"), "native_tests": tests,
        "source_exposure": {"findings":0,"text_files_scanned":final_exposure["text_files_scanned"]},
        "browser_checks": {name: count for name, count in browser_specs.items()},
        "browser_checks_total": sum(browser_specs.values()), "responsive_checks": 24,
        "accessibility": {"states":9,"violations":0,"keyboard_checks":axe["keyboard_checks"],"incomplete_findings":sum(len(s.get("incomplete",[])) for s in axe["scans"])},
        "evaluations": evaluations, "immutable_evaluation_inputs": len(input_hashes),
        "model": {key:model[key] for key in ("version", "artifact_uri", "artifact_sha256", "threshold")},
        "current_environment": native["environment"],
        "dependency_audit": {"status":"current_locked_versions_checked", "generated_at":audit["generated_at"], "osv":audit["osv"], "npm":audit["npm"], "limitations":audit["limitations"]},
        "backup_restore": {"status":"current_native_passed", "destination_verified":True, "tracking_verified":True, "approval_verified":True, "source_database_written_by_check":False, "database_removed":True, "dump_removed":True},
        "unavailable_checks": {
            "current_linux_suite": {"status":"not_run", "reason":"Free cloud execution quota exhausted; no configured WSL Linux distribution. No paid upgrade authorized."},
            "current_docker_startup": {"status":"not_rechecked", "reason":"Prior Linux container smoke is historical evidence only."},
            "hosted_backend": {"status":"not_deployed", "reason":"Review exercised a private native disposable service; no hosted backend deployment was performed."},
            "current_ci": {"status":"not_verified", "reason":"No successful current-source CI run is supplied by this local review."},
            "current_clean_install": {"status":"not_rechecked", "reason":"The existing native environment was used; no new dependency installation was performed."},
        },
        "historical_evidence": historical,
        "evidence_files": [evidence(path) for path in sorted(set(proof_paths)) if path.is_file()],
        "limitations": [
            "Current evidence is native Windows evidence; historical Linux, container and earlier backup reports do not establish v0.2.1 compatibility or freshness.",
            "Frozen synthetic cases were rerun for compatibility; they are not new independent generalization or real-customer accuracy measurements.",
            "V4 retains21/160 workflow failures; observed zero unauthorized effects is bounded evidence, not a guarantee.",
            "OIDC uses a mocked HTTPS provider and actual signatures; no real provider account or hosted callback was exercised.",
            "Automated axe scans plus limited keyboard, viewport and text-size checks do not establish complete WCAG conformance.",
        ],
    }
    write(out / "review-manifest-v0.2.1.json", manifest)
    validate(out / "review-manifest-v0.2.1.json")
    return manifest


def validate(path):
    manifest = read(path)
    require(manifest.get("schema_version") == "1.0" and manifest.get("release") == "v0.2.1", "Unknown review manifest")
    require(manifest["python_source_tree_sha256"] == backend_digest(), "Manifest backend is stale")
    for entry in [manifest["source_snapshot"], *manifest["evidence_files"], *manifest["historical_evidence"]]:
        current_hashes({entry["path"]:entry["sha256"]}, "manifest evidence")
    snapshot = read(ROOT / manifest["source_snapshot"]["path"])
    require(snapshot == source_snapshot(), "Manifest source snapshot is stale")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=WORK)
    parser.add_argument("--ui-work-dir", type=Path, help="Separate final browser proof directory; evaluation/backup/audit remain in --work-dir")
    parser.add_argument("--verify", action="store_true", help="Only verify the collected manifest against current files")
    args = parser.parse_args()
    manifest = validate(OUT / "review-manifest-v0.2.1.json") if args.verify else build(args.work_dir.resolve(), OUT, args.ui_work_dir.resolve() if args.ui_work_dir else None)
    print(json.dumps({"release":manifest["release"],"tests":manifest["native_tests"],"browser_checks":manifest["browser_checks_total"],"manifest":str(OUT / "review-manifest-v0.2.1.json"),"current_linux":"not_run"}, indent=2))


if __name__ == "__main__":
    main()
