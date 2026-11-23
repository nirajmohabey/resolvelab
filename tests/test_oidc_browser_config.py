"""Only public, explicitly trusted OIDC metadata reaches the browser."""
from resolvelab.config import public_auth_config


def test_fixture_auth_config_is_explicit_local_only(client):
    response=client.get('/v1/auth/config')
    assert response.status_code==200
    assert response.json()=={'mode':'fixture','local':True,'configured':True,'demo':True}
    login=client.post('/v1/demo/session',json={'actor':'alpha-operator'}).json()
    session=client.get('/v1/session',headers={'Authorization':'Bearer '+login['access_token']}).json()
    assert session['organization']=={'tenant_id':'11111111-1111-4111-8111-111111111111','display_name':'Alpha Retail'}


def test_oidc_api_only_does_not_claim_browser_signin(client,monkeypatch):
    monkeypatch.setenv('AUTH_MODE','oidc')
    monkeypatch.delenv('OIDC_CLIENT_ID',raising=False)
    response=client.get('/v1/auth/config')
    assert response.status_code==200
    assert response.json()['mode']=='oidc' and response.json()['configured'] is False
    assert response.json()['demo'] is False
    assert client.post('/v1/demo/session',json={'actor':'alpha-operator'}).status_code==404


def test_trusted_spa_metadata_and_precise_connect_origins(client,monkeypatch):
    for key,value in {'AUTH_MODE':'oidc','OIDC_CLIENT_ID':'public-client-id','OIDC_ISSUER':'https://issuer.example.test/',
                      'OIDC_AUTHORIZATION_URL':'https://login.example.test/authorize','OIDC_TOKEN_URL':'https://token.example.test/oauth/token',
                      'OIDC_JWKS_URL':'https://issuer.example.test/keys','OIDC_AUDIENCE':'resolvelab-api',
                      'OIDC_SCOPE':'openid profile resolvelab:operate','OIDC_CLIENT_SECRET':'must-never-be-returned'}.items():
        monkeypatch.setenv(key,value)
    response=client.get('/v1/auth/config')
    config=response.json()
    assert config['configured'] is True and config['client_id']=='public-client-id'
    assert config['audience']=='resolvelab-api' and config['demo'] is False
    assert 'must-never-be-returned' not in response.text
    csp=response.headers['Content-Security-Policy']
    assert "connect-src 'self' https://issuer.example.test https://token.example.test;" in csp
    assert 'https://login.example.test' not in csp
    monkeypatch.setenv('OIDC_TOKEN_URL','http://untrusted.test/token')
    assert public_auth_config()['configured'] is False
