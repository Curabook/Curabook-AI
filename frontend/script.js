/**
 * script.js — Curabook PHI v10.0
 *
 * FIXES IN THIS VERSION:
 *
 * FIX-1: AUTH PERSISTENCE FOREVER
 *   Root cause: __phi_auth_ready flag + _onSignInRunning guard was preventing
 *   re-initialization on tab revisit because getSession() returned a valid session
 *   but onSignIn was skipped due to state flags.
 *   Fix: On every page load, always call onSignIn() if a valid session exists.
 *   We store user_id + access_token in localStorage so performance_patch renders
 *   cached data instantly. Auth state is re-validated on every boot.
 *
 * FIX-2: HEALTH MEMORY WORKING IN CHAT
 *   Root cause: _cachedMemories was sent as a "hint" but the backend was not
 *   guaranteed to use it. The real fix is in chat_routes.py (_build_context_with_memories
 *   fetches fresh memories synchronously before the LLM call).
 *   Frontend fix: after EVERY message, refresh _cachedMemories so the UI stays
 *   in sync. Also display memory count in the cockpit so users can verify.
 *
 * FIX-3: FEEDBACK SYSTEM — Full NPS (1-10) + category tags
 *   Upgraded from basic to full NPS widget with color-coded scores,
 *   category selection, and smooth success animation.
 *
 * FIX-4: PAYPAL PAYMENT TIERS (replaces Razorpay)
 *   Free tier: 14-day trial, then Shield plan required.
 *   Pro tier: unlimited reports, full marker memory, advocacy briefs.
 *   PayPal subscription flow — redirects to PayPal hosted checkout.
 *   No Razorpay SDK needed.
 *
 * FIX-5: SPEED IMPROVEMENTS
 *   - Parallel startup fetches (history + markers + shield in one call)
 *   - 45s timeout (was 65s)
 *   - Behavioral data parsed client-side before API call
 *   - File previews rendered immediately (no waiting)
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
let _taperPlan     = null;  // cached taper plan from API
let _consentsSaved = false;
let _consentsPromise = null;
let _redirecting   = false;

// Payment state
let _userPlan = "free";
// No free report limit — 14-day trial model. isPro check controls access.

// FIX-2: Health memory cache — refreshed after every message
let _cachedMemories = [];
let _memoryCount    = 0;

// FIX-1: Auth — no debounce timer, no flags blocking re-init
let _authInitialized = false;  // Single flag — set to true after first successful onSignIn
let _authInProgress  = false;  // Prevent duplicate concurrent calls

// ── Behavioral reporting patterns ──────────────────────────────────────────
const _BEHAVIORAL_REPORTING_PATTERNS = [
  /\b(\d{2,3})\s*(?:g|grams?)\s+(?:of\s+)?protein/i,
  /protein[:\s]+(\d{2,3})\s*(?:g|grams?)/i,
  /(\d{3,6})\s+steps/i,
  /slept?\s+(\d+(?:\.\d)?)\s*(?:h(?:ours?)?|hrs?)/i,
];

const _SHIELD_REPLY_KEYWORDS = [
  "protein", "grams", "g/day", "g protein", "steps", "walked", "walking",
  "sleep", "slept", "hours of sleep", "logged", "recorded", "stored your",
  "i've noted", "i'll remember", "noted that"
];

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
  _user = null; _convId = null; _isSending = false;
  _consentsSaved = false; _shieldLoaded = false; _shieldRetries = 0;
  _authInitialized = false; _authInProgress = false;
  setSendingState(false);
  try {
    const keysToRemove = Object.keys(localStorage).filter(k =>
      k.startsWith("sb-") || k.startsWith("supabase") || k.startsWith("gotrue") ||
      k.startsWith("pkce") || k.startsWith("phi_cache") || k === "phi-auth-token" ||
      k === "phi_user_id"
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
// BOOT — FIX-1: ALWAYS re-initialize on every page load
// ══════════════════════════════════════════════════════════════════════════════
async function boot() {
  wakeUpServer();

  try {
    _sb = supabase.createClient(SUPABASE_URL, SUPABASE_KEY, {
      auth: {
        detectSessionInUrl: true,
        persistSession:     true,
        autoRefreshToken:   true,
        storage:            window.localStorage,
      }
    });

    _sb.auth.onAuthStateChange(async (event, session) => {
      console.log("[AUTH]", event, session?.user?.email?.slice(0, 8) ?? "none");

      if (event === "SIGNED_OUT") {
        if (!_redirecting) {
          _redirecting = true;
          _authInitialized = false;
          window.location.replace("/login");
        }
        return;
      }

      if (event === "TOKEN_REFRESHED" && session?.user) {
        _user = session.user;
        try { localStorage.setItem("phi_user_id", _user.id); } catch (e) {}
        return;
      }
    });

    const { data: { session } } = await _sb.auth.getSession();
    if (session?.user) {
      await _runOnSignIn(session.user, session);
    } else {
      const hash = window.location.hash;
      if (!hash || !hash.includes("access_token")) {
        if (!IS_LOCAL) {
          setTimeout(() => {
            if (!_user) window.location.replace("/login");
          }, 3000);
        }
      }
    }

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && _isSending && Date.now() - _sendStart > 65000) {
        _isSending = false; setSendingState(false);
      }
    });

    document.addEventListener("visibilitychange", async () => {
      if (!document.hidden && _user) {
        try {
          const { data: { session } } = await _sb.auth.getSession();
          if (!session?.user && !_redirecting) {
            console.log("[AUTH] Session expired while tab was hidden");
            await doSignOut();
          } else if (session?.user) {
            _user = session.user;
          }
        } catch (e) {}
      }
    });

  } catch (err) {
    console.error("[PHI] Boot error:", err);
    toast("Failed to initialize — please refresh.", "err");
  }
}

async function _runOnSignIn(user, session) {
  if (_authInProgress) {
    console.log("[AUTH] Init already in progress, skipping");
    return;
  }
  _authInProgress = true;
  try {
    await onSignIn(user, session);
    _authInitialized = true;
  } catch (e) {
    console.error("[AUTH] onSignIn error:", e);
  } finally {
    _authInProgress = false;
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// ON SIGN IN
// ══════════════════════════════════════════════════════════════════════════════
async function onSignIn(user, session) {
  console.log("[AUTH] onSignIn for", user.email?.slice(0, 8));

  _shieldLoaded = false;
  _shieldRetries = 0;
  _user = user;

  try {
    localStorage.setItem("phi_user_id", user.id);
    if (session?.access_token) {
      localStorage.setItem("phi_access_token", session.access_token);
    }
  } catch (e) {}

  const meta = user.user_metadata || {};
  _userName = meta.first_name || user.email?.split("@")[0]?.split(/[._-]/)[0] || "there";
  _userName = _userName[0].toUpperCase() + _userName.slice(1);

  setText("userEmail", user.email);
  setText("welcomeName", _userName);
  const av = el("userAvatar");
  if (av) av.textContent = _userName[0].toUpperCase();

  const h = new Date().getHours();
  setText("timeGreeting", h < 12 ? "morning" : h < 17 ? "afternoon" : "evening");

  window.__phi_auth_ready = true;
  window.__phi_user_id = user.id;
  window.dispatchEvent(new CustomEvent("phi:authed", { detail: { userId: user.id } }));

  await Promise.allSettled([
    saveConsents().catch(e => console.warn("[CONSENT]", e)),
    loadHistory(),
    loadMarkersData(),
    loadPaymentStatus(),
    _refreshMemoryCache(),
  ]);

  _startPlanPoll(); // start polling for plan changes (revoke detection)
  await autoLoadShield();

  const gw = localStorage.getItem("phi_goal_wt");
  if (gw) {
    if (el("inputGoalWt")) el("inputGoalWt").value = gw;
    if (el("proteinInput")) el("proteinInput").value = gw;
    calcProteinDisplay(parseFloat(gw), false);
  }

  _updateMemoryCountDisplay();

  console.log("[AUTH] Init complete for", user.email?.slice(0, 8));
}

// ── FIX-2: Memory cache management ────────────────────────────────────────
async function _refreshMemoryCache() {
  const h = await headers();
  if (!h) return;
  try {
    const { ok, data } = await apiJson("/api/memory/facts", { headers: h });
    if (ok && data?.facts) {
      _cachedMemories = data.facts;
      _memoryCount = data.count || data.facts.length;
      _updateMemoryCountDisplay();
      console.log(`[MEMORY] Cached ${_cachedMemories.length} facts`);
    }
  } catch (e) {
    console.warn("[MEMORY] Cache refresh error:", e);
  }
}

function _updateMemoryCountDisplay() {
  const badge = el("memoryCountBadge");
  if (badge) {
    badge.textContent = _memoryCount > 0 ? `${_memoryCount} facts stored` : "No facts yet";
    badge.style.color = _memoryCount > 0 ? "var(--ok)" : "var(--text-3)";
  }
}

// ── Payment status ─────────────────────────────────────────────────────────
async function loadPaymentStatus() {
  const h = await headers();
  if (!h) return;
  try {
    const { ok, data } = await apiJson("/api/payment/status", { headers: h });
    if (ok && data) {
      _userPlan         = data.plan || "free";
      window._userPlan  = _userPlan; // expose for phi_fixes.js
      // trial model — no per-report limits
      // Apply free-all override (founder toggle)
      if (data.is_free_all) {
        _userPlan         = "pro";
        window._userPlan  = "pro";
        // pro user — full access
      }
      _renderPlanBadge();
    }
  } catch (e) {
    console.warn("[PAYMENT] Status load error:", e);
  }
}

function _renderPlanBadge() {
  const planEl = el("userPlan");
  if (planEl) {
    const isPro = _userPlan === "pro" || _userPlan === "annual" || _userPlan === "monthly" || _userPlan === "trial";
    planEl.textContent = isPro ? "Curabook Pro ✦" : "Curabook Free";
    planEl.style.color = isPro ? "var(--signal)" : "var(--text-3)";
  }
}

// ── Plan poll — detects founder revoke within 60s without page refresh ──────
let _planPollTimer = null;

function _startPlanPoll() {
  if (_planPollTimer) return; // already running
  _planPollTimer = setInterval(async () => {
    const h = await headers();
    if (!h) return;
    try {
      const { ok, data } = await apiJson("/api/payment/status", { headers: h });
      if (!ok || !data) return;

      const prevPlan = _userPlan;
      const newPlan  = data.plan || "free";
      const wasPro   = prevPlan === "pro" || prevPlan === "annual" || prevPlan === "monthly" || prevPlan === "trial";
      const nowPro   = newPlan  === "pro" || newPlan  === "annual" || newPlan  === "monthly" || newPlan  === "trial";

      // Update globals
      _userPlan         = newPlan;
        window._userPlan  = newPlan;
      // trial model — access controlled by plan only
      if (data.is_free_all) {
        _userPlan         = "pro";
        window._userPlan  = "pro";
        // pro user — full access
      }
      _renderPlanBadge();

      // Plan was revoked — show upgrade modal immediately
      if (wasPro && !nowPro && !data.is_free_all) {
        console.log("[PLAN POLL] Plan revoked by founder — showing upgrade modal");
        showUpgradeModal("revoked");
      }
    } catch (e) {
      console.warn("[PLAN POLL] error:", e);
    }
  }, 60000); // poll every 60 seconds
}

// ── Upload click gate ──────────────────────────────────────────────────────
function handleUploadClick() {
  const isPro = _userPlan === "pro" || _userPlan === "annual" || _userPlan === "monthly" || _userPlan === "trial";
  if (!isPro) {
    showUpgradeModal("upload");
    return;
  }
  el("fileInput")?.click();
}

// ── FIX-4: Upgrade modal — PayPal ─────────────────────────────────────────
function showUpgradeModal(reason = "manual") {
  const existing = el("upgradeModal");
  if (existing) existing.remove();

  const modal = document.createElement("div");
  modal.id = "upgradeModal";
  modal.style.cssText = `
    position:fixed;inset:0;background:rgba(0,0,0,.78);z-index:9999;
    display:flex;align-items:center;justify-content:center;padding:16px;
    animation:fadeIn .2s ease;
  `;

  modal.innerHTML = `
    <div style="
      background:var(--surface);
      border:1px solid var(--border-2);
      border-radius:20px;
      padding:28px 24px 24px;
      max-width:420px;
      width:100%;
      box-shadow:0 32px 80px rgba(0,0,0,.6);
      animation:slideUp .25s ease;
      position:relative;
      overflow:hidden;
    ">
      <div style="position:absolute;top:0;left:0;right:0;height:3px;
        background:linear-gradient(90deg,var(--signal),var(--signal-2));"></div>

      <button onclick="closeUpgradeModal()" style="
        position:absolute;top:14px;right:14px;
        width:30px;height:30px;border-radius:8px;
        border:none;background:var(--surface-3);color:var(--text-2);
        font-size:.85rem;cursor:pointer;
        display:flex;align-items:center;justify-content:center;
        transition:all .15s;">
        <i class="fa-solid fa-xmark"></i>
      </button>

      <div style="display:flex;align-items:center;gap:12px;margin-bottom:18px;padding-right:36px;">
        <div style="
          width:42px;height:42px;flex-shrink:0;
          background:linear-gradient(135deg,var(--signal),var(--signal-2));
          border-radius:12px;display:flex;align-items:center;justify-content:center;
          font-family:var(--serif);font-size:1.15rem;color:#0a0b0e;font-weight:600;
          box-shadow:0 4px 16px var(--signal-glow);">φ</div>
        <div>
          <h2 style="font-family:var(--serif);font-size:1.2rem;font-weight:400;
            color:var(--text);margin-bottom:2px;line-height:1.2;">Start your 14-day free trial</h2>
          <p style="font-size:.73rem;color:var(--text-3);line-height:1.4;">
            Full access · Cancel anytime · Less than half a Wegovy copay
          </p>
        </div>
      </div>

      ${reason === "upload" ? `
        <div style="
          background:var(--amber-dim);border:1px solid rgba(251,191,36,.3);
          border-radius:10px;padding:10px 13px;margin-bottom:16px;
          font-size:.8rem;color:var(--amber);
          display:flex;align-items:center;gap:8px;">
          <i class="fa-solid fa-triangle-exclamation" style="flex-shrink:0;"></i>
          <span><strong>Shield plan required</strong> — Start your 14-day free trial to unlock lab uploads and cliff detection.</span>
        </div>` : ""}

      <div style="
        background:var(--surface-2);border:1px solid var(--border);
        border-radius:12px;padding:14px 16px;margin-bottom:18px;">
        <div style="font-size:.62rem;font-weight:700;color:var(--text-3);
          text-transform:uppercase;letter-spacing:.1em;margin-bottom:10px;">Everything in Shield</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:7px 12px;">
          ${[
            ["fa-syringe","Live drug level tracker"],
            ["fa-file-medical","Lab marker monitoring"],
            ["fa-brain","Full AI health memory"],
            ["fa-shield-halved","PA appeal generator"],
            ["fa-chart-line","Cliff signal detection"],
            ["fa-stethoscope","Doctor visit prep"],
          ].map(([icon, label]) => `
            <div style="display:flex;align-items:center;gap:7px;font-size:.78rem;color:var(--text-2);">
              <i class="fa-solid ${icon}" style="color:var(--signal);font-size:.7rem;width:14px;text-align:center;flex-shrink:0;"></i>
              ${label}
            </div>`).join("")}
        </div>
      </div>

      <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:14px;">

        <button id="ppAnnualBtn" onclick="initiatePayPalCheckout('annual')" style="
          width:100%;padding:13px 16px;
          background:var(--signal);color:#0a0b0e;
          border:none;border-radius:12px;
          font-size:.9rem;font-weight:700;
          cursor:pointer;font-family:var(--sans);
          box-shadow:0 4px 20px var(--signal-glow);
          transition:all .15s;
          display:flex;align-items:center;justify-content:space-between;
          position:relative;">
          <span style="display:flex;align-items:center;gap:8px;">
            Shield Annual
            <span style="font-size:.58rem;font-weight:700;background:rgba(0,0,0,.2);color:#0a0b0e;padding:2px 7px;border-radius:20px;letter-spacing:.04em;">BEST VALUE</span>
          </span>
          <span style="font-family:var(--mono);font-size:1rem;">$179<span style="font-size:.72rem;font-weight:500;">/yr</span></span>
        </button>

        <button id="ppMonthlyBtn" onclick="initiatePayPalCheckout('monthly')" style="
          width:100%;padding:13px 16px;
          background:var(--surface-3);color:var(--text);
          border:2px solid var(--border-2);border-radius:12px;
          font-size:.9rem;font-weight:600;
          cursor:pointer;font-family:var(--sans);
          transition:all .15s;
          display:flex;align-items:center;justify-content:space-between;">
          <span>Shield Monthly</span>
          <span style="font-family:var(--mono);font-size:1rem;color:var(--text);">$24.99<span style="font-size:.72rem;font-weight:500;color:var(--text-2);">/mo</span></span>
        </button>

      </div>

      <div style="font-size:.68rem;color:var(--text-3);text-align:center;line-height:1.6;margin-bottom:10px;padding:8px 10px;background:var(--surface-2);border:1px solid var(--border);border-radius:8px;">
        14-day free trial on both plans · HSA/FSA eligible · Save $120 on annual vs monthly
      </div>

      <div id="upgradeStatus" style="text-align:center;font-size:.78rem;color:var(--text-3);min-height:18px;margin-bottom:6px;"></div>
      <p style="font-size:.67rem;color:var(--text-3);text-align:center;line-height:1.6;">
        <i class="fa-solid fa-lock" style="font-size:.6rem;margin-right:3px;"></i>
        Secure via PayPal · No card stored · Cancel anytime
      </p>
    </div>
  `;

  modal.addEventListener("click", e => { if (e.target === modal) closeUpgradeModal(); });
  document.body.appendChild(modal);
}

function closeUpgradeModal() {
  const m = el("upgradeModal");
  if (m) m.remove();
}

// ── FIX-4: PayPal checkout ─────────────────────────────────────────────────
async function initiatePayPalCheckout(plan = "monthly") {
  const PLAN_LABELS = { monthly: "Shield Monthly — $24.99/mo", annual: "Shield Annual — $179/yr" };
  const btnId    = plan === "annual" ? "ppAnnualBtn" : "ppMonthlyBtn";
  const btn      = el(btnId);
  const statusEl = el("upgradeStatus");
  const origLabel = PLAN_LABELS[plan] || "Upgrade";

  if (btn) {
    btn.disabled  = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i>';
  }
  if (statusEl) { statusEl.textContent = "Connecting to PayPal…"; statusEl.style.color = "var(--text-3)"; }

  const h = await headers();
  if (!h) {
    toast("Please sign in to upgrade.", "err");
    if (btn) { btn.disabled = false; btn.innerHTML = origLabel; }
    return;
  }

  try {
    const res = await fetch(API + "/api/payment/paypal/create-subscription", {
      method:  "POST",
      headers: h,
      body:    JSON.stringify({ plan }),
      signal:  AbortSignal.timeout(15000),
    });

    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.error || `Payment setup failed (${res.status}). Please try again.`);
    }

    const data = await res.json();
    if (!data.approve_url) throw new Error("PayPal did not return a checkout URL. Please try again.");

    if (statusEl) statusEl.textContent = "Redirecting to PayPal…";

    // Redirect to PayPal hosted checkout.
    // PayPal returns user to FRONTEND_URL/payment/success?subscription_id=xxx
    // which payment_routes.py serves (app.html), and phi_fixes.js calls
    // /api/payment/paypal/capture to activate the plan in the database.
    window.location.href = data.approve_url;

  } catch (e) {
    console.error("[UPGRADE]", e);
    if (statusEl) { statusEl.textContent = e.message; statusEl.style.color = "var(--danger)"; }
    if (btn) { btn.disabled = false; btn.innerHTML = origLabel; }
    toast(e.message, "err");
  }
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
    const t = setTimeout(() => ctrl.abort(), 45000);
    try {
      const r = await fetch(API + path, { ...opts, signal: ctrl.signal });
      clearTimeout(t);
      return r;
    } catch (e) {
      clearTimeout(t);
      if (e.name === "AbortError") throw new Error("Server is taking too long. Please try again.");
      throw e;
    }
  };

  try {
    const res = await doFetch();
    if (res.status >= 500) {
      console.warn(`[API] ${path} returned ${res.status}, retrying in 2s…`);
      await new Promise(r => setTimeout(r, 2000));
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
      try { localStorage.setItem("phi_access_token", data.session.access_token); } catch (e) {}
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
const isDesktop    = () => window.innerWidth >= 1024;
const openSidebar  = () => {
  if (isDesktop()) { el("sidebar")?.classList.remove("desktop-collapsed"); }
  else { el("sidebar")?.classList.add("open"); el("sidebarOverlay")?.classList.add("show"); }
  closeCockpit();
};
const closeSidebar = () => {
  if (isDesktop()) { el("sidebar")?.classList.add("desktop-collapsed"); }
  else { el("sidebar")?.classList.remove("open"); el("sidebarOverlay")?.classList.remove("show"); }
};
const openCockpit  = () => { el("cockpit")?.classList.add("open"); el("cockpitOverlay")?.classList.add("show"); closeSidebar(); };
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
  setText("convTitle", { chat: "Chat with Curabook", health: "My Health", reports: "Lab Reports" }[view] || "");
}

async function loadHealthView() {
  const content = el("healthContent"); if (!content) return;
  content.innerHTML = `<div class="hv-empty"><i class="fa-solid fa-spinner fa-spin"></i> Loading your health picture…</div>`;
  const h = await headers(); if (!h) return;
  try {
    const [mR, dR, tR] = await Promise.allSettled([
      apiJson("/api/health-markers", { headers: h }),
      apiJson("/api/dashboard",      { headers: h }),
      apiJson("/api/taper",          { headers: h }),
    ]);
    const markers  = mR.status === "fulfilled" && mR.value.ok && Array.isArray(mR.value.data) ? mR.value.data : [];
    const dashData = dR.status === "fulfilled" && dR.value.ok && dR.value.data ? dR.value.data : null;
    _taperPlan = tR.status === "fulfilled" && tR.value.ok && tR.value.data?.plan ? tR.value.data.plan : null;
    // Always render health view — taper tracker is always available
    // If no markers yet, buildHealthViewHTML shows an upload prompt inside the view
    // instead of replacing the entire view and hiding the taper tracker
    content.innerHTML = buildHealthViewHTML(markers, dashData);
    content.querySelectorAll("[data-ask]").forEach(btn =>
      btn.addEventListener("click", () => { switchView("chat"); setTimeout(() => sendMessage(btn.dataset.ask), 100); })
    );
  } catch (e) {
    content.innerHTML = `<div class="hv-empty">Could not load health data.</div>`;
  }
}


// ── Taper Tracker ────────────────────────────────────────────────────────────

function _taperSetupForm() {
  return `<div id="taperSetupForm" style="margin-top:12px;display:none">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">
      <div>
        <label class="sl-label">Medication</label>
        <select id="taperMed" class="sl-input" style="width:100%;padding:8px;border-radius:8px;background:var(--surface-2);border:1px solid var(--border);color:var(--text)">
          <option value="semaglutide">Wegovy / Ozempic (semaglutide)</option>
          <option value="tirzepatide">Zepbound / Mounjaro (tirzepatide)</option>
        </select>
      </div>
      <div>
        <label class="sl-label">Taper type</label>
        <select id="taperType" class="sl-input" style="width:100%;padding:8px;border-radius:8px;background:var(--surface-2);border:1px solid var(--border);color:var(--text)">
          <option value="stretch">Stretch out (extend interval)</option>
          <option value="stepdown">Step down (reduce dose)</option>
        </select>
      </div>
      <div>
        <label class="sl-label">Current dose (mg)</label>
        <input type="number" id="taperDose" class="sl-input" placeholder="e.g. 1.7" step="0.1" min="0.1" max="5" style="width:100%">
      </div>
      <div>
        <label class="sl-label">Days between doses</label>
        <input type="number" id="taperFreq" class="sl-input" placeholder="7" min="7" max="30" value="7" style="width:100%">
      </div>
      <div>
        <label class="sl-label">Last dose taken</label>
        <input type="date" id="taperLastDose" class="sl-input" style="width:100%">
      </div>
      <div>
        <label class="sl-label">Target weeks to taper</label>
        <input type="number" id="taperWeeks" class="sl-input" placeholder="9" min="4" max="26" value="9" style="width:100%">
      </div>
    </div>
    <div style="display:flex;gap:8px">
      <button class="cp-primary-btn" style="flex:1" onclick="saveTaperPlan()">Start Tracking →</button>
      <button class="cp-secondary-btn" onclick="document.getElementById('taperSetupForm').style.display='none'">Cancel</button>
    </div>
  </div>`;
}

function _taperHungerBar(forecast) {
  if (!forecast || !forecast.length) return '';
  const days = ['T','T+1','T+2','T+3','T+4','T+5','T+6'];
  const colors = { low: 'var(--ok)', moderate: 'var(--amber)', high: 'var(--signal)' };
  const bars = forecast.map((f, i) => {
    const h = Math.round(100 - f.pct);
    const col = colors[f.hunger] || 'var(--ok)';
    return `<div style="display:flex;flex-direction:column;align-items:center;gap:3px;flex:1">
      <div style="width:100%;height:${Math.max(4, h * 0.5)}px;background:${col};border-radius:3px;min-height:4px"></div>
      <span style="font-size:10px;color:var(--text-3)">${days[i]}</span>
    </div>`;
  }).join('');
  return `<div style="margin-top:10px">
    <div style="font-size:11px;color:var(--text-3);margin-bottom:4px">Hunger forecast (higher bar = more intense)</div>
    <div style="display:flex;gap:4px;align-items:flex-end;height:32px">${bars}</div>
  </div>`;
}

function buildTaperHTML(plan) {
  // No plan — show a prominent call-to-action card, not just a tiny link
  if (!plan) {
    return `<div class="hv-section">
      <div style="background:linear-gradient(135deg,var(--surface-2) 0%,var(--surface-3,var(--surface-2)) 100%);
        border:1px solid var(--border);border-radius:14px;padding:18px 20px;">
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">
          <div style="width:40px;height:40px;border-radius:10px;background:var(--signal-dim,rgba(99,179,237,.15));
            display:flex;align-items:center;justify-content:center;flex-shrink:0">
            <i class="fa-solid fa-syringe" style="color:var(--signal);font-size:18px"></i>
          </div>
          <div>
            <div style="font-size:15px;font-weight:600;color:var(--text)">GLP-1 Taper Tracker</div>
            <div style="font-size:12px;color:var(--text-3);margin-top:1px">Know exactly when hunger will spike — before it does</div>
          </div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:14px">
          <div style="background:var(--surface);border-radius:8px;padding:10px;text-align:center">
            <div style="font-size:18px;font-weight:700;color:var(--text)">7-day</div>
            <div style="font-size:10px;color:var(--text-3);margin-top:2px">hunger forecast</div>
          </div>
          <div style="background:var(--surface);border-radius:8px;padding:10px;text-align:center">
            <div style="font-size:18px;font-weight:700;color:var(--text)">Live %</div>
            <div style="font-size:10px;color:var(--text-3);margin-top:2px">drug level</div>
          </div>
          <div style="background:var(--surface);border-radius:8px;padding:10px;text-align:center">
            <div style="font-size:18px;font-weight:700;color:var(--text)">Auto</div>
            <div style="font-size:10px;color:var(--text-3);margin-top:2px">next dose alert</div>
          </div>
        </div>
        <button onclick="document.getElementById('taperSetupForm').style.display='block';this.style.display='none'"
          class="cp-primary-btn" style="width:100%;justify-content:center">
          <i class="fa-solid fa-plus"></i> Set Up Taper Tracker
        </button>
        ${_taperSetupForm()}
      </div>
    </div>`;
  }

  const med        = plan.medication === 'tirzepatide' ? 'Tirzepatide' : 'Semaglutide';
  const brand      = plan.medication === 'tirzepatide' ? 'Zepbound / Mounjaro' : 'Wegovy / Ozempic';
  const pct        = plan.pct_active != null ? Math.round(plan.pct_active) : null;
  const days       = plan.days_since_dose;
  const nextDose   = plan.next_dose_date
    ? new Date(plan.next_dose_date + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    : '—';
  const freq       = plan.frequency_days || 7;
  const taperLabel = plan.taper_type === 'stepdown' ? 'Step-down protocol' : 'Stretch-out protocol';

  const pctColor = pct == null ? 'var(--text-3)'
    : pct > 70 ? 'var(--ok)' : pct > 40 ? 'var(--amber)' : 'var(--signal)';
  const hungerLabel = pct == null ? '' : pct > 70 ? 'Well suppressed' : pct > 40 ? 'Food noise rising' : 'Ghrelin surge zone';
  const hungerBar = _taperHungerBar(plan.hunger_forecast);

  return `<div class="hv-section">
    <div style="background:var(--surface-2);border:1px solid var(--border);border-radius:14px;overflow:hidden">

      <!-- Header bar -->
      <div style="padding:14px 18px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px">
        <i class="fa-solid fa-syringe" style="color:var(--signal);font-size:15px"></i>
        <span style="font-weight:600;font-size:14px;color:var(--text)">Taper Tracker</span>
        <span style="font-size:11px;color:var(--text-3);margin-left:4px">— ${taperLabel}</span>
        <button onclick="stopTaperPlan()" title="Stop tracking"
          style="margin-left:auto;background:none;border:none;color:var(--text-3);cursor:pointer;font-size:12px;padding:2px 6px">
          ✕ Stop
        </button>
      </div>

      <!-- Stats row -->
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;padding:16px 18px;gap:12px">
        <div>
          <div style="font-size:11px;color:var(--text-3);margin-bottom:4px;text-transform:uppercase;letter-spacing:.04em">Medication</div>
          <div style="font-size:14px;font-weight:600;color:var(--text)">${med}</div>
          <div style="font-size:11px;color:var(--text-3);margin-top:1px">${brand}</div>
        </div>
        <div>
          <div style="font-size:11px;color:var(--text-3);margin-bottom:4px;text-transform:uppercase;letter-spacing:.04em">Drug level</div>
          <div style="font-size:26px;font-weight:700;color:${pctColor};line-height:1">${pct != null ? pct + '%' : '—'}</div>
          <div style="font-size:11px;color:var(--text-3);margin-top:2px">${days != null ? 'Day ' + days + ' of ' + freq : ''} · ${hungerLabel}</div>
        </div>
        <div>
          <div style="font-size:11px;color:var(--text-3);margin-bottom:4px;text-transform:uppercase;letter-spacing:.04em">Next dose</div>
          <div style="font-size:18px;font-weight:600;color:var(--text)">${nextDose}</div>
          <div style="font-size:11px;color:var(--text-3);margin-top:2px">Every ${freq} days</div>
        </div>
      </div>

      <!-- Hunger forecast bar -->
      ${hungerBar ? `<div style="padding:0 18px 14px">${hungerBar}</div>` : ''}

      <!-- Action button -->
      <div style="padding:12px 18px;border-top:1px solid var(--border)">
        <button class="cp-primary-btn" style="width:100%;justify-content:center" onclick="logTaperDose()">
          <i class="fa-solid fa-syringe"></i> Took dose today — update tracker
        </button>
      </div>
    </div>
  </div>`;
}

async function saveTaperPlan() {
  const h = await headers(); if (!h) return;
  const body = {
    action:          'save',
    medication:      document.getElementById('taperMed')?.value || 'semaglutide',
    taper_type:      document.getElementById('taperType')?.value || 'stretch',
    current_dose:    parseFloat(document.getElementById('taperDose')?.value) || null,
    frequency_days:  parseInt(document.getElementById('taperFreq')?.value) || 7,
    last_dose_date:  document.getElementById('taperLastDose')?.value || new Date().toISOString().slice(0,10),
    target_weeks:    parseInt(document.getElementById('taperWeeks')?.value) || 9,
  };
  try {
    const { ok, data } = await apiJson('/api/taper', { method: 'POST', headers: h, body: JSON.stringify(body) });
    if (ok) { toast('Taper plan started ✓'); loadHealthView(); }
    else     toast('Could not save plan. Try again.', 'err');
  } catch { toast('Error saving plan.', 'err'); }
}

async function logTaperDose() {
  const h = await headers(); if (!h) return;
  try {
    const { ok } = await apiJson('/api/taper', { method: 'POST', headers: h,
      body: JSON.stringify({ action: 'log_dose' }) });
    if (ok) { toast('Dose logged ✓ — next dose date updated'); loadHealthView(); }
    else     toast('Could not log dose.', 'err');
  } catch { toast('Error logging dose.', 'err'); }
}

async function stopTaperPlan() {
  if (!confirm('Stop tracking this taper plan?')) return;
  const h = await headers(); if (!h) return;
  try {
    const { ok } = await apiJson('/api/taper', { method: 'POST', headers: h,
      body: JSON.stringify({ action: 'stop' }) });
    if (ok) { toast('Taper plan stopped'); _taperPlan = null; loadHealthView(); }
  } catch { toast('Error.', 'err'); }
}

function buildHealthViewHTML(markers, dashboard) {
  const abnormal = markers.filter(m => m.status === "HIGH" || m.status === "LOW");
  const trending = dashboard?.trends || [];
  // Use dashboard cliff_alerts if available; fall back to client-side detection from markers
  const _dashAlerts = dashboard?.cliff_alerts || [];
  const _clientAlerts = _computeCliffAlerts(markers);
  const cliffalerts = _dashAlerts.length > 0 ? _dashAlerts : _clientAlerts;
  const riskLevel = cliffalerts.length > 0 ? "high" : abnormal.length > 2 ? "warn" : "none";
  const riskScore = cliffalerts.length || abnormal.length;
  const riskLabel = riskLevel === "high" ? "Active Rebound Signals" : riskLevel === "warn" ? "Needs Attention" : "No Cliff Signals";
  const riskDesc = riskLevel === "high" ? `${cliffalerts.length} threshold${cliffalerts.length > 1 ? "s" : ""} exceeded.`
    : riskLevel === "warn" ? `${abnormal.length} markers outside normal range.`
    : "All monitored markers within normal range.";

  let html = buildTaperHTML(_taperPlan);
  html += `<div class="hv-section"><div class="hv-heading"><i class="fa-solid fa-triangle-exclamation"></i>Cliff Risk</div>
    <div class="cliff-card risk-${riskLevel}">
      <div><div class="cliff-num risk-${riskLevel}">${riskLevel === "none" ? "✓" : riskScore}</div></div>
      <div class="cliff-detail"><h3>${riskLabel}</h3><p>${riskDesc}</p>
      ${riskLevel !== "none" ? `<button class="alert-cta" data-ask="Run a full cliff risk analysis on my stored data. What are my urgent signals?">Ask Curabook →</button>` : ""}
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

  if (_cachedMemories.length > 0) {
    html += `<div class="hv-section"><div class="hv-heading"><i class="fa-solid fa-brain"></i>Curabook Health Memory (${_cachedMemories.length} facts)</div><div class="alert-feed">`;
    _cachedMemories.slice(0, 5).forEach(fact => {
      html += `<div class="alert-item ok" style="border-left-color:var(--signal);background:var(--signal-dim)">
        <div class="alert-desc" style="color:var(--text)">${esc(fact)}</div></div>`;
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
    const { ok, data } = await apiJson("/api/lab-reports", { headers: h });
    if (!ok || !data?.reports?.length) {
      list.innerHTML = `<div class="hv-empty"><i class="fa-solid fa-file-medical"></i>No lab reports yet.<br><button class="hv-cta-btn" onclick="handleUploadClick()"><i class="fa-solid fa-upload"></i> Upload First Report</button></div>`;
      return;
    }
    list.innerHTML = data.reports.map(r => {
      const date = r.report_date ? new Date(r.report_date).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "";
      const markerBadge = r.marker_count > 0
        ? `<span class="report-tag info">${r.marker_count} marker${r.marker_count !== 1 ? 's' : ''}</span>`
        : `<span class="report-tag info">Lab</span>`;
      const abnormalBadge = r.abnormal_count > 0
        ? `<span class="report-tag warn">${r.abnormal_count} abnormal</span>`
        : '';
      return `<div class="report-card">
        <div class="report-icon"><i class="fa-solid fa-file-medical-alt"></i></div>
        <div class="report-meta">
          <div class="report-name">${esc(r.filename || "Lab Report")}</div>
          <div class="report-date">${date}</div>
          <div class="report-tags">${markerBadge}${abnormalBadge}</div>
        </div>
        <button class="report-ask-btn" onclick="askAboutReport('${esc(r.filename || "report")}')">Ask Curabook →</button>
      </div>`;
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
function showChat()    { el("welcomeScreen")?.classList.add("hidden"); el("chatDisplay")?.classList.remove("hidden"); }

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

// ── Behavioral data parsing ────────────────────────────────────────────────
function _parseUserBehavioralData(text) {
  const metrics = {};
  const today = new Date().toISOString().slice(0, 10);
  const reportingKeywords = ["i ate", "i had", "i slept", "i walked", "i did", "today", "this morning", "last night", "logged", "tracked"];
  const lowerText = text.toLowerCase();
  const isReporting = reportingKeywords.some(kw => lowerText.includes(kw));
  if (!isReporting) return {};

  for (const pattern of [
    /(\d{2,3})\s*(?:g|grams?)\s+(?:of\s+)?protein/i,
    /protein[:\s]+(\d{2,3})\s*(?:g|grams?)/i,
  ]) {
    const m = text.match(pattern);
    if (m) { const v = parseInt(m[1]); if (v >= 10 && v <= 400) { metrics.protein = { value: v, unit: "g", date: today }; break; } }
  }
  for (const pattern of [/(\d{3,6})\s+steps/i, /steps[:\s]+(\d{3,6})/i, /walked?\s+(\d{3,6})/i]) {
    const m = text.match(pattern);
    if (m) { const v = parseInt(m[1]); if (v >= 0 && v <= 100000) { metrics.steps = { value: v, unit: "steps", date: today }; break; } }
  }
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
}

// ══════════════════════════════════════════════════════════════════════════════
// SEND MESSAGE
// ══════════════════════════════════════════════════════════════════════════════
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
  return _SHIELD_REPLY_KEYWORDS.some(kw => reply.toLowerCase().includes(kw));
}

function _replyHasMemoryData(reply) {
  return _MEMORY_REPLY_KEYWORDS.some(kw => reply.toLowerCase().includes(kw));
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

    const parsedMetrics = _parseUserBehavioralData(text);
    if (Object.keys(parsedMetrics).length > 0) {
      await _logBehavioralMetrics(parsedMetrics);
      const p = parsedMetrics.protein?.value || parseFloat(el("inputProtein")?.value) || 0;
      const s = parsedMetrics.steps?.value || parseFloat(el("inputSteps")?.value) || 0;
      const sl = parsedMetrics.sleep?.value || parseFloat(el("inputSleep")?.value) || 0;
      if (parsedMetrics.protein && el("inputProtein")) el("inputProtein").value = p;
      if (parsedMetrics.steps && el("inputSteps")) el("inputSteps").value = s;
      if (parsedMetrics.sleep && el("inputSleep")) el("inputSleep").value = sl;
      renderShield(p, s, sl, new Date().toISOString().slice(0, 10));
    }

    if (_uploads.length) {
      const isPro = _userPlan === "pro" || _userPlan === "annual" || _userPlan === "monthly" || _userPlan === "trial";
      const isMealPhoto = _uploads[0]?._isMealPhoto === true;

      // ── Lab report / document: use /analyze pipeline ─────────────────
      // (Meal photos are handled by _queueMealUpload which sets _docCtx directly)
      if (!isPro) {
        _isSending = false;
        setSendingState(false);
        showUpgradeModal("upload");
        return;
      }
      const lr = appendTyping(); updateTyping(lr, "📄 Processing file...");
      const result = await processUpload(_uploads[0]);
      lr?.remove();
      if (result?.document_text) {
        _uploads = []; clearFilePreview();
        _docCtx = { text: result.document_text, hasDoc: true, filename: result.filename || "" };
        toast(`${result.filename || "File"} analyzed ✓`);
        if (!isPro) { _renderPlanBadge(); }
        setTimeout(() => _refreshMemoryCache(), 2000);
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

    // Build payload BEFORE clearing _docCtx
    const _wasMealPhoto = _docCtx?.isMealPhoto === true;
    const payload = {
      conversation_id: _convId,
      message:         text,
      has_documents:   _docCtx?.hasDoc || false,
      document_text:   _docCtx?.hasDoc ? (_docCtx.text || "") : "",
    };

    // Clear meal photo context AFTER payload is built — only send image once
    if (_wasMealPhoto) {
      _docCtx = { text: null, hasDoc: false, filename: "" };
    }

    let dotCount = 0;
    const typingInterval = setInterval(() => {
      dotCount = (dotCount + 1) % 4;
      updateTyping(botRow, "Curabook is thinking" + ".".repeat(dotCount));
    }, 600);

    const res = await fetch(API + "/chat", { method: "POST", headers: h, body: JSON.stringify(payload) });
    clearInterval(typingInterval);

    if (res.status === 402) {
      const d = await res.json().catch(() => {});
      botRow?.remove();
      showUpgradeModal("upload");
      _isSending = false; setSendingState(false);
      return;
    }

    if (res.status === 401) {
      const refreshed = await handleUnauthorized(); if (!refreshed) throw new Error("Session expired. Please sign in again.");
      const h2 = await headers();
      if (h2) {
        const res2 = await fetch(API + "/chat", { method: "POST", headers: h2, body: JSON.stringify(payload) });
        const txt2 = await res2.text();
        let data2 = null; try { data2 = JSON.parse(txt2); } catch {}
        if (res2.ok && data2?.reply) {
          const cleanReply2 = interceptPHIActions(data2.reply, text);
          updateMsg(botRow, cleanReply2);
          await _postChatActions(cleanReply2, text, data2);
          return;
        }
      }
      throw new Error("Authentication failed. Please refresh the page.");
    }

    const txt = await res.text();
    let data = null; try { data = JSON.parse(txt); } catch (e) {}

    if (res.ok && data?.reply) {
      const cleanReply = interceptPHIActions(data.reply, text);
      updateMsg(botRow, cleanReply);
      await _postChatActions(cleanReply, text, data);
    } else {
      throw new Error(`Server Error (${res.status}): ${txt.slice(0, 150)}`);
    }

  } catch (err) {
    console.error("[PHI] Chat Error:", err);
    if (botRow) {
      updateMsg(botRow, `⚠️ **Connection issue:** ${err.message}\n\n*Please try again. If this persists, refresh the page.*`);
    } else {
      toast(err.message, "err");
    }
  } finally {
    _isSending = false;
    setSendingState(false);
    scrollBottom();
  }
}

async function _postChatActions(reply, userText, responseData) {
  const userMsgs = el("chatDisplay")?.querySelectorAll(".chat-msg.user-msg");
  if (userMsgs?.length === 1 && _convId) renameConversation(_convId, userText);

  if (_replyHasMemoryData(reply) || _replyHasMarkerData(reply)) {
    setTimeout(async () => {
      await _refreshMemoryCache();
      _updateMemoryCountDisplay();
    }, 1500);
  }

  if (_docCtx?.hasDoc && _replyHasMarkerData(reply)) {
    setTimeout(async () => {
      await loadMarkersData();
      await _refreshMemoryCache();
      setTimeout(() => refreshShieldFromBehavioral(), 2000);
      // Re-render health view so cliff signals reflect new markers
      if (el("viewHealth")?.classList.contains("active")) {
        setTimeout(() => loadHealthView(), 2500);
      }
    }, 1000);
  }

  if (_replyHasShieldData(reply)) {
    setTimeout(() => refreshShieldFromBehavioral(), 1500);
  }

  if (responseData?.behavioral_logged) {
    setTimeout(() => refreshShieldFromBehavioral(), 1000);
  }

  if (responseData?.plan) {
    _userPlan = responseData.plan;
    if (responseData.reports_remaining !== undefined) {
      // trial model — ignore per-report counter from backend
    }
    _renderPlanBadge();
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

// ── Meal photo queue — called from app.html handleMealPhoto ──────────────
window._queueMealUpload = function(file) {
  // Read base64 immediately and set _docCtx directly
  // Cannot set custom properties on native File objects — this bypasses that issue
  const reader = new FileReader();
  reader.onload = function(e) {
    const base64 = e.target.result;
    // Set docCtx directly — bypasses _uploads pipeline entirely
    _docCtx = { text: base64, hasDoc: true, filename: 'meal_photo.jpg', isMealPhoto: true };
    _uploads = []; // clear uploads so lab report pipeline not triggered
    clearFilePreview();
    // Pre-fill chat with meal context message
    const inp = el('msgInput') || el('chatInput');
    if (inp) {
      inp.value = 'I just ate this — estimate the protein and log it to my Shield total.';
      inp.dispatchEvent(new Event('input'));
    }
    // Show chat panel
    const panel = document.querySelector('.chat-panel');
    if (panel) panel.classList.add('active');
    if (inp) inp.focus();
    toast('Meal photo ready — tap Send to log protein');
  };
  reader.onerror = () => toast('Could not read photo', 'err');
  reader.readAsDataURL(file);
};

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
    let res = await Promise.race([doUp(s.access_token), new Promise((_, r) => setTimeout(() => r(new Error("timed out")), 60000))]);
    if (res.status === 401) {
      const refreshed = await handleUnauthorized(); if (!refreshed) return null;
      const s2 = await session(); if (s2) res = await doUp(s2.access_token);
    }
    if (res.status === 402) { showUpgradeModal("upload"); return null; }
    if (res.status === 403) {
      _consentsSaved = false; await saveConsents().catch(() => {});
      const s2 = await session(); if (s2) res = await doUp(s2.access_token);
    }
    if (res.status === 413) { toast("File too large (max 20MB).", "err"); return null; }
    if (!res.ok) { const d = await res.json().catch(() => {}); toast(d?.error || `Upload failed (${res.status}).`, "err"); return null; }
    const result = await res.json();
    setTimeout(async () => {
      await loadMarkersData();
      await _refreshMemoryCache();
      setTimeout(() => refreshShieldFromBehavioral(), 2000);
      // If user is on My Health view, refresh it so cliff signals update immediately
      if (el("viewHealth")?.classList.contains("active")) {
        setTimeout(() => loadHealthView(), 2500);
      }
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
    l.textContent = "⚕️ Curabook is an educational wellness tool. Always consult your healthcare provider.";
    elem.appendChild(l);
  }
}

const scrollBottom = () => { const d = el("chatDisplay"); if (d) d.scrollTop = d.scrollHeight; };

// ── Shield ─────────────────────────────────────────────────────────────────
async function autoLoadShield() {
  const h = await headers();
  if (!h) { renderShield(0, 0, 0, null); return; }
  const today = new Date().toISOString().slice(0, 10);

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
      if (Array.isArray(d.cliff_alerts)) _renderStartupAlerts(d.cliff_alerts);
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
        _applyShieldValues(get("protein"), get("steps"), get("sleep"), today);
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
    _applyShieldValues(get("protein"), get("steps"), get("sleep"), today);
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
  } catch (e) { console.warn("[SHIELD] Refresh failed:", e.message); }
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
  setRing("ringProtein",  440, pP);
  setRing("ringMovement", 346, mP);
  setRing("ringRecovery", 258, rP);
  setText("shieldScore", sc + "%");
  setText("shieldBadge", sc + "%");
  setText("proteinLegend",  p > 0 ? `${p}g / ${_proteinTarget}g (${pP}%)` : `Target: ${_proteinTarget}g — not logged yet`);
  setText("movementLegend", s > 0 ? `${s.toLocaleString()} steps (${mP}%)` : "Steps — not logged yet");
  setText("recoveryLegend", sl > 0 ? `${sl}h sleep (${rP}%)` : "Sleep — not logged yet");
  setBarPct("proteinBar",  pP);
  setBarPct("movementBar", mP);
  setBarPct("recoveryBar", rP);
}

function setRing(id, circ, pct) {
  const r = el(id);
  if (r) { r.style.strokeDasharray = circ; r.style.strokeDashoffset = circ - (circ * Math.max(0, Math.min(100, pct)) / 100); }
}
function setBarPct(id, pct) { const b = el(id); if (b) b.style.width = Math.max(0, pct) + "%"; }

// ── PHI action command interceptor ───────────────────────────────────────
function interceptPHIActions(text, userMessage) {
  if (!text) return text;
  let proteinValue = null;

  // ── Method 1: Explicit JSON action from backend ───────────────────────
  const actionMatch = text.match(/\{"action"\s*:\s*"log_protein"\s*,\s*"value"\s*:\s*([\d.]+)\s*\}/);
  if (actionMatch) {
    proteinValue = parseFloat(actionMatch[1]);
    text = text.replace(actionMatch[0], '').trim();
  }

  // ── Method 2: Fallback — scan PHI reply for protein logging statements ─
  if (!proteinValue && userMessage) {
    const msgLower = (userMessage || '').toLowerCase();
    const replyLower = text.toLowerCase();

    // User specified grams — only if message has explicit log intent
    const hasLogIntent = /\b(add|log|track|record|save)\b/.test(msgLower);
    const gramMsg = msgLower.match(/(\d+(?:\.\d+)?)\s*(?:g|gram|grams|gm)/);
    if (gramMsg && hasLogIntent) proteinValue = parseFloat(gramMsg[1]);

    // Never auto-log from PHI example lists (contains bullet points with ~Xg)
    const isExampleResponse = /bullet|\*\s*\w.*~?\d+g|common estimate|for example|typically|on average/i.test(text);
    if (isExampleResponse) proteinValue = null;

    // User said yes/log it and PHI mentioned a number
    const affirmatives = ['yes','log it','sure','ok','okay','add it','add','yep','yup','log','correct','log protein','add protein','add to shield','log to shield'];
    const isAffirmative = affirmatives.some(a => msgLower === a || msgLower.startsWith(a + ' ') || msgLower.includes(a));

    if (!proteinValue && isAffirmative) {
      // Extract from PHI reply — look for "70 grams", "logged 70", "adding 70g" etc
      const patterns = [
        /logged\s+protein[:\s]+([\d.]+)/,
        /log(?:ged|ging)?\s+([\d.]+)\s*g/,
        /add(?:ed|ing)?\s+([\d.]+)\s*g/,
        /([\d.]+)\s*g(?:ram)?s?\s+(?:of\s+)?protein/,
        /protein[:\s]+([\d.]+)\s*g/,
        /approximately\s+([\d.]+)\s*g/,
        /around\s+([\d.]+)\s*g/,
        /about\s+([\d.]+)\s*g/,
        /estimate[d]?\s+([\d.]+)\s*g/,
      ];
      for (const p of patterns) {
        const m = replyLower.match(p);
        if (m) { proteinValue = parseFloat(m[1]); break; }
      }
    }

    // PHI says "logged protein: 70" in its own message — always log
    const loggedMatch = replyLower.match(/logged\s+protein[:\s]+([\d.]+)/);
    if (loggedMatch) proteinValue = parseFloat(loggedMatch[1]);
  }

  // ── Execute if we found a value ───────────────────────────────────────
  if (proteinValue && proteinValue > 1 && proteinValue < 500) {
    setTimeout(() => logProteinFromChat(proteinValue), 300);
  }

  return text.trim();
}

// ── Log protein directly from chat confirmation ──────────────────────────
async function logProteinFromChat(grams) {
  const inp = el('inputProtein');
  if (inp) {
    // ADD to existing value — do not overwrite
    const current = parseFloat(inp.value) || 0;
    const newTotal = Math.round((current + grams) * 10) / 10;
    inp.value = newTotal;
    // Show toast confirming the addition
    toast(`+${grams}g protein logged — ${newTotal}g total today ✓`);
  }
  await updateShield();
  // Flash the shield section to show update
  const section = document.querySelector('.cp-section');
  if (section) {
    section.style.transition = 'background .3s';
    section.style.background = 'rgba(0,212,200,.08)';
    setTimeout(() => section.style.background = '', 800);
  }
}

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
  setText("proteinNum", _proteinTarget);
  setText("proteinCaption", `${gw} lbs × 0.545 = ${_proteinTarget}g/day`);
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

// Returns cliff alert objects (same logic as runCliffDetection but pure/no DOM writes)
function _computeCliffAlerts(markers) {
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
      if (pct >= 15) alerts.push({ headline: `Glucose rebound +${pct.toFixed(0)}%`, detail: `${r[0]._v} → ${r[r.length - 1]._v} mg/dL`, severity: "high" });
      else if (pct >= 10) alerts.push({ headline: `Glucose rising +${pct.toFixed(0)}%`, detail: "Approaching 15% threshold.", severity: "warn" });
    }
  }
  const hk = Object.keys(grouped).find(k => /hba1c/.test(k));
  if (hk) {
    const r = grouped[hk].sort((a, b) => a.date < b.date ? -1 : 1);
    for (let i = 1; i < r.length; i++) {
      const d = r[i]._v - r[i - 1]._v;
      if (d >= 0.25) { alerts.push({ headline: `HbA1c rebound +${d.toFixed(2)}%`, detail: `${r[i - 1]._v}% → ${r[i]._v}%`, severity: "high" }); break; }
    }
  }
  markers.filter(m => m.status === "HIGH" && !/glucose/i.test(m.marker_name)).slice(0, 2)
    .forEach(m => alerts.push({ headline: `${m.marker_name} HIGH`, detail: `${m.value} ${m.unit || ""}`, severity: "warn" }));
  return alerts;
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
  const btn = el("logNoiseBtn");
  if (btn?.disabled) return; // Prevent double-clicks
  
  const val = parseInt(el("noiseSlider")?.value || 5);
  const h = await headers(); 
  if (!h) { toast("Sign in to log.", "info"); return; }

  // 1. Lock the button UI
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Logging...';
  }

  try {
    await apiFetch("/api/behavioral-logs", { 
      method: "POST", 
      headers: h, 
      body: JSON.stringify({ 
        date: new Date().toISOString().slice(0, 10), 
        metric_name: "food_noise", 
        value: val, 
        unit: "1-10" 
      }) 
    });
    toast(`Food noise ${val}/10 logged ✓`, "ok");
  } catch (err) {
    toast("Failed to log food noise.", "err");
  } finally {
    // 2. Release the lock and restore UI
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = 'Log Noise Level'; // Or whatever your original button text was
    }
  }
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
  let out = `Curabook — Chat Export\n${"=".repeat(40)}\n\n`;
  msgs.forEach(m => {
    const role = m.classList.contains("user-msg") ? "You" : "Curabook";
    out += `${role}:\n${m.querySelector(".msg-body")?.innerText?.trim() || ""}\n\n`;
  });
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([out], { type: "text/plain" })), download: `phi-chat-${Date.now()}.txt` });
  a.click(); closeUserMenu(); toast("Chat exported");
}

// ══════════════════════════════════════════════════════════════════════════════
// FIX-3: FEEDBACK SYSTEM — Full NPS (1-10) + category + smooth animation
// ══════════════════════════════════════════════════════════════════════════════
function initFeedback() {
  const style = document.createElement("style");
  style.textContent = `
    .feedback-modal-overlay {
      position:fixed; inset:0; background:rgba(0,0,0,.7);
      z-index:9990; display:none; align-items:flex-end;
      justify-content:center; padding:0 0 80px;
    }
    @media(min-width:640px) { .feedback-modal-overlay { align-items:center; padding:20px; } }
    .feedback-modal-overlay.open { display:flex; }
    .feedback-modal {
      background:var(--surface); border:1px solid var(--border-2);
      border-radius:20px 20px 0 0; padding:24px 20px;
      width:100%; max-width:440px; position:relative;
      animation:slideUp .25s ease; max-height:90vh; overflow-y:auto;
    }
    @media(min-width:640px) { .feedback-modal { border-radius:20px; padding:28px; } }
    .feedback-close {
      position:absolute; top:14px; right:14px; width:32px; height:32px;
      border-radius:8px; border:none; background:none; color:var(--text-3);
      font-size:.9rem; cursor:pointer; display:flex; align-items:center; justify-content:center; transition:all .15s;
    }
    .feedback-close:hover { background:var(--surface-2); color:var(--text); }
    .feedback-header { display:flex; align-items:center; gap:12px; margin-bottom:20px; }
    .feedback-title { font-size:.95rem; font-weight:700; margin-bottom:2px; }
    .feedback-sub { font-size:.74rem; color:var(--text-3); }
    .feedback-nps-label { font-size:.74rem; font-weight:600; color:var(--text-2); margin-bottom:8px; }
    .feedback-nps-row { display:flex; gap:4px; margin-bottom:4px; }
    .nps-btn {
      flex:1; min-height:36px; border:1.5px solid var(--border);
      border-radius:6px; background:var(--surface-2); color:var(--text-3);
      font-size:.78rem; font-weight:600; cursor:pointer; font-family:var(--sans); transition:all .15s;
    }
    .nps-btn:hover { border-color:var(--signal); color:var(--signal); }
    .nps-btn.selected { color:#0a0b0e; font-weight:700; }
    .feedback-nps-captions { display:flex; justify-content:space-between; font-size:.64rem; color:var(--text-3); margin-bottom:14px; }
    .feedback-category-row { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:14px; }
    .cat-btn {
      padding:6px 12px; border:1.5px solid var(--border); border-radius:20px;
      background:var(--surface-2); color:var(--text-2); font-size:.74rem; font-weight:500;
      cursor:pointer; font-family:var(--sans); transition:all .15s; min-height:32px;
    }
    .cat-btn:hover, .cat-btn.selected { border-color:var(--signal); color:var(--signal); background:var(--signal-dim); }
    .feedback-textarea {
      width:100%; padding:10px 12px; border:1.5px solid var(--border);
      border-radius:10px; background:var(--surface-2); color:var(--text);
      font-size:.85rem; font-family:var(--sans); resize:none; outline:none;
      transition:border-color .2s; margin-bottom:10px; min-height:80px;
    }
    .feedback-textarea:focus { border-color:var(--signal); }
    .feedback-textarea::placeholder { color:var(--text-3); }
    .feedback-footer { display:flex; align-items:center; justify-content:space-between; }
    .feedback-char-count { font-size:.68rem; color:var(--text-3); }
    .feedback-submit {
      padding:9px 20px; background:var(--signal); color:#0a0b0e;
      border:none; border-radius:8px; font-size:.84rem; font-weight:700;
      cursor:pointer; font-family:var(--sans); display:flex;
      align-items:center; gap:6px; transition:all .15s; min-height:38px;
    }
    .feedback-submit:hover { background:var(--signal-2); }
    .feedback-submit:disabled { opacity:.6; cursor:not-allowed; }
    .feedback-success { display:none; flex-direction:column; align-items:center; text-align:center; padding:20px 0; animation:fadeUp .3s ease; }
    .feedback-success-icon { font-size:2.5rem; margin-bottom:12px; }
    .feedback-success strong { font-size:1rem; margin-bottom:6px; display:block; }
    .feedback-success p { font-size:.82rem; color:var(--text-2); }
  `;
  document.head.appendChild(style);

  const modal = document.createElement("div");
  modal.id = "feedbackModal";
  modal.className = "feedback-modal-overlay";
  modal.setAttribute("aria-hidden", "true");
  modal.innerHTML = `
    <div class="feedback-modal" role="dialog" aria-label="Send Feedback">
      <button class="feedback-close" id="feedbackClose" aria-label="Close"><i class="fa-solid fa-xmark"></i></button>
      <div class="feedback-header">
        <div style="width:40px;height:40px;border-radius:10px;background:var(--signal-dim);border:1px solid rgba(0,212,200,.2);display:flex;align-items:center;justify-content:center;color:var(--signal);font-size:1.1rem;flex-shrink:0;"><i class="fa-regular fa-comment-dots"></i></div>
        <div><h3 class="feedback-title">How's Curabook working for you?</h3><p class="feedback-sub">Your feedback shapes what we build next.</p></div>
      </div>
      <div class="feedback-nps-label">How likely are you to recommend Curabook? (1 = not at all, 10 = absolutely)</div>
      <div class="feedback-nps-row" id="feedbackNpsRow">
        ${[...Array(10)].map((_, i) => `<button class="nps-btn" data-val="${i+1}">${i+1}</button>`).join("")}
      </div>
      <div class="feedback-nps-captions"><span>Not likely</span><span>Very likely</span></div>
      <div class="feedback-category-row" id="feedbackCategories">
        <button class="cat-btn" data-cat="chat">💬 Chat</button>
        <button class="cat-btn" data-cat="reports">📋 Reports</button>
        <button class="cat-btn" data-cat="shield">🛡 Shield</button>
        <button class="cat-btn" data-cat="memory">🧠 Memory</button>
        <button class="cat-btn" data-cat="ui">✨ Design</button>
        <button class="cat-btn" data-cat="speed">⚡ Speed</button>
        <button class="cat-btn" data-cat="bug">🐛 Bug</button>
        <button class="cat-btn" data-cat="idea">💡 Idea</button>
        <button class="cat-btn" data-cat="payment">💳 Pricing</button>
      </div>
      <textarea id="feedbackText" class="feedback-textarea"
        placeholder="What do you love? What's confusing? What's missing? Be brutally honest."
        rows="3" maxlength="1000"></textarea>
      <div class="feedback-footer">
        <span class="feedback-char-count" id="feedbackCharCount">0 / 1000</span>
        <button class="feedback-submit" id="feedbackSubmit"><i class="fa-solid fa-paper-plane"></i> Send</button>
      </div>
      <div id="feedbackSuccess" class="feedback-success">
        <div class="feedback-success-icon">🎉</div>
        <strong>Thank you!</strong>
        <p>We read every message. Your input directly shapes Curabook.</p>
      </div>
    </div>`;
  document.body.appendChild(modal);

  let selectedNps = 0, selectedCategory = "";

  el("feedbackClose")?.addEventListener("click", () => closeFeedback());
  modal.addEventListener("click", e => { if (e.target === modal) closeFeedback(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeFeedback(); });

  modal.querySelectorAll(".nps-btn").forEach(b => b.addEventListener("click", () => {
    selectedNps = parseInt(b.dataset.val);
    modal.querySelectorAll(".nps-btn").forEach(rb => { rb.classList.remove("selected"); rb.style.background = ""; rb.style.borderColor = ""; });
    b.classList.add("selected");
    const color = selectedNps <= 6 ? "var(--danger)" : selectedNps <= 8 ? "var(--amber)" : "var(--ok)";
    b.style.background = color; b.style.borderColor = color;
  }));

  modal.querySelectorAll(".cat-btn").forEach(b => b.addEventListener("click", () => {
    selectedCategory = b.dataset.cat;
    modal.querySelectorAll(".cat-btn").forEach(cb => cb.classList.remove("selected"));
    b.classList.add("selected");
  }));

  const ta = el("feedbackText"); const cc = el("feedbackCharCount");
  ta?.addEventListener("input", () => { if (cc) cc.textContent = `${ta.value.length} / 1000`; });

  el("feedbackSubmit")?.addEventListener("click", () => _submitFeedback(selectedNps, selectedCategory));
}

function openFeedback() {
  const modal = el("feedbackModal"); if (!modal) return;
  modal.setAttribute("aria-hidden", "false"); modal.classList.add("open");
  document.body.style.overflow = "hidden";
  el("feedbackSuccess").style.display = "none";
  el("feedbackText").value = "";
  if (el("feedbackCharCount")) el("feedbackCharCount").textContent = "0 / 1000";
  ["feedbackNpsRow","feedbackCategories","feedbackText"].forEach(id => { const e = el(id); if (e) e.style.display = ""; });
  const footer = document.querySelector(".feedback-footer"); if (footer) footer.style.display = "";
  document.querySelector(".feedback-nps-label")?.style && (document.querySelector(".feedback-nps-label").style.display = "");
  document.querySelector(".feedback-nps-captions")?.style && (document.querySelector(".feedback-nps-captions").style.display = "");
  document.querySelector(".feedback-header")?.style && (document.querySelector(".feedback-header").style.display = "");
  modal.querySelectorAll(".nps-btn,.cat-btn").forEach(b => { b.classList.remove("selected"); b.style.background = ""; b.style.borderColor = ""; });
  const submitBtn = el("feedbackSubmit");
  if (submitBtn) { submitBtn.disabled = false; submitBtn.innerHTML = '<i class="fa-solid fa-paper-plane"></i> Send'; }
}

function closeFeedback() {
  const modal = el("feedbackModal"); if (!modal) return;
  modal.setAttribute("aria-hidden", "true"); modal.classList.remove("open");
  document.body.style.overflow = "";
}

async function _submitFeedback(nps, category) {
  const text = el("feedbackText")?.value?.trim() || "";
  const submitBtn = el("feedbackSubmit");
  if (!nps && !text) { toast("Please rate or write a message first.", "info"); return; }
  if (submitBtn) { submitBtn.disabled = true; submitBtn.innerHTML = '<i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i> Sending…'; }

  try {
    const h = await headers();
    if (h) {
      await fetch(API + "/api/feedback", {
        method: "POST", headers: h,
        body: JSON.stringify({ rating: nps, nps_score: nps, category, text, url: window.location.href, user_email: _user?.email || "anonymous", timestamp: new Date().toISOString() }),
        signal: AbortSignal.timeout(8000)
      });
    }
  } catch (e) {}

  const successEl = el("feedbackSuccess");
  if (successEl) successEl.style.display = "flex";
  ["feedbackNpsRow","feedbackCategories","feedbackText"].forEach(id => { const e = el(id); if (e) e.style.display = "none"; });
  const footer = document.querySelector(".feedback-footer"); if (footer) footer.style.display = "none";
  document.querySelector(".feedback-nps-label")?.style && (document.querySelector(".feedback-nps-label").style.display = "none");
  document.querySelector(".feedback-nps-captions")?.style && (document.querySelector(".feedback-nps-captions").style.display = "none");
  document.querySelector(".feedback-header")?.style && (document.querySelector(".feedback-header").style.display = "none");
  setTimeout(() => closeFeedback(), 2800);
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
const el       = id => document.getElementById(id);
const esc      = s  => String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const setText  = (id, v) => { const e = el(id); if (e) e.textContent = v; };
const setIcon  = (id, c) => { const e = el(id); if (e) e.className = `fa-solid ${c}`; };
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
  document.querySelectorAll(".nav-item[data-view]").forEach(btn =>
    btn.addEventListener("click", () => { switchView(btn.dataset.view); closeSidebar(); })
  );
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

  el("upgradeBtn")?.addEventListener("click", () => showUpgradeModal("manual"));

  el("historyList")?.addEventListener("click", e => {
    const del  = e.target.closest(".hist-del[data-del]");
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

  document.querySelectorAll(".suggestion-chips .chip").forEach(c =>
    c.addEventListener("click", () => { if (c.dataset.q) sendMessage(c.dataset.q); })
  );
  el("chatDisplay")?.addEventListener("click", e => {
    const chip = e.target.closest(".chip[data-q]"); if (chip?.dataset.q) { sendMessage(chip.dataset.q); return; }
    const cta  = e.target.closest("[data-ask]"); if (cta?.dataset.ask) sendMessage(cta.dataset.ask);
  });

  const fi = el("fileInput");
  fi?.addEventListener("change", handleFileSelect);

  ["attachTopBtn", "attachInputBtn", "uploadNudgeBtn", "reportsUploadBtn"].forEach(id => {
    el(id)?.addEventListener("click", () => handleUploadClick());
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
    if (e.key === "Escape") { closeSidebar(); closeUserMenu(); closeCockpit(); closeUpgradeModal(); closeFeedback(); }
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

  window.sendMessage   = sendMessage;
  window.openFeedback  = openFeedback;
  window.closeFeedback = closeFeedback;
});