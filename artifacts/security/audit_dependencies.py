"""Query exact locked package versions against OSV and the official npm registry."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
if not (ROOT / "uv.lock").exists():
    ROOT = ROOT / "outputs" / "resolvelab"
OUTPUT = ROOT / "artifacts" / "security"
OUTPUT.mkdir(parents=True, exist_ok=True)


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request_json(url, payload=None):
    request = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                     headers={"Content-Type": "application/json", "User-Agent": "ResolveLab-security-audit/1.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def main():
    generated = datetime.now(timezone.utc).isoformat()
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = []
    for item in lock["package"]:
        if "registry" not in item.get("source", {}):
            continue
        packages.append({"name": canonical(item["name"]), "version": item["version"], "ecosystem": "PyPI"})
    npm_lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    for path, item in npm_lock["packages"].items():
        if path:
            packages.append({"name": path.split("node_modules/")[-1], "version": item["version"], "ecosystem": "npm"})
    packages.sort(key=lambda row: (row["ecosystem"], row["name"], row["version"]))
    raw_results = []
    for start in range(0, len(packages), 100):
        queries = [{"package": {"name": row["name"], "ecosystem": row["ecosystem"]}, "version": row["version"]} for row in packages[start:start+100]]
        batch = request_json("https://api.osv.dev/v1/querybatch", {"queries": queries})
        if len(batch.get("results", [])) != len(queries):
            raise RuntimeError("OSV did not return a result for every exact locked-version query")
        raw_results.extend(batch["results"])
    findings = []
    ids = set()
    for package, result in zip(packages, raw_results):
        vulns = list(result.get("vulns", []))
        next_page = result.get("next_page_token")
        while next_page:
            page = request_json("https://api.osv.dev/v1/query", {"package": {"name": package["name"], "ecosystem": package["ecosystem"]},
                                                              "version": package["version"], "page_token": next_page})
            vulns.extend(page.get("vulns", []))
            next_page = page.get("next_page_token")
        if vulns:
            findings.append({"package": package, "advisory_ids": [v["id"] for v in vulns]})
            ids.update(v["id"] for v in vulns)
    advisories = {identifier: request_json("https://api.osv.dev/v1/vulns/" + urllib.parse.quote(identifier, safe="")) for identifier in sorted(ids)}
    osv_report = {"generated_at": generated, "source": "https://api.osv.dev/v1/querybatch", "packages": packages,
                  "queried_package_versions": len(packages), "findings": findings, "advisories": advisories, "raw_results": raw_results}
    (OUTPUT / "osv-audit.json").write_text(json.dumps(osv_report, indent=2) + "\n", encoding="utf-8")
    npm_cmd = shutil.which("npm.cmd") or shutil.which("npm")
    npm = subprocess.run([npm_cmd, "audit", "--json", "--package-lock-only", "--registry=https://registry.npmjs.org"],
                         cwd=ROOT, capture_output=True, text=True, timeout=60)
    npm_report = json.loads(npm.stdout)
    (OUTPUT / "npm-audit.json").write_text(json.dumps(npm_report, indent=2) + "\n", encoding="utf-8")
    npm_findings = npm_report.get("metadata", {}).get("vulnerabilities", {}).get("total")
    summary = {"generated_at": generated, "commands": ["python artifacts/security/audit_dependencies.py", "npm audit --json --package-lock-only --registry=https://registry.npmjs.org"],
               "inputs": {name: {"sha256": sha(ROOT / name), "bytes": (ROOT / name).stat().st_size} for name in ("uv.lock", "requirements.lock", "package-lock.json")},
               "osv": {"queried_python_versions": sum(p["ecosystem"] == "PyPI" for p in packages), "queried_npm_versions": sum(p["ecosystem"] == "npm" for p in packages),
                       "packages_with_advisories": len(findings), "unique_advisories": len(ids)},
               "npm": {"exit_code": npm.returncode, "reported_vulnerabilities": npm_findings, "registry": "https://registry.npmjs.org"},
               "limitations": ["This checks known advisories for exact application dependency versions as of the query time; it is not proof of security.",
                               "The OSV query includes all resolved PyPI package versions in uv.lock, including platform-specific and development dependencies, plus the npm lock graph.",
                               "This does not scan a built container's operating-system packages, deployment configuration, application authorization, or pretrained model contents.",
                               "No dependency or advisory was automatically suppressed or upgraded by the audit."]}
    (OUTPUT / "audit-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"osv": summary["osv"], "npm": summary["npm"], "finding_ids": sorted(ids)}, indent=2))
    return 1 if findings or npm_findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
