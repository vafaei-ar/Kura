"""Provider web console — a single self-contained HTML page.

Served at GET / and /console. Lists registered patient devices and lets a
provider start a check-in (choosing scenario + role). Talks to the same
push-service endpoints (/v1/devices, /v1/checkins/start). No build step.
"""

CONSOLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>VERA · Provider Console</title>
<style>
  :root { --teal:#167a6e; --teal-2:#3cc4b2; --ink:#16201e; --muted:#5b6b67;
          --line:#e3eae8; --bg:#f6f9f8; --card:#ffffff; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:var(--bg); color:var(--ink); }
  header { background:linear-gradient(180deg,var(--teal-2),var(--teal)); color:#fff;
           padding:20px 24px; }
  header h1 { margin:0; font-size:20px; font-weight:700; }
  header p { margin:4px 0 0; opacity:.9; font-size:13px; }
  .wrap { max-width:880px; margin:0 auto; padding:24px; }
  .bar { display:flex; gap:12px; align-items:center; margin-bottom:16px; flex-wrap:wrap; }
  .bar input, .bar select { padding:8px 10px; border:1px solid var(--line); border-radius:8px;
           font-size:14px; background:#fff; }
  .grow { flex:1; }
  button { cursor:pointer; border:0; border-radius:8px; padding:9px 14px; font-size:14px;
           font-weight:600; background:var(--teal); color:#fff; }
  button.ghost { background:#fff; color:var(--teal); border:1px solid var(--teal); }
  button:disabled { opacity:.5; cursor:default; }
  table { width:100%; border-collapse:collapse; background:var(--card);
          border:1px solid var(--line); border-radius:12px; overflow:hidden; }
  th, td { text-align:left; padding:12px 14px; font-size:14px; border-bottom:1px solid var(--line); }
  th { background:#eef5f3; color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.4px; }
  tr:last-child td { border-bottom:0; }
  .muted { color:var(--muted); font-size:12px; }
  .empty { padding:28px; text-align:center; color:var(--muted); }
  #toast { position:fixed; bottom:20px; left:50%; transform:translateX(-50%);
           background:var(--ink); color:#fff; padding:12px 18px; border-radius:10px;
           font-size:14px; opacity:0; transition:opacity .2s; pointer-events:none; max-width:90%; }
  #toast.show { opacity:1; }
  .pill { font-size:11px; padding:2px 8px; border-radius:999px; background:#eef5f3; color:var(--teal); }
</style>
</head>
<body>
<header>
  <h1>VERA · Provider Console</h1>
  <p>Start a post-discharge voice check-in for a patient.</p>
</header>
<div class="wrap">
  <div class="bar">
    <input id="provkey" type="password" placeholder="Provider key (if required)" class="grow"/>
    <label class="muted">Scenario
      <select id="scenario">
        <option value="guided.yml">Guided</option>
        <option value="rag_enhanced.yml">RAG-enhanced</option>
      </select>
    </label>
    <label class="muted">Role
      <select id="role">
        <option value="survivor">Survivor</option>
        <option value="caregiver">Caregiver</option>
        <option value="clinician">Clinician</option>
      </select>
    </label>
    <button class="ghost" onclick="load()">Refresh</button>
  </div>

  <table>
    <thead>
      <tr><th>Patient (user_id)</th><th>Device</th><th>Registered</th><th></th></tr>
    </thead>
    <tbody id="rows">
      <tr><td colspan="4" class="empty">Loading…</td></tr>
    </tbody>
  </table>
  <p class="muted" style="margin-top:10px">
    Tip: a patient appears here once their app has registered. The check-in is
    delivered to the phone instantly while the app is open.
  </p>
</div>
<div id="toast"></div>

<script>
const $ = (id) => document.getElementById(id);
function headers() {
  const h = { "content-type": "application/json" };
  const k = $("provkey").value.trim();
  if (k) h["X-Provider-Key"] = k;
  return h;
}
function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 3200);
}
async function load() {
  $("rows").innerHTML = '<tr><td colspan="4" class="empty">Loading…</td></tr>';
  try {
    const r = await fetch("/v1/devices", { headers: headers() });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const devices = await r.json();
    if (!devices.length) {
      $("rows").innerHTML = '<tr><td colspan="4" class="empty">No registered patients yet.</td></tr>';
      return;
    }
    $("rows").innerHTML = devices.map(d => `
      <tr>
        <td><strong>${d.user_id}</strong></td>
        <td><span class="pill">${d.platform}</span> <span class="muted">${d.token_preview}</span></td>
        <td class="muted">${new Date(d.registered_at).toLocaleString()}</td>
        <td><button onclick="start('${d.user_id}', this)">Start check-in</button></td>
      </tr>`).join("");
  } catch (e) {
    $("rows").innerHTML = '<tr><td colspan="4" class="empty">Could not load patients: ' + e.message + '</td></tr>';
  }
}
async function start(userId, btn) {
  btn.disabled = true; btn.textContent = "Starting…";
  try {
    const r = await fetch("/v1/checkins/start", {
      method: "POST", headers: headers(),
      body: JSON.stringify({ user_id: userId, scenario: $("scenario").value, role: $("role").value })
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || ("HTTP " + r.status));
    toast(data.live_delivered > 0
      ? `Check-in delivered to ${userId}'s phone.`
      : `Check-in created for ${userId}, but their app isn't open right now.`);
  } catch (e) {
    toast("Failed: " + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "Start check-in";
  }
}
load();
</script>
</body>
</html>
"""
