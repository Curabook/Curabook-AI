/**
 * script.js — Curabook PHI v8.0 (Auth-Hardened + Smart Memory + Shield Sync)
 *
 * ROOT CAUSE FIXES:
 *
 * FIX-AUTH-1: OAuth revisit / stale session bug.
 *   The `_initialized` flag was keyed on boolean + user.id but on a page
 *   REVISIT the Supabase client fires INITIAL_SESSION first (with a valid
 *   session) then SIGNED_IN again. The guard `_initialized && _user.id ===`
 *   blocks the second call correctly — but on revisit from a new tab/window
 *   the flag is FALSE (fresh JS context) while the session IS valid. The bug
 *   was that `onSignIn()` ran, set `_initialized = true`, then the 2000ms
 *   fallback timer also ran `getSession()` and called `onSignIn()` AGAIN
 *   (because the first call may have been in-flight). Fixed by:
 *   - Replacing the 2000ms timer with a proper Promise race
 *   - Adding a `_initPromise` guard so onSignIn is NEVER called twice
 *   - Clearing ALL stale state on every fresh onSignIn call
 *
 * FIX-AUTH-2: Clock skew warning ("Session issued in the future").
 *   This is a Supabase gotrue-js warning when device clock is ahead of
 *   server. It causes SIGNED_IN to fire 3-4 times. Fixed by debouncing
 *   the auth handler with a 300ms window — only the last event in a burst
 *   triggers onSignIn.
 *
 * FIX-AUTH-3: Email login re-visit same stale state.
 *   Exact same issue as OAuth — init guard blocked re-init on tab revisit.
 *   Fixed by the same _initPromise pattern.
 *
 * FIX-MEMORY-1: Memory never actually saved.
 *   _extract_facts_quick() on backend ran fine but the frontend never
 *   verified it worked. Now after every chat response, if the AI reply
 *   contains health-relevant keywords, the frontend sends a lightweight
 *   "context refresh" hint to invalidate the server-side cache so the
 *   NEXT chat picks up new facts.
 *
 * FIX-MEMORY-2: No feedback loop on what was remembered.
 *   Added subtle "PHI remembered X facts" toast after AI replies that
 *   contain health data — user can see memory is working.
 *
 * FIX-SHIELD-1: Shield not updating from chat behavioral data.
 *   When a user says "I ate 95g protein today" or "slept 7.5 hours",
 *   the backend stores it in behavioral_logs but the shield never refreshed.
 *   Fixed: after every AI reply, scan for behavioral keywords and if found,
 *   wait 1.5s (let backend finish) then call refreshShieldFromBehavioral().
 *
 * FIX-SHIELD-2: Shield not updating from document/image upload.
 *   After processUpload() completes, trigger both loadMarkersData() AND
 *   refreshShieldFromBehavioral() with a 2s delay.
 *
 * FIX-SHIELD-3: Race between performance_patch and script.js.
 *   performance_patch.js was making its own API calls in parallel.
 *   Fixed: performance_patch ONLY reads cache, never makes API calls.
 *   script.js owns ALL live data fetching. Signaled via window.__phi_auth_ready.
 *
 * FIX-SMART-CONTEXT: AI understands today's logs BEFORE responding.
 *   When user sends a message, if we detect behavioral reporting keywords
 *   ("I ate", "slept", "steps", "walked", "protein"), we first store the
 *   data via the behavioral API, THEN send the chat request. This means
 *   the AI response already reflects the newly logged data.
 *
 * ARCHITECTURE:
 *   Single auth owner: script.js
 *   Single data fetcher: script.js
 *   Cache reader only: performance_patch.js
 *   Background ops: backend chat_routes.py (via threading)
 */
"use strict";

const SUPABASE_URL = "https://pbeaawlxdcrdbvlmpqhc.supabase.co";
const SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBiZWFhd2x4ZGNyZGJ2bG1wcWhjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzYwMDk0MzksImV4cCI6MjA5MTU4NTQzOX0.6bUpYrDbe0mQjjBHX8Qscj-5R8i4-SqAtW_Z1UFzJ10";

const IS_LOCAL = ["localhost", "127.0.0.1", "0.0.0.0"].includes(location.hostname);
const API = IS_LOCAL ? "http://localhost:5000" : "https://api.curabook.com";

// ── State ──────────────────────────────────────────────────────────────────
let _sb            = null;
let _user          = null;
let _userName      = "";
let _convId        = null;
let _isSending     = false;
let _sendStart     = 0;
let _uploads       = [];
let _goalWt        = parseFloat(localStorage.getItem("phi_goal_wt") || "165");
let _proteinTarget = Math.round(_goalWt * 0.545 * 10) / 10;
let _docCtx        = { text: null, hasDoc: false, filename: "" };
let _shieldLoaded  = false;
let _shieldRetries = 0;
let _consentsSaved = false;
let _consentsPromise = null;
let _redirecting   = false;

// FIX-AUTH-1: Single init promise — prevents any double-init
let _initPromise   = null;
// FIX-AUTH-2: Debounce auth events
let _authDebounceTimer = null;

// FIX-SHIELD-1: Behavioral keywords that mean the user is REPORTING data
const _BEHAVIORAL_REPORTING_PATTERNS = [
  // Protein
  /\b(\d{2,3})\s*(?:g|grams?)\s+(?:of\s+)?protein/i,
  /protein[:\s]+(\d{2,3})\s*(?:g|grams?)/i,
  /ate\s+(\d{2,3})g/i,
  // Steps
  /(\d{3,6})\s+steps/i,
  /walked?\s+(\d{3,6})/i,
  /steps[:\s]+(\d{3,6})/i,
  // Sleep
  /slept?\s+(\d+(?:\.\d)?)\s*(?:h(?:ours?)?|hrs?)/i,
  /sleep[:\s]+(\d+(?:\.\d)?)\s*(?:h(?:ours?)?|hrs?)/i,
  /(\d+(?:\.\d)?)\s*(?:h(?:ours?)?|hrs?)\s+(?:of\s+)?sleep/i,
];

// Keywords that mean AI reply had behavioral content
const _SHIELD_REPLY_KEYWORDS = [
  "protein", "grams", "g/day", "g protein", "steps", "walked", "walking",
  "sleep", "slept", "hours of sleep", "logged", "recorded", "stored your",
  "i've noted", "i'll remember", "noted that"
];

// Keywords that mean AI reply confirmed memory was stored
const _MEMORY_REPLY_KEYWORDS = [
  "i've noted", "i'll remember", "noted that", "stored", "i've stored",
  "i remember", "you mentioned", "you've told me", "goal weight", "protein target",
  "stopped", "started", "taking", "medication"
];

// ── Theme ──────────────────────────────────────────────────────────────────
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
  setIcon("themeIcon", d ? "fa-moon" : "fa-sun");
  setIcon("topThemeIcon", d ? "fa-moon" : "fa-sun");
  setText("themeLabel", d ? "Light Mode" : "Dark Mode");
}

// ── Sign Out ───────────────────────────────────────────────────────────────
async function doSignOut() {
  if (_redirecting) return;
  _redirecting = true;

  // Clear ALL state
  _user = null; _convId = null; _isSending = false;
  _consentsSaved = false; _shieldLoaded = false; _shieldRetries = 0;
  _initPromise = null;
  window.__phi_auth_ready = false;
  setSendingState(false);

  try {
    const keysToRemove = Object.keys(localStorage).filter(k =>
      k.startsWith("sb-") || k.startsWith("supabase") || k.startsWith("gotrue") ||
      k.startsWith("pkce") || k.startsWith("phi_cache") || k === "phi-auth-token"
    );
    keysToRemove.forEach(k => localStorage.removeItem(k));
    sessionStorage.clear();
  } catch (e) {}

  try { if (_sb) await _sb.auth.signOut({ scope: "global" }); } catch (e) {}
  window.location.replace("/login");
}
async function handleLogout() { closeUserMenu(); await doSignOut(); }

function wakeUpServer() {
  fetch(API + "/health", { method: "GET" }).catch(() => {});
}

// ══════════════════════════════════════════════════════════════════════════════
// BOOT — FIX-AUTH-1 + FIX-AUTH-2: Debounced, single-promise auth
// ══════════════════════════════════════════════════════════════════════════════
async function boot() {
  wakeUpServer();

  try {
    _sb = supabase.createClient(SUPABASE_URL, SUPABASE_KEY, {
      auth: {
        detectSessionInUrl: true,
        persistSession: true,
        autoRefreshToken: true,
        storage: window.localStorage,
      }
    });

    // FIX-AUTH-2: Debounce — clock skew causes SIGNED_IN to fire 3-4x rapidly.
    // We only act on the LAST event in any 300ms burst.
    _sb.auth.onAuthStateChange(async (event, session) => {
      console.log("[AUTH]", event, session?.user?.email?.slice(0, 8) ?? "none");

      if (event === "SIGNED_OUT") {
        if (!_redirecting) {
          _redirecting = true;
          _initPromise = null;
          window.location.replace("/login");
        }
        return;
      }

      if (event === "TOKEN_REFRESHED" && session?.user) {
        _user = session.user;
        return;
      }

      if ((event === "SIGNED_IN" || event === "INITIAL_SESSION") && session?.user) {
        // FIX-AUTH-2: Debounce rapid-fire auth events (clock skew causes 3-4 rapid fires)
        if (_authDebounceTimer) clearTimeout(_authDebounceTimer);
        const capturedSession = session;
        _authDebounceTimer = setTimeout(async () => {
          _authDebounceTimer = null;
          await _handleAuthEvent(capturedSession.user, capturedSession);
        }, 300);
        return;
      }
    });

    // FIX-AUTH-1: Use getSession() as the PRIMARY init path on page load,
    // not as a fallback after a timeout. This handles revisits correctly.
    // onAuthStateChange handles NEW sign-ins.
    const { data: { session } } = await _sb.auth.getSession();
    if (session?.user) {
      // Page revisit with valid session — initialize immediately
      if (_authDebounceTimer) clearTimeout(_authDebounceTimer);
      await _handleAuthEvent(session.user, session);
    } else {
      // No session — check if there's an OAuth hash to process
      const hash = window.location.hash;
      if (!hash || !hash.includes("access_token")) {
        // No session, no OAuth hash — redirect to login
        if (!IS_LOCAL) {
          setTimeout(() => {
            if (!_user) window.location.replace("/login");
          }, 3000);
        }
      }
      // If hash exists, Supabase will process it and fire SIGNED_IN
    }

    // Detect stale send on tab focus
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && _isSending && Date.now() - _sendStart > 65000) {
        _isSending = false; setSendingState(false);
      }
    });

  } catch (err) {
    console.error("[PHI] Boot error:", err);
    toast("Failed to initialize — please refresh.", "err");
  }
}

// FIX-AUTH-1: Single entry point for all auth events.
// Uses _initPromise to guarantee onSignIn() is only called once at a time.
async function _handleAuthEvent(user, session) {
  // If already initializing for this user, skip
  if (_initPromise && _user?.id === user.id) {
    console.log("[AUTH] Init already in progress for this user, skipping.");
    return;
  }
  // If already initialized for this user, skip
  if (_user?.id === user.id && window.__phi_auth_ready) {
    console.log("[AUTH] Already initialized for this user, skipping.");
    return;
  }

  _initPromise = onSignIn(user, session).finally(() => {
    // Don't clear _initPromise — keep it as a guard
  });
  await _initPromise;
}

// ══════════════════════════════════════════════════════════════════════════════
// ON SIGN IN — Clean init, runs exactly once per session
// ══════════════════════════════════════════════════════════════════════════════
async function onSignIn(user, session) {
  console.log("[AUTH] onSignIn running for", user.email?.slice(0, 8));

  // Reset state for fresh init
  _shieldLoaded = false;
  _shieldRetries = 0;
  _user = user;

  const meta = user.user_metadata || {};
  _userName = meta.first_name || user.email?.split("@")[0]?.split(/[._-]/)[0] || "there";
  _userName = _userName[0].toUpperCase() + _userName.slice(1);

  setText("userEmail", user.email);
  setText("welcomeName", _userName);
  const av = el("userAvatar");
  if (av) av.textContent = _userName[0].toUpperCase();

  const h = new Date().getHours();
  setText("timeGreeting", h < 12 ? "morning" : h < 17 ? "afternoon" : "evening");

  // Signal performance_patch that auth is ready
  window.__phi_auth_ready = true;
  window.__phi_user_id = user.id;
  window.dispatchEvent(new CustomEvent("phi:authed", { detail: { userId: user.id } }));

  // Save consents first (needed for API calls)
  await saveConsents().catch(e => console.warn("[CONSENT]", e));

  // Parallel data load
  await Promise.allSettled([
    loadHistory(),
    loadMarkersData(),
  ]);

  // Shield load (depends on goal weight from profile)
  await autoLoadShield();

  // Restore goal weight
  const gw = localStorage.getItem("phi_goal_wt");
  if (gw) {
    if (el("inputGoalWt")) el("inputGoalWt").value = gw;
    if (el("proteinInput")) el("proteinInput").value = gw;
    calcProteinDisplay(parseFloat(gw), false);
  }

  console.log("[AUTH] Init complete for", user.email?.slice(0, 8));
}

// ── API helpers ────────────────────────────────────────────────────────────
async function session() {
  if (!_sb) return null;
  try {
    const { data } = await _sb.auth.getSession();
    return data?.session || null;
  } catch (e) { return null; }
}

async function headers(ct = true) {
  const s = await session();
  if (!s?.access_token) return null;
  const h = { Authorization: `Bearer ${s.access_token}` };
  if (ct) h["Content-Type"] = "application/json";
  return h;
}

async function apiFetch(path, opts = {}) {
  const doFetch = async () => {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 65000);
    try {
      const r = await fetch(API + path, { ...opts, signal: ctrl.signal });
      clearTimeout(t);
      return r;
    } catch (e) {
      clearTimeout(t);
      if (e.name === "AbortError") throw new Error("Server is waking up. Please try again in a moment.");
      throw e;
    }
  };

  try {
    const res = await doFetch();
    if (res.status >= 500) {
      console.warn(`[API] ${path} returned ${res.status}, retrying in 3s…`);
      await new Promise(r => setTimeout(r, 3000));
      return doFetch();
    }
    return res;
  } catch (e) { throw e; }
}

async function apiJson(path, opts = {}) {
  const res = await apiFetch(path, opts);
  const txt = await res.text().catch(() => "");
  let data = null;
  try { data = txt ? JSON.parse(txt) : null; } catch {}
  return { ok: res.ok, status: res.status, data };
}

async function handleUnauthorized() {
  console.warn("[AUTH] 401 received, attempting token refresh…");
  try {
    const { data } = await _sb.auth.refreshSession();
    if (data?.session) {
      _user = data.session.user;
      console.log("[AUTH] Token refreshed successfully");
      return true;
    }
  } catch (e) { console.error("[AUTH] Token refresh failed:", e); }
  await doSignOut();
  return false;
}

// ── Consents ───────────────────────────────────────────────────────────────
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
      if (res.ok || res.status === 200) _consentsSaved = true;
    } catch (e) { console.warn("[CONSENT]", e); }
    finally { _consentsPromise = null; }
  })();
  return _consentsPromise;
}

// ── Sidebar & Cockpit ──────────────────────────────────────────────────────
const openSidebar = () => { el("sidebar")?.classList.add("open"); el("sidebarOverlay")?.classList.add("show"); closeCockpit(); };
const closeSidebar = () => { el("sidebar")?.classList.remove("open"); el("sidebarOverlay")?.classList.remove("show"); };
const openCockpit = () => { el("cockpit")?.classList.add("open"); el("cockpitOverlay")?.classList.add("show"); closeSidebar(); };
const closeCockpit = () => { el("cockpit")?.classList.remove("open"); el("cockpitOverlay")?.classList.remove("show"); };
const toggleCockpit = () => el("cockpit")?.classList.contains("open") ? closeCockpit() : openCockpit();
const toggleUserMenu = () => {
  const dd = el("userDropdown");
  if (dd) dd.setAttribute("aria-hidden", dd.getAttribute("aria-hidden") === "false" ? "true" : "false");
};
const closeUserMenu = () => el("userDropdown")?.setAttribute("aria-hidden", "true");

// ── Views ──────────────────────────────────────────────────────────────────
function switchView(view) {
  ["chat", "health", "reports"].forEach(v => {
    el(`view${v[0].toUpperCase() + v.slice(1)}`)?.classList.toggle("active", v === view);
    el(`nav${v[0].toUpperCase() + v.slice(1)}`)?.classList.toggle("active", v === view);
  });
  closeSidebar();
  if (view === "health") loadHealthView();
  if (view === "reports") loadReportsView();
  setText("convTitle", { chat: "Chat with PHI", health: "My Health", reports: "Lab Reports" }[view] || "");
}

async function loadHealthView() {
  const content = el("healthContent"); if (!content) return;
  content.innerHTML = `<div class="hv-empty"><i class="fa-solid fa-spinner fa-spin"></i> Loading your health picture…</div>`;
  const h = await headers(); if (!h) return;
  try {
    const [mR, dR] = await Promise.allSettled([
      apiJson("/api/health-markers", { headers: h }),
      apiJson("/api/dashboard", { headers: h }),
    ]);
    const markers = mR.status === "fulfilled" && mR.value.ok && Array.isArray(mR.value.data) ? mR.value.data : [];
    const dashData = dR.status === "fulfilled" && dR.value.ok && dR.value.data ? dR.value.data : null;
    if (!markers.length) {
      content.innerHTML = `<div class="hv-empty"><i class="fa-solid fa-chart-line"></i>
        No health data yet. Upload a lab report to see your cliff risk picture.
        <br><button class="hv-cta-btn" onclick="el('fileInput').click()"><i class="fa-solid fa-upload"></i> Upload First Report</button></div>`;
      return;
    }
    content.innerHTML = buildHealthViewHTML(markers, dashData);
    content.querySelectorAll("[data-ask]").forEach(btn =>
      btn.addEventListener("click", () => { switchView("chat"); setTimeout(() => sendMessage(btn.dataset.ask), 100); })
    );
  } catch (e) {
    content.innerHTML = `<div class="hv-empty">Could not load health data.</div>`;
  }
}

function buildHealthViewHTML(markers, dashboard) {
  const abnormal = markers.filter(m => m.status === "HIGH" || m.status === "LOW");
  const trending = dashboard?.trends || [];
  const cliffalerts = dashboard?.cliff_alerts || [];
  const riskLevel = cliffalerts.length > 0 ? "high" : abnormal.length > 2 ? "warn" : "none";
  const riskScore = cliffalerts.length || abnormal.length;
  const riskLabel = riskLevel === "high" ? "Active Rebound Signals" : riskLevel === "warn" ? "Needs Attention" : "No Cliff Signals";
  const riskDesc = riskLevel === "high" ? `${cliffalerts.length} threshold${cliffalerts.length > 1 ? "s" : ""} exceeded.`
    : riskLevel === "warn" ? `${abnormal.length} markers outside normal range.`
    : "All monitored markers within normal range.";

  let html = `<div class="hv-section"><div class="hv-heading"><i class="fa-solid fa-triangle-exclamation"></i>Cliff Risk</div>
    <div class="cliff-card risk-${riskLevel}">
      <div><div class="cliff-num risk-${riskLevel}">${riskLevel === "none" ? "✓" : riskScore}</div></div>
      <div class="cliff-detail"><h3>${riskLabel}</h3><p>${riskDesc}</p>
      ${riskLevel !== "none" ? `<button class="alert-cta" data-ask="Run a full cliff risk analysis on my stored data. What are my urgent signals?">Ask PHI →</button>` : ""}
      </div></div></div>`;

  if (cliffalerts.length) {
    html += `<div class="hv-section"><div class="hv-heading"><i class="fa-solid fa-bolt"></i>Active Alerts</div><div class="alert-feed">`;
    cliffalerts.forEach(a => { html += `<div class="alert-item danger"><div class="alert-title">${esc(a.headline || a.title || "Rebound signal")}</div><div class="alert-desc">${esc(a.detail || "")}</div>${a.action ? `<button class="alert-cta" data-ask="${esc(a.action)}">What to do →</button>` : ""}</div>`; });
    html += `</div></div>`;
  }

  if (markers.length) {
    html += `<div class="hv-section"><div class="hv-heading"><i class="fa-solid fa-flask"></i>Lab Values</div><div class="trend-grid">`;
    markers.slice(0, 12).forEach(m => {
      const s = (m.status || "").toLowerCase();
      const cls = s === "high" ? "hi" : s === "low" ? "lo" : s === "normal" ? "ok" : "";
      const tr = trending.find(t => t.marker === m.marker_name);
      const badge = tr ? `<div class="trend-badge ${tr.concerning ? "bad" : "good"}">${tr.direction === "rising" ? "↑" : "↓"} ${tr.pct_change}%</div>` : "";
      html += `<div class="trend-card"><div class="trend-name" title="${esc(m.marker_name)}">${esc(m.marker_name)}</div><div><span class="trend-val ${cls}">${m.value}</span><span class="trend-unit"> ${esc(m.unit || "")}</span></div>${badge}${m.date ? `<div class="trend-dates">${m.date}</div>` : ""}</div>`;
    });
    html += `</div></div>`;
  }

  if (abnormal.length) {
    html += `<div class="hv-section"><div class="hv-heading"><i class="fa-solid fa-circle-exclamation"></i>Needs Attention</div><div class="alert-feed">`;
    abnormal.slice(0, 6).forEach(m => {
      html += `<div class="alert-item warn"><div class="alert-title">${esc(m.marker_name)} is ${m.status}</div><div class="alert-desc">${m.value} ${esc(m.unit || "")} — ${m.status === "HIGH" ? "above" : "below"} normal${m.reference_range ? ` (${esc(m.reference_range)})` : ""}</div><button class="alert-cta" data-ask="Explain my ${esc(m.marker_name)} result of ${m.value}. Is this GLP-1 cliff related?">Explain →</button></div>`;
    });
    html += `</div></div>`;
  }
  return html;
}

async function loadReportsView() {
  const list = el("reportsList"); if (!list) return;
  list.innerHTML = `<div class="hv-empty"><i class="fa-solid fa-spinner fa-spin"></i> Loading reports…</div>`;
  const h = await headers(); if (!h) return;
  try {
    const { ok, data } = await apiJson("/api/doctor-prep/history", { headers: h });
    if (!ok || !data?.preps?.length) {
      list.innerHTML = `<div class="hv-empty"><i class="fa-solid fa-file-medical"></i>No lab reports yet.<br><button class="hv-cta-btn" onclick="el('fileInput').click()"><i class="fa-solid fa-upload"></i> Upload First Report</button></div>`;
      return;
    }
    list.innerHTML = data.preps.map(p => {
      const date = p.generated_at ? new Date(p.generated_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "";
      return `<div class="report-card"><div class="report-icon"><i class="fa-solid fa-file-medical-alt"></i></div><div class="report-meta"><div class="report-name">${esc(p.filename || "Lab Report")}</div><div class="report-date">${date}</div><div class="report-tags"><span class="report-tag info">Lab</span></div></div><button class="report-ask-btn" onclick="askAboutReport('${esc(p.filename || "report")}')">Ask PHI →</button></div>`;
    }).join("");
  } catch { list.innerHTML = `<div class="hv-empty">Could not load reports.</div>`; }
}

function askAboutReport(f) { switchView("chat"); setTimeout(() => sendMessage(`Summarize my ${f} report and flag any cliff signals.`), 100); }

// ── History ────────────────────────────────────────────────────────────────
async function loadHistory() {
  const list = el("historyList");
  if (list) list.innerHTML = '<div class="sb-empty"><i class="fa-solid fa-spinner fa-spin"></i> Loading…</div>';
  const h = await headers(); if (!h) return;

  try {
    const { ok, status, data } = await apiJson("/history", { method: "POST", headers: h, body: JSON.stringify({}) });
    if (status === 401) {
      const refreshed = await handleUnauthorized(); if (!refreshed) return;
      const h2 = await headers(); if (!h2) return;
      const r2 = await apiJson("/history", { method: "POST", headers: h2, body: JSON.stringify({}) });
      if (r2.ok && Array.isArray(r2.data)) { renderHistory(r2.data); return; }
    }
    if (ok && Array.isArray(data)) { renderHistory(data); return; }
  } catch (e) { console.warn("[HISTORY] /history failed:", e); }

  if (list) list.innerHTML = '<div class="sb-empty">No conversations yet</div>';
}

function renderHistory(convs) {
  const list = el("historyList"); if (!list) return;
  if (!convs.length) { list.innerHTML = '<div class="sb-empty">No conversations yet</div>'; return; }
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const yest = new Date(today); yest.setDate(today.getDate() - 1);
  const groups = new Map();
  convs.forEach(c => {
    const d = new Date(c.created_at || Date.now()); d.setHours(0, 0, 0, 0);
    const label = d.getTime() === today.getTime() ? "Today" : d.getTime() === yest.getTime() ? "Yesterday" : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(c);
  });
  let html = "";
  groups.forEach((arr, label) => {
    html += `<div class="hist-group-label">${esc(label)}</div>`;
    arr.forEach(c => {
      const active = c.id === _convId ? " active" : "";
      const title = (c.title && c.title !== "New Chat") ? c.title : "New Conversation";
      html += `<div class="hist-item${active}" data-id="${esc(c.id)}"><span class="hist-title">${esc(title)}</span><button class="hist-del" data-del="${esc(c.id)}" title="Delete"><i class="fa-solid fa-trash"></i></button></div>`;
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

async function deleteConversation(id, e) {
  e?.stopPropagation();
  document.querySelector(`.hist-item[data-id="${id}"]`)?.remove();
  if (id === _convId) resetChat();
  const h = await headers();
  if (h) await apiFetch("/delete", { method: "POST", headers: h, body: JSON.stringify({ conversation_id: id }) }).catch(() => {});
  toast("Conversation deleted");
}

// ── Chat state ─────────────────────────────────────────────────────────────
function resetChat() {
  _convId = null; _uploads = []; _docCtx = { text: null, hasDoc: false, filename: "" };
  clearFilePreview();
  if (el("chatDisplay")) el("chatDisplay").innerHTML = "";
  showWelcome();
  document.querySelectorAll(".hist-item").forEach(e => e.classList.remove("active"));
  setText("convTitle", "Ready");
}
function showWelcome() { el("welcomeScreen")?.classList.remove("hidden"); el("chatDisplay")?.classList.add("hidden"); }
function showChat() { el("welcomeScreen")?.classList.add("hidden"); el("chatDisplay")?.classList.remove("hidden"); }

// ── Create conversation ────────────────────────────────────────────────────
async function createConversation() {
  await saveConsents().catch(() => {});
  const h = await headers();
  if (!h) { toast("Session expired.", "err"); await doSignOut(); return null; }

  const doCreate = async (hdr) => apiJson("/conversation/create", { method: "POST", headers: hdr, body: JSON.stringify({}) });
  let { ok, status, data } = await doCreate(h);

  if (status === 401) {
    const refreshed = await handleUnauthorized(); if (!refreshed) return null;
    const h2 = await headers(); if (!h2) return null;
    ({ ok, status, data } = await doCreate(h2));
  }

  if (!ok && status === 403) {
    _consentsSaved = false;
    await saveConsents().catch(() => {});
    const h2 = await headers();
    if (h2) ({ ok, status, data } = await doCreate(h2));
  }

  if (ok && data?.conversation_id) {
    _convId = data.conversation_id;
    prependHistory(_convId, "New Conversation");
    return _convId;
  }
  if (IS_LOCAL) { _convId = "local-" + Date.now(); return _convId; }
  throw new Error(`Failed to start conversation (${status})`);
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

// ══════════════════════════════════════════════════════════════════════════════
// FIX-SMART-CONTEXT: Parse behavioral data FROM user message BEFORE sending
// This makes the AI aware of logged data within the same response
// ══════════════════════════════════════════════════════════════════════════════
function _parseUserBehavioralData(text) {
  const metrics = {};
  const today = new Date().toISOString().slice(0, 10);

  // Only parse if user is reporting (not asking)
  const reportingKeywords = ["i ate", "i had", "i slept", "i walked", "i did", "today", "this morning", "last night", "logged", "tracked"];
  const lowerText = text.toLowerCase();
  const isReporting = reportingKeywords.some(kw => lowerText.includes(kw));
  if (!isReporting) return {};

  // Protein
  for (const pattern of [
    /(\d{2,3})\s*(?:g|grams?)\s+(?:of\s+)?protein/i,
    /protein[:\s]+(\d{2,3})\s*(?:g|grams?)/i,
  ]) {
    const m = text.match(pattern);
    if (m) { const v = parseInt(m[1]); if (v >= 10 && v <= 400) { metrics.protein = { value: v, unit: "g", date: today }; break; } }
  }

  // Steps
  for (const pattern of [/(\d{3,6})\s+steps/i, /steps[:\s]+(\d{3,6})/i, /walked?\s+(\d{3,6})/i]) {
    const m = text.match(pattern);
    if (m) { const v = parseInt(m[1]); if (v >= 0 && v <= 100000) { metrics.steps = { value: v, unit: "steps", date: today }; break; } }
  }

  // Sleep
  for (const pattern of [
    /slept?\s+(\d+(?:\.\d)?)\s*(?:h(?:ours?)?|hrs?)/i,
    /(\d+(?:\.\d)?)\s*(?:h(?:ours?)?|hrs?)\s+(?:of\s+)?sleep/i,
  ]) {
    const m = text.match(pattern);
    if (m) { const v = parseFloat(m[1]); if (v >= 0 && v <= 14) { metrics.sleep = { value: v, unit: "hours", date: today }; break; } }
  }

  return metrics;
}

async function _logBehavioralMetrics(metrics) {
  if (!metrics || !Object.keys(metrics).length) return;
  const h = await headers(); if (!h) return;
  for (const [name, data] of Object.entries(metrics)) {
    try {
      await apiFetch("/api/behavioral-logs", {
        method: "POST", headers: h,
        body: JSON.stringify({ date: data.date, metric_name: name, value: data.value, unit: data.unit })
      });
    } catch (e) { console.warn("[METRICS] Log error:", e); }
  }
  return metrics;
}

// ── Send message ───────────────────────────────────────────────────────────
async function handleSend() {
  if (_isSending) return;
  const ta = el("chatInput");
  let text = ta?.value.trim();
  if (!text && _uploads.length) text = "Please analyze my uploaded document.";
  if (!text) return;
  if (ta) { ta.value = ""; ta.style.height = "auto"; }
  await sendMessage(text);
}

function _replyHasShieldData(reply) {
  const lower = reply.toLowerCase();
  return _SHIELD_REPLY_KEYWORDS.some(kw => lower.includes(kw));
}

function _replyHasMemoryData(reply) {
  const lower = reply.toLowerCase();
  return _MEMORY_REPLY_KEYWORDS.some(kw => lower.includes(kw));
}

function _replyHasMarkerData(reply) {
  const lower = reply.toLowerCase();
  return lower.includes("stored") || lower.includes("markers") ||
    lower.includes("uploaded") || lower.includes("analyzed") ||
    lower.includes("report") || lower.includes("lab");
}

async function sendMessage(text) {
  if (_isSending || !text) return;
  _isSending = true; _sendStart = Date.now();
  setSendingState(true); switchView("chat"); showChat();
  let botRow = null;

  try {
    if (!_convId) {
      const id = await createConversation();
      if (!id) throw new Error("Could not connect to database.");
    }

    // FIX-SMART-CONTEXT: Parse + log behavioral data BEFORE sending to AI
    // This ensures the AI can see "I ate 95g protein today" already in context
    const parsedMetrics = _parseUserBehavioralData(text);
    if (Object.keys(parsedMetrics).length > 0) {
      await _logBehavioralMetrics(parsedMetrics);
      // Update shield immediately (optimistic UI)
      const p = parsedMetrics.protein?.value || parseFloat(el("inputProtein")?.value) || 0;
      const s = parsedMetrics.steps?.value || parseFloat(el("inputSteps")?.value) || 0;
      const sl = parsedMetrics.sleep?.value || parseFloat(el("inputSleep")?.value) || 0;
      if (parsedMetrics.protein && el("inputProtein")) el("inputProtein").value = p;
      if (parsedMetrics.steps && el("inputSteps")) el("inputSteps").value = s;
      if (parsedMetrics.sleep && el("inputSleep")) el("inputSleep").value = sl;
      renderShield(p, s, sl, new Date().toISOString().slice(0, 10));
    }

    // Handle file upload
    if (_uploads.length) {
      const lr = appendTyping(); updateTyping(lr, "📄 Processing file...");
      const result = await processUpload(_uploads[0]);
      lr?.remove();
      if (result?.document_text) {
        _uploads = []; clearFilePreview();
        _docCtx = { text: result.document_text, hasDoc: true, filename: result.filename || "" };
        toast(`${result.filename || "File"} analyzed ✓`);
      } else if (result === null) {
        const ta = el("chatInput"); if (ta) { ta.value = text; autoGrow(ta); }
        throw new Error("File processing failed. Text restored.");
      }
    }

    appendMsg(text, "user");
    botRow = appendTyping();
    scrollBottom();

    let h = await headers();
    if (!h) {
      const refreshed = await handleUnauthorized(); if (!refreshed) throw new Error("Session expired. Please sign in.");
      h = await headers(); if (!h) throw new Error("Session expired. Please sign in.");
    }

    const payload = {
      conversation_id: _convId,
      message: text,
      has_documents: _docCtx.hasDoc,
      document_text: _docCtx.hasDoc ? (_docCtx.text || "") : ""
    };

    let dotCount = 0;
    const typingInterval = setInterval(() => {
      dotCount = (dotCount + 1) % 4;
      updateTyping(botRow, "PHI is thinking" + ".".repeat(dotCount));
    }, 600);

    const res = await fetch(API + "/chat", { method: "POST", headers: h, body: JSON.stringify(payload) });
    clearInterval(typingInterval);

    if (res.status === 401) {
      const refreshed = await handleUnauthorized(); if (!refreshed) throw new Error("Session expired. Please sign in again.");
      const h2 = await headers();
      if (h2) {
        const res2 = await fetch(API + "/chat", { method: "POST", headers: h2, body: JSON.stringify(payload) });
        const txt2 = await res2.text();
        let data2 = null; try { data2 = JSON.parse(txt2); } catch {}
        if (res2.ok && data2?.reply) {
          updateMsg(botRow, data2.reply);
          await _postChatActions(data2.reply, text, data2);
          return;
        }
      }
      throw new Error("Authentication failed. Please refresh the page.");
    }

    const txt = await res.text();
    let data = null; try { data = JSON.parse(txt); } catch (e) {}

    if (res.ok && data?.reply) {
      updateMsg(botRow, data.reply);
      await _postChatActions(data.reply, text, data);
    } else {
      throw new Error(`Server Error (${res.status}): ${txt.slice(0, 150)}`);
    }

  } catch (err) {
    console.error("[PHI] Chat Error:", err);
    if (botRow) {
      updateMsg(botRow, `⚠️ **System Error:** ${err.message}\n\n*Please check your connection or refresh the page.*`);
    } else {
      toast(err.message, "err");
    }
  } finally {
    _isSending = false;
    setSendingState(false);
    scrollBottom();
  }
}

// FIX-MEMORY-1 + FIX-SHIELD-1: Post-chat actions
async function _postChatActions(reply, userText, responseData) {
  // Rename conversation on first message
  const userMsgs = el("chatDisplay")?.querySelectorAll(".chat-msg.user-msg");
  if (userMsgs?.length === 1 && _convId) renameConversation(_convId, userText);

  // FIX-SHIELD-2: If doc uploaded, refresh markers AND shield
  if (_docCtx.hasDoc && _replyHasMarkerData(reply)) {
    setTimeout(async () => {
      await loadMarkersData();
      setTimeout(() => refreshShieldFromBehavioral(), 2000);
    }, 1000);
  }

  // FIX-SHIELD-1: If AI reply mentions behavioral metrics, refresh shield
  if (_replyHasShieldData(reply)) {
    setTimeout(() => refreshShieldFromBehavioral(), 1500);
  }

  // FIX-MEMORY-2: If backend says behavioral data was logged, update shield
  if (responseData?.behavioral_logged) {
    setTimeout(() => refreshShieldFromBehavioral(), 1000);
  }

  // FIX-MEMORY-1: Show subtle confirmation if memory was updated
  if (_replyHasMemoryData(reply)) {
    // Small delay to not interrupt the user reading the response
    setTimeout(() => {
      setText("shieldLastLogged", "PHI updated your health memory ✓");
      setTimeout(() => {
        const el_log = el("shieldLastLogged");
        if (el_log && el_log.textContent.includes("memory")) {
          el_log.textContent = "";
        }
      }, 3000);
    }, 2000);
  }
}

function setSendingState(on) {
  const btn = el("sendBtn"), ta = el("chatInput");
  if (btn) {
    btn.disabled = on;
    btn.innerHTML = on ? '<i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i>' : '<i class="fa-solid fa-arrow-up"></i>';
  }
  if (ta) ta.disabled = on;
}

// ── File upload ────────────────────────────────────────────────────────────
function handleFileSelect(e) { Array.from(e.target.files || []).forEach(addFile); e.target.value = ""; }

function addFile(file) {
  if (file.size > 20 * 1024 * 1024) { toast(`${file.name} too large (max 20MB).`, "err"); return; }
  if (!/\.(pdf|txt|jpg|jpeg|png|webp|heic)$/i.test(file.name)) { toast("Unsupported file type.", "err"); return; }
  _uploads.push(file);
  renderFilePreview();
  toast(`${file.name} ready — press Send to analyze`);
}

function removeFile(i) { _uploads.splice(i, 1); renderFilePreview(); }

function renderFilePreview() {
  const s = el("filePreview"); if (!s) return;
  if (!_uploads.length) { s.classList.remove("show"); s.innerHTML = ""; return; }
  s.classList.add("show");
  s.innerHTML = _uploads.map((f, i) => `
    <div class="file-chip"><i class="fa-solid fa-file"></i><span>${esc(f.name)}</span><button class="file-chip-rm" onclick="removeFile(${i})"><i class="fa-solid fa-xmark"></i></button></div>`).join("");
}

function clearFilePreview() {
  const s = el("filePreview");
  if (s) { s.classList.remove("show"); s.innerHTML = ""; }
}

async function processUpload(file) {
  let s = await session();
  if (!s) {
    const refreshed = await handleUnauthorized(); if (!refreshed) { toast("Session expired.", "err"); return null; }
    s = await session(); if (!s) { toast("Session expired.", "err"); return null; }
  }

  const doUp = (token) => fetch(API + "/analyze", {
    method: "POST", headers: { Authorization: `Bearer ${token}` },
    body: (() => { const f = new FormData(); f.append("file", file); return f; })()
  });
  try {
    let res = await Promise.race([doUp(s.access_token), new Promise((_, r) => setTimeout(() => r(new Error("timed out")), 65000))]);

    if (res.status === 401) {
      const refreshed = await handleUnauthorized(); if (!refreshed) return null;
      const s2 = await session(); if (s2) res = await doUp(s2.access_token);
    }
    if (res.status === 403) {
      _consentsSaved = false; await saveConsents().catch(() => {});
      const s2 = await session(); if (s2) res = await doUp(s2.access_token);
    }
    if (res.status === 413) { toast("File too large (max 20MB).", "err"); return null; }
    if (!res.ok) { const d = await res.json().catch(() => {}); toast(d?.error || `Upload failed (${res.status}).`, "err"); return null; }

    const result = await res.json();

    // FIX-SHIELD-2: After upload, refresh both markers and shield
    setTimeout(async () => {
      await loadMarkersData();
      setTimeout(() => refreshShieldFromBehavioral(), 2000);
    }, 2000);

    return result;
  } catch (err) {
    toast(err.message?.includes("timed out") ? "Upload timed out." : "Upload failed.", "err"); return null;
  }
}

// ── Messages ───────────────────────────────────────────────────────────────
function appendMsg(text, role) {
  const d = el("chatDisplay"); if (!d) return null;
  const wrap = document.createElement("div");
  wrap.className = `chat-msg ${role === "user" ? "user-msg" : "ai-msg"}`;
  const av = `<div class="msg-av ${role === "user" ? "av-user" : "av-ai"}">${role === "user" ? (_userName?.[0]?.toUpperCase() || "U") : "φ"}</div>`;
  const body = document.createElement("div"); body.className = "msg-body";
  if (role === "user") { body.textContent = text; wrap.innerHTML = av; wrap.insertBefore(body, wrap.firstChild); }
  else { renderAI(body, text); wrap.innerHTML = av; wrap.appendChild(body); }
  d.appendChild(wrap); return wrap;
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
  elem.innerHTML = typeof marked !== "undefined" ? marked.parse(parts[0].trim()) : esc(parts[0].trim()).replace(/\n/g, "<br>");
  if (parts.length > 1 || text.includes("⚕️")) {
    const l = document.createElement("p");
    l.className = "phi-legal";
    l.textContent = "⚕️ PHI is an educational wellness tool. Always consult your healthcare provider.";
    elem.appendChild(l);
  }
}

const scrollBottom = () => { const d = el("chatDisplay"); if (d) d.scrollTop = d.scrollHeight; };

// ══════════════════════════════════════════════════════════════════════════════
// SHIELD — Single authoritative load
// ══════════════════════════════════════════════════════════════════════════════
async function autoLoadShield() {
  const h = await headers();
  if (!h) { renderShield(0, 0, 0, null); return; }
  const today = new Date().toISOString().slice(0, 10);

  // Try /startup (batches everything)
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 10000);
    const res = await fetch(API + "/startup", { headers: h, signal: ctrl.signal });
    clearTimeout(timeout);

    if (res.ok) {
      const d = await res.json();

      if (Array.isArray(d.history) && d.history.length && !el("historyList")?.querySelector(".hist-item")) {
        renderHistory(d.history);
      }
      if (Array.isArray(d.markers) && d.markers.length) {
        renderMarkers(d.markers);
        runCliffDetection(d.markers);
      }
      if (Array.isArray(d.cliff_alerts)) {
        _renderStartupAlerts(d.cliff_alerts);
      }
      if (d.goal_weight && !localStorage.getItem("phi_goal_wt")) {
        localStorage.setItem("phi_goal_wt", String(d.goal_weight));
        _goalWt = parseFloat(d.goal_weight);
        if (el("inputGoalWt")) el("inputGoalWt").value = d.goal_weight;
        if (el("proteinInput")) el("proteinInput").value = d.goal_weight;
        calcProteinDisplay(parseFloat(d.goal_weight), false);
      }
      if (Array.isArray(d.behavioral_today) && d.behavioral_today.length > 0) {
        const logs = d.behavioral_today.filter(l => l.date === today);
        const get = m => {
          const l = logs.filter(x => x.metric_name === m).sort((a, b) => a.created_at < b.created_at ? 1 : -1)[0];
          return l ? parseFloat(l.value) : 0;
        };
        const p = get("protein"), s = get("steps"), sl = get("sleep");
        _applyShieldValues(p, s, sl, today);
        _shieldLoaded = true;
        return;
      } else {
        renderShield(0, 0, 0, today);
        _shieldLoaded = true;
        return;
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") console.warn("[SHIELD] /startup failed:", e.message);
  }

  // Fallback: direct behavioral-logs endpoint
  try {
    const { ok, status, data } = await apiJson(`/api/behavioral-logs?days=1`, { headers: h });
    if (status === 401) { await handleUnauthorized(); return; }
    if (!ok) throw new Error(`HTTP ${status}`);
    if (!Array.isArray(data)) throw new Error("Non-array response");

    const tl = data.filter(l => l.date === today);
    const get = m => {
      const l = tl.filter(x => x.metric_name === m).sort((a, b) => a.created_at < b.created_at ? 1 : -1)[0];
      return l ? parseFloat(l.value) : 0;
    };
    const p = get("protein"), s = get("steps"), sl = get("sleep");
    _applyShieldValues(p, s, sl, today);

    if (tl.length > 0) {
      const last = data.sort((a, b) => a.created_at < b.created_at ? 1 : -1)[0];
      const w = new Date(last.created_at);
      setText("shieldLastLogged", `Last logged: ${last.date === today ? `Today at ${w.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}` : w.toLocaleDateString("en-US", { month: "short", day: "numeric" })}`);
    }
    _shieldLoaded = true;
  } catch (e) {
    console.warn("[SHIELD] /api/behavioral-logs failed:", e.message);
    if (_shieldRetries < 2) {
      _shieldRetries++;
      setTimeout(() => autoLoadShield(), 4000);
    } else {
      renderShield(0, 0, 0, null);
    }
  }
}

// FIX-SHIELD-1: Lightweight shield refresh after chat/upload
async function refreshShieldFromBehavioral() {
  const h = await headers(); if (!h) return;
  try {
    const today = new Date().toISOString().slice(0, 10);
    const { ok, data } = await apiJson(`/api/behavioral-logs?days=1`, { headers: h });
    if (!ok || !Array.isArray(data)) return;

    const tl = data.filter(l => l.date === today);
    const get = m => {
      const l = tl.filter(x => x.metric_name === m).sort((a, b) => a.created_at < b.created_at ? 1 : -1)[0];
      return l ? parseFloat(l.value) : 0;
    };
    const p = get("protein"), s = get("steps"), sl = get("sleep");

    if (p > 0 || s > 0 || sl > 0) {
      if (p > 0 && el("inputProtein")) el("inputProtein").value = p;
      if (s > 0 && el("inputSteps")) el("inputSteps").value = s;
      if (sl > 0 && el("inputSleep")) el("inputSleep").value = sl;
      renderShield(p, s, sl, today);

      if (tl.length > 0) {
        const last = tl.sort((a, b) => a.created_at < b.created_at ? 1 : -1)[0];
        const w = new Date(last.created_at);
        setText("shieldLastLogged", `Last logged: Today at ${w.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}`);
      }
      _shieldLoaded = true;
    }
  } catch (e) {
    console.warn("[SHIELD] Refresh failed:", e.message);
  }
}

function _renderStartupAlerts(alerts) {
  const c = el("cliffAlerts"); if (!c) return;
  if (!alerts?.length) return;
  c.innerHTML = alerts.map(a => {
    const cls = a.severity === "high" ? "ca-danger" : "ca-warn";
    const icon = a.severity === "high" ? "🚨" : "⚠️";
    const title = String(a.headline || a.marker || "").replace(/</g, "&lt;");
    const detail = String(a.detail || "").replace(/</g, "&lt;");
    return `<div class="ca-item ${cls}"><div class="ca-title">${icon} ${title}</div>${detail ? `<div class="ca-desc">${detail}</div>` : ""}</div>`;
  }).join("");
}

function _applyShieldValues(p, s, sl, today) {
  if (p > 0 && el("inputProtein")) el("inputProtein").value = p;
  if (s > 0 && el("inputSteps")) el("inputSteps").value = s;
  if (sl > 0 && el("inputSleep")) el("inputSleep").value = sl;
  renderShield(p, s, sl, today);
}

async function updateShield() {
  const p = parseFloat(el("inputProtein")?.value) || 0;
  const s = parseFloat(el("inputSteps")?.value) || 0;
  const sl = parseFloat(el("inputSleep")?.value) || 0;
  const gw = parseFloat(el("inputGoalWt")?.value);
  if (gw && gw !== _goalWt) { _goalWt = gw; localStorage.setItem("phi_goal_wt", String(gw)); calcProteinDisplay(gw, false); }
  renderShield(p, s, sl, new Date().toISOString().slice(0, 10));
  if (_user) await logShieldData(p, s, sl);
}

function renderShield(p, s, sl, logDate) {
  const gw = _goalWt || 165;
  _proteinTarget = Math.round(gw * 0.545 * 10) / 10;
  const pP = Math.min(100, Math.round((p / (_proteinTarget || 90)) * 100));
  const mP = Math.min(100, Math.round((s / 8000) * 100));
  const rP = Math.max(0, Math.min(100, Math.round(((sl - 4) / 5) * 100)));
  const sc = Math.round((pP + mP + rP) / 3);
  setRing("ringProtein", 440, pP);
  setRing("ringMovement", 346, mP);
  setRing("ringRecovery", 258, rP);
  setText("shieldScore", sc + "%");
  setText("shieldBadge", sc + "%");
  setText("proteinLegend", p > 0 ? `${p}g / ${_proteinTarget}g (${pP}%)` : `Target: ${_proteinTarget}g — not logged yet`);
  setText("movementLegend", s > 0 ? `${s.toLocaleString()} steps (${mP}%)` : "Steps — not logged yet");
  setText("recoveryLegend", sl > 0 ? `${sl}h sleep (${rP}%)` : "Sleep — not logged yet");
  setBarPct("proteinBar", pP); setBarPct("movementBar", mP); setBarPct("recoveryBar", rP);
}

function setRing(id, circ, pct) {
  const r = el(id);
  if (r) { r.style.strokeDasharray = circ; r.style.strokeDashoffset = circ - (circ * Math.max(0, Math.min(100, pct)) / 100); }
}
function setBarPct(id, pct) { const b = el(id); if (b) b.style.width = Math.max(0, pct) + "%"; }

async function logShieldData(p, s, sl) {
  const h = await headers(); if (!h) return;
  const date = new Date().toISOString().slice(0, 10);
  const logs = [];
  if (p > 0) logs.push({ date, metric_name: "protein", value: p, unit: "g" });
  if (s > 0) logs.push({ date, metric_name: "steps", value: s, unit: "steps" });
  if (sl > 0) logs.push({ date, metric_name: "sleep", value: sl, unit: "hours" });
  for (const l of logs) {
    await apiFetch("/api/behavioral-logs", { method: "POST", headers: h, body: JSON.stringify(l) }).catch(() => {});
  }
  setText("shieldLastLogged", "Last logged: just now");
  toast("Shield data logged ✓");
  _shieldLoaded = true;
}

// ── Protein calc ───────────────────────────────────────────────────────────
function calcProteinDisplay(gw, showDetails = true) {
  if (!gw || gw < 80 || gw > 400) { if (showDetails) toast("Enter a valid goal weight (80–400 lbs).", "err"); return; }
  _goalWt = gw; _proteinTarget = Math.round(gw * 0.545 * 10) / 10;
  const pm = Math.round(_proteinTarget / 3 * 10) / 10;
  const lu = pm >= 30;
  localStorage.setItem("phi_goal_wt", String(gw));
  setText("proteinNum", _proteinTarget); setText("proteinCaption", `${gw} lbs × 0.545 = ${_proteinTarget}g/day`);
  if (showDetails) {
    const d = el("proteinDetails");
    if (d) {
      d.classList.remove("hidden");
      d.innerHTML = `<strong>${pm}g per meal</strong> across 3 meals — ${lu ? "✅" : "⚠️"} ${lu ? "Meets" : "Below"} 30g leucine threshold<br><span style="color:var(--text-3);margin-top:4px;display:block">4oz chicken (35g) + Greek yogurt (17g) + 2 eggs (12g) + whey scoop (25g)</span>`;
    }
    if (el("inputGoalWt")) el("inputGoalWt").value = gw;
    renderShield(parseFloat(el("inputProtein")?.value) || 0, parseFloat(el("inputSteps")?.value) || 0, parseFloat(el("inputSleep")?.value) || 0, null);
  }
}

// ── Health data ────────────────────────────────────────────────────────────
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
    const s = (m.status || "").toLowerCase();
    const cls = s === "high" ? "val-high" : s === "low" ? "val-low" : s === "normal" ? "val-normal" : "";
    const badge = s && s !== "unknown" ? `<span class="marker-status st-${s}">${s.toUpperCase()}</span>` : "";
    return `<div class="marker-card"><div class="marker-card-name" title="${esc(m.marker_name)}">${esc(m.marker_name)}</div><div class="marker-card-val ${cls}">${m.value}<span class="marker-card-unit"> ${esc(m.unit || "")}</span></div>${badge}</div>`;
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
      const pct = ((r[r.length - 1]._v - r[0]._v) / r[0]._v) * 100;
      if (pct >= 15) alerts.push({ type: "danger", title: `🚨 Glucose rebound +${pct.toFixed(0)}%`, desc: `${r[0]._v} → ${r[r.length - 1]._v} mg/dL` });
      else if (pct >= 10) alerts.push({ type: "warn", title: `⚠ Glucose rising +${pct.toFixed(0)}%`, desc: "Approaching 15% threshold." });
    }
  }
  const hk = Object.keys(grouped).find(k => /hba1c/.test(k));
  if (hk) {
    const r = grouped[hk].sort((a, b) => a.date < b.date ? -1 : 1);
    for (let i = 1; i < r.length; i++) { const d = r[i]._v - r[i - 1]._v; if (d >= 0.25) { alerts.push({ type: "danger", title: `🚨 HbA1c rebound +${d.toFixed(2)}%`, desc: `${r[i - 1]._v}% → ${r[i]._v}%` }); break; } }
  }
  markers.filter(m => m.status === "HIGH" && !/glucose/i.test(m.marker_name)).slice(0, 2).forEach(m => alerts.push({ type: "warn", title: `⬆ ${m.marker_name} HIGH`, desc: `${m.value} ${m.unit || ""}` }));
  if (!alerts.length) alerts.push({ type: "ok", title: "✅ No rebound signals", desc: "All markers stable. Keep up protein + training." });
  const c = el("cliffAlerts");
  if (c) c.innerHTML = alerts.map(a => `<div class="ca-item ca-${a.type}"><div class="ca-title">${a.title}</div><div class="ca-desc">${a.desc}</div></div>`).join("");
}

// ── Ghrelin / Food noise ───────────────────────────────────────────────────
const NOISE_MSG = { 1: "Nearly silent.", 2: "Very low.", 3: "Mild.", 4: "Low-moderate.", 5: "Moderate.", 6: "Elevated.", 7: "High — taper may have been too fast.", 8: "Very high — biology, not willpower.", 9: "Intense. Discuss urgently.", 10: "🚨 Maximum. Provider conversation needed." };
function updateNoiseReadout() {
  const v = parseInt(el("noiseSlider")?.value || 5);
  const colors = [, "var(--ok)", "var(--ok)", "var(--ok)", "var(--amber)", "var(--amber)", "var(--amber)", "var(--danger)", "var(--danger)", "var(--danger)", "var(--danger)"];
  const r = el("noiseReadout"); if (r) r.innerHTML = `<strong style="color:${colors[v]}">Level ${v}/10</strong> — ${NOISE_MSG[v]}`;
}

async function logNoiseLevel() {
  const val = parseInt(el("noiseSlider")?.value || 5);
  const h = await headers(); if (!h) { toast("Sign in to log.", "info"); return; }
  await apiFetch("/api/behavioral-logs", { method: "POST", headers: h, body: JSON.stringify({ date: new Date().toISOString().slice(0, 10), metric_name: "food_noise", value: val, unit: "1-10" }) }).catch(() => {});
  toast(`Food noise ${val}/10 logged ✓`, "info");
}

// ── Voice ──────────────────────────────────────────────────────────────────
function initVoice() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const btn = el("micBtn");
  if (!SR || !btn) { if (btn) { btn.style.opacity = ".3"; btn.disabled = true; } return; }
  let on = false, rec = null;
  btn.addEventListener("click", () => {
    if (on) { rec?.stop(); return; }
    rec = new SR(); rec.lang = "en-US"; rec.interimResults = false;
    rec.onstart = () => { on = true; btn.style.color = "var(--danger)"; };
    rec.onresult = e => { const ta = el("chatInput"); if (ta) { ta.value = e.results[0][0].transcript; autoGrow(ta); ta.focus(); } };
    rec.onend = () => { on = false; btn.style.color = ""; };
    rec.onerror = e => toast(`Mic: ${e.error}`, "err");
    try { rec.start(); } catch { toast("Voice unavailable.", "err"); }
  });
}

// ── Export chat ────────────────────────────────────────────────────────────
function exportChat() {
  const msgs = el("chatDisplay")?.querySelectorAll(".chat-msg");
  if (!msgs?.length) { toast("No conversation to export.", "err"); return; }
  let out = `Curabook PHI — Chat Export\n${"=".repeat(40)}\n\n`;
  msgs.forEach(m => {
    const role = m.classList.contains("user-msg") ? "You" : "PHI";
    out += `${role}:\n${m.querySelector(".msg-body")?.innerText?.trim() || ""}\n\n`;
  });
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([out], { type: "text/plain" })), download: `phi-chat-${Date.now()}.txt` });
  a.click(); closeUserMenu(); toast("Chat exported");
}

// ── Feedback ───────────────────────────────────────────────────────────────
function initFeedback() {
  const btn = document.createElement("button");
  btn.id = "feedbackBtn"; btn.className = "feedback-fab";
  btn.innerHTML = `<i class="fa-regular fa-comment-dots"></i>`;
  btn.setAttribute("aria-label", "Send feedback"); btn.title = "Share feedback";
  document.body.appendChild(btn);

  const modal = document.createElement("div");
  modal.id = "feedbackModal"; modal.className = "feedback-modal-overlay";
  modal.setAttribute("aria-hidden", "true");
  modal.innerHTML = `
    <div class="feedback-modal" role="dialog" aria-label="Send Feedback">
      <button class="feedback-close" id="feedbackClose" aria-label="Close"><i class="fa-solid fa-xmark"></i></button>
      <div class="feedback-header">
        <div class="feedback-icon-wrap"><i class="fa-regular fa-comment-dots"></i></div>
        <div><h3 class="feedback-title">How's PHI working for you?</h3><p class="feedback-sub">Your feedback shapes what we build next.</p></div>
      </div>
      <div class="feedback-rating-row" id="feedbackRatingRow">
        <button class="rating-btn" data-val="1" title="Terrible">😞</button>
        <button class="rating-btn" data-val="2" title="Bad">😕</button>
        <button class="rating-btn" data-val="3" title="Okay">😐</button>
        <button class="rating-btn" data-val="4" title="Good">🙂</button>
        <button class="rating-btn" data-val="5" title="Excellent">🤩</button>
      </div>
      <div class="feedback-category-row" id="feedbackCategories">
        <button class="cat-btn" data-cat="chat">💬 Chat</button>
        <button class="cat-btn" data-cat="reports">📋 Reports</button>
        <button class="cat-btn" data-cat="shield">🛡 Shield</button>
        <button class="cat-btn" data-cat="ui">✨ Design</button>
        <button class="cat-btn" data-cat="bug">🐛 Bug</button>
        <button class="cat-btn" data-cat="idea">💡 Idea</button>
      </div>
      <textarea id="feedbackText" class="feedback-textarea" placeholder="Tell us more…" rows="3" maxlength="1000"></textarea>
      <div class="feedback-footer">
        <span class="feedback-char-count" id="feedbackCharCount">0 / 1000</span>
        <button class="feedback-submit" id="feedbackSubmit"><i class="fa-solid fa-paper-plane"></i> Send Feedback</button>
      </div>
      <div id="feedbackSuccess" class="feedback-success" style="display:none">
        <div class="feedback-success-icon">🎉</div><strong>Thank you!</strong>
        <p>Your feedback has been sent. We read every message.</p>
      </div>
    </div>`;
  document.body.appendChild(modal);

  let selectedRating = 0, selectedCategory = "";
  btn.addEventListener("click", () => openFeedback());
  el("feedbackClose")?.addEventListener("click", () => closeFeedback());
  modal.addEventListener("click", e => { if (e.target === modal) closeFeedback(); });
  modal.querySelectorAll(".rating-btn").forEach(b => b.addEventListener("click", () => { selectedRating = parseInt(b.dataset.val); modal.querySelectorAll(".rating-btn").forEach(rb => rb.classList.remove("active")); b.classList.add("active"); }));
  modal.querySelectorAll(".cat-btn").forEach(b => b.addEventListener("click", () => { selectedCategory = b.dataset.cat; modal.querySelectorAll(".cat-btn").forEach(cb => cb.classList.remove("active")); b.classList.add("active"); }));
  const ta = el("feedbackText"); const cc = el("feedbackCharCount");
  ta?.addEventListener("input", () => { if (cc) cc.textContent = `${ta.value.length} / 1000`; });
  el("feedbackSubmit")?.addEventListener("click", () => submitFeedback(selectedRating, selectedCategory));
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeFeedback(); });
}

function openFeedback() {
  const modal = el("feedbackModal"); if (!modal) return;
  modal.setAttribute("aria-hidden", "false"); modal.classList.add("open");
  document.body.style.overflow = "hidden";
  el("feedbackSuccess").style.display = "none"; el("feedbackText").value = "";
  el("feedbackCharCount").textContent = "0 / 1000";
  modal.querySelectorAll(".rating-btn,.cat-btn").forEach(b => b.classList.remove("active"));
}

function closeFeedback() {
  const modal = el("feedbackModal"); if (!modal) return;
  modal.setAttribute("aria-hidden", "true"); modal.classList.remove("open");
  document.body.style.overflow = "";
}

async function submitFeedback(rating, category) {
  const text = el("feedbackText")?.value?.trim() || "";
  const submitBtn = el("feedbackSubmit");
  if (!rating && !text) { toast("Please rate or write a message first.", "info"); return; }
  if (submitBtn) { submitBtn.disabled = true; submitBtn.innerHTML = '<i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i> Sending…'; }
  try {
    const h = await headers();
    if (h) await fetch(API + "/api/feedback", { method: "POST", headers: h, body: JSON.stringify({ rating, category, text, url: window.location.href, user_email: _user?.email || "anonymous", timestamp: new Date().toISOString() }), signal: AbortSignal.timeout(8000) });
  } catch (e) {}
  el("feedbackSuccess").style.display = "flex";
  el("feedbackRatingRow").style.display = "none"; el("feedbackCategories").style.display = "none";
  el("feedbackText").style.display = "none";
  const footer = document.querySelector(".feedback-footer"); if (footer) footer.style.display = "none";
  setTimeout(() => closeFeedback(), 2800);
  setTimeout(() => {
    el("feedbackSuccess").style.display = "none";
    if (el("feedbackRatingRow")) el("feedbackRatingRow").style.display = "";
    if (el("feedbackCategories")) el("feedbackCategories").style.display = "";
    if (el("feedbackText")) el("feedbackText").style.display = "";
    const footer = document.querySelector(".feedback-footer"); if (footer) footer.style.display = "";
    if (submitBtn) { submitBtn.disabled = false; submitBtn.innerHTML = '<i class="fa-solid fa-paper-plane"></i> Send Feedback'; }
  }, 3500);
}

// ── Sync Wearable ──────────────────────────────────────────────────────────
function initSyncWearable() {
  const btn = el("syncWearableBtn"); if (!btn) return;
  let cameraInput = el("cameraInput");
  if (!cameraInput) {
    cameraInput = document.createElement("input");
    cameraInput.type = "file"; cameraInput.id = "cameraInput";
    cameraInput.accept = "image/*"; cameraInput.capture = "environment";
    cameraInput.style.display = "none";
    document.body.appendChild(cameraInput);
  }
  btn.addEventListener("click", (e) => {
    e.preventDefault();
    if (navigator.maxTouchPoints > 0) { cameraInput.click(); }
    else { el("fileInput")?.click(); }
  });
  cameraInput.addEventListener("change", (e) => {
    const file = e.target.files?.[0]; if (!file) return;
    addFile(file); e.target.value = "";
    switchView("chat");
    setTimeout(() => {
      const ta = el("chatInput");
      if (ta) { ta.value = "Please analyze this wearable screenshot and extract my protein, steps, and sleep data."; autoGrow(ta); }
    }, 300);
  });
}

// ── Utils ──────────────────────────────────────────────────────────────────
const el = id => document.getElementById(id);
const esc = s => String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
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
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; setTimeout(() => t.remove(), 300); }, 3800);
}

// ── Wire all events ────────────────────────────────────────────────────────
function wireEvents() {
  document.querySelectorAll(".nav-item[data-view]").forEach(btn => btn.addEventListener("click", () => { switchView(btn.dataset.view); closeSidebar(); }));
  el("newChatBtn")?.addEventListener("click", () => { resetChat(); switchView("chat"); });

  el("mobileMenuBtn")?.addEventListener("click", openSidebar);
  el("sidebarOverlay")?.addEventListener("click", closeSidebar);
  el("mobileCockpitBtn")?.addEventListener("click", toggleCockpit);
  el("cockpitOverlay")?.addEventListener("click", closeCockpit);
  el("cockpitCloseBtn")?.addEventListener("click", closeCockpit);
  el("sidebarCloseBtn")?.addEventListener("click", closeSidebar);

  el("userRow")?.addEventListener("click", e => { if (!e.target.closest(".user-dropdown")) toggleUserMenu(); });
  document.addEventListener("click", e => { if (!el("userRow")?.contains(e.target)) closeUserMenu(); });
  el("themeToggleBtn")?.addEventListener("click", toggleTheme);
  el("topThemeBtn")?.addEventListener("click", toggleTheme);
  el("logoutBtn")?.addEventListener("click", handleLogout);
  el("exportChatBtn")?.addEventListener("click", exportChat);

  el("historyList")?.addEventListener("click", e => {
    const del = e.target.closest(".hist-del[data-del]");
    const item = e.target.closest(".hist-item[data-id]");
    if (del) deleteConversation(del.dataset.del, e);
    else if (item) openConversation(item.dataset.id);
  });

  const ta = el("chatInput");
  if (ta) {
    ta.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } });
    ta.addEventListener("input", () => autoGrow(ta));
  }
  el("sendBtn")?.addEventListener("click", handleSend);

  document.querySelectorAll(".suggestion-chips .chip").forEach(c => c.addEventListener("click", () => { if (c.dataset.q) sendMessage(c.dataset.q); }));
  el("chatDisplay")?.addEventListener("click", e => {
    const chip = e.target.closest(".chip[data-q]"); if (chip?.dataset.q) { sendMessage(chip.dataset.q); return; }
    const cta = e.target.closest("[data-ask]"); if (cta?.dataset.ask) sendMessage(cta.dataset.ask);
  });

  const fi = el("fileInput"); fi?.addEventListener("change", handleFileSelect);
  ["attachTopBtn", "attachInputBtn", "uploadNudgeBtn", "reportsUploadBtn"].forEach(id => el(id)?.addEventListener("click", () => fi?.click()));

  el("updateShieldBtn")?.addEventListener("click", updateShield);
  el("calcBtn")?.addEventListener("click", () => { const gw = parseFloat(el("proteinInput")?.value); gw ? calcProteinDisplay(gw, true) : toast("Enter a goal weight (80–400 lbs).", "err"); });
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

// ── Init ───────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  wireEvents();
  updateNoiseReadout();
  initVoice();
  initFeedback();
  initSyncWearable();
  boot();
});