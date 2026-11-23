"""Download immutable public training/model inputs; never downloads at request time."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DATASET_REVISION = "430d1a89bd93bd1fa23c16f29dd53e73f0087443"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
DATASET_FILE = "Bitext_Sample_Customer_Support_Training_Dataset_27K_responses-v11.csv"
EXPECTED_HASHES={
    DATASET_FILE:"6f81102b0100b97b8468eb04368033a23206bf1fde9d53500d5806ec1001a434",
    "tokenizer.json":"be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037",
    "model.onnx":"b941bf19f1f1283680f449fa6a7336bb5600bdcd5f84d10ddc5cd72218a0fd21",
    "config.json":"953f9c0d463486b10a6871cc2fd59f223b2c70184f49815e7efbcab5d8908b41"}


def download(url: str, destination: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        request = urllib.request.Request(url, headers={"User-Agent": "ResolveLab-reproducible-build/1.0"})
        with urllib.request.urlopen(request, timeout=180) as response:
            content = response.read()
        temporary = destination.with_suffix(destination.suffix + ".part")
        temporary.write_bytes(content)
        temporary.replace(destination)
    content = destination.read_bytes()
    expected=EXPECTED_HASHES.get(destination.name)
    if expected and hashlib.sha256(content).hexdigest()!=expected:
        raise RuntimeError(f"Checksum mismatch for frozen input {destination.name}; restore the verified artifact before continuing")
    return {"url": url, "path": str(destination.relative_to(ROOT)).replace("\\", "/"),
            "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}


def main() -> None:
    dataset_url = f"https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset/resolve/{DATASET_REVISION}"
    model_url = f"https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/{MODEL_REVISION}"
    dataset = download(f"{dataset_url}/{DATASET_FILE}", ROOT / "work/raw" / DATASET_FILE)
    card = download(f"{dataset_url}/README.md", ROOT / "work/raw/bitext_README.md")
    model_files = [download(f"{model_url}/{remote}", ROOT / "artifacts/embeddings" / local)
                   for remote, local in [("tokenizer.json", "tokenizer.json"),
                                         ("onnx/model_quint8_avx2.onnx", "model.onnx"),
                                         ("config.json", "config.json"),
                                         ("README.md", "README.md")]]
    manifest = {"dataset": "bitext/Bitext-customer-support-llm-chatbot-training-dataset",
                "dataset_revision": DATASET_REVISION, "dataset_license": "CDLA-Sharing-1.0",
                "source": dataset, "source_card": card,
                "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
                "embedding_revision": MODEL_REVISION, "embedding_dimension": 384,
                "embedding_license": "Apache-2.0", "embedding_files": model_files}
    target = ROOT / "artifacts/source_manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"source_rows": "inspect in train", "manifest": str(target),
                      "dataset_sha256": dataset["sha256"], "embedding_files": len(model_files)}))


if __name__ == "__main__":
    main()
