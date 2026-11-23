"""Check publishable files for local secrets without printing their values."""
import json
from pathlib import Path
import re
import subprocess
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
paths = subprocess.check_output(
    ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
).decode().split("\0")
secrets = {}
envfile = ROOT / ".env"
if envfile.exists():
    for line in envfile.read_text().splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        if any(word in key for word in ("SECRET", "PASSWORD", "API_KEY")) and len(value) >= 16:
            secrets[key] = value
        if key.endswith("DATABASE_URL"):
            password = unquote(urlsplit(value).password or "")
            if len(password) >= 16:
                secrets[key + " password"] = password

binary_suffixes = {".onnx", ".joblib", ".png", ".jpg", ".pdf", ".zip", ".mp4", ".webm", ".ico"}
patterns = {
    "GitHub token": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})"),
    "AWS access key": re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "provider key": re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{40,}"),
}
findings = []
scanned = 0
for name in sorted(set(filter(None, paths))):
    path = ROOT / name
    if not path.is_file():
        continue
    parts = Path(name).parts
    if any(part in {".runtime", ".venv", "node_modules", ".git", "work"} for part in parts):
        findings.append({"path": name, "kind": "private runtime directory"})
    if any(part.startswith(".env") and part != ".env.example" for part in parts):
        findings.append({"path": name, "kind": "environment file"})
    if path.suffix.lower() in binary_suffixes:
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    scanned += 1
    for key, value in secrets.items():
        if value in text:
            findings.append({"path": name, "kind": "local secret", "key": key})
    for kind, pattern in patterns.items():
        if pattern.search(text):
            findings.append({"path": name, "kind": kind})

report = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "text_files_scanned": scanned,
    "local_secret_values_checked": len(secrets),
    "findings": findings,
    "limitations": [
        "Checks the current Git-index and unignored working files, not deleted history or external services.",
        "Known-prefix and exact-local-secret checks are not an exhaustive secret detector.",
        "Binary model and media contents are excluded; their origin and checksums are documented separately.",
    ],
}
output = ROOT / "artifacts/security/source-exposure-check.json"
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, indent=2))
raise SystemExit(bool(findings))
