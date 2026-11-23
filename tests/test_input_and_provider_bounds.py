"""Input and optional-provider resource contracts use no external API calls."""
import json
import os
import subprocess
import sys
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError

from resolvelab.db import ROOT
from resolvelab.schemas import CreateCase
from resolvelab.security import strict_request_json


@pytest.mark.parametrize("field", ["message", "order_ref"])
def test_database_text_rejects_null_before_storage(client, field):
    from resolvelab.evaluate import auth_headers
    body = {"message": "Where is my order?", field: "bad\x00value"}
    with pytest.raises(ValidationError):
        CreateCase.model_validate(body)
    response = client.post("/v1/cases", json=body, headers={**auth_headers(), "Idempotency-Key": "bad-text"})
    assert response.status_code == 422
    assert response.headers["X-Request-ID"]


@pytest.mark.parametrize("body", [
    b'{"message":"first","message":"second"}',
    b'{"message":NaN}', b'{"message":Infinity}',
    b'{"message":-Infinity}', b'{"message":"\xff"}',
    b'{"message":' + b'['*1500 + b'0' + b']'*1500 + b'}',
])
def test_nonstandard_or_ambiguous_json_is_rejected_with_contract_error(client, body):
    response = client.post("/v1/cases", content=body, headers={"Content-Type":"application/json"})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_json"
    assert response.headers["X-Request-ID"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_json_depth_scanner_ignores_brackets_and_escaped_quotes_inside_strings():
    value = {"message": '[quoted] {text} ' + '"\\'*100}
    assert strict_request_json(json.dumps(value).encode()) == value


def test_optimized_evaluation_cannot_publish_unchecked_results():
    result = subprocess.run([sys.executable,"-O","-c","import resolvelab.evaluate"], cwd=ROOT,
                            env=os.environ.copy(), capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert "Evaluation requires assertion checks" in result.stderr


def _provider(monkeypatch, response):
    from resolvelab import generation
    original_client = httpx.Client
    monkeypatch.setenv("GENERATION_ADAPTER","openai")
    monkeypatch.setenv("OPENAI_API_KEY","fixture-not-a-real-key")
    monkeypatch.setenv("OPENAI_MODEL","fixture-model")
    transport = httpx.MockTransport(lambda request: response)
    monkeypatch.setattr(generation.httpx,"Client",lambda **kwargs:original_client(transport=transport,**kwargs))
    return generation


def test_provider_stream_is_stopped_at_response_byte_budget(monkeypatch):
    class Endless(httpx.SyncByteStream):
        count=0
        closed=False
        def __iter__(self):
            for _ in range(10000):
                self.count+=1
                yield b"x"*16384
        def close(self):self.closed=True
    stream=Endless()
    generation=_provider(monkeypatch,httpx.Response(200,stream=stream))
    with pytest.raises(generation.ProviderUnavailable):
        generation.generate({"draft":{"answer":"Pending"},"evidence":[]})
    assert stream.count<=18 and stream.closed


@pytest.mark.parametrize("payload", [[],{"output":["bad"]},{"output":[{"content":[3]}]},
                                         {"output":[{"content":[{"type":"output_text","text":4}]}]}])
def test_provider_malformed_envelopes_fail_as_provider_errors(monkeypatch,payload):
    generation=_provider(monkeypatch,httpx.Response(200,json=payload))
    with pytest.raises(generation.ProviderUnavailable):
        generation.generate({"draft":{"answer":"Pending"},"evidence":[]})


def test_evidence_budget_is_checked_before_a_provider_request(monkeypatch):
    generation=_provider(monkeypatch,httpx.Response(200,json={}))
    evidence=[{"chunk_id":"id","locator":"policy","body":"x"*(generation.MAX_EVIDENCE_BYTES+1)}]
    with patch.object(generation.httpx,"Client") as client:
        with pytest.raises(generation.ProviderUnavailable,match="budget"):
            generation.generate({"draft":{"answer":"Pending"},"evidence":evidence})
        client.assert_not_called()


def test_readiness_does_not_admit_a_broken_published_corpus(client, monkeypatch):
    from resolvelab.retrieval import CorpusAdmissionError
    def broken(*args, **kwargs):
        raise CorpusAdmissionError("Invalid retained corpus")
    monkeypatch.setattr('resolvelab.retrieval.retrieve',broken)
    assert client.get('/health/live').status_code==200
    assert client.get('/health/ready').status_code==503


def test_content_length_is_bounded_before_integer_conversion(client):
    response=client.post('/v1/cases',content=b'{}',headers={'Content-Type':'application/json','Content-Length':'9'*5000})
    assert response.status_code==413 and response.headers['X-Request-ID']


@pytest.mark.parametrize('setting',['LOGIN_REQUESTS_PER_MINUTE','IP_REQUESTS_PER_MINUTE','ACTOR_REQUESTS_PER_MINUTE'])
def test_fractional_request_quotas_are_rejected_at_startup(monkeypatch,setting):
    from resolvelab.config import validate_configuration
    monkeypatch.setenv(setting,'1.5')
    with pytest.raises(RuntimeError,match=setting):validate_configuration()
