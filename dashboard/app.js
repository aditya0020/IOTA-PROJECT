'use strict';
const $ = id => document.getElementById(id);
let apiKey = '', packageId = '', packages = [], history = [], offset = 0;
const SESSION_KEY = 'trace.workspace.session.v1';
let sessionExpiresAt = 0;
function clearSession() {
  try { sessionStorage.removeItem(SESSION_KEY); } catch { /* Storage may be disabled. */ }
}
function saveSession() {
  if (!apiKey) return;
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify({key:apiKey, backend:apiBase, packageId, expiresAt:sessionExpiresAt}));
  } catch { /* Monitoring still works if tab storage is unavailable. */ }
}
let apiBase = location.origin;
// Flask serves /static/index.html; a standalone preview needs a separate API.
const standalone = !location.pathname.startsWith('/static/');
const backendSettings = document.createElement('details');
const backendSummary = document.createElement('summary');
backendSummary.textContent = 'Backend connection';
const backendLabel = document.createElement('label');
backendLabel.htmlFor = 'backend-url';
backendLabel.textContent = 'Flask backend URL';
const backendInput = document.createElement('input');
backendInput.id = 'backend-url';
backendInput.type = 'url';
backendInput.required = true;
backendInput.value = standalone ? 'http://127.0.0.1:5000' : location.origin;
const backendHint = document.createElement('p');
backendHint.textContent = 'Keep the Flask backend running while using Live Server.';
backendSettings.append(backendSummary, backendLabel, backendInput, backendHint);
backendSettings.open = standalone;
$('login-form').insertBefore(backendSettings, $('login-form').querySelector('button'));
document.querySelectorAll('a.brand').forEach(link => link.setAttribute('href', './index.html'));
let polling = null, busy = false, generation = 0, latestData = null;
const LIVE_POLL_INTERVAL_MS = 1000;
let historySignature = '', chartSignature = '', alertsSignature = '', chainSignature = '';
let accelerationBaseline = null;
const ACCELERATION_ZERO_DEADBAND = 0.35;
const metrics = {
  temperature: ['Temperature', '°C'], humidity: ['Humidity', '%'], distance: ['Distance', 'cm'],
  accel_mps2: ['Acceleration', 'm/s²'], gyro_radps: ['Rotation', 'rad/s']
};
const reasonNames = { TEMPERATURE_VIOLATION: 'Temperature outside configured range', UNUSUAL_ACCELERATION: 'Unusual acceleration detected', UNUSUAL_ROTATION: 'Unusual rotation detected' };
const date = value => new Date(value).toLocaleString(undefined, {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'});
function element(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
function badge(value) { return element('span', value, `badge ${value.toLowerCase()}`); }
function showError(error) { $('page-error').textContent = error.message; $('page-error').hidden = false; }
function connection(text, style = '') { $('connection').textContent = text; $('connection').className = `connection ${style}`; }

async function api(path, payload) {
  let response;
  try {
    response = await fetch(`${apiBase}/api${path}`, {
    method: payload === undefined ? 'GET' : 'POST',
    headers: {'X-API-Key': apiKey, 'Content-Type': 'application/json'},
    body: payload === undefined ? undefined : JSON.stringify(payload),
    signal: AbortSignal.timeout(20000), cache: 'no-store'
    });
  } catch {
    throw new Error(`Cannot connect to Flask at ${apiBase}. Start the backend and check the Backend connection URL. For Live Server, also check CORS_ALLOWED_ORIGINS in .env.`);
  }
  if (!response.headers.get('content-type')?.includes('application/json')) {
    throw new Error('This URL is not the Flask API. Set Backend connection to your Flask address (normally http://127.0.0.1:5000).');
  }
  const body = await response.json();
  if (!response.ok) {
    const error = new Error(body.error || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return body;
}

function resetVerification() {
  $('verification-state').textContent = 'NOT CHECKED';
  $('verification-state').className = 'badge neutral';
  $('verification-description').textContent = 'Run verification to check individual sensor proofs.';
  $('anchored-count').nextElementSibling.textContent = 'individual proofs anchored';
  $('anchored-count').textContent = '—';
}

function resetRenderCache() {
  historySignature = '';
  chartSignature = '';
  alertsSignature = '';
  chainSignature = '';
}

function resetAccelerationCalibration() {
  accelerationBaseline = null;
  resetRenderCache();
}

function calibratedAcceleration(value) {
  if (value === undefined || value === null) return undefined;
  const raw = Number(value);
  if (!Number.isFinite(raw)) return undefined;
  if (accelerationBaseline === null) accelerationBaseline = raw;
  const adjusted = Math.abs(raw - accelerationBaseline);
  return adjusted < ACCELERATION_ZERO_DEADBAND ? 0 : adjusted;
}

function readingValue(row, key) {
  return key === 'accel_mps2' ? calibratedAcceleration(row[key]) : row[key];
}

async function loadPackages(preferred) {
  const body = await api('/packages?limit=500');
  packages = body.items;
  $('package-select').replaceChildren(...packages.map(p => {
    const option = element('option', `${p.id} · ${p.name}`); option.value = p.id; return option;
  }));
  packageId = packages.some(p => p.id === preferred) ? preferred : packages[0]?.id || '';
  $('package-select').value = packageId;
  $('empty-packages').hidden = !!packageId;
  $('shipment-content').hidden = !packageId;
  $('package-select').disabled = !packageId;
  saveSession();
  if (!packageId) connection('Ready · no shipments', 'online');
}

function renderMetric(id, value, unit) {
  const node = $(id);
  const text = value === undefined ? '—' : Number(value).toFixed(unit === 'rad/s' ? 2 : 1);
  const signature = `${text}|${unit}`;
  if (node.dataset.signature === signature) return;
  node.dataset.signature = signature;
  node.replaceChildren(document.createTextNode(text), element('span', unit));
}

function renderLive(data) {
  latestData = data;
  const p = data.package, r = data.reading;
  $('package-id').textContent = p.id;
  $('package-name').textContent = p.name;
  $('origin').textContent = p.origin;
  $('destination').textContent = p.destination;
  $('package-status').textContent = p.status;
  $('package-status').className = `badge ${p.status.toLowerCase()}`;
  $('last-reading').textContent = r ? date(r.timestamp) : 'No readings yet';
  $('sample-status').textContent = r ? `Seq ${r.seq} · sample ${r.status} · UNO ${r.source_status}` : 'Waiting for gateway';
  $('temp-range').textContent = `Allowed range ${p.min_temp}–${p.max_temp} °C`;
  renderMetric('temperature', r?.temperature, '°C');
  renderMetric('humidity', r?.humidity, '%');
  renderMetric('distance', r?.distance, 'cm');
  renderMetric('acceleration', calibratedAcceleration(r?.accel_mps2), 'm/s²');
  renderMetric('rotation', r?.gyro_radps, 'rad/s');
  const stale = r && Date.parse(data.server_time) - Date.parse(r.timestamp) > 15000;
  connection(!r ? 'Waiting for gateway' : stale ? 'Sensor data is stale' : 'Live · updating every second', stale || !r ? 'stale' : 'online');
  $('updated').textContent = `Synced ${new Date().toLocaleTimeString()}`;
}

document.querySelector('#activity thead tr').append(element('th', 'Blockchain proof'));

function renderHistory() {
  const signature = `${accelerationBaseline ?? ''}|${offset}|${$('history-limit').value}|${JSON.stringify(history)}`;
  if (historySignature !== signature) {
    historySignature = signature;
    $('history').replaceChildren(...history.map(row => {
      const tr = element('tr');
      for (const value of [date(row.timestamp), row.seq, `${row.temperature.toFixed(1)} °C`, `${row.humidity.toFixed(1)} %`, `${row.distance.toFixed(1)} cm`, `${calibratedAcceleration(row.accel_mps2).toFixed(2)} m/s²`, `${row.gyro_radps.toFixed(2)} rad/s`]) tr.append(element('td', value));
      const status = element('td'); status.append(badge(row.status)); tr.append(status);
      const proof = element('td');
      proof.append(badge(row.blockchain_status || 'DISABLED'));
      const details = element('details'); details.append(element('summary', 'Proof details'));
      details.append(element('p', `Data hash: ${row.blockchain_data_hash || '—'}`),
        element('p', `Transaction: ${row.blockchain_tx_hash || '—'}`),
        element('p', `Block: ${row.blockchain_block_number ?? '—'}`));
      if (row.blockchain_explorer_url && /^https?:\/\//.test(row.blockchain_explorer_url)) {
        const link = element('a', 'View transaction ↗'); link.href = row.blockchain_explorer_url;
        link.target = '_blank'; link.rel = 'noopener noreferrer'; details.append(link);
      }
      const verify = element('button', 'Verify reading', 'secondary');
      const result = element('span', 'NOT CHECKED'); result.setAttribute('role', 'status');
      verify.addEventListener('click', async () => {
        verify.disabled = true;
        try { const report = await api(`/readings/${row.id}/verify`); result.textContent = report.state; }
        catch (error) { result.textContent = 'UNAVAILABLE'; showError(error); }
        finally { verify.disabled = false; }
      });
      details.append(verify, result); proof.append(details); tr.append(proof);
      return tr;
    }));
    if (!history.length) { const tr = element('tr'); const td = element('td', 'No readings in this page.'); td.colSpan = 9; tr.append(td); $('history').append(tr); }
    $('history-page').textContent = history.length ? `Readings ${offset + 1}–${offset + history.length} · newest first` : 'No readings';
    $('newer').disabled = offset === 0;
    $('older').disabled = history.length < Number($('history-limit').value);
  }
  renderChart();
}

function svgNode(tag, attrs, text) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderChart() {
  const key = $('chart-metric').value, [label, unit] = metrics[key];
  const chart = $('chart');
  $('chart-label').textContent = `${label} · ${unit}`;
  $('chart-caption').textContent = `${history.length} readings in this history page`;
  chart.setAttribute('aria-label', `${label} history, ${history.length} readings, ${unit}`);
  const width = Math.max(300, chart.clientWidth - 30);
  const signature = `${key}|${width}|${accelerationBaseline ?? ''}|${history.map(row => row.hash || `${row.timestamp}:${row.seq}`).join('|')}`;
  if (chartSignature === signature) return;
  chartSignature = signature;
  if (!history.length) { chart.replaceChildren(element('p', 'Sensor trends will appear after the first reading.', 'chart-empty')); return; }
  const rows = [...history].reverse(), values = rows.map(r => readingValue(r, key));
  let min = Math.min(...values), max = Math.max(...values);
  const pad = Math.max((max - min) * .2, .5); min -= pad; max += pad;
  const height = 218, left = 47, right = 17, top = 15, bottom = 35;
  const first = Date.parse(rows[0].timestamp), last = Date.parse(rows.at(-1).timestamp);
  const x = (row, i) => left + (last === first ? (rows.length === 1 ? .5 : i / (rows.length - 1)) : (Date.parse(row.timestamp) - first) / (last - first)) * (width - left - right);
  const y = value => top + (max - value) / (max - min) * (height - top - bottom);
  const svg = svgNode('svg', {viewBox:`0 0 ${width} ${height}`, role:'presentation'});
  for (let i = 0; i <= 4; i++) {
    const value = min + (max - min) * i / 4, pos = y(value);
    svg.append(svgNode('line', {x1:left,y1:pos,x2:width-right,y2:pos,stroke:'#edf1e7','stroke-dasharray':'3 4'}));
    svg.append(svgNode('text', {x:left-10,y:pos+3,'text-anchor':'end'}, value.toFixed(1)));
  }
  const points = rows.map((row,i) => `${x(row,i)},${y(readingValue(row, key))}`);
  if (rows.length > 1) {
    svg.append(svgNode('polygon', {points:`${x(rows[0],0)},${height-bottom} ${points.join(' ')} ${x(rows.at(-1),rows.length-1)},${height-bottom}`,fill:'#edf4e8'}));
    svg.append(svgNode('polyline', {points:points.join(' '),fill:'none',stroke:'#669567','stroke-width':2,'stroke-linejoin':'round'}));
  }
  rows.forEach((row,i) => {
    const value = readingValue(row, key);
    const circle = svgNode('circle', {cx:x(row,i),cy:y(value),r:rows.length < 30 ? 3 : 1.5,fill:'#55845b'});
    circle.append(svgNode('title', {}, `${date(row.timestamp)} · ${value} ${unit} · seq ${row.seq}`)); svg.append(circle);
  });
  [0, Math.floor((rows.length-1)/2), rows.length-1].filter((v,i,a)=>a.indexOf(v)===i).forEach(i=> {
    svg.append(svgNode('text', {x:x(rows[i],i),y:height-10,'text-anchor': i===0 && rows.length>1 ? 'start' : i===rows.length-1 && rows.length>1 ? 'end' : 'middle'}, new Date(rows[i].timestamp).toLocaleTimeString()));
  });
  chart.replaceChildren(svg);
}

function renderAlerts(items) {
  $('alert-count').textContent = items.length === 25 ? '25+' : items.length;
  const signature = items.map(item => item.hash || `${item.timestamp}:${item.status}:${item.reasons.join(',')}`).join('|');
  if (alertsSignature === signature) return;
  alertsSignature = signature;
  $('alerts').replaceChildren();
  if (!items.length) { const empty = element('div', undefined, 'empty-alerts'); empty.append(element('span','✓'), document.createTextNode('No condition alerts recorded.')); $('alerts').append(empty); return; }
  for (const alert of items) {
    const item = element('div', undefined, 'alert-item');
    item.append(element('div', '!', `alert-symbol ${alert.status.toLowerCase()}`));
    const body = element('div');
    body.append(element('strong', alert.status === 'TAMPERED' ? 'Movement requires inspection' : 'Temperature warning'), element('p', alert.reasons.map(r=>reasonNames[r] || r).join(' · ')), element('small', date(alert.timestamp)));
    item.append(body); $('alerts').append(item);
  }
}

function renderChain(data) {
  const signature = `${data.enabled}|${data.items.map(item => `${item.hash}:${item.state}:${item.tx_hash || ''}:${item.block_number || ''}:${item.last_error || ''}`).join('|')}`;
  if (chainSignature === signature) return;
  chainSignature = signature;
  $('chain-events').replaceChildren();
  if (!data.items.length) { $('chain-events').append(element('p', 'No ledger events recorded.', 'empty-alerts')); return; }
  for (const event of data.items) {
    const row = element('div', undefined, 'chain-event');
    row.append(element('span', {REGISTER:'Registration',CONDITION:'Condition change',CHECKPOINT:'Checkpoint'}[event.kind] || event.kind), badge(event.state));
    const details = element('div');
    const description = event.state === 'CONFIRMED' ? `Block ${event.block_number}` : !data.enabled ? 'Blockchain disabled · queued' : event.state === 'FAILED' ? `Retry pending · ${event.last_error}` : 'Waiting for chain worker';
    const hash = element('code', event.tx_hash || event.hash); hash.title = event.tx_hash || event.hash;
    details.append(element('small',description), hash); row.append(details); $('chain-events').append(row);
  }
}

async function refresh() {
  if (busy || !apiKey || !packageId) return;
  busy = true;
  const token = generation, id = packageId, page = offset, limit = $('history-limit').value;
  try {
    const base = `/packages/${encodeURIComponent(id)}`;
    const [live, readings, alerts, chain] = await Promise.all([api(`${base}/live`), api(`${base}/readings?limit=${limit}&offset=${page}`), api(`${base}/alerts?limit=25`), api(`${base}/blockchain?limit=20`)]);
    if (token !== generation) return;
    if (latestData?.reading?.hash !== live.reading?.hash) resetVerification();
    renderLive(live); history = readings.items; renderHistory(); renderAlerts(alerts.items); renderChain(chain);
    $('page-error').hidden = true;
  } catch (error) { if (token === generation) { showError(error); connection('Connection interrupted', 'stale'); } }
  finally { busy = false; if (token !== generation && apiKey) refresh(); }
}

// Update the visible sensor values frequently without repeatedly rebuilding
// history, alert and blockchain panels while the sample is unchanged.
async function refreshLive() {
  if (busy || !apiKey || !packageId) return;
  busy = true;
  const token = generation, id = packageId;
  let readingChanged = false;
  try {
    const live = await api(`/packages/${encodeURIComponent(id)}/live`);
    if (token !== generation) return;
    readingChanged = latestData?.reading?.hash !== live.reading?.hash;
    if (readingChanged) resetVerification();
    renderLive(live);
    $('page-error').hidden = true;
  } catch (error) {
    if (token === generation) { showError(error); connection('Connection interrupted', 'stale'); }
  } finally {
    busy = false;
  }
  // Fetch the supporting panels once when a new sample is observed.
  if (readingChanged && token === generation && apiKey) refresh();
}

async function changePackage() {
  packageId = $('package-select').value; offset = 0; generation++; latestData = null; resetAccelerationCalibration();
  saveSession();
  resetVerification();
  connection('Loading shipment…');
  await refresh();
}

async function signIn(key, backend, preferred = '', expiresAt = Date.now() + 8 * 60 * 60 * 1000) {
  apiKey = key;
  resetAccelerationCalibration();
  $('login-error').textContent = '';
  const button = $('login-form').querySelector('button'); button.disabled = true;
  try {
    const backendUrl = new URL(backend);
    if (!['http:', 'https:'].includes(backendUrl.protocol) || backendUrl.username || backendUrl.password || backendUrl.search || backendUrl.hash || backendUrl.pathname !== '/') {
      throw new Error('Enter a backend origin such as http://127.0.0.1:5000, without a path or credentials.');
    }
    apiBase = backendUrl.origin;
    sessionExpiresAt = expiresAt;
    await loadPackages(preferred); $('api-key').value = ''; $('access').hidden = true; $('workspace').hidden = false;
    clearInterval(polling);
    polling = setInterval(refreshLive, LIVE_POLL_INTERVAL_MS);
    await refresh();
  } catch (error) {
    apiKey = '';
    if (error.status === 401) clearSession();
    $('login-error').textContent = error.message;
  }
  finally { button.disabled = false; }
}

$('login-form').addEventListener('submit', async event => {
  event.preventDefault();
  await signIn($('api-key').value, backendInput.value);
});

$('logout').addEventListener('click', () => {
  clearInterval(polling); apiKey = ''; packageId = ''; generation++; latestData = null; resetAccelerationCalibration();
  clearSession();
  $('workspace').hidden = true; $('access').hidden = false; $('api-key').focus();
});
$('mobile-logout').addEventListener('click', () => $('logout').click());
$('package-select').addEventListener('change', changePackage);
$('refresh').addEventListener('click', async () => { try { await loadPackages(packageId); await refresh(); } catch (error) { showError(error); } });
$('chart-metric').addEventListener('change', renderChart);
window.addEventListener('resize', () => { if (!$('workspace').hidden) renderChart(); });
$('history-limit').addEventListener('change', () => { offset = 0; generation++; resetRenderCache(); refresh(); });
$('older').addEventListener('click', () => { offset += Number($('history-limit').value); generation++; resetRenderCache(); refresh(); });
$('newer').addEventListener('click', () => { offset = Math.max(0, offset - Number($('history-limit').value)); generation++; resetRenderCache(); refresh(); });

$('verify').addEventListener('click', async () => {
  const button = $('verify'), token = generation, id = packageId;
  button.disabled = true; button.textContent = 'Checking ledger…';
  try {
    const report = await api(`/packages/${encodeURIComponent(id)}/verify`);
    if (token !== generation) return;
    $('verification-state').textContent = report.state;
    $('verification-state').className = `badge ${report.state.toLowerCase()}`;
    const description = {
      VERIFIED:'All retained sensor readings match their individual on-chain proofs.', PARTIAL:'Some retained sensor readings do not yet have verified individual proofs.',
      ANCHORED:'Individual proof submissions are confirmed. Use Verify reading to check each proof.',
      PENDING:'Individual proofs are awaiting anchoring.',
      INVALID:'An individual sensor reading does not match its on-chain proof. Inspect the affected reading.',
      UNAVAILABLE:'The ledger could not be reached or its configuration is incomplete. Verification is unavailable.',
      OFFLINE:'Blockchain is disabled. Individual on-chain proofs have not been checked.'
    };
    const confirmed = report.individual_proofs_confirmed ?? 0;
    const coverage = [`${confirmed} readings have individual blockchain proofs.`];
    coverage.push(`${report.individual_proofs_verified ?? 0} individual proofs verified against the blockchain.`);
    if (report.individual_proofs_disabled) coverage.push(`${report.individual_proofs_disabled} readings have individual proof anchoring disabled (including historical readings).`);
    if (report.individual_proofs_pending) coverage.push(`${report.individual_proofs_pending} individual proofs are pending.`);
    if (report.individual_proofs_failed) coverage.push(`${report.individual_proofs_failed} individual proofs failed and await retry.`);
    coverage.push(`Legacy checkpoints cover ${report.legacy_checkpoint_anchored_readings ?? report.anchored_readings ?? 0} / ${report.reading_count} readings.`);
    if (report.legacy_checkpoint_state === 'INVALID') coverage.push('Legacy checkpoints: historical checkpoint chain does not match the retained dataset; it may predate data cleanup. Individual proof verification is independent.');
    else coverage.push(`Legacy checkpoint status: ${report.legacy_checkpoint_state ?? 'UNAVAILABLE'}.`);
    $('verification-description').textContent = `${description[report.state]} ${coverage.join(' ')} Snapshot of ${report.reading_count} retained readings checked ${new Date().toLocaleTimeString()}.`;
    $('anchored-count').textContent = `${report.state === 'VERIFIED' ? report.individual_proofs_verified : confirmed} / ${report.reading_count}`;
    $('anchored-count').nextElementSibling.textContent = report.state === 'VERIFIED' ? 'individual proofs anchored and verified' : 'individual proofs anchored';
  } catch (error) { if (token === generation) showError(error); }
  finally { button.disabled = false; button.textContent = '◇ Verify records'; }
});

function openRegister() { $('register-error').textContent = ''; $('register-dialog').showModal(); }
$('open-register').addEventListener('click', openRegister);
$('empty-register').addEventListener('click', openRegister);
$('close-register').addEventListener('click', () => $('register-dialog').close());
$('register-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  const data = Object.fromEntries(new FormData(event.target));
  for (const key of ['min_temp','max_temp','accel_threshold','gyro_threshold']) data[key] = Number(data[key]);
  try {
    await api('/packages', data); $('register-dialog').close(); generation++; offset = 0; resetAccelerationCalibration();
    await loadPackages(data.id); resetVerification(); await refresh();
  } catch (error) { $('register-error').textContent = error.message; }
  finally { button.disabled = false; }
});

// Restore only this tab's session, and validate it with Flask before opening the workspace.
async function restoreSession() {
  let saved;
  try { saved = JSON.parse(sessionStorage.getItem(SESSION_KEY)); } catch { clearSession(); return; }
  if (!saved) return;
  if (typeof saved.key !== 'string' || !saved.key || typeof saved.backend !== 'string' ||
      !Number.isFinite(saved.expiresAt) || saved.expiresAt <= Date.now()) {
    clearSession(); return;
  }
  backendInput.value = saved.backend;
  await signIn(saved.key, saved.backend, saved.packageId, saved.expiresAt);
}
restoreSession();
