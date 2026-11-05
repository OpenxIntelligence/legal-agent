/* Question-led, local matter workspace. Model requests require an explicit Send or Start action. */
(() => {
  'use strict';
  const DRAFT_KEY = 'legal-agent.intake.v1';
  const state = {id:null, matter:null, run:null, timer:null, loading:false, refreshFlight:null, files:[], lastMessages:'', lastRail:'', lastResults:'', history:[], lastReplyContext:'', starting:false, pendingStart:null, recovering:false, sourceSequence:0, reviewOpening:false, selectedRun:null, awaitingRun:null, sending:null, uploading:false, epoch:0, lastOverview:'', pane:'findings', replyReady:false, sourceOrigin:null, replySnapshot:null, sourceRevision:null};
  const list = v => Array.isArray(v) ? v : (v && typeof v === 'object' ? Object.values(v) : []);
  const text = v => v == null ? '' : (typeof v === 'string' ? v : JSON.stringify(v));
  const e = v => esc(text(v));
  const recoveryState = run => run?.recovery?.state || 'none';
  const recoveryHold = run => ['available','unavailable'].includes(recoveryState(run)) || (recoveryState(run)==='recovered' && run?.status==='paused');
  const runBusy = (matter,run) => !!state.awaitingRun || list(matter?.jobs).some(j=>j.running) || !!run?.job || recoveryState(run)==='active_elsewhere' || (run?.status==='running' && !['available','unavailable'].includes(recoveryState(run)));
  const path = suffix => `/api/matters/${encodeURIComponent(state.id)}${suffix || ''}`;
  const statusWords = {intake:'Ready to plan',ready:'Ready to investigate',running:'Investigating',paused:'Paused',needs_input:'Your input needed',awaiting_signoff:'Review required',answered:'Draft ready · unchecked',done:'Run ended · inspect review record',draft:'Draft',prepared:'Prepared'};
  const statusText = value => statusWords[value] || String(value || 'Prepared').replaceAll('_',' ');
  function setStatus(id, message, error=false) {
    const el=$(id); if (!el) return;
    el.textContent=message; el.setAttribute('role', error ? 'alert' : 'status');
    el.classList.toggle('matter-error',error);
  }
  function modelConnected(m=state.matter){return typeof m?.api_key_configured==='boolean'?m.api_key_configured:!!info.api_key_configured;}
  function executionReady(m=state.matter){return !!m?.can_run&&!m.demo&&modelConnected(m);}
  function normalizedFilename(name){
    const parts=name.split('.'),extension=parts.pop().toLowerCase();
    return `${parts.join('_').replace(/[^A-Za-z0-9_-]/g,'_').slice(0,64)||'document'}.${extension}`;
  }
  const fileStem=name=>/\.[^.]+$/.test(name)?normalizedFilename(name).replace(/\.[^.]+$/,'').toLowerCase():String(name||'').replace(/[^A-Za-z0-9_-]/g,'_').slice(0,64).toLowerCase();
  function hasNewDocuments(m=state.matter,r=state.run){
    if(!r)return false;
    if(typeof r.requires_new_run==='boolean')return r.requires_new_run;
    const stamp=r.started||list(m?.runs).find(item=>item.id===r.id)?.started;
    const started=typeof stamp==='number'?stamp*1000:Date.parse(stamp||'');
    return list(m?.messages).some(message=>message.kind==='documents_saved'&&(!Number.isFinite(started)||Date.parse(message.at)>started));
  }
  function syncSourceRevision(revision){
    if(state.sourceRevision!==null&&state.sourceRevision!==revision){
      state.sourceSequence++;const viewer=$('matterSourceViewer');if(viewer){viewer.hidden=true;viewer.replaceChildren();}
    }
    state.sourceRevision=revision;
  }
  function stop() { window.MatterRecordExplorer?.stop();saveReplyDraft();state.epoch++;if (state.timer) clearInterval(state.timer); state.timer=null; }
  function saveReplyDraft(){
    if(!state.id||!state.replyReady||!$('matterReply')||$('matterReply').disabled)return;
    const value={text:$('matterReply').value,requestId:$('matterRequest')?.value||''};
    if(state.replySnapshot?.text===value.text&&state.replySnapshot?.requestId===value.requestId)return;
    MatterWorkspaceStore.write(state.id,{...value,runId:state.run?.id||''});state.replySnapshot=value;
    updateDraftStatus();
  }
  function updateDraftStatus(){
    const el=$('matterDraftStatus');if(!el)return;
    el.textContent=$('matterReply')?.value?(MatterWorkspaceStore.storageAvailable?'Unsent draft kept in this browser tab. Nothing sent to the agent.':'Unsent draft kept while this page stays open. Browser storage is unavailable.'):'No unsent draft.';
  }
  function restoreReplyDraft(){
    if(state.replyReady)return;
    const saved=MatterWorkspaceStore.read(state.id);$('matterReply').value=saved.text;
    const request=$('matterRequest');
    const targetValid=saved.runId===state.run?.id&&list(state.run?.case?.requests).some(q=>q.id===saved.requestId&&q.status==='open');
    if(saved.text&&request)request.value=targetValid?saved.requestId:'';
    if(saved.text&&saved.requestId&&!targetValid)setStatus('matterReplyStatus','Unsent draft restored as general context. Its original request is no longer selected or open. Choose a current request explicitly before answering it.');
    state.replySnapshot={text:saved.text,requestId:request?.value||''};state.replyReady=true;updateDraftStatus();
  }
  function setPane(){
    // Conversation and work product remain visible together; navigation only moves focus.
    for(const id of ['matterResults','matterConversationPanel'])if($(id))$(id).hidden=false;
  }
  async function loadSwitcher(id){
    try{const response=await api('/api/matters');if(state.id!==id)return;
      const select=$('matterSwitcher');if(!select)return;
      const matters=list(response.matters||response);select.innerHTML=matters.map(m=>`<option value="${e(m.id)}" ${m.id===id?'selected':''}>${e(m.title||m.question||m.id)}</option>`).join('');
      if(!matters.some(m=>m.id===id))select.add(new Option(state.matter?.title||id,id,true,true));
    }catch{const select=$('matterSwitcher');if(select){select.options[0].textContent=state.matter?.title||'Current investigation';}}
  }
  function focusHeading(id,initial=false) { requestAnimationFrame(() => { if(initial&&$('matterView')?.contains(document.activeElement)&&document.activeElement.matches('button,input,select,textarea,a,summary'))return;const el=$(id); if (el) el.focus({preventScroll:true}); }); }
  function readDraft() { try { return JSON.parse(localStorage.getItem(DRAFT_KEY) || '{}'); } catch { return {}; } }
  function saveDraft(form) {
    const value={}; for (const key of ['question','role','jurisdiction','deadline','deliverable']) value[key]=form.elements[key].value;
    try { localStorage.setItem(DRAFT_KEY,JSON.stringify(value)); } catch { setStatus('intakeStatus','Draft storage is unavailable. Keep this page open until you save the investigation.'); }
  }
  function forgetDraft() {
    if($('prepareMatter')?.disabled){setStatus('intakeStatus','Please wait while the investigation is being saved.');return;}
    try { localStorage.removeItem(DRAFT_KEY); } catch {}
    const form=$('intakeForm'); if (form) form.reset(); state.files=[]; renderFiles();
    setStatus('intakeStatus','Intake draft cleared from this browser. Saved matters are unchanged.');
  }
  function modeLine(demo,connected=!!info.api_key_configured) { return demo
    ? '<strong>Demo workspace · saves only.</strong> No agent response or model calls. <a href="#/settings">Connection settings →</a> <span>For investigations, launch <code>./legal-agent --live</code>.</span>'
    : connected
      ? '<strong>Live workspace.</strong> Send or Start shares matter content with your model provider and may incur charges. <a href="#/settings">Connection settings →</a>'
      : '<strong>Connect your model to investigate.</strong> Your question and documents can be saved now. <a class="workspace-setup-link" href="#/settings">Add API key in Settings →</a>'; }
  function updateMode() {
    if($('homeView')&&!$('homeView').hidden&&$('intakeMode')){
      $('intakeMode').innerHTML=modeLine(!!info.isolated);
      $('prepareMatter').textContent=info.isolated?'Save demo intake':info.api_key_configured?'Send to agent':'Save question';
      if($('intakeSendHint'))$('intakeSendHint').textContent=info.isolated?'No model call. Demo saves only.':info.api_key_configured?'Enter to send · Shift+Enter for a new line. Sends records to your provider.':'Add an API key in Settings to receive agent responses.';
    }
    if(state.matter&&$('matterView')&&!$('matterView').hidden&&$('matterMode'))$('matterMode').innerHTML=modeLine(state.matter.demo,modelConnected());
  }
  async function home() {
    stop(); state.id=null; state.matter=null; state.run=null;
    const homeEpoch=state.epoch;
    try { const latest=await api('/api/info');if(homeEpoch!==state.epoch)return;info=latest; }
    catch (_) { if(homeEpoch!==state.epoch)return; }
    const el=$('homeView'); if (!el) return;
    if (!el.querySelector('#intakeForm')) {
      el.innerHTML=`<div class="workspace-title"><div><p class="workspace-eyebrow">YOUR WORKSPACE</p><h1 id="matterHomeHeading" tabindex="-1">Start with your legal question</h1><p>Keep the question, records, and source-linked work together.</p></div></div>
        <div id="intakeMode" class="workspace-mode" role="status"></div><div class="workspace-layout intake-layout"><div class="workspace-main">
          <form id="intakeForm" class="intake-composer">
            <details class="intake-starters"><summary>Choose a task to help frame your question</summary><div class="task-starters" role="group" aria-label="Start with a task"><button type="button" data-intake-task="notice">Understand a notice</button><button type="button" data-intake-task="contract">Review a contract</button><button type="button" data-intake-task="timeline">Build a timeline</button><button type="button" data-intake-task="claims">Challenge a claim</button></div></details>
            <label class="composer-label" for="matterQuestion">Your legal question or task</label>
            <textarea id="matterQuestion" name="question" required minlength="10" maxlength="12000" rows="5" placeholder="What happened, what do you need to decide, and what should the agent help you produce?" aria-describedby="questionHint"></textarea>
            <p id="questionHint" class="field-hint">Describe your legal question in at least 10 characters. Documents are optional.</p><div class="composer-actions intake-send"><button type="submit" class="primary" id="prepareMatter">Save question</button><span id="intakeSendHint">No model call until Send or Start.</span></div><div id="intakeStatus" class="form-status" role="status" aria-live="polite"></div>
            <div class="intake-attachments"><div id="matterDropzone" class="intake-dropzone"><label class="attachment-label" for="matterFiles">Add supporting documents <span>optional</span></label><p id="matterDropHint">Drop files here, or choose them below.</p><input id="matterFiles" type="file" multiple accept=".txt,.md,.pdf" aria-describedby="matterDropHint matterFileTypes"><p id="matterFileTypes" class="field-hint">PDF, text, or Markdown · up to 8 files</p></div><details class="intake-file-limits"><summary>File size and naming requirements</summary><p>PDFs: up to 5 MB (5,000,000 bytes) each. Text / Markdown: up to 256,000 characters each.</p><p>Spaces and punctuation are converted to underscores for storage. Names are limited to 64 characters before the extension; the saved name is shown below. Files must have distinct names before their extensions.</p></details><ul id="intakeFiles" class="attachment-list" aria-label="Selected documents"></ul></div>
            <details class="intake-context"><summary>Add context <span>location, dates, preferred result</span></summary><div class="context-grid">
              <label>Your perspective<select name="role" id="matterRole"><option value="person">I need help understanding a legal issue</option><option value="lawyer">I am preparing legal work</option><option value="reviewer">I am reviewing an argument or draft</option></select></label>
              <label>Jurisdiction or location <span>if known</span><input name="jurisdiction" id="matterJurisdiction" maxlength="200" placeholder="For example: California, US"></label>
              <label>Relevant date or deadline <span>as stated in your records</span><input name="deadline" id="matterDeadline" maxlength="200" placeholder="For example: reply requested by 20 November"></label>
              <label>What would help you?<select name="deliverable" id="matterDeliverable"><option value="reviewable memo">A source-linked explanation</option><option value="chronology">A timeline of events</option><option value="claim review">A review of claims and evidence</option><option value="questions for counsel">Questions to discuss with a lawyer</option></select></label>
            </div></details>

            <div class="draft-note">Text draft saved in this browser. Reattach unsaved files after reloading.<button type="button" id="clearIntake" class="text-button">Clear draft</button></div>
          </form>
          <section class="saved-matters" aria-labelledby="savedMattersHeading"><div class="section-heading"><h2 id="savedMattersHeading">Your investigations</h2><button type="button" class="text-button" id="refreshMatters">Refresh</button></div><label class="matter-search-label" for="matterSearch">Find an investigation<input type="search" id="matterSearch" placeholder="Search questions or titles" autocomplete="off"></label><div id="matterList" aria-live="polite">Loading saved investigations…</div></section>
        </div><aside class="workspace-rail intake-rail"><section class="rail-section"><h2>From question to review</h2><ol class="workflow-steps"><li><strong>Frame the question</strong><span>Save the problem, context, and documents together.</span></li><li><strong>Investigate with the agent</strong><span>In live mode, start the agent from your saved workspace.</span></li><li><strong>Inspect and challenge</strong><span>Check the sources, surface gaps, and review the result.</span></li></ol></section>
          <p class="workspace-caution">Research assistance; a qualified legal professional should review the output. A date you enter is context, not a calculated filing deadline.</p>
        </aside></div>`;
      const form=$('intakeForm'),draft=readDraft();
      for (const key of ['question','role','jurisdiction','deadline','deliverable']) if (draft[key] != null) form.elements[key].value=draft[key];
      form.addEventListener('input',()=>saveDraft(form));
      form.addEventListener('submit',prepare);
      $('matterQuestion').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();if(!$('prepareMatter').disabled)form.requestSubmit();}});
      $('matterQuestion').addEventListener('invalid',()=>setStatus('intakeStatus','Describe the legal question you want help with (at least 10 characters).',true));
      $('matterFiles').addEventListener('change',selectFiles);
      const dropzone=$('matterDropzone');
      dropzone.addEventListener('dragover',event=>{event.preventDefault();if(!$('prepareMatter').disabled){event.dataTransfer.dropEffect='copy';dropzone.classList.add('is-dragging');}});
      dropzone.addEventListener('dragleave',event=>{if(!dropzone.contains(event.relatedTarget))dropzone.classList.remove('is-dragging');});
      dropzone.addEventListener('drop',event=>{event.preventDefault();dropzone.classList.remove('is-dragging');selectFiles({target:{files:event.dataTransfer.files,value:''}});});
      const starters={notice:{question:'Help me understand the notice or demand I received. Identify what it asks me to do, what my records establish, what remains uncertain, and what to ask a qualified lawyer.\n\nMy situation: ',deliverable:'questions for counsel'},contract:{question:'Review the attached contract for obligations, one-sided terms, and questions I should resolve before agreeing. Link each observation to its source and keep missing context explicit.\n\nMy situation and priorities: ',deliverable:'reviewable memo'},timeline:{question:'Build a chronology from my records. Distinguish documented events from assertions, flag conflicting dates, and identify gaps that prevent a reliable account.\n\nWhat happened: ',deliverable:'chronology'},claims:{question:'Test the claims in this matter against the supplied records. Identify supporting and conflicting evidence, missing sources, and questions that could change the assessment.\n\nThe claim I want to examine: ',deliverable:'claim review'}};
      el.querySelectorAll('[data-intake-task]').forEach(button=>button.addEventListener('click',()=>{const task=starters[button.dataset.intakeTask],question=$('matterQuestion');if($('prepareMatter').disabled)return;if((question.value.trim()||($('matterDeliverable').value!=='reviewable memo'&&$('matterDeliverable').value!==task.deliverable))&&!window.confirm('Replace your current question and requested output with this starting point? Your other context and selected files will stay.'))return;question.value=task.question;$('matterDeliverable').value=task.deliverable;saveDraft(form);question.focus();question.setSelectionRange(question.value.length,question.value.length);setStatus('intakeStatus','Starting point added. Describe your situation before preparing the investigation.');}));
      $('matterSearch').addEventListener('input',()=>{const results=$('matterList');if(results._renderMatters)results._renderMatters();});
      $('clearIntake').addEventListener('click',forgetDraft);
      $('refreshMatters').addEventListener('click',loadMatters);
      el.querySelectorAll('[data-example]').forEach(button=>button.addEventListener('click',()=>openDemo(button.dataset.example)));
    }
    $('intakeMode').innerHTML=modeLine(!!info.isolated);
    $('prepareMatter').textContent=info.isolated?'Save demo intake':info.api_key_configured?'Send to agent':'Save question';
      if($('intakeSendHint'))$('intakeSendHint').textContent=info.isolated?'No model call. Demo saves only.':info.api_key_configured?'Enter to send · Shift+Enter for a new line. Sends records to your provider.':'Add an API key in Settings to receive agent responses.';
    await loadMatters();
  }
  async function loadMatters() {
    try {
      const rows=await api('/api/matters');
      const matters=Array.isArray(rows)?rows:(rows.matters||[]);
      const el=$('matterList'); if (!el) return;
      el.classList.remove('matter-error');el.setAttribute('role','status');
      el._renderMatters=()=>{
        const query=($('matterSearch').value||'').trim().toLocaleLowerCase();
        const filtered=matters.filter(m=>[m.title,m.question].join(' ').toLocaleLowerCase().includes(query));
        el.innerHTML=!matters.length?'<div class="matter-empty"><strong>No investigations yet.</strong><p>Describe your question above to save your first one.</p></div>':!filtered.length?'<div class="matter-empty"><strong>No matching investigations.</strong><p>Try another word or clear the search to see all saved work.</p></div>':filtered.map(m=>`<a class="saved-matter" href="#/matter/${encodeURIComponent(m.id)}"><span><strong>${e(m.title||m.question)}</strong><small>${e(statusText(m.status))} · ${list(m.documents).length} document${list(m.documents).length===1?'':'s'}</small></span><span aria-hidden="true">→</span></a>`).join('');
      };
      el._renderMatters();
    } catch(error) { setStatus('matterList',`Could not load saved investigations: ${error.message}`,true); }
  }
  function renderFiles() {
    const el=$('intakeFiles'); if (!el) return;
    el.innerHTML=state.files.map((f,index)=>`<li><span>${e(f.name)}${normalizedFilename(f.name)!==f.name?` <small>Saved as ${e(normalizedFilename(f.name))}</small>`:''} <small>${(f.size/1024).toFixed(0)} KB</small></span><button type="button" data-remove="${index}" aria-label="Remove ${e(f.name)}">Remove</button></li>`).join('');
    el.querySelectorAll('[data-remove]').forEach(button=>button.addEventListener('click',()=>{if($('prepareMatter').disabled){setStatus('intakeStatus','Please wait while the investigation is being saved.');return;}state.files.splice(Number(button.dataset.remove),1);renderFiles();$('matterFiles').focus();}));
  }
  function selectFiles(event) {
    if($('prepareMatter').disabled){setStatus('intakeStatus','Please wait while the investigation is being saved.');return;}
    const next=Array.from(event.target.files||[]),errors=[];let added=0;
    for (const file of next) {
      if (!/\.(txt|md|pdf)$/i.test(file.name)) {errors.push(`${file.name}: choose a .txt, .md, or .pdf file.`);continue;}
      if (/\.pdf$/i.test(file.name)&&file.size>5000000) {errors.push(`${file.name}: PDF files must be 5,000,000 bytes or smaller.`);continue;}
      if (!/\.pdf$/i.test(file.name)&&file.size>1024*1024) {errors.push(`${file.name}: text file is too large. Limit it to 256,000 characters.`);continue;}
      if(state.files.some(f=>fileStem(f.name)===fileStem(file.name))){errors.push(`${file.name}: its saved name conflicts with another selected document (including across file types). Rename one file and add it again.`);continue;}
      if(state.files.length>=8){errors.push(`${file.name}: choose up to 8 files in total.`);continue;}
      state.files.push(file);added++;
    }
    event.target.value='';renderFiles();
    setStatus('intakeStatus',[added?`${added} document${added===1?'':'s'} added.`:'',...errors].filter(Boolean).join(' '),errors.length>0);
  }
  async function serializeFile(file) {
    if (!/\.pdf$/i.test(file.name)) {const body=await file.text();if(body.length>256000)throw new Error(`${file.name}: text exceeds 256,000 characters.`);return {filename:normalizedFilename(file.name),text:body};}
    const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
    return {filename:normalizedFilename(file.name),content_b64:btoa(binary)};
  }
  async function prepare(event) {
    event.preventDefault();const form=event.currentTarget,button=$('prepareMatter');
    if (!form.reportValidity())return;
    button.disabled=true;setStatus('intakeStatus','Saving your question and documents…');
    try {
      const documents=await Promise.all(state.files.map(serializeFile));const body={documents};
      for(const key of ['question','role','jurisdiction','deadline','deliverable'])body[key]=form.elements[key].value.trim();
      const matter=await api('/api/matters',body);
      try{localStorage.removeItem(DRAFT_KEY);}catch{}
      state.files=[];form.reset();renderFiles();
      state.pendingStart=!info.isolated&&!!info.api_key_configured?matter.id:null;
      setStatus('intakeStatus','Investigation saved.');location.hash=`#/matter/${encodeURIComponent(matter.id)}`;
    }catch(error){setStatus('intakeStatus',`Not saved: ${error.message}`,true);}finally{button.disabled=false;}
  }
  async function open(id) {
    stop();state.id=id;state.sourceRevision=null;state.replyReady=false;state.pane=MatterWorkspaceStore.read(id).pane;state.run=null;state.awaitingRun=null;state.sending=null;state.epoch++;state.selectedRun=new URLSearchParams(location.hash.split('?')[1]||'').get('run');state.lastOverview='';state.lastMessages='';state.lastRail='';state.lastResults='';state.lastReplyContext='';
    const el=$('matterView');if(!el)return;
    el.innerHTML=`<div class="matter-toolbar"><label class="matter-switcher" for="matterSwitcher">Investigation<select id="matterSwitcher"><option value="${e(id)}">Loading investigations…</option></select></label><a href="#/home">+ New investigation</a><button type="button" id="exportMatter">Export original agent draft</button></div><div class="workspace-title matter-title"><div><p class="workspace-eyebrow">INVESTIGATION</p><h1 id="matterTitle" tabindex="-1">Loading investigation…</h1><p id="matterSubtitle"></p></div><span class="matter-status" id="matterState" role="status"></span></div>
      <div id="matterMode" class="workspace-mode"></div><div id="matterError" role="alert" class="form-status"></div>
      <section id="matterOverview" class="matter-overview" aria-label="Investigation overview"></section><nav class="workspace-jumps" aria-label="Jump within workspace"><a href="#matterResults" data-workspace-jump="matterResults">Findings & work product ↓</a><a href="#matterConversationPanel" data-workspace-jump="matterConversationPanel">Conversation & steering ↓</a><a href="#matterDocuments" data-workspace-jump="matterDocuments">Documents ↓</a><a href="#matterRecords" data-workspace-jump="matterRecords">Record explorer ↓</a></nav><div id="matterDesk" class="workspace-layout matter-layout desk-layout"><section class="workspace-main" aria-label="Agent workspace">
        <section class="agent-panel" id="matterConversationPanel"><div class="section-heading"><h2 tabindex="-1" id="matterConversationHeading">Copilot</h2><span class="subtle-label">Direct the agent · keep context here</span></div><div id="matterConversation" class="matter-conversation"></div><form id="matterReplyForm" class="matter-reply"><label for="matterReply">Message the agent</label><div id="matterReplyContext"></div><textarea id="matterReply" disabled rows="3" maxlength="12000" required placeholder="Ask a follow-up, add a fact, or challenge a finding…"></textarea><div class="composer-actions"><button type="submit" class="primary" id="sendMatterMessage" disabled>Save message</button><span id="matterReplyHint">Saved locally. No model call until you start or resume.</span></div><p id="matterDraftStatus" class="workspace-pane-hint" role="status"></p><div id="matterReplyStatus" role="status" aria-live="polite" class="form-status"></div></form></section>
        <div class="workspace-product"><section id="matterEditor" class="agent-panel" hidden></section><section id="matterResults" class="agent-panel" aria-labelledby="matterResultsHeading"></section><section id="matterRecords" class="agent-panel"></section></div>
      </section><aside class="workspace-evidence" aria-label="Evidence inspector"><section id="matterSourceViewer" class="agent-panel source-viewer" aria-labelledby="matterSourceHeading" hidden></section><section class="rail-section" id="matterUpload"><label for="matterAddFiles"><strong>Upload documents</strong></label><input id="matterAddFiles" type="file" multiple accept=".txt,.md,.pdf"><p class="field-hint">TXT, Markdown or PDF · up to 5 MB per PDF. Added records are used in the next new investigation.</p><div id="matterUploadStatus" class="form-status" role="status" aria-live="polite"></div></section><aside id="matterRail" class="workspace-rail" aria-label="Investigation context and evidence"></aside></aside></div>`;
    el.querySelectorAll('[data-workspace-jump]').forEach(a=>a.onclick=event=>{event.preventDefault();jumpTo(a.dataset.workspaceJump);});
    $('matterReplyForm').addEventListener('submit',sendMessage);
    $('matterAddFiles').addEventListener('change',uploadDocuments);
    $('matterReply').addEventListener('input',saveReplyDraft);
    $('matterReply').addEventListener('keydown',event=>{
      if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){
        event.preventDefault();
        if(!$('sendMatterMessage').disabled)$('matterReplyForm').requestSubmit();
      }
    });
    $('matterReplyContext').addEventListener('change',saveReplyDraft);
    $('matterSwitcher').onchange=event=>{saveReplyDraft();location.hash=`#/matter/${encodeURIComponent(event.target.value)}`;};
    setPane(state.pane);loadSwitcher(id);
    $('exportMatter').addEventListener('click',exportMatter);
    const openingEpoch=state.epoch;await refresh();if(state.id!==id||state.epoch!==openingEpoch)return;focusHeading('matterTitle',true);
    state.timer=setInterval(()=>{if(location.hash.split('?')[0]===`#/matter/${encodeURIComponent(state.id)}`)refresh();else stop();},3000);
    if(state.pendingStart===id){state.pendingStart=null;await start(false,true);}
  }
  function selectRun(id) {
    if(id===state.selectedRun)return;
    saveReplyDraft();state.replyReady=false;state.awaitingRun=null;state.selectedRun=id;state.epoch++;state.run=null;state.sourceSequence++;
    state.lastResults='';state.lastOverview='';state.lastReplyContext='';
    const viewer=$('matterSourceViewer');if(viewer)viewer.hidden=true;
    history.replaceState(null,'',`#/matter/${encodeURIComponent(state.id)}${id?'?run='+encodeURIComponent(id):''}`);
    $('matterReply').disabled=true;$('sendMatterMessage').disabled=true;
    refresh();
  }
  async function refresh(fresh=false){
    const epoch=state.epoch,id=state.id;
    if(!id)return false;
    if(state.refreshFlight?.epoch===epoch){
      const result=await state.refreshFlight.promise;
      return fresh&&state.id===id&&state.epoch===epoch?refresh():result;
    }
    const flight={epoch};state.refreshFlight=flight;flight.promise=fetchRefresh();
    try{return await flight.promise;}
    finally{if(state.refreshFlight===flight)state.refreshFlight=null;}
  }
  async function fetchRefresh() {
    if(!state.id||state.loading===state.epoch)return;
    const epoch=state.epoch,requested=state.id;state.loading=epoch;
    try{
      const matter=await api(path());if(state.id!==requested||state.epoch!==epoch)return;
      syncSourceRevision(matter.record_revision);
      const runs=list(matter.runs);
      let selected=runs.find(r=>(r.id||r.run_id)===state.selectedRun)||runs[0];
      if(state.awaitingRun){
        const job=list(matter.jobs).find(j=>j.id===state.awaitingRun.job);
        const fresh=runs.find(r=>r.id===job?.run_id)||runs.find(r=>!state.awaitingRun.before.includes(r.id));
        if(fresh){selected=fresh;state.awaitingRun=null;}
        else if(job&&!job.running){state.awaitingRun=null;}
        else selected=null;
      }
      const run=selected?await api(path(`/runs/${encodeURIComponent(selected.id||selected.run_id)}`)):null;
      if(state.id!==requested||state.epoch!==epoch)return;
      state.matter=matter;state.run=run;state.selectedRun=run?run.id:null;
      renderMatter();
      const failed=list(matter.jobs).filter(job=>!job.running&&job.returncode!=null&&Number(job.returncode)!==0).at(-1);
      const latestJob=list(matter.jobs).at(-1);
      setStatus('matterError',failed&&failed===latestJob?'The last investigation process ended unsuccessfully. Your saved question and messages are retained. Check model settings, then retry Start or Resume.':'',!!failed&&failed===latestJob);return true;
    }catch(error){if(state.id===requested&&state.epoch===epoch)setStatus('matterError',`Could not refresh this investigation: ${error.message}. Your unsent message is still here.`,true);}
    finally{if(state.loading===epoch)state.loading=false;}
    return false;
  }
  function jumpTo(id) {
    if(['matterReply','matterConversationPanel'].includes(id))setPane('conversation');
    else if(id==='matterResults')setPane('findings');
    const target=$(id);if(!target)return;
    target.scrollIntoView({block:'start',behavior:'instant'});
    const focus=target.matches('textarea,button,input')?target:target.querySelector('h2,button,textarea');
    if(focus){if(!focus.hasAttribute('tabindex')&&!focus.matches('textarea,button,input'))focus.tabIndex=-1;focus.focus({preventScroll:true});}
  }
  function renderOverview(m,r,busy,historic,blocking){
    const runs=list(m.runs),facts=list(((r||{}).case||{}).facts),requests=list(((r||{}).case||{}).requests).filter(q=>q.status==='open');
    let next=m.status==='incomplete'?'Some documents could not be prepared. Review the upload errors.':!modelConnected(m)&&!m.demo?'Your question is saved. Connect a model in Settings to investigate.':'Your question is saved. Start the agent when ready.',target='matterResults';
    if(m.demo&&!r)next='Saved locally. Open documents or add context; live mode is required for an agent response.';
    if(state.awaitingRun)next='Starting the investigation. Waiting for the new run to appear…';
    if(r)next=blocking?`Answer the agent’s request: ${text(blocking.item||blocking.need||blocking.question||blocking.id).slice(0,180)}`:busy?'The agent is working. Inspect recorded activity and findings as they arrive.':r.status==='paused'?'Inspect the pause details, then resume when ready.':'Inspect the recorded findings and check their sources.';
    if(blocking)target='matterReply';
    if(blocking&&list(r.pending_request_ids).includes(blocking.id)){next='Your answer is saved and awaiting delivery. Resume the investigation to apply it.';target='matterResults';}
    if(hasNewDocuments(m,r)&&!busy&&!historic&&executionReady(m))next='Document preparation changed after this run started. Your next message starts a new investigation with the current records; the earlier findings remain available.';
    if(!m.demo&&!modelConnected(m)&&!busy){next='Your work is saved. Add your API key in Settings to investigate or resume.';target='matterResults';}
    if(recoveryState(r)==='active_elsewhere')next='This run is active outside this server. Inspect saved activity here; execution controls are unavailable.';
    else if(recoveryState(r)==='available')next='The last execution was interrupted. Recover its saved checkpoint, then inspect the records before deciding whether to resume.';
    else if(recoveryHold(r))next='Inspect the interrupted run’s saved activity and records before continuing. Recovery does not establish that unfinished tool effects were rolled back.';
    if(historic)next='You are inspecting an earlier run. Return to the latest run to send directions.';
    const html=`<div class="overview-top"><div><p class="workspace-eyebrow">${historic?'EARLIER RUN':'NEXT STEP'}</p><p class="overview-next">${e(next)}</p></div>${runs.length?`<label class="run-picker-label" for="matterRunPicker">Run history<select id="matterRunPicker">${state.awaitingRun?'<option value="" selected disabled>Starting new investigation…</option>':''}${runs.map((run,index)=>`<option value="${e(run.id)}" ${r&&run.id===r.id?'selected':''}>${index===0?'Latest · ':''}${e(statusText(run.status))} · ${e(run.started?new Date(run.started).toLocaleString():run.id)}</option>`).join('')}</select></label>`:''}</div>
      <div class="overview-bottom"><div class="overview-counts"><span><strong>${list(m.documents).length}</strong> document${list(m.documents).length===1?'':'s'}</span><span><strong>${facts.length}</strong> evidence item${facts.length===1?'':'s'}</span><span><strong>${requests.length}</strong> open request${requests.length===1?'':'s'}</span></div><nav class="matter-jumps" aria-label="Evidence navigation"><button type="button" data-jump="matterDocuments">Documents</button><button type="button" data-jump="matterRecords">Record explorer</button></nav></div>${historic?'<div class="history-notice">Earlier run · findings and exports refer to the selected run. Conversation is shared. <button type="button" id="returnLatestRun">Return to latest run</button></div>':`<button type="button" class="text-button overview-action" data-jump="${target}">${blocking&&target==='matterReply'?'Answer request':r?'Inspect findings':'Open investigation controls'} →</button>`}`;
    changed('matterOverview',html,'lastOverview');
    $('matterOverview').querySelectorAll('[data-jump]').forEach(button=>button.onclick=()=>{if(button.dataset.jump==='matterReply'&&blocking&&$('matterRequest'))$('matterRequest').value=blocking.id;jumpTo(button.dataset.jump);});
    if($('matterRunPicker'))$('matterRunPicker').onchange=event=>selectRun(event.target.value);
    if($('returnLatestRun'))$('returnLatestRun').onclick=()=>selectRun(runs[0].id);
  }
  function changed(id,html,key){const el=$(id);if(el&&state[key]!==html){el.innerHTML=html;state[key]=html;}}
  function renderMatter(){
    syncSourceRevision(state.matter.record_revision);
    const m=state.matter,r=state.run,cs=(r||{}).case||{},historic=!!(r&&list(m.runs)[0]&&list(m.runs)[0].id!==r.id),blocking=r&&r.status==='needs_input'&&list(cs.requests).at(-1)?.status==='open'?list(cs.requests).at(-1):null,busy=runBusy(m,r);
    $('matterTitle').textContent=m.title||m.question; 
    $('matterSubtitle').textContent=[m.jurisdiction||'Jurisdiction not yet specified',m.deliverable||'Source-linked explanation'].join(' · ');
    $('matterState').textContent=historic?'Earlier run · '+statusText(r.status):recoveryState(r)==='active_elsewhere'?'Running outside this server':recoveryState(r)==='available'?'Recovery available':recoveryState(r)==='unavailable'?'Recovery unavailable':busy?'Investigating':!r&&m.demo?'Saved · demo':!r&&!modelConnected(m)?'Model connection needed':!r&&!m.can_run?'Preparation incomplete':statusText(r?r.status:m.status);$('matterMode').innerHTML=modeLine(m.demo,modelConnected(m));
    $('matterState').dataset.phase=historic?'history':['available','unavailable'].includes(recoveryState(r))?'recovery':busy?'running':r?.status||(!executionReady(m)?'setup':'prepared');
    renderOverview(m,r,busy,historic,blocking);
    $('matterReply').disabled=historic||recoveryState(r)==='active_elsewhere';$('sendMatterMessage').disabled=historic||recoveryState(r)==='active_elsewhere'||!!state.sending||state.starting||state.recovering||state.uploading||!!state.awaitingRun;
    $('sendMatterMessage').textContent=state.sending?'Sending…':recoveryHold(r)?'Save message':executionReady(m)?(busy?'Queue message':hasNewDocuments(m,r)?'Send · new investigation':'Send'):'Save message';
    const messages=list(m.messages).filter(message=>message.kind!=='intake_checklist').map(message=>{const user=message.role==='user';const kind=message.kind||'';const ai=kind==='agent'||kind==='model';return `<article class="matter-message ${user?'from-user':'from-assistant'}"><div class="message-meta"><strong>${user?'You':(ai?'Agent':'Saved receipt')}</strong><span>${ai?'Model-generated · review required':(user?'You supplied this':'Saved locally · not an agent response')}</span></div>${message.delivery&&message.delivery.run_id?`<p class="message-delivery">Queued at save for run ${e(message.delivery.run_id)} · receipt does not confirm application</p>`:''}<div class="message-body">${renderMarkdown(text(message.body||message.text))}</div></article>`;}).join('');
    changed('matterConversation',(messages||'<p class="matter-empty">Your intake has been saved. Add context below.</p>')+(!r&&executionReady(m)?`<div class="chat-start"><p>Your question is ready. Ask the agent to investigate it, or add more context below.</p><button type="button" class="primary" id="chatStartAgent" ${state.starting||state.sending||state.uploading||busy?'disabled':''}>${state.starting||busy?'Starting…':'Investigate this question'}</button></div>`:''),'lastMessages');
    if($('chatStartAgent'))$('chatStartAgent').onclick=()=>start(false);
    const missing=list(m.missing);const plan=list(m.plan);
    const docs=list(m.documents);
    $('matterAddFiles').disabled=busy||state.uploading||!!state.sending||state.starting||state.recovering;
    $('matterUpload').querySelector('.field-hint').textContent=busy?'The agent is running. Add records when it pauses.':'PDF, text or Markdown. New documents apply to the next new investigation.';
    const replyRequests=hasNewDocuments(m,r)?[]:list(cs.requests).filter(q=>q.status==='open');
    const replyContext=replyRequests.length?`<label class="reply-request-label" for="matterRequest">Answer a specific request<select id="matterRequest"><option value="">General context or direction</option>${replyRequests.map(q=>`<option value="${e(q.id)}">${e(q.need||q.question||q.item||q.id)}</option>`).join('')}</select></label>`:'';
    if(state.lastReplyContext!==replyContext){
      const selected=$('matterRequest')?.value||'';
      $('matterReplyContext').innerHTML=replyContext;state.lastReplyContext=replyContext;
      if($('matterRequest'))$('matterRequest').value=replyRequests.some(q=>q.id===selected)?selected:$('matterReply').value?'':blocking?blocking.id:'';
      const current=$('matterRequest')?.value||'';
      if(state.replyReady&&state.replySnapshot)state.replySnapshot={...state.replySnapshot,requestId:current};
      if(selected&&selected!==current&&$('matterReply').value)setStatus('matterReplyStatus','Your unsent draft is now general context. Its original request is no longer open. Choose a current request explicitly before answering it.');
    }
    $('matterReplyHint').textContent=recoveryState(r)==='active_elsewhere'?'This run is active outside this server. Execution controls are unavailable here.':recoveryHold(r)?'Messages save only. Inspect recovery details and choose an explicit investigation action below.':historic?'Return to the latest run to send a message.':m.demo?'Demo: saves only; no agent response. Enter to save · Shift+Enter for a new line.':!modelConnected(m)?'Messages save only. Add your API key in Settings to get an agent response.':!m.can_run?'Messages save only. Resolve the intake errors before starting an investigation.':busy?'Enter queues your message for the next resume; it cannot interrupt this slice. Shift+Enter for a new line.':hasNewDocuments(m,r)?'Enter starts a NEW investigation with your uploaded documents. Shift+Enter for a new line. Uses your provider; up to 10 turns.':'Enter to send · Shift+Enter for a new line. Uses your model provider; up to 10 turns.';
    const rail=`<section class="rail-section"><h2>Initial intake checklist</h2>${plan.length?`<ol class="matter-plan">${plan.map(s=>`<li>${e(s)}</li>`).join('')}</ol>`:'<p>The plan will appear once intake is prepared.</p>'}<p class="field-hint">This intake checklist is a starting point; the agent may revise its plan after reading the evidence.</p></section>
      <section class="rail-section"><h2>Initial context to clarify</h2>${missing.length?`<ul class="missing-list">${missing.map(s=>`<li>${e(s)}</li>`).join('')}</ul><p class="field-hint">Reply in the conversation. Missing information must stay explicit.</p>`:'<p>No intake questions flagged. This does not establish that the evidence is complete.</p>'}</section>
      <section class="rail-section" id="matterDocuments"><h2 tabindex="-1">Documents <span>${docs.length}</span></h2>${list(m.upload_errors).length?`<div class="matter-error" role="alert">${list(m.upload_errors).map(f=>`<p>${e(f.file)}: ${e(f.error)}. Retry this file to continue.</p>`).join('')}</div>`:''}${docs.length?`<ul class="matter-documents">${docs.map(d=>`<li><strong>${e(d.file||d.filename||d.id)}</strong><span>${d.state==='quarantined'?'Quarantined · source access blocked':d.state==='transcription_pending'?'Saved · extraction pending':d.state==='transcribed'?'Text extracted':'Saved · extraction not confirmed'}</span>${d.state!=='quarantined'?`<button type="button" class="text-button" data-source-doc="${e(d.id||d.document_id)}" data-source-page="1" aria-label="Read extracted source: ${e(d.file||d.filename||d.id)}">Read extracted source</button>`:''}</li>`).join('')}</ul>`:'<p>No documents added. Findings cannot be grounded in records that have not been provided.</p>'}</section>
      <section class="rail-section"><h2>Context you supplied</h2><dl class="matter-context"><dt>Perspective</dt><dd>${e(({person:'Seeking understanding',lawyer:'Preparing legal work',reviewer:'Reviewing an argument'})[m.role]||m.role||'Not specified')}</dd><dt>Location / jurisdiction</dt><dd>${e(m.jurisdiction||'Not specified')}</dd><dt>Date or deadline</dt><dd>${e(m.deadline||'Not specified')}</dd><dt>Requested output</dt><dd>${e(m.deliverable||'Source-linked explanation')}</dd></dl><p class="field-hint">Dates are user-provided context, not verified filing deadlines.</p></section>`;
    changed('matterRail',rail,'lastRail');
    const railEl=$('matterRail'),documents=$('matterDocuments');
    if(documents)railEl.prepend(documents);
    for(const section of [...railEl.querySelectorAll(':scope > section')]){
      if(section.id==='matterDocuments')continue;
      const details=document.createElement('details');details.className='rail-context-details';
      const summary=document.createElement('summary');summary.textContent=section.querySelector('h2')?.textContent||'Context';
      section.querySelector('h2')?.remove();section.replaceWith(details);details.append(summary,section);
    }
    let result=`<div class="section-heading"><h2 id="matterResultsHeading">Findings & work product</h2><span class="subtle-label">Inspect sources before relying on a draft</span></div>`;
    if(!r){
      result+=`<div class="run-empty"><strong>${m.demo?'Your saved question is ready for live investigation.':!modelConnected(m)?'Connect a model to investigate your saved question.':!m.can_run?'Finish preparing your documents.':'Ready to investigate your question.'}</strong><p>${m.demo?'No findings yet. This demo cannot research or answer your question. Read documents or save context here; launch ./legal-agent --live to investigate.':!modelConnected(m)?'Your question and documents are saved. Add your API key in Settings, then return here to start the investigation.':!m.can_run?'Review the upload errors in Documents and retry the affected files. Your saved question is retained.':'Start the agent to examine the supplied records. Evidence, gaps, and a draft will appear here as they are recorded.'}</p></div>`;
    }else{
      const facts=list(cs.facts),memo=cs.memo;
      result+=`<div class="investigation-status"><strong>${e(recoveryState(r)==='available'?'Recovery available':recoveryState(r)==='unavailable'?'Recovery unavailable':recoveryState(r)==='active_elsewhere'?'Running outside this server':statusText(r.status))}</strong><span>${facts.length} recorded evidence item${facts.length===1?'':'s'} · ${list(cs.requests).filter(q=>q.status==='open').length} open request(s)</span></div>`;
      const requests=list(cs.requests).filter(q=>q.status==='open');
      if(requests.length)result+=`<div class="attention-card"><strong>The agent needs your input</strong>${requests.map(q=>`<p>${e(q.need||q.question||q.item)}</p>`).join('')}<p class="field-hint">Save your answer in the conversation, then resume the investigation.</p></div>`;
      if(['available','unavailable','recovered','active_elsewhere'].includes(recoveryState(r)))result+=`<div class="attention-card" role="status"><strong>${e(({available:'Saved run can be recovered',unavailable:'Saved run needs manual review',recovered:'Interrupted execution recovered',active_elsewhere:'Run active outside this server'})[recoveryState(r)])}</strong><p>${e(r.recovery.detail||'Inspect saved activity and records before continuing.')}</p>${recoveryState(r)==='recovered'?'<p>Inspect saved activity and records before resuming. Tool effects after the last checkpoint may already exist; recovery does not undo them or guarantee exactly-once execution.</p>':''}</div>`;
      if(r.error||r.pause_detail||r.stop_action)result+=`<div class="attention-card"><strong>Investigation paused</strong><p>${e(r.error||r.pause_detail||r.stop_action)}</p></div>`;
      if(list(r.source_changed).length||list(r.source_unavailable).length)result+='<div class="attention-card"><strong>Source changes need review.</strong><p>Some evidence changed or is unavailable. Previous conclusions and checks may be stale.</p></div>';
      if(memo){result+=`<div class="matter-memo"><p class="subtle-label">MODEL DRAFT · ${e(statusText(memo.status||'draft'))}</p><h3>${e(memo.conclusion||'Investigation draft')}</h3>${list(memo.findings).map(f=>`<article class="matter-finding"><div class="finding-status">${e(f.status||'unassessed')}</div><h4>${e(f.item||'Finding')}</h4><p>${e(f.summary)}</p>${list(f.citations).map(c=>citation(c,m.id)).join('')}</article>`).join('')}${list(memo.gaps).length?`<div class="attention-card"><strong>Gaps and limits</strong><ul>${list(memo.gaps).map(g=>`<li>${e(g)}</li>`).join('')}</ul></div>`:''}</div>`;}
      if(facts.length)result+=`<details class="recorded-evidence" ${!memo?'open':''}><summary>Inspect recorded evidence (${facts.length})</summary>${facts.map(f=>`<article><strong>${e(f.item||f.id)}</strong><span class="finding-status">${e(f.stance||'unassessed')}</span>${citation(f,m.id)}</article>`).join('')}</details>`;
      if(!memo&&!facts.length)result+='<p class="matter-empty">No findings have been recorded yet. A running status alone does not mean that evidence has been verified.</p>';
      const steps=list((r.state||{}).plan);
      if(steps.length)result+=`<section class="run-plan"><h3>Agent’s working plan</h3><ol>${steps.map(s=>`<li>${e(typeof s==='object'?(s.step||s.description||s.text||s):s)}</li>`).join('')}</ol></section>`;
      const activity=list(r.events).map(activityLabel).filter(Boolean).slice(-5);
      if(activity.length)result+=`<section class="run-activity" aria-label="Recent agent activity"><h3>Recent activity</h3><ol>${activity.map(item=>`<li>${e(item)}</li>`).join('')}</ol><p class="field-hint">Activity records show what the agent attempted; they do not establish legal correctness.</p></section>`;
      result+=`<details class="run-diagnostics"><summary>Run details</summary><dl class="matter-context"><dt>Run</dt><dd>${e(r.id)}</dd><dt>Status</dt><dd>${e(r.status)}</dd></dl><p>${list(r.events).length} saved activity events. Outputs require human review; tool completion alone is not a verification result.</p></details>`;
    }
    if(r&&!busy)result+=`<div class="evidence-review-action"><h3>Review the evidence and claims</h3><p>Inspect the run’s actual findings in a claim review, check their sources, and record your review decision.</p><button type="button" id="openMatterEvidenceReview" ${state.reviewOpening?'disabled':''}>${state.reviewOpening?'Opening evidence review…':'Open evidence review'}</button><p class="field-hint">Opens a local review work product. This does not run the model or approve its conclusions.</p></div>`;
    const awaitingAnswer=blocking&&!list(r.pending_request_ids).includes(blocking.id);
    const canResume=!historic&&r&&r.recovery?.can_resume!==false&&!hasNewDocuments(m,r)&&['paused','needs_input'].includes(r.status);
    result+=`<div class="run-actions">${historic?'<p>Earlier run selected. Return to the latest run to start or resume an investigation.</p>':recoveryState(r)==='available'?`<button type="button" id="recoverMatter" ${state.recovering||state.starting||state.sending||state.uploading?'disabled':''}>${state.recovering?'Recovering saved run…':'Recover saved run'}</button><p>Updates the saved execution state only. This does not call your model or resume tool execution.</p>`:recoveryState(r)==='active_elsewhere'?'<p class="running-label" role="status">This run is active outside the current server. Execution controls are unavailable here.</p>':busy?'<p class="running-label" role="status">Investigation in progress. Findings refresh automatically. Saved directions will be applied when you resume after this slice.</p>':executionReady(m)?`<div class="run-buttons">${canResume?`<button type="button" class="primary" id="resumeMatter" ${state.starting||state.sending||state.uploading||awaitingAnswer?'disabled':''}>${awaitingAnswer?'Answer the request before resuming':'Resume investigation'}</button>`:''}<button type="button" class="${canResume?'':'primary'}" id="runMatter" ${state.starting||state.sending||state.uploading?'disabled':''}>${hasNewDocuments(m,r)?'Investigate updated documents':r?'Start a new investigation':'Start agent investigation'}</button></div><p>Uses your configured model provider and may incur charges. Runs up to 10 turns. A new investigation includes all saved context and retains earlier runs.</p>`:`<p class="field-hint">${m.demo?'No model calls are available in this demo.':!modelConnected(m)?'Messages are saved only until a model is connected. <a href="#/settings">Add API key in Settings →</a>':'Resolve the document preparation errors before starting.'}</p>`}</div><p class="workspace-caution">An agent draft is research assistance. Inspect original sources and have a qualified legal professional review the result.</p>`;
    changed('matterResults',result,'lastResults');
    window.MatterRecordExplorer?.mount($('matterRecords'),{matterId:m.id,runId:r?.id||'',busy,revision:JSON.stringify([m.updated,m.record_revision,r?.case]),openSource:(doc,page,origin)=>openSource(doc,page,origin)});
    restoreReplyDraft();setPane(state.pane);
    if(window.MatterWorkspaceEditor)MatterWorkspaceEditor.mount($('matterEditor'),{matterId:m.id,runId:!busy?r?.id||'':'',openSource:(doc,page,origin)=>openSource(doc,page,origin),freshness:{changed:r?.source_changed||[],unavailable:r?.source_unavailable||[]},sourceRevision:m.record_revision,isCurrent:()=>state.id===m.id&&state.run?.id===r?.id});
    const reviewButton=$('openMatterEvidenceReview');if(reviewButton)reviewButton.onclick=openEvidenceReview;
    const runButton=$('runMatter');if(runButton)runButton.onclick=()=>start(false);const resumeButton=$('resumeMatter');if(resumeButton)resumeButton.onclick=()=>start(true);const recoverButton=$('recoverMatter');if(recoverButton)recoverButton.onclick=recoverRun;
    $('matterView').querySelectorAll('[data-source-doc]').forEach(a=>{a.onclick=event=>{event.preventDefault();openSource(a.dataset.sourceDoc,a.dataset.sourcePage,a);};});
  }
  function activityLabel(event){
    const p=event.payload||{},tool=text(p.tool_name).replaceAll('_',' ');
    if(event.event_type==='tool_call')return `Agent requested: ${tool||'tool action'}`;
    if(event.event_type==='tool_result'){const result=p.result||{};return `${tool||'Tool action'}: ${result.error?'returned an error':'returned a result'}`;}
    if(event.event_type==='verification')return `${text(p.check||'Recorded check')}: ${p.passed===true?'passed its recorded checks':p.passed===false?'requires attention':'result recorded'}`;
    if(event.event_type==='run_started')return 'Investigation started';
    if(event.event_type==='run_finished')return `Investigation slice ended: ${statusText(p.status)}`;
    return '';
  }
  function citation(c,matterId){
    const doc=c.document_id||c.document||'',page=c.page;
    return `<div class="matter-citation">${c.quote?`<blockquote>${e(c.quote)}</blockquote>`:'<p class="field-hint">No quotation recorded for this reference.</p>'}<span>${e(doc||'Source not identified')}${page?` · page ${e(page)}`:''}</span>${doc?` <button type="button" class="text-button" data-source-doc="${e(doc)}" data-source-page="${e(page||1)}">Read extracted source</button>`:''}</div>`;
  }
  async function openSource(doc,page,origin){
    state.sourceOrigin=origin||document.activeElement;const viewer=$('matterSourceViewer'),requested=state.id,revision=state.matter?.record_revision,sequence=++state.sourceSequence;if(!viewer)return;
    const finding=origin?.closest('.matter-finding, .recorded-evidence article')?.querySelector('h4,strong')?.textContent;
    viewer.hidden=false;viewer.innerHTML=`<div class="source-toolbar"><h2 id="matterSourceHeading" tabindex="-1">Source: ${e(doc)}</h2><button type="button" id="closeMatterSource">Close source</button></div>${finding?`<p class="workspace-pane-hint">From finding: ${e(finding)}</p>`:''}<div id="matterSourceBody" role="status">Loading extracted source…</div>`;
    $('closeMatterSource').onclick=()=>{viewer.hidden=true;const button=state.sourceOrigin?.isConnected?state.sourceOrigin:$('matterView').querySelector(`[data-source-doc="${CSS.escape(doc)}"]`);if(button)button.focus();};
    focusHeading('matterSourceHeading');viewer.scrollIntoView({block:'start',behavior:'instant'});
    try{
      const source=await api(path(`/documents/${encodeURIComponent(doc)}/source`));if(state.id!==requested||sequence!==state.sourceSequence||revision!==state.matter?.record_revision||viewer.hidden)return;
      $('matterSourceBody').removeAttribute('role');
      $('matterSourceBody').innerHTML=`<p class="field-hint">Extracted text from this investigation. Compare with the original file when layout, redactions, or signatures matter. A recorded quote is not an independent verification.</p>${list(source.pages).map(p=>`<article class="source-text-page" id="matterSourcePage-${Number(p.page)||0}"><h3>Page ${e(p.page)}</h3><pre>${e(p.text)}</pre></article>`).join('')||'<p>No extracted text is available for this document.</p>'}`;
      const target=$(`matterSourcePage-${Number(page)||1}`);if(target)target.scrollIntoView({block:'nearest',behavior:'instant'});
    }catch(error){if(state.id===requested&&sequence===state.sourceSequence&&revision===state.matter?.record_revision&&!viewer.hidden)setStatus('matterSourceBody',`Source unavailable: ${error.message}`,true);}
  }
  async function uploadDocuments(event){
    const input=event.target,files=Array.from(input.files||[]),matterId=state.id;
    if(!files.length||state.uploading||state.sending||state.starting)return;
    state.uploading=true;input.disabled=true;renderMatter();
    const current=()=>state.id===matterId&&$('matterAddFiles')===input;
    setStatus('matterUploadStatus','Uploading and extracting documents…');
    try{
      if(files.length+list(state.matter.documents).length>10)throw new Error('A workspace supports up to 10 documents.');
      const stems=new Set(list(state.matter.documents).map(doc=>fileStem(doc.file||doc.filename||doc.id)));
      for(const file of files){const stem=fileStem(file.name);if(stems.has(stem))throw new Error(`${file.name}: its saved name conflicts with an existing or selected document. Rename it before uploading.`);stems.add(stem);}
      const documents=await Promise.all(files.map(async file=>{
        if(!/\.(txt|md|pdf)$/i.test(file.name))throw new Error(`${file.name}: choose TXT, Markdown or PDF.`);
        if(file.size>(/\.pdf$/i.test(file.name)?5000000:1048576))throw new Error(`${file.name}: file is too large.`);
        return serializeFile(file);
      }));
      const matter=await api(`/api/matters/${encodeURIComponent(matterId)}/documents`,{documents});
      if(!current())return;
      state.matter=matter;renderMatter();
      const failures=list(matter.upload_errors);
      setStatus('matterUploadStatus',failures.length?`Some documents failed: ${failures.map(f=>`${f.file}: ${f.error}`).join('; ')}`:`${documents.length} document${documents.length===1?'':'s'} uploaded. Start a new investigation to use the updated records.`,failures.length>0);
      input.value='';
    }catch(error){if(current())setStatus('matterUploadStatus',`Upload failed: ${error.message}`,true);}
    finally{state.uploading=false;if(current())renderMatter();}
  }
  async function sendMessage(event){
    event.preventDefault();const input=$('matterReply'),draft=input.value,body=draft.trim();if(!body||input.disabled||state.sending||state.starting||state.recovering||state.uploading||state.awaitingRun)return;
    const matterId=state.id,runId=state.run?.id,epoch=state.epoch,token={},invoke=executionReady()&&!recoveryHold(state.run)&&recoveryState(state.run)!=='active_elsewhere';state.sending=token;
    const current=()=>state.id===matterId&&state.epoch===epoch;
    renderMatter();setStatus('matterReplyStatus','Saving your message…');
    try{const payload={text:body};const request=$('matterRequest');if(request&&request.value)payload.request_id=request.value;if(!hasNewDocuments()&&state.run&&(['paused','needs_input','running'].includes(state.run.status)||payload.request_id))payload.run_id=runId;else if(hasNewDocuments())delete payload.request_id;if(!invoke&&!(recoveryState(state.run)==='recovered'&&state.run?.recovery?.can_resume)){delete payload.run_id;delete payload.request_id;}
      const m=await api(path('/messages'),payload);MatterWorkspaceStore.clearReply(matterId,draft);if(!current())return;state.matter=m;if(input.value===draft){input.value='';state.replySnapshot={text:'',requestId:$('matterRequest')?.value||''};}updateDraftStatus();renderMatter();
      setStatus('matterReplyStatus',m.delivery&&m.delivery.state==='queued'?'Message saved and queued, not yet applied. Resume after the current slice to deliver it to the agent.':'Message saved. It will be included when you start a new investigation.');input.focus();const refreshed=await refresh(true);
      if(!refreshed){if(current())setStatus('matterReplyStatus','Message saved. Could not refresh the investigation; retry Start once the connection recovers.',true);return;}
      if(current()&&invoke){
        const run=state.run,busy=runBusy(state.matter,run);
        const blocking=list(run?.case?.requests).filter(q=>q.status==='open');
        const unanswered=run?.status==='needs_input'&&blocking.some(q=>!list(run.pending_request_ids).includes(q.id));
        if(recoveryHold(run))setStatus('matterReplyStatus','Message saved. Inspect the recovery details and records before choosing an explicit investigation action.');
        else if(busy)setStatus('matterReplyStatus',m.delivery?.state==='queued'?'Message queued. It will be applied when this investigation resumes after the current slice.':'Message saved for the next new investigation. It has not been delivered to the current run.');
        else if(unanswered&&!hasNewDocuments())setStatus('matterReplyStatus','Message saved. Select and answer the open request above before the agent can continue.');
        else await start(!!run&&!hasNewDocuments()&&['paused','needs_input'].includes(run.status),true);
      }
    }catch(error){if(current())setStatus('matterReplyStatus',`Message not saved: ${error.message}`,true);}
    finally{if(state.sending===token)state.sending=null;if(state.id===matterId&&state.matter)renderMatter();}
  }
  async function openEvidenceReview(){
    if(state.reviewOpening||!state.run)return;
    state.reviewOpening=true;const matterId=state.id,runId=state.run.id;renderMatter();
    try{
      const endpoint=path(`/runs/${encodeURIComponent(runId)}/artifacts`),existing=await api(endpoint);
      let instance=list(existing.instances).find(item=>item.template==='citation-claim-audit');
      if(!instance){const opened=await api(endpoint,{title:'Evidence and claim review',template:'citation-claim-audit'});instance=typeof opened.instance==='string'?{id:opened.instance}:opened.instance;}
      if(!instance||!instance.id)throw new Error('The review did not return a saved work product.');
      if(state.id===matterId&&state.run?.id===runId)location.hash=`#/artifact/${encodeURIComponent(runId)}/${encodeURIComponent(instance.id)}?matter=${encodeURIComponent(matterId)}`;
    }catch(error){if(state.id===matterId)setStatus('matterReplyStatus',`Could not open evidence review: ${error.message}`,true);}
    finally{state.reviewOpening=false;if(state.id===matterId&&state.matter)renderMatter();}
  }
  async function recoverRun(){
    if(state.recovering||state.starting||state.sending||state.uploading||recoveryState(state.run)!=='available')return;
    const matterId=state.id,runId=state.run.id,epoch=state.epoch;
    const current=()=>state.id===matterId&&state.run?.id===runId&&state.epoch===epoch;
    state.recovering=true;renderMatter();setStatus('matterReplyStatus','Recovering saved execution state…');
    try{await api(path(`/runs/${encodeURIComponent(runId)}/recover`),{});if(!current())return;const refreshed=await refresh(true);if(current())setStatus('matterReplyStatus',refreshed?(recoveryState(state.run)==='recovered'?'Saved run recovered. Inspect the activity and records before choosing an investigation action. No model was called.':'Recovery request completed. Inspect the latest run status before continuing. No model was called.'):'Recovery was requested, but status could not be refreshed. Refresh before retrying.',!refreshed);}
    catch(error){if(current())setStatus('matterReplyStatus',`Could not recover saved run: ${error.message}`,true);}
    finally{state.recovering=false;if(state.id===matterId&&state.matter)renderMatter();}
  }
  async function start(resume,fromSend=false){
    if(state.recovering||['available','active_elsewhere'].includes(recoveryState(state.run))){setStatus('matterReplyStatus','Resolve the run recovery state before starting execution.',true);return;}
    if(resume&&state.run?.recovery?.can_resume===false){setStatus('matterReplyStatus','This saved run cannot resume. Inspect its records and start a new investigation when ready.',true);return;}
    if(state.sending&&!fromSend){setStatus('matterReplyStatus','Wait for your message to finish saving before starting the agent.');return;}
    if(state.uploading){setStatus('matterReplyStatus','Wait for the document upload to finish before starting the agent.');return;}
    if(!executionReady()){setStatus('matterReplyStatus',!modelConnected()?'Your message is saved. Add your API key in Settings to investigate.':'Finish document preparation before investigating.',true);return;}
    if(resume&&hasNewDocuments()){setStatus('matterReplyStatus','New documents need a new investigation. Choose Investigate updated documents or send a message to start it.',true);return;}
    if(state.starting)return;const matterId=state.id,epoch=state.epoch,runId=state.run?.id,before=list(state.matter?.runs).map(r=>r.id);
    const current=()=>state.id===matterId&&state.epoch===epoch;
    state.starting=true;renderMatter();setStatus('matterReplyStatus',resume?'Resuming the agent…':'Starting the agent…');
    try{const job=await api(resume?path(`/runs/${encodeURIComponent(runId)}/resume`):path('/start'),{turns:10});if(!current())return;
      state.selectedRun=resume?runId:null;state.awaitingRun=resume?null:{job:job.id,before};state.epoch++;
      const launchEpoch=state.epoch;
      history.replaceState(null,'',`#/matter/${encodeURIComponent(matterId)}`);const refreshed=await refresh();
      if(state.id!==matterId||state.epoch!==launchEpoch)return;
      const latest=list(state.matter?.jobs).find(item=>item.id===job.id)||job;
      const failed=latest.returncode!=null&&Number(latest.returncode)!==0;
      if(failed)setStatus('matterReplyStatus','Investigation could not start or continue. Your question and messages are saved. Check Settings, then retry Start or Resume.',true);
      else if(!refreshed)setStatus('matterReplyStatus','Start was requested, but its status could not be refreshed. Your work is saved. Refresh before retrying.',true);
      else setStatus('matterReplyStatus','Agent started. Follow progress and findings in the work area.');
    }catch(error){if(current())setStatus('matterReplyStatus',`Could not start: ${error.message}`,true);}
    finally{state.starting=false;if(state.id===matterId&&state.matter){state.lastResults='';renderMatter();}}
  }
  async function exportMatter(){
    const id=state.id,runId=state.run?.id,epoch=state.epoch;
    if(!await refresh(true)||state.id!==id||state.run?.id!==runId||state.epoch!==epoch){setStatus('matterReplyStatus','Export could not refresh the selected run. Try again.',true);return;}
    const m=state.matter;if(!m)return;const r=state.run,cs=(r||{}).case||{},memo=cs.memo;
    const lines=[`# ${m.title||m.question}`,'',`Question: ${m.question}`,`Jurisdiction: ${m.jurisdiction||'Not specified'}`,`Date/deadline supplied by user: ${m.deadline||'Not specified'}`,`Requested output: ${m.deliverable||'Not specified'}`,`Selected run: ${r?r.id:'No run'}`,'',m.demo?'DEMO: no model investigation performed.':'Research assistance. Qualified legal review is required.','','Original agent draft and saved conversation. Reviewer edits are excluded; use Export saved review in Your editable review for the saved human revision.','',...(list(r?.source_changed).length||list(r?.source_unavailable).length?['WARNING: Evidence changed or is unavailable. Prior conclusions and checks may be stale.','']:[]),'## Saved conversation','',...list(m.messages).flatMap(s=>[`### ${s.role==='user'?'User':'Intake / agent message'}`,text(s.body||s.text),''])];
    if(memo){lines.push('## Agent draft',`Status: ${memo.status||'unassessed'}`,'',text(memo.conclusion),'');for(const f of list(memo.findings)){lines.push(`### ${f.item||'Finding'} (${f.status||'unassessed'})`,text(f.summary),'');for(const c of list(f.citations))lines.push(`Source: ${c.document_id||'Unknown'} page ${c.page||'unknown'}`,`> ${c.quote||'No quotation recorded'}`,'');}lines.push('## Gaps',...list(memo.gaps).map(g=>`- ${text(g)}`));}
    else lines.push('## Findings','No model findings recorded.');
    const blob=new Blob([lines.join('\n')],{type:'text/markdown;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`legal-agent-${m.id}.md`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    setStatus('matterReplyStatus','Original agent draft and conversation exported. Saved review edits are excluded.');
  }
  window.addEventListener('pagehide',saveReplyDraft);
  window.MatterUI={home,open,refresh,stop,loadMatters,exportMatter,updateMode};
})();
