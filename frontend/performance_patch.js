/**
 * performance_patch.js — Curabook PHI (FIXED)
 *
 * ROOT CAUSES FIXED:
 *
 * FIX-1: The old patch tried to override `onSignIn` from a different script
 *   via `window.onSignIn`. But script.js declares `async function onSignIn()`
 *   at module scope — that declaration is hoisted and NOT on `window`. The
 *   setInterval patch never actually replaced the real function.
 *   FIX: We hook into Supabase's onAuthStateChange BEFORE script.js runs,
 *   via a shared flag + DOMContentLoaded ordering guarantee. We fire our
 *   own parallel startup fetch on the SIGNED_IN event.
 *
 * FIX-2: /api/startup failing silently left UI empty. Now falls back to
 *   individual endpoints (/history, /api/health-markers, /api/dashboard)
 *   with proper error logging.
 *
 * FIX-3: Cache was keyed by user.id but user.id was not yet available
 *   when cache reads happened. Now we store user.id in window after auth.
 *
 * FIX-4: Shield cache read happened before autoLoadShield() completed,
 *   so cached values were always stale/zero. Now renders cached shield
 *   first, then overwrites with fresh server data.
 *
 * LOAD ORDER: Add this BEFORE script.js in index.html:
 *   <script src="performance_patch.js"></script>
 *   <script src="script.js"></script>
 *   <script src="cockpit_upgrades.js"></script>
 */
"use strict";

/* ─── 1. CACHE HELPERS ─────────────────────────────────────────────────────── */
const Cache = {
  set(key, data, ttlSeconds) {
    try {
      localStorage.setItem("phi_cache_" + key, JSON.stringify({
        data,
        expires: Date.now() + ttlSeconds * 1000,
      }));
    } catch(e) {}
  },
  get(key) {
    try {
      const raw = localStorage.getItem("phi_cache_" + key);
      if (!raw) return null;
      const { data, expires } = JSON.parse(raw);
      if (Date.now() > expires) {
        localStorage.removeItem("phi_cache_" + key);
        return null;
      }
      return data;
    } catch(e) { return null; }
  },
  clear(key) {
    try { localStorage.removeItem("phi_cache_" + key); } catch(e) {}
  },
  clearAll() {
    try {
      Object.keys(localStorage)
        .filter(k => k.startsWith("phi_cache_"))
        .forEach(k => localStorage.removeItem(k));
    } catch(e) {}
  },
};
window.Cache = Cache;

/* ─── 2. SHARED STATE ──────────────────────────────────────────────────────── */
// Signals that our patch already handled startup — script.js checks this
window.__phi_startup_done = false;
window.__phi_user_id = null;

/* ─── 3. STARTUP DATA LOADER ───────────────────────────────────────────────── */
async function _loadStartupData(token, userId) {
  const hdrs = { Authorization: "Bearer " + token };
  const API  = (window.IS_LOCAL || ["localhost","127.0.0.1"].includes(location.hostname))
    ? "http://localhost:5000"
    : "https://api.curabook.com";

  // ── Try the batch startup endpoint first ────────────────────────────────────
  let data = null;
  try {
    const res = await fetch(API + "/api/startup", { headers: hdrs });
    if (res.ok) {
      data = await res.json();
      console.log("[PERF] /api/startup OK in", data.elapsed_ms + "ms");
    } else {
      console.warn("[PERF] /api/startup returned", res.status, "— falling back");
    }
  } catch(e) {
    console.warn("[PERF] /api/startup network error:", e.message, "— falling back");
  }

  // ── Fallback: individual calls in parallel ──────────────────────────────────
  if (!data) {
    try {
      const [histRes, markRes] = await Promise.all([
        fetch(API + "/history",             { method: "POST", headers: { ...hdrs, "Content-Type": "application/json" }, body: "{}" }),
        fetch(API + "/api/health-markers",  { headers: hdrs }),
      ]);
      data = {
        history:      histRes.ok  ? await histRes.json()  : [],
        markers:      markRes.ok  ? await markRes.json()  : [],
        cliff_alerts: [],
        goal_weight:  null,
        user_name:    "",
      };
      console.log("[PERF] Fallback parallel fetch complete");
    } catch(e) {
      console.error("[PERF] Fallback fetch failed:", e.message);
      return;
    }
  }

  // ── Render history ──────────────────────────────────────────────────────────
  const history = Array.isArray(data.history) ? data.history : [];
  if (history.length) {
    Cache.set("history_" + userId, history, 120);
    // Wait for script.js to define renderHistory
    _waitFor(() => typeof renderHistory === "function", () => renderHistory(history));
  }

  // ── Render markers + cliff alerts ───────────────────────────────────────────
  const markers = Array.isArray(data.markers) ? data.markers : [];
  if (markers.length) {
    Cache.set("markers_" + userId, markers, 300);
    _waitFor(() => typeof renderMarkers === "function", () => {
      renderMarkers(markers);
      if (typeof runCliffDetection === "function") runCliffDetection(markers);
    });
  }

  // ── Render startup cliff alerts in cockpit ──────────────────────────────────
  const alerts = Array.isArray(data.cliff_alerts) ? data.cliff_alerts : [];
  _waitFor(() => !!document.getElementById("cliffAlerts"), () => {
    _renderCliffAlerts(alerts);
  });

  // ── Apply goal weight ───────────────────────────────────────────────────────
  if (data.goal_weight) {
    const gw = parseFloat(data.goal_weight);
    Cache.set("goal_wt_" + userId, gw, 3600);
    localStorage.setItem("phi_goal_wt", String(gw));
    _waitFor(() => typeof calcProteinDisplay === "function", () => {
      const gwInput = document.getElementById("inputGoalWt");
      const piInput = document.getElementById("proteinInput");
      if (gwInput) gwInput.value = gw;
      if (piInput) piInput.value = gw;
      calcProteinDisplay(gw, false);
    });
  }

  // ── Render cached shield immediately, then let autoLoadShield update ─────────
  const cachedShield = Cache.get("shield_" + userId);
  if (cachedShield) {
    _waitFor(() => typeof renderShield === "function", () => {
      renderShield(
        cachedShield.protein || 0,
        cachedShield.steps   || 0,
        cachedShield.sleep   || 0,
        null
      );
    });
  }

  window.__phi_startup_done = true;
  console.log("[PERF] Startup render complete. history:", history.length, "markers:", markers.length);
}

/* ─── 4. POLL UNTIL CONDITION IS MET, THEN CALL FN ────────────────────────── */
function _waitFor(condition, fn, maxMs = 5000) {
  if (condition()) { fn(); return; }
  const start = Date.now();
  const id = setInterval(() => {
    if (condition()) { clearInterval(id); fn(); return; }
    if (Date.now() - start > maxMs) { clearInterval(id); }
  }, 50);
}

/* ─── 5. CLIFF ALERTS RENDERER ─────────────────────────────────────────────── */
function _renderCliffAlerts(alerts) {
  const c = document.getElementById("cliffAlerts");
  if (!c) return;
  if (!alerts || !alerts.length) {
    c.innerHTML = `<div class="ca-item ca-ok">
      <div class="ca-title">&#x2705; No rebound signals</div>
      <div class="ca-desc">All monitored markers stable. Keep up protein and training.</div>
    </div>`;
    return;
  }
  c.innerHTML = alerts.map(a => {
    const cls = a.severity === "high" ? "ca-danger" : "ca-warn";
    const icon = a.severity === "high" ? "&#x1F6A8;" : "&#x26A0;&#xFE0F;";
    const title = String(a.headline || a.marker || "Signal detected")
      .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    const detail = String(a.detail || "")
      .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    return `<div class="ca-item ${cls}">
      <div class="ca-title">${icon} ${title}</div>
      ${detail ? `<div class="ca-desc">${detail}</div>` : ""}
    </div>`;
  }).join("");
}

/* ─── 6. HOOK INTO SUPABASE AUTH — BEFORE script.js runs ───────────────────── */
// We intercept the onAuthStateChange callback the moment the Supabase SDK fires.
// Because DOMContentLoaded fires scripts in order, this script runs first.
// We set a global __phi_patch_onSignIn that script.js's boot() will call.

window.__phi_patch_onSignIn = async function(user, token) {
  if (!user || !token) return;
  window.__phi_user_id = user.id;

  // Render from cache INSTANTLY (0ms) before any network call
  const uid = user.id;

  const cachedHistory = Cache.get("history_" + uid);
  const cachedMarkers = Cache.get("markers_" + uid);
  const cachedGw      = Cache.get("goal_wt_" + uid);

  if (cachedHistory?.length) {
    _waitFor(() => typeof renderHistory === "function",
      () => renderHistory(cachedHistory));
  }
  if (cachedMarkers?.length) {
    _waitFor(() => typeof renderMarkers === "function", () => {
      renderMarkers(cachedMarkers);
      if (typeof runCliffDetection === "function") runCliffDetection(cachedMarkers);
    });
  }
  if (cachedGw) {
    localStorage.setItem("phi_goal_wt", String(cachedGw));
    _waitFor(() => typeof calcProteinDisplay === "function",
      () => calcProteinDisplay(cachedGw, false));
  }

  // Kick off network fetch (non-blocking)
  _loadStartupData(token, uid).catch(e =>
    console.error("[PERF] _loadStartupData error:", e));

  // Cache shield after autoLoadShield completes
  _waitFor(() => typeof autoLoadShield === "function", async () => {
    try {
      await autoLoadShield();
      const p  = parseFloat(document.getElementById("inputProtein")?.value) || 0;
      const s  = parseFloat(document.getElementById("inputSteps")?.value)   || 0;
      const sl = parseFloat(document.getElementById("inputSleep")?.value)   || 0;
      if (p || s || sl) Cache.set("shield_" + uid, { protein:p, steps:s, sleep:sl }, 300);
    } catch(e) {}
  });
};

/* ─── 7. PATCH script.js's onSignIn — runs AFTER DOMContentLoaded ──────────── */
// We can't override the hoisted function declaration, so instead we wrap
// the onAuthStateChange registration point. script.js calls boot() inside
// DOMContentLoaded. We use a MutationObserver trick: patch the global Supabase
// createClient to intercept the onAuthStateChange subscription.

const _origCreateClient = window.supabase?.createClient;
if (_origCreateClient) {
  const _patchedCreate = function(...args) {
    const client = _origCreateClient.apply(window.supabase, args);
    const _origOnAuth = client.auth.onAuthStateChange.bind(client.auth);

    client.auth.onAuthStateChange = function(callback) {
      const wrappedCallback = async (event, session) => {
        // Run original callback
        await callback(event, session);
        // After SIGNED_IN, fire our parallel startup if not already done
        if (event === "SIGNED_IN" && session?.user && session?.access_token) {
          if (!window.__phi_startup_done) {
            window.__phi_patch_onSignIn(session.user, session.access_token)
              .catch(e => console.warn("[PERF] patch_onSignIn error:", e));
          }
        }
        if (event === "SIGNED_OUT") {
          Cache.clearAll();
          window.__phi_startup_done = false;
          window.__phi_user_id = null;
        }
      };
      return _origOnAuth(wrappedCallback);
    };

    // Also intercept getSession for the non-event code path in boot()
    const _origGetSession = client.auth.getSession.bind(client.auth);
    client.auth.getSession = async function() {
      const result = await _origGetSession();
      const session = result?.data?.session;
      if (session?.user && session?.access_token && !window.__phi_startup_done) {
        // Fire startup for existing session (page refresh, not a new sign-in)
        setTimeout(() => {
          window.__phi_patch_onSignIn(session.user, session.access_token)
            .catch(e => console.warn("[PERF] getSession patch error:", e));
        }, 0);
      }
      return result;
    };

    return client;
  };

  // Replace on the supabase namespace object
  try {
    window.supabase = { ...window.supabase, createClient: _patchedCreate };
  } catch(e) {
    console.warn("[PERF] Could not patch supabase.createClient:", e);
  }
}

/* ─── 8. PATCH processUpload — invalidate marker cache after upload ─────────── */
document.addEventListener("DOMContentLoaded", () => {
  _waitFor(() => typeof processUpload === "function", () => {
    const _orig = window.processUpload;
    window.processUpload = async function(file) {
      const result = await _orig.call(this, file);
      if (result?.success !== false && window.__phi_user_id) {
        Cache.clear("markers_" + window.__phi_user_id);
        Cache.clear("shield_"  + window.__phi_user_id);
        console.log("[PERF] Cache cleared after upload");
      }
      return result;
    };
  });

  // Patch doSignOut to clear cache
  _waitFor(() => typeof doSignOut === "function", () => {
    const _orig = window.doSignOut;
    window.doSignOut = async function() {
      Cache.clearAll();
      return _orig.call(this);
    };
  });
});

console.log("[PERF] performance_patch.js loaded — supabase patched:", !!_origCreateClient);