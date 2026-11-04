/* Unsent workspace state stays in this tab. Nothing here sends a message. */
(() => {
  'use strict';
  const PREFIX='legal-agent.workspace.v1:';
  const memory=new Map();
  const defaults=()=>({text:'',requestId:'',runId:'',pane:'findings'});
  const validId=id=>typeof id==='string'&&/^matter-[a-f0-9]{32}$/.test(id);
  const panes=new Set(['findings','conversation']);
  let storage,available;
  function storageReady(){
    if(available!==undefined)return available;
    try{
      storage=window.sessionStorage;
      const probe=PREFIX+'storage-check';
      storage.setItem(probe,'1');storage.removeItem(probe);available=true;
    }catch{available=false;}
    return available;
  }
  function normalize(value){
    const result=defaults();
    if(!value||typeof value!=='object'||Array.isArray(value))return result;
    if(typeof value.text==='string')result.text=value.text.slice(0,12000);
    for(const key of ['requestId','runId'])if(typeof value[key]==='string'&&value[key].length<=128)result[key]=value[key];
    if(panes.has(value.pane))result.pane=value.pane;
    return result;
  }
  function read(id){
    if(!validId(id))return defaults();
    if(storageReady()){
      try{
        const raw=storage.getItem(PREFIX+id);
        if(raw!==null){
          let parsed;try{parsed=JSON.parse(raw);}catch{parsed=null;}
          memory.set(id,normalize(parsed));
        }
      }catch{available=false;}
    }
    return {...(memory.get(id)||defaults())};
  }
  function write(id,patch){
    if(!validId(id)||!patch||typeof patch!=='object'||Array.isArray(patch))return false;
    const keys=['text','requestId','runId','pane'].filter(key=>Object.prototype.hasOwnProperty.call(patch,key));
    if(!keys.length)return false;
    for(const key of keys){
      if(key==='pane'){if(!panes.has(patch[key]))return false;}
      else if(typeof patch[key]!=='string'||patch[key].length>(key==='text'?12000:128))return false;
    }
    const next=read(id);for(const key of keys)next[key]=patch[key];
    memory.set(id,next);
    if(storageReady()){
      try{storage.setItem(PREFIX+id,JSON.stringify(next));}catch{available=false;}
    }
    return true;
  }
  function clearReply(id,expectedText){
    if(!validId(id)||typeof expectedText!=='string'||read(id).text!==expectedText)return false;
    return write(id,{text:'',requestId:'',runId:''});
  }
  window.MatterWorkspaceStore=Object.freeze({read,write,clearReply,get storageAvailable(){return storageReady();}});
})();
