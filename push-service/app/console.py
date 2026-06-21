"""Provider web console — a single self-contained HTML page.

Served at GET / and /console. Lets a provider start a check-in (real VERA
scenarios + survivor/caregiver role) and review results (VERA clinician summary:
flags by tier). Talks only to the push-service endpoints.
"""

CONSOLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>VERA · Provider Console</title>
<style>
  :root { --teal:#167a6e; --teal-2:#3cc4b2; --ink:#16201e; --muted:#5b6b67;
          --line:#e3eae8; --bg:#f6f9f8; --card:#ffffff; --red:#c0362c; --amber:#b8860b; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:var(--bg); color:var(--ink); }
  header { background:linear-gradient(180deg,var(--teal-2),var(--teal)); color:#fff; padding:20px 24px; }
  header h1 { margin:0; font-size:20px; font-weight:700; }
  header p { margin:4px 0 0; opacity:.9; font-size:13px; }
  .wrap { max-width:920px; margin:0 auto; padding:24px; }
  h2 { font-size:14px; text-transform:uppercase; letter-spacing:.5px; color:var(--muted); margin:28px 0 10px; }
  .bar { display:flex; gap:12px; align-items:center; margin-bottom:16px; flex-wrap:wrap; }
  .bar input, .bar select { padding:8px 10px; border:1px solid var(--line); border-radius:8px; font-size:14px; background:#fff; }
  .grow { flex:1; }
  button { cursor:pointer; border:0; border-radius:8px; padding:9px 14px; font-size:14px; font-weight:600; background:var(--teal); color:#fff; }
  button.ghost { background:#fff; color:var(--teal); border:1px solid var(--teal); }
  button:disabled { opacity:.5; cursor:default; }
  table { width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:12px; overflow:hidden; }
  th, td { text-align:left; padding:12px 14px; font-size:14px; border-bottom:1px solid var(--line); }
  th { background:#eef5f3; color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.4px; }
  tr:last-child td { border-bottom:0; }
  .muted { color:var(--muted); font-size:12px; }
  .empty { padding:24px; text-align:center; color:var(--muted); }
  .pill { font-size:11px; padding:2px 8px; border-radius:999px; background:#eef5f3; color:var(--teal); }
  #toast { position:fixed; bottom:20px; left:50%; transform:translateX(-50%); background:var(--ink); color:#fff; padding:12px 18px; border-radius:10px; font-size:14px; opacity:0; transition:opacity .2s; pointer-events:none; max-width:90%; }
  #toast.show { opacity:1; }
  /* results modal */
  #overlay { position:fixed; inset:0; background:rgba(0,0,0,.4); display:none; align-items:center; justify-content:center; padding:20px; }
  #overlay.show { display:flex; }
  #modal { background:#fff; border-radius:14px; max-width:560px; width:100%; max-height:85vh; overflow:auto; padding:22px; }
  .banner { padding:12px 14px; border-radius:10px; font-weight:600; margin-bottom:14px; }
  .banner.priority { background:#fbeae8; color:var(--red); }
  .banner.ok { background:#e7f4ea; color:#1f7a3f; }
  .flag { border:1px solid var(--line); border-radius:10px; padding:10px 12px; margin-bottom:8px; font-size:14px; }
  .tier1 { border-left:4px solid var(--red); }
  .tier2 { border-left:4px solid var(--amber); }
  .tier3 { border-left:4px solid var(--teal); }
  .closeX { float:right; background:none; color:var(--muted); font-size:18px; padding:0 4px; }
</style>
</head>
<body>
<header>
  <h1>VERA · Provider Console</h1>
  <p>Start a post-discharge voice check-in and review results.</p>
</header>
<div class="wrap">
  <div class="bar">
    <input id="provkey" type="password" placeholder="Provider key" class="grow"/>
    <label class="muted">Check-in
      <select id="scenario">
        <option value="guided">General check-in (guided)</option>
        <option value="micro_routine">Routine check-in</option>
        <option value="micro_worsening">Worsening check-in</option>
        <option value="micro_redflag">Red-flag check-in (demo)</option>
        <option value="rag_enhanced">General check-in (AI-enhanced)</option>
      </select>
    </label>
    <label class="muted" title="Optional: VERA may add one short empathetic sentence before a question (DRAFT)">
      <input type="checkbox" id="empathy"/> Empathetic
    </label>
    <button class="ghost" onclick="loadAll()">Refresh</button>
  </div>

  <h2>Patients</h2>
  <table>
    <thead><tr><th>Patient (user_id)</th><th>Role</th><th>Device</th><th>Registered</th><th></th></tr></thead>
    <tbody id="rows"><tr><td colspan="5" class="empty">Loading…</td></tr></tbody>
  </table>

  <h2>Recent check-ins
    <label style="float:right;text-transform:none;letter-spacing:0;font-weight:400;font-size:13px;color:var(--ink)">
      <input type="checkbox" id="flagsOnly" onchange="loadHistory()"/> Red flags only
    </label>
  </h2>
  <table>
    <thead><tr><th>Patient</th><th>Check-in</th><th>Started</th><th>Status</th><th></th></tr></thead>
    <tbody id="history"><tr><td colspan="5" class="empty">None yet.</td></tr></tbody>
  </table>
</div>

<div id="toast"></div>
<div id="overlay" onclick="if(event.target.id==='overlay')closeModal()">
  <div id="modal"></div>
</div>

<script>
const $ = (id) => document.getElementById(id);
function headers() {
  const h = { "content-type": "application/json" };
  const k = $("provkey").value.trim();
  if (k) h["X-Provider-Key"] = k;
  return h;
}
function toast(msg){ const t=$("toast"); t.textContent=msg; t.classList.add("show"); setTimeout(()=>t.classList.remove("show"),3200); }
function closeModal(){ $("overlay").classList.remove("show"); }

async function loadAll(){ await Promise.all([loadDevices(), loadHistory()]); }

async function loadDevices(){
  $("rows").innerHTML = '<tr><td colspan="4" class="empty">Loading…</td></tr>';
  try{
    const r = await fetch("/v1/devices", { headers: headers() });
    if(!r.ok) throw new Error("HTTP "+r.status);
    const d = await r.json();
    $("rows").innerHTML = d.length ? d.map(x=>`
      <tr><td><strong>${x.user_id}</strong></td>
      <td><span class="pill">${x.role || 'survivor'}</span></td>
      <td><span class="pill">${x.platform}</span> <span class="muted">${x.token_preview}</span></td>
      <td class="muted">${new Date(x.registered_at).toLocaleString()}</td>
      <td><button onclick="start('${x.user_id}',this)">Start check-in</button></td></tr>`).join("")
      : '<tr><td colspan="5" class="empty">No registered patients yet.</td></tr>';
  }catch(e){ $("rows").innerHTML = '<tr><td colspan="4" class="empty">Could not load: '+e.message+'</td></tr>'; }
}

function statusCell(x){
  if(x.has_priority === true) return '<span style="color:var(--red);font-weight:600">⚠ Priority</span>';
  if(x.status === "completed") return '<span style="color:#1f7a3f">✓ Routine</span>';
  return '<span class="muted">in progress…</span>';
}
async function loadHistory(){
  try{
    const url = "/v1/checkins" + ($("flagsOnly").checked ? "?priority_only=true" : "");
    const r = await fetch(url, { headers: headers() });
    if(!r.ok){ $("history").innerHTML='<tr><td colspan="5" class="empty">Enter the provider key, then Refresh.</td></tr>'; return; }
    const h = await r.json();
    $("history").innerHTML = h.length ? h.map(x=>`
      <tr><td><strong>${x.user_id}</strong></td>
      <td>${x.scenario} <span class="muted">· ${x.role}</span></td>
      <td class="muted">${new Date(x.started_at).toLocaleString()}</td>
      <td>${statusCell(x)}</td>
      <td><button class="ghost" onclick="viewResult('${x.session_id}')">View result</button></td></tr>`).join("")
      : '<tr><td colspan="5" class="empty">None match.</td></tr>';
  }catch(e){ /* ignore */ }
}

async function start(userId, btn){
  btn.disabled=true; btn.textContent="Starting…";
  try{
    const r = await fetch("/v1/checkins/start",{ method:"POST", headers:headers(),
      body: JSON.stringify({ user_id:userId, scenario:$("scenario").value, empathy:$("empathy").checked }) });
    const data = await r.json();
    if(!r.ok) throw new Error(data.detail || ("HTTP "+r.status));
    toast(data.live_delivered>0 ? `Delivered to ${userId}'s phone.` : `Created for ${userId} — app will pick it up when open.`);
    loadHistory();
  }catch(e){ toast("Failed: "+e.message); }
  finally{ btn.disabled=false; btn.textContent="Start check-in"; }
}

function flagText(f){
  if(typeof f === "string") return f;
  return f.label || f.text || f.message || f.reason || f.name || JSON.stringify(f);
}
function renderFlags(items){
  if(!items || !items.length) return '<p class="muted">None.</p>';
  return items.map(f=>{ const t=(f && f.tier)||3; return `<div class="flag tier${t}"><strong>Tier ${t}</strong> — ${flagText(f)}</div>`; }).join("");
}

async function viewResult(sessionId){
  $("modal").innerHTML = '<button class="closeX" onclick="closeModal()">✕</button><p class="empty">Loading result…</p>';
  $("overlay").classList.add("show");
  try{
    const r = await fetch(`/v1/checkins/${sessionId}/summary`, { headers: headers() });
    const data = await r.json();
    if(!data.ready){
      $("modal").innerHTML = '<button class="closeX" onclick="closeModal()">✕</button>'
        + '<h2>No result yet</h2><p class="muted">The patient may not have finished the check-in, or VERA hasn\\'t recorded an outcome. Try again shortly.</p>';
      return;
    }
    const s = data.summary;
    const banner = s.has_priority
      ? '<div class="banner priority">⚠ Priority — needs clinician review</div>'
      : '<div class="banner ok">✓ All routine — no priority flags</div>';
    $("modal").innerHTML = '<button class="closeX" onclick="closeModal()">✕</button>'
      + '<h2 style="margin-top:0">Check-in result</h2>'
      + banner
      + (s.user_reported_urgency ? `<p><strong>Patient-reported urgency:</strong> ${s.user_reported_urgency}</p>` : '')
      + '<h2>Priority items</h2>' + renderFlags(s.priority_items)
      + '<h2>Routine items</h2>' + renderFlags(s.routine_items)
      + (s.suggested_route ? `<p class="muted">Suggested routing (DRAFT): ${typeof s.suggested_route==='string'?s.suggested_route:JSON.stringify(s.suggested_route)}</p>` : '');
  }catch(e){
    $("modal").innerHTML = '<button class="closeX" onclick="closeModal()">✕</button><p class="empty">Could not load: '+e.message+'</p>';
  }
}

loadAll();
</script>
</body>
</html>
"""
