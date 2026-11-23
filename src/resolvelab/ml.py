"""Immutable trained routing artifact; confidence never authorizes a tool."""
from __future__ import annotations

import json
from pathlib import Path
import os
import re
import unicodedata
from functools import lru_cache

import joblib
import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\{\{.*?\}\}", " ordernumber ", text)
    text = re.sub(r"\b[a-z]-\d+\b", " ordernumber ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def normalize_v2(text: str) -> str:
    """A versioned feature transform; legacy releases retain their original transform."""
    value=unicodedata.normalize("NFKC",text).casefold().replace("’", "'")
    for before,after in {"don't":"do not","doesn't":"does not","can't":"cannot","won't":"will not",
                         "isn't":"is not","i'm":"i am","i'd":"i would","i've":"i have"}.items():
        value=value.replace(before,after)
    value=re.sub(r"\{\{.*?\}\}"," order ",value)
    value=re.sub(r"\b[a-z]{1,6}[- ]?\d{2,12}\b|\b\d{3,12}\b"," order ",value)
    value=re.sub(r"[^a-z0-9\s]"," ",value)
    value=re.sub(r"\border\s+(?:number\s+)?order\b","order",value)
    return " ".join(value.split())


def calibrated_probabilities(probabilities, temperature: float):
    logits=np.log(np.clip(np.asarray(probabilities,dtype=np.float64),1e-9,1.0))/float(temperature)
    logits-=logits.max(axis=-1,keepdims=True)
    result=np.exp(logits)
    return result/result.sum(axis=-1,keepdims=True)


_HUMAN_REQUEST = re.compile(r"\b(?:speak|talk|chat|connect|transfer|handoff|hand off|pass)\b.{0,65}\b(?:human|person|staff|employee|agent|representative|someone)\b|\b(?:human|person|employee|representative|staff member)\b.{0,45}\b(?:review|explain|consider|judgment|handle|answer|take over)\b|^(?:human|agent|representative|a real person|manual review)(?: please)?$",re.I)
_NEGATED_CANCEL = re.compile(r"\b(?:do not|don't|never|not asking to|not requesting|no need to)\s+(?:(?:ever|actually|automatically|please)\s+)?(?:cancel|stop|void|withdraw)\b",re.I)
_UNSUPPORTED_TOPIC = re.compile(r"\b(?:refund\w*|reimburs\w*|return\w*|exchang\w*|password\w*|account\w*|address\w*|payment\w*|invoice\w*|discount\w*|newsletter\w*|registration\w*|replacement\w*)\b|\b(?:place|buy|add)\b.{0,30}\b(?:another|replacement|new|more)\b.{0,20}\b(?:order|purchase|product|item)\b",re.I)


def human_requested(message: str) -> bool:
    value=normalize_v2(message)
    if re.search(r"\b(?:do not|rather not|no need to)\s+(?:connect|speak|talk|transfer)\b",value):return False
    return bool(_HUMAN_REQUEST.search(value))


def routing_text_v4(message: str) -> str:
    """Explicit, versioned semantic features; does not authorize a tool."""
    value=unicodedata.normalize("NFKC",message).casefold().replace("’", "'")
    if _NEGATED_CANCEL.search(value) and re.search(r"\b(?:track\w*|where|locat\w*|status)\b",value):
        clauses=re.split(r"[;,]|\bbut\b",value)
        value=" ".join(clause for clause in clauses if not _NEGATED_CANCEL.search(clause)) or value
    result=normalize_v2(value)
    if human_requested(message) and "human support agent requested" not in result:
        result+=" human support agent requested"
    return result


def scope_guard_v1(message: str) -> str | None:
    """Fail-closed scope denial for explicit unsupported/negated actions.

    Human requests are handed to learned routing, which may escalate them; this
    guard never performs an action or grants data access. A recognized denial is
    a rule decision, not a calibrated model probability.
    """
    value=normalize_v2(message)
    if human_requested(message):return None
    if _NEGATED_CANCEL.search(value) and not re.search(r"\b(?:track\w*|where|locat\w*|status)\b",value):
        return "negated_automatic_action"
    for match in _UNSUPPORTED_TOPIC.finditer(value):
        prefix=value[max(0,match.start()-65):match.start()]
        if re.search(r"\b(?:no|not|without|rather than)\b(?:\s+\w+){0,5}\s*$",prefix):continue
        return "explicit_unsupported_topic"
    return None


def apply_scope_guards(probabilities,messages,labels):
    values=np.asarray(probabilities,dtype=np.float64).copy();reasons=[]
    for i,message in enumerate(messages):
        reason=scope_guard_v1(message);reasons.append(reason)
        if reason:
            values[i]=0.;values[i,list(labels).index("out_of_scope")]=1.
    return values,reasons


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
        if self.bundle.get("normalization_version") in {"unicode-order-v2","semantic-scope-v1"}:
            return self._predict_v4(text)
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

    def _predict_v4(self, message: str) -> dict:
        from resolvelab.retrieval import embed_consistent
        normalized=routing_text_v4(message) if self.bundle.get("normalization_version")=="semantic-scope-v1" else normalize_v2(message)
        bundle=self.bundle
        word_x=bundle["features"].transform([normalized])
        weight=float(bundle["blend_weight"])
        vector=embed_consistent([normalized]) if weight else None
        scope_score=None
        if bundle["architecture"]=="hierarchical":
            scope=bundle["scope_word"].predict_proba(word_x)
            intent=bundle["intent_word"].predict_proba(word_x)
            if weight:
                scope=(1-weight)*scope+weight*bundle["scope_dense"].predict_proba(vector)
                intent=(1-weight)*intent+weight*bundle["intent_dense"].predict_proba(vector)
            scope_score=float(scope[0,list(bundle["scope_word"].classes_).index(1)])
            probabilities=np.zeros((1,len(bundle["labels"])),dtype=np.float64)
            for index,label in enumerate(bundle["labels"]):
                probabilities[0,index]=(1-scope_score) if label=="out_of_scope" else scope_score*intent[0,list(bundle["intent_word"].classes_).index(label)]
        else:
            probabilities=bundle["flat_word"].predict_proba(word_x)
            if weight:
                probabilities=(1-weight)*probabilities+weight*bundle["flat_dense"].predict_proba(vector)
        probabilities=calibrated_probabilities(probabilities,bundle.get("temperature",1.0))
        scope_reason=None
        if bundle.get("scope_guard_version")=="explicit-scope-v1":
            probabilities,reasons=apply_scope_guards(probabilities,[message],bundle["labels"]);scope_reason=reasons[0]
        probabilities=probabilities[0]
        index=int(probabilities.argmax())
        return {"intent":str(bundle["labels"][index]),"score":float(probabilities[index]),"threshold":self.threshold,
                "version":self.version,"scope_score":scope_score,"calibration_temperature":float(bundle.get("temperature",1.0)),
                "feature_version":bundle["normalization_version"]+"/individual-onnx-v1",
                "scope_guard_reason":scope_reason,"score_source":"scope_rule" if scope_reason else "calibrated_model"}


def model_manifest() -> dict:
    return json.loads((ROOT / "artifacts/model_manifest.json").read_text(encoding="utf-8"))
