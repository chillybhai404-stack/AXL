const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
let pw = sessionStorage.getItem("axu_pw") || "";
let list = [], cur = null, files = {}, tabsOpen = [], active = null, dirty = new Set(), problems = [];
let catalog = [], vault = [], ed = null, saveTimer = null, booted = false;
let fileFilter = "";

async function api(path, opt = {}) {
  const h = {"x-admin-password": pw};
  let body = opt.body;
  if (body && !(body instanceof FormData)) { h["content-type"] = "application/json"; body = JSON.stringify(body); }
  let r;
  try { r = await fetch(path, {method: opt.method || "GET", headers: h, body}); }
  catch (e) { throw new Error("Could not reach AXU server. Check the deployed URL, internet connection and server status."); }
  const raw = await r.text();
  let j = {}; try { j = raw ? JSON.parse(raw) : {}; } catch { j = {detail: raw.slice(0, 240)}; }
  if (!r.ok) {
    const detail = typeof j.detail === "string" ? j.detail : (j.message || j.error || "");
    throw new Error(detail || `Request failed (${r.status} ${r.statusText})`);
  }
  return j;
}
function status(t, bad) { const e = $("#st-save"); e.textContent = t; e.className = bad ? "err" : ""; }

// ---------- login + nav ----------
async function enter() {
  try { await api("/api/assistants"); }
  catch (e) { $("#loginmsg").textContent = pw ? e.message : ""; return; }
  sessionStorage.setItem("axu_pw", pw);
  $("#login").hidden = true; $("#app").hidden = false;
  if (!booted) boot();
  catalog = await api("/api/addons");
  await refresh(); loadKeys(); loadVault();
}
$("#loginbtn").onclick = () => { pw = $("#pw").value; enter(); };
$("#pw").onkeydown = e => { if (e.key === "Enter") $("#loginbtn").click(); };
$("#logout").onclick = () => { sessionStorage.removeItem("axu_pw"); location.reload(); };
$$("#nav button").forEach(b => b.onclick = () => {
  $$("#nav button").forEach(x => x.classList.toggle("on", x === b));
  ["studio", "keys", "merger", "vault", "ai"].forEach(v => $("#view-" + v).hidden = v !== b.dataset.view);
  if (b.dataset.view === "ai") loadAiModels();
});
$$("#ptabs button").forEach(b => b.onclick = () => {
  $$("#ptabs button").forEach(x => x.classList.toggle("on", x === b));
  $("#console").hidden = b.dataset.p !== "console"; $("#problems").hidden = b.dataset.p !== "problems";
});

// Touch-first Studio controls (Acode-style file drawer, editor and console dock).
function closeFilesDrawer() { document.body.classList.remove("files-open"); $("#filescrim").hidden = true; }
function setMobileDock(active) { ["files", "editor", "console"].forEach(n => $("#dock-" + n)?.classList.toggle("on", n === active)); }
function showEditor() { closeFilesDrawer(); $("#main").classList.remove("console-open"); setMobileDock("editor"); $("#code").focus({preventScroll: true}); }
function showConsole() { closeFilesDrawer(); $("#main").classList.add("console-open"); setMobileDock("console"); }
function showFiles() { document.body.classList.add("files-open"); $("#filescrim").hidden = false; setMobileDock("files"); }
["#mobile-files", "#dock-files"].forEach(sel => $(sel).onclick = showFiles);
["#mobile-editor", "#dock-editor"].forEach(sel => $(sel).onclick = showEditor);
["#mobile-console", "#dock-console"].forEach(sel => $(sel).onclick = showConsole);
$("#filescrim").onclick = closeFilesDrawer;

function boot() {
  booted = true;
  ed = new Editor({
    ta: $("#code"), hl: $("#hl"), gutter: $("#gutter"),
    onChange: () => {
      if (!active) return;
      files[active] = ed.getValue(); dirty.add(active); renderTabs(); status("Unsaved changes");
      const n = active; clearTimeout(saveTimer); saveTimer = setTimeout(() => saveFile(n), 900);
    },
    onCursor: (l, c) => $("#st-pos").textContent = `Ln ${l}, Col ${c}`,
    onSave: () => active && saveFile(active),
  });
  $("#klimits").innerHTML = limitsGrid("k");
  $("#mlimits").innerHTML = limitsGrid("m");
  $("#vprov").innerHTML = Object.entries(PROV).map(([k, v]) => `<option value="${k}">${v[3]}</option>`).join("");
  provDefaults();
}

// ---------- studio: assistants + project ----------
async function refresh(keep) {
  list = await api("/api/assistants");
  const opts = list.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join("");
  $("#asel").innerHTML = opts; $("#kscope").innerHTML = opts;
  if (keep && list.find(a => a.id == keep)) $("#asel").value = keep;
  await loadProject();
}
async function loadProject() {
  cur = list.find(a => a.id == $("#asel").value) || null;
  files = {}; tabsOpen = []; active = null; dirty.clear(); problems = [];
  if (cur) {
    const src = await api(`/api/assistants/${cur.id}/sources`);
    files = Object.fromEntries(src.map(f => [f.name, f.content]));
    problems = (await api(`/api/assistants/${cur.id}/problems`)).errors;
    tabsOpen = ["main.axl"]; active = "main.axl";
  }
  ed.setValue(active ? files[active] : "");
  ed.setErrors(errLines(active));
  renderAll();
}
const errLines = n => problems.filter(p => p.file === n).map(p => p.line);
function renderAll() { renderTabs(); renderFiles(); renderTables(); renderAddons(); renderProblems(); }
$("#asel").onchange = loadProject;

$("#newbtn").onclick = async () => {
  try {
    const r = await api("/api/assistants", {method: "POST", body: {name: $("#newname").value}});
    $("#newname").value = ""; await refresh(r.id); status("Assistant created with a starter project");
  } catch (e) { status(e.message, true); }
};
$("#delassistant").onclick = async () => {
  if (!cur || !confirm(`Delete "${cur.name}" and all its files and data?`)) return;
  await api("/api/assistants/" + cur.id, {method: "DELETE"}); await refresh();
};

// tabs
function renderTabs() {
  $("#tabs").innerHTML = tabsOpen.map(n =>
    `<div class="tab ${n === active ? "on" : ""}" data-open="${esc(n)}">${esc(n)}${dirty.has(n) ? '<i class="dot"></i>' : ""}<button class="x" data-close="${esc(n)}">&times;</button></div>`).join("");
}
$("#tabs").onclick = e => {
  const c = e.target.dataset.close;
  if (c) return closeTab(c);
  const t = e.target.closest("[data-open]"); if (t) openFile(t.dataset.open);
};
function openFile(n) {
  if (active) files[active] = ed.getValue();
  active = n; if (!tabsOpen.includes(n)) tabsOpen.push(n);
  ed.setValue(files[n] ?? ""); ed.setErrors(errLines(n)); renderTabs(); renderFiles();
}
function closeTab(n) {
  if (tabsOpen.length < 2) return;
  const i = tabsOpen.indexOf(n); tabsOpen.splice(i, 1);
  if (n === active) openFile(tabsOpen[Math.max(0, i - 1)]); else renderTabs();
}

// files
function renderFiles() {
  const names = Object.keys(files).sort().filter(n => n.toLowerCase().includes(fileFilter));
  $("#files").innerHTML = names.map(n =>
    `<div class="fitem ${n === active ? "on" : ""}" data-open="${esc(n)}"><span title="${esc(n)}">${esc(n)}</span>${n === "main.axl" ? "" : `<button class="x" data-renamefile="${esc(n)}" title="Rename file">✎</button><button class="x" data-delfile="${esc(n)}" title="Delete file">&times;</button>`}</div>`).join("") || '<div class="pad small muted">No matching files.</div>';
}
$("#filesearch").oninput = e => { fileFilter = e.target.value.trim().toLowerCase(); renderFiles(); };
$("#files").onclick = async e => {
  const rename = e.target.dataset.renamefile;
  if (rename) {
    const proposed = prompt("Rename AXL file (without needing to type .axl):", rename.replace(/\.axl$/i, ""));
    if (!proposed || !proposed.trim()) return;
    const target = proposed.trim().replace(/\.axl$/i, "") + ".axl";
    if (target === rename) return;
    if (!/^[A-Za-z0-9_-]{1,40}\.axl$/.test(target)) return status("File names can use letters, numbers, - and _", true);
    try {
      if (dirty.has(rename)) await saveFile(rename);
      if (dirty.has(rename)) return status("Save failed. Fix the save error before renaming this file.", true);
      const r = await api(`/api/assistants/${cur.id}/sources/${encodeURIComponent(rename)}`, {method: "PATCH", body: {name: target}});
      files[target] = files[rename]; delete files[rename]; dirty.delete(rename);
      tabsOpen = tabsOpen.map(n => n === rename ? target : n);
      if (active === rename) active = target;
      problems = r.errors; if (active) ed.setValue(files[active]); renderAll(); status(`Renamed to ${target}`);
    } catch (err) { status(err.message, true); }
    return;
  }
  const d = e.target.dataset.delfile;
  if (d) {
    if (!confirm(`Delete ${d}?`)) return;
    const r = await api(`/api/assistants/${cur.id}/sources/${encodeURIComponent(d)}`, {method: "DELETE"});
    delete files[d]; dirty.delete(d); tabsOpen = tabsOpen.filter(x => x !== d); problems = r.errors;
    openFile(tabsOpen.includes(active) && active !== d ? active : "main.axl"); renderProblems(); return;
  }
  const t = e.target.closest("[data-open]"); if (t) openFile(t.dataset.open);
};
$("#newfile").onclick = () => { if (!cur) return; $("#newfilerow").hidden = false; $("#newfilename").focus(); };
$("#newfilename").onkeydown = async e => {
  if (e.key === "Escape") { $("#newfilerow").hidden = true; return; }
  if (e.key !== "Enter") return;
  const n = e.target.value.trim().replace(/\.axl$/i, "");
  if (!/^[A-Za-z0-9_\-]{1,40}$/.test(n)) return status("File names can use letters, numbers, - and _", true);
  const name = n + ".axl", content = `# ${name}\n`;
  try {
    await api(`/api/assistants/${cur.id}/sources`, {method: "PUT", body: {name, content}});
    files[name] = content; e.target.value = ""; $("#newfilerow").hidden = true; openFile(name);
  } catch (err) { status(err.message, true); }
};

// saving
async function saveFile(n) {
  if (!cur || files[n] === undefined) return;
  status("Saving...");
  try {
    const r = await api(`/api/assistants/${cur.id}/sources`, {method: "PUT", body: {name: n, content: files[n]}});
    dirty.delete(n); problems = r.errors; ed.setErrors(errLines(active)); renderTabs(); renderProblems();
    status(problems.length ? `Saved - ${problems.length} problem${problems.length > 1 ? "s" : ""}` : "Saved", problems.length > 0);
  } catch (e) { status(e.message, true); }
}

// Export actions. The current file is downloaded as plain .axl; project export
// is a ZIP produced by the backend and deliberately excludes API secrets.
async function saveAll() {
  if (active) files[active] = ed.getValue();
  const pending = [...dirty];
  for (const name of pending) await saveFile(name);
  if (dirty.size) throw new Error("Some files could not be saved. Check the status message and try again.");
}
function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob), a = document.createElement("a");
  a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}
$("#downloadfile").onclick = () => {
  if (!active) return status("Open a file first", true);
  files[active] = ed.getValue();
  downloadBlob(new Blob([files[active]], {type: "text/plain;charset=utf-8"}), active);
};
async function exportProject() {
  if (!cur) return status("Choose an assistant first", true);
  try {
    await saveAll(); status("Preparing project export…");
    const r = await fetch(`/api/assistants/${cur.id}/export`, {headers: {"x-admin-password": pw}});
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || `Export failed (${r.status})`); }
    const blob = await r.blob();
    const cd = r.headers.get("content-disposition") || "";
    const match = /filename="?([^";]+)"?/i.exec(cd);
    downloadBlob(blob, match?.[1] || `${cur.name.replace(/[^a-z0-9_-]/gi, "_")}-axl-project.zip`);
    status("Project exported");
  } catch (e) { status(e.message, true); }
}
$("#exportproject").onclick = exportProject;
$("#dock-export").onclick = exportProject;
async function saveToolbar() { try { await saveAll(); status("All files saved"); } catch (e) { status(e.message, true); } }
$("#saveall").onclick = saveToolbar;
$("#dock-save").onclick = saveToolbar;
$("#dock-export").title = "Export the entire AXL project as ZIP";

// problems
function renderProblems() {
  $("#pcount").textContent = problems.length;
  $("#problems").innerHTML = problems.map((p, i) =>
    `<div class="prob" data-prob="${i}"><span class="err">error</span> ${esc(p.file)}:${p.line} <span class="muted">${esc(p.message)}</span></div>`).join("") || '<div class="muted">No problems.</div>';
}
$("#problems").onclick = e => {
  const t = e.target.closest("[data-prob]"); if (!t) return;
  const p = problems[+t.dataset.prob]; openFile(p.file); ed.jumpTo(p.line);
};

// data
function renderTables() {
  $("#tables").innerHTML = cur ? (cur.tables.map(t =>
    `<div class="fitem"><span><b>${esc(t.name)}</b> <small class="muted">${t.kind === "text" ? "document" : esc(t.columns)}</small></span><button class="x" data-deltable="${esc(t.name)}">&times;</button></div>`).join("") || '<div class="pad small muted">No uploaded data.</div>') : "";
}
async function reloadTables() { list = await api("/api/assistants"); cur = list.find(a => a.id == cur.id); renderTables(); }
$("#tables").onclick = async e => {
  const n = e.target.dataset.deltable; if (!n) return;
  await api(`/api/assistants/${cur.id}/tables/${encodeURIComponent(n)}`, {method: "DELETE"}); reloadTables();
};
$("#upbtn").onclick = () => cur && $("#file").click();
$("#file").onchange = async () => {
  const f = $("#file").files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f); $("#file").value = "";
  try {
    const r = await api(`/api/assistants/${cur.id}/files`, {method: "POST", body: fd});
    $("#dmsg").textContent = `Added "${r.table}" (${r.rows} ${r.kind === "text" ? "text chunks" : "rows"})`; reloadTables();
  } catch (e) { $("#dmsg").textContent = e.message; }
};
$("#drivebtn").onclick = async () => {
  try {
    const r = await api(`/api/assistants/${cur.id}/drive`, {method: "POST", body: {url: $("#drive").value}});
    $("#dmsg").textContent = `Imported "${r.table}" (${r.rows} ${r.kind === "text" ? "text chunks" : "rows"})`; reloadTables();
  } catch (e) { $("#dmsg").textContent = e.message; }
};

// add-ons
const hasAddon = n => new RegExp("^\\s*use\\s+" + n + "\\s*$", "mi").test(files["main.axl"] || "");
function renderAddons() {
  $("#addons").innerHTML = catalog.map(a =>
    `<div class="addon"><div><b>${esc(a.title)}</b>${a.offline ? ' <span class="small muted">offline</span>' : ""}<div class="small muted">${esc(a.desc)}</div><div class="small ex">${esc(a.example)}</div></div>
     <button class="sm ${hasAddon(a.name) ? "" : "primary"}" style="margin:0;align-self:flex-start" data-addon="${a.name}">${hasAddon(a.name) ? "Remove" : "Add"}</button></div>`).join("");
}
$("#addons").onclick = e => {
  const n = e.target.dataset.addon; if (!n || !cur) return;
  let t = files["main.axl"] || "";
  if (hasAddon(n)) t = t.split("\n").filter(l => !new RegExp("^\\s*use\\s+" + n + "\\s*$", "i").test(l)).join("\n");
  else {
    const L = t.split("\n"); let at = -1;
    L.forEach((l, i) => { if (/^\s*(use|import)\s/i.test(l)) at = i; });
    if (at < 0) L.forEach((l, i) => { if (at < 0 && /^\s*assistant:/i.test(l)) at = i; });
    L.splice(at + 1, 0, "use " + n); t = L.join("\n");
  }
  files["main.axl"] = t; dirty.add("main.axl");
  if (active === "main.axl") ed.setValue(t, true);
  renderAddons(); renderTabs(); saveFile("main.axl");
};

// console
function log(kind, text, src) {
  const d = document.createElement("div"); d.className = "l " + kind;
  d.textContent = (kind === "you" ? "> " : "") + text;
  if (src) { const s = document.createElement("span"); s.className = "src"; s.textContent = src; d.appendChild(s); }
  $("#clog").appendChild(d); $("#clog").scrollTop = 1e9;
}
$("#tq").onkeydown = async e => {
  if (e.key !== "Enter" || !cur) return;
  const q = e.target.value.trim(); if (!q) return; e.target.value = "";
  log("you", q);
  try {
    await Promise.all([...dirty].map(saveFile));
    const r = await api(`/api/assistants/${cur.id}/test`, {method: "POST", body: {q}});
    log("bot", r.answer, r.source + (r.debug ? " | " + r.debug : ""));
  } catch (err) { log("bot", err.message, "error"); }
};

// ---------- limits grid (keys + merger) ----------
const PRESETS = {trial: [50, 0, 100, 10, 7], starter: [1000, 20000, 0, 30, 0], unlimited: [0, 0, 0, 0, 0]};
const LIM = [["day", "Per day", 1000], ["month", "Per month", 0], ["total", "Total (ever)", 0], ["min", "Per minute", 0], ["exp", "Expires in (days)", 0]];
function limitsGrid(p) {
  return `<label>Limits on responses (0 = unlimited)</label>
  <div class="row">${Object.keys(PRESETS).map(k => `<button class="ghost sm" data-preset="${k}" data-prefix="${p}">${k[0].toUpperCase() + k.slice(1)}</button>`).join("")}</div>
  <div class="grid">${LIM.map(([k, l, d]) => `<div><label for="${p}${k}">${l}</label><input id="${p}${k}" type="number" min="0" value="${d}"></div>`).join("")}</div>`;
}
const readLimits = p => { const v = k => +$(`#${p}${k}`).value || 0;
  return {daily_limit: v("day"), monthly_limit: v("month"), total_limit: v("total"), per_minute: v("min"), expires_days: v("exp")}; };
document.addEventListener("click", e => {
  const b = e.target.closest("[data-preset]"); if (!b) return;
  LIM.forEach(([k], i) => $(`#${b.dataset.prefix}${k}`).value = PRESETS[b.dataset.preset][i]);
});

// ---------- keys ----------
function keyRow(k) {
  const lim = n => n || "unlimited";
  return `<div class="item"><span><b>${esc(k.prefix)}...</b> ${esc(k.label)} ${k.revoked ? '<span class="err">(revoked)</span>' : ""}<br>
    <small class="muted">${k.bundle.length ? "merged: " + esc(k.bundle.join(", ")) + " (" + k.mode + ")" : esc(k.scope.join(", "))}<br>
    ${k.usage.day}/${lim(k.limits.daily)} today, ${k.usage.month}/${lim(k.limits.monthly)} month, ${k.usage.total}/${lim(k.limits.total)} total${k.limits.per_minute ? ", " + k.limits.per_minute + "/min" : ""}${k.expires_at ? ", expires " + new Date(k.expires_at * 1000).toLocaleDateString() : ""}</small></span>
    ${k.revoked ? "" : `<button class="sm" data-rev="${k.id}">Revoke</button>`}</div>`;
}
async function loadKeys() {
  const ks = await api("/api/keys");
  $("#klist").innerHTML = ks.map(keyRow).join("") || '<div class="muted">No keys yet.</div>';
  $("#mkeys").innerHTML = ks.filter(k => k.bundle.length).map(keyRow).join("") || '<div class="muted">No merged keys yet.</div>';
}
function showKey(sel, key, assistant = "") {
  const b = $(sel); b.hidden = false; b.dataset.key = key; b.dataset.assistant = assistant || "";
  b.innerHTML = '<div class="muted small">Copy it now. This secret is shown only once.</div><div class="key-reveal"><code></code></div><div class="key-actions"><button class="sm" data-copy-issued>Copy key</button><button class="sm primary" data-test-issued>Test this key</button></div><div class="key-test-result small muted" aria-live="polite"></div>';
  b.querySelector("code").textContent = key;
}
async function testIssuedKey(container) {
  const output = container.querySelector(".key-test-result"), key = container.dataset.key || "", assistant = container.dataset.assistant || "";
  output.className = "key-test-result small muted"; output.textContent = "Testing /v1/chat/completions…";
  try {
    const body = {model: "axl-assistant", messages: [{role: "user", content: "Reply with exactly: AXL API key working"}]};
    if (assistant) body.assistant = assistant;
    const response = await fetch("/v1/chat/completions", {method: "POST", headers: {"Authorization": "Bearer " + key, "Content-Type": "application/json"}, body: JSON.stringify(body)});
    const raw = await response.text(); let data = {}; try { data = raw ? JSON.parse(raw) : {}; } catch { data = {detail: raw.slice(0, 240)}; }
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status} ${response.statusText}`);
    const answer = data.choices?.[0]?.message?.content || "Connected, but the server returned no chat text.";
    output.className = "key-test-result small ok"; output.textContent = "Connection successful: " + answer;
  } catch (e) { output.className = "key-test-result small err"; output.textContent = "Test failed: " + e.message; }
}
document.addEventListener("click", async e => {
  if (e.target.closest("[data-copy-issued]")) {
    const box = e.target.closest(".keybox"), key = box?.dataset.key || "";
    try { await navigator.clipboard.writeText(key); e.target.textContent = "Copied"; }
    catch { const area = document.createElement("textarea"); area.value = key; document.body.appendChild(area); area.select(); document.execCommand("copy"); area.remove(); e.target.textContent = "Copied"; }
    return;
  }
  if (e.target.closest("[data-test-issued]")) { const box = e.target.closest(".keybox"); if (box) await testIssuedKey(box); return; }
  const id = e.target.dataset.rev;
  if (id && confirm("Revoke this key?")) { await api(`/api/keys/${id}/revoke`, {method: "POST"}); loadKeys(); }
});
$("#kbtn").onclick = async () => {
  try {
    const selected = [...$("#kscope").selectedOptions];
    const assistantIds = selected.map(o => +o.value);
    const assistantName = (selected[0] || $("#kscope").options[0])?.textContent || "";
    const r = await api("/api/keys", {method: "POST", body: {label: $("#klabel").value, assistants: assistantIds, ...readLimits("k")}});
    showKey("#kout", r.key, assistantName); loadKeys();
  } catch (e) { alert(e.message); }
};

// ---------- vault ----------
const PROV = {
  openai: ["openai", "https://api.openai.com/v1", "gpt-4o-mini", "OpenAI"],
  anthropic: ["anthropic", "https://api.anthropic.com", "claude-haiku-4-5-20251001", "Anthropic"],
  gemini: ["gemini", "https://generativelanguage.googleapis.com", "gemini-3.8-flash", "Google Gemini"],
  groq: ["groq", "https://api.groq.com/openai/v1", "openai/gpt-oss-20b", "Groq"],
  openrouter: ["openrouter", "https://openrouter.ai/api/v1", "openai/gpt-4o-mini", "OpenRouter"],
  custom: ["custom", "", "", "Custom (OpenAI-compatible)"],
};
function provDefaults() { const p = PROV[$("#vprov").value]; $("#vurl").value = p[1]; $("#vmodel").value = p[2]; }
$("#vprov").onchange = provDefaults;
async function loadVault() {
  vault = await api("/api/vault");
  $("#vlist").innerHTML = vault.map(v =>
    `<div class="item"><span><b>${esc(v.name)}</b><br><small class="muted">${esc(v.provider)} / ${esc(v.model || "default model")}</small></span><button class="sm" data-vdel="${esc(v.name)}">Delete</button></div>`).join("") || '<div class="muted">Vault is empty.</div>';
  renderMerge();
}
$("#vtest").onclick = async () => {
  const msg = $("#vmsg");
  if (!$("#vsecret").value.trim()) { msg.className = "small err"; msg.textContent = "Paste the provider API key first."; return; }
  msg.className = "small muted"; msg.textContent = "Testing provider connection…";
  try {
    const r = await api("/api/vault/test", {method: "POST", body: {name: $("#vname").value || "provider_test", secret: $("#vsecret").value,
      provider: PROV[$("#vprov").value][0], base_url: $("#vurl").value, model: $("#vmodel").value}});
    msg.className = "small ok"; msg.textContent = `Connected to ${r.provider} (${r.model}): ${r.answer}`;
  } catch (e) { msg.className = "small err"; msg.textContent = "Connection failed: " + e.message; }
};
$("#vbtn").onclick = async () => {
  try {
    const r = await api("/api/vault", {method: "PUT", body: {name: $("#vname").value, secret: $("#vsecret").value, provider: PROV[$("#vprov").value][0], base_url: $("#vurl").value, model: $("#vmodel").value}});
    $("#vsecret").value = ""; $("#vmsg").className = "small ok"; $("#vmsg").textContent = "Saved as " + r.name; loadVault();
  } catch (e) { $("#vmsg").className = "small err"; $("#vmsg").textContent = e.message; }
};
$("#vlist").onclick = async e => {
  const n = e.target.dataset.vdel;
  if (n && confirm("Delete this vault key?")) { await api("/api/vault/" + encodeURIComponent(n), {method: "DELETE"}); loadVault(); }
};

// ---------- key merger ----------
let mOrder = [], mSel = new Set();
function renderMerge() {
  mOrder = mOrder.filter(n => vault.find(v => v.name === n));
  vault.forEach(v => { if (!mOrder.includes(v.name)) mOrder.push(v.name); });
  $("#mlist").innerHTML = mOrder.map((n, i) => {
    const v = vault.find(x => x.name === n);
    return `<div class="mrow"><label class="chk"><input type="checkbox" data-msel="${esc(n)}" ${mSel.has(n) ? "checked" : ""}><b>${esc(n)}</b> <span class="muted small">${esc(v.provider)} / ${esc(v.model || "default model")}</span></label>
      <span><button class="x" data-mup="${i}" title="Move up">&uarr;</button><button class="x" data-mdown="${i}" title="Move down">&darr;</button></span></div>`;
  }).join("") || '<div class="muted small">Add provider keys in the Vault first.</div>';
}
$("#mlist").onclick = e => {
  const t = e.target;
  if (t.dataset.msel) { t.checked ? mSel.add(t.dataset.msel) : mSel.delete(t.dataset.msel); return; }
  const up = t.dataset.mup, down = t.dataset.mdown;
  const i = up !== undefined ? +up : down !== undefined ? +down : -1, j = up !== undefined ? i - 1 : i + 1;
  if (i < 0 || j < 0 || j >= mOrder.length) return;
  [mOrder[i], mOrder[j]] = [mOrder[j], mOrder[i]]; renderMerge();
};
$("#mbtn").onclick = async () => {
  const bundle = mOrder.filter(n => mSel.has(n));
  if (!bundle.length) return alert("Tick at least one key to merge.");
  try {
    const r = await api("/api/keys", {method: "POST", body: {label: $("#mlabel").value, assistants: [], bundle, bundle_mode: $("#mmode").value, ...readLimits("m")}});
    showKey("#mout", r.key); loadKeys();
  } catch (e) { alert(e.message); }
};

if (pw) enter();


// ---------- AI Lab ----------
async function loadAiModels() {
  try {
    const data = await api("/api/ai/models");
    const textModels = data.text || [], visionModels = data.vision || [];
    $("#ai-text-model").innerHTML = textModels.map(m => `<option value="${esc(m.name)}">${esc(m.name)}</option>`).join("") || '<option value="">No trained text models</option>';
    $("#ai-vision-model").innerHTML = visionModels.map(m => `<option value="${esc(m.name)}">${esc(m.name)}</option>`).join("") || '<option value="">No trained vision models</option>';
    const groups = [
      ...textModels.map(m => ({...m, kind: "Text Transformer"})),
      ...visionModels.map(m => ({...m, kind: "Vision CNN"}))
    ];
    $("#ai-model-list").innerHTML = groups.map(m => {
      const metrics = (m.metrics || []).map(x => m.kind === "Text Transformer"
        ? `E${x.epoch}: loss ${x.train_loss}`
        : `E${x.epoch}: validation ${(100 * x.validation_accuracy).toFixed(1)}%`).join(" · ");
      const details = m.kind === "Text Transformer"
        ? `${Number(m.parameters || 0).toLocaleString()} parameters · ${m.dataset_bytes || 0} dataset bytes`
        : `${m.image_count || 0} images · ${(m.classes || []).join(", ")}`;
      return `<div class="ai-model"><div><b>${esc(m.name)}</b><span class="ai-tag">${esc(m.kind)}</span><p class="muted small">${esc(details)}</p><p class="muted small">${esc(metrics)}</p></div></div>`;
    }).join("") || '<p class="muted small">No local models yet. Train one above.</p>';
    if (!data.torch_available) $("#ai-model-list").insertAdjacentHTML("beforeend", '<p class="err small">PyTorch is not installed. Add requirements-ai.txt dependencies and restart the server.</p>');
  } catch (e) { $("#ai-model-list").textContent = e.message; }
}
$("#ai-refresh").onclick = loadAiModels;

function setAiStatus(id, message, bad = false) {
  const el = $(id); el.textContent = message; el.className = "small " + (bad ? "err" : "muted");
}
async function pollAiJob(jobId, statusId) {
  while (true) {
    const job = await api(`/api/ai/jobs/${encodeURIComponent(jobId)}`);
    const metrics = (job.metrics || []).slice(-1)[0];
    const metricText = metrics ? (metrics.train_loss !== undefined ? ` · train loss ${metrics.train_loss}` : ` · validation ${(100 * metrics.validation_accuracy).toFixed(1)}%`) : "";
    setAiStatus(statusId, `${job.status.toUpperCase()} · ${job.progress || 0}% · ${job.message || ""}${metricText}`, job.status === "failed");
    if (["completed", "failed"].includes(job.status)) { if (job.status === "completed") await loadAiModels(); return job; }
    await new Promise(resolve => setTimeout(resolve, 1800));
  }
}
$("#ai-text-train").onclick = async () => {
  const file = $("#ai-text-file").files[0];
  if (!file) return setAiStatus("#ai-text-status", "Choose a text dataset first.", true);
  const fd = new FormData();
  fd.append("file", file); fd.append("model_name", $("#ai-text-name").value);
  ["epochs", "steps", "block", "emb", "heads", "layers"].forEach(k => fd.append(
    ({epochs:"epochs",steps:"steps_per_epoch",block:"block_size",emb:"embedding_size",heads:"attention_heads",layers:"layers"})[k], $("#ai-text-" + k).value));
  try { const r = await api("/api/ai/text/train", {method:"POST", body:fd}); await pollAiJob(r.job_id, "#ai-text-status"); }
  catch (e) { setAiStatus("#ai-text-status", e.message, true); }
};
$("#ai-vision-train").onclick = async () => {
  const file = $("#ai-vision-file").files[0];
  if (!file) return setAiStatus("#ai-vision-status", "Choose a dataset ZIP first.", true);
  const fd = new FormData(); fd.append("file", file); fd.append("model_name", $("#ai-vision-name").value);
  fd.append("epochs", $("#ai-vision-epochs").value); fd.append("image_size", $("#ai-vision-size").value); fd.append("batch_size", $("#ai-vision-batch").value);
  try { const r = await api("/api/ai/vision/train", {method:"POST", body:fd}); await pollAiJob(r.job_id, "#ai-vision-status"); }
  catch (e) { setAiStatus("#ai-vision-status", e.message, true); }
};
$("#ai-text-generate").onclick = async () => {
  const model = $("#ai-text-model").value; if (!model) return $("#ai-text-output").textContent = "Train a text model first.";
  $("#ai-text-output").textContent = "Generating…";
  try { const r = await api("/api/ai/text/generate", {method:"POST", body:{model, prompt:$("#ai-text-prompt").value, max_new_tokens:160, temperature:0.8}}); $("#ai-text-output").textContent = r.text + "\n\n" + r.warning; }
  catch (e) { $("#ai-text-output").textContent = e.message; }
};
$("#ai-vision-predict").onclick = async () => {
  const model = $("#ai-vision-model").value, file = $("#ai-vision-test-file").files[0];
  if (!model || !file) return $("#ai-vision-output").textContent = "Choose a trained vision model and test image.";
  const fd = new FormData(); fd.append("model_name", model); fd.append("file", file); $("#ai-vision-output").textContent = "Classifying…";
  try { const r = await api("/api/ai/vision/predict", {method:"POST", body:fd}); $("#ai-vision-output").textContent = `Prediction: ${r.category}\nConfidence: ${(100*r.confidence).toFixed(1)}%\nScores: ${JSON.stringify(r.scores, null, 2)}\n\n${r.warning}`; }
  catch (e) { $("#ai-vision-output").textContent = e.message; }
};
