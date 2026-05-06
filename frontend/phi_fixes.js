/**
 * phi_fixes.js — Two targeted fixes
 *
 * FIX 1: Wearable/Watch Screenshot — Direct Vision API processing
 *   The sync button now immediately sends the image to GPT-4o Vision,
 *   extracts protein/steps/sleep values, and updates the Shield —
 *   no manual chat send required.
 *
 * FIX 2: Feedback FAB — Moved to sidebar footer on mobile
 *   Replaces the floating FAB (which overlapped the send button) with
 *   a small icon button inside the sidebar footer next to the user row.
 *
 * DROP-IN: Add <script src="phi_fixes.js"></script> after script.js
 *   and after cockpit_upgrades.js in app.html.
 */
"use strict";

// ═══════════════════════════════════════════════════════════════════════
// FIX 2: MOVE FEEDBACK BUTTON — Remove FAB, add to sidebar footer
// Must run before initFeedback() wires events, so we patch early via
// DOMContentLoaded.
// ═══════════════════════════════════════════════════════════════════════

document.addEventListener("DOMContentLoaded", () => {

  // --- 2a. Kill the FAB if it already exists (from script.js initFeedback) ---
  function _removeFab() {
    const fab = document.getElementById("feedbackBtn");
    if (fab) fab.remove();
  }

  // Run immediately and again after a short delay (in case initFeedback
  // runs after DOMContentLoaded)
  _removeFab();
  setTimeout(_removeFab, 800);

  // --- 2b. Inject a small feedback icon into the sidebar footer ---
  //   We wait for the footer to exist (it always does after DOM load).
  const sbFooter = document.querySelector(".sb-footer");
  if (sbFooter) {
    // Create a compact feedback trigger that sits above the user-row
    const feedbackTrigger = document.createElement("button");
    feedbackTrigger.id = "sidebarFeedbackBtn";
    feedbackTrigger.setAttribute("aria-label", "Send feedback");
    feedbackTrigger.setAttribute("title", "Send feedback about PHI");
    feedbackTrigger.innerHTML = `
      <i class="fa-regular fa-comment-dots"></i>
      <span>Share Feedback</span>
    `;
    feedbackTrigger.style.cssText = `
      width: 100%;
      display: flex;
      align-items: center;
      gap: 9px;
      padding: 0 14px;
      min-height: 40px;
      border: none;
      background: none;
      color: var(--text-3);
      font-size: .82rem;
      font-family: var(--sans);
      font-weight: 500;
      cursor: pointer;
      border-radius: var(--r);
      margin-bottom: 4px;
      transition: all .15s;
    `;
    feedbackTrigger.addEventListener("mouseenter", () => {
      feedbackTrigger.style.background = "var(--surface-2)";
      feedbackTrigger.style.color = "var(--text)";
    });
    feedbackTrigger.addEventListener("mouseleave", () => {
      feedbackTrigger.style.background = "none";
      feedbackTrigger.style.color = "var(--text-3)";
    });
    feedbackTrigger.addEventListener("click", () => {
      // Close sidebar on mobile when opening feedback
      if (typeof closeSidebar === "function") closeSidebar();
      // Open the feedback modal (created by initFeedback in script.js)
      if (typeof openFeedback === "function") openFeedback();
    });

    // Insert before the user-row divider/user row
    sbFooter.insertBefore(feedbackTrigger, sbFooter.firstChild);
  }

  // --- 2c. Also remove FAB styles if they were injected into <head> ---
  // The FAB styles are injected by initFeedback() at runtime, so we patch
  // the position after the modal is created.
  setTimeout(() => {
    _removeFab();
    // Keep the modal itself — just remove the floating button
  }, 1500);

});

// ═══════════════════════════════════════════════════════════════════════
// FIX 1: WEARABLE SCREENSHOT — Direct Vision processing
// Replaces the initSyncWearable() behaviour from script.js
// ═══════════════════════════════════════════════════════════════════════

(function patchSyncWearable() {

  const API = ["localhost", "127.0.0.1", "0.0.0.0"].includes(location.hostname)
    ? "http://localhost:5000"
    : "https://api.curabook.com";

  // --- UI helpers (mirrors script.js) ---
  const _el   = id => document.getElementById(id);
  const _toast = (msg, type = "info") => {
    const c = _el("toasts"); if (!c) return;
    const t = document.createElement("div");
    const icons = { ok: "circle-check", err: "circle-exclamation", info: "circle-info" };
    t.className = `toast toast-${type}`;
    t.innerHTML = `<i class="fa-solid fa-${icons[type] || "circle-info"}"></i> ${msg}`;
    c.appendChild(t);
    setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; setTimeout(() => t.remove(), 300); }, 4000);
  };

  // --- Get auth headers ---
  async function _headers() {
    if (typeof headers === "function") return headers();
    // Fallback: read token from localStorage
    try {
      const keys = Object.keys(localStorage).filter(k => k.startsWith("sb-") && k.endsWith("-auth-token"));
      for (const key of keys) {
        const parsed = JSON.parse(localStorage.getItem(key) || "{}");
        if (parsed?.access_token) return {
          "Content-Type": "application/json",
          "Authorization": "Bearer " + parsed.access_token
        };
      }
    } catch (e) {}
    return null;
  }

  // --- Send image to backend Vision endpoint and extract health metrics ---
  async function _processWearableImage(file) {
    // Show processing state in cockpit
    const btn = _el("syncWearableBtn");
    const origHTML = btn ? btn.innerHTML : "";
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = `<i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i> Reading screenshot…`;
    }

    _toast("📸 Sending to Vision AI — reading your watch data…", "info");

    // Convert file to base64
    const base64 = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload  = () => resolve(reader.result.split(",")[1]); // strip data URL prefix
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });

    const mimeType = file.type || "image/jpeg";
    const dataUrl  = `data:${mimeType};base64,${base64}`;

    // Ask the backend to extract health data from the image via GPT-4o Vision
    const h = await _headers();
    if (!h) {
      _toast("Session expired — please refresh.", "err");
      if (btn) { btn.disabled = false; btn.innerHTML = origHTML; }
      return;
    }

    try {
      // Use the /chat endpoint with the image as document_text (base64 data URL)
      // and a specific extraction prompt
      const payload = {
        conversation_id: typeof _convId !== "undefined" ? _convId : ("wearable-" + Date.now()),
        message: (
          "Extract ALL health metrics from this wearable/watch screenshot. " +
          "I need: protein (grams), steps, sleep (hours), weight (lbs) if shown, " +
          "heart rate, calories burned, active minutes. " +
          "Return ONLY the numbers found — formatted like: " +
          "Protein: Xg | Steps: X | Sleep: Xh | Weight: Xlbs | etc. " +
          "If a metric is not visible, skip it."
        ),
        has_documents: true,
        document_text: dataUrl,
      };

      const res = await fetch(API + "/chat", {
        method: "POST",
        headers: h,
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(45000),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData?.message || `Server error ${res.status}`);
      }

      const data = await res.json();
      const reply = data?.reply || "";

      // Parse extracted values from the AI reply
      const extracted = _parseVisionReply(reply);

      if (!extracted.hasAny) {
        _toast("⚠️ Could not read metrics from this screenshot. Try a clearer image.", "err");
        if (btn) { btn.disabled = false; btn.innerHTML = origHTML; }
        return;
      }

      // Update the Shield input fields with extracted values
      _applyExtractedToShield(extracted);

      // Log to behavioral API
      await _logExtractedMetrics(extracted, h);

      // Refresh Shield display
      const p  = extracted.protein || parseFloat(_el("inputProtein")?.value) || 0;
      const s  = extracted.steps   || parseFloat(_el("inputSteps")?.value)   || 0;
      const sl = extracted.sleep   || parseFloat(_el("inputSleep")?.value)   || 0;
      if (typeof renderShield === "function") {
        renderShield(p, s, sl, new Date().toISOString().slice(0, 10));
      }

      // Build a readable success message
      const parts = [];
      if (extracted.protein) parts.push(`${extracted.protein}g protein`);
      if (extracted.steps)   parts.push(`${extracted.steps.toLocaleString()} steps`);
      if (extracted.sleep)   parts.push(`${extracted.sleep}h sleep`);
      if (extracted.weight)  parts.push(`${extracted.weight} lbs`);

      _toast(
        parts.length
          ? `✅ Synced from screenshot: ${parts.join(" · ")}`
          : "✅ Screenshot processed — Shield updated.",
        "ok"
      );

      // Show extracted metrics in a brief cockpit note
      _showExtractionSummary(extracted);

    } catch (err) {
      console.error("[WEARABLE]", err);
      _toast(`Screenshot read failed: ${err.message?.slice(0, 80) || "Try again."}`, "err");
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = origHTML; }
    }
  }

  // --- Parse the AI reply for numeric health values ---
  function _parseVisionReply(text) {
    const lower = text.toLowerCase();
    const extracted = { hasAny: false };

    const _find = (patterns) => {
      for (const pat of patterns) {
        const m = text.match(pat);
        if (m) return parseFloat(m[1].replace(",", ""));
      }
      return null;
    };

    // Protein (grams)
    const protein = _find([
      /protein[:\s]+(\d+\.?\d*)\s*g/i,
      /(\d+\.?\d*)\s*g(?:rams?)?\s+protein/i,
      /protein.*?(\d+\.?\d*)\s*g/i,
    ]);
    if (protein && protein >= 10 && protein <= 400) {
      extracted.protein = protein;
      extracted.hasAny  = true;
    }

    // Steps
    const steps = _find([
      /steps[:\s]+([\d,]+)/i,
      /([\d,]+)\s+steps/i,
      /step count[:\s]+([\d,]+)/i,
    ]);
    if (steps && steps >= 100 && steps <= 100000) {
      extracted.steps  = steps;
      extracted.hasAny = true;
    }

    // Sleep (hours)
    const sleep = _find([
      /sleep[:\s]+(\d+\.?\d*)\s*h(?:ours?)?/i,
      /(\d+\.?\d*)\s*h(?:ours?)?\s+(?:of\s+)?sleep/i,
      /slept[:\s]+(\d+\.?\d*)/i,
    ]);
    if (sleep && sleep >= 0 && sleep <= 14) {
      extracted.sleep  = sleep;
      extracted.hasAny = true;
    }

    // Weight (lbs)
    const weight = _find([
      /weight[:\s]+(\d+\.?\d*)\s*(?:lbs?|pounds?)/i,
      /(\d+\.?\d*)\s*(?:lbs?|pounds?)\s+weight/i,
    ]);
    if (weight && weight >= 80 && weight <= 500) {
      extracted.weight = weight;
      extracted.hasAny = true;
    }

    // Calories
    const calories = _find([
      /calor(?:ies?)[:\s]+([\d,]+)/i,
      /([\d,]+)\s+(?:kcal|calories?)/i,
    ]);
    if (calories && calories >= 100 && calories <= 10000) {
      extracted.calories = calories;
      extracted.hasAny   = true;
    }

    // Heart rate
    const hr = _find([
      /(?:heart rate|hr|bpm)[:\s]+(\d+)/i,
      /(\d+)\s*bpm/i,
    ]);
    if (hr && hr >= 30 && hr <= 220) {
      extracted.heartRate = hr;
      extracted.hasAny    = true;
    }

    return extracted;
  }

  // --- Apply extracted values to Shield input fields ---
  function _applyExtractedToShield(extracted) {
    if (extracted.protein) {
      const el = _el("inputProtein"); if (el) el.value = extracted.protein;
    }
    if (extracted.steps) {
      const el = _el("inputSteps"); if (el) el.value = extracted.steps;
    }
    if (extracted.sleep) {
      const el = _el("inputSleep"); if (el) el.value = extracted.sleep;
    }
    if (extracted.weight) {
      const el = _el("inputGoalWt"); if (el && !el.value) el.value = extracted.weight;
    }
  }

  // --- Log extracted metrics to /api/behavioral-logs ---
  async function _logExtractedMetrics(extracted, h) {
    const date = new Date().toISOString().slice(0, 10);
    const metrics = [];
    if (extracted.protein) metrics.push({ date, metric_name: "protein",    value: extracted.protein, unit: "g"     });
    if (extracted.steps)   metrics.push({ date, metric_name: "steps",      value: extracted.steps,   unit: "steps" });
    if (extracted.sleep)   metrics.push({ date, metric_name: "sleep",      value: extracted.sleep,   unit: "hours" });
    if (extracted.weight)  metrics.push({ date, metric_name: "weight",     value: extracted.weight,  unit: "lbs"   });
    if (extracted.calories)metrics.push({ date, metric_name: "calories",   value: extracted.calories,unit: "kcal"  });

    const logHeaders = { ...h };
    delete logHeaders["Content-Type"];
    logHeaders["Content-Type"] = "application/json";

    for (const metric of metrics) {
      try {
        await fetch(API + "/api/behavioral-logs", {
          method: "POST",
          headers: logHeaders,
          body: JSON.stringify(metric),
          signal: AbortSignal.timeout(8000),
        });
      } catch (e) {
        console.warn("[WEARABLE] Log error:", e.message);
      }
    }
  }

  // --- Show a small extraction summary card in the cockpit ---
  function _showExtractionSummary(extracted) {
    // Find or create a summary element in the Shield section
    let summary = _el("wearableSyncSummary");
    if (!summary) {
      const section = _el("syncWearableBtn")?.closest(".cp-section");
      if (!section) return;
      summary = document.createElement("div");
      summary.id = "wearableSyncSummary";
      summary.style.cssText = `
        margin-top: 10px;
        background: var(--signal-dim);
        border: 1px solid rgba(0,212,200,.2);
        border-radius: var(--r-sm);
        padding: 10px 12px;
        font-size: .75rem;
        color: var(--signal);
        line-height: 1.7;
        animation: fadeIn .3s ease;
      `;
      _el("syncWearableBtn")?.insertAdjacentElement("afterend", summary);
    }

    const rows = [];
    if (extracted.protein)   rows.push(`💪 Protein: <strong>${extracted.protein}g</strong>`);
    if (extracted.steps)     rows.push(`👟 Steps: <strong>${extracted.steps.toLocaleString()}</strong>`);
    if (extracted.sleep)     rows.push(`😴 Sleep: <strong>${extracted.sleep}h</strong>`);
    if (extracted.weight)    rows.push(`⚖️ Weight: <strong>${extracted.weight} lbs</strong>`);
    if (extracted.calories)  rows.push(`🔥 Calories: <strong>${extracted.calories.toLocaleString()} kcal</strong>`);
    if (extracted.heartRate) rows.push(`❤️ Heart rate: <strong>${extracted.heartRate} bpm</strong>`);

    summary.innerHTML = `
      <div style="font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;margin-bottom:5px;color:var(--text-3);">
        📸 From screenshot — ${new Date().toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
      </div>
      ${rows.join("<br>")}
    `;

    // Auto-hide after 30 seconds
    setTimeout(() => { if (summary) summary.style.opacity = "0.4"; }, 30000);
  }

  // --- Wire up the sync button to use direct Vision processing ---
  function _wireWearableButton() {
    const syncBtn = _el("syncWearableBtn");
    if (!syncBtn) return;

    // Create a dedicated camera input (no gallery-only restriction)
    let camInput = _el("wearableCameraInput");
    if (!camInput) {
      camInput = document.createElement("input");
      camInput.type    = "file";
      camInput.id      = "wearableCameraInput";
      camInput.accept  = "image/*";
      // On mobile: offer camera. On desktop: file picker.
      if (navigator.maxTouchPoints > 0) {
        camInput.capture = "environment";
      }
      camInput.style.display = "none";
      document.body.appendChild(camInput);
    }

    // Remove ALL existing click listeners by cloning the button
    const newBtn = syncBtn.cloneNode(true);
    syncBtn.parentNode.replaceChild(newBtn, syncBtn);

    newBtn.addEventListener("click", e => {
      e.preventDefault();
      camInput.click();
    });

    camInput.addEventListener("change", e => {
      const file = e.target.files?.[0];
      if (!file) return;
      e.target.value = ""; // reset so same file can be re-selected
      _processWearableImage(file);
    });
  }

  // Wire on DOM ready (may already be ready)
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _wireWearableButton);
  } else {
    // Delay slightly so script.js initSyncWearable() runs first, then we overwrite
    setTimeout(_wireWearableButton, 200);
  }

})();