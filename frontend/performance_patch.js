/**
 * performance_patch.js — Curabook PHI v4 (SYNC-CACHE SAFE)
 *
 * FIX: Reads localStorage synchronously on script parse to render the cached
 * UI instantly. Prevents script.js from showing loading spinners or double-fetching.
 *
 * LOAD ORDER: Must be the FIRST script in index.html, before script.js.
 */
"use strict";

/* ─── 1. CACHE ─────────────────────────────────────────────────────────────── */
const Cache = {
  set(key, data, ttlSeconds) {
    try {
      localStorage.setItem("phi_cache_" + key, JSON.stringify({
        data, expires: Date.now() + ttlSeconds * 1000
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
  clear(key) { try { localStorage.removeItem("phi_cache_" + key); } catch(e) {} },
  clearAll() {
    try {
      Object.keys(localStorage)
        .filter(k => k.startsWith("phi_cache_"))
        .forEach(k => localStorage.removeItem(k));
    } catch(e) {}
  },
};
window.Cache = Cache;

/* ─── 2. STATE ─────────────────────────────────────────────────────────────── */
let _started = false;
let _userId  = null;

/* ─── 3. API BASE ──────────────────────────────────────────────────────────── */
function _api() {
  return ["localhost","127.0.0.1","0.0.0.0"].includes(location.hostname)
    ? "http://localhost:5000"
    : "https://api.curabook.com";
}

/* ─── 4. WAIT FOR CONDITION, THEN CALL FN ──────────────────────────────────── */
function _when(condition, fn, maxMs = 8000) {
  if (condition()) { try { fn(); } catch(e) {} return; }
  const start = Date.now();
  const id = setInterval(() => {
    if (condition()) {
      clearInterval(id);
      try { fn(); } catch(e) {}
      return;
    }
    if (Date.now() - start > maxMs) clearInterval(id);
  }, 30);
}

/* ─── 5. GET AUTH TOKEN ────────────────────────────────────────────────────── */
async function _getToken() {
  if (window._sb) {
    try {
      const { data } = await window._sb.auth.getSession();
      if (data?.session?.access_token) return data.session.access_token;
    } catch(e) {}
  }
  try {
    const keys = Object.keys(localStorage)
      .filter(k => k.startsWith("sb-") && k.endsWith("-auth-token"));
    for (const key of keys) {
      const parsed = JSON.parse(localStorage.getItem(key) || "{}");
      if (parsed?.access_token) return parsed.access_token;
    }
  } catch(e) {}
  return null;
}

/* ─── 6. RENDER CLIFF ALERTS ───────────────────────────────────────────────── */
function _renderAlerts(alerts) {
  const c = document.getElementById("cliffAlerts");
  if (!c) return;
  if (!alerts?.length) {
    c.innerHTML = `<div class="ca-item ca-ok">
      <div class="ca-title">✅ No rebound signals</div>
      <div class="ca-desc">All monitored markers stable.</div>
    </div>`;
    return;
  }
  c.innerHTML = alerts.map(a => {
    const cls   = a.severity === "high" ? "ca-danger" : "ca-warn";
    const icon  = a.severity === "high" ? "🚨" : "⚠️";
    const title  = String(a.headline || a.marker || "").replace(/</g,"&lt;");
    const detail = String(a.detail   || "").replace(/</g,"&lt;");
    return `<div class="ca-item ${cls}">
      <div class="ca-title">${icon} ${title}</div>
      ${detail ? `<div class="ca-desc">${detail}</div>` : ""}
    </div>`;
  }).join("");
}

/* ─── 7. INSTANT CACHE RENDERER ────────────────────────────────────────────── */
function _getUserIdSync() {
  try {
    const keys = Object.keys(localStorage).filter(k => k.startsWith("sb-") && k.endsWith("-auth-token"));
    for (const key of keys) {
      const parsed = JSON.parse(localStorage.getItem(key) || "{}");
      if (parsed?.user?.id) return parsed.user.id;
    }
  } catch(e) {}
  return null;
}

function _renderCache(userId) {
  window._perf_patch_active = true; // Signal to script.js to skip double-fetching
  
  const cachedHistory = Cache.get("history_" + userId);
  const cachedMarkers = Cache.get("markers_" + userId);
  const cachedGw      = Cache.get("goal_wt_"  + userId);
  const cachedShield  = Cache.get("shield_" + userId);

  if (cachedHistory?.length) {
    _when(() => typeof renderHistory === "function", () => renderHistory(cachedHistory));
  }
  if (cachedMarkers?.length) {
    _when(() => typeof renderMarkers === "function", () => {
      renderMarkers(cachedMarkers);
      if (typeof runCliffDetection === "function") runCliffDetection(cachedMarkers);
    });
  }
  if (cachedGw) {
    localStorage.setItem("phi_goal_wt", String(cachedGw));
    _when(() => typeof calcProteinDisplay === "function", () => {
      const gwEl = document.getElementById("inputGoalWt");
      const piEl = document.getElementById("proteinInput");
      if (gwEl) gwEl.value = cachedGw;
      if (piEl) piEl.value = cachedGw;
      calcProteinDisplay(parseFloat(cachedGw), false);
    });
  }
  if (cachedShield) {
    _when(() => typeof renderShield === "function", () => {
      renderShield(cachedShield.protein || 0, cachedShield.steps || 0, cachedShield.sleep || 0, null);
    });
  }
}

// EXECUTE INSTANTLY ON SCRIPT LOAD:
const syncUserId = _getUserIdSync();
if (syncUserId) {
  _userId = syncUserId;
  _renderCache(syncUserId);
}

/* ─── 8. BACKGROUND NETWORK FETCH ──────────────────────────────────────────── */
async function _fetchFresh(userId) {
  if (_started) return;
  _started = true;
  _userId  = userId;

  let token = null;
  for (let i = 0; i < 30; i++) {
    token = await _getToken();
    if (token) break;
    await new Promise(r => setTimeout(r, 100));
  }
  if (!token) return;

  const hdrs = { Authorization: "Bearer " + token };
  let data = null;

  try {
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 6000);
    const res = await fetch(_api() + "/api/startup", { headers: hdrs, signal: controller.signal });
    if (res.ok) data = await res.json();
  } catch(e) {}

  // Fallback if batch endpoint fails
  if (!data) {
    try {
      const [hRes, mRes] = await Promise.allSettled([
        fetch(_api() + "/history", { method: "POST", headers: { ...hdrs, "Content-Type": "application/json" }, body: "{}" }),
        fetch(_api() + "/api/health-markers", { headers: hdrs })
      ]);
      data = {
        history: (hRes.status === "fulfilled" && hRes.value.ok) ? await hRes.value.json() : [],
        markers: (mRes.status === "fulfilled" && mRes.value.ok) ? await mRes.value.json() : [],
        cliff_alerts: []
      };
    } catch(e) { return; }
  }

  // Update caches and DOM with fresh data
  const history = Array.isArray(data.history) ? data.history : [];
  if (history.length) {
    Cache.set("history_" + userId, history, 120);
    _when(() => typeof renderHistory === "function", () => renderHistory(history));
  }

  const markers = Array.isArray(data.markers) ? data.markers : [];
  if (markers.length) {
    Cache.set("markers_" + userId, markers, 300);
    _when(() => typeof renderMarkers === "function", () => {
      renderMarkers(markers);
      if (typeof runCliffDetection === "function") runCliffDetection(markers);
    });
  }

  const alerts = Array.isArray(data.cliff_alerts) ? data.cliff_alerts : [];
  _when(() => !!document.getElementById("cliffAlerts"), () => _renderAlerts(alerts));
  
  if (data.goal_weight) {
    const gw = parseFloat(data.goal_weight);
    Cache.set("goal_wt_" + userId, gw, 3600);
    localStorage.setItem("phi_goal_wt", String(gw));
    _when(() => typeof calcProteinDisplay === "function", () => {
      const gwEl = document.getElementById("inputGoalWt");
      const piEl = document.getElementById("proteinInput");
      if (gwEl) gwEl.value = gw;
      if (piEl) piEl.value = gw;
      calcProteinDisplay(gw, false);
    });
  }
  
  _when(() => typeof autoLoadShield === "function", async () => {
    try {
      await autoLoadShield();
      const p  = parseFloat(document.getElementById("inputProtein")?.value) || 0;
      const s  = parseFloat(document.getElementById("inputSteps")?.value)   || 0;
      const sl = parseFloat(document.getElementById("inputSleep")?.value)   || 0;
      if (p || s || sl) Cache.set("shield_" + userId, { protein:p, steps:s, sleep:sl }, 300);
    } catch(e) {}
  });
}

/* ─── 9. POLL FOR FULL BOOT ────────────────────────────────────────────────── */
(function() {
  let checks = 0;
  const id = setInterval(() => {
    checks++;
    if (checks > 600 || _started) { clearInterval(id); return; }

    const user = window._user;
    if (!user?.id) return;

    clearInterval(id);
    _fetchFresh(user.id).catch(() => {});
  }, 30);
})();

/* ─── 10. INVALIDATE CACHE AFTER UPLOAD ────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", () => {
  _when(() => typeof processUpload === "function", () => {
    const _orig = processUpload;
    window.processUpload = async function(file) {
      const result = await _orig.call(this, file);
      if (result?.success !== false && _userId) {
        Cache.clear("markers_" + _userId);
        Cache.clear("shield_"  + _userId);
      }
      return result;
    };
  });

  _when(() => typeof doSignOut === "function", () => {
    const _orig = doSignOut;
    window.doSignOut = async function() {
      Cache.clearAll();
      _started = false;
      _userId  = null;
      return _orig.call(this);
    };
  });
});

console.log("[PERF] performance_patch.js v4 loaded");