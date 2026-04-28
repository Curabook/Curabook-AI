/**
 * script.js — Curabook PHI v3.0 (Reviewer Patch)
 *
 * FIXES: 
 * 1. Optimistic Chat Deletion with confirmation.
 * 2. Infinite Loader timeouts for Health & Reports.
 * 3. Wearable Sync camera integration.
 * 4. Removed Export Chat clutter.
 */
"use strict";

const SUPABASE_URL = "https://pbeaawlxdcrdbvlmpqhc.supabase.co";
const SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBiZWFhd2x4ZGNyZGJ2bG1wcWhjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzYwMDk0MzksImV4cCI6MjA5MTU4NTQzOX0.6bUpYrDbe0mQjjBHX8Qscj-5R8i4-SqAtW_Z1UFzJ10";

const IS_LOCAL = ["localhost","127.0.0.1","0.0.0.0"].includes(location.hostname);
// This tells the browser to use the current Vercel domain, fixing all API blocks!
const API = IS_LOCAL ? "http://localhost:5000" : "";

let _sb           = null;
let _user         = null;
let _userName     = "";
let _convId       = null;
let _isSending    = false;
let _sendStart    = 0;
let _uploads      = [];
let _goalWt       = parseFloat(localStorage.getItem("phi_goal_wt") || "165");
let _proteinTarget = Math.round(_goalWt * 0.545 * 10) / 10;
let _docCtx        = { text: null, hasDoc: false, filename: "" };

let _initialized     = false;
let _consentsSaved   = false;
let _consentsPromise = null;
let _redirecting     = false;

const NOISE_MSG = {
  1:"Nearly silent.",2:"Very low.",3:"Mild.",4:"Low-moderate.",5:"Moderate.",
  6:"Elevated.",7:"High — taper may have been too fast.",8:"Very high — biology, not willpower.",
  9:"Intense. Discuss urgently.",10:"🚨 Maximum. Provider conversation needed."
};

/* ═══ THEME ═══ */
function initTheme() { applyTheme(localStorage.getItem("phi_theme") || "dark"); }
function toggleTheme() {
  const current = document.documentElement.dataset.theme || "dark";
  applyTheme(current === "dark" ? "light" : "dark");
  closeUserMenu();
}
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("phi_theme", t);
  const d = t === "dark";
  setIcon("themeIcon",   d ? "fa-moon" : "fa-sun");
  setIcon("topThemeIcon", d ? "fa-moon" : "fa-sun");
  setText("themeLabel",  d ? "Light Mode" : "Dark Mode");
}

/* ═══ SIGN OUT ═══ */
async function doSignOut() {
  if (_redirecting) return;
  _redirecting   = true;
  _initialized   = false;
  _user          = null;
  window._user   = null;
  _convId        = null;
  _isSending     = false;
  _consentsSaved = false;
  setSendingState(false);

  try {
    const keysToRemove = Object.keys(localStorage).filter(k =>
      k.startsWith("sb-")        ||
      k.startsWith("supabase")   ||
      k.startsWith("gotrue")     ||
      k.startsWith("pkce")
    );
    keysToRemove.forEach(k => localStorage.removeItem(k));
    sessionStorage.clear();
  } catch(e) {}

  try {
    if (_sb) _sb.auth.signOut({ scope: "global" }).catch(() => {});
  } catch(e) {}

  window.location.replace("/login");
}

async function handleLogout() {
  closeUserMenu();
  await doSignOut();
}

/* ═══ BOOT ═══ */
async function boot() {
  try {
    _sb = supabase.createClient(SUPABASE_URL, SUPABASE_KEY, {
      auth: {
        detectSessionInUrl: true,
        persistSession:     true,
        autoRefreshToken:   true,
      }
    });

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && _isSending && Date.now() - _sendStart > 30000) {
        _isSending = false; setSendingState(false);
      }
    });

    _sb.auth.onAuthStateChange(async (event, session) => {
      if (event === "SIGNED_IN" && session?.user && !_initialized) {
        await onSignIn(session.user);
      }
      if (event === "TOKEN_REFRESHED" && session?.user) {
        _user = session.user;
        window._user = session.user;
      }
      if (event === "SIGNED_OUT" && !_redirecting) {
        _redirecting = true;
        _initialized = false;
        _user = null;
        window._user = null;
        _convId = null;
        window.location.replace("/login");
      }
    });

    const { data } = await _sb.auth.getSession();
    if (data?.session?.user) {
      if (!_initialized) await onSignIn(data.session.user);
    } else {
      if (!IS_LOCAL) {
        window.location.replace("/login");
      }
    }
  } catch(err) {
    console.error("[PHI] Boot:", err);
    toast("Failed to initialize — please refresh.", "err");
  }
}

async function onSignIn(user) {
  if (_initialized) return; 
  _initialized = true;
  _user = user;
  window._user = user;

  const meta = user.user_metadata || {};
  _userName = meta.first_name
    || user.email?.split("@")[0]?.split(/[._-]/)[0]
    || "there";
  _userName = _userName[0].toUpperCase() + _userName.slice(1);

  setText("userEmail", user.email);
  setText("welcomeName", _userName);
  const av = el("userAvatar");
  if (av) av.textContent = _userName[0].toUpperCase();

  const h = new Date().getHours();
  setText("timeGreeting", h < 12 ? "morning" : h < 17 ? "afternoon" : "evening");

  await saveConsents().catch(() => {});
  
  if (!window._perf_patch_active || window._perf_patch_failed_auth) {
    await loadHistory();
    autoLoadShield().catch(() => {});
    loadMarkersData().catch(() => {});
  }

  const gw = localStorage.getItem("phi_goal_wt");
  if (gw) {
    if (el("inputGoalWt")) el("inputGoalWt").value = gw;
    if (el("proteinInput")) el("proteinInput").value = gw;
    calcProteinDisplay(parseFloat(gw), false);
  }
}

/* ═══ API HELPERS ═══ */
async function session() {
  if (!_sb) return null;
  try {
    const { data } = await _sb.auth.getSession();
    return data?.session || null;
  } catch(e) { return null; }
}

async function headers(ct = true) {
  const s = await session();
  if (!s?.access_token) return null;
  const h = { Authorization: `Bearer ${s.access_token}` };
  if (ct) h["Content-Type"] = "application/json";
  return h;
}

async function apiFetch(path, opts = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 32000);
  try {
    const r = await fetch(API + path, { ...opts, signal: ctrl.signal });
    clearTimeout(t);
    return r;
  } catch(e) {
    clearTimeout(t);
    if (e.name === "AbortError") throw new Error("Request timed out");
    throw e;
  }
}

async function apiJson(path, opts = {}) {
  const res = await apiFetch(path, opts);
  const txt = await res.text().catch(() => "");
  let data = null;
  try { data = txt ? JSON.parse(txt) : null; } catch {}
  return { ok: res.ok, status: res.status, data };
}

async function handleUnauthorized() {
  try {
    const { data } = await _sb.auth.refreshSession();
    if (data?.session) { 
      _user = data.session.user; 
      window._user = data.session.user;
      return true; 
    }
  } catch {}
  await doSignOut();
  return false;
}

/* ═══ CONSENTS ═══ */
async function saveConsents() {
  if (_consentsSaved) return;
  if (_consentsPromise) return _consentsPromise;
  _consentsPromise = (async () => {
    try {
      const h = await headers();
      if (!h) return;
      const res = await apiFetch("/api/consent", {
        method: "POST",
        headers: h,
        body: JSON.stringify({ consents: ["data_processing", "ai_processing", "document_processing"] })
      });
      if (res.ok || res.status === 200) _consentsSaved = true;
    } catch(e) {
      console.warn("[PHI] saveConsents:", e);
    } finally {
      _consentsPromise = null;
    }
  })();
  return _consentsPromise;
}

/* ═══ SIDEBAR & COCKPIT (MOBILE SMOOTH FIX) ═══ */
const closeSidebar = () => { el("sidebar")?.classList.remove("open"); el("sidebarOverlay")?.classList.remove("show"); };
const closeCockpit = () => { el("cockpit")?.classList.remove("open"); el("cockpitOverlay")?.classList.remove("show"); };

const openSidebar = () => {
  if (el("cockpit")?.classList.contains("open")) { 
    closeCockpit(); 
    setTimeout(() => { el("sidebar")?.classList.add("open"); el("sidebarOverlay")?.classList.add("show"); }, 200); 
  } else { 
    el("sidebar")?.classList.add("open"); el("sidebarOverlay")?.classList.add("show"); 
  }
};

const openCockpit = () => {
  if (el("sidebar")?.classList.contains("open")) { 
    closeSidebar(); 
    setTimeout(() => { el("cockpit")?.classList.add("open"); el("cockpitOverlay")?.classList.add("show"); }, 200); 
  } else { 
    el("cockpit")?.classList.add("open"); el("cockpitOverlay")?.classList.add("show"); 
  }
};

const toggleCockpit = () => el("cockpit")?.classList.contains("open") ? closeCockpit() : openCockpit();
const toggleUserMenu = () => {
  const dd = el("userDropdown");
  if (dd) dd.setAttribute("aria-hidden", dd.getAttribute("aria-hidden") === "false" ? "true" : "false");
};
const closeUserMenu = () => el("userDropdown")?.setAttribute("aria-hidden", "true");

/* ═══ VIEWS ═══ */
function switchView(view) {
  ["chat", "health", "reports"].forEach(v => {
    el(`view${v[0].toUpperCase() + v.slice(1)}`)?.classList.toggle("active", v === view);
    el(`nav${v[0].toUpperCase() + v.slice(1)}`)?.classList.toggle("active", v === view);
  });
  closeSidebar();
  if (view === "health")  loadHealthView();
  if (view === "reports") loadReportsView();
  setText("convTitle", { chat: "Chat with PHI", health: "My Health", reports: "Lab Reports" }[view] || "");
}

async function loadHealthView() {
  const content = el("healthContent"); if (!content) return;
  
  if (!content.querySelector(".cliff-card") && !content.querySelector(".trend-card")) {
    content.innerHTML = `<div class="hv-empty" id="healthLoading"><i class="fa-solid fa-spinner fa-spin"></i>Loading your health picture…</div>`;
  }

  // REVIEWER FIX: Infinite Loader Timeout
  const timeoutId = setTimeout(() => {
    const loader = el("healthLoading");
    if (loader) {
      content.innerHTML = `<div class="hv-empty"><i class="fa-solid fa-heart-pulse" style="font-size:2rem;opacity:.3;margin-bottom:12px;display:block"></i>No health markers detected yet. Upload a lab report.<br><button class="hv-cta-btn" onclick="el('fileInput').click()"><i class="fa-solid fa-upload"></i> Upload First Report</button></div>`;
    }
  }, 4000);
  
  const h = await headers(); if (!h) { clearTimeout(timeoutId); return; }
  try {
    const [mR, dR] = await Promise.allSettled([
      apiJson("/api/health-markers", { headers: h }),
      apiJson("/api/dashboard",      { headers: h }),
    ]);
    clearTimeout(timeoutId);

    const markers  = mR.status === "fulfilled" && mR.value.ok && Array.isArray(mR.value.data) ? mR.value.data : [];
    const dashData = dR.status === "fulfilled" && dR.value.ok && dR.value.data ? dR.value.data : null;
    if (!markers.length) {
      content.innerHTML = `<div class="hv-empty"><i class="fa-solid fa-chart-line" style="font-size:2rem;opacity:.3;margin-bottom:12px;display:block"></i>
        No health data yet. Upload a lab report to see your cliff risk picture.
        <br><button class="hv-cta-btn" onclick="el('fileInput').click()">
          <i class="fa-solid fa-upload"></i> Upload First Report
        </button></div>`;
      return;
    }
    content.innerHTML = buildHealthViewHTML(markers, dashData);
    content.querySelectorAll("[data-ask]").forEach(btn =>
      btn.addEventListener("click", () => { switchView("chat"); setTimeout(() => sendMessage(btn.dataset.ask), 100); })
    );
  } catch(e) {
    clearTimeout(timeoutId);
    content.innerHTML = `<div class="hv-empty">Could not load health data.</div>`;
  }
}

function buildHealthViewHTML(markers, dashboard) {
  const abnormal    = markers.filter(m => m.status === "HIGH" || m.status === "LOW");
  const trending    = (dashboard?.trends        || []);
  const cliffalerts = (dashboard?.cliff_alerts  || []);
  const riskLevel   = cliffalerts.length > 0 ? "high" : abnormal.length > 2 ? "warn" : "none";
  const riskScore   = cliffalerts.length || abnormal.length;
  const riskLabel   = riskLevel === "high"  ? "Active Rebound Signals"
                    : riskLevel === "warn"  ? "Needs Attention"
                    : "No Cliff Signals";
  const riskDesc    = riskLevel === "high"
                    ? `${cliffalerts.length} threshold${cliffalerts.length > 1 ? "s" : ""} exceeded.`
                    : riskLevel === "warn"  ? `${abnormal.length} markers outside normal range.`
                    : "All monitored markers within normal range.";
  let html = `<div class="hv-section">
    <div class="hv-heading"><i class="fa-solid fa-triangle-exclamation"></i>Cliff Risk</div>
    <div class="cliff-card risk-${riskLevel}">
      <div><div class="cliff-num risk-${riskLevel}">${riskLevel === "none" ? "✓" : riskScore}</div></div>
      <div class="cliff-detail"><h3>${riskLabel}</h3><p>${riskDesc}</p>
        ${riskLevel !== "none" ? `<button class="alert-cta" data-ask="Run a full cliff risk analysis on my stored data. What are my urgent signals?">Ask PHI →</button>` : ""}
      </div></div></div>`;

  if (cliffalerts.length) {
    html += `<div class="hv-section"><div class="hv-heading"><i class="fa-solid fa-bolt"></i>Active Alerts</div><div class="alert-feed">`;
    cliffalerts.forEach(a => { html += `<div class="alert-item danger">
      <div class="alert-title">${esc(a.headline || a.title || "Rebound signal")}</div>
      <div class="alert-desc">${esc(a.detail || "")}</div>
      ${a.action ? `<button class="alert-cta" data-ask="${esc(a.action)}">What to do →</button>` : ""}
    </div>`; });
    html += `</div></div>`;
  }

  if (markers.length) {
    html += `<div class="hv-section"><div class="hv-heading"><i class="fa-solid fa-flask"></i>Lab Values</div><div class="trend-grid">`;
    markers.slice(0, 12).forEach(m => {
      const s   = (m.status || "").toLowerCase();
      const cls = s === "high" ? "hi" : s === "low" ? "lo" : s === "normal" ? "ok" : "";
      const tr  = trending.find(t => t.marker === m.marker_name);
      const badge = tr ? `<div class="trend-badge ${tr.concerning ? "bad" : "good"}">${tr.direction === "rising" ? "↑" : "↓"} ${tr.pct_change}%</div>` : "";
      html += `<div class="trend-card">
        <div class="trend-name" title="${esc(m.marker_name)}">${esc(m.marker_name)}</div>
        <div><span class="trend-val ${cls}">${m.value}</span><span class="trend-unit"> ${esc(m.unit || "")}</span></div>
        ${badge}${m.date ? `<div class="trend-dates">${m.date}</div>` : ""}
      </div>`;
    });
    html += `</div></div>`;
  }

  if (abnormal.length) {
    html += `<div class="hv-section"><div class="hv-heading"><i class="fa-solid fa-circle-exclamation"></i>Needs Attention</div><div class="alert-feed">`;
    abnormal.slice(0, 6).forEach(m => { html += `<div class="alert-item warn">
      <div class="alert-title">${esc(m.marker_name)} is ${m.status}</div>
      <div class="alert-desc">${m.value} ${esc(m.unit || "")} — ${m.status === "HIGH" ? "above" : "below"} normal${m.reference_range ? ` (${esc(m.reference_range)})` : ""}</div>
      <button class="alert-cta" data-ask="Explain my ${esc(m.marker_name)} result of ${m.value}. Is this GLP-1 cliff related?">Explain →</button>
    </div>`; });
    html += `</div></div>`;
  }
  return html;
}

async function loadReportsView() {
  const list = el("reportsList"); if (!list) return;
  list.innerHTML = `<div class="hv-empty" id="reportsLoading"><i class="fa-solid fa-spinner fa-spin"></i>Loading reports…</div>`;
  
  // REVIEWER FIX: Infinite Loader Timeout
  const timeoutId = setTimeout(() => {
    const loader = el("reportsLoading");
    if (loader) {
      list.innerHTML = `<div class="hv-empty"><i class="fa-solid fa-file-medical" style="font-size:2rem;opacity:.3;margin-bottom:12px;display:block"></i>No lab reports uploaded yet.<br><button class="hv-cta-btn" onclick="el('fileInput').click()"><i class="fa-solid fa-upload"></i> Upload First Report</button></div>`;
    }
  }, 4000);

  const h = await headers(); if (!h) { clearTimeout(timeoutId); return; }
  try {
    const { ok, data } = await apiJson("/doctor-prep/history", { headers: h });
    clearTimeout(timeoutId);

    if (!ok || !data?.preps?.length) {
      list.innerHTML = `<div class="hv-empty"><i class="fa-solid fa-file-medical" style="font-size:2rem;opacity:.3;margin-bottom:12px;display:block"></i>No lab reports yet.
        <br><button class="hv-cta-btn" onclick="el('fileInput').click()"><i class="fa-solid fa-upload"></i> Upload First Report</button></div>`;
      return;
    }
    list.innerHTML = data.preps.map(p => {
      const date = p.generated_at
        ? new Date(p.generated_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
        : "";
      return `<div class="report-card">
        <div class="report-icon"><i class="fa-solid fa-file-medical-alt"></i></div>
        <div class="report-meta">
          <div class="report-name">${esc(p.filename || "Lab Report")}</div>
          <div class="report-date">${date}</div>
          <div class="report-tags"><span class="report-tag info">Lab</span></div>
        </div>
        <button class="report-ask-btn" onclick="askAboutReport('${esc(p.filename || "report")}')">Ask PHI →</button>
      </div>`;
    }).join("");
  } catch {
    clearTimeout(timeoutId);
    list.innerHTML = `<div class="hv-empty">Could not load reports.</div>`;
  }
}

function askAboutReport(f) { switchView("chat"); setTimeout(() => sendMessage(`Summarize my ${f} report and flag any cliff signals.`), 100); }

/* ═══ HISTORY ═══ */
async function loadHistory() {
  const h = await headers(); if (!h) return;
  try {
    let { ok, status, data } = await apiJson("/history", { method: "POST", headers: h, body: JSON.stringify({}) });
    if (status === 401) {
      const ok2 = await handleUnauthorized();
      if (!ok2) return;
      const h2 = await headers(); if (!h2) return;
      ({ ok, data } = await apiJson("/history", { method: "POST", headers: h2, body: JSON.stringify({}) }));
    }
    if (ok && Array.isArray(data)) renderHistory(data);
  } catch(e) { console.warn("[PHI] loadHistory:", e); }
}

function renderHistory(convs) {
  const list = el("historyList"); if (!list) return;
  if (!convs.length) { list.innerHTML = '<div class="sb-empty">No conversations yet</div>'; return; }
  const today = new Date(); today.setHours(0,0,0,0);
  const yest  = new Date(today); yest.setDate(today.getDate() - 1);
  const groups = new Map();
  convs.forEach(c => {
    const d = new Date(c.created_at || Date.now()); d.setHours(0,0,0,0);
    const label = d.getTime() === today.getTime() ? "Today"
                : d.getTime() === yest.getTime()  ? "Yesterday"
                : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(c);
  });
  let html = "";
  groups.forEach((arr, label) => {
    html += `<div class="hist-group-label">${esc(label)}</div>`;
    arr.forEach(c => {
      const active = c.id === _convId ? " active" : "";
      const title  = (c.title && c.title !== "New Chat") ? c.title : "New Conversation";
      html += `<div class="hist-item${active}" data-id="${esc(c.id)}">
        <span class="hist-title">${esc(title)}</span>
        <button class="hist-del" data-del="${esc(c.id)}" title="Delete"><i class="fa-solid fa-trash"></i></button>
      </div>`;
    });
  });
  list.innerHTML = html;
}

async function openConversation(id) {
  if (_isSending || id === _convId) { closeSidebar(); return; }
  _convId = id; _uploads = []; _docCtx = { text: null, hasDoc: false, filename: "" };
  clearFilePreview(); showChat();
  if (el("chatDisplay")) el("chatDisplay").innerHTML = "";
  document.querySelectorAll(".hist-item").forEach(e => e.classList.toggle("active", e.dataset.id === id));
  closeSidebar();
  const h = await headers(); if (!h) return;
  try {
    const { ok, data } = await apiJson("/conversation", { method: "POST", headers: h, body: JSON.stringify({ conversation_id: id }) });
    if (ok && Array.isArray(data)) { data.forEach(m => appendMsg(m.content, m.role === "user" ? "user" : "ai")); scrollBottom(); }
  } catch {}
}

// REVIEWER FIX: Optimistic UI Deletion
async function deleteConversation(id, e) {
  e?.preventDefault();
  e?.stopPropagation();
  
  if (!confirm("Are you sure you want to delete this conversation?")) return;

  // Optimistic Hide
  const elItem = document.querySelector(`.hist-item[data-id="${id}"]`);
  if (elItem) elItem.style.display = 'none';
  
  if (id === _convId) resetChat();

  const h = await headers();
  if (h) {
    try {
      // Must use apiJson to resolve proper backend routing
      const res = await apiJson("/delete", { method: "POST", headers: h, body: JSON.stringify({ conversation_id: id }) });
      
      // If the server returns a 4xx or 5xx status, throw an error to trigger the catch block
      if (!res.ok) throw new Error("Backend deletion failed");

      if (elItem) elItem.remove();
      toast("Conversation deleted");
    } catch (err) {
      console.error("Failed to delete", err);
      if (elItem) elItem.style.display = 'flex'; // Bring back if it fails
      toast("Failed to delete conversation", "err");
    }
  }
}

/* ═══ CHAT STATE ═══ */
function resetChat() {
  _convId = null; _uploads = []; _docCtx = { text: null, hasDoc: false, filename: "" };
  clearFilePreview();
  if (el("chatDisplay")) el("chatDisplay").innerHTML = "";
  showWelcome();
  document.querySelectorAll(".hist-item").forEach(e => e.classList.remove("active"));
  setText("convTitle", "Ready");
}
function showWelcome() { el("welcomeScreen")?.classList.remove("hidden"); el("chatDisplay")?.classList.add("hidden"); }
function showChat()    { el("welcomeScreen")?.classList.add("hidden");    el("chatDisplay")?.classList.remove("hidden"); }

/* ═══ CONVERSATION CREATE ═══ */
async function createConversation() {
  await saveConsents().catch(() => {});
  const h = await headers();
  if (!h) { toast("Session expired.", "err"); doSignOut(); return null; }
  const doCreate = async (hdr) => apiJson("/conversation/create", { method: "POST", headers: hdr, body: JSON.stringify({}) });
  let { ok, status, data } = await doCreate(h);
  if (!ok && status === 403) {
    _consentsSaved = false;
    await saveConsents().catch(() => {});
    const h2 = await headers();
    if (h2) ({ ok, status, data } = await doCreate(h2));
  }
  if (!ok && status === 401) { await handleUnauthorized(); return null; }
  if (ok && data?.conversation_id) {
    _convId = data.conversation_id;
    prependHistory(_convId, "New Conversation");
    return _convId;
  }
  if (IS_LOCAL) { _convId = "local-" + Date.now(); return _convId; }
  toast("Could not start conversation. Try refreshing.", "err");
  return null;
}

function prependHistory(id, title) {
  const list = el("historyList"); if (!list) return;
  list.querySelector(".sb-empty")?.remove();
  let group = list.querySelector(".hist-group-label");
  if (!group || group.textContent !== "Today") {
    group = Object.assign(document.createElement("div"), { className: "hist-group-label", textContent: "Today" });
    list.prepend(group);
  }
  const item = Object.assign(document.createElement("div"), { className: "hist-item active" });
  item.dataset.id = id;
  item.innerHTML = `<span class="hist-title">${esc(title)}</span><button class="hist-del" data-del="${esc(id)}" title="Delete"><i class="fa-solid fa-trash"></i></button>`;
  group.insertAdjacentElement("afterend", item);
  document.querySelectorAll(".hist-item").forEach(e => e.classList.toggle("active", e.dataset.id === id));
}

async function renameConversation(id, title) {
  const h = await headers(); if (!h || !id) return;
  const short = title.slice(0, 50);
  const t = document.querySelector(`.hist-item[data-id="${id}"] .hist-title`); if (t) t.textContent = short;
  setText("convTitle", short);
  await apiFetch("/rename", { method: "POST", headers: h, body: JSON.stringify({ conversation_id: id, title: short }) }).catch(() => {});
}

/* ═══ SEND ═══ */
async function handleSend() {
  if (_isSending) return;
  const ta = el("chatInput");
  let text = ta?.value.trim();
  if (!text && _uploads.length) text = "Please analyze my uploaded lab report — explain every finding and flag any cliff signals.";
  if (!text) return;
  if (ta) { ta.value = ""; ta.style.height = "auto"; }
  await sendMessage(text);
}

async function sendMessage(text) {
  if (_isSending || !text) return;
  _isSending = true; _sendStart = Date.now();
  setSendingState(true); switchView("chat"); showChat();
  try {
    if (!_convId) {
      const id = await createConversation();
      if (!id) return;
    }

    if (_uploads.length) {
      const lr = appendTyping(); updateTyping(lr, "📄 Reading your lab report…");
      const result = await processUpload(_uploads[0]);
      lr?.remove(); _uploads = []; clearFilePreview();
      if (result?.document_text) {
        _docCtx = { text: result.document_text, hasDoc: true, filename: result.filename || "" };
        toast(`${result.filename || "Report"} analyzed ✓`);
      } else if (result === null) return;
    }

    appendMsg(text, "user");
    const botRow = appendTyping();
    scrollBottom();

    const h = await headers();
    if (!h) { updateMsg(botRow, "Session expired — please refresh."); await handleUnauthorized(); return; }

    const payload = {
      conversation_id: _convId,
      message:         text,
      has_documents:   _docCtx.hasDoc,
      document_text:   _docCtx.hasDoc ? (_docCtx.text || "") : ""
    };

    let dotCount = 0;
    const typingInterval = setInterval(() => {
      dotCount = (dotCount + 1) % 4;
      updateTyping(botRow, "PHI is thinking" + ".".repeat(dotCount));
    }, 600);

    let { ok, status, data } = await apiJson("/chat", { method: "POST", headers: h, body: JSON.stringify(payload) });
    clearInterval(typingInterval);

    if (!ok && status === 401) {
      const ok2 = await handleUnauthorized();
      if (ok2) {
        const h2 = await headers();
        if (h2) ({ ok, status, data } = await apiJson("/chat", { method: "POST", headers: h2, body: JSON.stringify(payload) }));
      }
    }
    if (!ok && status === 403) {
      _consentsSaved = false;
      await saveConsents().catch(() => {});
      const h3 = await headers();
      if (h3) ({ ok, status, data } = await apiJson("/chat", { method: "POST", headers: h3, body: JSON.stringify(payload) }));
    }

    if (ok && data?.reply) {
      updateMsg(botRow, data.reply);
      const chatDisplay = el("chatDisplay");
      const userMsgs = chatDisplay?.querySelectorAll(".chat-msg.user-msg");
      if (userMsgs?.length === 1 && _convId) renameConversation(_convId, text);
      if (_docCtx.hasDoc) setTimeout(loadMarkersData, 2000);
    } else {
      const msg = status === 401 ? "Session expired — please sign in again."
                : status === 403 ? "Access issue — please refresh the page."
                : "I ran into a technical issue. Please try again in a moment.";
      updateMsg(botRow, msg + "\n\n---\n⚕️ *Always consult your healthcare provider.*");
    }
  } catch(err) {
    console.error("[PHI] sendMessage:", err);
    const d = el("chatDisplay");
    if (d) {
      const last = d.querySelector(".ai-msg:last-child .msg-body");
      if (last && last.querySelector(".typing-indicator")) {
        const errMsg = err.message?.includes("timed out")
          ? "Request timed out — your PDF may be large. Try a smaller file."
          : "Connection error — please check your internet and try again.";
        last.innerHTML = errMsg + "<p class='phi-legal'>⚕️ PHI is an educational wellness tool.</p>";
      }
    }
  } finally {
    _isSending = false; setSendingState(false); scrollBottom();
  }
}

function setSendingState(on) {
  const btn = el("sendBtn"), ta = el("chatInput");
  if (btn) {
    btn.disabled  = on;
    btn.innerHTML = on
      ? '<i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i>'
      : '<i class="fa-solid fa-arrow-up"></i>';
  }
  if (ta) ta.disabled = on;
}

/* ═══ FILE UPLOAD (IMAGE SUPPORT FIX) ═══ */
function handleFileSelect(e) { 
  Array.from(e.target.files || []).forEach(addFile); 
  e.target.value = ""; 

  // REVIEWER FIX: Auto-prompt for Wearable Sync
  if (window.isWearableSync) {
    const ta = el('chatInput');
    if (ta) {
      ta.value = "Here is a screenshot from my health app. Please extract my steps, sleep, and protein and log them for today.";
      autoGrow(ta);
    }
    window.isWearableSync = false;
    
    // Auto-click send after a tiny delay for UX feel
    setTimeout(() => {
      const sendBtn = el('sendBtn');
      if (sendBtn && !sendBtn.disabled) sendBtn.click();
    }, 500);
  }
}

function addFile(file) {
  if (file.size > 10 * 1024 * 1024) { toast(`${file.name} too large (max 10MB).`, "err"); return; }
  
  const isImg = file.type.startsWith("image/") || /\.(png|jpe?g|webp|heic)$/i.test(file.name);
  const isDoc = /\.(pdf|txt)$/i.test(file.name);
  
  if (!isDoc && !isImg) { toast("Only PDF, TXT, or Images supported.", "err"); return; }
  
  _uploads.push(file);
  renderFilePreview();
  toast(`${file.name} ready — press Send to analyze`);
}

function removeFile(i) { _uploads.splice(i, 1); renderFilePreview(); }

function renderFilePreview() {
  const s = el("filePreview"); if (!s) return;
  if (!_uploads.length) { s.classList.remove("show"); s.innerHTML = ""; return; }
  s.classList.add("show");
  
  s.innerHTML = _uploads.map((f, i) => {
    const isImg = f.type.startsWith("image/") || /\.(png|jpe?g|webp|heic)$/i.test(f.name);
    const iconClass = f.name.endsWith(".pdf") ? "fa-file-pdf" : isImg ? "fa-file-image" : "fa-file-lines";
    
    return `
      <div class="file-chip">
        <i class="fa-solid ${iconClass}"></i>
        <span>${esc(f.name)}</span>
        <button class="file-chip-rm" onclick="removeFile(${i})"><i class="fa-solid fa-xmark"></i></button>
      </div>`;
  }).join("");
}

function clearFilePreview() {
  const s = el("filePreview");
  if (s) { s.classList.remove("show"); s.innerHTML = ""; }
}

async function processUpload(file) {
  const s = await session(); if (!s) { toast("Session expired.", "err"); return null; }

  const doUp = (token) => fetch(API + "/analyze", {
    method:  "POST",
    headers: { Authorization: `Bearer ${token}` },
    body:    (() => { const f = new FormData(); f.append("file", file); return f; })()
  });

  try {
    let res = await Promise.race([
      doUp(s.access_token),
      new Promise((_, r) => setTimeout(() => r(new Error("timed out")), 60000)) 
    ]);
    if (res.status === 401) { await handleUnauthorized(); return null; }
    if (res.status === 403) {
      _consentsSaved = false;
      await saveConsents().catch(() => {});
      const s2 = await session();
      if (s2) res = await doUp(s2.access_token);
    }
    if (res.status === 413) { toast("File too large (max 5MB).", "err"); return null; }
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      toast(d.error || `Upload failed (${res.status}).`, "err");
      return null;
    }
    return await res.json();
  } catch(err) {
    toast(err.message?.includes("timed out") ? "Upload timed out — try a smaller file." : "Upload failed.", "err");
    return null;
  }
}

/* ═══ MESSAGES ═══ */
function appendMsg(text, role) {
  const d = el("chatDisplay"); if (!d) return null;
  const wrap = document.createElement("div");
  wrap.className = `chat-msg ${role === "user" ? "user-msg" : "ai-msg"}`;
  const av = `<div class="msg-av ${role === "user" ? "av-user" : "av-ai"}">${role === "user" ? (_userName?.[0]?.toUpperCase() || "U") : "φ"}</div>`;
  const body = document.createElement("div"); body.className = "msg-body";
  if (role === "user") {
    body.textContent = text;
    wrap.innerHTML = av;
    wrap.insertBefore(body, wrap.firstChild);
  } else {
    renderAI(body, text);
    wrap.innerHTML = av;
    wrap.appendChild(body);
  }
  d.appendChild(wrap);
  return wrap;
}

function appendTyping() {
  const d = el("chatDisplay"); if (!d) return null;
  const w = document.createElement("div"); w.className = "chat-msg ai-msg";
  w.innerHTML = `<div class="msg-av av-ai">φ</div><div class="msg-body"><div class="typing-indicator"><div class="t-dot"></div><div class="t-dot"></div><div class="t-dot"></div></div></div>`;
  d.appendChild(w); scrollBottom(); return w;
}

function updateTyping(w, text) { const b = w?.querySelector(".msg-body"); if (b) b.textContent = text; }

function updateMsg(w, text) { const b = w?.querySelector(".msg-body"); if (b) { renderAI(b, text); scrollBottom(); } }

function renderAI(elem, text) {
  if (!elem) return;
  const parts = text.split(/---\n⚕️/);
  elem.innerHTML = typeof marked !== "undefined" ? marked.parse(parts[0].trim()) : esc(parts[0].trim());
  if (parts.length > 1) {
    const l = document.createElement("p");
    l.className  = "phi-legal";
    l.textContent = "⚕️ PHI is an educational wellness tool. Always consult your healthcare provider.";
    elem.appendChild(l);
  }
}

const scrollBottom = () => { const d = el("chatDisplay"); if (d) d.scrollTop = d.scrollHeight; };

/* ═══ HEALTH DATA ═══ */
async function loadMarkersData() {
  const h = await headers(); if (!h) return;
  try {
    const { ok, data } = await apiJson("/api/health-markers", { headers: h });
    if (ok && Array.isArray(data) && data.length) { renderMarkers(data); runCliffDetection(data); }
  } catch {}
}

function renderMarkers(markers) {
  const g = el("markersGrid"); if (!g) return;
  if (!markers.length) { g.innerHTML = '<div class="markers-empty">Upload a lab report to see your markers</div>'; return; }
  g.innerHTML = markers.slice(0, 10).map(m => {
    const s   = (m.status || "").toLowerCase();
    const cls = s === "high" ? "val-high" : s === "low" ? "val-low" : s === "normal" ? "val-normal" : "";
    const badge = s && s !== "unknown" ? `<span class="marker-status st-${s}">${s.toUpperCase()}</span>` : "";
    return `<div class="marker-card">
      <div class="marker-card-name" title="${esc(m.marker_name)}">${esc(m.marker_name)}</div>
      <div class="marker-card-val ${cls}">${m.value}<span class="marker-card-unit"> ${esc(m.unit || "")}</span></div>
      ${badge}
    </div>`;
  }).join("");
}

function runCliffDetection(markers) {
  const alerts = [], grouped = {};
  markers.forEach(m => {
    const k = (m.marker_name || "").toLowerCase();
    if (!grouped[k]) grouped[k] = [];
    grouped[k].push({ ...m, _v: parseFloat(m.value) });
  });
  const gk = Object.keys(grouped).find(k => /fasting.*glucose|blood.*glucose|^glucose/.test(k));
  if (gk) {
    const r = grouped[gk].sort((a, b) => a.date < b.date ? -1 : 1);
    if (r.length >= 2) {
      const pct = ((r[r.length-1]._v - r[0]._v) / r[0]._v) * 100;
      if (pct >= 15) alerts.push({ type: "danger", title: `🚨 Glucose rebound +${pct.toFixed(0)}%`, desc: `${r[0]._v} → ${r[r.length-1]._v} mg/dL` });
      else if (pct >= 10) alerts.push({ type: "warn", title: `⚠ Glucose rising +${pct.toFixed(0)}%`, desc: "Approaching 15% threshold." });
    }
  }
  const hk = Object.keys(grouped).find(k => /hba1c/.test(k));
  if (hk) {
    const r = grouped[hk].sort((a, b) => a.date < b.date ? -1 : 1);
    for (let i = 1; i < r.length; i++) {
      const d = r[i]._v - r[i-1]._v;
      if (d >= 0.25) { alerts.push({ type: "danger", title: `🚨 HbA1c rebound +${d.toFixed(2)}%`, desc: `${r[i-1]._v}% → ${r[i]._v}%` }); break; }
    }
  }
  markers.filter(m => m.status === "HIGH" && !/glucose/i.test(m.marker_name)).slice(0, 2)
    .forEach(m => alerts.push({ type: "warn", title: `⬆ ${m.marker_name} HIGH`, desc: `${m.value} ${m.unit || ""}` }));
  if (!alerts.length) alerts.push({ type: "ok", title: "✅ No rebound signals", desc: "All markers stable. Keep up protein + training." });
  const c = el("cliffAlerts");
  if (c) c.innerHTML = alerts.map(a => `<div class="ca-item ca-${a.type}"><div class="ca-title">${a.title}</div><div class="ca-desc">${a.desc}</div></div>`).join("");
}

/* ═══ SHIELD ═══ */
async function autoLoadShield() {
  const h = await headers(); if (!h) { renderShield(0, 0, 0, null); return; }
  const today = new Date().toISOString().slice(0, 10);
  try {
    const { ok, status, data } = await apiJson(`/api/behavioral-logs?days=1`, { headers: h });
    if (!ok || status >= 500 || !Array.isArray(data)) { renderShield(0, 0, 0, null); return; }
    const tl  = data.filter(l => l.date === today);
    const get = m => { const l = tl.filter(x => x.metric_name === m).sort((a, b) => a.created_at < b.created_at ? 1 : -1)[0]; return l ? parseFloat(l.value) : 0; };
    const p = get("protein"), s = get("steps"), sl = get("sleep");
    if (p  > 0 && el("inputProtein")) el("inputProtein").value = p;
    if (s  > 0 && el("inputSteps"))   el("inputSteps").value   = s;
    if (sl > 0 && el("inputSleep"))   el("inputSleep").value   = sl;
    if (data.length > 0) {
      const last = data.sort((a, b) => a.created_at < b.created_at ? 1 : -1)[0];
      const w = new Date(last.created_at);
      setText("shieldLastLogged", `Last logged: ${last.date === today ? `Today at ${w.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}` : w.toLocaleDateString("en-US", { month: "short", day: "numeric" })}`);
    }
    renderShield(p, s, sl, today);
  } catch(e) { console.warn("[PHI] Shield:", e); renderShield(0, 0, 0, null); }
}

function updateShield() {
  const p  = parseFloat(el("inputProtein")?.value) || 0;
  const s  = parseFloat(el("inputSteps")?.value)   || 0;
  const sl = parseFloat(el("inputSleep")?.value)   || 0;
  const gw = parseFloat(el("inputGoalWt")?.value);
  
  if (gw && gw !== _goalWt) { 
    _goalWt = gw; 
    localStorage.setItem("phi_goal_wt", String(gw)); 
    calcProteinDisplay(gw, false); 
  }

  if (window.Cache && window._user?.id) {
    window.Cache.set("shield_" + window._user.id, { protein: p, steps: s, sleep: sl });
  }

  renderShield(p, s, sl, new Date().toISOString().slice(0, 10));
  if (_user) logShieldData(p, s, sl);
}

function renderShield(p, s, sl, logDate) {
  const gw = _goalWt || 165;
  _proteinTarget = Math.round(gw * 0.545 * 10) / 10;
  const pP = Math.min(100, Math.round((p  / (_proteinTarget || 90)) * 100));
  const mP = Math.min(100, Math.round((s  / 8000) * 100));
  const rP = Math.max(0, Math.min(100, Math.round(((sl - 4) / 5) * 100)));
  const sc = Math.round((pP + mP + rP) / 3);
  setRing("ringProtein",  440, pP);
  setRing("ringMovement", 346, mP);
  setRing("ringRecovery", 258, rP);
  setText("shieldScore",  sc + "%");
  setText("shieldBadge",  sc + "%");
  setText("proteinLegend",  p  > 0 ? `${p}g / ${_proteinTarget}g (${pP}%)` : `Target: ${_proteinTarget}g — not logged`);
  setText("movementLegend", s  > 0 ? `${s.toLocaleString()} steps (${mP}%)` : "Steps — not logged");
  setText("recoveryLegend", sl > 0 ? `${sl}h sleep (${rP}%)`              : "Sleep — not logged");
  setBarPct("proteinBar",  pP);
  setBarPct("movementBar", mP);
  setBarPct("recoveryBar", rP);
}

function setRing(id, circ, pct) {
  const r = el(id);
  if (r) {
    r.style.strokeDasharray  = circ;
    r.style.strokeDashoffset = circ - (circ * Math.max(0, Math.min(100, pct)) / 100);
  }
}
function setBarPct(id, pct) { const b = el(id); if (b) b.style.width = Math.max(0, pct) + "%"; }

async function logShieldData(p, s, sl) {
  const h = await headers(); if (!h) return;
  const date = new Date().toISOString().slice(0, 10);
  const logs = [];
  if (p  > 0) logs.push({ date, metric_name: "protein", value: p,  unit: "g"     });
  if (s  > 0) logs.push({ date, metric_name: "steps",   value: s,  unit: "steps" });
  if (sl > 0) logs.push({ date, metric_name: "sleep",   value: sl, unit: "hours" });
  logs.forEach(l => apiFetch("/api/behavioral-logs", { method: "POST", headers: h, body: JSON.stringify(l) }).catch(() => {}));
  setText("shieldLastLogged", "Last logged: just now");
  toast("Shield data logged ✓");
}

/* ═══ PROTEIN CALC ═══ */
function calcProteinDisplay(gw, showDetails = true) {
  if (!gw || gw < 80 || gw > 400) { if (showDetails) toast("Enter a valid goal weight (80–400 lbs).", "err"); return; }
  _goalWt = gw; _proteinTarget = Math.round(gw * 0.545 * 10) / 10;
  const pm = Math.round(_proteinTarget / 3 * 10) / 10;
  const lu = pm >= 30;
  localStorage.setItem("phi_goal_wt", String(gw));
  setText("proteinNum",     _proteinTarget);
  setText("proteinCaption", `${gw} lbs × 0.545 = ${_proteinTarget}g/day`);
  if (showDetails) {
    const d = el("proteinDetails");
    if (d) {
      d.classList.remove("hidden");
      d.innerHTML = `<strong>${pm}g per meal</strong> across 3 meals — ${lu ? "✅" : "⚠️"} ${lu ? "Meets" : "Below"} 30g leucine threshold<br>
        <span style="color:var(--text-3);margin-top:4px;display:block">4oz chicken (35g) + Greek yogurt (17g) + 2 eggs (12g) + whey scoop (25g)</span>`;
    }
    if (el("inputGoalWt")) el("inputGoalWt").value = gw;
    renderShield(
      parseFloat(el("inputProtein")?.value) || 0,
      parseFloat(el("inputSteps")?.value)   || 0,
      parseFloat(el("inputSleep")?.value)   || 0,
      null
    );
  }
}

/* ═══ GHRELIN / FOOD NOISE ═══ */
function updateNoiseReadout() {
  const v = parseInt(el("noiseSlider")?.value || 5);
  const colors = [, "var(--ok)", "var(--ok)", "var(--ok)", "var(--amber)", "var(--amber)",
    "var(--amber)", "var(--danger)", "var(--danger)", "var(--danger)", "var(--danger)"];
  const r = el("noiseReadout");
  if (r) r.innerHTML = `<strong style="color:${colors[v]}">Level ${v}/10</strong> — ${NOISE_MSG[v]}`;
}

async function logNoiseLevel() {
  const val = parseInt(el("noiseSlider")?.value || 5);
  const h = await headers(); if (!h) { toast("Sign in to log.", "info"); return; }
  await apiFetch("/api/behavioral-logs", {
    method: "POST", headers: h,
    body: JSON.stringify({ date: new Date().toISOString().slice(0, 10), metric_name: "food_noise", value: val, unit: "1-10" })
  }).catch(() => {});
  toast(`Food noise ${val}/10 logged ✓`, "info");
}

/* ═══ VOICE ═══ */
function initVoice() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const btn = el("micBtn");
  if (!SR || !btn) { if (btn) { btn.style.opacity = ".3"; btn.disabled = true; } return; }
  let on = false, rec = null;
  btn.addEventListener("click", () => {
    if (on) { rec?.stop(); return; }
    rec = new SR(); rec.lang = "en-US"; rec.interimResults = false;
    rec.onstart  = () => { on = true; btn.style.color = "var(--danger)"; };
    rec.onresult = e => { const ta = el("chatInput"); if (ta) { ta.value = e.results[0][0].transcript; autoGrow(ta); ta.focus(); } };
    rec.onend    = () => { on = false; btn.style.color = ""; };
    rec.onerror  = e => toast(`Mic: ${e.error}`, "err");
    try { rec.start(); } catch { toast("Voice unavailable.", "err"); }
  });
}


/* ═══ UTILS ═══ */
const el      = id => document.getElementById(id);
const esc     = s  => String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const setText = (id, v) => { const e = el(id); if (e) e.textContent = v; };
const setIcon = (id, c) => { const e = el(id); if (e) e.className = `fa-solid ${c}`; };
const autoGrow = ta => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 130) + "px"; };

function toast(msg, type = "ok") {
  const c = el("toasts"); if (!c) return;
  const t = document.createElement("div");
  const icons = { ok: "circle-check", err: "circle-exclamation", info: "circle-info" };
  t.className = `toast toast-${type}`;
  t.innerHTML = `<i class="fa-solid fa-${icons[type] || "circle-info"}"></i> ${esc(msg)}`;
  c.appendChild(t);
  setTimeout(() => {
    t.style.opacity    = "0";
    t.style.transition = "opacity .3s";
    setTimeout(() => t.remove(), 300);
  }, 3800);
}

/* ═══ EVENTS ═══ */
function wireEvents() {
  document.querySelectorAll(".nav-item[data-view]").forEach(btn =>
    btn.addEventListener("click", () => { switchView(btn.dataset.view); closeSidebar(); })
  );
  el("newChatBtn")?.addEventListener("click", () => { resetChat(); switchView("chat"); });

  el("mobileMenuBtn")?.addEventListener("click", openSidebar);
  el("sidebarOverlay")?.addEventListener("click", closeSidebar);
  el("mobileCockpitBtn")?.addEventListener("click", toggleCockpit);
  el("cockpitOverlay")?.addEventListener("click", closeCockpit);
  el("cockpitCloseBtn")?.addEventListener("click", closeCockpit);

  el("userRow")?.addEventListener("click", e => { if (!e.target.closest(".user-dropdown")) toggleUserMenu(); });
  document.addEventListener("click", e => { if (!el("userRow")?.contains(e.target)) closeUserMenu(); });
  el("themeToggleBtn")?.addEventListener("click", toggleTheme);
  el("topThemeBtn")?.addEventListener("click", toggleTheme);
  el("logoutBtn")?.addEventListener("click", handleLogout);

  el("historyList")?.addEventListener("click", e => {
    const del  = e.target.closest(".hist-del[data-del]");
    const item = e.target.closest(".hist-item[data-id]");
    if (del)  deleteConversation(del.dataset.del, e);
    else if (item) openConversation(item.dataset.id);
  });

  const ta = el("chatInput");
  if (ta) {
    ta.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } });
    ta.addEventListener("input", () => autoGrow(ta));
  }
  el("sendBtn")?.addEventListener("click", handleSend);

  document.querySelectorAll(".suggestion-chips .chip").forEach(c =>
    c.addEventListener("click", () => { if (c.dataset.q) sendMessage(c.dataset.q); })
  );

  el("chatDisplay")?.addEventListener("click", e => {
    const chip = e.target.closest(".chip[data-q]");
    if (chip?.dataset.q) { sendMessage(chip.dataset.q); return; }
    const cta  = e.target.closest("[data-ask]");
    if (cta?.dataset.ask) sendMessage(cta.dataset.ask);
  });

  const fi = el("fileInput"); fi?.addEventListener("change", handleFileSelect);
  ["attachTopBtn", "attachInputBtn", "uploadNudgeBtn", "reportsUploadBtn"].forEach(id =>
    el(id)?.addEventListener("click", () => fi?.click())
  );

  // REVIEWER FIX: Wearable Camera Button Event
  el("syncWearableBtn")?.addEventListener("click", () => {
    closeCockpit(); 
    window.isWearableSync = true;
    fi?.click(); 
  });

  el("updateShieldBtn")?.addEventListener("click", updateShield);
  el("calcBtn")?.addEventListener("click", () => {
    const gw = parseFloat(el("proteinInput")?.value);
    gw ? calcProteinDisplay(gw, true) : toast("Enter a goal weight (80–400 lbs).", "err");
  });
  el("proteinInput")?.addEventListener("keydown", e => { if (e.key === "Enter") el("calcBtn")?.click(); });
  el("noiseSlider")?.addEventListener("input", updateNoiseReadout);
  el("logNoiseBtn")?.addEventListener("click", logNoiseLevel);
  el("refreshAlertsBtn")?.addEventListener("click", loadMarkersData);
  el("refreshMarkersBtn")?.addEventListener("click", loadMarkersData);

  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key === "k") { e.preventDefault(); resetChat(); el("chatInput")?.focus(); }
    if (e.key === "Escape") { closeSidebar(); closeUserMenu(); closeCockpit(); }
  });

  document.addEventListener("dragover", e => e.preventDefault());
  document.addEventListener("drop", e => { e.preventDefault(); Array.from(e.dataTransfer?.files || []).forEach(addFile); });
}

/* ═══ INIT ═══ */
document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  wireEvents();
  updateNoiseReadout();
  initVoice();
  boot();
});