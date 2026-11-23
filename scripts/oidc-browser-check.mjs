/* Read-only browser security checks with mocked HTTPS IdP and real RSA signatures. */
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { generateKeyPairSync, createHash, sign } from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const base=process.env.BASE_URL||'http://127.0.0.1:8000';
const idp='https://idp.example.test';
const issuer=idp+'/';
const clientId='resolvelab-public-browser-test';
const pendingKey='resolvelab.oidc.pending.v1';
const output=path.join(root,'artifacts/security');
await fs.mkdir(output,{recursive:true});
const sourceFiles=['web/auth-client-src.js','web/auth-client.js','web/app.js','package-lock.json'];
const initialHashes={};
for(const filename of sourceFiles)initialHashes[filename]=createHash('sha256').update(await fs.readFile(path.join(root,filename))).digest('hex');
const pair=generateKeyPairSync('rsa',{modulusLength:2048});
const other=generateKeyPairSync('rsa',{modulusLength:2048});
const jwk={...pair.publicKey.export({format:'jwk'}),kid:'test-key',use:'sig',alg:'RS256'};
const browser=await chromium.launch({channel:process.env.BROWSER_CHANNEL||'chrome',headless:true});
const results=[];
let sourceChanged=false;
const config={mode:'oidc',local:false,demo:false,configured:true,issuer,client_id:clientId,
  authorization_endpoint:idp+'/authorize',token_endpoint:idp+'/token',jwks_uri:idp+'/keys',
  audience:'resolvelab-api',scope:'openid profile resolvelab:operate',algorithm:'RS256'};
const customerCase={tenant_id:'11111111-1111-4111-8111-111111111111',case_id:'33333333-3333-4333-8333-333333333333',
  release_id:'44444444-4444-4444-8444-444444444444',state:'new',version:1,message:'Mock private case for verified organization',
  customer_subject:'verified-customer',order_ref:'P-100',resolution:null,proposals:[],actions:[]};

function signedIdToken(nonce,kind){
  const now=Math.floor(Date.now()/1000);
  const claims={iss:issuer,sub:'external-test-user',aud:clientId,iat:now,exp:now+300,nonce};
  if(kind==='bad_nonce')claims.nonce='incorrect-nonce';
  if(kind==='expired_token'){claims.iat=now-1200;claims.exp=now-600;}
  const header=Buffer.from(JSON.stringify({alg:'RS256',kid:'test-key',typ:'JWT'})).toString('base64url');
  const body=Buffer.from(JSON.stringify(claims)).toString('base64url');
  const input=header+'.'+body;
  return input+'.'+sign('RSA-SHA256',Buffer.from(input),kind==='bad_signature'?other.privateKey:pair.privateKey).toString('base64url');
}

async function scenario(kind){
  const context=await browser.newContext({viewport:{width:1360,height:900},reducedMotion:'reduce'});
  const page=await context.newPage();
  const pageErrors=[];
  page.on('pageerror',error=>pageErrors.push(error.message));
  const seen={token:0,jwks:0,session:0,demo:0,authorization:0,unauthenticated_protected:0};
  let authorization,heldCases,releaseCases;
  let delayCases=false;
  const expectedAccess='synthetic-access-'+kind;
  const selected=kind==='unconfigured'?{mode:'oidc',local:false,demo:false,configured:false,message:'Browser sign-in is not configured.'}:
    kind==='spoofed_fixture'?{mode:'fixture',local:false,demo:true,configured:true}:config;
  const json=async(route,data,status=200)=>route.fulfill({status,contentType:'application/json',headers:{'Cache-Control':'no-store','Access-Control-Allow-Origin':base},body:JSON.stringify(data)});
  await context.route(idp+'/**',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers:{'Access-Control-Allow-Origin':base,'Access-Control-Allow-Methods':'POST, GET, OPTIONS','Access-Control-Allow-Headers':'content-type'}});
    if(url.pathname==='/authorize'){
      seen.authorization++;authorization=url;
      return route.fulfill({status:200,contentType:'text/html',body:'<!doctype html><title>Mock identity provider</title><h1>Mock identity provider</h1>'});
    }
    if(url.pathname==='/token'){
      seen.token++;
      const form=new URLSearchParams(request.postData());
      assert.equal(form.get('grant_type'),'authorization_code');
      assert.equal(form.get('client_id'),clientId);
      assert.equal(form.get('redirect_uri'),base+'/');
      assert.equal(form.get('code'),'fixture-code');
      assert.equal(createHash('sha256').update(form.get('code_verifier')).digest('base64url'),authorization.searchParams.get('code_challenge'));
      assert(!form.has('client_secret'),'A public browser client must never send a secret');
      return json(route,{access_token:expectedAccess,token_type:'Bearer',expires_in:300,
        id_token:signedIdToken(authorization.searchParams.get('nonce'),kind)});
    }
    if(url.pathname==='/keys'){seen.jwks++;return json(route,{keys:[jwk]});}
    throw new Error('Unexpected mocked provider endpoint: '+url.pathname);
  });
  await context.route(base+'/**',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.pathname==='/'){
      const response=await route.fetch();
      const headers={...response.headers()};
      const csp=headers['content-security-policy']||"default-src 'self'; connect-src 'self';";
      headers['content-security-policy']=csp.replace(/connect-src[^;]*;/,"connect-src 'self' "+idp+';');
      return route.fulfill({response,headers,body:await response.body()});
    }
    if(url.pathname.startsWith('/static/')||url.pathname==='/favicon.ico')return route.continue();
    if(url.pathname==='/v1/auth/config')return json(route,selected);
    if(url.pathname==='/v1/demo/session'){seen.demo++;return json(route,{code:'fixture_auth_disabled',message:'Fixture authentication is forbidden.'},403);}
    if(url.pathname.startsWith('/v1/')){
      if(request.headers().authorization!=='Bearer '+expectedAccess){
        seen.unauthenticated_protected++;
        return json(route,{code:'authentication_required',message:'A bearer token is required.'},401);
      }
      if(url.pathname==='/v1/session'){
        seen.session++;
        return json(route,{principal:{tenant_id:customerCase.tenant_id,actor_id:'55555555-5555-4555-8555-555555555555',role:'operator',customer_subject:'verified-customer'},
          organization:{tenant_id:customerCase.tenant_id,display_name:'Verified Acme Organization'},demo:false,fixture_clock:null});
      }
      if(url.pathname==='/v1/cases'){
        if(delayCases){heldCases=true;await new Promise(resolve=>{releaseCases=resolve;});}
        return json(route,{items:[customerCase],limit:100,offset:0});
      }
      if(url.pathname==='/v1/cases/'+customerCase.case_id)return json(route,customerCase);
      if(url.pathname==='/v1/dashboard')return json(route,{states:{new:1},total_cases:1,successful_actions:0});
      if(url.pathname==='/v1/releases'||url.pathname==='/v1/evaluations')return json(route,{items:[]});
      throw new Error('Unexpected mocked application operation: '+url.pathname);
    }
    return route.continue();
  });
  try{
    await page.goto(base);
    if(kind==='unconfigured'||kind==='spoofed_fixture'){
      await page.locator('#notice').filter({hasText:/not configured|not available/i}).waitFor();
      assert(await page.locator('#signIn').isDisabled());
      assert(await page.locator('#actor').isHidden());
      assert.equal(seen.demo,0);assert.equal(seen.token,0);assert.equal(seen.session,0);
    }else{
      await page.locator('#signIn:not([hidden])').waitFor();
      assert(await page.locator('#actor').isHidden());
      await page.locator('#signIn').click();
      await page.waitForURL(idp+'/authorize**');
      assert.equal(authorization.searchParams.get('response_type'),'code');
      assert.equal(authorization.searchParams.get('code_challenge_method'),'S256');
      assert.equal(authorization.searchParams.get('client_id'),clientId);
      assert.equal(authorization.searchParams.get('redirect_uri'),base+'/');
      assert(authorization.searchParams.get('state').length>=32);
      assert(authorization.searchParams.get('nonce').length>=32);
      assert(authorization.searchParams.get('code_challenge').length>=43);
      if(kind==='malformed_pending_timestamp'){
        await context.addInitScript(({origin,key})=>{
          if(location.origin===origin&&location.search.includes('code=')){
            const pending=JSON.parse(sessionStorage.getItem(key));pending.createdAt='invalid-time';
            sessionStorage.setItem(key,JSON.stringify(pending));
          }
        },{origin:base,key:pendingKey});
      }
      const state=kind==='bad_state'?'incorrect-state':authorization.searchParams.get('state');
      await page.goto(base+'/?code=fixture-code&state='+encodeURIComponent(state)+'&iss='+encodeURIComponent(issuer));
      if(['bad_state','bad_nonce','bad_signature','expired_token','malformed_pending_timestamp'].includes(kind)){
        await page.locator('#notice').filter({hasText:'Sign-in could not be completed'}).waitFor();
        assert.equal(seen.token,['bad_state','malformed_pending_timestamp'].includes(kind)?0:1);
        assert.equal(seen.session,0);
        assert(await page.locator('#actor').isHidden());
        assert(!((await page.locator('#caseList').innerText()).includes('Mock private case')));
      }else{
        await page.locator('#tenantName').filter({hasText:'Verified Acme Organization'}).waitFor();
        await page.locator('#detail h2').filter({hasText:customerCase.message}).waitFor();
        assert(await page.locator('#actor').isHidden());
        assert(await page.locator('.identity-label').isHidden());
        assert.equal(seen.session,1);assert.equal(seen.token,1);assert(seen.jwks>=1);assert.equal(seen.demo,0);
        assert.equal(await page.locator('#environmentTag').innerText(),'OIDC');
        if(kind==='late_response_after_disconnect'){
          delayCases=true;
          await page.locator('#refresh').click();
          for(let i=0;i<50&&!heldCases;i++)await page.waitForTimeout(20);
          assert(heldCases,'A protected cases response must actually be pending');
          await page.locator('#signOut').click();
          releaseCases();await page.waitForTimeout(300);
          assert(!((await page.locator('#caseList').innerText()).includes('Mock private case')),'A stale response restored private case data after disconnect');
          assert(!((await page.locator('#detail').innerText()).includes('Mock private case')));
        }else{
          await page.locator('#signOut').click();
          await page.locator('#notice').filter({hasText:'disconnected'}).waitFor();
        }
        assert(await page.locator('#signOut').isHidden());
      }
      assert.equal(new URL(page.url()).search,'','Callback parameters must be removed from browser history');
    }
    const storage=await page.evaluate(()=>({local:{...localStorage},session:{...sessionStorage}}));
    assert(!JSON.stringify(storage).includes(expectedAccess),'An access token reached persistent storage');
    assert(!Object.hasOwn(storage.session,pendingKey),'Consumed PKCE state was not removed');
    assert.equal((await context.cookies()).length,0,'No application token cookie is expected');
    assert.deepEqual(pageErrors,[]);
    results.push({scenario:kind,passed:true,requests:seen});
    console.log('PASS '+kind);
  }catch(error){
    results.push({scenario:kind,passed:false,error:error.message,requests:seen,page_errors:pageErrors});
    console.error('FAIL '+kind+': '+error.message);
  }finally{if(releaseCases)releaseCases();await context.close();}
}

try{
  for(const name of ['success_and_disconnect','bad_state','bad_nonce','bad_signature','expired_token','unconfigured','spoofed_fixture','malformed_pending_timestamp','late_response_after_disconnect'])await scenario(name);
}finally{
  const hashes={};
  for(const filename of sourceFiles)hashes[filename]=createHash('sha256').update(await fs.readFile(path.join(root,filename))).digest('hex');
  const sourceStable=JSON.stringify(initialHashes)===JSON.stringify(hashes);
  sourceChanged=!sourceStable;
  const report={generated_at:new Date().toISOString(),browser:browser.version(),mode:'mocked HTTPS IdP and app API, real RSA signatures, no production provider credentials',
    source_hashes:hashes,source_stable:sourceStable,passed:results.filter(x=>x.passed).length,total:results.length,results,
    limitations:['Identity-provider account registration, public TLS, real provider CORS and hosted callback URLs are not exercised by this isolated browser harness.','Backend token signatures and server-managed identity bindings are verified separately by PostgreSQL-backed acceptance tests.']};
  await fs.writeFile(path.join(output,'oidc-browser-results.json'),JSON.stringify(report,null,2));
  await browser.close();
}
if(sourceChanged||results.some(value=>!value.passed))process.exitCode=1;
