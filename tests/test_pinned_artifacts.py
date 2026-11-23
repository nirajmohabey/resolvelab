"""Release pinning cannot silently follow a mutable default model file."""
import json
from copy import deepcopy
import pytest
from resolvelab.db import ROOT
from resolvelab.service import load_pinned_router

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
