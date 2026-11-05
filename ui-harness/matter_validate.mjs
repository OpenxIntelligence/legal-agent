// Zero-dependency real-browser validation for the legal-agent matter intake UI.
// Drives chromium headless-shell over CDP using only Node builtins
// (fetch, WebSocket, child_process). No playwright/selenium needed.
// Usually run via ui-harness/browser_run.sh; direct usage:
//   node ui-harness/matter_validate.mjs <baseUrl> <debugPort> <shotDir> <shellBin> <profileDir>
// Requires a real loopback listener + an unsandboxed browser launch
// (see README.md); exits 1 on the first failed check group.
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync, readFileSync, cpSync } from "node:fs";

const [base, debugPort, shotDir, shellBin, profileDir, fixtureRoot] = process.argv.slice(2);
// Refuse to mutate a live workspace, including when a requested test port was occupied.
const preflight = await (await fetch(`${base}/api/info`)).json();
if (preflight.isolated !== true) throw new Error('Matter acceptance requires an isolated synthetic server.');
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


const question = 'Fictional harness matter: does the supplier email support a June 12 delivery commitment?';
const followup = 'Please challenge the date and distinguish a promise from an estimate.';
const sourceText = 'FICTIONAL_RECORD_ALPHA\nSupplier email: We estimate delivery on June 12; this is not a guaranteed date.\n';
let matterId = '', matterHash = '', secondMatterId = '';
async function visibleClick(selector) {
  const rect=await waitFor(()=>evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});if(!el||!el.getClientRects().length||el.disabled)return null;el.scrollIntoView({block:'center',behavior:'instant'});const r=el.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`),10000,`visible control ${selector}`);
  ensure(rect,`control is hidden, missing, or disabled: ${selector}`);
  await send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...rect});
  await send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...rect});
}
async function focusSurface(surface) {
  const id=surface==='conversation'?'matterReply':'matterResultsHeading';
  await waitFor(()=>evaluate(`document.querySelector('#${id}')?.getClientRects().length`),10000,`visible ${surface} surface`);
  await evaluate(`{const target=document.querySelector('#${id}');target.scrollIntoView({block:'center',behavior:'instant'});if(!target.hasAttribute('tabindex')&&!target.matches('textarea'))target.setAttribute('tabindex','-1');target.focus();}`);
  ensure(await evaluate(`!document.querySelector('#matterResults').hidden && !document.querySelector('#matterConversationPanel').hidden`),'workspace hid conversation or findings');
}
async function typeDraft(value) {
  await evaluate(`{const reply=document.querySelector('#matterReply');reply.value=${JSON.stringify(value)};reply.dispatchEvent(new Event('input',{bubbles:true}));}`);
}
function ensure(value, reason) { if (!value) throw new Error(reason); }
async function mobileShot(name) {
  await send('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:true});
  await sleep(250);
  const overflow=await evaluate('document.documentElement.scrollWidth-innerWidth');
  await shot(name);
  ensure(overflow<=1, `390px viewport overflows by ${overflow}px`);
  await send('Emulation.setDeviceMetricsOverride', {width:1440,height:1000,deviceScaleFactor:1,mobile:false});
}
await step('home composer is question-led, honest, and mobile usable', async()=>{
  await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await send('Page.navigate',{url:base});
  await waitFor(()=>evaluate(`document.querySelector('#matterQuestion') && /Demo workspace/.test(document.querySelector('#intakeMode').textContent)`),15000,'intake composer');
  ensure(await evaluate(`!!document.querySelector('#prepareMatter') && /No model call/.test(document.querySelector('#intakeForm').textContent)`),'missing no-model-call explanation');
  await shot('matter-01-home-desktop');await mobileShot('matter-02-home-mobile');
});
await step('task starters preserve drafts and never submit automatically',async()=>{
  ensure(await evaluate(`document.querySelector('#matterList').textContent.includes('No investigations yet')`),'empty saved-work state missing');
  await evaluate(`document.querySelector('[data-intake-task="timeline"]').click()`);
  ensure(await evaluate(`document.querySelector('#matterQuestion').value.includes('Build a chronology') && document.querySelector('#matterDeliverable').value==='chronology' && !location.hash.startsWith('#/matter/')`),'starter did not fill question and output without submitting');
  await reloadPage();
  await waitFor(()=>evaluate(`document.querySelector('#matterQuestion')?.value.includes('Build a chronology')`),15000,'starter draft persistence');
  ensure(await evaluate(`{const original=window.confirm;window.confirm=()=>false;document.querySelector('[data-intake-task="contract"]').click();window.confirm=original;document.querySelector('#matterQuestion').value.includes('Build a chronology');}`),'starter silently replaced a nonempty draft');
  await evaluate(`document.querySelector('#clearIntake').click()`);
  ensure(await evaluate(`document.querySelector('#matterQuestion').value===''`),'clear draft did not clear starter');
});
await step('dropzone uses real files and preserves validation',async()=>{
  await evaluate(`{const dt=new DataTransfer();dt.items.add(new File(['Fictional drop text'],'starter-drop.txt',{type:'text/plain'}));dt.items.add(new File(['bad'],'bad-upload.exe'));document.querySelector('#matterDropzone').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));}`);
  ensure(await evaluate(`document.querySelector('#intakeFiles').textContent.includes('starter-drop.txt') && !document.querySelector('#intakeFiles').textContent.includes('bad-upload.exe') && document.querySelector('#intakeStatus').textContent.includes('choose a .txt')`),'drop bypassed file validation or lost valid file');
  ensure(await evaluate(`document.querySelector('#matterFiles').getAttribute('type')==='file' && !document.querySelector('#matterFiles').hidden`),'native keyboard file chooser unavailable');
  await evaluate(`document.querySelector('#intakeFiles [data-remove]').click()`);
  ensure(await evaluate(`document.querySelector('#intakeFiles').children.length===0 && document.activeElement.id==='matterFiles'`),'remove did not restore keyboard focus to file input');
});
await step('intake accepts ordinary filenames and detects normalized collisions without losing files',async()=>{
  await evaluate(`{const dt=new DataTransfer();dt.items.add(new File(['Fictional signed agreement'],'Lease Agreement (signed).txt',{type:'text/plain'}));document.querySelector('#matterDropzone').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));}`);
  ensure(await evaluate(`document.querySelector('#intakeFiles').children.length===1 && document.querySelector('#intakeFiles').textContent.includes('Lease Agreement (signed).txt')`),'ordinary filename rejected or lost display name');
  await evaluate(`{const dt=new DataTransfer();dt.items.add(new File(['Fictional colliding record'],'Lease_Agreement__signed_.txt',{type:'text/plain'}));document.querySelector('#matterDropzone').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));}`);
  ensure(await evaluate(`document.querySelector('#intakeFiles').children.length===1 && /already|duplicate|collision|conflicts|same/i.test(document.querySelector('#intakeStatus').textContent)`),'normalized collision accepted or original selection lost');
  await evaluate(`document.querySelector('#intakeFiles [data-remove]').click()`);
});
await step('saved investigations search distinguishes no matches',async()=>{
  const fixture=await(await fetch(`${base}/api/matters`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:'Fictional search fixture for intake validation.',documents:[{filename:'search-record.txt',text:'Fictional record'}]})})).json();
  ensure(!!fixture.id,'search fixture not saved');
  await evaluate(`MatterUI.loadMatters()`);
  ensure(await evaluate(`{const search=document.querySelector('#matterSearch');search.value='zzzz-no-saved-match';search.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('#matterList').textContent.includes('No matching investigations');}`),'no-match state confused with empty workspace');
  ensure(await evaluate(`{const search=document.querySelector('#matterSearch');search.value='search fixture';search.dispatchEvent(new Event('input',{bubbles:true}));document.querySelectorAll('#matterList .saved-matter').length===1 && document.querySelector('#matterList').textContent.includes('1 document');}`),'search did not find saved question or show document receipt count');
  await evaluate(`document.querySelector('#matterSearch').value='';document.querySelector('#matterSearch').dispatchEvent(new Event('input',{bubbles:true}))`);
});
await step('question and selected text file create a persisted matter',async()=>{
  await evaluate(`document.querySelector('#matterQuestion').value=${JSON.stringify(question)};document.querySelector('#matterQuestion').dispatchEvent(new Event('input',{bubbles:true}));
    {const dt=new DataTransfer();dt.items.add(new File([${JSON.stringify(sourceText)}],'supplier-email.txt',{type:'text/plain'}));const input=document.querySelector('#matterFiles');input.files=dt.files;input.dispatchEvent(new Event('change',{bubbles:true}));}`);
  ensure(await evaluate(`document.querySelector('#intakeFiles').textContent.includes('supplier-email.txt')`),'selected file not shown');
  await evaluate(`document.querySelector('#intakeForm').requestSubmit()`);
  await waitFor(()=>evaluate(`location.hash.startsWith('#/matter/') && document.querySelector('#matterTitle')?.textContent.includes('Fictional harness matter')`),25000,'saved matter');
  matterHash=await evaluate('location.hash');matterId=decodeURIComponent(matterHash.split('/')[2]);
  const saved=await (await fetch(`${base}/api/matters/${matterId}`)).json();
  ensure(saved.question===question,'saved question differs');ensure(saved.documents.length===1,'document receipt missing');
  ensure(saved.documents[0].id==='supplier-email','wrong document receipt');
  ensure(saved.runs.length===0,'preparation unexpectedly ran agent');
  await shot('matter-03-prepared-desktop');
});
await step('short-window composer sends with Enter and keeps Shift+Enter as a newline',async()=>{
  const keyboardMessage='Fictional keyboard submission: please distinguish an estimate from a promise.';
  const count=async()=>{const saved=await(await fetch(`${base}/api/matters/${matterId}`)).json();return saved.messages.length;};
  const before=await count();
  await send('Emulation.setDeviceMetricsOverride',{width:1280,height:680,deviceScaleFactor:1,mobile:false});
  try{
    await visibleClick('#matterReply');await typeDraft('Fictional line one');
    await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,modifiers:8,text:'\r'});
    await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,modifiers:8});
    ensure(await evaluate(`document.querySelector('#matterReply').value==='Fictional line one\\n'`),'Shift+Enter did not insert a newline');
    ensure(await count()===before,'Shift+Enter submitted the message');
    await typeDraft(keyboardMessage);
    ensure(await evaluate(`{const button=document.querySelector('#sendMatterMessage'),r=button.getBoundingClientRect(),chat=document.querySelector('#matterConversationPanel').getBoundingClientRect(),work=document.querySelector('.workspace-product').getBoundingClientRect();!button.disabled && r.top>=77 && r.bottom<=innerHeight && chat.right<=work.left+1;}`),'Send is disabled/offscreen or workspace is not side by side at1280x680');
    await shot('matter-17-short-window-send');
    await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,text:'\r'});
    await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
    await waitFor(async()=>{const saved=await(await fetch(`${base}/api/matters/${matterId}`)).json();return saved.messages.some(message=>message.body===keyboardMessage);},7000,'native Enter persists message');
    ensure((await(await fetch(`${base}/api/matters/${matterId}`)).json()).messages.filter(message=>message.body===keyboardMessage).length===1,'Enter submission did not persist exactly once');
    ensure(await evaluate(`document.querySelector('#matterReply').value===''`),'successful Enter did not clear submitted draft');
  }finally{await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});}
});
await step('demo has no start control or invented model output; server rejects start',async()=>{
  ensure(await evaluate(`!document.querySelector('#runMatter') && /cannot research or answer/.test(document.querySelector('#matterResults').textContent) && /legal-agent --live/.test(document.querySelector('#matterResults').textContent)`),'demo start not visibly disabled');
  ensure(await evaluate(`![...document.querySelectorAll('.matter-message .message-meta')].some(el=>el.textContent.includes('Model-generated')) && !/MODEL DRAFT/.test(document.querySelector('#matterResults').textContent)`),'invented model answer shown');
  const response=await fetch(`${base}/api/matters/${matterId}/start`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{"turns":1}'});
  ensure(response.status===409,'demo start endpoint accepted investigation');
  ensure(/disabled/.test((await response.json()).error||''),'start rejection lacks explanation');
});
await step('reload and follow-up retain conversation without generating analysis',async()=>{
  await reloadPage();
  await waitFor(()=>evaluate(`document.querySelector('#matterConversation')?.textContent.includes(${JSON.stringify(question)})`),15000,'reloaded conversation');
  await focusSurface('conversation');
  await evaluate(`document.querySelector('#matterReply').value=${JSON.stringify(followup)};document.querySelector('#matterReplyForm').requestSubmit()`);
  await waitFor(()=>evaluate(`document.querySelector('#matterConversation').textContent.includes(${JSON.stringify(followup)})`),10000,'saved followup');
  await reloadPage();
  await waitFor(()=>evaluate(`document.querySelector('#matterConversation')?.textContent.includes(${JSON.stringify(followup)})`),15000,'persistent followup');
  const saved=await(await fetch(`${base}/api/matters/${matterId}`)).json();
  ensure(saved.messages.some(m=>m.kind==='followup'&&m.body===followup),'followup not on disk');ensure(saved.runs.length===0,'followup unexpectedly started agent');
  ensure(!saved.messages.some(m=>m.kind==='agent'||m.kind==='model'),'fake AI response persisted');
  await mobileShot('matter-04-conversation-mobile');
});
await step('scoped document source opens with actual supplied evidence',async()=>{
  await waitFor(()=>evaluate(`document.querySelector('[data-source-doc="supplier-email"]')`),15000,'document inspect control');
  await evaluate(`document.querySelector('[data-source-doc="supplier-email"]').click()`);
  await waitFor(()=>evaluate(`document.querySelector('#matterSourceViewer')?.textContent.includes('FICTIONAL_RECORD_ALPHA')`),15000,'extracted source');
  ensure(await evaluate(`document.querySelector('#matterConversation').textContent.includes(${JSON.stringify(question)})`),'source inspection lost claim context');
  await shot('matter-05-evidence-desktop');await mobileShot('matter-06-evidence-mobile');
  const second=await(await fetch(`${base}/api/matters`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:'Fictional unrelated matter with a different supplier.',documents:[{filename:'other-record.txt',text:'FICTIONAL_RECORD_BETA'}]})})).json();
  ensure(second.id&&second.id!==matterId,'second matter not created');secondMatterId=second.id;
  const response=await fetch(`${base}/api/matters/${second.id}/documents/supplier-email/source`);
  ensure(!response.ok,'second matter can read first matter source');
  ensure(!(await response.text()).includes('FICTIONAL_RECORD_ALPHA'),'cross-matter content disclosed');
});
await step('source inspector stays accessible in the workspace and restores its invoking control',async()=>{
  await focusSurface('findings');
  await visibleClick('#matterRail [data-source-doc="supplier-email"]');
  await waitFor(()=>evaluate(`document.querySelector('#matterSourceBody')?.textContent.includes('FICTIONAL_RECORD_ALPHA')`),10000,'source content in evidence rail');
  ensure(await evaluate(`{const source=document.querySelector('#matterSourceViewer').getBoundingClientRect(),desk=document.querySelector('#matterView').getBoundingClientRect();source.left>=desk.left-1 && source.right<=desk.right+1 && source.width>=200 && !document.querySelector('#matterResults').hidden && !document.querySelector('#matterConversationPanel').hidden;}`),'desktop source leaves workspace, hides a panel, or is too narrow');
  await shot('matter-12-workspace-source-desktop');
  await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await evaluate(`document.querySelector('#matterSourceViewer').scrollIntoView({block:'start',behavior:'instant'})`);await sleep(150);
  ensure(await evaluate(`document.documentElement.scrollWidth-innerWidth<=1`),'mobile source causes horizontal overflow');await shot('matter-13-workspace-source-mobile');
  await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await visibleClick('#closeMatterSource');
  ensure(await evaluate(`document.querySelector('#matterSourceViewer').hidden && document.activeElement.matches('#matterRail [data-source-doc="supplier-email"]')`),'close source did not restore invoking control focus');
});
await step('post-intake file upload normalizes its name, persists and remains matter scoped',async()=>{
  ensure(fixtureRoot&&/legal-agent-matter-browser\./.test(fixtureRoot),'upload requires owned synthetic fixture root');
  const filename='later supplier record.txt',filePath=`${fixtureRoot}/${filename}`,text='FICTIONAL_POST_INTAKE_RECORD: later supplier clarification, no guaranteed delivery date.';
  writeFileSync(filePath,text);
  const {root}=await send('DOM.getDocument');
  const {nodeId}=await send('DOM.querySelector',{nodeId:root.nodeId,selector:'#matterAddFiles'});
  ensure(nodeId,'document upload input missing');
  await send('DOM.setFileInputFiles',{nodeId,files:[filePath]});
  await waitFor(()=>evaluate(`document.querySelector('#matterUploadStatus').textContent.includes('1 document uploaded')`),15000,'real post-intake document upload');
  const saved=await(await fetch(`${base}/api/matters/${matterId}`)).json();
  ensure(saved.documents.some(doc=>(doc.document_id||doc.id)==='later_supplier_record'),'normalized attachment not persisted');
  ensure(saved.documents.some(doc=>(doc.document_id||doc.id)==='supplier-email'),'existing attachment disappeared');
  await reloadPage();
  await waitFor(()=>evaluate(`document.querySelector('#matterRail [data-source-doc="later_supplier_record"]')`),15000,'uploaded document after reload');
  await visibleClick('#matterRail [data-source-doc="later_supplier_record"]');
  await waitFor(()=>evaluate(`document.querySelector('#matterSourceBody')?.textContent.includes('FICTIONAL_POST_INTAKE_RECORD')`),10000,'uploaded evidence extracted source');
  const response=await fetch(`${base}/api/matters/${secondMatterId}/documents/later_supplier_record/source`);
  ensure(!response.ok&&!(await response.text()).includes('FICTIONAL_POST_INTAKE_RECORD'),'uploaded document leaked across matters');
  await shot('matter-18-post-intake-upload');await visibleClick('#closeMatterSource');
});
await step('home resumes the saved matter after reload',async()=>{
  await evaluate(`location.hash='#/home'`);
  await waitFor(()=>evaluate(`document.querySelector('.saved-matter[href="${matterHash}"]')`),10000,'saved matter list');
  await evaluate(`document.querySelector('.saved-matter[href="${matterHash}"]').click()`);
  await waitFor(()=>evaluate(`!document.querySelector('#matterView').hidden && document.querySelector('#matterConversation')?.textContent.includes(${JSON.stringify(followup)})`),10000,'resumed matter');
});
await step('conversation and findings stay visible with keyboard access and independent matter drafts',async()=>{
  await focusSurface('conversation');
  ensure(await evaluate(`{const chat=document.querySelector('#matterConversationPanel').getBoundingClientRect(),findings=document.querySelector('#matterResults').getBoundingClientRect();chat.width>=290 && findings.width>=300 && chat.right<=findings.left+1;}`),'desktop conversation and findings are not simultaneously side by side');
  ensure(await evaluate(`!document.querySelector('#workspaceFindingsTab') && !document.querySelector('#workspaceConversationTab')`),'exclusive workspace tabs remain');
  await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});
  await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});
  ensure(await evaluate(`document.activeElement.id==='sendMatterMessage'`),'keyboard cannot reach Save message from the composer');
  const firstDraft='Unsent question for ALPHA only.',secondDraft='Unsent question for BETA only.';
  await typeDraft(firstDraft);
  await reloadPage();
  await waitFor(()=>evaluate(`document.querySelector('#matterReply')?.value===${JSON.stringify(firstDraft)} && !document.querySelector('#matterConversationPanel').hidden && !document.querySelector('#matterResults').hidden`),15000,'reload restores unsent conversation');
  await waitFor(()=>evaluate(`document.querySelector('#matterSwitcher')?.querySelector('option[value="${secondMatterId}"]')`),10000,'second saved matter in switcher');
  await evaluate(`{const switcher=document.querySelector('#matterSwitcher');switcher.value=${JSON.stringify(secondMatterId)};switcher.dispatchEvent(new Event('change',{bubbles:true}));}`);
  await waitFor(()=>evaluate(`location.hash==='#/matter/${secondMatterId}' && document.querySelector('#matterTitle')?.textContent.includes('unrelated')`),15000,'switch second saved matter');
  ensure(await evaluate(`document.querySelector('#matterReply').value==='' && !document.querySelector('#matterResults').hidden && document.querySelector('#matterSourceViewer').hidden`),'second matter inherited first draft or source');
  await focusSurface('conversation');await typeDraft(secondDraft);await focusSurface('findings');
  await evaluate(`{const switcher=document.querySelector('#matterSwitcher');switcher.value=${JSON.stringify(matterId)};switcher.dispatchEvent(new Event('change',{bubbles:true}));}`);
  await waitFor(()=>evaluate(`document.querySelector('#matterReply')?.value===${JSON.stringify(firstDraft)} && !document.querySelector('#matterConversationPanel').hidden`),15000,'first independent draft restored');
  await evaluate(`location.hash='#/home'`);
  await waitFor(()=>evaluate(`document.querySelector('.saved-matter[href="${matterHash}"]')`),10000,'home saved-work list');
  await visibleClick(`.saved-matter[href="${matterHash}"]`);
  await waitFor(()=>evaluate(`!document.querySelector('#matterView').hidden && document.querySelector('#matterReply')?.value===${JSON.stringify(firstDraft)} && !document.querySelector('#matterConversationPanel').hidden`),15000,'home return preserves draft');
  ensure(await evaluate(`MatterWorkspaceStore.read(${JSON.stringify(secondMatterId)}).text===${JSON.stringify(secondDraft)}`),'second draft overwritten by first');
  await typeDraft('');await focusSurface('findings');
  await evaluate(`window.scrollTo(0,Math.max(0,scrollY+document.querySelector('#matterView').getBoundingClientRect().top-90))`);
  await shot('matter-10-workspace-desktop');
  await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await sleep(100);
  ensure(await evaluate(`{const findings=document.querySelector('#matterResults').getBoundingClientRect(),chat=document.querySelector('#matterConversationPanel').getBoundingClientRect();chat.bottom<=findings.top+1 && findings.width>300 && chat.width>300 && !document.querySelector('#matterResults').hidden && !document.querySelector('#matterConversationPanel').hidden && document.documentElement.scrollWidth-innerWidth<=1;}`),'narrow workspace buries conversation below findings or overflows');
  await evaluate(`document.querySelector('#matterResults').scrollIntoView({block:'start',behavior:'instant'})`);await shot('matter-11-workspace-mobile');
  await evaluate(`document.querySelector('#matterConversationPanel').scrollIntoView({block:'start',behavior:'instant'})`);await shot('matter-14-conversation-mobile');
  await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
});
await step('recorded findings open a scoped claim review and return to investigation',async()=>{
  const response=await fetch(`${base}/api/matters/${matterId}/gallery/demo`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  ensure(response.ok,'could not seed explicitly fictional review fixture');
  await reloadPage();
  await waitFor(()=>evaluate(`document.querySelector('#openMatterEvidenceReview')`),15000,'review findings action');
  const reviewDraft='Unsent direction retained while reviewing actual evidence.';
  await focusSurface('conversation');await typeDraft(reviewDraft);await focusSurface('findings');
  // Force the create branch while persisting through the actual server endpoint.
  await evaluate(`{const original=window.fetch;let once=true;window.fetch=async function(url,opts){if(once && !opts?.method && String(url).endsWith('/artifacts')){once=false;return new Response(JSON.stringify({instances:[]}),{status:200,headers:{'Content-Type':'application/json'}});}return original.apply(this,arguments);};document.querySelector('#openMatterEvidenceReview').click();}`);
  await waitFor(()=>evaluate(`location.hash.startsWith('#/artifact/') && document.querySelector('#artifactView')?.textContent.includes('Evidence and claim review')`),20000,'persisted claim review');
  ensure(await evaluate(`new URLSearchParams(location.hash.split('?')[1]).get('matter')===${JSON.stringify(matterId)}`),'lost matter scope');
  ensure(!await evaluate(`document.querySelector('#artifactView').textContent.includes('Loading audit')`),'review stuck loading');
  await shot('matter-07-scoped-review');
  await evaluate(`document.querySelector('#artifactView a[href^="${matterHash}"]').click()`);
  await waitFor(()=>evaluate(`document.querySelector('#matterConversation')?.textContent.includes(${JSON.stringify(followup)})`),15000,'return to investigation');
  ensure(await evaluate(`document.querySelector('#matterReply').value===${JSON.stringify(reviewDraft)} && !document.querySelector('#matterResults').hidden && !document.querySelector('#matterConversationPanel').hidden`),'review return lost draft or simultaneous workspace');
  await focusSurface('conversation');await typeDraft('');await focusSurface('findings');
});
await step('editable review saves a version beside conversation without changing the model memo',async()=>{
  const matter=await(await fetch(`${base}/api/matters/${matterId}`)).json(),run=matter.runs[0].id;
  const runUrl=`${base}/api/matters/${matterId}/runs/${run}`,beforeRun=await(await fetch(runUrl)).json();
  const originalMemo=JSON.stringify(beforeRun.case?.memo);
  await waitFor(()=>evaluate(`document.querySelector('#createWorkspaceReview')?.getClientRects().length`),10000,'explicit editable-review control');
  ensure(await evaluate(`!document.querySelector('[data-editor-row]')`),'editor loaded editable claims before explicit request');
  await evaluate(`window.__editorOriginalFetch=window.fetch;window.__editorModelDispatches=0;window.fetch=async function(url,opts){if(opts?.method==='POST'&&['/start','/resume'].some(suffix=>new URL(String(url),location.href).pathname.endsWith(suffix)))window.__editorModelDispatches++;return window.__editorOriginalFetch.apply(this,arguments);};`);
  try{
    await visibleClick('#createWorkspaceReview');
    await waitFor(()=>evaluate(`document.querySelector('[data-editor-row] textarea')`),15000,'actual saved editable claims');
    const meta=await evaluate(`(()=>{const row=document.querySelector('[data-editor-row]');const link=row.parentElement.querySelector('a[href^="#/artifact/"]');return {row:row.dataset.editorRow,original:row.querySelector('textarea').value,artifact:decodeURIComponent(link.hash.split('/')[3].split('?')[0]),version:Number(document.querySelector('[data-editor-version]').dataset.editorVersion)};})()`);
    ensure(meta.artifact&&meta.version>0,'editor lacks persisted version identity');
    const detailUrl=`${base}/api/matters/${matterId}/runs/${run}/artifacts/${encodeURIComponent(meta.artifact)}`;
    const before=(await(await fetch(detailUrl)).json()).instance;
    const revision=meta.original+' [Fictional harness reviewer correction: delivery is an estimate, not a guarantee.]';
    await evaluate(`{const input=document.querySelector('[data-editor-row] textarea');input.value=${JSON.stringify(revision)};input.dispatchEvent(new Event('input',{bubbles:true}));}`);
    await sleep(3500); // Normal matter polling must not recreate the separate editor.
    ensure(await evaluate(`document.querySelector('[data-editor-row] textarea').value===${JSON.stringify(revision)}`),'polling erased the unsaved review edit');
    await visibleClick('[data-editor-save]');
    await waitFor(()=>evaluate(`Number(document.querySelector('[data-editor-version]')?.dataset.editorVersion)===${before.versions.length+1}`),15000,'new saved review version');
    const after=(await(await fetch(detailUrl)).json()).instance;
    ensure(after.versions.length===before.versions.length+1,'revision did not persist exactly one version');
    ensure(after.rows.find(row=>row.row_id===meta.row)?.assertion===revision,'saved assertion differs from typed revision');
    ensure(JSON.stringify((await(await fetch(runUrl)).json()).case?.memo)===originalMemo,'editing review changed original model memo');
    ensure(await evaluate(`!!document.querySelector('[data-editor-source]') && /Quote checks:/.test(document.querySelector('#matterEditor').textContent)`),'editable claims lack source inspection or check outcomes');
    await visibleClick('[data-editor-source]');
    await waitFor(()=>evaluate(`!document.querySelector('#matterSourceViewer').hidden && document.querySelector('#matterSourceBody')?.textContent`),10000,'editor citation opens scoped source');
    await visibleClick('#closeMatterSource');
    const currentRow=after.rows.find(row=>row.row_id===meta.row),external=revision+' [Fictional second reviewer edit.]';
    const externalSave=await fetch(`${detailUrl}/revise`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({row:meta.row,assertion:external,expected_assertion_version:currentRow.assertion_version})});
    ensure(externalSave.ok,'synthetic second reviewer save failed');
    const unsent=revision+' [Fictional stale tab edit.]';
    await evaluate(`{const input=document.querySelector('[data-editor-row] textarea');input.value=${JSON.stringify(unsent)};input.dispatchEvent(new Event('input',{bubbles:true}));}`);
    await visibleClick('[data-editor-save]');
    await waitFor(()=>evaluate(`document.querySelector('#matterEditor').textContent.includes('changed elsewhere')`),10000,'stale edit conflict');
    ensure(await evaluate(`document.querySelector('[data-editor-row] textarea').value===${JSON.stringify(unsent)} && document.querySelector('[data-editor-save]').disabled`),'conflict lost unsent edit or permits blind retry');
    ensure((await(await fetch(detailUrl)).json()).instance.rows.find(row=>row.row_id===meta.row)?.assertion===external,'stale save overwrote another reviewer');
    await visibleClick('#createWorkspaceReview');
    await waitFor(()=>evaluate(`document.querySelector('[data-editor-row] textarea').dataset.savedAssertion===${JSON.stringify(external)}`),10000,'reload exposes latest saved assertion');
    ensure(await evaluate(`document.querySelector('[data-editor-row] textarea').value===${JSON.stringify(unsent)} && document.querySelector('#matterEditor').textContent.includes(${JSON.stringify(external)})`),'reload lost draft or omitted conflict comparison');
    await evaluate(`window.__exportCreate=URL.createObjectURL;window.__reviewExportText=null;URL.createObjectURL=function(blob){blob.text().then(text=>window.__reviewExportText=text);return window.__exportCreate.call(this,blob);};`);
    try{
      await visibleClick('[data-editor-export]');
      await waitFor(()=>evaluate(`typeof window.__reviewExportText==='string'`),10000,'saved review export content');
      ensure(await evaluate(`window.__reviewExportText.includes('Fictional second reviewer edit.') && !window.__reviewExportText.includes('Fictional stale tab edit.') && window.__reviewExportText.includes('Saved version:') && window.__reviewExportText.includes('Review decision:') && window.__reviewExportText.includes(${JSON.stringify(run)})`),'export omitted saved revision identity/status or included unsent draft');
    }finally{await evaluate(`URL.createObjectURL=window.__exportCreate`);}
    ensure(await evaluate(`window.__editorModelDispatches===0 && !document.querySelector('#matterConversationPanel').hidden && !document.querySelector('#matterResults').hidden`),'editing hid conversation/findings or dispatched model investigation');
    await evaluate(`window.scrollTo(0,Math.max(0,scrollY+document.querySelector('#matterEditor').getBoundingClientRect().top-90))`);await shot('matter-15-editor-desktop');
    await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    await evaluate(`window.scrollTo(0,Math.max(0,scrollY+document.querySelector('#matterEditor').getBoundingClientRect().top-100))`);await sleep(150);
    ensure(await evaluate(`document.documentElement.scrollWidth-innerWidth<=1`),'mobile editable review overflows');await shot('matter-16-editor-mobile');
    await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
    const policy=`${fixtureRoot}/workspace/matters/${matterId}/workspace/quarantine.json`;
    let previousPolicy='{}';try{previousPolicy=readFileSync(policy,'utf8');}catch{}
    try{
      writeFileSync(policy,JSON.stringify({'DEMO-STATUS':{reason:'synthetic editor cache invalidation'}}));
      await evaluate(`MatterUI.refresh()`);
      await waitFor(()=>evaluate(`!!document.querySelector('[data-editor-invalidated="true"]')`),10000,'editor evidence invalidation after source access changes');
      ensure(await evaluate(`!document.querySelector('[data-editor-source]') && !document.querySelector('[data-editor-row]') && !document.querySelector('[data-editor-export]')`),'editor retains cached evidence/check/export after source access change');
    }finally{writeFileSync(policy,previousPolicy);}
    await evaluate(`MatterUI.refresh()`);await visibleClick('#createWorkspaceReview');
    await waitFor(()=>evaluate(`document.querySelector('[data-editor-row] textarea')?.value===${JSON.stringify(unsent)}`),10000,'invalidated editor restores unsent draft after explicit reload');
  }finally{await evaluate(`window.fetch=window.__editorOriginalFetch`);}
});
await step('persisted run history survives polling, reload, review return, and request navigation',async()=>{
  ensure(fixtureRoot&&/legal-agent-matter-browser\./.test(fixtureRoot),'history fixture requires owned ephemeral test root');
  const root=`${fixtureRoot}/workspace/matters/${matterId}`;
  const original='run-demo-audit',latest='run-zz-history-fixture';
  cpSync(`${root}/workspace/runs/${original}`,`${root}/workspace/runs/${latest}`,{recursive:true});
  const progressPath=`${root}/workspace/runs/${latest}/progress.json`;
  const progress=JSON.parse(readFileSync(progressPath,'utf8'));progress.status='needs_input';
  writeFileSync(progressPath,JSON.stringify(progress));
  const record=JSON.parse(readFileSync(`${root}/workspace/case_records/${original}.json`,'utf8'));
  record.run_id=latest;record.requests=[{id:'request-history-fixture',item:'Provide the date of the fictional supplier email.',status:'open',visible_ids:[]}];
  writeFileSync(`${root}/workspace/case_records/${latest}.json`,JSON.stringify(record));
  await evaluate(`location.hash=${JSON.stringify(matterHash)}`);await reloadPage();
  await waitFor(()=>evaluate(`document.querySelector('#matterRunPicker')?.options.length===2 && document.querySelector('#matterRunPicker').value==='run-zz-history-fixture'`),15000,'two actual saved runs');
  ensure(await evaluate(`document.querySelector('.overview-next').textContent.includes('fictional supplier email') && document.querySelector('#matterRequest').value==='request-history-fixture'`),'blocking request not prioritized');
  await evaluate(`document.querySelector('#matterRunPicker').value='run-demo-audit';document.querySelector('#matterRunPicker').dispatchEvent(new Event('change',{bubbles:true}));`);
  await waitFor(()=>evaluate(`document.querySelector('#matterReply').disabled && document.querySelector('.history-notice')`),10000,'historical message guard');
  ensure(await evaluate(`new URLSearchParams(location.hash.split('?')[1]).get('run')==='run-demo-audit'`),'selected run not in URL');
  await sleep(3500);
  ensure(await evaluate(`document.querySelector('#matterRunPicker').value==='run-demo-audit' && document.querySelector('#matterReply').disabled`),'polling replaced historical selection');
  await reloadPage();
  await waitFor(()=>evaluate(`document.querySelector('#matterRunPicker')?.value==='run-demo-audit' && document.querySelector('#matterReply').disabled`),15000,'history survives reload');
  await shot('matter-08-run-history-desktop');await mobileShot('matter-09-run-history-mobile');
  await evaluate(`document.querySelector('#openMatterEvidenceReview').click()`);
  await waitFor(()=>evaluate(`document.querySelector('#artifactView .matter-review-back')`),15000,'historical review');
  await evaluate(`document.querySelector('#artifactView .matter-review-back').click()`);
  await waitFor(()=>evaluate(`document.querySelector('#matterRunPicker')?.value==='run-demo-audit' && document.querySelector('#matterReply').disabled`),15000,'return preserves history selection');
  await evaluate(`document.querySelector('#returnLatestRun').click()`);
  await waitFor(()=>evaluate(`document.querySelector('#matterRunPicker')?.value==='run-zz-history-fixture' && !document.querySelector('#matterReply').disabled`),10000,'latest run returns to direction mode');
  await evaluate(`document.querySelector('[data-jump="matterReply"]').click()`);
  ensure(await evaluate(`document.activeElement.id==='matterReply' && document.querySelector('#matterRequest').value==='request-history-fixture'`),'answer request action lost focus or request association');
});
await step('unsent request answers keep their original run and never target a replacement request',async()=>{
  const latest='run-zz-history-fixture',original='run-demo-audit',draft='Unsent answer for the original dated-email request.';
  await focusSurface('conversation');
  await evaluate(`{const request=document.querySelector('#matterRequest');request.value='request-history-fixture';request.dispatchEvent(new Event('change',{bubbles:true}));}`);await typeDraft(draft);
  ensure(await evaluate(`MatterWorkspaceStore.read(${JSON.stringify(matterId)}).runId==='${latest}' && MatterWorkspaceStore.read(${JSON.stringify(matterId)}).requestId==='request-history-fixture'`),'draft lacks original request provenance');
  await evaluate(`{const picker=document.querySelector('#matterRunPicker');picker.value='${original}';picker.dispatchEvent(new Event('change',{bubbles:true}));}`);
  await waitFor(()=>evaluate(`document.querySelector('#matterReply')?.disabled && document.querySelector('#matterReply').value===${JSON.stringify(draft)}`),10000,'historic run retains read-only draft');
  await evaluate(`MatterUI.refresh()`);await sleep(300);
  ensure(await evaluate(`MatterWorkspaceStore.read(${JSON.stringify(matterId)}).runId==='${latest}' && MatterWorkspaceStore.read(${JSON.stringify(matterId)}).requestId==='request-history-fixture'`),'historic polling reassigned saved draft provenance');
  await visibleClick('#returnLatestRun');
  await waitFor(()=>evaluate(`document.querySelector('#matterRunPicker')?.value==='${latest}' && document.querySelector('#matterRequest')?.value==='request-history-fixture'`),10000,'original request target restored');
  const recordPath=`${fixtureRoot}/workspace/matters/${matterId}/workspace/case_records/${latest}.json`,record=JSON.parse(readFileSync(recordPath,'utf8'));
  record.requests[0].id='replacement-request-fixture';record.requests[0].item='A different request must not receive an old unsent answer.';writeFileSync(recordPath,JSON.stringify(record));
  await evaluate(`MatterUI.refresh()`);
  await waitFor(()=>evaluate(`document.querySelector('#matterRequest')?.querySelector('option[value="replacement-request-fixture"]')`),10000,'replacement request from actual persisted record');
  ensure(await evaluate(`document.querySelector('#matterRequest').value==='' && document.querySelector('#matterReply').value===${JSON.stringify(draft)}`),'polling silently attached old draft to replacement request');
  await reloadPage();
  await waitFor(()=>evaluate(`document.querySelector('#matterReply')?.value===${JSON.stringify(draft)} && document.querySelector('#matterRequest')?.value===''`),10000,'reload leaves stale target as general context');
  ensure(await evaluate(`document.querySelector('#matterReplyStatus').textContent.includes('original request')`),'stale-target restoration lacks a visible explanation');
  await typeDraft('');
});
await step('slow message response cannot duplicate a send or erase the next draft',async()=>{
  const submitted='Fictional slow-response followup, saved exactly once.',nextDraft='Keep this newly typed next question.';
  await focusSurface('conversation');
  await evaluate(`window.__slowMatterOriginal=window.fetch;window.__slowMatterPosts=0;window.fetch=async function(url,opts){if(opts?.method==='POST'&&String(url).endsWith('/messages')){window.__slowMatterPosts++;const response=await window.__slowMatterOriginal.apply(this,arguments);window.__slowMatterSaved=true;await new Promise(resolve=>window.__releaseMatterSend=resolve);return response;}return window.__slowMatterOriginal.apply(this,arguments);};document.querySelector('#matterRequest').value='';document.querySelector('#matterReply').value=${JSON.stringify(submitted)};document.querySelector('#matterReplyForm').requestSubmit();`);
  try{
    await waitFor(()=>evaluate(`window.__slowMatterSaved===true`),10000,'actual slow message persistence');
    await evaluate(`document.querySelector('#matterReply').value=${JSON.stringify(nextDraft)};MatterUI.refresh()`);
    await sleep(3500); // Includes the normal polling interval while POST response remains held.
    ensure(await evaluate(`document.querySelector('#sendMatterMessage').disabled`),'polling re-enabled pending send');
    ensure(await evaluate(`document.querySelector('#matterAddFiles').disabled && [...document.querySelectorAll('#runMatter,#resumeMatter,#chatStartAgent')].every(button=>button.disabled)`),'pending message permits manual agent start or document upload');
    await evaluate(`document.querySelector('#matterReplyForm').requestSubmit()`);await sleep(200);
    ensure(await evaluate(`window.__slowMatterPosts===1`),'second submit duplicated the pending request');
    await evaluate(`window.__releaseMatterSend()`);
    await waitFor(()=>evaluate(`!document.querySelector('#sendMatterMessage').disabled`),10000,'send response completion');
    ensure(await evaluate(`document.querySelector('#matterReply').value===${JSON.stringify(nextDraft)}`),'response erased newly typed draft');
    const saved=await(await fetch(`${base}/api/matters/${matterId}`)).json();
    ensure(saved.messages.filter(m=>m.body===submitted).length===1,'actual message was not persisted exactly once');
  }finally{await evaluate(`window.__releaseMatterSend?.();window.fetch=window.__slowMatterOriginal;document.querySelector('#matterReply').value='';`);}
});
await step('persisted pending answer permits Resume without calling a provider',async()=>{
  await focusSurface('findings');
  const root=`${fixtureRoot}/workspace/matters/${matterId}`,run='run-zz-history-fixture';
  const file=`${root}/workspace/case_records/${run}.json`,record=JSON.parse(readFileSync(file,'utf8'));
  record.requests[0].id='R1';writeFileSync(file,JSON.stringify(record));
  writeFileSync(`${root}/workspace/steering-inbox.json`,JSON.stringify([{queue_id:'browser-pending-answer',run_id:run,request_id:'R1',text:'The fictional email date was June 1.'}]));
  const detail=await(await fetch(`${base}/api/matters/${matterId}/runs/${run}`)).json();
  ensure(detail.pending_request_ids.includes('R1'),'persisted answer metadata absent from actual API');
  await evaluate(`window.__resumeOriginal=window.fetch;window.__mockResumePosts=0;window.fetch=async function(url,opts){const path=new URL(String(url),location.href).pathname;if(opts?.method==='POST'&&(path.endsWith('/resume')||path.endsWith('/start'))){if(!path.endsWith('/runs/${run}/resume'))throw Error('Unexpected run dispatch');window.__mockResumePosts++;return new Response(JSON.stringify({id:'browser-mock-resume',run_id:'${run}',running:false}),{status:200});}const response=await window.__resumeOriginal.apply(this,arguments);if(!opts?.method&&path==='/api/matters/${matterId}'){const body=await response.json();body.can_run=true;body.demo=false;body.api_key_configured=true;return new Response(JSON.stringify(body),{status:200});}return response;};MatterUI.refresh();`);
  try{
    await waitFor(()=>evaluate(`document.querySelector('#resumeMatter')&&!document.querySelector('#resumeMatter').disabled`),10000,'queued answer resume affordance');
    await evaluate(`document.querySelector('#resumeMatter').click()`);
    await waitFor(()=>evaluate(`window.__mockResumePosts===1`),10000,'mock resume submission');
    await waitFor(()=>evaluate(`document.querySelector('#matterReplyStatus').textContent.includes('Agent started') && !document.querySelector('#resumeMatter').disabled`),10000,'completed mocked resume acknowledgment');
  }finally{await evaluate(`window.fetch=window.__resumeOriginal`);}
});
await step('new investigation selects a run discovered after the start response',async()=>{
  const run='run-zz-history-fixture',fresh='run-zzzz-delayed-browser';
  const detail=await(await fetch(`${base}/api/matters/${matterId}/runs/${run}`)).json();
  await evaluate(`window.__lateOriginal=window.fetch;window.__latePublished=false;window.__lateStarts=0;window.__lateDetail=${JSON.stringify(detail)};window.fetch=async function(url,opts){const path=new URL(String(url),location.href).pathname;if(opts?.method==='POST'&&(path.endsWith('/start')||path.endsWith('/resume'))){if(!path.endsWith('/start'))throw Error('Unexpected run dispatch');window.__lateStarts++;return new Response(JSON.stringify({id:'browser-late-job',running:true}),{status:200});}if(!opts?.method&&path==='/api/matters/${matterId}/runs/${fresh}'){return new Response(JSON.stringify({...window.__lateDetail,id:'${fresh}',status:'running',pending_request_ids:[]}),{status:200});}const response=await window.__lateOriginal.apply(this,arguments);if(!opts?.method&&path==='/api/matters/${matterId}'){const body=await response.json();body.can_run=true;body.demo=false;body.api_key_configured=true;body.jobs=window.__lateStarts?[{id:'browser-late-job',running:true,run_id:window.__latePublished?'${fresh}':''}]:[];if(window.__latePublished)body.runs.unshift({id:'${fresh}',status:'running'});return new Response(JSON.stringify(body),{status:200});}return response;};MatterUI.refresh();`);
  try{
    await waitFor(()=>evaluate(`document.querySelector('#runMatter')&&!document.querySelector('#runMatter').disabled`),10000,'mock new-run action');
    await evaluate(`document.querySelector('#runMatter').click()`);
    await waitFor(()=>evaluate(`window.__lateStarts===1&&document.querySelector('#matterReplyStatus').textContent.includes('Agent started')`),10000,'start response before run exists');
    ensure(await evaluate(`document.querySelector('#matterRunPicker')?.value!=='${run}'`),'start pinned the old run while waiting for discovery');
    ensure(await evaluate(`document.querySelector('#matterAddFiles').disabled`),'busy investigation permits document upload');
    await evaluate(`window.__latePublished=true;MatterUI.refresh()`);
    await waitFor(()=>evaluate(`document.querySelector('#matterRunPicker')?.value==='${fresh}'`),10000,'late run selected');
    ensure(await evaluate(`window.__lateStarts===1`),'discovery dispatched an extra start');
  }finally{await evaluate(`window.fetch=window.__lateOriginal`);await reloadPage();}
});
await step('live Enter persists context then resumes with mocked steering and provider boundaries',async()=>{
  const run='run-zz-history-fixture',message='Fictional keyboard answer: the email date was June 1.';
  await evaluate(`window.__sendResumeOriginal=window.fetch;window.__sendResumePosts=0;window.__sendResumeDelivery=null;window.fetch=async function(url,opts){const path=new URL(String(url),location.href).pathname;if(opts?.method==='POST'&&path==='/api/matters/${matterId}/messages'){const payload=JSON.parse(opts.body);window.__sendResumeDelivery=payload;const response=await window.__sendResumeOriginal.call(this,url,{...opts,body:JSON.stringify({text:payload.text})});const body=await response.json();return new Response(JSON.stringify({...body,delivery:{state:'queued',run_id:payload.run_id,request_id:payload.request_id}}),{status:response.status});}if(opts?.method==='POST'&&path==='/api/matters/${matterId}/runs/${run}/resume'){window.__sendResumePosts++;return new Response(JSON.stringify({id:'browser-send-resume',run_id:'${run}',running:false}),{status:200});}if(opts?.method==='POST'&&path.endsWith('/start'))throw Error('Send resumed an existing investigation by incorrectly starting another run');const response=await window.__sendResumeOriginal.apply(this,arguments);if(!opts?.method&&path==='/api/matters/${matterId}'){const body=await response.json();body.can_run=true;body.demo=false;body.api_key_configured=true;return new Response(JSON.stringify(body),{status:200});}return response;};location.hash=${JSON.stringify(matterHash)};MatterUI.refresh();`);
  try{
    await waitFor(()=>evaluate(`document.querySelector('#matterSwitcher')?.value===${JSON.stringify(matterId)} && document.querySelector('#sendMatterMessage')?.textContent==='Send' && document.querySelector('#matterRunPicker')?.value==='${run}'`),10000,'live paused-run Send composer');
    await visibleClick('#matterReply');
    await evaluate(`{const request=document.querySelector('#matterRequest');if(request){request.value='R1';request.dispatchEvent(new Event('change',{bubbles:true}));}}`);await typeDraft(message);
    await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,text:'\r'});await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
    try{await waitFor(()=>evaluate(`window.__sendResumePosts===1 && document.querySelector('#matterReplyStatus').textContent.includes('Agent started')`),10000,'Enter automatically resumes selected investigation');}
    catch(error){throw new Error(`${error.message}; ${JSON.stringify(await evaluate(`({posts:window.__sendResumePosts,status:document.querySelector('#matterReplyStatus').textContent,request:document.querySelector('#matterRequest')?.value,draft:document.querySelector('#matterReply').value,disabled:document.querySelector('#matterReply').disabled,focus:document.activeElement.id})`))}`);}
    ensure(await evaluate(`window.__sendResumeDelivery.run_id==='${run}' && window.__sendResumeDelivery.request_id==='R1'`),'Send lost selected run/request provenance before delivery');
    const saved=await(await fetch(`${base}/api/matters/${matterId}`)).json();ensure(saved.messages.filter(item=>item.body===message).length===1,'resumed answer was not saved exactly once');
  }finally{await evaluate(`window.fetch=window.__sendResumeOriginal`);}
});
await step('live Enter saves before dispatch and surfaces provider failure without a fabricated answer',async()=>{
  const created=await(await fetch(`${base}/api/matters`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:'Fictional live-send fixture: investigate the uncertain supplier commitment.'})})).json();
  ensure(created.id,'live-send fixture was not saved');
  const id=created.id,failed='Fictional Send test: save this even if the provider is unavailable.',sent='Fictional Send retry: start the investigation after saving this.';
  await evaluate(`window.__liveSendOriginal=window.fetch;window.__liveSendStarts=0;window.fetch=async function(url,opts){const path=new URL(String(url),location.href).pathname;if(opts?.method==='POST'&&path==='/api/matters/${id}/start'){window.__liveSendStarts++;return window.__liveSendStarts===1?new Response(JSON.stringify({error:'Fictional provider is unavailable'}),{status:503}):new Response(JSON.stringify({id:'browser-send-job',running:false}),{status:200});}const response=await window.__liveSendOriginal.apply(this,arguments);if(!opts?.method&&path==='/api/matters/${id}'){const body=await response.json();body.can_run=true;body.demo=false;body.api_key_configured=true;return new Response(JSON.stringify(body),{status:200});}return response;};location.hash='#/matter/${id}';`);
  try{
    await waitFor(()=>evaluate(`document.querySelector('#matterSwitcher')?.value==='${id}' && document.querySelector('#sendMatterMessage')?.textContent==='Send' && !document.querySelector('#matterReply').disabled`),10000,'live Send composer');
    await visibleClick('#matterReply');await typeDraft(failed);
    await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,text:'\r'});await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
    await waitFor(()=>evaluate(`window.__liveSendStarts===1 && document.querySelector('#matterReplyStatus').textContent.includes('Fictional provider is unavailable')`),10000,'explicit dispatch failure');
    let detail=await(await fetch(`${base}/api/matters/${id}`)).json();
    ensure(detail.messages.filter(message=>message.body===failed).length===1,'provider failure lost or duplicated saved context');
    ensure(!detail.messages.some(message=>['model','agent'].includes(message.kind))&&detail.runs.length===0,'failed start fabricated a model answer/run');
    await typeDraft(sent);
    await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,text:'\r'});await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
    await waitFor(()=>evaluate(`window.__liveSendStarts===2 && document.querySelector('#matterReplyStatus').textContent.includes('Agent started')`),10000,'Enter starts after persisted message');
    detail=await(await fetch(`${base}/api/matters/${id}`)).json();ensure(detail.messages.filter(message=>message.body===sent).length===1,'live Enter did not save retry exactly once');
    ensure(!detail.messages.some(message=>['model','agent'].includes(message.kind)),'job acknowledgment invented an agent answer');
  }finally{await evaluate(`window.fetch=window.__liveSendOriginal`);}
});
await step('busy steering hints and unconfirmed message saves remain honest and actionable',async()=>{
  const created=await(await fetch(`${base}/api/matters`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:'Fictional save-response failure fixture.'})})).json(),id=created.id;
  await evaluate(`window.__saveFailureOriginal=window.fetch;window.__saveFailurePosts=0;window.fetch=async function(url,opts){const path=new URL(String(url),location.href).pathname;if(opts?.method==='POST'&&path==='/api/matters/${id}/messages'){window.__saveFailurePosts++;return new Response(JSON.stringify({error:'Fictional storage response unavailable'}),{status:503});}const response=await window.__saveFailureOriginal.apply(this,arguments);if(!opts?.method&&path==='/api/matters/${id}'){const body=await response.json();body.demo=false;body.can_run=true;body.api_key_configured=true;body.jobs=[{id:'synthetic-active-job',running:true}];return new Response(JSON.stringify(body),{status:200});}return response;};location.hash='#/matter/${id}';`);
  try{
    await waitFor(()=>evaluate(`document.querySelector('#matterSwitcher')?.value==='${id}'&&document.querySelector('#sendMatterMessage')?.textContent==='Steer agent'`),10000,'busy steering composer');
    ensure(await evaluate(`document.querySelector('#matterReplyHint').textContent.includes('next safe boundary')&&!document.querySelector('#matterReplyHint').textContent.includes('next resume')`),'Steer composer falsely promises Queue delivery');
    const draft='Fictional direction retained after a failed save response.';await typeDraft(draft);await visibleClick('#sendMatterMessage');
    await waitFor(()=>evaluate(`document.querySelector('#matterReplyStatus').getAttribute('role')==='alert'&&document.querySelector('#matterReplyStatus').textContent.includes('save could not be confirmed')`),10000,'actionable unconfirmed save');
    ensure(await evaluate(`document.querySelector('#matterReply').value===${JSON.stringify(draft)}&&document.querySelector('#matterReplyStatus').textContent.includes('before retrying')&&!document.querySelector('#matterConversation').textContent.includes('Fictional storage response unavailable')&&window.__saveFailurePosts===1`),'failure lost draft, invented agent response or auto-retried');
  }finally{await evaluate(`window.fetch=window.__saveFailureOriginal`);}
});
await step('missing model connection saves context; failed startup stays actionable',async()=>{
  const created=await(await fetch(`${base}/api/matters`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:'Fictional missing connection and failed startup fixture.'})})).json(),id=created.id;
  await evaluate(`window.__guardOriginal=window.fetch;window.__guardConnected=false;window.__guardStarts=0;window.fetch=async function(url,opts){const path=new URL(String(url),location.href).pathname;if(opts?.method==='POST'&&(path.endsWith('/start')||path.endsWith('/resume'))){window.__guardStarts++;return new Response(JSON.stringify({id:'failed-start-fixture',running:false,returncode:99}),{status:200});}const response=await window.__guardOriginal.apply(this,arguments);if(!opts?.method&&path==='/api/matters/${id}'){const body=await response.json();body.demo=false;body.api_key_configured=window.__guardConnected;body.can_run=window.__guardConnected;body.jobs=window.__guardStarts?[{id:'failed-start-fixture',running:false,returncode:99}]:[];return new Response(JSON.stringify(body),{status:200});}return response;};location.hash='#/matter/${id}';`);
  try{
    await waitFor(()=>evaluate(`document.querySelector('#matterSwitcher')?.value==='${id}' && document.querySelector('#matterTitle').textContent.includes('missing connection') && document.querySelector('#sendMatterMessage').textContent==='Save message'`),10000,'save-only missing-key workspace');
    ensure(await evaluate(`!document.querySelector('#chatStartAgent') && !!document.querySelector('#matterMode a[href="#/settings"]')`),'missing-key workspace offers execution or lacks Settings recovery');
    await typeDraft('Fictional context retained without a connected model.');await visibleClick('#sendMatterMessage');
    await waitFor(async()=>{const saved=await(await fetch(`${base}/api/matters/${id}`)).json();return saved.messages.some(m=>m.body==='Fictional context retained without a connected model.');},10000,'save without model');
    ensure(await evaluate(`window.__guardStarts===0`),'missing-key Send dispatched investigation');
    await evaluate(`window.__guardConnected=true;MatterUI.refresh()`);
    await waitFor(()=>evaluate(`document.querySelector('#chatStartAgent') && document.querySelector('#sendMatterMessage').textContent==='Send'`),10000,'connection readiness refreshed without reload');
    await visibleClick('#chatStartAgent');
    await waitFor(()=>evaluate(`document.querySelector('#matterError').textContent.includes('unsuccessfully') && /could not start/i.test(document.querySelector('#matterReplyStatus').textContent)`),10000,'failed process remains visible with recovery');
    await sleep(3500);
    ensure(await evaluate(`document.querySelector('#matterError').textContent.includes('unsuccessfully') && !document.querySelector('#matterReplyStatus').textContent.includes('Agent started')`),'poll hid failed startup or retained contradictory success');
  }finally{await evaluate(`window.fetch=window.__guardOriginal`);}
});
await step('uploaded records start a new investigation without steering the older run',async()=>{
  const run='run-zz-history-fixture';
  const upload=await fetch(`${base}/api/matters/${matterId}/documents`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({documents:[{filename:'new-evidence.txt',text:'Fictional newly supplied record requiring a new investigation.'}]})});
  ensure(upload.ok,'new evidence upload fixture failed');
  await evaluate(`window.__newDocsOriginal=window.fetch;window.__newDocsStarts=0;window.__newDocsDelivery=null;window.fetch=async function(url,opts){const path=new URL(String(url),location.href).pathname;if(opts?.method==='POST'&&(path.endsWith('/start')||path.endsWith('/resume'))){if(!path.endsWith('/start'))throw Error('New records resumed stale run');window.__newDocsStarts++;return new Response(JSON.stringify({id:'new-docs-job',running:false}),{status:200});}if(opts?.method==='POST'&&path==='/api/matters/${matterId}/messages')window.__newDocsDelivery=JSON.parse(opts.body);const response=await window.__newDocsOriginal.apply(this,arguments);if(!opts?.method&&path==='/api/matters/${matterId}'){const body=await response.json();body.demo=false;body.can_run=true;body.api_key_configured=true;return new Response(JSON.stringify(body),{status:200});}if(!opts?.method&&path==='/api/matters/${matterId}/runs/${run}'){const body=await response.json();body.started='2020-01-01T00:00:00Z';return new Response(JSON.stringify(body),{status:200});}return response;};location.hash='#/matter/${matterId}?run=${run}';`);
  try{
    await waitFor(()=>evaluate(`document.querySelector('#sendMatterMessage').textContent==='Send · new investigation'`),10000,'updated-record dispatch affordance');
    ensure(await evaluate(`!document.querySelector('#resumeMatter') && !document.querySelector('#matterRequest')`),'stale run still offers resume/request delivery after upload');
    await typeDraft('Fictional instruction: investigate the newly uploaded record.');await visibleClick('#sendMatterMessage');
    await waitFor(()=>evaluate(`window.__newDocsStarts===1`),10000,'new-record investigation starts');
    ensure(await evaluate(`!window.__newDocsDelivery.run_id && !window.__newDocsDelivery.request_id`),'new evidence message queued to older snapshot');
  }finally{await evaluate(`window.fetch=window.__newDocsOriginal`);}
});
await step('record explorer queries real scoped records and opens source beside chat',async()=>{
  const run='run-zz-history-fixture';
  const recordPath=`${fixtureRoot}/workspace/matters/${matterId}/workspace/case_records/${run}.json`;
  const record=JSON.parse(readFileSync(recordPath,'utf8'));
  record.facts=[...(record.facts||[]),{id:'F-EXPLORER',item:'Fictional supplier statement',claim:'Explorer source linkage fixture',document_id:'supplier-email',page:1,quote:'FICTIONAL_RECORD_ALPHA',stance:'supports',kind:'fact'}];
  writeFileSync(recordPath,JSON.stringify(record));
  await evaluate(`location.hash='#/matter/${matterId}?run=${run}'`);
  await waitFor(()=>evaluate(`document.querySelector('#matterRunPicker')?.value==='${run}' && document.querySelector('#recordQueryView')`),15000,'record explorer mount');
  await visibleClick('#matterRecords button[type="submit"]');
  await waitFor(()=>evaluate(`document.querySelector('.record-query-table')?.textContent.includes('supplier-email')`),10000,'actual document query');
  await evaluate(`document.querySelector('#recordQueryView').value='citations';document.querySelector('#recordQueryView').dispatchEvent(new Event('change',{bubbles:true}));`);
  await waitFor(()=>evaluate(`document.querySelector('.record-query-table')?.textContent.includes('FICTIONAL_RECORD_ALPHA')`),10000,'actual citation query');
  await visibleClick('#matterRecords .record-query-table button');
  await waitFor(()=>evaluate(`document.querySelector('#matterSourceBody')?.textContent.includes('FICTIONAL_RECORD_ALPHA')`),10000,'query-to-source link');
  ensure(await evaluate(`!document.querySelector('#matterConversationPanel').hidden && !document.querySelector('#matterResults').hidden`),'query hid chat or findings');
  await visibleClick('[data-jump="matterRecords"]');
  await shot('matter-21-record-explorer-desktop');
  await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await evaluate(`document.querySelector('#matterRecords').scrollIntoView({block:'start',behavior:'instant'})`);
  await mobileShot('matter-22-record-explorer-mobile');
  await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  ensure(await evaluate(`{const row=document.querySelector('.record-query-table tbody tr'),source=row?.querySelector('button'),r=source?.getBoundingClientRect();r && r.left>=0 && r.right<=innerWidth && row.querySelector('td').contains(source) && row.querySelector('details');}`),'mobile citations hide source behind internal identifiers');
  await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  const other=await fetch(`${base}/api/matters/${secondMatterId}/records?run=${run}&preset=claims`);
  ensure(other.status===404 && !(await other.text()).includes('FICTIONAL_RECORD_ALPHA'),'record query leaked another matter run');
});
await step('record snapshots invalidate on access changes and pending responses cannot restore old rows',async()=>{
  const run='run-zz-history-fixture',policyPath=`${fixtureRoot}/workspace/matters/${matterId}/workspace/quarantine.json`;
  await evaluate(`window.__recordsOriginal=window.fetch;window.__heldRecords=null;window.fetch=async function(url,opts){const response=await window.__recordsOriginal.apply(this,arguments);if(new URL(String(url),location.href).pathname.endsWith('/records')){await new Promise(resolve=>window.__heldRecords=resolve);}return response;};document.querySelector('#matterRecords button[type=submit]').click();`);
  await waitFor(()=>evaluate(`!!window.__heldRecords`),10000,'held actual records response');
  writeFileSync(policyPath,JSON.stringify({'supplier-email':{reason:'synthetic query revocation test'}}));
  await evaluate(`MatterUI.refresh()`);
  await waitFor(()=>evaluate(`document.querySelector('#matterRecords').textContent.includes('Records changed')`),10000,'cross-record access invalidation');
  ensure(await evaluate(`!document.querySelector('#matterSourceViewer').textContent.includes('FICTIONAL_RECORD_ALPHA')`),'open source text survived access revocation');
  await evaluate(`window.__heldRecords();window.fetch=window.__recordsOriginal;`);await sleep(300);
  ensure(!await evaluate(`!!document.querySelector('#matterRecords .record-query-table')`),'late response restored revoked content');
  const denied=await(await fetch(`${base}/api/matters/${matterId}/records?run=${run}&preset=citations`)).json();
  ensure(!JSON.stringify(denied).includes('FICTIONAL_RECORD_ALPHA'),'revoked citation remained queryable');
  const root=`${fixtureRoot}/workspace/matters/${matterId}/workspace`;
  writeFileSync(policyPath,'{}');await evaluate(`MatterUI.refresh()`);
  await visibleClick('#matterRecords button[type="submit"]');
  await waitFor(()=>evaluate(`!!document.querySelector('#matterRecords .record-query-table')`),10000,'refresh after restored source access');
  await evaluate(`window.__sourceOriginal=window.fetch;window.__heldSource=null;window.fetch=async function(url,opts){const response=await window.__sourceOriginal.apply(this,arguments);if(new URL(String(url),location.href).pathname.endsWith('/source'))await new Promise(resolve=>window.__heldSource=resolve);return response;};document.querySelector('#matterRecords .record-query-table button').click();`);
  await waitFor(()=>evaluate(`!!window.__heldSource`),10000,'held actual source response');
  writeFileSync(policyPath,JSON.stringify({'supplier-email':{reason:'synthetic in-flight source revocation'}}));
  await evaluate(`MatterUI.refresh()`);
  await waitFor(()=>evaluate(`!document.querySelector('#matterRecords .record-query-table')`),10000,'invalidate during source load');
  await evaluate(`window.__heldSource();window.fetch=window.__sourceOriginal;`);await sleep(300);
  ensure(await evaluate(`!document.querySelector('#matterSourceViewer').textContent.includes('FICTIONAL_RECORD_ALPHA')`),'late source response restored revoked text');
  writeFileSync(policyPath,'{}');
  await evaluate(`location.hash='#/matter/${secondMatterId}'`);
  await waitFor(()=>evaluate(`document.querySelector('#matterSwitcher')?.value==='${secondMatterId}' && document.querySelector('#recordQueryView')`),10000,'switch record scope');
  ensure(!await evaluate(`document.querySelector('#matterRecords').textContent.includes('FICTIONAL_RECORD_ALPHA')`),'record table persisted across matter switch');
});
await step('abandoned execution shows recovery beside chat instead of a permanent running spinner',async()=>{
  ensure(fixtureRoot&&/legal-agent-matter-browser\./.test(fixtureRoot),'recovery fixture requires owned ephemeral test root');
  const run='run-zz-history-fixture',file=`${fixtureRoot}/workspace/matters/${matterId}/workspace/runs/${run}/progress.json`;
  const previous=readFileSync(file,'utf8'),progress=JSON.parse(previous);
  progress.status='running';progress.messages=[{role:'user',content:'Fictional recoverable checkpoint'}];
  writeFileSync(file,JSON.stringify(progress));
  try{
    await evaluate(`location.hash='#/matter/${matterId}?run=${run}'`);
    await waitFor(()=>evaluate(`document.querySelector('#recoverMatter') && document.querySelector('#matterState')?.textContent==='Recovery available'`),15000,'explicit recovery affordance');
    ensure(await evaluate(`document.querySelector('#matterReply') && !document.querySelector('#matterReply').disabled`),'conversation unavailable during recovery inspection');
    ensure(await evaluate(`document.querySelector('#matterResults').textContent.includes('does not call your model')`),'recovery cost boundary missing');
  }finally{writeFileSync(file,previous);}
});
await step('investigation rail follows recorded plans, requests, gaps and safe source provenance',async()=>{
  ensure(fixtureRoot&&/legal-agent-matter-browser\./.test(fixtureRoot),'adaptive rail fixture requires owned ephemeral test root');
  const root=`${fixtureRoot}/workspace/matters/${matterId}`,run='run-zz-history-fixture';
  const file=`${root}/workspace/case_records/${run}.json`,original=readFileSync(file,'utf8'),record=JSON.parse(original);
  record.plan={revision:2,steps:['Check the agency notice rule first.']};
  record.task_understanding={actor:'The agency, not the individual applicant',event:'Agency designation review and notice publication',interpretation:'Distinguish an agency notice deadline from an individual re-registration deadline.',intended_outcome:'Explain the consequence of agency inaction with governing sources.',success_criteria:['Identify the actor and the legal deadline.'],assumptions:['The user refers to agency inaction.'],alternatives:[{interpretation:'The individual missed a registration deadline.',material:true,next_action:'Ask which actor and deadline the user means.'}],version:2,revision_reason:'The user clarified agency inaction.',original_request:'Why did the agency not act? <img src=x onerror="window.__taskXss=true">',stale:false};
  record.facts=[...(record.facts||[]),{id:'F_TREE',item:'notice',document_id:'public-fixture',page:1,quote:'Fictional authority excerpt.'}];
  record.research_tree={notice:{id:'notice',question:'Does the notice rule apply?',status:'supported',summary:'Fictional assessment still needs country context.',evidence_ids:['F_TREE'],next_action:'Identify the country designation.'},timing:{id:'timing',parent:'notice',question:'Was a notice published on time?',hypothesis:'The publication timing may change the result.',status:'blocked',summary:'The date is missing.',evidence_ids:[],next_action:'Find the official publication date.'}};
  record.checklist={agency_notice:{question:'Did the agency issue a timely notice?',evidence_needed:'The current statute and designated-country notice.',status:'open'}};
  record.requests=[{id:'request-adaptive',need:'Which country designation and November year?',item:'agency_notice',status:'open'}];
  record.memo={conclusion:'Fictional research draft',findings:[{item:'agency_notice',summary:'A fictional finding.',citations:[{document_id:'public-fixture',page:1,quote:'Fictional authority excerpt.'}]}],gaps:'Identify the designation and year; then compare the official notice dates.'};
  record.public_sources={'public-fixture':{document_id:'public-fixture',url:'https://www.uscis.gov/humanitarian/temporary-protected-status',title:'Fixture agency authority',retrieved_at:'2026-11-04',version:'Fetched snapshot'}};
  record.sources={'public-fixture':{citation:'Fixture citation'},'unsafe-fixture':{url:'javascript:alert(1)',title:'Unsafe fixture'}};
  writeFileSync(file,JSON.stringify(record));
  try{
    await evaluate(`location.hash='#/matter/${matterId}?run=${run}'`);await reloadPage();
    await waitFor(()=>evaluate(`document.querySelector('#matterRail')?.textContent.includes('Check the agency notice rule first.')`),15000,'run-backed agent plan');
    ensure(await evaluate(`document.querySelector('#matterRail').textContent.includes('Did the agency issue a timely notice?')`),'actual issue question absent');
    ensure(await evaluate(`document.querySelector('.task-understanding').textContent.includes('The agency, not the individual applicant')&&document.querySelector('.task-understanding').textContent.includes('Agency designation review and notice publication')&&document.querySelector('.task-understanding').textContent.includes('not user confirmation')&&document.querySelector('.task-understanding').textContent.includes('Material alternative')`),'task framing lost actor/event or interpretation boundary');
    ensure(await evaluate(`document.querySelector('.task-original').textContent.includes('<img src=x onerror=')&&!document.querySelector('.task-understanding img')&&!window.__taskXss`),'original request was interpreted as HTML');
    await evaluate(`document.querySelector('[data-research-node="__task_alternatives"]').open=true;document.querySelector('[data-research-node="__task_original"]').open=true`);
    ensure(await evaluate(`document.querySelector('.research-tree [data-research-node="notice"] [data-research-node="timing"]')&&document.querySelector('.research-tree').textContent.includes('2 branches · 1 unresolved')&&document.querySelector('.research-tree').textContent.includes('unverified')&&document.querySelector('[data-research-node="notice"] [data-status="supported"]')`),'research tree hierarchy, status or assessment caveat missing');
    await evaluate(`document.querySelector('.research-references').open=true`);
    ensure(await evaluate(`document.querySelector('.research-references').textContent.includes('F_TREE')&&document.querySelector('.research-references .matter-citation')`),'research evidence references absent');
    await evaluate(`document.querySelector('[data-research-node="notice"]').open=false`);
    ensure(!await evaluate(`document.querySelector('#matterRail').textContent.includes('Initial intake checklist')`),'static intake shown as current plan');
    ensure(await evaluate(`document.querySelector('#matterResults').textContent.includes('Identify the designation and year; then compare the official notice dates.')`),'string memo gaps silently lost');
    ensure(await evaluate(`document.querySelector('#matterRail a[href="https://www.uscis.gov/humanitarian/temporary-protected-status"]') && document.querySelector('.matter-citation a[href="https://www.uscis.gov/humanitarian/temporary-protected-status"]')`),'original source provenance not linked in rail and citation');
    ensure(!await evaluate(`document.querySelector('#matterRail a[href^="javascript:"]')`),'unsafe source link rendered');
    ensure(await evaluate(`document.querySelector('#matterRail').textContent.includes('Which country designation and November year?')`),'actual agent request absent');
    if(await evaluate(`!!document.querySelector('[data-rail-request="request-adaptive"]')`)){
      await evaluate(`document.querySelector('[data-rail-section="questions"]').open=true;document.querySelector('[data-rail-request="request-adaptive"]').click()`);
      ensure(await evaluate(`document.querySelector('#matterRequest')?.value==='request-adaptive' && document.activeElement===document.querySelector('#matterReply')`),'request did not target Copilot reply');
    }else{
      ensure(await evaluate(`document.querySelector('#matterRail').textContent.includes('New documents are waiting for a new investigation.') && !document.querySelector('#matterRequest')`),'new documents silently targeted an obsolete run request');
    }
    await evaluate(`document.querySelector('[data-rail-section="context"]').open=true`);
    record.plan={revision:3,steps:['Compare the official notice publication dates.']};record.task_understanding.stale=true;writeFileSync(file,JSON.stringify(record));
    await evaluate(`MatterUI.refresh()`);
    await waitFor(()=>evaluate(`document.querySelector('#matterRail')?.textContent.includes('Compare the official notice publication dates.')`),10000,'updated actual plan');
    ensure(await evaluate(`document.querySelector('[data-rail-section="context"]').open && !document.querySelector('#matterRail').textContent.includes('Check the agency notice rule first.')`),'polling reset disclosure or retained stale plan');
    ensure(await evaluate(`!document.querySelector('[data-research-node="notice"]').open`),'polling expanded a collapsed research branch');
    ensure(await evaluate(`document.querySelector('.task-stale').textContent.includes('needs reassessment')&&document.querySelector('[data-research-node="__task_original"]').open&&document.querySelector('[data-research-node="__task_alternatives"]').open`),'stale task interpretation or stable disclosures missing');
    const evidence=record.facts.find(f=>f.id==='F_TREE');record.facts=record.facts.filter(f=>f.id!=='F_TREE');record.withdrawn_facts=[...(record.withdrawn_facts||[]),evidence];writeFileSync(file,JSON.stringify(record));
    await evaluate(`MatterUI.refresh()`);
    await waitFor(()=>evaluate(`document.querySelector('[data-research-node="notice"] [data-status="stale"]')?.textContent==='stale · evidence changed'`),10000,'withdrawal invalidates recorded research assessment');
    ensure(await evaluate(`document.querySelector('.research-tree').textContent.includes('2 branches · 2 unresolved')&&document.querySelector('[data-research-node="notice"]').textContent.includes('Recorded assessment: supported.')&&document.querySelector('[data-research-node="notice"]').textContent.includes('Fictional assessment still needs country context.')&&document.querySelector('.research-references').textContent.includes('evidence unavailable or withdrawn')&&!document.querySelector('.research-references .matter-citation')`),'stale branch lost original assessment, unresolved count or evidence warning');
    ensure(JSON.parse(readFileSync(file,'utf8')).research_tree.notice.status==='supported','UI mutated recorded research assessment');
    await shot('adaptive-investigation-rail');
  }finally{writeFileSync(file,original);}
});
await step('Queue saves without execution and pending messages support edit, cancel and honest delivery state',async()=>{
  const run='run-zz-history-fixture',message='Fictional queued direction: check the notice date.';
  await evaluate(`window.__queueOriginal=window.fetch;window.__queueStarts=0;window.__queuePayload=null;window.fetch=async function(url,opts){const path=new URL(String(url),location.href).pathname;if(opts?.method==='POST'&&(path.endsWith('/start')||path.endsWith('/resume'))){window.__queueStarts++;throw Error('Queue must never start execution');}if(opts?.method==='POST'&&path==='/api/matters/${matterId}/messages')window.__queuePayload=JSON.parse(opts.body);const response=await window.__queueOriginal.apply(this,arguments);if(!opts?.method&&(path==='/api/matters/${matterId}'||path==='/api/matters/${matterId}/runs/${run}')){const body=await response.json();body.demo=false;body.can_run=true;body.api_key_configured=true;body.requires_new_run=false;return new Response(JSON.stringify(body),{status:200});}return response;};location.hash='#/matter/${matterId}?run=${run}';`);
  try{
    await waitFor(()=>evaluate(`document.querySelector('#matterMessageMode') && document.querySelector('#matterRequest')`),15000,'live delivery selector');
    await evaluate(`document.querySelector('#matterMessageMode').value='queue';document.querySelector('#matterMessageMode').dispatchEvent(new Event('change'));document.querySelector('#matterRequest').value='';`);
    await typeDraft(message);await visibleClick('#sendMatterMessage');
    await waitFor(()=>evaluate(`document.querySelector('#matterQueue').textContent.includes(${JSON.stringify(message)})`),10000,'actual queued delivery record');
    ensure(await evaluate(`window.__queueStarts===0 && window.__queuePayload.mode==='queue' && window.__queuePayload.run_id==='${run}'`),'Queue triggered execution or lost mode/run scope');
    const detail=await(await fetch(`${base}/api/matters/${matterId}/runs/${run}`)).json();
    const row=detail.queued_messages.find(q=>q.text===message);ensure(row&&row.state==='queued','message not durably queued');
    await visibleClick(`[data-queue-edit="${row.queue_id}"]`);
    await evaluate(`document.querySelector('#queueEditText').value='Fictional revised pending direction.';document.querySelector('#queueEditText').dispatchEvent(new Event('input',{bubbles:true}));`);
    await visibleClick(`[data-queue-save="${row.queue_id}"]`);
    await waitFor(()=>evaluate(`document.querySelector('#matterQueue').textContent.includes('Fictional revised pending direction.') && !document.querySelector('#queueEditText')`),10000,'durable queue revision');
    const changed=await(await fetch(`${base}/api/matters/${matterId}/runs/${run}`)).json();
    ensure(changed.queued_messages.find(q=>q.queue_id===row.queue_id).revision===2,'queue edit revision not persisted');
    await visibleClick(`[data-queue-cancel="${row.queue_id}"]`);
    await waitFor(()=>evaluate(`!document.querySelector('[data-queue-cancel="${row.queue_id}"]') && document.querySelector('#matterQueue').textContent.includes('Cancelled')`),10000,'queue cancellation');
    const cancelled=await(await fetch(`${base}/api/matters/${matterId}/runs/${run}`)).json();ensure(cancelled.queued_messages.find(q=>q.queue_id===row.queue_id).state==='cancelled','cancellation not persisted');
    const queued=await(await fetch(`${base}/api/matters/${matterId}/messages`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:'Fictional safe-boundary direction.',run_id:run,mode:'steer'})})).json();
    ensure(queued.delivery?.mode==='steer','steer delivery mode missing');
    const inbox=`${fixtureRoot}/workspace/matters/${matterId}/workspace/steering-inbox.json`,rows=JSON.parse(readFileSync(inbox,'utf8'));
    rows.find(q=>q.queue_id===queued.delivery.queue_id).state='delivered';writeFileSync(inbox,JSON.stringify(rows));
    await evaluate(`MatterUI.refresh()`);
    await waitFor(()=>evaluate(`document.querySelector('#matterQueue').textContent.includes('Delivered to agent context')`),10000,'honest delivered state');
    ensure(await evaluate(`document.querySelector('#matterQueue').textContent.includes('not proof it acted on the message') && !document.querySelector('[data-queue-edit="${queued.delivery.queue_id}"]')`),'delivery falsely implied action or remained editable');
    await shot('message-queue-desktop');
    await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    await evaluate(`document.querySelector('#matterQueue').scrollIntoView({block:'center'})`);
    ensure(await evaluate(`document.querySelector('#matterMessageMode').getBoundingClientRect().width>100 && document.documentElement.scrollWidth<=innerWidth`),'delivery controls overflow mobile workspace');
    await mobileShot('message-queue-mobile');
  }finally{await evaluate(`window.fetch=window.__queueOriginal`);await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});}
});
await step('initial Send stays visible, Enter saves once before dispatch, and provider errors stay actionable',async()=>{
  await send('Emulation.setDeviceMetricsOverride',{width:1280,height:720,deviceScaleFactor:1,mobile:false});
  await evaluate(`window.__intakeFetch=window.fetch;window.__intakeStarts=0;window.__intakeSaved=false;window.fetch=async function(url,opts){const p=new URL(String(url),location.href).pathname;if(opts?.method==='POST'&&p.endsWith('/start')){window.__intakeStarts++;const saved=await(await window.__intakeFetch(p.slice(0,-6))).json();window.__intakeSaved=saved.question==='Explain this fictional contract notice to me.';return new Response(JSON.stringify({error:'Synthetic initial provider failure'}),{status:503});}const response=await window.__intakeFetch.apply(this,arguments);if(!opts?.method&&(p==='/api/info'||(p.startsWith('/api/matters/')&&p.split('/').length===4))){const body=await response.json();body.isolated=false;body.demo=false;body.can_run=true;body.api_key_configured=true;return new Response(JSON.stringify(body),{status:200});}return response;};location.hash='#/home';`);
  try{
    await waitFor(()=>evaluate(`document.querySelector('#prepareMatter')?.textContent==='Send to agent'`),10000,'initial live Send');
    await evaluate(`window.scrollTo(0,0);document.querySelector('#matterQuestion').value='Explain this fictional contract notice to me.';`);
    ensure(await evaluate(`(()=>{const r=document.querySelector('#prepareMatter').getBoundingClientRect();return r.top>=0&&r.bottom<innerHeight;})()`),'initial action is below short laptop viewport');
    await visibleClick('#matterQuestion');
    await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,text:'\r'});await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
    await waitFor(()=>evaluate(`window.__intakeStarts===1&&document.querySelector('#matterReplyStatus')?.textContent.includes('Synthetic initial provider failure')`),15000,'initial saved dispatch and explicit failure');
    ensure(await evaluate(`window.__intakeSaved`),'initial question was not persisted before dispatch');
    ensure(await evaluate(`!document.querySelector('#matterConversation').textContent.includes('Synthetic initial provider failure')`),'dispatch failure fabricated conversation');
  }finally{await evaluate(`window.fetch=window.__intakeFetch`);}
});
const fails=results.filter(r=>r.status==='FAIL');
writeFileSync(`${shotDir}/matter-results.json`,JSON.stringify(results,null,2)+'\n');
console.log(`\nMATTER SUMMARY: ${results.length-fails.length}/${results.length} ok, ${fails.length} failed`);
ws.close();shell.kill('SIGKILL');process.exit(fails.length?1:0);
