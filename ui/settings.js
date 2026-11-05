/* Credentials go only to the local server: never browser storage or URLs. */
window.SettingsUI = (() => {
  let epoch = 0, settings = null, busy = false;
  const byId = id => document.getElementById(id);
  function el(tag, text, props = {}) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    Object.assign(node, props);
    return node;
  }
  function close() {
    epoch++;
    const field = byId('settingsApiKey');
    if (field) field.value = '';
    byId('settingsView')?.replaceChildren();
    settings = null;
    busy = false;
  }
  async function request(body) {
    const response = await fetch(body ? '/api/settings/key' : '/api/settings', {
      method: body ? 'POST' : 'GET', cache: 'no-store', credentials: 'same-origin',
      headers: body ? {'Content-Type':'application/json', 'X-Legal-Agent-CSRF':settings.csrf_token} : {},
      ...(body ? {body:JSON.stringify(body)} : {})
    });
    // Do not display arbitrary response bodies: credential failures must be redacted.
    if (!response.ok) {
      const messages = {403:'Settings changes are unavailable. Refresh Settings and try again in the local live workspace.',
        409:'The key change could not be confirmed. Stop any running investigations, then refresh status and try again.',
        400:'The key was not saved. Use a single API token without spaces or shell characters.'};
      throw new Error(messages[response.status] || 'The configuration request could not be completed. Refresh status before trying again.');
    }
    return response.json();
  }
  function announce(message, error = false) {
    const node = byId('settingsNotice');
    if (!node) return;
    node.textContent = message;
    node.setAttribute('role', error ? 'alert' : 'status');
    node.classList.toggle('settings-error', error);
  }
  function controls() {
    const locked = busy || !settings?.can_edit;
    for (const id of ['settingsApiKey','settingsSave']) if (byId(id)) byId(id).disabled = locked;
    if (byId('settingsRemove')) byId('settingsRemove').disabled = locked || !settings?.api_key_configured;
    if (byId('settingsCancelRemove')) byId('settingsCancelRemove').disabled = busy;
    if (byId('settingsConfirmRemove')) byId('settingsConfirmRemove').disabled = busy;
    if (byId('settingsRefresh')) byId('settingsRefresh').disabled = busy;
    byId('settingsKeyForm')?.setAttribute('aria-busy', String(busy));
  }
  function updateStatus() {
    const configured = !!settings.api_key_configured;
    byId('settingsKeyStatus').textContent = configured ? 'Key configured · connection unverified' : 'No API key configured';
    byId('settingsKeyStatus').className = 'settings-pill';
    byId('settingsSave').textContent = configured ? 'Replace API key' : 'Save API key';
    byId('settingsModel').textContent = settings.model || 'Not specified';
    byId('settingsProvider').textContent = settings.provider_host || 'Not specified';
    byId('settingsApiKeyLabel').textContent = 'API key for ' + (settings.provider_host || 'your configured provider');
    byId('settingsCallStatus').textContent = settings.isolated
      ? 'Model calls are disabled in this demo. Open the live workspace to investigate.'
      : configured ? 'Investigations use ' + (settings.model || 'the configured model') + ' at ' + (settings.provider_host || 'the configured provider') + '. Provider access has not been tested.'
      : 'Add a key for this provider to enable model investigations. Saving a key does not make a test call.';
    controls();
  }
  async function mutate(remove = false) {
    if (busy || !settings?.can_edit) return;
    const currentEpoch = epoch;
    let removed = false;
    const field = byId('settingsApiKey');
    if (!remove && !field.value.trim()) { announce('Enter an API key to save.', true); field.focus(); return; }
    busy = true; controls(); announce(remove ? 'Removing key…' : 'Saving key locally…');
    const body = remove ? {remove:true} : {api_key:field.value.trim()};
    // Clear immediately; the key is never retained in application state.
    field.value = '';
    try {
      const result = await request(body);
      if (epoch !== currentEpoch) return;
      settings = {...settings, ...result};
      // Read back status instead of claiming persistence from the POST alone.
      const confirmed = await request();
      if (epoch !== currentEpoch) return;
      settings = confirmed;
      byId('settingsRemoval').hidden = true;
      updateStatus();
      removed = remove;
      announce(settings.api_key_configured
        ? 'Key saved locally. It has not been sent to the provider for verification.'
        : 'Key removed. Add a key before starting another model investigation.');
    } catch (error) {
      if (epoch === currentEpoch) announce(error.message, true);
    } finally {
      // Drop the secret from the temporary payload too.
      if (body.api_key) body.api_key = '';
      if (epoch === currentEpoch) {
        busy = false; controls();
        if (removed) byId('settingsApiKey').focus();
      }
    }
  }
  function card(title, text) {
    const node = el('section', '', {className:'settings-card'});
    node.append(el('h2', title), el('p', text));
    return node;
  }
  function render() {
    const root = byId('settingsView');
    root.replaceChildren();
    const heading = el('div', '', {className:'settings-heading'});
    const title = el('div'); title.append(el('p','WORKSPACE SETTINGS',{className:'settings-eyebrow'}),el('h1','Connect your agent'),
      el('p','Manage model access and see how this workspace runs.'));
    const refresh = el('button','Refresh status',{id:'settingsRefresh',type:'button'});
    refresh.addEventListener('click',()=>open(true)); heading.append(title,refresh); root.append(heading);
    const notice = el('p','',{id:'settingsNotice',className:'settings-notice'});
    notice.setAttribute('role','status'); root.append(notice);
    const grid = el('div','',{className:'settings-grid'});
    const model = card('Model connection','Your key is write-only. The workspace never returns the saved key to this page.');
    model.append(el('p','',{id:'settingsKeyStatus',className:'settings-pill'}),el('p','',{id:'settingsCallStatus',className:'settings-help'}));
    const details = el('dl','',{className:'settings-details'});
    for (const [label,value] of [['Model',settings.model || 'Not specified'],['Provider',settings.provider_host || 'Not specified']]) {
      details.append(el('dt',label),el('dd',value,{id:label==='Model'?'settingsModel':'settingsProvider'}));
    }
    model.append(details);
    const form = el('form','',{id:'settingsKeyForm',autocomplete:'off'});
    const label = el('label','API key',{id:'settingsApiKeyLabel',htmlFor:'settingsApiKey'});
    const input = el('input','',{id:'settingsApiKey',type:'password',name:'local-provider-key',autocomplete:'new-password',spellcheck:false,maxLength:4096});
    input.setAttribute('aria-describedby','settingsKeyHelp'); input.setAttribute('autocapitalize','none');
    const help = el('p','Stored in the local .env file (not encrypted), with owner-only permissions. Never stored in this browser. Use an 8–4096 character token containing letters, numbers, or . _ + / = -. The configured provider receives the key when you run an investigation.',{id:'settingsKeyHelp',className:'settings-help'});
    const actions = el('div','',{className:'settings-actions'});
    const save = el('button','Save API key',{id:'settingsSave',type:'submit',className:'primary'});
    const remove = el('button','Remove saved key',{id:'settingsRemove',type:'button'});
    remove.addEventListener('click',()=>{byId('settingsRemoval').hidden=false;byId('settingsConfirmRemove').focus();});
    actions.append(save,remove); form.append(label,input,help,actions);
    form.addEventListener('submit',event=>{event.preventDefault();mutate();});
    const removal = el('div','',{id:'settingsRemoval',className:'settings-removal',hidden:true});
    const confirm = el('button','Remove key',{id:'settingsConfirmRemove',type:'button'});
    confirm.addEventListener('click',()=>mutate(true));
    const cancel = el('button','Keep key',{id:'settingsCancelRemove',type:'button'});
    cancel.addEventListener('click',()=>{removal.hidden=true;remove.focus();});
    removal.append(el('p','Remove model access from this local workspace?'),confirm,cancel);
    form.append(removal); model.append(form);
    if (!settings.can_edit) model.append(el('p',settings.isolated
      ? 'This synthetic demo cannot save credentials. Open the local live workspace to configure model access.'
      : settings.busy ? 'An investigation is running. Refresh status after it stops to change the key.'
      : 'Credential changes are unavailable in this workspace.',{className:'settings-help'}));
    const safety = card('Execution & data access','Current capabilities reported by this local server. Refresh status after configuration changes.');
    const execution = settings.execution || {};
    const reported = value => typeof value === 'string' && value.trim() ? value : 'Status unavailable. Refresh status to check.';
    const sandbox = execution.sandboxed === false ? 'OS sandbox not active'
      : execution.sandboxed === true ? 'OS sandbox reported active' : 'OS sandbox status unavailable';
    safety.append(el('p',sandbox,{className:'settings-pill settings-warning'}));
    const capabilities = el('dl','',{className:'settings-capabilities'});
    const rows = [
      ['Runtime permissions',reported(execution.permissions)],
      ['Process credentials',execution.environment_policy === 'allowlist-v1'
        ? reported(execution.credential_scope) : 'Credential filtering status unavailable.'],
      ['Provider data',reported(execution.provider_data)],
      ['Run recovery',reported(execution.recovery)],
      ['Record queries','The workspace record explorer queries one investigation’s saved records locally, without a model call.'],
      ['External databases',execution.external_databases === false ? 'No external databases connected.'
        : execution.external_databases === true ? 'A database connection is reported configured; connection health is unverified.' : 'Connection status unavailable.'],
      ['Trace export',execution.telemetry_export === false ? 'No external telemetry export configured. Inspect saved run activity in the workspace.'
        : execution.telemetry_export === true ? 'External telemetry export is reported configured; delivery is unverified.' : 'External telemetry status unavailable. Saved run activity is available in the workspace.']
    ];
    for (const [label,value] of rows) capabilities.append(el('dt',label),el('dd',value));
    safety.append(capabilities);
    grid.append(model,safety);root.append(grid);updateStatus();
  }
  async function open(restoreFocus = false) {
    close();
    const currentEpoch = epoch;
    const root = byId('settingsView');
    root.append(el('p','Loading local configuration…',{role:'status'}));
    try {
      const result = await request();
      if (epoch !== currentEpoch) return;
      settings = result; render();
      if (restoreFocus) byId('settingsRefresh').focus();
    } catch (_) {
      if (epoch !== currentEpoch) return;
      root.replaceChildren(el('h1','Settings unavailable'),el('p','The local configuration could not be loaded. Refresh this page to try again.'));
    }
  }
  window.addEventListener('pagehide',close);
  return {open,close};
})();
