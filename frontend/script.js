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
 * FIX-4: RAZORPAY INTERNATIONAL PAYMENT TIERS
 *   Free tier: 1 report upload, unlimited chat.
 *   Pro tier: unlimited reports, full marker memory, advocacy briefs.
 *   Razorpay checkout flow with proper signature verification.
 *   Upgrade modal shown when free tier limit is hit.
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
let _consentsSaved = false;
let _consentsPromise = null;
let _redirecting   = false;

// FIX-4: Payment state
let _userPlan         = "free";
let _reportsRemaining = 1;

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
    // FIX-1: Clear ALL auth-related localStorage keys on sign out
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
// The key insight: we never cache "already initialized" across page loads.
// Every page load must validate the session and call onSignIn().
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

    // FIX-1: Listen for auth state changes
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
        // Update stored token for performance_patch
        try { localStorage.setItem("phi_user_id", _user.id); } catch (e) {}
        return;
      }

      // Don't handle SIGNED_IN from onAuthStateChange if boot() already handled it
      // This prevents double-initialization
    });

    // FIX-1: PRIMARY init — getSession() on EVERY page load, no flag checks
    const { data: { session } } = await _sb.auth.getSession();
    if (session?.user) {
      await _runOnSignIn(session.user, session);
    } else {
      // Check for OAuth hash in URL
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

    // FIX-1: Handle page becoming visible after being hidden (tab switching)
    // Re-validate session silently
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

// FIX-1: Single clean entry point for sign-in initialization
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
// ON SIGN IN — Complete init, always runs on every page load
// ══════════════════════════════════════════════════════════════════════════════
async function onSignIn(user, session) {
  console.log("[AUTH] onSignIn for", user.email?.slice(0, 8));

  _shieldLoaded = false;
  _shieldRetries = 0;
  _user = user;

  // FIX-1: Store user_id AND access_token for performance_patch
  try {
    localStorage.setItem("phi_user_id", user.id);
    if (session?.access_token) {
      // Store in the format performance_patch expects
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

  // FIX-1: Parallel load — all data fetches run simultaneously
  await Promise.allSettled([
    saveConsents().catch(e => console.warn("[CONSENT]", e)),
    loadHistory(),
    loadMarkersData(),
    loadPaymentStatus(),
    _refreshMemoryCache(),  // FIX-2: pre-load memories on startup
  ]);

  await autoLoadShield();

  // Restore goal weight
  const gw = localStorage.getItem("phi_goal_wt");
  if (gw) {
    if (el("inputGoalWt")) el("inputGoalWt").value = gw;
    if (el("proteinInput")) el("proteinInput").value = gw;
    calcProteinDisplay(parseFloat(gw), false);
  }

  // Show memory count in cockpit
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
  // Show memory count badge in cockpit if element exists
  const badge = el("memoryCountBadge");
  if (badge) {
    badge.textContent = _memoryCount > 0 ? `${_memoryCount} facts stored` : "No facts yet";
    badge.style.color = _memoryCount > 0 ? "var(--ok)" : "var(--text-3)";
  }
}

// ── FIX-4: Payment status ─────────────────────────────────────────────────
async function loadPaymentStatus() {
  const h = await headers();
  if (!h) return;
  try {
    const { ok, data } = await apiJson("/api/payment/status", { headers: h });
    if (ok && data) {
      _userPlan = data.plan || "free";
      _reportsRemaining = data.reports_remaining ?? 1;
      _renderPlanBadge();
    }
  } catch (e) {
    console.warn("[PAYMENT] Status load error:", e);
  }
}

function _renderPlanBadge() {
  const planEl = el("userPlan");
  if (planEl) {
    const isPro = _userPlan === "pro" || _userPlan === "annual";
    planEl.textContent = isPro ? "PHI Pro ✦" : "PHI Free";
    planEl.style.color = isPro ? "var(--signal)" : "var(--text-3)";
  }
}

// ── FIX-4: Upload click gate ───────────────────────────────────────────────
function handleUploadClick() {
  const isPro = _userPlan === "pro" || _userPlan === "annual";
  if (!isPro && _reportsRemaining <= 0) {
    showUpgradeModal("upload");
    return;
  }
  el("fileInput")?.click();
}

// ── FIX-4: Upgrade modal ───────────────────────────────────────────────────
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

      <!-- top accent line -->
      <div style="position:absolute;top:0;left:0;right:0;height:3px;
        background:linear-gradient(90deg,var(--signal),var(--signal-2));"></div>

      <!-- close -->
      <button onclick="closeUpgradeModal()" style="
        position:absolute;top:14px;right:14px;
        width:30px;height:30px;border-radius:8px;
        border:none;background:var(--surface-3);color:var(--text-2);
        font-size:.85rem;cursor:pointer;
        display:flex;align-items:center;justify-content:center;
        transition:all .15s;
      " onmouseover="this.style.background='var(--surface-4)'"
         onmouseout="this.style.background='var(--surface-3)'">
        <i class="fa-solid fa-xmark"></i>
      </button>

      <!-- header -->
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:18px;padding-right:36px;">
        <div style="
          width:42px;height:42px;flex-shrink:0;
          background:linear-gradient(135deg,var(--signal),var(--signal-2));
          border-radius:12px;display:flex;align-items:center;justify-content:center;
          font-family:var(--serif);font-size:1.15rem;color:#0a0b0e;font-weight:600;
          box-shadow:0 4px 16px var(--signal-glow);
        ">φ</div>
        <div>
          <h2 style="font-family:var(--serif);font-size:1.2rem;font-weight:400;
            color:var(--text);margin-bottom:2px;line-height:1.2;">Upgrade to PHI Pro</h2>
          <p style="font-size:.73rem;color:var(--text-3);line-height:1.4;">
            Unlimited reports · Health memory · Insurance PA support
          </p>
        </div>
      </div>

      <!-- upload limit banner -->
      ${reason === "upload" ? `
        <div style="
          background:var(--amber-dim);border:1px solid rgba(251,191,36,.3);
          border-radius:10px;padding:10px 13px;margin-bottom:16px;
          font-size:.8rem;color:var(--amber);
          display:flex;align-items:center;gap:8px;
        ">
          <i class="fa-solid fa-triangle-exclamation" style="flex-shrink:0;"></i>
          <span><strong>Free limit reached</strong> — You've used your 1 free report upload.</span>
        </div>` : ""}

      <!-- feature list -->
      <div style="
        background:var(--surface-2);border:1px solid var(--border);
        border-radius:12px;padding:14px 16px;margin-bottom:18px;
      ">
        <div style="font-size:.62rem;font-weight:700;color:var(--text-3);
          text-transform:uppercase;letter-spacing:.1em;margin-bottom:10px;">What you unlock</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:7px 12px;">
          ${[
            ["fa-file-medical","Unlimited lab reports"],
            ["fa-brain","Full health memory"],
            ["fa-shield-halved","Insurance PA support"],
            ["fa-bell","Weekly health briefs"],
            ["fa-chart-line","Trend tracking"],
            ["fa-stethoscope","Doctor visit prep"],
          ].map(([icon, label]) => `
            <div style="display:flex;align-items:center;gap:7px;font-size:.78rem;color:var(--text-2);">
              <i class="fa-solid ${icon}" style="color:var(--signal);font-size:.7rem;width:14px;text-align:center;flex-shrink:0;"></i>
              ${label}
            </div>`).join("")}
        </div>
      </div>

      <!-- plan buttons — VERTICAL stack so both are always visible -->
      <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:14px;">

        <!-- Monthly -->
        <button id="rzpMonthlyBtn" onclick="initiateRazorpayCheckout('monthly')" style="
          width:100%;padding:13px 16px;
          background:var(--signal);color:#0a0b0e;
          border:none;border-radius:12px;
          font-size:.9rem;font-weight:700;
          cursor:pointer;font-family:var(--sans);
          box-shadow:0 4px 20px var(--signal-glow);
          transition:all .15s;
          display:flex;align-items:center;justify-content:space-between;
        ">
          <span>Monthly</span>
          <span style="font-family:var(--mono);font-size:1rem;">$39<span style="font-size:.72rem;font-weight:500;">/mo</span></span>
        </button>

        <!-- Annual — explicit dark bg + light text so it's always readable -->
        <button id="rzpAnnualBtn" onclick="initiateRazorpayCheckout('annual')" style="
          width:100%;padding:13px 16px;
          background:var(--surface-3);color:var(--text);
          border:2px solid var(--border-2);border-radius:12px;
          font-size:.9rem;font-weight:600;
          cursor:pointer;font-family:var(--sans);
          transition:all .15s;
          display:flex;align-items:center;justify-content:space-between;
          position:relative;
        ">
          <span style="display:flex;align-items:center;gap:8px;">
            Annual
            <span style="
              font-size:.58rem;font-weight:700;
              background:var(--ok);color:#0a0b0e;
              padding:2px 7px;border-radius:20px;letter-spacing:.04em;
            ">SAVE 20%</span>
          </span>
          <span style="font-family:var(--mono);font-size:1rem;color:var(--text);">$379<span style="font-size:.72rem;font-weight:500;color:var(--text-2);">/yr</span></span>
        </button>

      </div>

      <!-- trust line -->
      <p style="font-size:.67rem;color:var(--text-3);text-align:center;line-height:1.6;">
        <i class="fa-solid fa-lock" style="font-size:.6rem;margin-right:3px;"></i>
        Secure via Razorpay · No card stored · Cancel anytime
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

// ── FIX-4: Razorpay checkout — handles both order_id (one-time) and subscription_id flows ──
async function initiateRazorpayCheckout(plan = "monthly") {
  const PLAN_LABELS = { monthly: "Monthly — $49/mo", annual: "Annual — $399/yr", clinical: "Clinical — $99/mo" };
  const btnId = plan === "annual" ? "rzpAnnualBtn" : "rzpMonthlyBtn";
  const btn = el(btnId);
  const origLabel = PLAN_LABELS[plan] || "Upgrade";
  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i>'; }

  const h = await headers();
  if (!h) {
    toast("Please sign in to upgrade.", "err");
    if (btn) { btn.disabled = false; btn.innerHTML = origLabel; }
    return;
  }

  try {
    const { ok, data } = await apiJson("/api/payment/razorpay/order", {
      method: "POST",
      headers: h,
      body: JSON.stringify({ plan })
    });

    // Backend returns either order_id (one-time) or subscription_id (recurring)
    if (!ok || (!data?.order_id && !data?.subscription_id)) {
      toast(data?.error || "Payment setup failed. Please try again.", "err");
      if (btn) { btn.disabled = false; btn.innerHTML = origLabel; }
      return;
    }

    // Ensure Razorpay SDK is available (loaded from <head>, but guard anyway)
    if (typeof Razorpay === "undefined") {
      await new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = "https://checkout.razorpay.com/v1/checkout.js";
        s.onload = resolve;
        s.onerror = () => reject(new Error("Razorpay script failed to load"));
        document.head.appendChild(s);
      });
    }

    // Build Razorpay options — handle subscription vs one-time
    const isSubscription = data.mode === "subscription" || !!data.subscription_id;
    const options = {
      key:         data.razorpay_key_id,
      amount:      data.amount,
      currency:    data.currency || "USD",
      name:        "Curabook PHI",
      description: data.description || `PHI ${plan.charAt(0).toUpperCase() + plan.slice(1)} Plan`,
      handler: async function(response) {
        try {
          const verifyH = await headers();
          if (!verifyH) return;
          const vRes = await apiJson("/api/payment/razorpay/verify", {
            method: "POST",
            headers: verifyH,
            body: JSON.stringify({
              order_id:        data.order_id        || "",
              subscription_id: data.subscription_id || response.razorpay_subscription_id || "",
              payment_id:      response.razorpay_payment_id,
              signature:       response.razorpay_signature,
              plan:            plan,
            })
          });
          if (vRes.ok && vRes.data?.success) {
            _userPlan = vRes.data.plan || plan;
            _reportsRemaining = 9999;
            _renderPlanBadge();
            toast(`🎉 ${vRes.data.message || "Welcome to PHI Pro! Unlimited reports unlocked."}`, "ok");
            closeUpgradeModal();
            setTimeout(() => location.reload(), 2000);
          } else {
            toast("Payment verification failed. Contact support@curabook.com", "err");
          }
        } catch (e) {
          console.error("[RAZORPAY verify]", e);
          toast("Verification error. Contact support@curabook.com", "err");
        }
      },
      prefill: { email: _user?.email || "" },
      theme:   { color: "#00d4c8" },
      modal:   {
        ondismiss: () => {
          if (btn) { btn.disabled = false; btn.innerHTML = origLabel; }
          toast("Payment cancelled.", "info");
        }
      }
    };

    // Wire the correct Razorpay key depending on flow
    if (isSubscription) {
      options.subscription_id = data.subscription_id;
    } else {
      options.order_id = data.order_id;
    }

    const rzp = new Razorpay(options);
    rzp.open();

  } catch (e) {
    console.error("[RAZORPAY]", e);
    toast("Payment unavailable. Please try again.", "err");
    if (btn) { btn.disabled = false; btn.innerHTML = origLabel; }
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
    const t = setTimeout(() => ctrl.abort(), 45000); // FIX-5: 45s timeout
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
const openSidebar  = () => { el("sidebar")?.classList.add("open"); el("sidebarOverlay")?.classList.add("show"); closeCockpit(); };
const closeSidebar = () => { el("sidebar")?.classList.remove("open"); el("sidebarOverlay")?.classList.remove("show"); };
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
        <br><button class="hv-cta-btn" onclick="handleUploadClick()"><i class="fa-solid fa-upload"></i> Upload First Report</button></div>`;
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

  // FIX-2: Show health memory facts in health view
  if (_cachedMemories.length > 0) {
    html += `<div class="hv-section"><div class="hv-heading"><i class="fa-solid fa-brain"></i>PHI Health Memory (${_cachedMemories.length} facts)</div><div class="alert-feed">`;
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
    const { ok, data } = await apiJson("/api/doctor-prep/history", { headers: h });
    if (!ok || !data?.preps?.length) {
      list.innerHTML = `<div class="hv-empty"><i class="fa-solid fa-file-medical"></i>No lab reports yet.<br><button class="hv-cta-btn" onclick="handleUploadClick()"><i class="fa-solid fa-upload"></i> Upload First Report</button></div>`;
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
// SEND MESSAGE — FIX-2: health memory context handled server-side synchronously
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

    // Parse + log behavioral data client-side BEFORE API call (FIX-5: speed)
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

    // Handle file upload with payment gate
    if (_uploads.length) {
      const isPro = _userPlan === "pro" || _userPlan === "annual";
      if (!isPro && _reportsRemaining <= 0) {
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
        if (!isPro) {
          _reportsRemaining = Math.max(0, _reportsRemaining - 1);
          _renderPlanBadge();
        }
        // FIX-2: Refresh memories after upload (new markers stored)
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

    const payload = {
      conversation_id: _convId,
      message:         text,
      has_documents:   _docCtx.hasDoc,
      document_text:   _docCtx.hasDoc ? (_docCtx.text || "") : "",
    };

    // FIX-5: Animated typing indicator
    let dotCount = 0;
    const typingInterval = setInterval(() => {
      dotCount = (dotCount + 1) % 4;
      updateTyping(botRow, "PHI is thinking" + ".".repeat(dotCount));
    }, 600);

    const res = await fetch(API + "/chat", { method: "POST", headers: h, body: JSON.stringify(payload) });
    clearInterval(typingInterval);

    // Handle 402 upgrade required
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

  // FIX-2: Always refresh memory cache after AI reply
  // This ensures the UI reflects any facts the AI just stored
  if (_replyHasMemoryData(reply) || _replyHasMarkerData(reply)) {
    setTimeout(async () => {
      await _refreshMemoryCache();
      _updateMemoryCountDisplay();
    }, 1500);
  }

  if (_docCtx.hasDoc && _replyHasMarkerData(reply)) {
    setTimeout(async () => {
      await loadMarkersData();
      await _refreshMemoryCache();
      setTimeout(() => refreshShieldFromBehavioral(), 2000);
    }, 1000);
  }

  if (_replyHasShieldData(reply)) {
    setTimeout(() => refreshShieldFromBehavioral(), 1500);
  }

  if (responseData?.behavioral_logged) {
    setTimeout(() => refreshShieldFromBehavioral(), 1000);
  }

  // Update payment state from response
  if (responseData?.plan) {
    _userPlan = responseData.plan;
    if (responseData.reports_remaining !== undefined) {
      _reportsRemaining = responseData.reports_remaining;
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
    if (res.status === 402) {
      showUpgradeModal("upload");
      return null;
    }
    if (res.status === 403) {
      _consentsSaved = false; await saveConsents().catch(() => {});
      const s2 = await session(); if (s2) res = await doUp(s2.access_token);
    }
    if (res.status === 413) { toast("File too large (max 20MB).", "err"); return null; }
    if (!res.ok) { const d = await res.json().catch(() => {}); toast(d?.error || `Upload failed (${res.status}).`, "err"); return null; }
    const result = await res.json();
    setTimeout(async () => {
      await loadMarkersData();
      await _refreshMemoryCache(); // FIX-2: refresh after upload
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

// ══════════════════════════════════════════════════════════════════════════════
// FIX-3: FEEDBACK SYSTEM — Full NPS (1-10) + category + smooth animation
// UI-FIX: Feedback button moved from floating FAB to sidebar nav item (#navFeedbackBtn).
//         The FAB was invisible on mobile (hidden under nav) and cluttered desktop.
//         openFeedback() is exposed on window so the sidebar button can call it.
// ══════════════════════════════════════════════════════════════════════════════
function initFeedback() {
  // UI-FIX: No FAB button created here — sidebar nav item #navFeedbackBtn triggers openFeedback()
  // window.openFeedback is exposed below so onclick="openFeedback()" in HTML works

  // Inject feedback modal styles
  const style = document.createElement("style");
  style.textContent = `
    .feedback-modal-overlay {
      position: fixed; inset: 0; background: rgba(0,0,0,.7);
      z-index: 9990; display: none; align-items: flex-end;
      justify-content: center; padding: 0 0 80px;
    }
    @media(min-width:640px) { .feedback-modal-overlay { align-items: center; padding: 20px; } }
    .feedback-modal-overlay.open { display: flex; }

    .feedback-modal {
      background: var(--surface); border: 1px solid var(--border-2);
      border-radius: 20px 20px 0 0; padding: 24px 20px;
      width: 100%; max-width: 440px; position: relative;
      animation: slideUp .25s ease;
      max-height: 90vh; overflow-y: auto;
    }
    @media(min-width:640px) { .feedback-modal { border-radius: 20px; padding: 28px; } }

    .feedback-close {
      position: absolute; top: 14px; right: 14px;
      width: 32px; height: 32px; border-radius: 8px;
      border: none; background: none; color: var(--text-3);
      font-size: .9rem; cursor: pointer; display: flex;
      align-items: center; justify-content: center; transition: all .15s;
    }
    .feedback-close:hover { background: var(--surface-2); color: var(--text); }

    .feedback-header { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
    .feedback-icon-wrap {
      width: 40px; height: 40px; border-radius: 10px;
      background: var(--signal-dim); border: 1px solid rgba(0,212,200,.2);
      display: flex; align-items: center; justify-content: center;
      color: var(--signal); font-size: 1.1rem; flex-shrink: 0;
    }
    .feedback-title { font-size: .95rem; font-weight: 700; margin-bottom: 2px; }
    .feedback-sub { font-size: .74rem; color: var(--text-3); }

    /* NPS */
    .feedback-nps-label { font-size: .74rem; font-weight: 600; color: var(--text-2); margin-bottom: 8px; }
    .feedback-nps-row { display: flex; gap: 4px; margin-bottom: 4px; }
    .nps-btn {
      flex: 1; min-height: 36px; border: 1.5px solid var(--border);
      border-radius: 6px; background: var(--surface-2); color: var(--text-3);
      font-size: .78rem; font-weight: 600; cursor: pointer;
      font-family: var(--sans); transition: all .15s;
    }
    .nps-btn:hover { border-color: var(--signal); color: var(--signal); }
    .nps-btn.selected { color: #0a0b0e; font-weight: 700; }
    .feedback-nps-captions {
      display: flex; justify-content: space-between;
      font-size: .64rem; color: var(--text-3); margin-bottom: 14px;
    }

    /* Categories */
    .feedback-category-row {
      display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px;
    }
    .cat-btn {
      padding: 6px 12px; border: 1.5px solid var(--border);
      border-radius: 20px; background: var(--surface-2);
      color: var(--text-2); font-size: .74rem; font-weight: 500;
      cursor: pointer; font-family: var(--sans); transition: all .15s;
      min-height: 32px;
    }
    .cat-btn:hover { border-color: var(--signal); color: var(--signal); }
    .cat-btn.selected { background: var(--signal-dim); border-color: var(--signal); color: var(--signal); }

    .feedback-textarea {
      width: 100%; padding: 10px 12px; border: 1.5px solid var(--border);
      border-radius: 10px; background: var(--surface-2); color: var(--text);
      font-size: .85rem; font-family: var(--sans); resize: none;
      outline: none; transition: border-color .2s; margin-bottom: 10px;
      min-height: 80px;
    }
    .feedback-textarea:focus { border-color: var(--signal); }
    .feedback-textarea::placeholder { color: var(--text-3); }

    .feedback-footer { display: flex; align-items: center; justify-content: space-between; }
    .feedback-char-count { font-size: .68rem; color: var(--text-3); }
    .feedback-submit {
      padding: 9px 20px; background: var(--signal); color: #0a0b0e;
      border: none; border-radius: 8px; font-size: .84rem; font-weight: 700;
      cursor: pointer; font-family: var(--sans); display: flex;
      align-items: center; gap: 6px; transition: all .15s; min-height: 38px;
    }
    .feedback-submit:hover { background: var(--signal-2); }
    .feedback-submit:disabled { opacity: .6; cursor: not-allowed; }

    .feedback-success {
      display: none; flex-direction: column; align-items: center;
      text-align: center; padding: 20px 0; animation: fadeUp .3s ease;
    }
    .feedback-success-icon { font-size: 2.5rem; margin-bottom: 12px; }
    .feedback-success strong { font-size: 1rem; margin-bottom: 6px; display: block; }
    .feedback-success p { font-size: .82rem; color: var(--text-2); }
  `;
  document.head.appendChild(style);

  // Modal HTML
  const modal = document.createElement("div");
  modal.id = "feedbackModal";
  modal.className = "feedback-modal-overlay";
  modal.setAttribute("aria-hidden", "true");
  modal.innerHTML = `
    <div class="feedback-modal" role="dialog" aria-label="Send Feedback">
      <button class="feedback-close" id="feedbackClose" aria-label="Close">
        <i class="fa-solid fa-xmark"></i>
      </button>
      <div class="feedback-header">
        <div class="feedback-icon-wrap"><i class="fa-regular fa-comment-dots"></i></div>
        <div>
          <h3 class="feedback-title">How's PHI working for you?</h3>
          <p class="feedback-sub">Your feedback shapes what we build next.</p>
        </div>
      </div>

      <div class="feedback-nps-label">How likely are you to recommend PHI? (1 = not at all, 10 = absolutely)</div>
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
        <button class="feedback-submit" id="feedbackSubmit">
          <i class="fa-solid fa-paper-plane"></i> Send
        </button>
      </div>

      <div id="feedbackSuccess" class="feedback-success">
        <div class="feedback-success-icon">🎉</div>
        <strong>Thank you!</strong>
        <p>We read every message. Your input directly shapes PHI.</p>
      </div>
    </div>`;
  document.body.appendChild(modal);

  // State
  let selectedNps = 0, selectedCategory = "";

  // UI-FIX: sidebar nav item triggers openFeedback() — no FAB listener needed
  el("feedbackClose")?.addEventListener("click", () => closeFeedback());
  modal.addEventListener("click", e => { if (e.target === modal) closeFeedback(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeFeedback(); });

  // NPS scoring
  modal.querySelectorAll(".nps-btn").forEach(b => b.addEventListener("click", () => {
    selectedNps = parseInt(b.dataset.val);
    modal.querySelectorAll(".nps-btn").forEach(rb => {
      rb.classList.remove("selected");
      rb.style.background = "";
      rb.style.borderColor = "";
    });
    b.classList.add("selected");
    const color = selectedNps <= 6 ? "var(--danger)" : selectedNps <= 8 ? "var(--amber)" : "var(--ok)";
    b.style.background = color;
    b.style.borderColor = color;
  }));

  // Category selection
  modal.querySelectorAll(".cat-btn").forEach(b => b.addEventListener("click", () => {
    selectedCategory = b.dataset.cat;
    modal.querySelectorAll(".cat-btn").forEach(cb => cb.classList.remove("selected"));
    b.classList.add("selected");
  }));

  // Char count
  const ta = el("feedbackText"); const cc = el("feedbackCharCount");
  ta?.addEventListener("input", () => { if (cc) cc.textContent = `${ta.value.length} / 1000`; });

  // Submit
  el("feedbackSubmit")?.addEventListener("click", () => _submitFeedback(selectedNps, selectedCategory));
}

function openFeedback() {
  const modal = el("feedbackModal"); if (!modal) return;
  modal.setAttribute("aria-hidden", "false"); modal.classList.add("open");
  document.body.style.overflow = "hidden";
  // Reset state
  el("feedbackSuccess").style.display = "none";
  el("feedbackText").value = "";
  if (el("feedbackCharCount")) el("feedbackCharCount").textContent = "0 / 1000";
  el("feedbackNpsRow")?.style && (el("feedbackNpsRow").style.display = "");
  el("feedbackCategories")?.style && (el("feedbackCategories").style.display = "");
  el("feedbackText")?.style && (el("feedbackText").style.display = "");
  const footer = document.querySelector(".feedback-footer"); if (footer) footer.style.display = "";
  const label = document.querySelector(".feedback-nps-label"); if (label) label.style.display = "";
  const caps = document.querySelector(".feedback-nps-captions"); if (caps) caps.style.display = "";
  document.querySelector(".feedback-header")?.style && (document.querySelector(".feedback-header").style.display = "");
  modal.querySelectorAll(".nps-btn,.cat-btn").forEach(b => {
    b.classList.remove("selected");
    b.style.background = "";
    b.style.borderColor = "";
  });
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
        method: "POST",
        headers: h,
        body: JSON.stringify({
          rating:     nps,
          nps_score:  nps,
          category,
          text,
          url:        window.location.href,
          user_email: _user?.email || "anonymous",
          timestamp:  new Date().toISOString()
        }),
        signal: AbortSignal.timeout(8000)
      });
    }
  } catch (e) {}

  // Show success state
  const successEl = el("feedbackSuccess");
  if (successEl) successEl.style.display = "flex";

  // Hide inputs
  ["feedbackNpsRow", "feedbackCategories", "feedbackText"].forEach(id => {
    const e = el(id); if (e) e.style.display = "none";
  });
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

  // FIX-4: All upload buttons go through payment gate
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

  // Expose on window so inline onclick handlers and other scripts can call these
  // Bug-10-FIX: food_noise_emergency.js calls window.sendMessage — must be on window
  // UI-FIX: sidebar feedback button uses onclick="openFeedback()"
  window.sendMessage   = sendMessage;
  window.openFeedback  = openFeedback;
  window.closeFeedback = closeFeedback;
});