/* Explicit, versioned editing of an audit copy. Never changes the model memo. */
(() => {
  'use strict';
  const mounts = new WeakMap(), drafts = new Map();
  const node = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
  };
  const rows = value => Array.isArray(value) ? value : [];
  async function request(path, body) {
    const response=await fetch(path,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const result=await response.json().catch(()=>({}));
    if(!response.ok){const error=new Error(result.error||response.statusText);error.status=response.status;throw error;}
    return result;
  }
  const md=value=>String(value??'').replace(/[\\`*_{}[\]()<>#|]/g,'\\$&').replace(/\n/g,'\n  ');
  const outcome=value=>String(value||'not run').replaceAll('_',' ');
  function clear(container) {
    const old = mounts.get(container);
    if (old) old.disposed = true;
    mounts.delete(container);
    container.replaceChildren();
    container.hidden=true;
  }
  function mount(container, options) {
    if (!container) return;
    const {matterId, runId} = options || {};
    if (!/^matter-[a-f0-9]{32}$/.test(matterId || '') || !/^run-[A-Za-z0-9_-]+$/.test(runId || '')) {
      clear(container); return;
    }
    const previous = mounts.get(container);
    if (previous && previous.matterId === matterId && previous.runId === runId) {
      previous.isCurrent = options.isCurrent;
      previous.openSource=options.openSource;previous.freshness=options.freshness;
      previous.updateFreshness?.();
      if(previous.sourceRevision!==options.sourceRevision){
        previous.sourceRevision=options.sourceRevision;previous.invalidate?.();
      }
      return;
    }
    clear(container);
    container.hidden=false;
    const state = {matterId, runId, isCurrent:options.isCurrent, openSource:options.openSource, freshness:options.freshness, sourceRevision:options.sourceRevision, generation:0, conflicted:new Set(), disposed:false, busy:false, instance:null};
    mounts.set(container, state);
    const current = () => !state.disposed && mounts.get(container) === state &&
      (typeof state.isCurrent !== 'function' || state.isCurrent());
    const base = `/api/matters/${encodeURIComponent(matterId)}/runs/${encodeURIComponent(runId)}/artifacts`;
    const draftKey = rowId => `${matterId}/${runId}/${state.instance.id}/${rowId}`;
    const heading = node('h3', 'Your editable review');
    const explanation = node('p', 'Edit a separate work product beside the conversation. The original model draft stays unchanged. Saving records a new version and requires a fresh review.', 'field-hint');
    const status = node('p', '', 'form-status'); status.setAttribute('role','status'); status.setAttribute('aria-live','polite');
    const content = node('div');
    const freshness=node('p','','field-hint');freshness.dataset.editorFreshness='';
    state.updateFreshness=()=>{
      const changed=rows(state.freshness?.changed),unavailable=rows(state.freshness?.unavailable);
      freshness.textContent=changed.length||unavailable.length?'Source changes or unavailable documents require a fresh review. Previous checks may be stale.':'';
    };
    state.updateFreshness();
    const open = node('button', 'Create editable review'); open.type='button'; open.id='createWorkspaceReview';
    const hint = node('p', 'Uses cited findings from this run. Reuses an existing claim review when available. No model call or approval.', 'field-hint');
    container.append(heading, explanation, open, hint, freshness, status, content);
    const say = (message, error=false) => {
      if (!current()) return;
      status.textContent=message; status.setAttribute('role',error?'alert':'status');
    };
    const setBusy = value => {
      state.busy=value;
      open.disabled=value;
      for (const control of content.querySelectorAll('button,textarea')) control.disabled=value;
      if(!value)for(const article of content.querySelectorAll('[data-editor-row]')){
        const input=article.querySelector('textarea'),unchanged=input.value===input.dataset.savedAssertion;
        article.querySelector('[data-editor-save]').disabled=unchanged||state.conflicted.has(article.dataset.editorRow);
        article.querySelector('[data-editor-cancel]').disabled=unchanged;
      }
    };
    state.invalidate=()=>{
      state.generation++;state.busy=false;open.disabled=false;
      content.replaceChildren(node('p','Sources or recorded evidence changed. Reload saved review before inspecting or exporting its evidence. Your unsent edits are preserved.','field-hint'));
      content.dataset.editorInvalidated='true';
      open.textContent='Reload saved review';
      say('Recorded evidence changed. Reload the saved review to check current source access and check results. Unsent edits are preserved.',true);
    };
    function citation(c){
      const block=node('div',undefined,'matter-citation');
      block.append(node('p',`${c.document_id||'Unknown source'} · page ${c.page||'unknown'}`),node('blockquote',c.quote||'No quotation recorded.'));
      if(c.document_id&&typeof state.openSource==='function'){
        const button=node('button','Inspect cited source');button.type='button';button.dataset.editorSource='';
        button.onclick=()=>{if(current())state.openSource(c.document_id,c.page||1,button);};block.append(button);
      }
      return block;
    }
    async function exportReview(){
      if(state.busy||!state.instance||!current())return;
      const generation=state.generation;setBusy(true);say('Preparing saved review export…');
      try{
        // Read the scope-filtered live report, never cached textarea contents or old citations.
        const report=await request(`${base}/${encodeURIComponent(state.instance.id)}/export`);
        if(!current()||generation!==state.generation)return;
        const review=report.review||{};
        const lines=[`# Saved review: ${md(report.title||report.instance)}`,'',`Matter: ${md(matterId)}`,`Run: ${md(runId)}`,`Review artifact: ${md(report.instance)}`,`Saved version: ${md(report.exported_version)}`,`Exported at: ${new Date().toISOString()}`,'','Separate reviewer-edited work product. The original agent draft is unchanged. Unsent edits are excluded.','',`Review decision: ${md(review.decision||'pending')}`,`Review current for this snapshot: ${review.current===true?'yes':review.current===false?'no — stale':'not established'}`,`Approval scope: ${md(review.scope||'not established')}`,`Review notes: ${md(review.notes||'none')}`,...rows(review.qualifications).map(q=>`Qualification: ${md(q)}`),'','## Limitations',...rows(report.limits).map(x=>`- ${md(x)}`)];
        if(rows(state.freshness?.changed).length||rows(state.freshness?.unavailable).length)lines.push('- Source changes or unavailable documents were reported by the workspace. Previous checks may be stale.');
        for(const row of rows(report.rows)){
          lines.push('',`## ${md(row.row_id)} — ${md(row.kind)}`,`Assertion revision: ${md(row.assertion_version)}`,'',md(row.assertion),'',`Quote checks: ${md(outcome(row.structural?.outcome))}`,`Model support assessment: ${md(outcome(row.semantic?.outcome))}`,...rows(row.limitations).map(x=>`- ${md(x)}`));
          for(const c of rows(row.citations))lines.push('',`Source: ${md(c.document_id)} · page ${md(c.page)}`,`Quotation: ${md(c.quote||'No quotation recorded.')}`);
          if(row.contested?.contested){lines.push('','Opposing evidence recorded:');for(const c of rows(row.contested.opposing))lines.push(`- ${md(c.document_id)} · page ${md(c.page)}: ${md(c.quote)}`);}
        }
        if(rows(report.defects).length){lines.push('','## Recorded review defects');for(const d of report.defects)lines.push(`- ${md(d.id||d.defect_id)} · ${md(d.severity)} · ${md(d.status)}: ${md(d.rationale||d.category)}`);}
        const challenge=report.adversarial||{};
        if(challenge.id)lines.push('','## Recorded adversarial challenge',`Strongest argument against: ${md(challenge.strongest_against||'not recorded')}`,`Alternative theory: ${md(challenge.alternative_theory||'not recorded')}`,`Fact that could reverse the result: ${md(challenge.reversing_fact||'not recorded')}`);
        const objections=rows(report.panel?.objections?.open);
        if(objections.length){lines.push('','## Open recorded objections');for(const objection of objections)lines.push(`- ${md(objection.id)}: ${md(objection.objection)}`);}
        const blob=new Blob([lines.join('\n')],{type:'text/markdown;charset=utf-8'}),url=URL.createObjectURL(blob),link=node('a');
        link.href=url;link.download=`saved-review-${runId}-${report.instance}-v${report.exported_version}.md`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
        say(`Saved review version ${report.exported_version} exported. Unsent edits were excluded.`);
      }catch(error){if(current()&&generation===state.generation)say(`Review export failed: ${error.message}`,true);}
      finally{if(current()&&generation===state.generation)setBusy(false);}
    }
    function render() {
      content.replaceChildren();delete content.dataset.editorInvalidated;
      const inst=state.instance;
      if (!inst) return;
      const version=rows(inst.versions).at(-1)?.v||rows(inst.versions).length;
      const currency=state.reviewStatus?.review_current;
      const meta=node('p', `Saved review version ${version} · ${rows(inst.rows).length} claims · Review: ${inst.review?.decision || 'pending'}${currency===false?' (stale)':currency===true?' (current snapshot)':' (currentness not established)'}`, 'field-hint');
      meta.dataset.editorVersion=String(version); content.append(meta);
      const exportButton=node('button','Export saved review as Markdown');exportButton.type='button';exportButton.dataset.editorExport='';
      exportButton.onclick=exportReview;content.append(exportButton);
      content.append(node('p','Exports the latest saved review, including review status and limitations. Unsent edits are excluded.','field-hint'));
      if (!rows(inst.rows).length) content.append(node('p','This review has no editable claims.'));
      for (const row of rows(inst.rows)) {
        const article=node('article',undefined,'matter-finding'); article.dataset.editorRow=String(row.row_id);
        const label=node('label',`${row.kind === 'conclusion' ? 'Conclusion' : 'Finding'} · ${row.row_id}`);
        const input=node('textarea'); input.rows=4; input.maxLength=12000; input.dataset.editorAssertion='';
        input.id=`workspace-claim-${row.row_id}`; label.htmlFor=input.id;
        const original=String(row.assertion || ''), key=draftKey(row.row_id);
        input.dataset.savedAssertion=original;
        input.value=drafts.has(key)?drafts.get(key):original;
        const note=node('p',input.value!==original?'Unsent edit restored in this page.':'', 'field-hint');
        const controls=node('div',undefined,'composer-actions');
        const save=node('button','Save revision'); save.type='button';save.dataset.editorSave=String(row.row_id);
        const cancel=node('button','Cancel edit'); cancel.type='button';cancel.dataset.editorCancel=String(row.row_id);
        const sync=()=>{const changed=input.value!==original;save.disabled=!changed||state.busy||state.conflicted.has(String(row.row_id));cancel.disabled=!changed||state.busy;};
        input.addEventListener('input',()=>{
          if(input.value===original)drafts.delete(key);else drafts.set(key,input.value);
          note.textContent=input.value!==original?'Unsent edit kept while this page stays open. Saving does not verify the claim.':'';sync();
        });
        cancel.addEventListener('click',()=>{if(state.busy)return;drafts.delete(key);input.value=original;note.textContent='Edit discarded; saved revision unchanged.';sync();input.focus();});
        save.addEventListener('click',async()=>{
          if(state.busy||!current()||input.value===original)return;
          const assertion=input.value;
          if(!assertion.trim()){say('Enter a claim before saving. Use the full review tools to remove or qualify a claim.',true);input.focus();return;}
          const generation=state.generation;setBusy(true);say('Saving revision…');
          let persisted=false;
          try {
            await request(`${base}/${encodeURIComponent(inst.id)}/revise`,{row:row.row_id,assertion,expected_assertion_version:row.assertion_version});
            persisted=true;
            if(!current()||generation!==state.generation)return;
            if(drafts.get(key)===assertion)drafts.delete(key);
            const response=await request(`${base}/${encodeURIComponent(inst.id)}`);
            if(!current()||generation!==state.generation)return;
            state.instance=response.instance;state.reviewStatus=response.status;
            if(!state.instance||state.instance.id!==inst.id)throw new Error('The saved review could not be read back.');
            state.busy=false;render();
            say('Revision saved as a new version. The original model draft is unchanged. This review needs fresh checks and review.');
            [...content.querySelectorAll('[data-editor-assertion]')].find(el=>el.closest('[data-editor-row]').dataset.editorRow===String(row.row_id))?.focus();
          } catch(error) {
            if(!current()||generation!==state.generation)return;
            if(error.status===409){
              state.conflicted.add(String(row.row_id));
              say('This claim changed elsewhere. Your unsent edit is preserved. Reload saved review, compare the latest saved assertion with your draft, then save deliberately.',true);
              setBusy(false);open.focus();
            }else say(persisted?`Revision saved, but its latest version could not be loaded. Reopen the review to confirm it. ${error.message}`:`Revision was not saved: ${error.message}`,true);
          } finally {
            if(current()&&generation===state.generation)setBusy(false);
          }
        });
        controls.append(save,cancel);article.append(label,input,controls,note);
        if(input.value!==original){const saved=node('details');saved.append(node('summary','Compare with latest saved assertion'),node('p',original));article.append(saved);}
        const checks=row.checks||{},stale=row.stale||{};
        article.append(node('p',`Recorded finding: ${outcome(row.finding_status||'unassessed')} · Quote checks: ${outcome(checks.structural?.outcome)}${stale.structural?' (stale)':''} · Model support assessment: ${outcome(checks.semantic?.outcome)}${stale.semantic?' (stale)':''}`,'field-hint'));
        for(const message of [...rows(stale.reasons),...rows(checks.structural?.problems),...rows(checks.semantic?.problems)])article.append(node('p',message,'field-hint'));
        const evidence=node('details');evidence.open=row.kind!=='conclusion';evidence.append(node('summary',`Cited evidence (${rows(row.citations).length})`));
        for(const c of rows(row.citations))evidence.append(citation(c));
        if(!rows(row.citations).length)evidence.append(node('p','No citations recorded for this claim.'));
        article.append(evidence);
        if(row.contested?.contested){const contrary=node('details');contrary.open=true;contrary.append(node('summary','Opposing evidence recorded'));for(const c of rows(row.contested.opposing))contrary.append(citation(c));article.append(contrary);}
        content.append(article);sync();
      }
      const full=node('a','Open full review and version history');
      full.href=`#/artifact/${encodeURIComponent(runId)}/${encodeURIComponent(inst.id)}?matter=${encodeURIComponent(matterId)}`;
      content.append(full);
      open.textContent='Reload saved review';
    }
    open.addEventListener('click',async()=>{
      if(state.busy||!current())return;
      const generation=state.generation;setBusy(true);say('Opening saved review…');
      try {
        const response=await request(base);
        if(!current()||generation!==state.generation)return;
        let instance=rows(response.instances).find(item=>item.template==='citation-claim-audit');
        if(!instance){
          const created=await request(base,{title:'Evidence and claim review',template:'citation-claim-audit'});
          instance=typeof created.instance==='string'?{id:created.instance}:created.instance;
        }
        if(!current()||generation!==state.generation)return;
        if(!instance?.id)throw new Error('No saved review was returned.');
        const detail=await request(`${base}/${encodeURIComponent(instance.id)}`);
        if(!current()||generation!==state.generation)return;
        if(detail.instance?.template!=='citation-claim-audit')throw new Error('This work product cannot be edited here.');
        state.instance=detail.instance;state.reviewStatus=detail.status;state.conflicted.clear();state.busy=false;render();say('Review loaded. Unsent edits are preserved; compare them with the latest saved assertion before saving.');
      } catch(error){if(current()&&generation===state.generation)say(`Could not open an editable review: ${error.message}`,true);}
      finally{if(current()&&generation===state.generation)setBusy(false);}
    });
  }
  window.MatterWorkspaceEditor={mount,clear};
})();
