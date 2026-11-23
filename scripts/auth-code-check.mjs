import assert from 'node:assert/strict';
import {config,installProvider,password}from'./neon-provider-fixture.mjs';
assert.ok(process.argv[2],'Explicit scratch output required');
let provider;
const email='browser-fixture@example.invalid';
async function check(page,kind){
 if(kind==='resend'){
  await page.getByRole('button',{name:'Verify your email',exact:true}).click();await page.getByLabel('Email',{exact:true}).fill(email);
  let body;page.on('request',request=>{if(new URL(request.url()).pathname==='/auth/email-otp/send-verification-otp')body=request.postDataJSON();});
  await page.getByRole('button',{name:/Resend verification (?:email|code)/}).click();await page.locator("#notice").filter({hasText:'If verification is available'}).waitFor();
  assert.ok(provider.calls.some(call=>call.path==='/auth/email-otp/send-verification-otp'&&call.method==='POST'),'Resend must call the provider verification-code endpoint');
  assert.deepEqual(body,{email,type:'email-verification'});assert.ok(!provider.calls.some(call=>call.path==='/auth/send-verification-email'));
 }else if(kind==='signup'){
  await page.getByRole('button',{name:'Create an account',exact:true}).click();await page.getByLabel('Name',{exact:true}).fill('Fictional test user');await page.getByLabel('Email',{exact:true}).fill(email);await page.getByLabel('Password',{exact:true}).fill(password);await page.getByRole('button',{name:'Create account',exact:true}).click();await page.getByLabel('Verification code',{exact:true}).waitFor();
  assert.doesNotMatch(await page.locator("#notice").innerText(),/Account created/i);assert.match(await page.locator("#notice").innerText(),/already registered/i);
  assert.equal(await page.getByRole('button',{name:'Back to sign in',exact:true}).isVisible(),true);
 }else{
  await page.getByRole('button',{name:'Verify your email',exact:true}).click();await page.getByLabel('Email',{exact:true}).fill(email);await page.getByLabel('Verification code',{exact:true}).fill('123456');
  assert.match(await page.getByRole('form',{name:'Workspace account'}).innerText(),/Already registered/i);assert.equal(await page.getByRole('button',{name:'Forgot password?',exact:true}).count(),1);await page.getByRole('button',{name:'Forgot password?',exact:true}).click();await page.getByRole('button',{name:'Send reset code',exact:true}).waitFor();assert.equal(await page.getByLabel('Email',{exact:true}).inputValue(),email);await page.getByRole('button',{name:'Back to sign in',exact:true}).click();await page.getByLabel('Password',{exact:true}).waitFor();assert.equal(await page.getByLabel('Password',{exact:true}).inputValue(),'');
 }
 await provider.assertNoPersistence();
}
import{startHarness,scenario,finishHarness}from'./ui-race-check.mjs';
await startHarness();try{for(const kind of ['resend','signup','registered'])await scenario('Verification-code UX '+kind,'fixture',async({page})=>{await page.getByLabel('Email',{exact:true}).waitFor();await check(page,kind);},{signedIn:false,prepare:async({page,setReply})=>{setReply('GET','/v1/auth/config',()=>({body:config}));provider=await installProvider(page,{signupUnverified:true});}});}finally{await finishHarness(process.argv[2],3);}
