/**
 * cockpit_upgrades.js — Curabook PHI v2.0 Vision Edition
 *
 * Drop this file in /frontend/ and add <script src="cockpit_upgrades.js"></script>
 * at the END of index.html (after script.js).
 *
 * What this adds:
 *   1. CAMERA-1  — Mobile camera integration (photograph lab reports directly)
 *   2. CHART-1   — Muscle vs Fat dual-line chart (Quality of Maintenance)
 *   3. TOAST-UX  — Upload progress messages with photo-specific guidance
 *   4. RADAR     — Real-time cliff risk badge in the cockpit header
 *   5. WIRING    — Patches the existing #fileInput accept attribute for images
 *
 * Requirements:
 *   - Chart.js loaded from CDN (added by upgrades_head_snippet.html)
 *   - script.js already loaded (this patches, not replaces, existing functions)
 */
"use strict";

/* ═══════════════════════════════════════════════════════════════════════
   CAMERA-1: Patch file input to accept photos + trigger camera on mobile
═══════════════════════════════════════════════════════════════════════ */

(function patchFileInputs() {
  // Wait for DOM + script.js to be fully loaded
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", patchFileInputs);
    return;
  }

  // Patch the existing hidden file input
  const existingInput = document.getElementById("fileInput");
  if (existingInput) {
    existingInput.accept = ".pdf,.txt,.jpg,.jpeg,.png,.webp,.heic,image/*";
    // Note: we do NOT add capture="environment" here because that disables
    // the gallery option on iOS — users should choose. The camera button
    // (below) uses a dedicated input with capture="environment".
  }

  // Create a second input specifically for the camera button
  const cameraInput = document.createElement("input");
  cameraInput.type    = "file";
  cameraInput.id      = "cameraInput";
  cameraInput.accept  = "image/*";
  cameraInput.capture = "environment";
  cameraInput.style.display = "none";
  document.body.appendChild(cameraInput);

  cameraInput.addEventListener("change", (e) => {
    if (typeof addFile === "function") {
      Array.from(e.target.files || []).forEach(addFile);
    }
    e.target.value = "";
  });

  // Wire up all camera trigger buttons
  document.querySelectorAll("[data-camera-trigger], #cameraBtn, #mobileCameraBtn").forEach(btn => {
    btn.addEventListener("click", () => cameraInput.click());
  });
})();


/* ═══════════════════════════════════════════════════════════════════════
   CHART-1: Muscle vs Fat — Quality of Maintenance dual-line chart
   Shows the story that MATTERS: lean mass stability > scale weight
═══════════════════════════════════════════════════════════════════════ */

const MuscleChart = (() => {
  let _chartInstance = null;
  let _container     = null;

  function _createContainer() {
    const section = document.createElement("section");
    section.className = "cp-section";
    section.id        = "muscleChartSection";
    section.innerHTML = `
      <div class="cp-section-hd">
        <h2 class="cp-section-title">Quality of Maintenance</h2>
        <span class="cp-section-badge" id="compBadge" style="font-size:.65rem;background:var(--ok-dim);color:var(--ok);padding:2px 8px;border-radius:20px;border:1px solid rgba(74,222,128,.2)">Recomp Active</span>
      </div>
      <p class="cp-caption" style="margin-bottom:10px;">
        Lean mass preservation matters more than the number on the scale.
        <em style="color:var(--signal)">Flat weight + shrinking fat = winning.</em>
      </p>
      <div style="position:relative;height:160px;width:100%;">
        <canvas id="muscleChartCanvas"></canvas>
      </div>
      <div id="compStats" style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:10px;"></div>
    `;
    return section;
  }

  function _waitForChartJS(cb, attempts = 0) {
    if (window.Chart) { cb(); return; }
    if (attempts > 30) { console.warn("[MuscleChart] Chart.js not loaded"); return; }
    setTimeout(() => _waitForChartJS(cb, attempts + 1), 200);
  }

  function _renderStats(leanKg, fatKg, leanPct, trend) {
    const el = document.getElementById("compStats");
    if (!el) return;
    const trendColor = trend === "improving" ? "var(--ok)" : trend === "warning" ? "var(--amber)" : "var(--text-3)";
    const trendIcon  = trend === "improving" ? "↗" : trend === "warning" ? "↘" : "→";
    el.innerHTML = `
      <div style="background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:8px 10px;text-align:center;">
        <div style="font-family:var(--mono);font-size:1rem;font-weight:500;color:var(--ok)">${leanPct}%</div>
        <div style="font-size:.65rem;color:var(--text-3);text-transform:uppercase;letter-spacing:.06em;margin-top:2px">Lean Mass</div>
      </div>
      <div style="background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:8px 10px;text-align:center;">
        <div style="font-family:var(--mono);font-size:1rem;font-weight:500;color:${trendColor}">${trendIcon} ${trend}</div>
        <div style="font-size:.65rem;color:var(--text-3);text-transform:uppercase;letter-spacing:.06em;margin-top:2px">Composition</div>
      </div>`;
  }

  function render(markers) {
    // Find weight + body fat readings over time, or simulate from available data
    const weightHistory = markers
      .filter(m => /weight|body weight/i.test(m.marker_name || ""))
      .sort((a, b) => a.date < b.date ? -1 : 1);

    const fatHistory = markers
      .filter(m => /body fat|fat %|fat percent/i.test(m.marker_name || ""))
      .sort((a, b) => a.date < b.date ? -1 : 1);

    // If we have weight history, use it; otherwise show illustrative data
    let labels = [], weightData = [], leanData = [];
    const hasBothSeries = weightHistory.length >= 2 && fatHistory.length >= 2;

    if (hasBothSeries) {
      // Real data path
      const weights    = weightHistory.map(m => ({ date: m.date, val: parseFloat(m.value) }));
      const fatPcts    = fatHistory.map(m => ({ date: m.date, val: parseFloat(m.value) }));
      const minLen     = Math.min(weights.length, fatPcts.length);
      for (let i = 0; i < minLen; i++) {
        const w      = weights[i].val;
        const fatPct = fatPcts[i].val;
        const fatLbs = w * (fatPct / 100);
        const lean   = w - fatLbs;
        labels.push(weights[i].date.slice(5));     // MM-DD
        weightData.push(w);
        leanData.push(parseFloat(lean.toFixed(1)));
      }
    } else if (weightHistory.length >= 2) {
      // Weight-only path: estimate lean as 70% of weight (conservative)
      weightHistory.forEach(m => {
        const w = parseFloat(m.value);
        labels.push(m.date.slice(5));
        weightData.push(w);
        leanData.push(parseFloat((w * 0.70).toFixed(1)));
      });
    } else {
      // No data — show empty state
      document.getElementById("compStats").innerHTML =
        `<div style="grid-column:span 2;text-align:center;color:var(--text-3);font-size:.76rem;padding:8px;">
          Upload multiple reports over time to see your body composition trend.
        </div>`;
      return;
    }

    // Trend calculation
    const leanFirst = leanData[0];
    const leanLast  = leanData[leanData.length - 1];
    const leanPct   = weightData.length > 0
      ? Math.round((leanLast / weightData[weightData.length - 1]) * 100)
      : 70;
    const leanDelta = leanLast - leanFirst;
    const trend     = leanDelta >= 0 ? "improving" : leanDelta < -2 ? "warning" : "stable";

    // Badge update
    const badge = document.getElementById("compBadge");
    if (badge) {
      const cfg = {
        improving: { text: "Recomp ↗", bg: "var(--ok-dim)",    color: "var(--ok)",    border: "rgba(74,222,128,.2)" },
        stable:    { text: "Stable →",  bg: "var(--signal-dim)", color: "var(--signal)", border: "rgba(0,212,200,.2)"  },
        warning:   { text: "Watch ↘",   bg: "var(--amber-dim)",  color: "var(--amber)",  border: "rgba(251,191,36,.2)" },
      }[trend];
      badge.textContent = cfg.text;
      badge.style.background = cfg.bg;
      badge.style.color      = cfg.color;
      badge.style.border     = `1px solid ${cfg.border}`;
    }

    _renderStats(leanLast.toFixed(1), (weightData[weightData.length-1] - leanLast).toFixed(1), leanPct, trend);

    // Chart
    _waitForChartJS(() => {
      const canvas = document.getElementById("muscleChartCanvas");
      if (!canvas) return;
      const ctx = canvas.getContext("2d");

      if (_chartInstance) { _chartInstance.destroy(); _chartInstance = null; }

      const isDark = document.documentElement.dataset.theme !== "light";
      const gridColor  = isDark ? "rgba(255,255,255,.06)" : "rgba(0,0,0,.06)";
      const labelColor = isDark ? "#5a6070" : "#9ca3af";
      const signal     = isDark ? "#00d4c8" : "#00bcd5";
      const amber      = "#fbbf24";

      _chartInstance = new Chart(ctx, {
        type: "line",
        data: {
          labels,
          datasets: [
            {
              label:           "Total Weight (lbs)",
              data:            weightData,
              borderColor:     amber,
              backgroundColor: "rgba(251,191,36,.08)",
              borderWidth:     2,
              tension:         0.4,
              pointRadius:     3,
              pointHoverRadius: 5,
              fill:            false,
            },
            {
              label:           "Lean Mass (lbs)",
              data:            leanData,
              borderColor:     signal,
              backgroundColor: "rgba(0,212,200,.08)",
              borderWidth:     2.5,
              tension:         0.4,
              pointRadius:     3,
              pointHoverRadius: 5,
              fill:            true,
            },
          ],
        },
        options: {
          responsive:          true,
          maintainAspectRatio: false,
          animation:           { duration: 800, easing: "easeOutQuart" },
          plugins: {
            legend: {
              position:  "top",
              labels:    { color: labelColor, font: { size: 10 }, boxWidth: 12, padding: 10 },
            },
            tooltip: {
              backgroundColor: isDark ? "#1a1d24" : "#fff",
              borderColor:     isDark ? "rgba(255,255,255,.1)" : "rgba(0,0,0,.1)",
              borderWidth:     1,
              titleColor:      isDark ? "#f0f2f5" : "#111",
              bodyColor:       isDark ? "#9aa3b0" : "#6b7280",
              callbacks: {
                label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y} lbs`,
              },
            },
          },
          scales: {
            x: {
              grid:   { color: gridColor },
              ticks:  { color: labelColor, font: { size: 9 }, maxTicksLimit: 6 },
            },
            y: {
              grid:   { color: gridColor },
              ticks:  { color: labelColor, font: { size: 9 },
                        callback: v => `${v}` },
            },
          },
        },
      });
    });
  }

  function init() {
    const cockpit = document.getElementById("cockpit");
    if (!cockpit) return;

    // Insert after the Metabolic Shield section
    const shieldSection = cockpit.querySelector(".cp-section:first-of-type");
    _container = _createContainer();
    if (shieldSection && shieldSection.nextSibling) {
      cockpit.insertBefore(_container, shieldSection.nextSibling);
    } else {
      cockpit.appendChild(_container);
    }
  }

  return { init, render };
})();


/* ═══════════════════════════════════════════════════════════════════════
   CAMERA BUTTON: Inject camera icon into topbar for mobile
═══════════════════════════════════════════════════════════════════════ */

(function injectCameraButton() {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", injectCameraButton);
    return;
  }

  // Only show camera button on touch devices (mobile)
  const isMobile = navigator.maxTouchPoints > 0 && window.innerWidth < 900;
  if (!isMobile) return;

  const topbarRight = document.querySelector(".topbar-right");
  if (!topbarRight) return;

  const cameraBtn = document.createElement("button");
  cameraBtn.id          = "cameraCaptureBtn";
  cameraBtn.className   = "topbar-icon-btn mobile-only";
  cameraBtn.title       = "Photograph a lab report";
  cameraBtn.setAttribute("aria-label", "Take photo of lab report");
  cameraBtn.innerHTML   = '<i class="fa-solid fa-camera"></i>';
  cameraBtn.setAttribute("data-camera-trigger", "true");

  // Insert before the existing attach button
  const attachBtn = document.getElementById("attachTopBtn");
  if (attachBtn) {
    topbarRight.insertBefore(cameraBtn, attachBtn);
  } else {
    topbarRight.prepend(cameraBtn);
  }
})();


/* ═══════════════════════════════════════════════════════════════════════
   TOAST-UX: Photo-specific upload guidance messages
   Patches the global addFile() function to show better messages for images
═══════════════════════════════════════════════════════════════════════ */

(function patchAddFileToasts() {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", patchAddFileToasts);
    return;
  }

  // Override the upload nudge button text on mobile
  const nudgeBtn = document.getElementById("uploadNudgeBtn");
  if (nudgeBtn && navigator.maxTouchPoints > 0) {
    nudgeBtn.innerHTML = '<i class="fa-solid fa-camera"></i> Photo or Upload Lab Report';
  }

  // Override file input label in input bar
  const chatInput = document.getElementById("chatInput");
  if (chatInput && navigator.maxTouchPoints > 0) {
    chatInput.placeholder = "Ask PHI or photograph your lab report…";
  }

  // Patch processUpload to show photo-specific loading message
  const origProcessUpload = window.processUpload;
  if (typeof origProcessUpload === "function") {
    window.processUpload = async function(file) {
      const isImage = /\.(jpg|jpeg|png|webp|heic)$/i.test(file.name);
      if (isImage) {
        // Update the typing indicator with photo-specific message
        const typingEl = document.querySelector(".chat-msg.ai-msg:last-child .msg-body");
        if (typingEl) {
          typingEl.textContent = "📸 Sending to Vision AI for OCR…";
        }
        // Show a toast with expected wait time
        if (typeof toast === "function") {
          toast("Photo received — Vision AI is reading your lab report (10-20 seconds)…", "info");
        }
      }
      return origProcessUpload.call(this, file);
    };
  }
})();


/* ═══════════════════════════════════════════════════════════════════════
   RADAR: Live cliff risk score in the cockpit header
   Updates every time markers are loaded via loadMarkersData()
═══════════════════════════════════════════════════════════════════════ */

(function injectCliffRadar() {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", injectCliffRadar);
    return;
  }

  // Inject a mini cliff score strip at the very top of the cockpit
  const cockpit = document.getElementById("cockpit");
  if (!cockpit) return;

  const strip = document.createElement("div");
  strip.id = "cliffRadarStrip";
  strip.style.cssText = `
    display:flex; align-items:center; justify-content:space-between;
    padding:10px 15px 8px;
    border-bottom:1px solid var(--border);
    background:var(--surface);
    font-size:.72rem;
    position:sticky; top:0; z-index:5;
  `;
  strip.innerHTML = `
    <span style="color:var(--text-3);font-weight:700;letter-spacing:.08em;text-transform:uppercase;">Cliff Monitor</span>
    <div style="display:flex;align-items:center;gap:6px;">
      <span id="radarStatus" style="color:var(--ok);font-weight:600;">● Stable</span>
      <span id="radarScore" style="font-family:var(--mono);font-size:.75rem;color:var(--text-3)"></span>
    </div>
  `;
  // Insert before the first cp-section
  const firstSection = cockpit.querySelector(".cp-section");
  if (firstSection) {
    cockpit.insertBefore(strip, firstSection);
  } else {
    cockpit.prepend(strip);
  }

  // Patch runCliffDetection to also update the radar strip
  const origRunCliffDetection = window.runCliffDetection;
  if (typeof origRunCliffDetection === "function") {
    window.runCliffDetection = function(markers) {
      origRunCliffDetection.call(this, markers);
      updateRadar(markers);
      // Also render the muscle chart with the new data
      MuscleChart.render(markers);
    };
  }

  function updateRadar(markers) {
    const status = document.getElementById("radarStatus");
    const score  = document.getElementById("radarScore");
    if (!status || !score) return;

    const abnormal = markers.filter(m => m.status === "HIGH" || m.status === "LOW");
    const grouped  = {};
    markers.forEach(m => {
      const k = (m.marker_name || "").toLowerCase();
      if (!grouped[k]) grouped[k] = [];
      grouped[k].push({ ...m, _v: parseFloat(m.value) });
    });

    let highAlerts = 0;

    // Glucose rebound check
    const gk = Object.keys(grouped).find(k => /fasting.*glucose|blood.*glucose|^glucose/.test(k));
    if (gk) {
      const r = grouped[gk].sort((a, b) => a.date < b.date ? -1 : 1);
      if (r.length >= 2) {
        const pct = ((r[r.length-1]._v - r[0]._v) / r[0]._v) * 100;
        if (pct >= 15) highAlerts++;
      }
    }

    // HbA1c rebound check
    const hk = Object.keys(grouped).find(k => /hba1c/.test(k));
    if (hk) {
      const r = grouped[hk].sort((a, b) => a.date < b.date ? -1 : 1);
      for (let i = 1; i < r.length; i++) {
        if (r[i]._v - r[i-1]._v >= 0.25) { highAlerts++; break; }
      }
    }

    if (highAlerts > 0) {
      status.style.color   = "var(--danger)";
      status.textContent   = `🚨 ${highAlerts} Alert${highAlerts > 1 ? "s" : ""}`;
      score.textContent    = "Act now";
    } else if (abnormal.length > 2) {
      status.style.color   = "var(--amber)";
      status.textContent   = `⚠ ${abnormal.length} Markers`;
      score.textContent    = "Monitor";
    } else if (abnormal.length > 0) {
      status.style.color   = "var(--amber)";
      status.textContent   = `⚠ ${abnormal.length} Marker`;
      score.textContent    = "Watch";
    } else {
      status.style.color   = "var(--ok)";
      status.textContent   = "● Stable";
      score.textContent    = markers.length > 0 ? `${markers.length} tracked` : "";
    }
  }
})();


/* ═══════════════════════════════════════════════════════════════════════
   INIT: Set up chart container and load Chart.js if not already loaded
═══════════════════════════════════════════════════════════════════════ */

(function init() {
  function setup() {
    // Inject Chart.js if not already loaded
    if (!window.Chart) {
      const s  = document.createElement("script");
      s.src    = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js";
      s.defer  = true;
      s.onload = () => MuscleChart.init();
      document.head.appendChild(s);
    } else {
      MuscleChart.init();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setup);
  } else {
    setup();
  }
})();