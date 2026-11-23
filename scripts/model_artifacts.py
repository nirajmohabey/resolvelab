"""Keep release-pinned model files immutable across reproducibility runs."""
import hashlib
from pathlib import Path
import joblib


def save_immutable(bundle, preferred_path: Path) -> Path:
    temporary=preferred_path.with_name(preferred_path.stem+".building.joblib")
    joblib.dump(bundle,temporary,compress=3)
    digest=hashlib.sha256(temporary.read_bytes()).hexdigest()
    target=preferred_path
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest()==digest:
            temporary.unlink()
            return target
        target=preferred_path.with_name(preferred_path.stem+"-"+digest[:12]+preferred_path.suffix)
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
            raise RuntimeError("Content-addressed artifact collision")
        temporary.unlink()
    else:
        temporary.replace(target)
    return target
