"""Release pinning cannot silently follow a mutable default model file."""
import json
from copy import deepcopy
from hashlib import sha256
import joblib
import numpy as np
import pytest
from resolvelab.db import ROOT
from resolvelab.service import load_pinned_router


class FixturePipeline:
    classes_ = np.asarray(["cancel_request", "track_order"])

    def __init__(self, probabilities):
        self.probabilities = probabilities

    def predict_proba(self, messages):
        return np.asarray([self.probabilities for _ in messages])


def release():
    info=json.loads((ROOT/'artifacts/model_manifest.json').read_text())
    return {'artifact_uri':info['artifact_uri'],'artifact_sha256':info['artifact_sha256'],'model_version':info['version'],'validation_threshold':info['threshold']}

def test_current_release_loads_exact_version():
    item=release()
    assert load_pinned_router(item).version==item['model_version']

@pytest.mark.parametrize('field,value',[('artifact_sha256','0'*64),('model_version','unpublished-version'),('validation_threshold',.99999),('artifact_uri','../pyproject.toml')])
def test_corrupt_or_substituted_release_fails_closed(field,value):
    item=deepcopy(release());item[field]=value
    with pytest.raises(RuntimeError):load_pinned_router(item)


def test_same_artifact_path_with_a_new_pin_loads_the_new_bundle(tmp_path, monkeypatch):
    from resolvelab import db, ml

    monkeypatch.setattr(db, "ROOT", tmp_path)
    artifact = tmp_path / "artifacts" / "router-fixture.joblib"
    artifact.parent.mkdir()

    def write(probabilities):
        joblib.dump({"version": "fixture-router-v1", "threshold": .425,
                     "pipeline": FixturePipeline(probabilities)}, artifact)
        return {"artifact_uri": "artifacts/router-fixture.joblib",
                "artifact_sha256": sha256(artifact.read_bytes()).hexdigest(),
                "model_version": "fixture-router-v1", "validation_threshold": .425}

    ml._load.cache_clear()
    try:
        first = write([.01, .99])
        assert load_pinned_router(first).predict("fixture request")["intent"] == "track_order"
        second = write([.99, .01])
        assert second["artifact_sha256"] != first["artifact_sha256"]
        assert load_pinned_router(second).predict("fixture request")["intent"] == "cancel_request"
        with pytest.raises(RuntimeError, match="checksum mismatch"):
            load_pinned_router(first)
    finally:
        ml._load.cache_clear()


def test_router_verifies_the_pinned_bytes_before_deserialization(tmp_path, monkeypatch):
    from resolvelab import ml

    artifact = tmp_path / "untrusted-fixture.joblib"
    artifact.write_bytes(b"changed after an earlier integrity check")
    monkeypatch.setattr(ml.joblib, "load", lambda _: pytest.fail("Unverified bytes must not be deserialized"))
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        ml.Router(str(artifact), artifact_sha256="0" * 64)


def test_optional_openai_adapter_schema_binds_answer(monkeypatch):
    from resolvelab.generation import generate
    monkeypatch.setenv('GENERATION_ADAPTER','openai')
    monkeypatch.setenv('OPENAI_API_KEY','fixture-not-a-real-key')
    monkeypatch.setenv('OPENAI_MODEL','fixture-mocked-model')
    expected='The order is pending.'
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def raise_for_status(self):pass
        def iter_bytes(self,**kwargs):yield json.dumps({'output':[{'content':[{'type':'output_text','text':json.dumps({'answer':expected})}]}]}).encode()
    class Client:
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def stream(self,method,url,json,headers):
            assert method=='POST'
            assert url=='https://api.openai.com/v1/responses'
            assert json['store'] is False
            assert json['text']['format']['schema']['properties']['answer']['enum']==[expected]
            return Response()
    monkeypatch.setattr('resolvelab.generation.httpx.Client',Client)
    assert generate({'draft':{'answer':expected},'evidence':[]})['answer']==expected
