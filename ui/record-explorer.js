/* Local, read-only record views. No model call and no legal assessment. */
(() => {
  'use strict';
  let current=null, request=null;
  const views=[['documents','Documents'],['claims','Recorded claims'],['citations','Source citations'],['open-requests','Open requests'],['review-defects','Review defects']];
  const node=(tag,text,className)=>{const el=document.createElement(tag);if(text!=null)el.textContent=String(text);if(className)el.className=className;return el;};
  function stop(){request?.abort();request=null;current=null;}
  function mount(el,context){
    if(!el)return;
    const key=JSON.stringify([context.matterId,context.runId]);
    if(current?.key===key&&current.el===el){
      if(current.revision!==context.revision&&(current.hasResult||request)){
        request?.abort();request=null;current.sequence++;current.hasResult=false;
        current.result.replaceChildren(node('p','Records changed. Refresh this view to inspect a new snapshot.','record-query-stale'));
        current.button.disabled=false;current.button.textContent='Refresh view';
      }
      Object.assign(current,context);return;
    }
    stop();el.replaceChildren();
    const heading=node('h2','Record explorer');heading.id='recordExplorerHeading';
    const header=node('div',null,'section-heading');header.append(heading,node('span','Read only · no model call','subtle-label'));
    const description=node('p','Inspect this investigation’s saved records. These views report what was recorded; they do not assess legal correctness.','field-hint');
    const form=node('form',null,'record-query-form');const label=node('label','Record view');label.htmlFor='recordQueryView';
    const select=node('select');select.id='recordQueryView';for(const [id,title]of views){const option=node('option',title);option.value=id;select.append(option);}
    const button=node('button','Open view');button.type='submit';
    form.append(label,select,button);
    const result=node('div',null,'record-query-result');result.setAttribute('aria-live','polite');
    result.append(node('p',context.runId?'Choose a view to inspect the selected run.':'Documents are available before a run. Claims and review records appear after investigation.','field-hint'));
    el.append(header,description,form,result);el.setAttribute('aria-labelledby',heading.id);
    current={...context,key,el,select,button,result,sequence:0,hasResult:false};
    form.addEventListener('submit',event=>{event.preventDefault();load(current);});
    select.addEventListener('change',()=>{if(current?.el===el)load(current);});
  }
  async function load(ctx){
    if(!ctx||current!==ctx)return;
    request?.abort();const controller=new AbortController();request=controller;
    const sequence=++ctx.sequence, preset=ctx.select.value, revision=ctx.revision;
    ctx.hasResult=false;ctx.button.disabled=true;ctx.button.textContent='Loading…';
    ctx.result.replaceChildren(node('p','Reading a fresh snapshot…','field-hint'));
    const timer=setTimeout(()=>controller.abort(),15000);
    const valid=()=>current===ctx&&ctx.sequence===sequence&&ctx.revision===revision&&ctx.el.isConnected;
    try{
      const params=new URLSearchParams({preset,run:ctx.runId||''});
      const response=await fetch(`/api/matters/${encodeURIComponent(ctx.matterId)}/records?${params}`,{signal:controller.signal,cache:'no-store'});
      if(!response.ok)throw Error(response.status===404?'This run is unavailable in the investigation.':'The record view could not be loaded. Refresh and try again.');
      const data=await response.json();if(!valid())return;
      if(data.snapshot?.matter_id!==ctx.matterId||(data.snapshot?.run_id||'')!==(ctx.runId||''))throw Error('The record snapshot does not match this workspace.');
      show(ctx,data);ctx.hasResult=true;
    }catch(error){if(valid()){
      const message=error.name==='AbortError'?'The record view timed out. Try again.':error.message;
      const alert=node('p',message,'matter-error');alert.setAttribute('role','alert');ctx.result.replaceChildren(alert);
    }}finally{clearTimeout(timer);if(valid()){ctx.button.disabled=false;ctx.button.textContent='Refresh view';}if(request===controller)request=null;}
  }
  function show(ctx,data){
    const out=ctx.result;out.replaceChildren();
    const count=Array.isArray(data.rows)?data.rows.length:0;
    out.append(node('p',`${count} record${count===1?'':'s'}${data.truncated?' · more records omitted':''} · snapshot loaded ${new Date().toLocaleTimeString()}`,'record-query-summary'));
    if(!count)out.append(node('p','No matching records in this snapshot. This does not establish that no evidence or legal issue exists.','field-hint'));
    else{
      const scroll=node('div',null,'record-query-table');scroll.tabIndex=0;scroll.setAttribute('role','region');scroll.setAttribute('aria-label','Record results; scroll horizontally for more columns');
      const table=node('table');const caption=node('caption',views.find(v=>v[0]===data.preset)?.[1]||'Record results');table.append(caption);
      const columns=data.columns||[];
      const internal=new Set(['owner_type','owner_id','artifact_id','claim_id','request_id','defect_id']);
      const priority=['source_handle','file','document_id','page','quote','text','item','need','rationale','status'];
      const visible=columns.map((name,index)=>({name,index})).filter(col=>!internal.has(col.name)).sort((a,b)=>{
        const rank=name=>priority.includes(name)?priority.indexOf(name):priority.length;
        return rank(a.name)-rank(b.name)||a.index-b.index;
      });
      const hidden=columns.map((name,index)=>({name,index})).filter(col=>internal.has(col.name));
      const label=name=>name==='source_handle'?'Source':String(name).replaceAll('_',' ');
      const head=node('thead'),headrow=node('tr');for(const col of visible){const th=node('th',label(col.name));th.scope='col';headrow.append(th);}if(hidden.length)headrow.append(node('th','Record details'));head.append(headrow);table.append(head);
      const handles=new Map((data.source_handles||[]).map(h=>[h.handle,h]));const body=node('tbody');
      for(const row of data.rows){const tr=node('tr');for(const col of visible){const value=row[col.index],td=node('td');td.dataset.label=label(col.name);const source=handles.get(value);
        if(source&&source.matter_id===ctx.matterId&&(source.run_id||'')===(ctx.runId||'')){
          const button=node('button',`Inspect ${source.document_id} · p. ${source.page||1}`,'text-button');button.type='button';button.onclick=()=>{if(current===ctx&&ctx.hasResult)ctx.openSource(source.document_id,source.page||1,button);};td.append(button);
        }else td.textContent=value==null?'—':String(value);tr.append(td);
      }if(hidden.length){const td=node('td');td.dataset.label='Record details';const details=node('details');details.append(node('summary','Identifiers'));for(const col of hidden)details.append(node('p',`${label(col.name)}: ${row[col.index]??'—'}`));td.append(details);tr.append(td);}body.append(tr);}table.append(body);scroll.append(table);out.append(scroll);
    }
    if(data.cells_truncated)out.append(node('p','Long cells were shortened. Open the source for full context.','field-hint'));
    for(const warning of data.warnings||[])out.append(node('p',warning,'field-hint'));
    const details=node('details',null,'record-query-details');details.append(node('summary','Query and snapshot details'));
    details.append(node('p',`Snapshot ${data.snapshot.id}`,'field-hint'),node('pre',data.sql||''));
    details.append(node('p','Local record query. Auto Ontology is not connected. Refresh after source changes; this is a saved-record snapshot, not a live legal assessment.','field-hint'));out.append(details);
  }
  window.MatterRecordExplorer={mount,stop};
})();
