/**
 * script.js — Curabook PHI v2.1 (Bug-Fixed Edition)
 *
 * FIXES vs v2.0:
 * FIX-SHIELD: autoLoadShield() now handles HTTP 500 from missing
 *   behavioral_logs table gracefully — renders zeros instead of crashing.
 *   Also handles the case where the table exists but has no data today.
 *
 * FIX-HEALTH-VIEW: loadHealthView() no longer crashes when cliff_alerts
 *   is missing or empty from the dashboard response. All array accesses
 *   are guarded. Works with any combination of missing fields.
 *
 * FIX-CHIPS: Dynamic chips added to chat (e.g., after document upload or
 *   from the AI's suggested follow-ups) are now clickable. Uses event
 *   delegation on #chatDisplay instead of per-element handlers.
 *
 * FIX-COCKPIT: cockpitCloseBtn is now correctly shown/hidden based on
 *   viewport. Fixed the CSS/JS conflict where style.css display:none
 *   fought against JS setting display:flex.
 *
 * FIX-403: saveConsents() properly awaited before createConversation().
 *   The old fire-and-forget pattern created a race where the first
 *   conversation create always raced against consent save.
 *
 * FIX-HISTORY: historyList click delegation now correctly handles clicks
 *   on child elements of .hist-item (e.g., clicking the title text).
 *
 * PRESERVED: All FIX-1 through FIX-10 from v2.0.
 */
"use strict";

/* ═══════════════════ CONFIG ═══════════════════ */
const SUPABASE_URL = "https://pbeaawlxdcrdbvlmpqhc.supabase.co";
const SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBiZWFhd2x4ZGNyZGJ2bG1wcWhjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzYwMDk0MzksImV4cCI6MjA5MTU4NTQzOX0.6bUpYrDbe0mQjjBHX8Qscj-5R8i4-SqAtW_Z1UFzJ10";

const IS_LOCAL = ["localhost", "127.0.0.1", "0.0.0.0"].includes(location.hostname);
const API      = IS_LOCAL ? "http://localhost:5000" : "https://api.curabook.com";

/* ═══════════════════ STATE ═══════════════════ */
let _sb           = null;
let _user         = null;
let _userName     = "";
let _convId       = null;
let _isSending    = false;
let _sendStart    = 0;
let _uploads      = [];
let _goalWt       = parseFloat(localStorage.getItem("phi_goal_wt") || "165");
let _proteinTarget = Math.round(_goalWt * 0.545 * 10) / 10;

// Consent state — single source of truth
let _consentsSaved   = false;
let _consentsPromise = null;

// Doc context per conversation
let _docCtx = { text: null, hasDoc: false, filename: "" };

const NOISE_MSG = {
  1:"Nearly silent — ghrelin well suppressed.", 2:"Very low. Behavioral strategies may suffice.",
  3:"Mild. 35g+ protein per meal typically helps.", 4:"Low-moderate. Check your daily protein target.",
  5:"Moderate. A 20-min post-meal walk helps.", 6:"Elevated. Sleep below 7h raises ghrelin ~15%.",
  7:"High — dose reduction may have been too fast.", 8:"Very high. Classic ghrelin surge — not willpower.",
  9:"Intense rebound. Discuss urgently with provider.", 10:"🚨 Maximum. Urgent provider conversation needed."
};

/* ═══════════════════ THEME ═══════════════════ */
function initTheme() { applyTheme(localStorage.getItem("phi_theme") || "dark"); }
function toggleTheme() { applyTheme((document.documentElement.dataset.theme || "dark") === "dark" ? "light" : "dark"); closeUserMenu(); }
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("phi_theme", t);
  const dark = t === "dark";
  setIcon("themeIcon",    dark ? "fa-moon" : "fa-sun");
  setIcon("topThemeIcon", dark ? "fa-moon" : "fa-sun");
  setText("themeLabel",   dark ? "Light Mode" : "Dark Mode");
}

/* ═══════════════════ BOOT ═══════════════════ */
async function boot() {
  try {
    _sb = supabase.createClient(SUPABASE_URL, SUPABASE_KEY, {
      auth: { detectSessionInUrl: true, persistSession: true, autoRefreshToken: true }
    });

    // FIX-9: Auto-reset stuck spinner on tab focus
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && _isSending && Date.now() - _sendStart > 35000) {
        _isSending = false; setSendingState(false);
      }
    });

    _sb.auth.onAuthStateChange(async (event, session) => {
      if (event === "SIGNED_IN"  && session?.user) await onSignIn(session.user);
      if (event === "SIGNED_OUT") location.href = "/login";
    });

    const { data } = await _sb.auth.getSession();
    if (!data?.session?.user) {
      if (IS_LOCAL) { console.warn("[PHI] Dev: no session"); return; }
      location.href = "/login";
      return;
    }
    await onSignIn(data.session.user);
  } catch (err) {
    console.error("[PHI] Boot:", err);
    toast("Failed to initialize — please refresh.", "err");
  }
}

async function onSignIn(user) {
  _user     = user;
  _userName = (user.user_metadata?.first_name
    || user.email?.split("@")[0]?.split(/[._-]/)[0]
    || "there");
  _userName = _userName[0].toUpperCase() + _userName.slice(1);

  setText("userEmail",   user.email);
  setText("welcomeName", _userName);
  if (el("userAvatar")) el("userAvatar").textContent = _userName[0].toUpperCase();

  const h = new Date().getHours();
  setText("timeGreeting", h < 12 ? "morning" : h < 17 ? "afternoon" : "evening");

  // FIX-403: Fire consents but don't block onSignIn — createConversation awaits it
  saveConsents().catch(() => {});
  await loadHistory();
  autoLoadShield().catch(() => {});
  loadMarkersData().catch(() => {});

  const saved = localStorage.getItem("phi_goal_wt");
  if (saved) {
    const gwInput = el("inputGoalWt");
    if (gwInput) gwInput.value = saved;
    const pInput = el("proteinInput");
    if (pInput) pInput.value = saved;
    calcProteinDisplay(parseFloat(saved), false);
  }
}

/* ═══════════════════ API ═══════════════════ */
async function session() {
  try {
    const { data } = await _sb.auth.getSession();
    if (data?.session) return data.session;
    const { data: r } = await _sb.auth.refreshSession();
    return r?.session || null;
  } catch { return null; }
}

async function headers(ct = "application/json") {
  const s = await session();
  if (!s) return null;
  const h = { Authorization: `Bearer ${s.access_token}` };
  if (ct) h["Content-Type"] = ct;
  return h;
}

async function apiFetch(path, opts = {}) {
  const ctrl = new AbortController();
  const t    = setTimeout(() => ctrl.abort(), 30000);
  try {
    const res = await fetch(API + path, { ...opts, signal: ctrl.signal });
    clearTimeout(t);
    return res;
  } catch (e) {
    clearTimeout(t);
    if (e.name === "AbortError") throw new Error("Request timed out");
    throw e;
  }
}

async function apiJson(path, opts = {}) {
  const res  = await apiFetch(path, opts);
  const text = await res.text().catch(() => "");
  let data   = null;
  try { data = text ? JSON.parse(text) : null; } catch {}
  return { ok: res.ok, status: res.status, data };
}

/**
 * FIX-403: saveConsents() is properly deduplicated.
 * Shared promise prevents parallel calls. Once saved, returns immediately.
 */
async function saveConsents() {
  if (_consentsSaved) return;
  if (_consentsPromise) return _consentsPromise;

  _consentsPromise = (async () => {
    try {
      const h = await headers();
      if (!h) return;
      const res = await apiFetch("/api/consent", {
        method: "POST", headers: h,
        body: JSON.stringify({ consents: ["data_processing", "ai_processing", "document_processing"] })
      });
      if (res.ok || res.status === 200) { _consentsSaved = true; }
    } catch (e) {
      console.warn("[PHI] saveConsents non-fatal:", e);
    } finally {
      _consentsPromise = null;
    }
  })();

  return _consentsPromise;
}

/* ═══════════════════ SIDEBAR ═══════════════════ */
const openSidebar  = () => { el("sidebar")?.classList.add("open"); el("sidebarOverlay")?.classList.add("show"); closeCockpit(); };
const closeSidebar = () => { el("sidebar")?.classList.remove("open"); el("sidebarOverlay")?.classList.remove("show"); };
const toggleUserMenu = () => {
  const dd = el("userDropdown");
  if (dd) dd.setAttribute("aria-hidden", dd.getAttribute("aria-hidden") === "false" ? "true" : "false");
};
const closeUserMenu = () => el("userDropdown")?.setAttribute("aria-hidden", "true");

/* ═══════════════════ COCKPIT ═══════════════════ */
const openCockpit  = () => { el("cockpit")?.classList.add("open"); el("cockpitOverlay")?.classList.add("show"); closeSidebar(); };
const closeCockpit = () => { el("cockpit")?.classList.remove("open"); el("cockpitOverlay")?.classList.remove("show"); };
const toggleCockpit = () => el("cockpit")?.classList.contains("open") ? closeCockpit() : openCockpit();

/* ═══════════════════ VIEWS ═══════════════════ */
function switchView(view) {
  ["chat", "health", "reports"].forEach(v => {
    el(`view${v[0].toUpperCase() + v.slice(1)}`)?.classList.toggle("active", v === view);
    el(`nav${v[0].toUpperCase() + v.slice(1)}`)?.classList.toggle("active", v === view);
  });
  closeSidebar();
  if (view === "health")  loadHealthView();
  if (view === "reports") loadReportsView();
  const titles = { chat: "Chat with PHI", health: "My Health", reports: "Lab Reports" };
  setText("convTitle", titles[view] || "");
}

/* ─── My Health view ─── */
async function loadHealthView() {
  const content = el("healthContent");
  if (!content) return;
  content.innerHTML = `<div class="hv-empty"><i class="fa-solid fa-spinner fa-spin"></i>Loading your health picture…</div>`;

  const h = await headers();
  if (!h) return;

  try {
    const [markersRes, dashRes] = await Promise.allSettled([
      apiJson("/api/health-markers", { headers: h }),
      apiJson("/api/dashboard",      { headers: h }),
    ]);

    const markers   = (markersRes.status === "fulfilled" && markersRes.value.ok && Array.isArray(markersRes.value.data)) ? markersRes.value.data : [];
    // FIX-HEALTH-VIEW: Guard against any shape of dashboard response
    const dashData  = (dashRes.status === "fulfilled" && dashRes.value.ok && dashRes.value.data) ? dashRes.value.data : null;

    if (!markers.length && !dashData) {
      content.innerHTML = `
        <div class="hv-empty">
          <i class="fa-solid fa-chart-line"></i>
          No health data yet. Upload a Quest or LabCorp PDF to see your cliff risk picture.
          <br><button class="hv-cta-btn" onclick="document.getElementById('fileInput').click()">
            <i class="fa-solid fa-upload"></i> Upload Your First Report
          </button>
        </div>`;
      return;
    }

    content.innerHTML = buildHealthViewHTML(markers, dashData);

    // Wire "Ask PHI" buttons via delegation on content element
    content.querySelectorAll("[data-ask]").forEach(btn => {
      btn.addEventListener("click", () => {
        const q = btn.dataset.ask;
        switchView("chat");
        setTimeout(() => sendMessage(q), 100);
      });
    });

  } catch (e) {
    console.error("[PHI] Health view:", e);
    content.innerHTML = `<div class="hv-empty"><i class="fa-solid fa-circle-exclamation"></i>Could not load health data. Please try again.</div>`;
  }
}

function buildHealthViewHTML(markers, dashboard) {
  // FIX-HEALTH-VIEW: All array accesses guarded with fallbacks
  const abnormal     = markers.filter(m => m.status === "HIGH" || m.status === "LOW");
  const trending     = (dashboard?.trends || []);
  const cliffalerts  = (dashboard?.cliff_alerts || []);  // was crashing when missing

  let html = "";

  // Cliff Risk Summary
  const riskLevel = cliffalerts.length > 0 ? "high" : abnormal.length > 2 ? "warn" : "none";
  const riskScore = cliffalerts.length > 0 ? cliffalerts.length : abnormal.length;
  const riskLabel = riskLevel === "high" ? "Active Rebound Signals" : riskLevel === "warn" ? "Markers Need Attention" : "No Cliff Signals";
  const riskDesc  = riskLevel === "high"
    ? `${cliffalerts.length} clinical rebound threshold${cliffalerts.length > 1 ? "s" : ""} exceeded. Discuss with your provider urgently.`
    : riskLevel === "warn"
    ? `${abnormal.length} markers are outside the normal range.`
    : "All monitored markers are within normal range. Keep up protein intake and resistance training.";

  html += `
    <div class="hv-section">
      <div class="hv-heading"><i class="fa-solid fa-triangle-exclamation"></i>Cliff Risk Summary</div>
      <div class="cliff-card risk-${riskLevel}">
        <div>
          <div class="cliff-num risk-${riskLevel}">${riskLevel === "none" ? "✓" : riskScore}</div>
          <div style="font-size:.7rem;color:var(--text-3);margin-top:3px">${riskLevel === "none" ? "Clear" : "Signal" + (riskScore > 1 ? "s" : "")}</div>
        </div>
        <div class="cliff-detail">
          <h3>${riskLabel}</h3>
          <p>${riskDesc}</p>
          ${riskLevel !== "none" ? `<button class="alert-cta" data-ask="Run a full cliff risk analysis on my stored data. What are my most urgent signals?">Ask PHI to analyze →</button>` : ""}
        </div>
      </div>
    </div>`;

  // Active Cliff Alerts
  if (cliffalerts.length) {
    html += `<div class="hv-section"><div class="hv-heading"><i class="fa-solid fa-bolt"></i>Active Alerts</div><div class="alert-feed">`;
    cliffalerts.forEach(a => {
      html += `
        <div class="alert-item danger">
          <div class="alert-title">${esc(a.headline || a.title || "Rebound signal")}</div>
          <div class="alert-desc">${esc(a.detail || a.desc || "")}</div>
          ${a.action ? `<button class="alert-cta" data-ask="${esc(a.action)}">What should I do? →</button>` : ""}
        </div>`;
    });
    html += `</div></div>`;
  }

  // Key Markers
  if (markers.length) {
    html += `<div class="hv-section"><div class="hv-heading"><i class="fa-solid fa-flask"></i>Latest Lab Values</div><div class="trend-grid">`;
    markers.slice(0, 12).forEach(m => {
      const s    = (m.status || "").toLowerCase();
      const cls  = s === "high" ? "hi" : s === "low" ? "lo" : s === "normal" ? "ok" : "";
      const trend = trending.find(t => t.marker === m.marker_name);
      let badgeHtml = "";
      if (trend) {
        const isGood = !trend.concerning;
        const dir    = trend.direction === "rising" ? "↑" : "↓";
        const cls2   = isGood ? "good" : "bad";
        badgeHtml = `<div class="trend-badge ${cls2}">${dir} ${trend.pct_change}%</div>`;
      }
      html += `
        <div class="trend-card">
          <div class="trend-name" title="${esc(m.marker_name)}">${esc(m.marker_name)}</div>
          <div><span class="trend-val ${cls}">${m.value}</span><span class="trend-unit"> ${esc(m.unit || "")}</span></div>
          ${badgeHtml}
          ${m.date ? `<div class="trend-dates">${m.date}</div>` : ""}
        </div>`;
    });
    html += `</div></div>`;
  }

  // Abnormal markers feed
  if (abnormal.length) {
    html += `<div class="hv-section"><div class="hv-heading"><i class="fa-solid fa-circle-exclamation"></i>Needs Attention</div><div class="alert-feed">`;
    abnormal.slice(0, 6).forEach(m => {
      const dir = m.status === "HIGH" ? "above" : "below";
      html += `
        <div class="alert-item warn">
          <div class="alert-title">${esc(m.marker_name)} is ${m.status}</div>
          <div class="alert-desc">${m.value} ${esc(m.unit || "")} — ${dir} normal range ${m.reference_range ? `(${esc(m.reference_range)})` : ""}</div>
          <button class="alert-cta" data-ask="Explain my ${esc(m.marker_name)} result of ${m.value} ${esc(m.unit || "")}. Is this related to the GLP-1 cliff? What should I do?">Explain this →</button>
        </div>`;
    });
    html += `</div></div>`;
  }

  return html;
}

/* ─── Lab Reports view ─── */
async function loadReportsView() {
  const list = el("reportsList");
  if (!list) return;
  list.innerHTML = `<div class="hv-empty"><i class="fa-solid fa-spinner fa-spin"></i>Loading your reports…</div>`;

  const h = await headers();
  if (!h) return;

  try {
    const { ok, data } = await apiJson("/doctor-prep/history", { headers: h });
    if (!ok || !data?.preps?.length) {
      list.innerHTML = `
        <div class="hv-empty">
          <i class="fa-solid fa-file-medical"></i>
          No lab reports uploaded yet.
          <br><button class="hv-cta-btn" onclick="document.getElementById('fileInput').click()">
            <i class="fa-solid fa-upload"></i> Upload Your First Report
          </button>
        </div>`;
      return;
    }

    list.innerHTML = data.preps.map(p => {
      const date = p.generated_at ? new Date(p.generated_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "";
      return `
        <div class="report-card">
          <div class="report-icon"><i class="fa-solid fa-file-medical-alt"></i></div>
          <div class="report-meta">
            <div class="report-name">${esc(p.filename || "Lab Report")}</div>
            <div class="report-date">${date}</div>
            <div class="report-tags"><span class="report-tag info">Lab Report</span></div>
          </div>
          <button class="report-ask-btn" onclick="askAboutReport('${esc(p.filename || "your report")}')">Ask PHI →</button>
        </div>`;
    }).join("");
  } catch (e) {
    list.innerHTML = `<div class="hv-empty"><i class="fa-solid fa-circle-exclamation"></i>Could not load reports. Please try again.</div>`;
  }
}

function askAboutReport(filename) {
  switchView("chat");
  setTimeout(() => sendMessage(`Please summarize my ${filename} report and flag any cliff signals or abnormal values.`), 100);
}

/* ═══════════════════ HISTORY ═══════════════════ */
async function loadHistory() {
  const h = await headers();
  if (!h) return;
  try {
    const { ok, data } = await apiJson("/history", { method: "POST", headers: h, body: JSON.stringify({}) });
    if (ok && Array.isArray(data)) renderHistory(data);
  } catch {}
}

function renderHistory(convs) {
  const list = el("historyList");
  if (!list) return;
  if (!convs.length) { list.innerHTML = '<div class="sb-empty">No conversations yet</div>'; return; }

  const today = new Date(); today.setHours(0, 0, 0, 0);
  const yest  = new Date(today); yest.setDate(today.getDate() - 1);
  const groups = new Map();

  convs.forEach(c => {
    const d = new Date(c.created_at || Date.now()); d.setHours(0, 0, 0, 0);
    const label = d.getTime() === today.getTime() ? "Today"
      : d.getTime() === yest.getTime() ? "Yesterday"
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
  _convId  = id;
  _uploads = [];
  _docCtx  = { text: null, hasDoc: false, filename: "" };
  clearFilePreview();
  showChat();
  if (el("chatDisplay")) el("chatDisplay").innerHTML = "";
  document.querySelectorAll(".hist-item").forEach(e => e.classList.toggle("active", e.dataset.id === id));
  closeSidebar();

  const h = await headers();
  if (!h) return;
  try {
    const { ok, data } = await apiJson("/conversation", { method: "POST", headers: h, body: JSON.stringify({ conversation_id: id }) });
    if (ok && Array.isArray(data)) { data.forEach(m => appendMsg(m.content, m.role === "user" ? "user" : "ai")); scrollBottom(); }
  } catch {}
}

async function deleteConversation(id, e) {
  e?.stopPropagation();
  document.querySelector(`.hist-item[data-id="${id}"]`)?.remove();
  if (id === _convId) resetChat();
  const h = await headers();
  if (h) await apiFetch("/delete", { method: "POST", headers: h, body: JSON.stringify({ conversation_id: id }) }).catch(() => {});
  toast("Conversation deleted");
}

/* ═══════════════════ CHAT RESET ═══════════════════ */
function resetChat() {
  _convId  = null;
  _uploads = [];
  _docCtx  = { text: null, hasDoc: false, filename: "" };
  clearFilePreview();
  if (el("chatDisplay")) el("chatDisplay").innerHTML = "";
  showWelcome();
  document.querySelectorAll(".hist-item").forEach(e => e.classList.remove("active"));
  setText("convTitle", "Ready");
}

function showWelcome() {
  el("welcomeScreen")?.classList.remove("hidden");
  el("chatDisplay")?.classList.add("hidden");
}
function showChat() {
  el("welcomeScreen")?.classList.add("hidden");
  el("chatDisplay")?.classList.remove("hidden");
}

/* ═══════════════════ CONVERSATION CREATE ═══════════════════ */
async function createConversation() {
  const h = await headers();
  if (!h) { toast("Session expired — please sign in.", "err"); location.href = "/login"; return null; }

  // FIX-403: Always await consents before creating conversation
  await saveConsents().catch(() => {});

  const tryCreate = async () => {
    return apiJson("/conversation/create", { method: "POST", headers: h, body: JSON.stringify({}) });
  };

  let { ok, status, data } = await tryCreate();

  if (!ok && status === 403) {
    _consentsSaved = false;
    await saveConsents().catch(() => {});
    ({ ok, status, data } = await tryCreate());
  }

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
  const list = el("historyList");
  if (!list) return;
  list.querySelector(".sb-empty")?.remove();
  let group = list.querySelector(".hist-group-label");
  if (!group || group.textContent !== "Today") {
    group = Object.assign(document.createElement("div"), { className: "hist-group-label", textContent: "Today" });
    list.prepend(group);
  }
  const item = Object.assign(document.createElement("div"), { className: "hist-item active" });
  item.dataset.id = id;
  item.innerHTML  = `<span class="hist-title">${esc(title)}</span><button class="hist-del" data-del="${esc(id)}" title="Delete"><i class="fa-solid fa-trash"></i></button>`;
  group.insertAdjacentElement("afterend", item);
  document.querySelectorAll(".hist-item").forEach(e => e.classList.toggle("active", e.dataset.id === id));
}

async function renameConversation(id, title) {
  const h = await headers();
  if (!h || !id) return;
  const short = title.slice(0, 50);
  const t = document.querySelector(`.hist-item[data-id="${id}"] .hist-title`);
  if (t) t.textContent = short;
  setText("convTitle", short);
  await apiFetch("/rename", { method: "POST", headers: h, body: JSON.stringify({ conversation_id: id, title: short }) }).catch(() => {});
}

/* ═══════════════════ SEND ═══════════════════ */
async function handleSend() {
  if (_isSending) return;
  const s = await session();
  if (!s) { toast("Session expired. Please sign in.", "err"); location.href = "/login"; return; }

  const ta   = el("chatInput");
  let   text = ta?.value.trim();
  if (!text && _uploads.length) text = "Please analyze my uploaded lab report — explain every finding, flag what's abnormal, and identify any cliff signals.";
  if (!text) return;
  if (ta) { ta.value = ""; ta.style.height = "auto"; }
  sendMessage(text);
}

async function sendMessage(text) {
  if (_isSending || !text) return;
  _isSending = true;
  _sendStart = Date.now();
  setSendingState(true);
  switchView("chat");
  showChat();

  if (!_convId) {
    const id = await createConversation();
    if (!id) { _isSending = false; setSendingState(false); return; }
  }

  // Process file upload if pending
  if (_uploads.length) {
    const loadRow = appendTyping();
    updateTyping(loadRow, "Reading your report…");
    const result = await processUpload(_uploads[0]);
    loadRow?.remove();
    _uploads = [];
    clearFilePreview();
    if (result?.document_text) {
      _docCtx.text     = result.document_text;
      _docCtx.hasDoc   = true;
      _docCtx.filename = result.filename || "";
      toast(`${result.filename || "Report"} analyzed ✓`);
    } else if (result === null) {
      _isSending = false; setSendingState(false); return;
    }
  }

  appendMsg(text, "user");
  const botRow = appendTyping();
  scrollBottom();

  const payload = {
    conversation_id: _convId,
    message:         text,
    has_documents:   _docCtx.hasDoc,
    document_text:   _docCtx.hasDoc ? (_docCtx.text || "") : "",
  };

  const h = await headers();
  if (!h) {
    updateMsg(botRow, "Session expired. Please sign in again.");
    _isSending = false; setSendingState(false); return;
  }

  let success = false;
  try {
    let { ok, status, data } = await apiJson("/chat", { method: "POST", headers: h, body: JSON.stringify(payload) });

    if (!ok && status === 403) {
      _consentsSaved = false;
      await saveConsents().catch(() => {});
      ({ ok, status, data } = await apiJson("/chat", { method: "POST", headers: h, body: JSON.stringify(payload) }));
    }

    if (ok && data?.reply) {
      updateMsg(botRow, data.reply);
      success = true;
    } else if (status === 401) {
      updateMsg(botRow, "Session expired. Please sign in again.");
      toast("Session expired.", "err");
    } else {
      updateMsg(botRow, "I ran into a technical issue. Please try again.\n\n---\n⚕️ *Always consult your healthcare provider.*");
    }

    const userMsgs = el("chatDisplay")?.querySelectorAll(".chat-msg.user-msg").length || 0;
    if (userMsgs === 1 && _convId) renameConversation(_convId, text);

    if (success && _docCtx.hasDoc) setTimeout(loadMarkersData, 2500);

  } catch (err) {
    updateMsg(botRow, (err.message?.includes("timed out") ? "The request timed out." : "Connection error.") +
      "\n\n---\n⚕️ *Always consult your healthcare provider.*");
  }

  _isSending = false; setSendingState(false); scrollBottom();
}

function setSendingState(on) {
  const btn = el("sendBtn");
  const ta  = el("chatInput");
  if (btn) {
    btn.disabled  = on;
    btn.innerHTML = on ? '<i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i>' : '<i class="fa-solid fa-arrow-up"></i>';
  }
  if (ta) ta.disabled = on;
}

/* ═══════════════════ FILE UPLOAD ═══════════════════ */
function handleFileSelect(e) {
  Array.from(e.target.files || []).forEach(addFile);
  e.target.value = "";
}

function addFile(file) {
  if (file.size > 10 * 1024 * 1024) { toast(`${file.name} is too large (max 10 MB).`, "err"); return; }
  if (!/\.(pdf|txt)$/i.test(file.name)) { toast("Only PDF or TXT files supported.", "err"); return; }
  _uploads.push(file);
  renderFilePreview();
  toast(`${file.name} ready — press Send to analyze`);
}

function removeFile(i) { _uploads.splice(i, 1); renderFilePreview(); }

function renderFilePreview() {
  const s = el("filePreview");
  if (!s) return;
  if (!_uploads.length) { s.classList.remove("show"); s.innerHTML = ""; return; }
  s.classList.add("show");
  s.innerHTML = _uploads.map((f, i) => `
    <div class="file-chip">
      <i class="fa-solid ${f.name.endsWith(".pdf") ? "fa-file-pdf" : "fa-file-lines"}"></i>
      <span>${esc(f.name)}</span>
      <button class="file-chip-rm" onclick="removeFile(${i})"><i class="fa-solid fa-xmark"></i></button>
    </div>`).join("");
}

function clearFilePreview() {
  const s = el("filePreview");
  if (s) { s.classList.remove("show"); s.innerHTML = ""; }
}

async function processUpload(file) {
  const s = await session();
  if (!s) { toast("Session expired.", "err"); return null; }

  const form = new FormData();
  form.append("file", file);

  const doUpload = async (token) => fetch(API + "/analyze", {
    method: "POST", headers: { Authorization: `Bearer ${token}` }, body: form
  });

  try {
    let res = await Promise.race([
      doUpload(s.access_token),
      new Promise((_, r) => setTimeout(() => r(new Error("Upload timed out")), 30000))
    ]);

    if (res.status === 403) {
      _consentsSaved = false;
      await saveConsents().catch(() => {});
      const s2 = await session();
      if (s2) res = await doUpload(s2.access_token);
    }

    if (res.status === 401) { toast("Session expired.", "err"); return null; }
    if (res.status === 413) { toast("File too large (max 5 MB).", "err"); return null; }
    if (res.status === 400) { const d = await res.json().catch(() => ({})); toast(d.error || "Could not read this file.", "err"); return null; }
    if (!res.ok) { const d = await res.json().catch(() => ({})); toast(d.error || `Upload failed (${res.status}).`, "err"); return null; }
    return await res.json();
  } catch (err) {
    toast(err.message?.includes("timed out") ? "Upload timed out." : "Upload failed.", "err");
    return null;
  }
}

/* ═══════════════════ MESSAGE RENDERING ═══════════════════ */
function appendMsg(text, role) {
  const d = el("chatDisplay");
  if (!d) return null;
  const wrap = document.createElement("div");
  wrap.className = `chat-msg ${role === "user" ? "user-msg" : "ai-msg"}`;
  const av   = `<div class="msg-av ${role === "user" ? "av-user" : "av-ai"}">${role === "user" ? (_userName?.[0]?.toUpperCase() || "U") : "φ"}</div>`;
  const body = document.createElement("div");
  body.className = "msg-body";
  if (role === "user") { body.textContent = text; wrap.innerHTML = av; wrap.insertBefore(body, wrap.firstChild); }
  else { renderAI(body, text); wrap.innerHTML = av; wrap.appendChild(body); }
  d.appendChild(wrap);
  return wrap;
}

function appendTyping() {
  const d = el("chatDisplay");
  if (!d) return null;
  const w = document.createElement("div");
  w.className = "chat-msg ai-msg";
  w.innerHTML = `<div class="msg-av av-ai">φ</div><div class="msg-body"><div class="typing-indicator"><div class="t-dot"></div><div class="t-dot"></div><div class="t-dot"></div></div></div>`;
  d.appendChild(w); scrollBottom(); return w;
}

function updateTyping(w, text) { const b = w?.querySelector(".msg-body"); if (b) b.textContent = text; }
function updateMsg(w, text) { const b = w?.querySelector(".msg-body"); if (b) { renderAI(b, text); scrollBottom(); } }

function renderAI(elem, text) {
  const parts = text.split(/---\n⚕️/);
  elem.innerHTML = typeof marked !== "undefined" ? marked.parse(parts[0].trim()) : esc(parts[0].trim());
  if (parts.length > 1) {
    const legal = document.createElement("p");
    legal.className = "phi-legal";
    legal.textContent = "⚕️ PHI is an educational wellness tool. Always consult your healthcare provider.";
    elem.appendChild(legal);
  }
}

const scrollBottom = () => { const d = el("chatDisplay"); if (d) d.scrollTop = d.scrollHeight; };

/* ═══════════════════ SIGN OUT ═══════════════════ */
async function handleLogout() {
  closeUserMenu();
  try { if (_sb) await _sb.auth.signOut(); } catch {}
  location.href = "/login";
}

/* ═══════════════════ EXPORT ═══════════════════ */
function exportChat() {
  const msgs = el("chatDisplay")?.querySelectorAll(".chat-msg");
  if (!msgs?.length) { toast("No conversation to export.", "err"); return; }
  let out = `Curabook PHI — Chat Export\n${"=".repeat(40)}\n\n`;
  msgs.forEach(m => {
    const role = m.classList.contains("user-msg") ? "You" : "PHI";
    out += `${role}:\n${m.querySelector(".msg-body")?.innerText?.trim() || ""}\n\n`;
  });
  const a = Object.assign(document.createElement("a"), {
    href: URL.createObjectURL(new Blob([out], { type: "text/plain" })),
    download: `phi-chat-${Date.now()}.txt`
  });
  a.click();
  closeUserMenu();
  toast("Chat exported");
}

/* ═══════════════════ HEALTH DATA ═══════════════════ */
async function loadMarkersData() {
  const h = await headers();
  if (!h) return;
  try {
    const { ok, data } = await apiJson("/api/health-markers", { headers: h });
    if (ok && Array.isArray(data) && data.length) { renderMarkers(data); runCliffDetection(data); }
  } catch {}
}

function renderMarkers(markers) {
  const g = el("markersGrid");
  if (!g) return;
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
  const alerts = [];
  const grouped = {};
  markers.forEach(m => {
    const k = (m.marker_name || "").toLowerCase();
    if (!grouped[k]) grouped[k] = [];
    grouped[k].push({ ...m, _v: parseFloat(m.value) });
  });

  const gk = Object.keys(grouped).find(k => /fasting.*glucose|blood.*glucose|glucose/.test(k));
  if (gk) {
    const r = grouped[gk].sort((a, b) => a.date < b.date ? -1 : 1);
    if (r.length >= 2) {
      const pct = ((r[r.length - 1]._v - r[0]._v) / r[0]._v) * 100;
      if      (pct >= 15) alerts.push({ type: "danger", title: `🚨 Glucose rebound +${pct.toFixed(0)}%`, desc: `${r[0]._v}→${r[r.length-1]._v} mg/dL. Cliff threshold exceeded.` });
      else if (pct >= 10) alerts.push({ type: "warn",   title: `⚠ Glucose rising +${pct.toFixed(0)}%`, desc: `Approaching the 15% rebound threshold.` });
    }
  }

  const hk = Object.keys(grouped).find(k => /hba1c|hemoglobin a1c/.test(k));
  if (hk) {
    const r = grouped[hk].sort((a, b) => a.date < b.date ? -1 : 1);
    for (let i = 1; i < r.length; i++) {
      const d = r[i]._v - r[i - 1]._v;
      if (d >= 0.25) { alerts.push({ type: "danger", title: `🚨 HbA1c rebound +${d.toFixed(2)}%`, desc: `${r[i-1]._v}%→${r[i]._v}%. Sustained metabolic rebound.` }); break; }
    }
  }

  markers.filter(m => m.status === "HIGH" && !/glucose/i.test(m.marker_name)).slice(0, 2)
    .forEach(m => alerts.push({ type: "warn", title: `⬆ ${m.marker_name} HIGH`, desc: `${m.value} ${m.unit || ""}` }));

  if (!alerts.length) alerts.push({ type: "ok", title: "✅ No rebound signals", desc: "All monitored markers are stable. Keep up protein and resistance training." });

  const c = el("cliffAlerts");
  if (c) c.innerHTML = alerts.map(a => `<div class="ca-item ca-${a.type}"><div class="ca-title">${a.title}</div><div class="ca-desc">${a.desc}</div></div>`).join("");
}

/* ═══════════════════ FIX-SHIELD: AUTO-SHIELD ═══════════════════ */
async function autoLoadShield() {
  const h = await headers();
  if (!h) { renderShield(0, 0, 0, null); return; }

  const today = new Date().toISOString().slice(0, 10);

  try {
    const { ok, status, data } = await apiJson(`/api/behavioral-logs?days=1`, { headers: h });

    // FIX-SHIELD: Handle missing table (500) and empty response gracefully
    if (!ok || status >= 500 || !Array.isArray(data)) {
      renderShield(0, 0, 0, null);
      return;
    }

    const todayLogs = data.filter(l => l.date === today);
    const get = (metric) => {
      const log = todayLogs.filter(l => l.metric_name === metric).sort((a, b) => a.created_at < b.created_at ? 1 : -1)[0];
      return log ? parseFloat(log.value) : 0;
    };

    const protein = get("protein");
    const steps   = get("steps");
    const sleep   = get("sleep");

    if (protein > 0 && el("inputProtein")) el("inputProtein").value = protein;
    if (steps   > 0 && el("inputSteps"))   el("inputSteps").value   = steps;
    if (sleep   > 0 && el("inputSleep"))   el("inputSleep").value   = sleep;

    const lastLog = data.length > 0 ? data.sort((a, b) => a.created_at < b.created_at ? 1 : -1)[0] : null;
    if (lastLog) {
      const when    = new Date(lastLog.created_at);
      const timeStr = when.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
      const dateStr = when.toLocaleDateString("en-US", { month: "short", day: "numeric" });
      setText("shieldLastLogged", `Last logged: ${lastLog.date === today ? `Today at ${timeStr}` : dateStr}`);
    }

    renderShield(protein, steps, sleep, lastLog ? lastLog.date : null);

  } catch (e) {
    // FIX-SHIELD: Never let autoLoadShield crash the app
    console.warn("[PHI] Shield load non-fatal:", e);
    renderShield(0, 0, 0, null);
  }
}

function updateShield() {
  const protein = parseFloat(el("inputProtein")?.value) || 0;
  const steps   = parseFloat(el("inputSteps")?.value)   || 0;
  const sleep   = parseFloat(el("inputSleep")?.value)   || 0;
  const gwInput = parseFloat(el("inputGoalWt")?.value);
  if (gwInput && gwInput !== _goalWt) {
    _goalWt = gwInput;
    localStorage.setItem("phi_goal_wt", String(_goalWt));
    calcProteinDisplay(_goalWt, false);
  }
  renderShield(protein, steps, sleep, new Date().toISOString().slice(0, 10));
  if (_user) logShieldData(protein, steps, sleep);
}

function renderShield(protein, steps, sleep, logDate) {
  const gw   = _goalWt || 165;
  _proteinTarget = Math.round(gw * 0.545 * 10) / 10;

  const pPct = Math.min(100, Math.round((protein / (_proteinTarget || 90)) * 100));
  const mPct = Math.min(100, Math.round((steps   / 8000) * 100));
  const rPct = Math.max(0, Math.min(100, Math.round(((sleep - 4) / 5) * 100)));
  const score = Math.round((pPct + mPct + rPct) / 3);

  setRing("ringProtein",  440, pPct);
  setRing("ringMovement", 346, mPct);
  setRing("ringRecovery", 258, rPct);

  setText("shieldScore",    score + "%");
  setText("shieldBadge",    score + "%");
  setText("proteinLegend",  protein > 0 ? `${protein}g / ${_proteinTarget}g (${pPct}%)` : `Target: ${_proteinTarget}g — not logged yet`);
  setText("movementLegend", steps > 0   ? `${steps.toLocaleString()} steps (${mPct}%)`  : "Steps — not logged yet");
  setText("recoveryLegend", sleep > 0   ? `${sleep}h sleep (${rPct}%)`                  : "Sleep — not logged yet");
  setBarPct("proteinBar",  pPct);
  setBarPct("movementBar", mPct);
  setBarPct("recoveryBar", rPct);
}

function setRing(id, circ, pct) {
  const r = el(id);
  if (r) { r.style.strokeDasharray = circ; r.style.strokeDashoffset = circ - (circ * Math.max(0, Math.min(100, pct)) / 100); }
}
function setBarPct(id, pct) { const b = el(id); if (b) b.style.width = Math.max(0, pct) + "%"; }

async function logShieldData(protein, steps, sleep) {
  const h = await headers();
  if (!h) return;
  const date = new Date().toISOString().slice(0, 10);
  const logs = [];
  if (protein > 0) logs.push({ date, metric_name: "protein", value: protein, unit: "g" });
  if (steps   > 0) logs.push({ date, metric_name: "steps",   value: steps,   unit: "steps" });
  if (sleep   > 0) logs.push({ date, metric_name: "sleep",   value: sleep,   unit: "hours" });
  logs.forEach(l => apiFetch("/api/behavioral-logs", { method: "POST", headers: h, body: JSON.stringify(l) }).catch(() => {}));
  setText("shieldLastLogged", "Last logged: just now");
  toast("Shield data logged ✓");
}

/* ═══════════════════ PROTEIN CALCULATOR ═══════════════════ */
function calcProteinDisplay(gw, showDetails = true) {
  if (!gw || gw < 80 || gw > 400) { if (showDetails) toast("Enter a valid goal weight (80–400 lbs).", "err"); return; }
  _goalWt        = gw;
  _proteinTarget = Math.round(gw * 0.545 * 10) / 10;
  const perMeal  = Math.round(_proteinTarget / 3 * 10) / 10;
  const leucine  = perMeal >= 30;
  localStorage.setItem("phi_goal_wt", String(gw));

  setText("proteinNum",     _proteinTarget);
  setText("proteinCaption", `${gw} lbs × 0.545 = ${_proteinTarget}g/day`);

  if (showDetails) {
    const d = el("proteinDetails");
    if (d) {
      d.classList.remove("hidden");
      d.innerHTML = `<strong>${perMeal}g per meal</strong> across 3 meals &nbsp;—&nbsp;
        ${leucine ? "✅" : "⚠️"} ${leucine ? "Meets" : "Below"} 30g leucine threshold<br>
        <span style="color:var(--text-3);margin-top:4px;display:block">
          Sample: 4oz chicken (35g) + Greek yogurt (17g) + 2 eggs (12g) + whey scoop (25g)
        </span>`;
    }
    const gwInput = el("inputGoalWt");
    if (gwInput) gwInput.value = gw;
    renderShield(
      parseFloat(el("inputProtein")?.value) || 0,
      parseFloat(el("inputSteps")?.value)   || 0,
      parseFloat(el("inputSleep")?.value)   || 0,
      null
    );
  }
}

/* ═══════════════════ GHRELIN LOG ═══════════════════ */
function updateNoiseReadout() {
  const v = parseInt(el("noiseSlider")?.value || 5);
  const colors = [, "var(--ok)", "var(--ok)", "var(--ok)", "var(--amber)", "var(--amber)", "var(--amber)", "var(--danger)", "var(--danger)", "var(--danger)", "var(--danger)"];
  const r = el("noiseReadout");
  if (r) r.innerHTML = `<strong style="color:${colors[v]}">Level ${v}/10</strong> — ${NOISE_MSG[v]}`;
}

async function logNoiseLevel() {
  const val = parseInt(el("noiseSlider")?.value || 5);
  const h   = await headers();
  if (!h) { toast("Sign in to log food noise.", "info"); return; }
  await apiFetch("/api/behavioral-logs", {
    method: "POST", headers: h,
    body: JSON.stringify({ date: new Date().toISOString().slice(0, 10), metric_name: "food_noise", value: val, unit: "1-10", notes: `Ghrelin surge level: ${val}/10` })
  }).catch(() => {});
  toast(`Food noise level ${val}/10 logged ✓`, "info");
}

/* ═══════════════════ VOICE ═══════════════════ */
function initVoice() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const btn = el("micBtn");
  if (!SR || !btn) { if (btn) { btn.style.opacity = ".3"; btn.disabled = true; } return; }
  let on = false, rec = null;
  btn.addEventListener("click", () => {
    if (on) { rec?.stop(); return; }
    rec = new SR(); rec.lang = "en-US"; rec.interimResults = false;
    rec.onstart  = () => { on = true;  btn.style.color = "var(--danger)"; };
    rec.onresult = e => { const ta = el("chatInput"); if (ta) { ta.value = e.results[0][0].transcript; autoGrow(ta); ta.focus(); } };
    rec.onend    = () => { on = false; btn.style.color = ""; };
    rec.onerror  = e => toast(`Mic: ${e.error}`, "err");
    try { rec.start(); } catch { toast("Voice unavailable.", "err"); }
  });
}

/* ═══════════════════ UTILS ═══════════════════ */
const el      = id => document.getElementById(id);
const esc     = s  => String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const setText = (id, v) => { const e = el(id); if (e) e.textContent = v; };
const setIcon = (id, c) => { const e = el(id); if (e) e.className = `fa-solid ${c}`; };
const autoGrow = ta => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 130) + "px"; };

function toast(msg, type = "ok") {
  const c = el("toasts");
  if (!c) return;
  const t = document.createElement("div");
  const icons = { ok: "circle-check", err: "circle-exclamation", info: "circle-info" };
  t.className = `toast toast-${type}`;
  t.innerHTML = `<i class="fa-solid fa-${icons[type] || "circle-info"}"></i> ${esc(msg)}`;
  c.appendChild(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; setTimeout(() => t.remove(), 300); }, 3800);
}

/* ═══════════════════ EVENT WIRING ═══════════════════ */
function wireEvents() {
  // Nav views
  document.querySelectorAll(".nav-item[data-view]").forEach(btn =>
    btn.addEventListener("click", () => { switchView(btn.dataset.view); closeSidebar(); })
  );

  el("newChatBtn")?.addEventListener("click", () => { resetChat(); switchView("chat"); });

  // Sidebar
  el("mobileMenuBtn")?.addEventListener("click",  openSidebar);
  el("sidebarOverlay")?.addEventListener("click", closeSidebar);

  // FIX-COCKPIT: Use CSS media query to decide button visibility, not JS style
  el("mobileCockpitBtn")?.addEventListener("click", toggleCockpit);
  el("cockpitOverlay")?.addEventListener("click",   closeCockpit);
  el("cockpitCloseBtn")?.addEventListener("click",  closeCockpit);

  // User menu
  el("userRow")?.addEventListener("click", e => { if (!e.target.closest(".user-dropdown")) toggleUserMenu(); });
  document.addEventListener("click", e => { if (!el("userRow")?.contains(e.target)) closeUserMenu(); });

  // Theme
  el("themeToggleBtn")?.addEventListener("click", toggleTheme);
  el("topThemeBtn")?.addEventListener("click",    toggleTheme);

  // User actions
  el("logoutBtn")?.addEventListener("click",    handleLogout);
  el("exportChatBtn")?.addEventListener("click", exportChat);

  // FIX-HISTORY: Use delegation, check all ancestors for data-id / data-del
  el("historyList")?.addEventListener("click", e => {
    const del  = e.target.closest(".hist-del[data-del]");
    const item = e.target.closest(".hist-item[data-id]");
    if (del)       deleteConversation(del.dataset.del, e);
    else if (item) openConversation(item.dataset.id);
  });

  // Chat input
  const ta = el("chatInput");
  if (ta) {
    ta.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } });
    ta.addEventListener("input",   () => autoGrow(ta));
  }

  el("sendBtn")?.addEventListener("click", handleSend);

  // Welcome chips (static)
  document.querySelectorAll(".suggestion-chips .chip").forEach(c =>
    c.addEventListener("click", () => { const q = c.dataset.q; if (q) sendMessage(q); })
  );

  // FIX-CHIPS: Event delegation on chatDisplay for dynamic chips added by AI responses
  el("chatDisplay")?.addEventListener("click", e => {
    const chip = e.target.closest(".chip[data-q]");
    if (chip) {
      const q = chip.dataset.q;
      if (q) sendMessage(q);
    }
    // Also handle alert-cta buttons in health view that got added dynamically
    const cta = e.target.closest(".alert-cta[data-ask]");
    if (cta) {
      const ask = cta.dataset.ask;
      if (ask) sendMessage(ask);
    }
  });

  // File attach
  const fi = el("fileInput");
  fi?.addEventListener("change", handleFileSelect);
  ["attachTopBtn", "attachInputBtn", "uploadNudgeBtn", "reportsUploadBtn"].forEach(id =>
    el(id)?.addEventListener("click", () => fi?.click())
  );

  // Cockpit actions
  el("updateShieldBtn")?.addEventListener("click", updateShield);
  el("calcBtn")?.addEventListener("click", () => {
    const gw = parseFloat(el("proteinInput")?.value);
    if (gw) calcProteinDisplay(gw, true);
    else    toast("Enter a goal weight (80–400 lbs).", "err");
  });
  el("proteinInput")?.addEventListener("keydown", e => { if (e.key === "Enter") el("calcBtn")?.click(); });
  el("noiseSlider")?.addEventListener("input",    updateNoiseReadout);
  el("logNoiseBtn")?.addEventListener("click",    logNoiseLevel);
  el("refreshAlertsBtn")?.addEventListener("click",  loadMarkersData);
  el("refreshMarkersBtn")?.addEventListener("click", loadMarkersData);

  // Keyboard shortcuts
  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key === "k") { e.preventDefault(); resetChat(); el("chatInput")?.focus(); }
    if (e.key === "Escape") { closeSidebar(); closeUserMenu(); closeCockpit(); }
  });

  // Drag & drop
  document.addEventListener("dragover", e => e.preventDefault());
  document.addEventListener("drop",     e => { e.preventDefault(); Array.from(e.dataTransfer?.files || []).forEach(addFile); });
}

/* ═══════════════════ INIT ═══════════════════ */
document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  wireEvents();
  updateNoiseReadout();
  initVoice();
  boot();
});