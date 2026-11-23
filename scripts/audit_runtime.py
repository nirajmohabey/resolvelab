"""Query exact runtime lock versions against OSV; preserve older audit reports."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/production-v0.3/security/python-runtime-osv.json"


def request(path, value=None):
    body = json.dumps(value).encode() if value is not None else None
    for attempt in range(3):
        try:
            with urlopen(Request("https://api.osv.dev/v1/" + path, data=body,
                                 headers={"Content-Type":"application/json","User-Agent":"ResolveLab-runtime-audit/0.3.0"}),timeout=30) as response:
                return json.load(response)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt+1)


def main():
    path = ROOT / "requirements.lock"
    raw = path.read_bytes()
    digest = sha256(raw).hexdigest()
    packages = [{"name":re.sub(r"[-_.]+","-",name).lower(),"version":version}
                for name,version in re.findall(r"^([A-Za-z0-9_.-]+)==([^ ;\\]+)",raw.decode(),re.M)]
    if not packages or len({row["name"] for row in packages}) != len(packages):
        raise RuntimeError("Runtime lock is empty or has ambiguous package versions")
    queries = [{"package":{"ecosystem":"PyPI","name":row["name"]},"version":row["version"]} for row in packages]
    results = []
    for start in range(0,len(queries),100):
        batch = request("querybatch",{"queries":queries[start:start+100]})
        if len(batch.get("results",[])) != len(queries[start:start+100]):
            raise RuntimeError("OSV did not answer every runtime version query")
        results.extend(batch["results"])
    findings, ids = [], set()
    for package,query,result in zip(packages,queries,results):
        vulns = list(result.get("vulns",[]))
        page = result.get("next_page_token")
        while page:
            extra = request("query",query | {"page_token":page})
            vulns.extend(extra.get("vulns",[]))
            page = extra.get("next_page_token")
        if vulns:
            identifiers = sorted({row["id"] for row in vulns})
            findings.append(package | {"advisory_ids":identifiers})
            ids.update(identifiers)
    advisories = {value:request("vulns/"+quote(value,safe="")) for value in sorted(ids)}
    stable = path.read_bytes() == raw
    report = {"generated_at":datetime.now(timezone.utc).isoformat(),"source":"https://api.osv.dev/v1/querybatch",
              "lock":"requirements.lock","lock_sha256":digest,"source_stable":stable,
              "queried_package_versions":len(packages),"unique_matching_advisories":len(ids),
              "packages":packages,"raw_results":results,"findings":findings,"advisories":advisories,
              "passed":stable and not findings,
              "scope":"Exact production runtime lock versions, including conditional platform versions; no local unpublished project, development group, model contents or operating-system packages.",
              "limitations":["No matching known advisory is a time-bounded database result, not proof of security.",
                             "Live deployment configuration, authorization, hosted platform and operating-system vulnerabilities require separate evidence."]}
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({key:report[key] for key in ("passed","queried_package_versions","unique_matching_advisories","lock_sha256","source_stable")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
