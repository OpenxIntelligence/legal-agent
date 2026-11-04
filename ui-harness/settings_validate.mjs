// Real-browser settings acceptance against a disposable fake live workshop. No model calls.
// Drives chromium headless-shell over CDP using only Node builtins
// (fetch, WebSocket, child_process). No playwright/selenium needed.
// Usually run via ui-harness/browser_run.sh; direct usage:
//   node ui-harness/matter_validate.mjs <baseUrl> <debugPort> <shotDir> <shellBin> <profileDir>
// Requires a real loopback listener + an unsandboxed browser launch
// (see README.md); exits 1 on the first failed check group.
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync, readFileSync, statSync, realpathSync } from "node:fs";

const [base, debugPort, shotDir, shellBin, profileDir, fixtureRoot, demoBase] = process.argv.slice(2);
// Never exercise credential writes against a real workshop.
if (!fixtureRoot || !/legal-agent-settings-browser\./.test(fixtureRoot)) throw Error('Owned settings fixture required');
const preflight = await (await fetch(`${base}/api/info`)).json();
if (realpathSync(preflight.workshop) !== realpathSync(fixtureRoot) || preflight.isolated) throw Error('Wrong settings fixture server');
mkdirSync(shotDir, { recursive: true });
const results = [];
function report(name, status, detail = "") {
  results.push({ name, status, detail: String(detail).slice(0, 300) });
  console.log(`${status} - ${name}${detail ? `: ${String(detail).slice(0, 200)}` : ""}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(fn, timeoutMs, what) {
  const t0 = Date.now();
  let last = "";
  for (;;) {
    try {
      const v = await fn();
      if (v) return v;
      last = String(v);
    } catch (e) { last = e.message; }
    if (Date.now() - t0 > timeoutMs) throw new Error(`timeout waiting for ${what} (last: ${last})`);
    await sleep(250);
  }
}

// --- launch browser ---
const shell = spawn(shellBin, [
  "--headless", `--remote-debugging-port=${debugPort}`,
  "--no-first-run", "--no-default-browser-check",
  `--user-data-dir=${profileDir}`, "--disable-search-engine-choice-screen",
  "about:blank",
], { stdio: ["ignore", "pipe", "pipe"] });
let shellErr = "";
shell.stderr.on("data", (d) => { shellErr += d.toString().slice(-2000); });
process.on("exit", () => { try { shell.kill("SIGKILL"); } catch {} });

let wsUrl;
try {
  const targets = await waitFor(async () => {
    const r = await fetch(`http://127.0.0.1:${debugPort}/json/list`);
    if (!r.ok) return null;
    const list = await r.json();
    const page = list.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
    return page ? page.webSocketDebuggerUrl : null;
  }, 30000, "debug target");
  wsUrl = targets;
  report("browser launches, CDP reachable", "ok");
} catch (e) {
  report("browser launches, CDP reachable", "FAIL", `${e.message} :: ${shellErr.slice(-300)}`);
  shell.kill("SIGKILL");
  process.exit(1);
}

const ws = new WebSocket(wsUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let msgId = 0;
const pending = new Map();
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) {
    const { res, rej } = pending.get(m.id);
    pending.delete(m.id);
    if (m.error) rej(new Error(JSON.stringify(m.error).slice(0, 300)));
    else res(m.result);
  }
};
function send(method, params = {}) {
  const id = ++msgId;
  return new Promise((res, rej) => {
    pending.set(id, { res, rej });
    ws.send(JSON.stringify({ id, method, params }));
    setTimeout(() => { if (pending.has(id)) { pending.delete(id); rej(new Error(`CDP timeout ${method}`)); } }, 20000);
  });
}
async function evaluate(js) {
  const r = await send("Runtime.evaluate", { expression: js, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(`page exception: ${JSON.stringify(r.exceptionDetails).slice(0, 300)}`);
  return r.result?.value;
}
async function reloadPage() {
  const token=Date.now()+Math.random();
  await evaluate(`window.__matterHarnessReload=${token}`);
  await send('Page.reload');
  await waitFor(()=>evaluate(`window.__matterHarnessReload!==${token} && document.readyState==='complete'`),15000,'new document after reload');
}
async function shot(name) {
  const r = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(`${shotDir}/${name}.png`, Buffer.from(r.data, "base64"));
}
await send("Page.enable");
await send("Runtime.enable");

async function step(name, fn) {
  try { const detail = await fn(); report(name, "ok", detail ?? ""); }
  catch (e) { report(name, "FAIL", e.message); }
}


function ensure(value,message){if(!value)throw Error(message);}
const tokens=['settings-fixture-initial-token','settings-fixture-saved-token','settings-fixture-replacement-token','settings-fixture-navigation-token'];
const envPath=`${fixtureRoot}/agent/.env`;
async function openSettings(url=base){
  await send('Page.navigate',{url:`${url}/#/settings`});
  await waitFor(()=>evaluate(`document.querySelector('#settingsKeyStatus')`),15000,'settings controls');
}
async function submitToken(token){
  await evaluate(`document.querySelector('#settingsApiKey').value=${JSON.stringify(token)};document.querySelector('#settingsKeyForm').requestSubmit()`);
  ensure(await evaluate(`document.querySelector('#settingsApiKey').value===''`),'submitted credential retained in field');
  await waitFor(()=>evaluate(`!document.querySelector('#settingsSave').disabled && document.querySelector('#settingsNotice').textContent.includes('Key saved locally')`),10000,'confirmed save');
}
async function noSecretLeaks(){
  const leaks=await evaluate(`(()=>{const tokens=${JSON.stringify(tokens)};const surfaces=[document.body.textContent,JSON.stringify({...localStorage}),JSON.stringify({...sessionStorage}),location.href,document.querySelector('#settingsView')?.innerHTML||''];return surfaces.some(s=>tokens.some(t=>s.includes(t)));})()`);
  ensure(!leaks,'credential leaked into rendered status, HTML, URL, or browser storage');
  const status=await(await fetch(`${base}/api/settings`)).text();
  ensure(!tokens.some(t=>status.includes(t)),'API returned saved credential');
}
await step('live fixture status is write-only and never prefills the credential',async()=>{
  await openSettings();
  ensure(await evaluate(`!document.querySelector('#settingsApiKey').disabled && document.querySelector('#settingsApiKey').type==='password' && document.querySelector('#settingsApiKey').value===''`),'live key field missing/locked/prefilled');
  await noSecretLeaks();
});
await step('execution capabilities show current boundaries without claiming isolation',async()=>{
  const status=await(await fetch(`${base}/api/settings`)).json();
  ensure(status.execution.environment_policy==='allowlist-v1' && status.execution.sandboxed===false,'unexpected execution contract');
  const text=await evaluate(`document.querySelector('.settings-capabilities')?.textContent || ''`);
  ensure(text.includes('ambient credentials are omitted') && text.includes('ownership survives server restarts') && text.includes('explicit recovery') && text.includes('No external databases connected') && text.includes('No external telemetry export'),'capability limits absent');
  ensure(await evaluate(`document.querySelector('#settingsView').textContent.includes('OS sandbox not active')`),'sandbox limitation missing');
});
await step('blank and invalid keys are rejected without altering saved credentials',async()=>{
  await evaluate(`document.querySelector('#settingsKeyForm').requestSubmit()`);
  await waitFor(()=>evaluate(`document.querySelector('#settingsNotice').textContent.includes('Enter an API key')`),5000,'blank validation');
  const before=readFileSync(envPath,'utf8');
  await evaluate(`document.querySelector('#settingsApiKey').value='invalid token with spaces';document.querySelector('#settingsKeyForm').requestSubmit()`);
  await waitFor(()=>evaluate(`document.querySelector('#settingsNotice').textContent.includes('single API token')`),10000,'actual400 validation');
  ensure(readFileSync(envPath,'utf8')===before,'invalid input changed actual env');
  ensure(await evaluate(`document.querySelector('#settingsApiKey').value===''`),'error retained key in field');
});
await step('real save and replacement survive status readback and reload',async()=>{
  await submitToken(tokens[1]);ensure(readFileSync(envPath,'utf8').includes(tokens[1]),'actual file did not persist key');
  ensure((statSync(envPath).mode&0o777)===0o600,'credential file not owner-only');
  await submitToken(tokens[2]);
  const stored=readFileSync(envPath,'utf8');ensure(stored.includes(tokens[2])&&!stored.includes(tokens[1]),'replacement did not replace persisted key');
  await reloadPage();await waitFor(()=>evaluate(`document.querySelector('#settingsKeyStatus')?.textContent.includes('Key configured')`),15000,'saved status reload');
  ensure(await evaluate(`document.querySelector('#settingsApiKey').value===''`),'reload prefilled key');await noSecretLeaks();
});
await step('removal requires confirmation, supports cancel, and actually removes access',async()=>{
  const before=readFileSync(envPath,'utf8');
  await evaluate(`document.querySelector('#settingsRemove').click()`);
  ensure(await evaluate(`!document.querySelector('#settingsRemoval').hidden`),'remove did not show confirmation');
  ensure(readFileSync(envPath,'utf8')===before,'opening confirmation removed key');
  await evaluate(`document.querySelector('#settingsCancelRemove').click()`);
  ensure(await evaluate(`document.querySelector('#settingsRemoval').hidden`),'cancel did not close confirmation');
  ensure(readFileSync(envPath,'utf8')===before,'cancel changed file');
  await evaluate(`document.querySelector('#settingsRemove').click();document.querySelector('#settingsConfirmRemove').click()`);
  await waitFor(()=>evaluate(`document.querySelector('#settingsKeyStatus').textContent.includes('No API key configured')`),10000,'actual removal readback');
  ensure(!tokens.some(t=>readFileSync(envPath,'utf8').includes(t)),'removed key remains in env');
  ensure(await evaluate(`document.activeElement===document.querySelector('#settingsApiKey')`),'successful removal stranded keyboard focus');
});
await step('navigation immediately clears an unsent credential and never restores it',async()=>{
  await evaluate(`document.querySelector('#settingsApiKey').value=${JSON.stringify(tokens[3])};location.hash='#/home'`);
  await waitFor(()=>evaluate(`document.querySelector('#settingsView').hidden && !document.querySelector('#settingsApiKey')`),10000,'settings teardown');
  await evaluate(`location.hash='#/settings'`);
  await waitFor(()=>evaluate(`document.querySelector('#settingsApiKey')`),10000,'returned settings');
  ensure(await evaluate(`document.querySelector('#settingsApiKey').value===''`),'navigation restored unsent key');await noSecretLeaks();
});
await step('403 and409 errors are actionable, redacted, and never persist test secrets',async()=>{
  for(const code of [403,409]){
    const before=readFileSync(envPath,'utf8');
    await evaluate(`window.__settingsOriginal=window.fetch;window.fetch=async function(url,opts){if(opts?.method==='POST'&&String(url)==='/api/settings/key')return new Response('settings-fixture-saved-token',{status:${code}});return window.__settingsOriginal.apply(this,arguments);};document.querySelector('#settingsApiKey').value=${JSON.stringify(tokens[1])};document.querySelector('#settingsKeyForm').requestSubmit();`);
    try{
      await waitFor(()=>evaluate(`document.querySelector('#settingsNotice').getAttribute('role')==='alert' && !document.querySelector('#settingsSave').disabled`),10000,`mock${code} error`);
      const expected=code===403?'Refresh Settings':'key change could not be confirmed';
      ensure(await evaluate(`document.querySelector('#settingsNotice').textContent.includes(${JSON.stringify(expected)})`),`${code} guidance missing`);
      ensure(await evaluate(`document.querySelector('#settingsApiKey').value===''`),`${code} retained credential`);
      ensure(readFileSync(envPath,'utf8')===before,'mock error changed file');await noSecretLeaks();
    }finally{await evaluate(`window.fetch=window.__settingsOriginal`);}
  }
});
await step('desktop1280 and mobile390 Settings have no horizontal overflow',async()=>{
  await evaluate(`document.querySelector('#settingsRefresh').focus();document.querySelector('#settingsRefresh').click()`);
  await waitFor(()=>evaluate(`document.querySelector('#settingsRefresh')===document.activeElement && document.querySelector('#settingsKeyStatus')?.textContent.includes('No API key configured')`),10000,'refresh status keyboard focus');
  for(const [width,height,name] of [[1280,800,'settings-desktop'],[390,844,'settings-mobile']]){
    await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<500});
    await evaluate(`scrollTo(0,0)`);await sleep(200);
    ensure(await evaluate(`document.documentElement.scrollWidth<=innerWidth+1`),`${width}px horizontal overflow`);
    ensure(await evaluate(`document.querySelector('#settingsApiKey').getBoundingClientRect().width>100`),'credential field collapsed');
    await shot(name);
  }
});
await step('synthetic demo cannot edit or save credentials',async()=>{
  await openSettings(demoBase);
  ensure(await evaluate(`document.querySelector('#settingsApiKey').disabled && document.querySelector('#settingsSave').disabled && document.querySelector('#settingsRemove').disabled`),'demo edit controls enabled');
  const status=await(await fetch(`${demoBase}/api/settings`)).json();
  const response=await fetch(`${demoBase}/api/settings/key`,{method:'POST',headers:{'Content-Type':'application/json','Origin':demoBase,'X-Legal-Agent-CSRF':status.csrf_token},body:JSON.stringify({api_key:tokens[1]})});
  ensure(response.status===403,'demo backend accepted key edit');await shot('settings-demo-mobile');
});
const fails=results.filter(r=>r.status==='FAIL');
writeFileSync(`${shotDir}/settings-results.json`,JSON.stringify(results,null,2)+'\n');
console.log(`\nSETTINGS SUMMARY: ${results.length-fails.length}/${results.length} ok, ${fails.length} failed`);
ws.close();shell.kill('SIGKILL');process.exit(fails.length?1:0);
