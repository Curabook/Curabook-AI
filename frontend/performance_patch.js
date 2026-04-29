/**
 * performance_patch.js — Curabook PHI v7 (SHIELD FAILSAFE)
 *
 * FIXES: 
 * 1. Shield network fetch has a fallback to script.js if the fetch drops.
 * 2. Caches Shield data instantly for zero-latency mobile reloads.
 */
"use strict";

const Cache = {
  set(key, data) { try { localStorage.setItem("phi_cache_" + key, JSON.stringify(data)); } catch(e) {} },
  get(key) { try { const raw = localStorage.getItem("phi_cache_" + key); return raw ? JSON.parse(raw) : null; } catch(e) { return null; } },
  clear(key) { try { localStorage.removeItem("phi_cache_" + key); } catch(e) {} },
  clearAll() { try { Object.keys(localStorage).filter(k => k.startsWith("phi_cache_")).forEach(k => localStorage.removeItem(k)); } catch(e) {} }
};
window.Cache = Cache;

function _api() { return ["localhost","127.0.0.1","0.0.0.0"].includes(location.hostname) ? "http://localhost:5000" : "https://api.curabook.com"; }

function _when(condition, fn, maxMs = 8000) {
  if (condition()) { try { fn(); } catch(e) {} return; }
  const start = Date.now();
  const id = setInterval(() => {
    if (condition()) { clearInterval(id); try { fn(); } catch(e) {} return; }
    if (Date.now() - start > maxMs) clearInterval(id);
  }, 30);
}

function _getAuthSync() {
  try {
    const keys = Object.keys(localStorage).filter(k => k.startsWith("sb-") && k.endsWith("-auth-token"));
    for (const key of keys) {
      const parsed = JSON.parse(localStorage.getItem(key) || "{}");
      if (parsed?.user?.id && parsed?.access_token) return { userId: parsed.user.id, token: parsed.access_token };
    }
  } catch(e) {}
  return null;
}

function _renderCache(userId) {
  window._perf_patch_active = true;

  const cachedHistory = Cache.get("history_" + userId);
  const cachedMarkers = Cache.get("markers_" + userId);
  const cachedGw      = Cache.get("goal_wt_"  + userId);
  const cachedShield  = Cache.get("shield_" + userId);

  if (cachedHistory?.length) _when(() => typeof renderHistory === "function", () => renderHistory(cachedHistory));
  if (cachedMarkers?.length) _when(() => typeof renderMarkers === "function", () => {
    renderMarkers(cachedMarkers);
    if (typeof runCliffDetection === "function") runCliffDetection(cachedMarkers);
  });
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
    _when(() => typeof renderShield === "function", () => renderShield(cachedShield.protein || 0, cachedShield.steps || 0, cachedShield.sleep || 0, null));
  }
}

async function _fetchFresh(userId, token) {
  const hdrs = { Authorization: "Bearer " + token };
  let data = null;

  try {
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 6000);
    const res = await fetch(_api() + "/startup", { headers: hdrs, signal: controller.signal });
    if (res.status === 401) { window._perf_patch_failed_auth = true; return; }
    if (res.ok) data = await res.json();
  } catch(e) {}

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

  // ── FAILSAFE DIRECT SHIELD FETCH ──
  try {
    const sRes = await fetch(_api() + "/api/behavioral-logs?days=1", { headers: hdrs });
    if (!sRes.ok) throw new Error("HTTP " + sRes.status);
    const sData = await sRes.json();
    if (!Array.isArray(sData)) throw new Error("API did not return an array");

    const today = new Date().toISOString().slice(0, 10);
    const tl = sData.filter(l => l.date === today);
    const get = m => {
      const l = tl.filter(x => x.metric_name === m).sort((a,b)=>a.created_at<b.created_at?1:-1)[0];
      return l ? parseFloat(l.value) : 0;
    };
    const p = get("protein"), s = get("steps"), sl = get("sleep");
    
    Cache.set("shield_" + userId, { protein: p, steps: s, sleep: sl });

    _when(() => typeof renderShield === "function", () => {
      const ep = document.getElementById("inputProtein"); if (ep && p>0) ep.value = p;
      const es = document.getElementById("inputSteps");   if (es && s>0) es.value = s;
      const esl = document.getElementById("inputSleep");  if (esl && sl>0) esl.value = sl;
      renderShield(p, s, sl, today);
      
      if (tl.length > 0) {
        const last = tl.sort((a, b) => a.created_at < b.created_at ? 1 : -1)[0];
        const w = new Date(last.created_at);
        const text = `Last logged: Today at ${w.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}`;
        const el = document.getElementById("shieldLastLogged");
        if(el) el.textContent = text;
      }
    });
  } catch(e) { 
    console.warn("[PERF] Shield fast-fetch failed, safely delegating to script.js", e.message); 
    _when(() => typeof autoLoadShield === "function", () => {
        autoLoadShield().catch(()=>{});
    });
  }
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

const authSync = _getAuthSync();
if (authSync) {
  _renderCache(authSync.userId);
  setTimeout(() => _fetchFresh(authSync.userId, authSync.token), 0);
}

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