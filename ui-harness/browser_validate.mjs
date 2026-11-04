// Zero-dependency real-browser validation for the legal-agent gallery UI.
// Drives chromium headless-shell over CDP using only Node builtins
// (fetch, WebSocket, child_process). No playwright/selenium needed.
// Usually run via ui-harness/browser_run.sh; direct usage:
//   node ui-harness/browser_validate.mjs <baseUrl> <debugPort> <shotDir> <shellBin> <profileDir>
// Requires a real loopback listener + an unsandboxed browser launch
// (see README.md); exits 1 on the first failed check group.
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";

const [base, debugPort, shotDir, shellBin, profileDir] = process.argv.slice(2);
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

// Product entry point and narrow screens share the same persisted workspace.
await step("home opens by default and adapts to mobile", async () => {
  await send("Emulation.setDeviceMetricsOverride", {width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await send("Page.navigate", {url:base});
  await waitFor(() => evaluate(`document.querySelector('#homeView:not([hidden])') && /Synthetic demo/.test(document.querySelector('#runtimeStatus').textContent)`),15000,"home status");
  if (!await evaluate(`document.title === 'Legal Agent' && document.querySelector('#intakeForm') && document.querySelector('#matterQuestion')`)) throw new Error("home entry points missing");
  await shot("00-home-desktop");
  await send("Emulation.setDeviceMetricsOverride", {width:390,height:844,deviceScaleFactor:1,mobile:true});
  await new Promise(r=>setTimeout(r,300));
  if (await evaluate(`document.documentElement.scrollWidth > innerWidth + 1`)) throw new Error("mobile home overflows horizontally");
  await shot("00-home-mobile");
  await send("Emulation.setDeviceMetricsOverride", {width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await evaluate(`document.querySelector('[data-example="citation-claim-audit"]').click()`);
  await waitFor(() => evaluate(`document.querySelector('#artH1')`),30000,"home audit shortcut");
  await evaluate(`location.hash='#/home'`);
  await waitFor(() => evaluate(`document.querySelector('#intakeForm') && document.querySelector('#matterList')`),10000,"intake and saved matters");
  return "desktop, 390px mobile, question composer, audit example, saved matters";
});

// --- 1. gallery ---
let artHash = "";
const ASSERT_ROW_BTN = `[...document.querySelectorAll("#artifactView .panel")].find(p => p.querySelector("h2") && /Assertions/.test(p.querySelector("h2").textContent)).querySelector("tbody tr td button.rowbtn")`;
await step("gallery renders cards + demo actions", async () => {
  await send("Page.navigate", { url: `${base}/#/gallery` });
  await waitFor(() => evaluate(`document.querySelector("#galH1") && document.querySelectorAll(".gal-cards article").length`), 15000, "gallery cards");
  const cards = await evaluate(`document.querySelectorAll(".gal-cards article").length`);
  const demos = await evaluate(`[...document.querySelectorAll(".gal-cards button")].filter(b=>/synthetic demo/i.test(b.textContent)).length`);
  if (!cards || !demos) throw new Error(`cards=${cards} demos=${demos}`);
  await shot("01-gallery");
  return `cards=${cards} demoButtons=${demos}`;
});

// --- 2. open synthetic demo (audit) ---
await step("open synthetic demo reaches artifact view", async () => {
  const n = await evaluate(`[...document.querySelectorAll(".gal-cards button")].filter(b=>/synthetic demo/i.test(b.textContent)).length`);
  if (!n) throw new Error("no demo button");
  await evaluate(`[...document.querySelectorAll(".gal-cards button")].find(b=>/synthetic demo/i.test(b.textContent)).click()`);
  await waitFor(() => evaluate(`document.body.dataset.view === "artifact" && !document.querySelector("#artifactView").hidden && document.querySelector("#artH1") && document.querySelector("#artH1").textContent`), 30000, "artifact h1");
  const h1 = await evaluate(`document.querySelector("#artH1").textContent`);
  const banner = await evaluate(`document.querySelector("#artifactView").textContent`);
  if (!/demo|synthetic/i.test(banner)) throw new Error("no demo banner text");
  artHash = await evaluate(`location.hash`);
  await shot("02-artifact");
  return h1;
});

// --- 3. assertions table ---
let firstRow = null;
await step("assertions table renders rows", async () => {
  const rows = await evaluate(`document.querySelectorAll("#artifactView table.audit-table tbody tr").length`);
  if (!rows) throw new Error("no assertion rows");
  firstRow = await evaluate(`(${ASSERT_ROW_BTN}).getAttribute("aria-label")`);
  return `rows=${rows} first=${firstRow}`;
});

// --- 4. select row -> detail + revise assertion ---
await step("select row shows correction form", async () => {
  await evaluate(`(${ASSERT_ROW_BTN}).click()`);
  await waitFor(() => evaluate(`document.querySelector("#revAssertion") ? "yes" : ""`), 10000, "revise form");
  await shot("03-rowdetail");
  return await evaluate(`document.querySelector("#rowDetail").textContent.slice(0,120)`);
});
async function artifactVersion() {
  return evaluate(`(document.querySelector("#artifactView").textContent.match(/artifact v(\\d+)/) || [])[1]`);
}
await step("revise assertion creates new revision", async () => {
  const before = await artifactVersion();
  await evaluate(`document.querySelector("#revAssertion").value = "Browser-corrected assertion: knowledge by May 6 only (reviewed record silent before May 5 approval)."`);
  await evaluate(`submitRevise('assertion')`);
  const after = await waitFor(async () => {
    const v = await artifactVersion();
    return v && v !== before ? v : "";
  }, 15000, "version bump");
  return `v${before} -> v${after}`;
});

// --- 5. run checker ---
await step("run checker updates outcomes", async () => {
  await evaluate(`[...document.querySelectorAll("#artifactView button")].find(b=>/Run checker/i.test(b.textContent)).click()`);
  await waitFor(async () => {
    const t = await evaluate(`document.querySelector("#artifactView").textContent`);
    return /Citations:.*(passed|failed|incomplete)/i.test(t) ? "yes" : "";
  }, 60000, "checker outcomes");
  await shot("04-checked");
  return await evaluate(`document.querySelector("#artStatus").textContent.slice(0,150)`);
});

// --- 6. raise objection (first challenge, no prior review) ---
let objId = null;
await step("raise first objection from panel", async () => {
  const has = await evaluate(`!!document.querySelector("#objText")`);
  if (!has) throw new Error("no #objText on fresh demo");
  await evaluate(`document.querySelector("#objText").value = "Browser objection: timing inference exceeds the May 6 source."`);
  await evaluate(`{ const r = document.querySelector("#objReporter"); if (r) r.value = "Browser Reviewer"; }`);
  await evaluate(`submitObjection('raise')`);
  objId = await waitFor(async () => {
    const t = await evaluate(`document.querySelector("#artifactView").textContent`);
    return /OBJ\d+/i.test(t) ? t.match(/OBJ\d+/i)[0] : "";
  }, 15000, "objection id");
  await shot("05-objection");
  return objId;
});
await step("disprove objection with counter-evidence", async () => {
  if (!objId) throw new Error("no objection to disprove");
  const sel = await evaluate(`!!document.querySelector(${JSON.stringify(`#disprove-${objId}`)})`);
  if (!sel) throw new Error(`no #disprove-${objId}`);
  await evaluate(`document.querySelector(${JSON.stringify(`#disprove-${objId}`)}).value = "May 7 attachment confirms knowledge only after approval; inference narrowed in v2."`);
  await evaluate(`submitObjection('disprove', ${JSON.stringify(objId)})`);
  await waitFor(async () => {
    const t = await evaluate(`document.querySelector("#artifactView").textContent`);
    return /disproven:/i.test(t) ? "yes" : "";
  }, 15000, "disproven label");
  return `${objId} disproven`;
});

// --- 7. blind review x2 (disagreeing) -> adjudicate ---
async function blindLabel(verdict, actor, rationale) {
  await evaluate(`[...document.querySelectorAll("#rowDetail button")].find(b=>/Start blind review/i.test(b.textContent)).click()`);
  await waitFor(() => evaluate(`!!document.querySelector("#blindVerdict")`), 10000, "assignment form");
  // blinding: system verdict must not be in the assignment payload
  const leak = await evaluate(`JSON.stringify(gal.assignment).toLowerCase()`);
  if (/support|contradic|insufficient/.test(leak) && /verdict|outcome|label/.test(leak))
    throw new Error(`assignment leaks verdict: ${leak.slice(0, 120)}`);
  await evaluate(`document.querySelector("#blindVerdict").value = ${JSON.stringify(verdict)}`);
  await evaluate(`document.querySelector("#blindOrigin").value = "simulated"`);
  await evaluate(`document.querySelector("#blindActor").value = ${JSON.stringify(actor)}`);
  await evaluate(`document.querySelector("#blindRationale").value = ${JSON.stringify(rationale)}`);
  await evaluate(`submitBlindLabel()`);
  await waitFor(async () => {
    const t = await evaluate(`document.querySelector("#rowDetail").textContent`);
    return new RegExp(actor.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).test(t) ? "yes" : "";
  }, 15000, `label ${actor}`);
}
await step("two disagreeing blind labels recorded", async () => {
  await blindLabel("insufficient", "sim:browser-1", "May 6 source cannot settle pre-May-5 knowledge.");
  await blindLabel("supported", "sim:browser-2", "Attachment chain implies earlier circulation.");
  await shot("06-labels");
  return "sim:browser-1 insufficient, sim:browser-2 supported";
});
await step("adjudicate unresolved row", async () => {
  const has = await evaluate(`!!document.querySelector("#adjDecision")`);
  if (!has) throw new Error("no adjudication form after disagreement");
  await evaluate(`document.querySelector("#adjDecision").value = "corrected"`);
  await evaluate(`document.querySelector("#adjActor").value = "Browser Adjudicator"`);
  await evaluate(`document.querySelector("#adjRationale").value = "v2 wording is supported; original timing question stays unresolved."`);
  await evaluate(`submitAdjudication()`);
  await waitFor(async () => {
    const t = await evaluate(`document.querySelector("#rowDetail").textContent`);
    return /Adjudicated:/i.test(t) ? "yes" : "";
  }, 15000, "adjudicated label");
  return "Adjudicated: corrected rendered";
});

// --- 8. hand note ---
await step("hand note lands in history", async () => {
  const has = await evaluate(`!!document.querySelector("#noteText")`);
  if (!has) throw new Error("no #noteText");
  await evaluate(`document.querySelector("#noteText").value = "Browser note: re-verify attachment date with counsel."`);
  await evaluate(`{ const a = document.querySelector("#noteAuthor"); if (a) a.value = "Browser Lawyer"; }`);
  await evaluate(`submitNote()`);
  await waitFor(async () => {
    const t = await evaluate(`document.querySelector("#artifactView").textContent`);
    return /re-verify attachment date/i.test(t) ? "yes" : "";
  }, 15000, "note text");
  return "note visible";
});

// --- 9. needs freeze + document add in runs view ---
await step("needs freeze buttons present", async () => {
  const needs = await evaluate(`[...document.querySelectorAll("#artifactView button")].filter(b=>/Freeze needs/i.test(b.textContent)).length`);
  if (!needs) throw new Error("no Freeze needs buttons");
  return `freezeButtons=${needs}`;
});
await step("label-only freeze renders abstain badge", async () => {
  const item = await evaluate(`(document.querySelector("textarea[id^='needs-']")||{}).id || ""`);
  if (!item.startsWith("needs-")) throw new Error("no needs textarea");
  const id = item.slice("needs-".length);
  await evaluate(`document.querySelector("textarea[id='${item}']").value = "vanished-browser-need"`);
  await evaluate(`submitNeeds('${id}')`);
  await waitFor(async () => {
    const t = await evaluate(`document.querySelector("#artifactView").textContent`);
    return /abstain/i.test(t) ? "yes" : "";
  }, 15000, "abstain badge");
  return `item=${id} abstains`;
});
await step("document upload from runs view", async () => {
  await send("Page.navigate", { url: `${base}/#/` });
  await waitFor(() => evaluate(`!!document.querySelector("#uploadText")`), 15000, "upload form");
  await evaluate(`document.querySelector("#uploadName").value = "BROWSER-NOTE.txt"`);
  await evaluate(`document.querySelector("#uploadText").value = "Browser-added note. The attachment arrived on May 7."`);
  await evaluate(`submitUpload()`);
  const st = await waitFor(async () => {
    const t = await evaluate(`(document.querySelector("#uploadStatus")||{}).textContent || ""`);
    return /transcrib|BROWSER-NOTE|listed/i.test(t) ? t : "";
  }, 15000, "upload status");
  await shot("07-upload");
  return st.slice(0, 120);
});

// --- 10. export via real route ---
await step("export returns versioned report", async () => {
  await send("Page.navigate", { url: `${base}/${artHash}` });
  await waitFor(() => evaluate(`document.body.dataset.view === "artifact" && !document.querySelector("#artifactView").hidden && document.querySelector("#artH1") ? "yes" : ""`), 20000, "artifact again");
  const rep = await evaluate(`api(gal.base + "/export").then(r => ({v: r.exported_version, q: (r.qualifications||r.review?.qualifications||[]).length, keys: Object.keys(r).slice(0,8).join(",")})).catch(e => ({err: e.message}))`);
  if (rep.err) throw new Error(rep.err);
  return `v=${rep.v} keys=${rep.keys}`;
});

// --- 11. reload keeps state ---
await step("reload preserves revision state", async () => {
  const before = await evaluate(`(document.querySelector("#artifactView").textContent.match(/artifact v(\\d+)/) || [])[1]`);
  await send("Page.reload", { ignoreCache: true });
  await waitFor(() => evaluate(`document.body.dataset.view === "artifact" && !document.querySelector("#artifactView").hidden && document.querySelector("#artH1") ? "yes" : ""`), 20000, "artifact after reload");
  const after = await evaluate(`(document.querySelector("#artifactView").textContent.match(/artifact v(\\d+)/) || [])[1]`);
  const note = await evaluate(`/re-verify attachment date/i.test(document.querySelector("#artifactView").textContent)`);
  if (before !== after || !note) throw new Error(`v${before}->v${after} note=${note}`);
  await shot("05-reloaded");
  return `v${after} + note survived reload`;
});

// --- 12. keyboard traversal ---
await step("keyboard Tab moves visible focus", async () => {
  await send("Page.navigate", { url: `${base}/#/gallery` });
  await waitFor(() => evaluate(`document.querySelector("#galH1") ? "yes" : ""`), 15000, "gallery");
  const chain = [];
  for (let i = 0; i < 8; i++) {
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", keyIdentifier: "U+0009" });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", keyIdentifier: "U+0009" });
    await sleep(120);
    chain.push(await evaluate(`(d=>d.tagName+"#"+(d.id||"-")+"."+(d.className&&d.className.baseVal===undefined?String(d.className).split(" ")[0]:"-"))(document.activeElement)`));
  }
  const moved = new Set(chain).size > 1 && !chain.every((c) => c.startsWith("BODY"));
  if (!moved) throw new Error(chain.join(" | "));
  return chain.join(" | ");
});

// --- 12b. regression examples panel: empty state + honest refusal ---
await step("examples panel renders with empty state", async () => {
  await send("Page.navigate", { url: `${base}/${artHash}` });
  await waitFor(() => evaluate(`document.querySelector("#exId") ? "yes" : ""`), 20000, "examples form");
  const empty = await evaluate(`document.querySelector("#artifactView").textContent`);
  if (!/No promoted examples for this matter yet/.test(empty)) throw new Error("no empty state");
  const grid = await evaluate(`document.querySelectorAll('[id^="exclass-"]').length`);
  if (!grid) throw new Error("no per-row class inputs");
  await evaluate(`document.querySelector("#exId").scrollIntoView({block: "center"})`);
  await shot("06b-examples");
  return `class inputs=${grid}`;
});
await step("adversarial demo refusal surfaces in UI", async () => {
  await evaluate(`document.querySelector("#exId").value = "browser-timing"`);
  await evaluate(`document.querySelector("#exRole").value = "regression"`);
  await evaluate(`document.querySelector("#exActor").value = "sim:browser-curator"`);
  await evaluate(`document.querySelector("#exFailure").value = "browser: timing overreach"`);
  await evaluate(`document.querySelector("#exBehavior").value = "browser: preserve current outcomes"`);
  await evaluate(`[...document.querySelectorAll('[id^="exclass-"]')].forEach(el => el.value = "clean")`);
  await evaluate(`submitExample()`);
  const msg = await waitFor(async () => {
    const t = await evaluate(`document.querySelector("#artStatus").textContent`);
    return /pinned source version|recorded/.test(t) ? t : "";
  }, 20000, "refusal message");
  if (!/pinned source version/.test(msg)) throw new Error(`expected refusal, got: ${msg}`);
  return msg.slice(0, 100);
});

// --- 13. chronology board over the same matter ---
await step("board renders shared-matter events", async () => {
  await send("Page.navigate", { url: `${base}/#/gallery` });
  await waitFor(() => evaluate(`gal.cards && gal.cards.length ? "yes" : ""`), 15000, "cards");
  await evaluate(`openDemo(gal.cards.find(c => /chronology/i.test(c.name)).id)`);
  await waitFor(() => evaluate(`document.body.dataset.view === "artifact" && !document.querySelector("#artifactView").hidden && document.querySelector("#artH1") ? "yes" : ""`), 20000, "board h1");
  const rows = await evaluate(`document.querySelectorAll("#artifactView table.audit-table tbody tr").length`);
  if (!rows) throw new Error("no board event rows");
  await evaluate(`document.querySelector('#artifactView table.audit-table tbody tr td button.rowbtn').click()`);
  await waitFor(() => evaluate(`document.querySelector("#eventDetail") && document.querySelector("#eventDetail").textContent.trim() ? "yes" : ""`), 10000, "event detail");
  await shot("08-board");
  return `events=${rows}`;
});

// --- 14. jurisdiction matrix over the same matter ---
await step("matrix renders cells with state", async () => {
  await send("Page.navigate", { url: `${base}/#/gallery` });
  await waitFor(() => evaluate(`gal.cards && gal.cards.length ? "yes" : ""`), 15000, "cards");
  await evaluate(`openDemo(gal.cards.find(c => /jurisdiction/i.test(c.name)).id)`);
  await waitFor(() => evaluate(`document.body.dataset.view === "artifact" && !document.querySelector("#artifactView").hidden && document.querySelector("#artH1") ? "yes" : ""`), 20000, "matrix h1");
  const rows = await evaluate(`document.querySelectorAll("#artifactView table.audit-table tbody tr").length`);
  if (!rows) throw new Error("no matrix cell rows");
  await evaluate(`document.querySelector('#artifactView table.audit-table tbody tr td button.rowbtn').click()`);
  await waitFor(() => evaluate(`document.querySelector("#cellDetail") && document.querySelector("#cellDetail").textContent.trim() ? "yes" : ""`), 10000, "cell detail");
  await shot("09-matrix");
  return `cells=${rows}`;
});

// --- 15. full-gallery sweep: every demo renders ---
await step("all gallery demos render", async () => {
  await send("Page.navigate", { url: `${base}/#/gallery` });
  await waitFor(() => evaluate(`gal.cards && gal.cards.length ? "yes" : ""`), 15000, "cards");
  const cards = await evaluate(`gal.cards.filter(c => (c.actions || []).includes("open_demo")).map(c => ({id: c.id, name: c.name}))`);
  const bad = [];
  for (const c of cards) {
    try {
      await send("Page.navigate", { url: `${base}/#/gallery` });
      await waitFor(() => evaluate(`gal.cards && gal.cards.length ? "yes" : ""`), 15000, "cards");
      await evaluate(`openDemo(${JSON.stringify(c.id)})`);
      await waitFor(() => evaluate(`document.body.dataset.view === "artifact" && !document.querySelector("#artifactView").hidden && document.querySelector("#artH1") ? "yes" : ""`), 20000, `h1 ${c.id}`);
      const h1 = await evaluate(`document.querySelector("#artH1").textContent`);
      if (/unavailable/i.test(h1)) throw new Error(`unavailable: ${h1}`);
      const rows = await evaluate(`document.querySelectorAll("#artifactView table.audit-table tbody tr").length`);
      await shot(`10-${c.id}`);
      report(`demo renders: ${c.id}`, "ok", `${h1.slice(0, 60)} rows=${rows}`);
    } catch (e) {
      bad.push(`${c.id}: ${e.message}`);
      report(`demo renders: ${c.id}`, "FAIL", e.message);
    }
  }
  if (bad.length) throw new Error(`${bad.length} demos failed: ${bad.join("; ").slice(0, 250)}`);
  return `${cards.length} demos rendered`;
});

await step("work-product search and mobile review stay usable", async () => {
  await evaluate(`location.hash='#/gallery'`);
  await waitFor(()=>evaluate(`document.body.dataset.view === 'gallery' && document.querySelector('.product-search input')`),15000,"catalog search");
  await evaluate(`filterProducts('citation')`);
  if (await evaluate(`document.querySelectorAll('.gal-cards article').length`) !== 1) throw new Error("search did not narrow results");
  await evaluate(`filterProducts('no-such-template-123')`);
  if (!await evaluate(`/No matching/.test(document.querySelector('.gal-cards').textContent)`)) throw new Error("search empty state missing");
  await evaluate(`filterProducts('')`);
  await evaluate(`openDemo('citation-claim-audit')`);
  await waitFor(()=>evaluate(`document.body.dataset.view === 'artifact' && document.querySelector('#artH1')`),15000,"mobile artifact");
  await send("Emulation.setDeviceMetricsOverride", {width:390,height:844,deviceScaleFactor:1,mobile:true});
  await new Promise(r=>setTimeout(r,300));
  const overflow = await evaluate(`document.documentElement.scrollWidth-innerWidth`);
  await shot("11-review-mobile");
  if (overflow > 1) throw new Error(`mobile artifact overflows by ${overflow}px`);
  return "search results, empty state, mobile review";
});
const fails = results.filter((r) => r.status === "FAIL");
console.log(`\nBROWSER SUMMARY: ${results.length - fails.length}/${results.length} ok, ${fails.length} failed`);
ws.close();
shell.kill("SIGKILL");
process.exit(fails.length ? 1 : 0);
