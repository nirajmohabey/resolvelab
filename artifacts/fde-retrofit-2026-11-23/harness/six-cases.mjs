// Agent-run simulated operator acceptance. All API calls target the owned loopback app.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
const project = process.env.UAT_PROJECT;
const require = createRequire(path.join(project, 'package.json'));
const {chromium} = require('playwright');
const base = process.env.BASE_URL;
assert(/^http:\/\/127\.0\.0\.1:8137$/.test(base));
const out = process.env.UAT_OUTPUT;
const control = process.env.UAT_CONTROL;
const pythonFiles = (await fs.readdir(path.join(project,'src/resolvelab'))).filter(name=>name.endsWith('.py')).map(name=>'src/resolvelab/'+name);
const sourceFiles = ['web/app.js','web/app.css','web/index.html','web/auth-client-src.js','web/auth-client.js',...pythonFiles,'fixtures/evaluation_cases.json','artifacts/v4/model_manifest.json','artifacts/router-domain-v4.joblib','artifacts/embeddings/model.onnx','artifacts/embeddings/tokenizer.json'];
const hashes = async () => Object.fromEntries(await Promise.all(sourceFiles.map(async name => [name, createHash('sha256').update(await fs.readFile(path.join(project,name))).digest('hex')])));
const report = {schema_version:'1.0',reviewer:'AI-agent simulated operator; no external human reviewer or customer signoff',scope:'Six synthetic acceptance cases through the existing interface, real local HTTP API and disposable PostgreSQL; no production enrollment/provider email check',started_at:new Date().toISOString(),base_url:base,cases:[],transactions:[],page_errors:[],source_hashes_before:await hashes()};
report.failure_injection={boundary:'existing generation.generate exception boundary',method:'Restart only the owned API with GENERATION_ADAPTER=uat-unavailable, which raises ProviderUnavailable before any external request; restore offline adapter on the same owned database before retry',external_http_requests:false,paid_provider_measured:false};
const browser = await chromium.launch({channel:'chrome',headless:true});
const context = await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
const page = await context.newPage();
page.setDefaultTimeout(12000);
page.on('pageerror', e=>report.page_errors.push(e.message));
report.console_errors=[];report.non_api_http_errors=[];
page.on('console', message=>{if(message.type()==='error')report.console_errors.push({message:message.text(),location:message.location()});});
page.on('dialog', dialog=>dialog.accept());
const captures=[];
page.on('response', response=>{
  const url=new URL(response.url());
  if(response.status()>=400&&!url.pathname.startsWith('/v1/'))report.non_api_http_errors.push({path:url.pathname,status:response.status()});
  if (!url.pathname.startsWith('/v1/') || url.pathname==='/v1/demo/session') return;
  captures.push((async()=>{
    let body; try{body=await response.json();}catch{body=null;}
    const request=response.request();
    report.transactions.push({observed_at:new Date().toISOString(),method:request.method(),path:url.pathname,status:response.status(),request_body:request.postDataJSON(),response:body,request_id:response.headers()['x-request-id']||null});
  })());
});
let token;
async function login(){const r=await context.request.post(base+'/v1/demo/session',{data:{actor:'alpha-operator'}});assert.equal(r.status(),200);token=(await r.json()).access_token;}
async function getCase(id){const r=await context.request.get(base+'/v1/cases/'+id,{headers:{Authorization:'Bearer '+token}});assert.equal(r.status(),200);const value=await r.json();report.transactions.push({method:'GET',path:'/v1/cases/'+id,status:r.status(),response:value,source:'authenticated evidence read; credential omitted'});return value;}
async function trace(id){const r=await context.request.get(base+'/v1/cases/'+id+'/trace',{headers:{Authorization:'Bearer '+token}});assert.equal(r.status(),200);const value=await r.json();report.transactions.push({method:'GET',path:'/v1/cases/'+id+'/trace',status:r.status(),response:value,source:'authenticated evidence read; credential omitted'});return value;}
async function clickCase(input){
  await page.getByRole('button',{name:'＋ New case',exact:true}).click();
  if(input.example)await page.getByRole('button',{name:input.example,exact:true}).click();
  if(input.message!==undefined)await page.locator('#message').fill(input.message);
  if(input.order!==undefined)await page.locator('#orderRef').fill(input.order);
  const pending=page.waitForResponse(r=>new URL(r.url()).pathname==='/v1/cases'&&r.request().method()==='POST'&&r.status()===201);
  await page.getByRole('button',{name:'Create case',exact:true}).click();
  const value=await(await pending).json();
  await page.getByRole('button',{name:'Resolve this case',exact:true}).waitFor();
  return value;
}
async function resolve(id,status=200,label='Resolve this case'){
 const pending=page.waitForResponse(r=>new URL(r.url()).pathname===`/v1/cases/${id}/resolve`&&r.request().method()==='POST');
 await page.getByRole('button',{name:label,exact:true}).click();
 const r=await pending;assert.equal(r.status(),status);return await r.json();
}
async function shot(name){await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});return name+'.png';}
async function run(id,name,expected,clicks,fn){
 const item={id,name,expected,ui_steps:clicks,started_at:new Date().toISOString()};
 report.cases.push(item);
 try{await fn(item);item.passed=true;console.log('PASS '+id+' '+name);}catch(e){item.passed=false;item.error=e.message;item.failure_screenshot=await shot(id+'-failed');console.log('FAIL '+id+' '+e.message);}
 item.completed_at=new Date().toISOString();
}
async function switchActor(actor,id){const r=page.waitForResponse(r=>new URL(r.url()).pathname===`/v1/cases/${id}`&&r.status()===200);await page.locator('#actor').selectOption(actor);await r;}
async function boundary(phase){await fs.writeFile(path.join(control,phase),'agent requested owned API configuration transition');const deadline=Date.now()+70000;while(true){try{await fs.access(path.join(control,phase+'-ready'));return;}catch{}if(Date.now()>deadline)throw new Error('Owned boundary transition timed out');await page.waitForTimeout(100);}}
try{
 await login();await page.goto(base);await page.locator('#statTotal').filter({hasText:/\d/}).waitFor();
 await run('AC01','Owned-order tracking','Resolved with authorized evidence; no action',['New case','Track an order','Create case','Resolve this case','Policy evidence'],async item=>{
  const c=await clickCase({example:'Track an order'});await resolve(c.case_id);await page.locator('#detail .badge.resolved').waitFor();
  await page.getByRole('tab',{name:'Policy evidence',exact:true}).click();await page.locator('.evidence-card').first().waitFor();assert((await page.locator('#detailBody').innerText()).includes('ownership'));
  const after=await getCase(c.case_id);assert.equal(after.state,'resolved');assert.equal(after.actions.length,0);assert(after.resolution.citations.length>0);
  item.actual={case_id:c.case_id,state:after.state,disposition:after.resolution.disposition,customer_subject:after.customer_subject,order_ref:after.order_ref,citations:after.resolution.citations.length,actions:0};item.screenshot=await shot('AC01-tracking-evidence');
 });
 let cancellation;
 await run('AC02','Eligible cancellation stops for approval','Pending proposal; case awaiting_approval; operator cannot approve; no order action',['New case','Request cancellation','Create case','Resolve this case'],async item=>{
  const c=await clickCase({example:'Request cancellation'});await resolve(c.case_id);await page.locator('#detail .badge.awaiting_approval').waitFor();
  const after=await getCase(c.case_id);assert.equal(after.state,'awaiting_approval');assert.equal(after.actions.length,0);assert.equal(await page.locator('#approveProposal').count(),0);assert(after.proposals.some(p=>p.state==='pending'));
  cancellation=after;item.actual={case_id:c.case_id,state:after.state,actions:0,pending_proposals:after.proposals.filter(p=>p.state==='pending').length,operator_approval_button:false};item.screenshot=await shot('AC02-awaiting-approval');
 });
 await run('AC03','Approver declines cancellation','Declined proposal escalates for human review; rejected receipt says order_changed false; no cancellation',['Switch to Alpha Approver','Choose Needs manual review','Decline proposal','Confirm'],async item=>{
  assert(cancellation,'AC02 prerequisite');const id=cancellation.case_id;await switchActor('alpha-approver-one',id);await page.locator('#declineReason').selectOption('needs_manual_review');
  const proposal=cancellation.proposals.find(p=>p.state==='pending');const pending=page.waitForResponse(r=>new URL(r.url()).pathname===`/v1/proposals/${proposal.proposal_id}/reject`&&r.status()===200);
  await page.getByRole('button',{name:'Decline proposal',exact:true}).click();const receipt=await(await pending).json();assert.equal(receipt.order_changed,false);await page.locator('#detail .badge.escalated').waitFor();
  const after=await getCase(id);assert.equal(after.state,'escalated');assert.equal(after.actions.length,0);assert.equal(after.proposals.find(p=>p.proposal_id===proposal.proposal_id).state,'rejected');
  item.actual={case_id:id,state:after.state,receipt,actions:0};item.screenshot=await shot('AC03-declined');await switchActor('alpha-operator',id);
 });
 await run('AC04','Unsupported refund request','Unsupported request escalates for human review; no proposal or action',['New case','Enter refund request','Create case','Resolve this case'],async item=>{
  const c=await clickCase({message:'Please refund my order A-100.',order:'A-100'});await resolve(c.case_id);await page.locator('#detail .badge.escalated').waitFor();
  const after=await getCase(c.case_id);assert.equal(after.state,'escalated');assert.equal(after.proposals.length,0);assert.equal(after.actions.length,0);
  item.actual={case_id:c.case_id,state:after.state,intent:after.intent,reason_code:after.resolution.reason_code,answer:after.resolution.answer,proposals:0,actions:0};item.screenshot=await shot('AC04-unsupported');
 });
 await run('AC05','Missing order information is attached to same case','needs_info; saving A-101 preserves case/customer/release and requires explicit resolution; second resolve succeeds',['New case','Enter Where is my order? without order','Create case','Resolve this case','Enter A-101','Save order reference','Resolve this case'],async item=>{
  const c=await clickCase({message:'Where is my order?',order:''});await resolve(c.case_id);await page.locator('#detail .badge.needs_info').waitFor();const missing=await getCase(c.case_id);
  item.before_screenshot=await shot('AC05-needs-info');await page.locator('#contextOrderRef').fill('A-101');const pending=page.waitForResponse(r=>new URL(r.url()).pathname===`/v1/cases/${c.case_id}/context`&&r.status()===200);
  await page.getByRole('button',{name:'Save order reference',exact:true}).click();const saved=await(await pending).json();assert.equal(saved.case_id,c.case_id);assert.equal(saved.customer_subject,missing.customer_subject);assert.equal(saved.release_id,missing.release_id);assert.equal(saved.state,'needs_info');assert.equal(saved.resolution,null);
  await page.getByRole('button',{name:'Resolve this case',exact:true}).waitFor();await resolve(c.case_id);await page.locator('#detail .badge.resolved').waitFor();const after=await getCase(c.case_id);assert.equal(after.order_ref,'A-101');assert.equal(after.actions.length,0);
  item.actual={case_id:c.case_id,initial_state:missing.state,saved_state:saved.state,final_state:after.state,same_customer:after.customer_subject===missing.customer_subject,same_release:after.release_id===missing.release_id,actions:0};item.screenshot=await shot('AC05-context-resolved');
 });
 await run('AC06','Recoverable generation failure and explicit retry','Unavailable generation boundary saves failed case and error trace; after dependency repair an operator retries current case/version and resolves without action',['New case','Track an order','Create case','[harness interrupts owned generation boundary]','Resolve this case','Execution trace','[harness repairs owned boundary]','Resolution','Retry resolution'],async item=>{
  const c=await clickCase({example:'Track an order'});await boundary('fail-generation');const failure=await resolve(c.case_id,503);assert.equal(failure.code,'provider_unavailable');await page.locator('#detail .badge.failed').waitFor();
  await page.locator(`#caseList [data-case="${c.case_id}"] .badge.failed`).waitFor();
  const failed=await getCase(c.case_id);const failedTrace=await trace(c.case_id);assert.equal(failed.state,'failed');assert(failedTrace.events.some(e=>e.stage==='generate'&&e.outcome==='error'));assert.equal(failed.actions.length,0);
  item.before_screenshot=await shot('AC06-failure-summary');await page.getByRole('tab',{name:'Execution trace',exact:true}).click();await page.locator('.trace-row').first().waitFor();item.trace_screenshot=await shot('AC06-failure-trace');
  await boundary('repair-generation');await page.getByRole('tab',{name:'Resolution',exact:true}).click();
  const retry=page.getByRole('button',{name:'Retry resolution',exact:true});item.retry_button_present=await retry.count()===1;item.actual={case_id:c.case_id,failed_state:failed.state,failed_version:failed.version,failure,actions_before_retry:failed.actions.length,generate_error_saved:true,failed_inbox_matches_detail:true};
  assert.equal(item.retry_button_present,true,'Failed case has a saved resolution object but the Resolution tab exposes no Retry resolution button');
  await resolve(c.case_id,200,'Retry resolution');await page.locator('#detail .badge.resolved').waitFor();const after=await getCase(c.case_id);assert.equal(after.state,'resolved');assert.equal(after.actions.length,0);assert(after.version>failed.version);
  const afterTrace=await trace(c.case_id);assert(afterTrace.events.some(e=>e.stage==='generate'&&e.outcome==='error'));assert(afterTrace.events.some(e=>e.stage==='generate'&&e.outcome==='ok'));
  Object.assign(item.actual,{final_state:after.state,final_version:after.version,prior_error_trace_preserved:true,actions:0});item.screenshot=await shot('AC06-recovered');
 });
 assert.deepEqual(report.page_errors,[]);
}finally{
 await Promise.all(captures);report.source_hashes_after=await hashes();report.source_stable=JSON.stringify(report.source_hashes_before)===JSON.stringify(report.source_hashes_after);report.completed_at=new Date().toISOString();report.passed=report.cases.filter(c=>c.passed).length;report.total=6;report.browser_version=browser.version();report.expected_http_errors=report.transactions.filter(t=>t.status===503&&t.response?.code==='provider_unavailable');report.unexpected_http_errors=report.transactions.filter(t=>t.status>=400&&!(t.status===503&&t.response?.code==='provider_unavailable'));report.expected_console_error='One Chromium failed-resource 503 for intentionally interrupted generation boundary';
 await fs.writeFile(path.join(out,'summary.json'),JSON.stringify(report,null,2)+'\n');await context.close();await browser.close();
}
assert.equal(report.passed,6);assert.equal(report.source_stable,true);
