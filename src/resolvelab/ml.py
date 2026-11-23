"""Immutable trained routing artifact; confidence never authorizes a tool."""
from __future__ import annotations

import json
from pathlib import Path
import os
import re
from functools import lru_cache

import joblib

ROOT = Path(__file__).resolve().parents[2]


def normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\{\{.*?\}\}", " ordernumber ", text)
    text = re.sub(r"\b[a-z]-\d+\b", " ordernumber ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def keyword_predict(text: str) -> str:
    value = normalize(text)
    if any(word in value for word in ("refund", "password", "address", "invoice", "payment", "return", "discount")):
        return "out_of_scope"
    if re.search(r"\b(cancel|cancellation|cancelling|canceling)\b", value):
        return "cancel_request"
    if re.search(r"\b(human|agent|representative|person|someone)\b", value):
        return "human_handoff"
    if re.search(r"\b(track|tracking|where|arrival|arrive|delivery status)\b", value):
        return "track_order"
    return "out_of_scope"


@lru_cache(maxsize=4)
def _load(path: str):
    target = Path(path)
    # Only a local artifact produced by the reviewed training command is loaded.
    # Never accept uploaded pickle/joblib files.
    return joblib.load(target)


class Router:
    def __init__(self, artifact_path: str | None = None):
        self.path = artifact_path or os.environ.get("ROUTER_ARTIFACT", str(ROOT / "artifacts/router.joblib"))
        self.bundle = _load(self.path)
        self.version = self.bundle["version"]
        self.threshold = float(self.bundle["threshold"])

    def predict(self, text: str) -> dict:
        model = self.bundle["pipeline"]
        probabilities = model.predict_proba([normalize(text)])[0]
        if self.bundle.get("dense_classifier") is not None and self.bundle.get("blend_weight", 0) > 0:
            from resolvelab.retrieval import embed
            dense = self.bundle["dense_classifier"]
            vector = embed([text])
            dense_probabilities = dense.predict_proba(vector)[0]
            aligned = [dense_probabilities[list(dense.classes_).index(label)] for label in model.classes_]
            weight = float(self.bundle["blend_weight"])
            probabilities = (1-weight)*probabilities + weight*__import__("numpy").asarray(aligned)
        index = int(probabilities.argmax())
        return {"intent": str(model.classes_[index]), "score": float(probabilities[index]),
                "threshold": self.threshold, "version": self.version}


def model_manifest() -> dict:
    return json.loads((ROOT / "artifacts/model_manifest.json").read_text(encoding="utf-8"))
