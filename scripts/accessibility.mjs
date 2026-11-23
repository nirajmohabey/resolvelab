import { chromium } from 'playwright';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const base=process.env.BASE_URL||'http://127.0.0.1:8000';
const browser=await chromium.launch({channel:'chrome',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
const page=await context.newPage();
const scans=[];
async function scan(name){
 const result=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze();
 scans.push({name,violations:result.violations,passes:result.passes.length,incomplete:result.incomplete.map(item=>({id:item.id,impact:item.impact,help:item.help,nodes:item.nodes.map(node=>({target:node.target,summary:node.failureSummary}))}))});
 console.log(JSON.stringify({name,violations:result.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))}));
}
try {
 await page.goto(base);await page.locator('#statTotal').filter({hasText:/\d/}).waitFor();
 await scan('desktop-inbox');
 await page.getByRole('button',{name:'Policy evidence',exact:true}).click();await page.locator('.evidence-card').first().waitFor();await scan('evidence');
 await page.getByRole('button',{name:'Execution trace',exact:true}).click();await page.locator('.trace-row').first().waitFor();await scan('trace');
 await page.getByRole('button',{name:'＋ New case',exact:true}).focus();
 await page.keyboard.press('Enter');await page.locator('#caseDialog').waitFor();await scan('new-case-dialog');
 await page.keyboard.press('Escape');assert.equal(await page.locator('#caseDialog').isVisible(),false);assert.equal(await page.locator('#newCase').evaluate(el=>el===document.activeElement),true);
 await page.getByRole('button',{name:'Evaluation lab',exact:false}).click();await page.getByRole('heading',{name:'Workflow invariants'}).waitFor();await scan('evaluation');
 const report=page.locator('[data-evaluation-report]').first();
 if(await report.count()){await report.click();await page.locator('#onlyEvaluationFailures').waitFor();await scan('evaluation-dialog');await page.keyboard.press('Escape');}
 await page.getByRole('button',{name:'Release registry',exact:false}).click();await page.getByRole('heading',{name:'Release registry',exact:true}).waitFor();await scan('releases');
 await page.getByRole('button',{name:'Case inbox',exact:false}).click();await page.setViewportSize({width:390,height:844});await scan('mobile-inbox');
 assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1)),'mobile overflow');
 await page.setViewportSize({width:1280,height:800});await page.evaluate(()=>document.documentElement.style.fontSize='200%');await scan('text-zoom-200-percent');
 assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1)),'200 percent text zoom horizontal overflow');
} finally {
 const output=new URL('../artifacts/accessibility/',import.meta.url);await fs.mkdir(output,{recursive:true});
 await fs.writeFile(new URL('axe-results.json',output),JSON.stringify({generated_at:new Date().toISOString(),standards:['wcag2a','wcag2aa','wcag21aa','wcag22aa'],scans,keyboard_checks:['Enter opens new case','Escape closes dialog','focus returns to invoking button'],limitation:'Automated checks and limited keyboard checks do not establish complete WCAG conformance or replace assistive-technology user testing.'},null,2));
 await browser.close();
}
assert.equal(scans.reduce((n,s)=>n+s.violations.length,0),0,'Automated accessibility violations remain');
