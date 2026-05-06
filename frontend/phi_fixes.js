/**
 * phi_fixes.js — v2 (400 error fixed)
 *
 * FIX 1: Wearable/Watch Screenshot — uses /analyze endpoint directly
 *   Root cause of 400: /chat requires a valid conversation_id, which is
 *   null when no chat is open. Fix: use /analyze (document upload endpoint)
 *   which has no conversation requirement, then parse the returned
 *   document_text + markers for health values.
 *
 * FIX 2: Feedback FAB — Moved into sidebar footer
 *   Removes the floating button that overlapped the send button on mobile.
 */
"use strict";

// ═══════════════════════════════════════════════════════════════════════
// FIX 2: FEEDBACK BUTTON — Remove FAB, inject into sidebar footer
// ═══════════════════════════════════════════════════════════════════════

document.addEventListener("DOMContentLoaded", () => {

  // Remove FAB now and after initFeedback() runs
  function _removeFab() {
    const fab = document.getElementById("feedbackBtn");
    if (fab) fab.remove();
  }
  _removeFab();
  setTimeout(_removeFab, 500);
  setTimeout(_removeFab, 1500);

  // Add compact feedback row inside sidebar footer
  const sbFooter = document.querySelector(".sb-footer");
  if (sbFooter && !document.getElementById("sidebarFeedbackBtn")) {
    const btn = document.createElement("button");
    btn.id = "sidebarFeedbackBtn";
    btn.setAttribute("aria-label", "Send feedback");
    btn.innerHTML = `<i class="fa-regular fa-comment-dots"></i><span>Share Feedback</span>`;
    btn.style.cssText = `
      width:100%; display:flex; align-items:center; gap:9px;
      padding:0 14px; min-height:40px; border:none; background:none;
      color:var(--text-3); font-size:.82rem; font-family:var(--sans);
      font-weight:500; cursor:pointer; border-radius:var(--r);
      margin-bottom:4px; transition:all .15s;
    `;
    btn.onmouseenter = () => { btn.style.background = "var(--surface-2)"; btn.style.color = "var(--text)"; };
    btn.onmouseleave = () => { btn.style.background = "none"; btn.style.color = "var(--text-3)"; };
    btn.onclick = () => {
      if (typeof closeSidebar === "function") closeSidebar();
      if (typeof openFeedback === "function") openFeedback();
    };
    sbFooter.insertBefore(btn, sbFooter.firstChild);
  }
});

// ═══════════════════════════════════════════════════════════════════════
// FIX 1: WEARABLE SCREENSHOT — Direct /analyze endpoint (no conv needed)
// ═══════════════════════════════════════════════════════════════════════

(function patchSyncWearable() {

  const API = ["localhost", "127.0.0.1", "0.0.0.0"].includes(location.hostname)
    ? "http://localhost:5000"
    : "https://api.curabook.com";

  const _el = id => document.getElementById(id);

  const _toast = (msg, type = "info") => {
    const c = _el("toasts");
    if (!c) return;
    const t = document.createElement("div");
    const icons = { ok: "circle-check", err: "circle-exclamation", info: "circle-info" };
    t.className = `toast toast-${type}`;
    t.innerHTML = `<i class="fa-solid fa-${icons[type] || "circle-info"}"></i> ${msg}`;
    c.appendChild(t);
    setTimeout(() => {
      t.style.opacity = "0";
      t.style.transition = "opacity .3s";
      setTimeout(() => t.remove(), 300);
    }, 4500);
  };

  // Get auth token — tries script.js session() first, then localStorage fallback
  async function _getToken() {
    if (typeof session === "function") {
      try {
        const s = await session();
        if (s?.access_token) return s.access_token;
      } catch (e) {}
    }
    try {
      const keys = Object.keys(localStorage).filter(
        k => k.startsWith("sb-") && k.endsWith("-auth-token")
      );
      for (const key of keys) {
        const parsed = JSON.parse(localStorage.getItem(key) || "{}");
        if (parsed?.access_token) return parsed.access_token;
      }
    } catch (e) {}
    return null;
  }

  // ── MAIN: Process wearable screenshot via /analyze ──────────────────
  // Uses multipart FormData — same as the paperclip upload flow.
  // /analyze does NOT need a conversation_id, so no 400 error.
  async function _processWearableImage(file) {
    const btn      = _el("syncWearableBtn");
    const origHTML = btn?.innerHTML || "";

    if (btn) {
      btn.disabled  = true;
      btn.innerHTML = `<i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i> Reading…`;
    }

    _toast("📸 Vision AI reading your screenshot…", "info");

    try {
      // Step 1: Auth
      const token = await _getToken();
      if (!token) {
        _toast("Session expired — please refresh the page.", "err");
        return;
      }

      // Step 2: Upload to /analyze as multipart (same as normal file upload)
      const form = new FormData();
      // Give it a .jpg extension so the server recognises it as an image
      const safeName = file.name?.match(/\.(jpg|jpeg|png|webp|heic)$/i)
        ? file.name
        : "wearable_screenshot.jpg";
      form.append("file", file, safeName);

      const res = await fetch(API + "/analyze", {
        method:  "POST",
        headers: { Authorization: "Bearer " + token },
        // DO NOT set Content-Type — browser sets multipart boundary automatically
        body:    form,
        signal:  AbortSignal.timeout(60000),
      });

      if (res.status === 401) {
        _toast("Session expired — please refresh.", "err");
        return;
      }

      if (!res.ok) {
        let errMsg = `Server error ${res.status}`;
        try {
          const d = await res.json();
          errMsg = d?.error || d?.message || errMsg;
        } catch (_) {}
        throw new Error(errMsg);
      }

      const data = await res.json();

      // Step 3: Parse OCR text + backend markers for health values
      const rawText       = data?.document_text || data?.summary_text || "";
      const backendMarkers = data?.markers || [];
      const extracted     = _parseWearableData(rawText, backendMarkers);

      if (!extracted.hasAny) {
        _toast(
          "⚠️ Couldn't read health metrics. Try a brighter, clearer photo of your watch face.",
          "err"
        );
        return;
      }

      // Step 4: Fill Shield inputs
      _applyToShield(extracted);

      // Step 5: Log to /api/behavioral-logs
      await _logMetrics(extracted, token);

      // Step 6: Re-render Shield rings
      const p  = extracted.protein || parseFloat(_el("inputProtein")?.value)  || 0;
      const s  = extracted.steps   || parseFloat(_el("inputSteps")?.value)    || 0;
      const sl = extracted.sleep   || parseFloat(_el("inputSleep")?.value)    || 0;
      if (typeof renderShield === "function") {
        renderShield(p, s, sl, new Date().toISOString().slice(0, 10));
      }

      // Step 7: Success toast
      const parts = [];
      if (extracted.protein)   parts.push(`${extracted.protein}g protein`);
      if (extracted.steps)     parts.push(`${extracted.steps.toLocaleString()} steps`);
      if (extracted.sleep)     parts.push(`${extracted.sleep}h sleep`);
      if (extracted.weight)    parts.push(`${extracted.weight} lbs`);
      if (extracted.calories)  parts.push(`${extracted.calories.toLocaleString()} kcal`);
      if (extracted.heartRate) parts.push(`${extracted.heartRate} bpm`);

      _toast(
        parts.length ? `✅ Synced: ${parts.join(" · ")}` : "✅ Shield updated.",
        "ok"
      );

      _showSummaryCard(extracted);

    } catch (err) {
      console.error("[WEARABLE]", err);
      _toast(`Screenshot failed: ${(err.message || "Unknown error").slice(0, 100)}`, "err");
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = origHTML; }
    }
  }

  // ── Parse OCR text + backend markers for health values ──────────────
  function _parseWearableData(text, backendMarkers) {
    const extracted = { hasAny: false };

    // First: check backend-extracted markers (most reliable)
    for (const m of backendMarkers) {
      const name = (m.marker || m.marker_name || "").toLowerCase();
      const val  = parseFloat(m.value);
      if (isNaN(val)) continue;
      if (name.includes("step"))                              { extracted.steps    = val; extracted.hasAny = true; }
      if (name.includes("protein"))                          { extracted.protein  = val; extracted.hasAny = true; }
      if (name.includes("sleep"))                            { extracted.sleep    = val; extracted.hasAny = true; }
      if (name.includes("weight"))                           { extracted.weight   = val; extracted.hasAny = true; }
      if (name.includes("calori"))                           { extracted.calories = val; extracted.hasAny = true; }
      if (name.includes("heart") || name.includes("bpm"))   { extracted.heartRate= val; extracted.hasAny = true; }
    }

    if (!text) return extracted;

    const _find = (patterns) => {
      for (const pat of patterns) {
        const m = text.match(pat);
        if (m) {
          const v = parseFloat((m[1] || "").replace(/,/g, ""));
          if (!isNaN(v)) return v;
        }
      }
      return null;
    };

    // Steps
    if (!extracted.steps) {
      const v = _find([
        /(\d[\d,]*)\s*steps/i,
        /steps[:\s]+([\d,]+)/i,
        /step\s*count[:\s]+([\d,]+)/i,
      ]);
      if (v && v >= 100 && v <= 100000) { extracted.steps = v; extracted.hasAny = true; }
    }

    // Protein
    if (!extracted.protein) {
      const v = _find([
        /protein[:\s]+(\d+\.?\d*)\s*g/i,
        /(\d+\.?\d*)\s*g\s+(?:of\s+)?protein/i,
      ]);
      if (v && v >= 5 && v <= 400) { extracted.protein = v; extracted.hasAny = true; }
    }

    // Sleep — "7h 32m", "7.5h", "7 hours"
    if (!extracted.sleep) {
      const hm = text.match(/(\d+)\s*h(?:ours?)?\s*(\d+)\s*m(?:in)?/i);
      if (hm) {
        const hrs = parseInt(hm[1]) + parseInt(hm[2]) / 60;
        if (hrs >= 0 && hrs <= 14) { extracted.sleep = Math.round(hrs * 10) / 10; extracted.hasAny = true; }
      }
      if (!extracted.sleep) {
        const v = _find([
          /sleep[:\s]+(\d+\.?\d*)\s*h/i,
          /(\d+\.?\d*)\s*h(?:ours?)?\s+(?:of\s+)?sleep/i,
          /time\s+asleep[:\s]+(\d+\.?\d*)/i,
          /slept?\s+(\d+\.?\d*)\s*h/i,
        ]);
        if (v && v >= 0 && v <= 14) { extracted.sleep = v; extracted.hasAny = true; }
      }
    }

    // Calories / active energy
    if (!extracted.calories) {
      const v = _find([
        /(\d[\d,]*)\s*(?:kcal|cal)\b/i,
        /calori(?:es?)[:\s]+([\d,]+)/i,
        /active\s+energy[:\s]+([\d,]+)/i,
        /energy\s+burned[:\s]+([\d,]+)/i,
      ]);
      if (v && v >= 50 && v <= 10000) { extracted.calories = v; extracted.hasAny = true; }
    }

    // Weight
    if (!extracted.weight) {
      const v = _find([
        /(\d+\.?\d*)\s*lbs?/i,
        /weight[:\s]+(\d+\.?\d*)/i,
      ]);
      // Also handle kg
      const kgMatch = text.match(/(\d+\.?\d*)\s*kg\b/i);
      const lbs = v && v >= 80 && v <= 500 ? v
        : kgMatch ? Math.round(parseFloat(kgMatch[1]) * 2.20462 * 10) / 10
        : null;
      if (lbs && lbs >= 80 && lbs <= 500) { extracted.weight = lbs; extracted.hasAny = true; }
    }

    // Heart rate
    if (!extracted.heartRate) {
      const v = _find([
        /(\d+)\s*bpm/i,
        /heart\s+rate[:\s]+(\d+)/i,
        /(?:resting\s+)?hr[:\s]+(\d+)/i,
      ]);
      if (v && v >= 30 && v <= 220) { extracted.heartRate = v; extracted.hasAny = true; }
    }

    return extracted;
  }

  // ── Fill Shield cockpit inputs ──────────────────────────────────────
  function _applyToShield(ex) {
    if (ex.protein) { const e = _el("inputProtein"); if (e) e.value = ex.protein; }
    if (ex.steps)   { const e = _el("inputSteps");   if (e) e.value = ex.steps;   }
    if (ex.sleep)   { const e = _el("inputSleep");   if (e) e.value = ex.sleep;   }
    if (ex.weight)  { const e = _el("inputGoalWt");  if (e && !e.value) e.value = ex.weight; }
  }

  // ── Log to /api/behavioral-logs ─────────────────────────────────────
  async function _logMetrics(ex, token) {
    const date = new Date().toISOString().slice(0, 10);
    const hdr  = { "Content-Type": "application/json", Authorization: "Bearer " + token };
    const rows = [];
    if (ex.protein)   rows.push({ date, metric_name: "protein",  value: ex.protein,   unit: "g"     });
    if (ex.steps)     rows.push({ date, metric_name: "steps",    value: ex.steps,     unit: "steps" });
    if (ex.sleep)     rows.push({ date, metric_name: "sleep",    value: ex.sleep,     unit: "hours" });
    if (ex.weight)    rows.push({ date, metric_name: "weight",   value: ex.weight,    unit: "lbs"   });
    if (ex.calories)  rows.push({ date, metric_name: "calories", value: ex.calories,  unit: "kcal"  });

    for (const row of rows) {
      try {
        await fetch(API + "/api/behavioral-logs", {
          method: "POST", headers: hdr,
          body: JSON.stringify(row),
          signal: AbortSignal.timeout(8000),
        });
      } catch (e) { console.warn("[WEARABLE] log error:", e.message); }
    }

    const lbl = _el("shieldLastLogged");
    if (lbl) lbl.textContent = `Last synced: Today at ${new Date().toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}`;
  }

  // ── Show summary card below sync button ─────────────────────────────
  function _showSummaryCard(ex) {
    let card = _el("wearableSyncSummary");
    if (!card) {
      const anchor = _el("syncWearableBtn");
      if (!anchor) return;
      card = document.createElement("div");
      card.id = "wearableSyncSummary";
      card.style.cssText = `
        margin-top:10px; padding:10px 12px;
        background:var(--signal-dim);
        border:1px solid rgba(0,212,200,.2);
        border-radius:var(--r-sm);
        font-size:.75rem; color:var(--signal); line-height:1.8;
        animation:fadeIn .3s ease;
      `;
      anchor.insertAdjacentElement("afterend", card);
    }
    const rows = [];
    if (ex.protein)   rows.push(`💪 Protein <strong>${ex.protein}g</strong>`);
    if (ex.steps)     rows.push(`👟 Steps <strong>${ex.steps.toLocaleString()}</strong>`);
    if (ex.sleep)     rows.push(`😴 Sleep <strong>${ex.sleep}h</strong>`);
    if (ex.weight)    rows.push(`⚖️ Weight <strong>${ex.weight} lbs</strong>`);
    if (ex.calories)  rows.push(`🔥 Calories <strong>${ex.calories.toLocaleString()} kcal</strong>`);
    if (ex.heartRate) rows.push(`❤️ HR <strong>${ex.heartRate} bpm</strong>`);
    card.innerHTML = `
      <div style="font-size:.63rem;font-weight:700;text-transform:uppercase;
        letter-spacing:.08em;color:var(--text-3);margin-bottom:5px;">
        📸 Synced ${new Date().toLocaleTimeString("en-US",{hour:"numeric",minute:"2-digit"})}
      </div>
      ${rows.join("<br>")}
    `;
    setTimeout(() => { if (card) card.style.opacity = "0.5"; }, 60000);
  }

  // ── Wire the sync button ────────────────────────────────────────────
  function _wire() {
    const syncBtn = _el("syncWearableBtn");
    if (!syncBtn) return;

    let fileInput = _el("wearableCameraInput");
    if (!fileInput) {
      fileInput = document.createElement("input");
      fileInput.type    = "file";
      fileInput.id      = "wearableCameraInput";
      fileInput.accept  = "image/*";
      
      // Remove or comment out the line below to allow file uploads on mobile
      // if (navigator.maxTouchPoints > 0) fileInput.capture = "environment"; 
      
      fileInput.style.display = "none";
      document.body.appendChild(fileInput);
    }

    // Clone to strip all previous event listeners (including initSyncWearable)
    const fresh = syncBtn.cloneNode(true);
    syncBtn.parentNode.replaceChild(fresh, syncBtn);

    fresh.addEventListener("click", e => {
      e.preventDefault();
      e.stopPropagation();
      fileInput.click();
    });

    fileInput.addEventListener("change", e => {
      const file = e.target.files?.[0];
      if (!file) return;
      e.target.value = "";
      _processWearableImage(file);
    });

    console.log("[PHI-FIXES] ✓ Wearable sync button wired to /analyze");
  }

  // Wait for script.js to finish, then overwrite its listener
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => setTimeout(_wire, 300));
  } else {
    setTimeout(_wire, 300);
  }

})();