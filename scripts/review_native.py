"""Run the complete native suite and exposure check, binding proof to stable inputs."""
from datetime import datetime, timezone
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import tomllib

from review_release import ROOT, OUT, REQUIRED_TESTS, backend_digest, junit_counts, require, sha, source_snapshot, write


def main():
    require(platform.system() == "Windows", "This review runner records native Windows proof only")
    target = OUT / "native"
    target.mkdir(parents=True, exist_ok=True)
    # Retain superseded source-bound proof without presenting it as current.
    if (target / "verification.json").exists():
        history = OUT / "native-history" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        history.mkdir(parents=True, exist_ok=False)
        hashes = {}
        for path in sorted(target.iterdir()):
            if path.is_file():
                shutil.copy2(path, history / path.name)
                hashes[path.name] = sha(path)
        write(history / "history.json", {"status":"superseded_native_run_not_current", "files":hashes})
    before = source_snapshot()
    started = datetime.now(timezone.utc).isoformat()
    started_clock = time.monotonic()
    junit = target / "native-tests.xml"
    # An interrupted rerun must never leave an older successful verification.
    proof = target / "verification.json"
    if proof.exists():
        proof.unlink()
    if junit.exists():
        junit.unlink()
    command = [sys.executable, str(ROOT / "scripts/run_tests.py"), "--junitxml", str(junit)]
    with (target / "native-tests.log").open("w", encoding="utf-8") as log:
        test = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    exposure = target / "source-exposure-check.json"
    exposure_command = [sys.executable, str(ROOT / "scripts/check_source_exposure.py"), "--output", str(exposure)]
    with (target / "source-exposure.log").open("w", encoding="utf-8") as log:
        scanned = subprocess.run(exposure_command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    after = source_snapshot()
    result = {
        "schema_version":"1.0", "started_at":started,
        "completed_at":datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds":round(time.monotonic() - started_clock, 3),
        "version":tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
        "environment":{"python":platform.python_version(),"platform":platform.platform()},
        "command":command, "exit_code":test.returncode,
        "source_exposure_command":exposure_command,"source_exposure_exit_code":scanned.returncode,
        "python_source_tree_sha256":backend_digest(),
        "source_hashes_before":before,"source_hashes_after":after,"source_stable":before == after,
        "junit_sha256":sha(junit) if junit.is_file() else None,
        "source_exposure_sha256":sha(exposure) if exposure.is_file() else None,
        "scope":f"Full native Windows suite; all {REQUIRED_TESTS} cases required, no skips. Existing source/worker database is not reset; run_tests.py enforces the dedicated disposable test database.",
    }
    write(proof, result)
    require(test.returncode == 0 and scanned.returncode == 0, "Native suite or exposure check failed; inspect versioned native logs")
    require(before == after, "Inputs changed during native verification; rerun after edits finish")
    counts = junit_counts(junit)
    print({"tests":counts,"source_stable":True,"proof":str(proof)})


if __name__ == "__main__":
    main()
