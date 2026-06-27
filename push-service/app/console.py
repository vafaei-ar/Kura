"""Provider web console — a single self-contained HTML page.

Served at GET / and /console. Clinicians log in (per-clinician accounts), start a
check-in (real VERA scenarios + survivor/caregiver role), review results (VERA
clinician summary: flags by tier), triage flags (acknowledge / resolve), and open
a per-patient timeline. Talks only to the push-service endpoints; auth is the
signed session cookie set by /v1/auth/login.
"""

CONSOLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Lion AI Navigator · Penn State Health</title>
<style>
  :root { --teal:#167a6e; --teal-2:#3cc4b2; --ink:#16201e; --muted:#5b6b67;
          --line:#e3eae8; --bg:#f6f9f8; --card:#ffffff; --red:#c0362c; --amber:#b8860b;
          --navy:#001E44; --navy-2:#13294B; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:var(--bg); color:var(--ink); }
  header { background:linear-gradient(165deg,var(--navy-2),var(--navy)); color:#fff; padding:18px 24px;
           display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; }
  header .eyebrow { font-size:11px; font-weight:800; letter-spacing:2px; opacity:.85; margin:0 0 3px; }
  header h1 { margin:0; font-size:20px; font-weight:700; }
  header p { margin:4px 0 0; opacity:.9; font-size:13px; }
  .whoami { font-size:13px; text-align:right; }
  .whoami button { background:rgba(255,255,255,.18); color:#fff; padding:6px 10px; font-size:12px; margin-left:10px; }
  .badge { display:inline-block; background:#fff; color:var(--red); font-weight:700; border-radius:999px;
           padding:2px 10px; font-size:13px; margin-left:8px; }
  .badge.zero { color:#1f7a3f; }
  .wrap { max-width:980px; margin:0 auto; padding:24px; }
  h2 { font-size:14px; text-transform:uppercase; letter-spacing:.5px; color:var(--muted); margin:28px 0 10px; }
  .bar { display:flex; gap:12px; align-items:center; margin-bottom:16px; flex-wrap:wrap; }
  .bar input, .bar select { padding:8px 10px; border:1px solid var(--line); border-radius:8px; font-size:14px; background:#fff; }
  .grow { flex:1; }
  button { cursor:pointer; border:0; border-radius:8px; padding:9px 14px; font-size:14px; font-weight:600; background:var(--teal); color:#fff; }
  button.ghost { background:#fff; color:var(--teal); border:1px solid var(--teal); }
  button.sm { padding:6px 10px; font-size:12px; }
  button:disabled { opacity:.5; cursor:default; }
  table { width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:12px; overflow:hidden; }
  th, td { text-align:left; padding:12px 14px; font-size:14px; border-bottom:1px solid var(--line); }
  th { background:#eef5f3; color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.4px; }
  tr:last-child td { border-bottom:0; }
  .link { color:var(--teal); cursor:pointer; font-weight:700; }
  .link:hover { text-decoration:underline; }
  .muted { color:var(--muted); font-size:12px; }
  .empty { padding:24px; text-align:center; color:var(--muted); }
  .pill { font-size:11px; padding:2px 8px; border-radius:999px; background:#eef5f3; color:var(--teal); }
  #toast { position:fixed; bottom:20px; left:50%; transform:translateX(-50%); background:var(--ink); color:#fff; padding:12px 18px; border-radius:10px; font-size:14px; opacity:0; transition:opacity .2s; pointer-events:none; max-width:90%; }
  #toast.show { opacity:1; }
  #overlay { position:fixed; inset:0; background:rgba(0,0,0,.4); display:none; align-items:center; justify-content:center; padding:20px; }
  #overlay.show { display:flex; }
  #modal { background:#fff; border-radius:14px; max-width:620px; width:100%; max-height:85vh; overflow:auto; padding:22px; }
  .banner { padding:12px 14px; border-radius:10px; font-weight:600; margin-bottom:14px; }
  .banner.priority { background:#fbeae8; color:var(--red); }
  .banner.ok { background:#e7f4ea; color:#1f7a3f; }
  .flag { border:1px solid var(--line); border-radius:10px; padding:10px 12px; margin-bottom:8px; font-size:14px; }
  .tier1 { border-left:4px solid var(--red); }
  .tier2 { border-left:4px solid var(--amber); }
  .tier3 { border-left:4px solid var(--teal); }
  .closeX { float:right; background:none; color:var(--muted); font-size:18px; padding:0 4px; }
  button.del { background:none; color:var(--muted); font-size:16px; padding:6px 8px; line-height:1; }
  button.del:hover { color:var(--red); }
  td.actions { white-space:nowrap; text-align:right; }
  /* login */
  #login { max-width:360px; margin:8vh auto; background:#fff; border:1px solid var(--line); border-radius:14px; padding:26px; }
  #login h2 { text-align:center; color:var(--ink); text-transform:none; letter-spacing:0; font-size:18px; margin:0 0 4px; }
  #login p { text-align:center; color:var(--muted); font-size:13px; margin:0 0 18px; }
  #login input { width:100%; padding:11px 12px; border:1px solid var(--line); border-radius:9px; font-size:15px; margin-bottom:12px; }
  #login button { width:100%; padding:12px; font-size:15px; }
  #login .err { color:var(--red); font-size:13px; text-align:center; min-height:18px; margin-top:6px; }
  .hidden { display:none !important; }
  .ack { color:#1f7a3f; }
  .seen { color:var(--amber); }
  .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:22px 0 6px; }
  .stat { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 14px; }
  .stat .n { font-size:24px; font-weight:700; color:var(--navy); }
  .stat .n.warn { color:var(--red); }
  .stat .l { font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.4px; margin-top:2px; }
  .age { font-size:11px; font-weight:700; padding:2px 8px; border-radius:999px; display:inline-block; margin-top:3px;
         background:#eef5f3; color:var(--muted); }
  .age.warn { background:#fdf2e2; color:var(--amber); }
  .age.crit { background:#fbeae8; color:var(--red); }
  .notes { margin-top:6px; }
  .note { background:#f4f8f7; border-radius:8px; padding:8px 10px; margin-bottom:6px; font-size:13px; }
  .note .meta { color:var(--muted); font-size:11px; margin-top:2px; }
  #modal textarea { width:100%; border:1px solid var(--line); border-radius:8px; padding:8px; font-size:13px; font-family:inherit; resize:vertical; }
</style>
</head>
<body>

<!-- LOGIN -->
<div id="login" class="hidden">
  <p style="text-align:center;font-size:11px;font-weight:800;letter-spacing:2px;color:var(--navy);margin:0 0 6px;">PENN STATE HEALTH</p>
  <h2>Lion AI Navigator</h2>
  <p>Provider Console — sign in with your clinician account.</p>
  <input id="lg_user" placeholder="Username" autocomplete="username"/>
  <input id="lg_pass" type="password" placeholder="Password" autocomplete="current-password"
         onkeydown="if(event.key==='Enter')doLogin()"/>
  <button onclick="doLogin()">Sign in</button>
  <div id="lg_err" class="err"></div>
</div>

<!-- APP -->
<div id="app" class="hidden">
<header>
  <div>
    <p class="eyebrow">PENN STATE HEALTH</p>
    <h1>Lion AI Navigator <span style="font-weight:400;opacity:.8">· Provider Console</span> <span id="badge" class="badge zero">0</span></h1>
    <p>Start a post-discharge voice check-in, review results, and triage flags.</p>
  </div>
  <div class="whoami">
    <span id="who"></span>
    <button id="adminBtn" class="hidden" onclick="openAdmin()">Settings</button>
    <button onclick="doLogout()">Sign out</button>
  </div>
</header>
<div class="wrap">
  <div id="stats" class="stats"></div>

  <div class="bar">
    <label class="muted">Check-in
      <select id="scenario">
        <option value="guided">General check-in (guided)</option>
        <option value="rag_enhanced">General check-in (AI-enhanced)</option>
      </select>
    </label>
    <label class="muted" title="Optional: VERA may add one short empathetic sentence before a question (DRAFT)">
      <input type="checkbox" id="empathy"/> Empathetic
    </label>
    <input id="patientSearch" placeholder="Search patients…" oninput="renderDevices()" class="grow"/>
    <button class="ghost" onclick="loadAll()">Refresh</button>
  </div>

  <h2>Patients</h2>
  <table>
    <thead><tr><th>Patient (user_id)</th><th>Role</th><th>Device</th><th>Registered</th><th></th></tr></thead>
    <tbody id="rows"><tr><td colspan="5" class="empty">Loading…</td></tr></tbody>
  </table>

  <h2>Recent check-ins
    <span style="float:right;text-transform:none;letter-spacing:0;font-weight:400;font-size:13px;color:var(--ink)">
      <select id="statusFilter" onchange="loadHistory()" style="padding:5px 8px;border:1px solid var(--line);border-radius:7px;">
        <option value="all">All</option>
        <option value="priority">Red flags only</option>
        <option value="open">Open priority (worklist)</option>
      </select>
    </span>
  </h2>
  <table>
    <thead><tr><th>Patient</th><th>Check-in</th><th>Started</th><th>Status / triage</th><th></th></tr></thead>
    <tbody id="history"><tr><td colspan="5" class="empty">None yet.</td></tr></tbody>
  </table>
</div>
</div>

<div id="toast"></div>
<div id="overlay" onclick="if(event.target.id==='overlay')closeModal()">
  <div id="modal"></div>
</div>

<script>
const $ = (id) => document.getElementById(id);
let DEVICES = [];
function toast(msg){ const t=$("toast"); t.textContent=msg; t.classList.add("show"); setTimeout(()=>t.classList.remove("show"),3200); }
function closeModal(){ $("overlay").classList.remove("show"); }
function fmt(ts){ return ts ? new Date(ts).toLocaleString() : "—"; }
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}[c])); }

async function api(path, opts){
  const o = Object.assign({ headers: { "content-type": "application/json" } }, opts||{});
  const r = await fetch(path, o);
  if(r.status === 401){ showLogin(); throw new Error("Please sign in."); }
  return r;
}

/* ---------- auth ---------- */
function showLogin(){ $("login").classList.remove("hidden"); $("app").classList.add("hidden"); }
function showApp(){ $("login").classList.add("hidden"); $("app").classList.remove("hidden"); }

async function boot(){
  try{
    const r = await fetch("/v1/auth/me", { headers:{ "content-type":"application/json" } });
    if(!r.ok){ showLogin(); return; }
    const data = await r.json();
    onLoggedIn(data.clinician);
  }catch(e){ showLogin(); }
}

let autoRefreshStarted = false;
function onLoggedIn(c){
  showApp();
  $("who").textContent = c.display_name + " · " + c.role;
  $("adminBtn").classList.toggle("hidden", c.role !== "admin");
  if(c.must_change_password){ promptChangePassword(true); }
  loadAll();
  if(!autoRefreshStarted){ startAutoRefresh(); autoRefreshStarted = true; }
}

async function doLogin(){
  $("lg_err").textContent = "";
  const username = $("lg_user").value.trim(), password = $("lg_pass").value;
  if(!username || !password){ $("lg_err").textContent = "Enter your username and password."; return; }
  try{
    const r = await fetch("/v1/auth/login", { method:"POST", headers:{ "content-type":"application/json" },
      body: JSON.stringify({ username, password }) });
    const data = await r.json();
    if(!r.ok) throw new Error(data.detail || "Sign-in failed");
    $("lg_pass").value = "";
    onLoggedIn(data.clinician);
  }catch(e){ $("lg_err").textContent = e.message; }
}

async function doLogout(){
  try{ await fetch("/v1/auth/logout", { method:"POST" }); }catch(e){}
  showLogin();
}

function promptChangePassword(force){
  $("modal").innerHTML =
    (force ? "" : '<button class="closeX" onclick="closeModal()">✕</button>')
    + '<h2 style="margin-top:0;text-transform:none;letter-spacing:0;color:var(--ink)">Set a new password</h2>'
    + (force ? '<p class="muted">For security, please choose your own password before continuing.</p>' : '')
    + '<input id="cp_cur" type="password" placeholder="Current password" style="width:100%;padding:11px;border:1px solid var(--line);border-radius:9px;margin-bottom:10px"/>'
    + '<input id="cp_new" type="password" placeholder="New password (min 8 chars)" style="width:100%;padding:11px;border:1px solid var(--line);border-radius:9px;margin-bottom:10px"/>'
    + '<div id="cp_err" class="muted" style="color:var(--red);min-height:16px"></div>'
    + '<button onclick="submitChangePassword()">Update password</button>';
  $("overlay").classList.add("show");
}
async function submitChangePassword(){
  const cur=$("cp_cur").value, nw=$("cp_new").value;
  try{
    const r = await api("/v1/auth/change-password", { method:"POST",
      body: JSON.stringify({ current_password:cur, new_password:nw }) });
    const data = await r.json();
    if(!r.ok) throw new Error(data.detail || "Could not update");
    closeModal(); toast("Password updated.");
  }catch(e){ $("cp_err").textContent = e.message; }
}

/* ---------- data ---------- */
async function loadAll(){ await Promise.all([loadStats(), loadDevices(), loadHistory(), loadBadge()]); }

async function loadBadge(){
  try{
    const r = await api("/v1/checkins/priority-count");
    const d = await r.json();
    const b = $("badge"); b.textContent = d.open_priority;
    b.classList.toggle("zero", d.open_priority === 0);
    b.title = d.open_priority + " open priority item(s); " + d.total_priority + " total flagged.";
  }catch(e){}
}

async function loadStats(){
  try{
    const r = await api("/v1/stats");
    const s = await r.json();
    const ack = s.median_ack_minutes == null ? "—"
      : (s.median_ack_minutes < 60 ? Math.round(s.median_ack_minutes)+"m"
         : (s.median_ack_minutes/60).toFixed(1)+"h");
    const card = (n, label, warn) =>
      `<div class="stat"><div class="n${warn?' warn':''}">${n}</div><div class="l">${label}</div></div>`;
    $("stats").innerHTML =
      card(s.patients, "Patients") +
      card(s.open_priority, "Open priority", s.open_priority>0) +
      card(s.awaiting_first_checkin, "Awaiting 1st check-in") +
      card(s.checkins_today, "Check-ins today") +
      card(ack, "Median time to ack");
  }catch(e){}
}

/* Auto-refresh the lists every 25s, but never while a modal is open (so it
   doesn't yank a result/notes view out from under the clinician). */
function startAutoRefresh(){
  setInterval(() => {
    if($("overlay").classList.contains("show")) return;
    if($("app").classList.contains("hidden")) return;
    loadStats(); loadDevices(); loadHistory(); loadBadge();
  }, 25000);
}

/* Aging pill for an unresolved priority item (how long it's been open). */
function agePill(startedAt){
  const mins = (Date.now() - new Date(startedAt).getTime()) / 60000;
  let cls = "age", txt;
  if(mins < 60) txt = Math.round(mins)+"m";
  else if(mins < 1440) txt = (mins/60).toFixed(1)+"h";
  else txt = Math.round(mins/1440)+"d";
  if(mins >= 1440) cls += " crit"; else if(mins >= 240) cls += " warn";
  return `<span class="${cls}" title="Open for this long">open ${txt}</span>`;
}

async function loadDevices(){
  $("rows").innerHTML = '<tr><td colspan="5" class="empty">Loading…</td></tr>';
  try{
    const r = await api("/v1/devices");
    DEVICES = await r.json();
    renderDevices();
  }catch(e){ $("rows").innerHTML = '<tr><td colspan="5" class="empty">Could not load: '+esc(e.message)+'</td></tr>'; }
}

function renderDevices(){
  const q = ($("patientSearch").value||"").toLowerCase();
  const list = DEVICES.filter(x => !q
    || (x.user_id||"").toLowerCase().includes(q)
    || (x.display_name||"").toLowerCase().includes(q));
  $("rows").innerHTML = list.length ? list.map(x=>`
    <tr><td><span class="link" onclick="viewPatient('${esc(x.user_id)}')">${esc(x.display_name || x.user_id)}</span>
        <div class="muted">${esc(x.user_id)}</div></td>
    <td><span class="pill">${esc(x.role || 'survivor')}</span></td>
    <td><span class="pill">${esc(x.platform)}</span> <span class="muted">${esc(x.token_preview)}</span></td>
    <td class="muted">${fmt(x.registered_at)}</td>
    <td class="actions"><button class="sm" onclick="start('${esc(x.user_id)}',this)">Start check-in</button>
    <button class="del" title="Delete patient" onclick="deletePatient('${esc(x.user_id)}')">🗑</button></td></tr>`).join("")
    : '<tr><td colspan="5" class="empty">No patients match.</td></tr>';
}

function triageCell(x){
  if(x.resolved_at) return `<span class="ack">✓ Resolved</span><div class="muted">${esc(x.resolved_by||"")} · ${fmt(x.resolved_at)}</div>`;
  if(x.has_priority === true){
    let s = '<span style="color:var(--red);font-weight:600">⚠ Priority</span> ' + agePill(x.started_at);
    if(x.acknowledged_at) s += `<div class="muted seen">👁 ${esc(x.acknowledged_by||"")} · ${fmt(x.acknowledged_at)}</div>`;
    return s;
  }
  if(x.status === "completed") return '<span style="color:#1f7a3f">✓ Routine</span>';
  return '<span class="muted">in progress…</span>';
}

function triageButtons(x){
  let b = `<button class="ghost sm" onclick="viewResult('${esc(x.session_id)}')">Result</button>`;
  if(x.has_priority === true){
    if(x.resolved_at){
      b += ` <button class="ghost sm" onclick="triage('${esc(x.session_id)}','reopen')">Reopen</button>`;
    } else {
      if(!x.acknowledged_at) b += ` <button class="sm" onclick="triage('${esc(x.session_id)}','acknowledge')">Acknowledge</button>`;
      b += ` <button class="sm" onclick="triage('${esc(x.session_id)}','resolve')">Resolve</button>`;
    }
  }
  b += ` <button class="del" title="Delete record" onclick="deleteCheckin('${esc(x.session_id)}')">🗑</button>`;
  return b;
}

async function loadHistory(){
  try{
    const f = $("statusFilter").value;
    let url = "/v1/checkins";
    if(f === "priority") url += "?priority_only=true";
    else if(f === "open") url += "?unresolved_priority=true";
    const r = await api(url);
    const h = await r.json();
    $("history").innerHTML = h.length ? h.map(x=>`
      <tr><td><span class="link" onclick="viewPatient('${esc(x.user_id)}')">${esc(x.user_id)}</span></td>
      <td>${esc(x.scenario)} <span class="muted">· ${esc(x.role)}</span></td>
      <td class="muted">${fmt(x.started_at)}</td>
      <td>${triageCell(x)}</td>
      <td class="actions">${triageButtons(x)}</td></tr>`).join("")
      : '<tr><td colspan="5" class="empty">None match.</td></tr>';
  }catch(e){ /* 401 handled in api() */ }
}

/* ---------- actions ---------- */
async function triage(sessionId, action, note){
  try{
    const opts = { method:"POST" };
    if(note){ opts.body = JSON.stringify({ note }); }
    const r = await api(`/v1/checkins/${encodeURIComponent(sessionId)}/${action}`, opts);
    if(!r.ok){ const d=await r.json().catch(()=>({})); throw new Error(d.detail || ("HTTP "+r.status)); }
    const labels = { acknowledge:"Acknowledged.", resolve:"Marked resolved.", reopen:"Reopened." };
    toast(labels[action] || "Updated."); loadStats(); loadHistory(); loadBadge();
  }catch(e){ toast("Failed: "+e.message); }
}

async function deletePatient(userId){
  if(!confirm(`Delete patient "${userId}" and all of their check-ins? This cannot be undone.`)) return;
  try{
    const r = await api(`/v1/devices/${encodeURIComponent(userId)}`, { method:"DELETE" });
    if(!r.ok){ const d=await r.json().catch(()=>({})); throw new Error(d.detail || ("HTTP "+r.status)); }
    toast(`Deleted ${userId}.`); loadAll();
  }catch(e){ toast("Failed: "+e.message); }
}

async function deleteCheckin(sessionId){
  if(!confirm("Delete this check-in record? This cannot be undone.")) return;
  try{
    const r = await api(`/v1/checkins/${encodeURIComponent(sessionId)}`, { method:"DELETE" });
    if(!r.ok){ const d=await r.json().catch(()=>({})); throw new Error(d.detail || ("HTTP "+r.status)); }
    toast("Record deleted."); loadHistory(); loadBadge();
  }catch(e){ toast("Failed: "+e.message); }
}

async function start(userId, btn){
  btn.disabled=true; btn.textContent="Starting…";
  try{
    const r = await api("/v1/checkins/start",{ method:"POST",
      body: JSON.stringify({ user_id:userId, scenario:$("scenario").value, empathy:$("empathy").checked }) });
    const data = await r.json();
    if(!r.ok) throw new Error(data.detail || ("HTTP "+r.status));
    toast(data.live_delivered>0 ? `Delivered to ${userId}'s phone.` : `Created for ${userId} — app will pick it up when open.`);
    loadHistory();
  }catch(e){ toast("Failed: "+e.message); }
  finally{ btn.disabled=false; btn.textContent="Start check-in"; }
}

/* ---------- result modal ---------- */
function flagText(f){
  if(typeof f === "string") return f;
  return f.label || f.text || f.message || f.reason || f.name || JSON.stringify(f);
}
function renderFlags(items){
  if(!items || !items.length) return '<p class="muted">None.</p>';
  return items.map(f=>{ const t=(f && f.tier)||3; return `<div class="flag tier${t}"><strong>Tier ${t}</strong> — ${esc(flagText(f))}</div>`; }).join("");
}

function renderNotes(notes){
  if(!notes || !notes.length) return '<p class="muted">No notes yet.</p>';
  return '<div class="notes">' + notes.map(n =>
    `<div class="note">${esc(n.text)}<div class="meta">${esc(n.author||"")} · ${fmt(n.created_at)}</div></div>`).join("") + '</div>';
}

async function viewResult(sessionId){
  $("modal").innerHTML = '<button class="closeX" onclick="closeModal()">✕</button><p class="empty">Loading result…</p>';
  $("overlay").classList.add("show");
  try{
    const [r, nr] = await Promise.all([
      api(`/v1/checkins/${sessionId}/summary`),
      api(`/v1/checkins/${sessionId}/notes`),
    ]);
    const data = await r.json();
    const notes = nr.ok ? await nr.json() : [];
    if(!data.ready){
      $("modal").innerHTML = '<button class="closeX" onclick="closeModal()">✕</button>'
        + '<h2>No result yet</h2><p class="muted">The patient may not have finished the check-in, or no outcome has been recorded yet. Try again shortly.</p>'
        + '<h2>Notes</h2>' + renderNotes(notes) + noteEditor(sessionId, false);
      return;
    }
    const s = data.summary;
    const banner = s.has_priority
      ? '<div class="banner priority">⚠ Priority — needs clinician review</div>'
      : '<div class="banner ok">✓ All routine — no priority flags</div>';
    $("modal").innerHTML = '<button class="closeX" onclick="closeModal()">✕</button>'
      + '<h2 style="margin-top:0">Check-in result</h2>'
      + banner
      + (s.user_reported_urgency ? `<p><strong>Patient-reported urgency:</strong> ${esc(s.user_reported_urgency)}</p>` : '')
      + '<h2>Priority items</h2>' + renderFlags(s.priority_items)
      + '<h2>Routine items</h2>' + renderFlags(s.routine_items)
      + (s.suggested_route ? `<p class="muted">Suggested routing (DRAFT): ${esc(typeof s.suggested_route==='string'?s.suggested_route:JSON.stringify(s.suggested_route))}</p>` : '')
      + '<h2>Notes</h2>' + renderNotes(notes) + noteEditor(sessionId, s.has_priority);
  }catch(e){
    $("modal").innerHTML = '<button class="closeX" onclick="closeModal()">✕</button><p class="empty">Could not load: '+esc(e.message)+'</p>';
  }
}

function noteEditor(sessionId, isPriority){
  let buttons = `<button class="sm" onclick="addNoteFromModal('${esc(sessionId)}')">Add note</button>`;
  if(isPriority){
    buttons += ` <button class="ghost sm" onclick="triageFromModal('${esc(sessionId)}','acknowledge')">Acknowledge + note</button>`
             + ` <button class="sm" onclick="triageFromModal('${esc(sessionId)}','resolve')">Resolve + note</button>`;
  }
  return `<textarea id="noteText" rows="2" placeholder="Add a note (e.g. called patient, advised ER)…"></textarea>`
       + `<div style="margin-top:8px">${buttons}</div>`;
}

async function addNoteFromModal(sessionId){
  const text = ($("noteText").value||"").trim();
  if(!text){ toast("Type a note first."); return; }
  try{
    const r = await api(`/v1/checkins/${encodeURIComponent(sessionId)}/notes`, { method:"POST", body: JSON.stringify({ text }) });
    if(!r.ok){ const d=await r.json().catch(()=>({})); throw new Error(d.detail || ("HTTP "+r.status)); }
    toast("Note added."); viewResult(sessionId);
  }catch(e){ toast("Failed: "+e.message); }
}

async function triageFromModal(sessionId, action){
  const note = ($("noteText").value||"").trim();
  await triage(sessionId, action, note || undefined);
  viewResult(sessionId);
}

/* ---------- per-patient view ---------- */
async function viewPatient(userId){
  $("modal").innerHTML = '<button class="closeX" onclick="closeModal()">✕</button><p class="empty">Loading patient…</p>';
  $("overlay").classList.add("show");
  try{
    const r = await api(`/v1/patients/${encodeURIComponent(userId)}`);
    const d = await r.json();
    if(!r.ok) throw new Error(d.detail || ("HTTP "+r.status));
    const p = d.patient, sm = d.summary;
    const rows = d.checkins.map(x=>`
      <tr><td>${esc(x.scenario)}</td>
      <td class="muted">${fmt(x.started_at)}</td>
      <td>${triageCell(x)}</td></tr>`).join("");
    $("modal").innerHTML = '<button class="closeX" onclick="closeModal()">✕</button>'
      + `<h2 style="margin-top:0;text-transform:none;letter-spacing:0;color:var(--ink);font-size:18px">${esc(p.display_name || p.user_id)}</h2>`
      + `<p class="muted">${esc(p.user_id)} · ${esc(p.role||'survivor')}</p>`
      + `<p>${sm.total} check-in(s) · <strong style="color:var(--red)">${sm.open_priority} open priority</strong> · ${sm.priority} flagged total</p>`
      + `<p class="muted">Last check-in: ${fmt(sm.last_checkin_at)}</p>`
      + (d.checkins.length
          ? '<table style="margin-top:8px"><thead><tr><th>Check-in</th><th>When</th><th>Status / triage</th></tr></thead><tbody>'+rows+'</tbody></table>'
          : '<p class="muted">No check-ins yet.</p>');
  }catch(e){
    $("modal").innerHTML = '<button class="closeX" onclick="closeModal()">✕</button><p class="empty">Could not load: '+esc(e.message)+'</p>';
  }
}

/* ---------- admin settings ---------- */
function field(label, id, value, type){
  const v = value == null ? "" : value;
  if(type === "checkbox"){
    return `<label style="display:flex;align-items:center;gap:8px;margin:8px 0;font-size:14px">
      <input type="checkbox" id="${id}" ${v ? "checked" : ""}/> ${label}</label>`;
  }
  return `<label style="display:block;margin:10px 0 4px;font-size:13px;color:var(--muted)">${label}</label>
    <input id="${id}" type="${type||'text'}" value="${esc(v)}"
      style="width:100%;padding:9px;border:1px solid var(--line);border-radius:8px;font-size:14px"/>`;
}

async function openAdmin(){
  $("modal").innerHTML = '<button class="closeX" onclick="closeModal()">✕</button><p class="empty">Loading settings…</p>';
  $("overlay").classList.add("show");
  try{
    const r = await api("/v1/admin/settings");
    const s = await r.json();
    if(!r.ok) throw new Error(s.detail || ("HTTP "+r.status));
    const pwState = s.smtp_password_configured
      ? '<span class="ack">configured in server environment</span>'
      : '<span style="color:var(--red)">not set — add SMTP_PASSWORD as an app setting</span>';
    const ready = s.email_alerts_ready
      ? '<div class="banner ok">✓ Email alerts are ready</div>'
      : '<div class="banner priority">⚠ Email alerts not fully configured</div>';
    $("modal").innerHTML = '<button class="closeX" onclick="closeModal()">✕</button>'
      + '<h2 style="margin-top:0;text-transform:none;letter-spacing:0;color:var(--ink);font-size:18px">Emergency alert settings</h2>'
      + ready
      + field("Email alerts enabled", "set_alerts_enabled", s.alerts_enabled, "checkbox")
      + field("Recipients (comma-separated)", "set_alert_email_to", s.alert_email_to)
      + field("From address", "set_alert_email_from", s.alert_email_from)
      + field("SMTP host", "set_smtp_host", s.smtp_host)
      + field("SMTP port", "set_smtp_port", s.smtp_port, "number")
      + field("Use STARTTLS", "set_smtp_use_tls", s.smtp_use_tls, "checkbox")
      + field("SMTP username", "set_smtp_user", s.smtp_user)
      + field("Console URL (for email links)", "set_console_base_url", s.console_base_url)
      + `<p class="muted" style="margin-top:10px">SMTP password: ${pwState}. For security it stays a server secret and isn't editable here.</p>`
      + '<div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap">'
      + '<button onclick="saveAdmin()">Save settings</button>'
      + '<button class="ghost" onclick="testEmail(this)">Send test email</button>'
      + '</div>';
  }catch(e){
    $("modal").innerHTML = '<button class="closeX" onclick="closeModal()">✕</button><p class="empty">Could not load: '+esc(e.message)+'</p>';
  }
}

async function saveAdmin(){
  const body = {
    alerts_enabled: $("set_alerts_enabled").checked,
    alert_email_to: $("set_alert_email_to").value.trim(),
    alert_email_from: $("set_alert_email_from").value.trim(),
    smtp_host: $("set_smtp_host").value.trim(),
    smtp_port: parseInt($("set_smtp_port").value || "587", 10),
    smtp_use_tls: $("set_smtp_use_tls").checked,
    smtp_user: $("set_smtp_user").value.trim(),
    console_base_url: $("set_console_base_url").value.trim(),
  };
  try{
    const r = await api("/v1/admin/settings", { method:"PUT", body: JSON.stringify(body) });
    if(!r.ok){ const d=await r.json().catch(()=>({})); throw new Error(d.detail || ("HTTP "+r.status)); }
    toast("Settings saved."); openAdmin();
  }catch(e){ toast("Failed: "+e.message); }
}

async function testEmail(btn){
  btn.disabled = true; btn.textContent = "Sending…";
  try{
    const r = await api("/v1/admin/test-email", { method:"POST" });
    const d = await r.json();
    toast(d.ok ? ("Test email "+d.detail) : ("Not sent: "+d.detail));
  }catch(e){ toast("Failed: "+e.message); }
  finally{ btn.disabled = false; btn.textContent = "Send test email"; }
}

boot();
</script>
</body>
</html>
"""
