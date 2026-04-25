/**
 * performance_patch.js — Curabook PHI v5 (STALE-WHILE-REVALIDATE)
 *
 * FIXES:
 * 1. Cache has infinite TTL to guarantee instant renders even after hours.
 * 2. Skips polling entirely; reads token synchronously and fires fetch instantly.
 * 3. Detects 401s from expired local tokens and signals script.js to take over.
 */
"use strict";

// 1. Cache (Stale-While-Revalidate pattern)
const Cache = {
  set(key, data) {
    try { localStorage.setItem("phi_cache_" + key, JSON.stringify(data)); } catch(e) {}
  },
  get(key) {
    try {
      const raw = localStorage.getItem("phi_cache_" + key);
      return raw ? JSON.parse(raw) : null;
    } catch(e) { return null; }
  },
  clear(key) { try { localStorage.removeItem("phi_cache_" + key); } catch(e) {} },
  clearAll() {
    try {
      Object.keys(localStorage).filter(k => k.startsWith("phi_cache_")).forEach(k => localStorage.removeItem(k));
    } catch(e) {}
  }
};
window.Cache = Cache;

// 2. API & Utility
function _api() {
  return ["localhost","127.0.0.1","0.0.0.0"].includes(location.hostname)
    ? "http://localhost:5000"
    : "https://api.curabook.com";
}

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

// 3. Extract Session Synchronously
function _getAuthSync() {
  try {
    const keys = Object.keys(localStorage).filter(k => k.startsWith("sb-") && k.endsWith("-auth-token"));
    for (const key of keys) {
      const parsed = JSON.parse(localStorage.getItem(key) || "{}");
      if (parsed?.user?.id && parsed?.access_token) {
        return { userId: parsed.user.id, token: parsed.access_token };
      }
    }
  } catch(e) {}
  return null;
}

// 4. Render the Cache Instantly
function _renderCache(userId) {
  window._perf_patch_active = true;

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

// 5. Fetch Fresh Data (Background Sync)
async function _fetchFresh(userId, token) {
  const hdrs = { Authorization: "Bearer " + token };
  let data = null;

  try {
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 6000);
    const res = await fetch(_api() + "/api/startup", { headers: hdrs, signal: controller.signal });
    if (res.status === 401) { window._perf_patch_failed_auth = true; return; }
    if (res.ok) data = await res.json();
  } catch(e) {}

  // Fallback if batch endpoint unavailable
  if (!data) {
    try {
      const hRes = await fetch(_api() + "/history", { method: "POST", headers: { ...hdrs, "Content-Type": "application/json" }, body: "{}" });
      if (hRes.status === 401) { window._perf_patch_failed_auth = true; return; }
      
      const mRes = await fetch(_api() + "/api/health-markers", { headers: hdrs });
      data = {
        history: hRes.ok ? await hRes.json() : [],
        markers: mRes.ok ? await mRes.json() : [],
        cliff_alerts: []
      };
    } catch(e) { return; }
  }

  // Update Caches and DOM
  if (Array.isArray(data.history) && data.history.length) {
    Cache.set("history_" + userId, data.history);
    _when(() => typeof renderHistory === "function", () => renderHistory(data.history));
  }
  if (Array.isArray(data.markers) && data.markers.length) {
    Cache.set("markers_" + userId, data.markers);
    _when(() => typeof renderMarkers === "function", () => {
      renderMarkers(data.markers);
      if (typeof runCliffDetection === "function") runCliffDetection(data.markers);
    });
  }
  if (Array.isArray(data.cliff_alerts)) {
    _when(() => !!document.getElementById("cliffAlerts"), () => _renderAlerts(data.cliff_alerts));
  }
  if (data.goal_weight) {
    const gw = parseFloat(data.goal_weight);
    Cache.set("goal_wt_" + userId, gw);
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
      if (p || s || sl) Cache.set("shield_" + userId, { protein:p, steps:s, sleep:sl });
    } catch(e) {}
  });
}

function _renderAlerts(alerts) {
  const c = document.getElementById("cliffAlerts");
  if (!c) return;
  if (!alerts?.length) {
    c.innerHTML = `<div class="ca-item ca-ok"><div class="ca-title">✅ No rebound signals</div><div class="ca-desc">All monitored markers stable.</div></div>`;
    return;
  }
  c.innerHTML = alerts.map(a => {
    const cls   = a.severity === "high" ? "ca-danger" : "ca-warn";
    const icon  = a.severity === "high" ? "🚨" : "⚠️";
    const title  = String(a.headline || a.marker || "").replace(/</g,"&lt;");
    const detail = String(a.detail   || "").replace(/</g,"&lt;");
    return `<div class="ca-item ${cls}"><div class="ca-title">${icon} ${title}</div>${detail ? `<div class="ca-desc">${detail}</div>` : ""}</div>`;
  }).join("");
}

// 6. EXECUTE INSTANTLY ON SCRIPT PARSE
const authSync = _getAuthSync();
if (authSync) {
  _renderCache(authSync.userId);
  // Fire network fetch in parallel (non-blocking)
  setTimeout(() => _fetchFresh(authSync.userId, authSync.token), 0);
}

// 7. Clear Cache on Action
document.addEventListener("DOMContentLoaded", () => {
  _when(() => typeof processUpload === "function", () => {
    const _orig = processUpload;
    window.processUpload = async function(file) {
      const result = await _orig.call(this, file);
      if (result?.success !== false && authSync?.userId) {
        Cache.clear("markers_" + authSync.userId);
        Cache.clear("shield_"  + authSync.userId);
      }
      return result;
    };
  });

  _when(() => typeof doSignOut === "function", () => {
    const _orig = doSignOut;
    window.doSignOut = async function() {
      Cache.clearAll();
      return _orig.call(this);
    };
  });
});