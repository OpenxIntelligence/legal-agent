// Offline behavior checks against the actual workspace handlers. No model calls.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const calls = [], notices = [], fields = new Map();
fields.set('matterReply', {value:'Please investigate this issue.', disabled:false, focus(){}});
let refreshResult = true;
let post = async () => ({});
const context = vm.createContext({
  window:{addEventListener(){}}, info:{api_key_configured:true},
  $:id=>fields.get(id), esc:String,
  api:async (path,body)=>{calls.push({path,body});return post(path,body);},
  MatterWorkspaceStore:{clearReply(){}},
  history:{replaceState(){}}, location:{hash:''},
  setTimeout, clearTimeout, setInterval, clearInterval,
});
const filename = new URL('../ui/matters.js', import.meta.url);
let source = fs.readFileSync(filename,'utf8');
source = source.replace('  window.MatterUI={', `
  renderMatter=()=>{};
  updateDraftStatus=()=>{};
  setStatus=(id,message,error)=>__notice(message,error);
  refresh=async()=>__refresh();
  window.recoveryTest={state,recoveryState,recoveryHold,runBusy,recoverRun,start,sendMessage};
  window.MatterUI={`);
context.__notice = (message,error)=>notices.push({message,error});
context.__refresh = ()=>refreshResult;
vm.runInContext(source,context,{filename:filename.pathname});
const app=context.window.recoveryTest;
function fixture(recovery,status='running') {
  calls.length=0;notices.length=0;post=async()=>({});refreshResult=true;
  Object.assign(app.state,{id:'matter-test',epoch:1,run:{id:'run-test',status,recovery},
    matter:{can_run:true,demo:false,api_key_configured:true,runs:[{id:'run-test'}],jobs:[],messages:[]},
    sending:null,starting:false,recovering:false,uploading:false,awaitingRun:null});
  fields.get('matterReply').value='Please investigate this issue.';
  fields.get('matterReply').disabled=false;
}

fixture({state:'available',can_resume:false});
assert.equal(app.runBusy(app.state.matter,app.state.run),false);
await app.start(false);
assert.equal(calls.length,0,'available recovery blocks a new execution');
let release;
post=()=>new Promise(resolve=>{release=resolve;});
const recovery=app.recoverRun();
await app.recoverRun();
assert.equal(calls.length,1,'double recovery click makes one request');
assert.equal(calls[0].path,'/api/matters/matter-test/runs/run-test/recover');
assert.deepEqual(Object.keys(calls[0].body),[],'recovery sends no paid execution options');
release({});await recovery;
assert.match(notices.at(-1).message,/No model was called/);

fixture({state:'active_elsewhere',can_resume:false});
assert.equal(app.runBusy(app.state.matter,app.state.run),true);
await app.start(true);await app.start(false);await app.recoverRun();
assert.equal(calls.length,0,'external owner blocks every execution action');

fixture({state:'recovered',can_resume:false},'paused');
await app.start(true);
assert.equal(calls.length,0,'non-resumable checkpoint cannot resume');
await app.sendMessage({preventDefault(){}});
assert.equal(calls.length,1,'sending after recovery only saves a message');
assert.match(calls[0].path,/\/messages$/);
assert.equal(calls[0].body.run_id,undefined,'save-only message is not queued for replay');

fixture({state:'recovered',can_resume:true},'paused');
await app.sendMessage({preventDefault(){}});
assert.equal(calls.length,1,'even resumable recovered run requires explicit Resume');
assert.equal(calls[0].body.run_id,'run-test','direction is queued for explicit resume without launching it');
assert.match(calls[0].path,/\/messages$/);

fixture({state:'available',can_resume:false});
post=async()=>{throw new Error('Recovery is unavailable');};
await app.recoverRun();
assert.equal(app.state.recovering,false,'failed recovery unlocks the interface');
assert.equal(notices.at(-1).error,true);
assert.match(notices.at(-1).message,/Could not recover/);
console.log('PASS: recovery ownership, duplicate guard, model-free request, explicit resume, and failure handling');
