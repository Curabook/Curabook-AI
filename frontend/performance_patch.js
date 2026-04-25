/**
 * performance_patch.js — Curabook PHI
 *
 * Add this BEFORE script.js in index.html:
 *   <script src="performance_patch.js"></script>
 *   <script src="script.js"></script>
 *
 * What this fixes:
 *  1. onSignIn() now fires ONE /api/startup call (parallel DB) instead of 4 sequential ones
 *  2. localStorage cache renders history + markers instantly (~0ms) while revalidating
 *  3. saveConsents no longer blocks the UI — runs fire-and-forget
 *  4. Shield renders from cache before the network responds
 *  5. Cache TTL: history = 2 min, markers = 5 min (auto-invalidated on new upload)
 *
 * These overrides run AFTER script.js loads (DOMContentLoaded order preserved).
 */
"use strict";

// ── Cache helpers ─────────────────────────────────────────────────────────────

const Cache = {
  set(key, data, ttlSeconds) {
    try {
      localStorage.setItem("phi_cache_" + key, JSON.stringify({
        data,
        expires: Date.now() + ttlSeconds * 1000,
      }));
    } catch(e) { /* storage full — ignore */ }
  },
  get(key) {
    try {
      const raw = localStorage.getItem("phi_cache_" + key);
      if (!raw) return null;
      const { data, expires } = JSON.parse(raw);
      if (Date.now() > expires) { localStorage.removeItem("phi_cache_" + key); return null; }
      return data;
    } catch(e) { return null; }
  },
  clear(key) { try { localStorage.removeItem("phi_cache_" + key); } catch(e) {} },
  clearAll() {
    try {
      Object.keys(localStorage)
        .filter(k => k.startsWith("phi_cache_"))
        .forEach(k => localStorage.removeItem(k));
    } catch(e) {}
  },
};

// Expose globally so document_routes can call Cache.clear("markers") after upload
window.Cache = Cache;

// ── Override: onSignIn — parallel fast path ───────────────────────────────────

// Store original boot so we can call sub-functions from it
let _originalOnSignIn = null;

document.addEventListener("DOMContentLoaded", () => {
  // Wait for script.js to define onSignIn, then override it
  const _patchInterval = setInterval(() => {
    if (typeof onSignIn === "undefined") return;
    clearInterval(_patchInterval);

    _originalOnSignIn = onSignIn;

    // Redefine onSignIn in global scope
    window.onSignIn = async function(user) {
      if (window._initialized) return;
      window._initialized = true;
      window._user = user;

      // ── 1. Resolve display name ─────────────────────────────────────────
      const meta = user.user_metadata || {};
      window._userName = meta.first_name
        || user.email?.split("@")[0]?.split(/[._-]/)[0]
        || "there";
      window._userName = window._userName[0].toUpperCase() + window._userName.slice(1);

      const setT = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
      setT("userEmail",   user.email);
      setT("welcomeName", window._userName);
      const av = document.getElementById("userAvatar");
      if (av) av.textContent = window._userName[0].toUpperCase();
      const h = new Date().getHours();
      setT("timeGreeting", h < 12 ? "morning" : h < 17 ? "afternoon" : "evening");

      // ── 2. Render from cache INSTANTLY (0ms) ───────────────────────────
      const cachedHistory = Cache.get("history_" + user.id);
      const cachedMarkers = Cache.get("markers_" + user.id);
      const cachedGw      = Cache.get("goal_wt_"  + user.id);

      if (cachedHistory?.length) {
        if (typeof renderHistory === "function") renderHistory(cachedHistory);
      }
      if (cachedMarkers?.length) {
        if (typeof renderMarkers    === "function") renderMarkers(cachedMarkers);
        if (typeof runCliffDetection === "function") runCliffDetection(cachedMarkers);
      }
      if (cachedGw) {
        window._goalWt = cachedGw;
        if (document.getElementById("inputGoalWt"))  document.getElementById("inputGoalWt").value  = cachedGw;
        if (document.getElementById("proteinInput")) document.getElementById("proteinInput").value  = cachedGw;
        if (typeof calcProteinDisplay === "function") calcProteinDisplay(cachedGw, false);
      }

      // ── 3. Fire non-blocking parallel network calls ────────────────────
      // saveConsents: fire-and-forget, never await
      (async () => {
        try {
          if (typeof saveConsents === "function") await saveConsents();
        } catch(e) {}
      })();

      // Startup batch + behavioral logs in parallel
      Promise.all([
        _fetchStartupBatch(user),
        _fetchShieldToday(user),
      ]).catch(() => {});
    };

    console.log("[PERF] onSignIn patched — parallel fast path active");
  }, 50);
});

// ── Startup batch fetcher ─────────────────────────────────────────────────────

async function _fetchStartupBatch(user) {
  let hdrs;
  try {
    const s = await window._sb.auth.getSession();
    if (!s?.data?.session?.access_token) return;
    hdrs = {
      Authorization: `Bearer ${s.data.session.access_token}`,
    };
  } catch(e) { return; }

  let data;
  try {
    const res = await fetch(window.API + "/api/startup", { headers: hdrs });
    if (!res.ok) { throw new Error("startup " + res.status); }
    data = await res.json();
  } catch(e) {
    console.warn("[PERF] /api/startup failed, falling back to individual calls:", e.message);
    // Fallback: call old functions if startup endpoint unavailable
    if (typeof loadHistory     === "function") loadHistory().catch(() => {});
    if (typeof loadMarkersData === "function") loadMarkersData().catch(() => {});
    return;
  }

  // ── Render history ────────────────────────────────────────────────────
  if (Array.isArray(data.history) && data.history.length) {
    Cache.set("history_" + user.id, data.history, 120);  // 2 min TTL
    if (typeof renderHistory === "function") renderHistory(data.history);
  }

  // ── Render markers + cliff alerts ─────────────────────────────────────
  if (Array.isArray(data.markers) && data.markers.length) {
    // Map marker_name → marker for renderMarkers compatibility
    const normalised = data.markers.map(m => ({
      ...m,
      marker_name: m.marker_name,
    }));
    Cache.set("markers_" + user.id, normalised, 300);  // 5 min TTL
    if (typeof renderMarkers     === "function") renderMarkers(normalised);
    if (typeof runCliffDetection === "function") runCliffDetection(normalised);
  }

  // ── Apply goal weight from profile ────────────────────────────────────
  if (data.goal_weight) {
    window._goalWt = data.goal_weight;
    Cache.set("goal_wt_" + user.id, data.goal_weight, 3600);  // 1 hr TTL
    localStorage.setItem("phi_goal_wt", String(data.goal_weight));
    if (document.getElementById("inputGoalWt"))  document.getElementById("inputGoalWt").value  = data.goal_weight;
    if (document.getElementById("proteinInput")) document.getElementById("proteinInput").value  = data.goal_weight;
    if (typeof calcProteinDisplay === "function") calcProteinDisplay(data.goal_weight, false);
  }

  // ── Cliff alerts in cockpit ───────────────────────────────────────────
  if (Array.isArray(data.cliff_alerts)) {
    _renderStartupCliffAlerts(data.cliff_alerts);
  }

  console.log(`[PERF] Startup loaded in ${data.elapsed_ms ?? "?"}ms —`,
    `history:${data.history?.length ?? 0}`,
    `markers:${data.markers?.length ?? 0}`,
    `alerts:${data.cliff_alerts?.length ?? 0}`);
}

// ── Shield today fetcher (behavioral logs) ────────────────────────────────────

async function _fetchShieldToday(user) {
  // Show cached shield immediately
  const cachedShield = Cache.get("shield_" + user.id);
  if (cachedShield) {
    if (typeof renderShield === "function") {
      renderShield(cachedShield.protein || 0, cachedShield.steps || 0, cachedShield.sleep || 0, null);
    }
  }

  // Then revalidate from server
  try {
    if (typeof autoLoadShield === "function") {
      await autoLoadShield();
      // Cache the current shield inputs after render
      const p  = parseFloat(document.getElementById("inputProtein")?.value) || 0;
      const s  = parseFloat(document.getElementById("inputSteps")?.value)   || 0;
      const sl = parseFloat(document.getElementById("inputSleep")?.value)   || 0;
      if (p || s || sl) Cache.set("shield_" + user.id, { protein: p, steps: s, sleep: sl }, 300);
    }
  } catch(e) {}
}

// ── Cliff alerts renderer for startup data ────────────────────────────────────

function _renderStartupCliffAlerts(alerts) {
  const c = document.getElementById("cliffAlerts");
  if (!c) return;
  if (!alerts.length) {
    c.innerHTML = `<div class="ca-item ca-ok">
      <div class="ca-title">✅ No rebound signals</div>
      <div class="ca-desc">All monitored markers stable. Keep up protein and training.</div>
    </div>`;
    return;
  }
  c.innerHTML = alerts.map(a => {
    const cls = a.severity === "high" ? "ca-danger" : "ca-warn";
    return `<div class="ca-item ${cls}">
      <div class="ca-title">${a.severity === "high" ? "🚨" : "⚠️"} ${_esc(a.headline || a.marker)}</div>
      ${a.detail ? `<div class="ca-desc">${_esc(a.detail)}</div>` : ""}
    </div>`;
  }).join("");
}

function _esc(s) {
  return String(s || "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// ── Cache invalidation on upload ──────────────────────────────────────────────
// Patch processUpload to clear marker cache after a successful upload

document.addEventListener("DOMContentLoaded", () => {
  const _patchUpload = setInterval(() => {
    if (typeof processUpload === "undefined") return;
    clearInterval(_patchUpload);

    const _origProcessUpload = window.processUpload;
    window.processUpload = async function(file) {
      const result = await _origProcessUpload.call(this, file);
      if (result?.success !== false && window._user) {
        // New report uploaded — invalidate marker + cliff caches
        Cache.clear("markers_" + window._user.id);
        Cache.clear("shield_"  + window._user.id);
        console.log("[PERF] Marker cache cleared after upload");
      }
      return result;
    };
  }, 50);
});

// ── Clear all cache on sign-out ───────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  const _patchSignout = setInterval(() => {
    if (typeof doSignOut === "undefined") return;
    clearInterval(_patchSignout);

    const _origSignOut = window.doSignOut;
    window.doSignOut = async function() {
      Cache.clearAll();
      return _origSignOut.call(this);
    };
  }, 50);
});

console.log("[PERF] performance_patch.js loaded");