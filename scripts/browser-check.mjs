import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const base=process.env.BASE_URL||'http://127.0.0.1:8000';
const output=new URL('../artifacts/browser/',import.meta.url);
await fs.mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:process.env.BROWSER_CHANNEL||'chrome',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000},recordVideo:{dir:output.pathname.replace(/^\/([A-Z]:)/,'$1'),size:{width:1440,height:1000}},reducedMotion:'reduce'});
const page=await context.newPage();
const errors=[];page.on('pageerror',e=>errors.push(e.message));
page.on('dialog',dialog=>dialog.accept());
const wait=()=>page.waitForTimeout(900);
const check=async(name,fn)=>{await fn();console.log('PASS '+name);};
async function create(example){await page.getByRole('button',{name:'＋ New case',exact:true}).click();await page.getByRole('button',{name:example,exact:true}).click();await page.getByRole('button',{name:'Create case',exact:true}).click();await page.getByRole('button',{name:'Resolve this case',exact:true}).waitFor();await wait();await page.getByRole('button',{name:'Resolve this case',exact:true}).click();}
try{
 await page.goto(base);await page.locator('#statTotal').filter({hasText:/\d/}).waitFor();
 await page.screenshot({path:new URL('01-inbox.png',output).pathname.replace(/^\/([A-Z]:)/,'$1'),fullPage:true});
 await check('tracking resolves with policy evidence',async()=>{await create('Track an order');await page.locator('#detail .badge.resolved').waitFor();await page.getByRole('button',{name:'Policy evidence',exact:true}).click();await page.locator('.evidence-card').first().waitFor();assert((await page.locator('#detailBody').innerText()).includes('ownership'));await wait();await page.screenshot({path:new URL('02-evidence.png',output).pathname.replace(/^\/([A-Z]:)/,'$1'),fullPage:true});});
 await check('trace stages visible',async()=>{await page.getByRole('button',{name:'Execution trace',exact:true}).click();await page.locator('.trace-row').first().waitFor();assert((await page.locator('#detailBody').innerText()).toLowerCase().includes('validate'));await wait();});
 await check('cancellation stops for approval',async()=>{await create('Request cancellation');await page.locator('#detail .badge.awaiting_approval').waitFor();await page.getByText('Switch to Alpha · Approver',{exact:false}).waitFor();await page.screenshot({path:new URL('03-proposal.png',output).pathname.replace(/^\/([A-Z]:)/,'$1'),fullPage:true});await wait();});
 await check('approver executes one local cancellation',async()=>{await page.locator('#actor').selectOption('alpha-approver-one');await page.getByRole('button',{name:'Approve cancellation',exact:true}).waitFor();await wait();await page.getByRole('button',{name:'Approve cancellation',exact:true}).click();await page.getByText('Cancellation completed. One durable action was recorded.',{exact:true}).waitFor();await page.screenshot({path:new URL('04-receipt.png',output).pathname.replace(/^\/([A-Z]:)/,'$1'),fullPage:true});});
 await check('human request persists escalation',async()=>{await page.locator('#actor').selectOption('alpha-operator');await page.locator('#statTotal').filter({hasText:/\d/}).waitFor();await create('Human handoff');await page.locator('#detail .badge.escalated').waitFor();await wait();});
 await check('evaluation and releases render',async()=>{await page.getByRole('button',{name:'Evaluation lab',exact:false}).click();await page.getByRole('heading',{name:'Workflow invariants'}).waitFor();await page.screenshot({path:new URL('05-evaluations.png',output).pathname.replace(/^\/([A-Z]:)/,'$1'),fullPage:true});await wait();await page.getByRole('button',{name:'Release registry',exact:false}).click();await page.getByRole('heading',{name:'Release registry',exact:true}).waitFor();});
 await check('tenant switch removes alpha case context',async()=>{await page.getByRole('button',{name:'Case inbox',exact:false}).click();await page.locator('#actor').selectOption('beta-operator');await page.locator('#tenantName').filter({hasText:'Beta Retail'}).waitFor();await page.locator('#detail h2').filter({hasText:'Track B-100'}).waitFor();assert(!(await page.locator('#detail').innerText()).includes('A-100'));});
 await check('mobile layout fits viewport',async()=>{await page.setViewportSize({width:390,height:844});await wait();const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);assert(!overflow,'horizontal overflow');await page.screenshot({path:new URL('06-mobile.png',output).pathname.replace(/^\/([A-Z]:)/,'$1'),fullPage:true});});
 assert.deepEqual(errors,[],'browser JavaScript errors');
 await fs.writeFile(new URL('browser-results.json',output),JSON.stringify({passed:8,errors,base_url:base,viewport:[1440,1000],mobile:[390,844],generated_at:new Date().toISOString()},null,2));
}finally{await context.close();await browser.close();}
