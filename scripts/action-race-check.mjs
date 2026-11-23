import assert from 'node:assert/strict';
import path from 'node:path';
import { mkdir } from 'node:fs/promises';
import { startHarness, scenario, finishHarness, ROOT, A, B, C, cases, within } from './ui-race-check.mjs';

// Real workbench module and DOM, with every /v1 response intercepted. The OIDC
// adapter is synthetic; these checks do not exercise a database or a real IdP.
const out = path.resolve(process.env.UI_REVIEW_OUTPUT || path.join(ROOT, 'artifacts/browser/action-races'));
await mkdir(out, { recursive: true });
const proposal = { proposal_id:'11112222-3333-4444-8555-666677778888', proposal_sha256:'b'.repeat(64), expected_case_version:4, expected_order_version:7, order_ref:'A-100', state:'pending' };
const awaiting = {...cases[0], state:'awaiting_approval', version:4, resolution:{answer:'Cancellation awaits a human review.',intent:'cancel_request',citations:[]},proposals:[proposal]};
const evidence = marker => ({items:[{title:marker,locator:'fresh-review#policy',body:marker,chunk_id:'fresh-review'}]});
let count=0;
async function run(name, mode, test, options={}) { count++; await scenario(name,mode,test,options); }
try {
  await startHarness();
  await run('pending resolution stays disabled across tab and case redraws', 'fixture', async({page,hold,release,requests,waitHeader})=>{
    const pending=hold('POST',`/v1/cases/${A}/resolve`);
    await page.locator('#resolveCase').click(); await within(pending.observed.promise,'pending resolution');
    await page.locator('[data-tab="evidence"]').click();
    await page.locator('[data-tab="summary"]').click();
    await page.screenshot({path:path.join(out,'resolve-pending-redraw.png'),fullPage:true});
    assert.equal(await page.locator('#resolveCase').isDisabled(),true,'Pending resolution must remain busy after redraw');
    await page.locator(`[data-case="${B}"]`).click(); await waitHeader('B');
    assert.equal(await page.locator('#resolveCase').isDisabled(),true,'Another case must wait for the pending write');
    await release(pending);
    await page.waitForFunction(()=>document.querySelector('#resolveCase') && !document.querySelector('#resolveCase').disabled);
    assert.equal(requests.filter(request=>request.method==='POST' && request.pathname.endsWith('/resolve')).length,1);
  });
  await run('pending approval prevents another approval or decline after redraw','fixture',async({page,hold,release,requests})=>{
    page.on('dialog',dialog=>dialog.accept());
    const pending=hold('POST',`/v1/proposals/${proposal.proposal_id}/approve`,{body:{status:'succeeded'}});
    await page.locator('#approveProposal').click();await within(pending.observed.promise,'pending approval');
    await page.locator('[data-tab="trace"]').click();
    await page.locator('[data-tab="summary"]').click();
    await page.screenshot({path:path.join(out,'approval-pending-redraw.png'),fullPage:true});
    assert.equal(await page.locator('#approveProposal').isDisabled(),true,'Pending approval must remain busy after redraw');
    assert.equal(await page.locator('#declineProposal').isDisabled(),true,'Decline must wait for pending approval');
    await release(pending);
    await page.waitForFunction(()=>document.querySelector('#approveProposal') && !document.querySelector('#approveProposal').disabled);
    assert.equal(requests.filter(request=>request.method==='POST' && request.pathname.endsWith('/approve')).length,1);
    assert.equal(requests.filter(request=>request.method==='POST' && request.pathname.endsWith('/reject')).length,0);
  },{role:'approver',prepare:async({setReply})=>{setReply('GET',`/v1/cases/${A}`,()=>({body:awaiting}));}});
  await run('old evidence error cannot overwrite newer evidence after revisiting tab','fixture',async({page,hold,release,setReply,waitBody})=>{
    const older=hold('GET',`/v1/cases/${A}/evidence`,{status:503,body:{message:'OLD-REVISITED-EVIDENCE-ERROR'}});
    await page.locator('[data-tab="evidence"]').click();await within(older.observed.promise,'old evidence request');
    await page.locator('[data-tab="trace"]').click();await waitBody('TRACE-A');
    setReply('GET',`/v1/cases/${A}/evidence`,()=>({body:evidence('CURRENT-EVIDENCE')}));
    await page.locator('[data-tab="evidence"]').click();await waitBody('CURRENT-EVIDENCE');
    await release(older);
    await page.screenshot({path:path.join(out,'revisited-evidence.png'),fullPage:true});
    assert.match(await page.locator('#detailBody').textContent(),/CURRENT-EVIDENCE/);
    assert.doesNotMatch(await page.locator('#detailBody').textContent(),/OLD-REVISITED/);
  });
  await run('old trace cannot overwrite newer trace after revisiting tab','fixture',async({page,hold,release,setReply,waitBody})=>{
    const older=hold('GET',`/v1/cases/${A}/trace`,{body:{events:[{stage:'OLD-TRACE',outcome:'ok',details:{},duration_ms:1}]}});
    await page.locator('[data-tab="trace"]').click();await within(older.observed.promise,'old trace request');
    await page.locator('[data-tab="evidence"]').click();await waitBody('EVIDENCE-A');
    setReply('GET',`/v1/cases/${A}/trace`,()=>({body:{events:[{stage:'CURRENT-TRACE',outcome:'ok',details:{},duration_ms:1}]}}));
    await page.locator('[data-tab="trace"]').click();await waitBody('CURRENT-TRACE');
    await release(older);
    await page.screenshot({path:path.join(out,'revisited-trace.png'),fullPage:true});
    assert.match(await page.locator('#detailBody').textContent(),/CURRENT-TRACE/);
    assert.doesNotMatch(await page.locator('#detailBody').textContent(),/OLD-TRACE/);
  });
  await run('disconnect notice survives old successful resolution refresh','oidc',async({page,hold,release})=>{
    const refresh=hold('GET','/v1/cases');
    await page.locator('#resolveCase').click();await within(refresh.observed.promise,'resolution inbox refresh');
    await page.locator('#signOut').click();
    await release(refresh);
    await page.screenshot({path:path.join(out,'disconnect-late-notice.png'),fullPage:true});
    assert.match(await page.locator('#notice').textContent(),/disconnected/);
    assert.match(await page.locator('#detail').textContent(),/Sign in to continue/);
  });
  await run('disconnect notice survives old successful approval refresh','oidc',async({page,hold,release})=>{
    page.on('dialog',dialog=>dialog.accept());
    const refresh=hold('GET','/v1/cases');
    await page.locator('#approveProposal').click();await within(refresh.observed.promise,'approval inbox refresh');
    await page.locator('#signOut').click();await release(refresh);
    assert.match(await page.locator('#notice').textContent(),/disconnected/);
    assert.match(await page.locator('#detail').textContent(),/Sign in to continue/);
  },{role:'approver',prepare:async({setReply})=>{
    setReply('GET',`/v1/cases/${A}`,()=>({body:awaiting}));
    setReply('POST',`/v1/proposals/${proposal.proposal_id}/approve`,()=>({body:{status:'succeeded'}}));
  }});
  await run('disconnect notice survives old successful case creation refresh','oidc',async({page,hold,release,setReply})=>{
    const created={...cases[0],case_id:C,message:'Created in the isolated action-race mock'};
    setReply('POST','/v1/cases',()=>({status:201,body:created}));
    setReply('GET',`/v1/cases/${C}`,()=>({body:created}));
    const refresh=hold('GET','/v1/cases');
    await page.locator('#newCase').click();await page.locator('#message').fill(created.message);await page.locator('#createSubmit').click();
    await within(refresh.observed.promise,'creation inbox refresh');
    await page.locator('#signOut').click();await release(refresh);
    assert.match(await page.locator('#notice').textContent(),/disconnected/);
    assert.match(await page.locator('#detail').textContent(),/Sign in to continue/);
  });
  await run('evaluation mode selector fits a 320px viewport','fixture',async({page})=>{
    await page.locator('[data-view="evaluation"]').click();await page.locator('#evalMode').waitFor();
    const bounds=await page.locator('#evalMode').evaluate(el=>({right:el.getBoundingClientRect().right,width:innerWidth,document_width:document.documentElement.scrollWidth}));
    assert(bounds.right<=bounds.width+1,'Evaluation mode selector must stay within the viewport');
    assert(bounds.document_width<=bounds.width+1,'Evaluation lab must not introduce document horizontal scrolling');
    await page.screenshot({path:path.join(out,'evaluation-320.png'),fullPage:true});
  },{viewport:{width:320,height:900}});
} finally { await finishHarness(path.join(out,'fresh-ui-review-results.json'),count); }
