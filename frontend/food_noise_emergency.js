/**
 * food_noise_emergency.js — PHI Food Noise Emergency Protocol
 * ═══════════════════════════════════════════════════════════════
 * 
 * This script injects the Food Noise Emergency Panel into the existing
 * Curabook PHI app (app.html / script.js) WITHOUT modifying those files.
 * 
 * INTEGRATION: Add to app.html AFTER phi_fixes.js:
 *   <script src="food_noise_emergency.js?v=1"></script>
 *
 * What it adds:
 *   1. A persistent "Food Noise" button in the cockpit (below Ghrelin slider)
 *   2. An emergency overlay triggered when food noise hits
 *   3. A 5-minute protocol with countdown timer
 *   4. Biological validation message (not willpower, it's ghrelin)
 *   5. Protein bridge suggestion based on stored goal weight
 *   6. Logs the food noise event to /api/behavioral-logs
 *   7. Sends context to chat with pre-loaded Food Noise Protocol
 * 
 * Completely non-destructive — removes itself cleanly if user closes.
 */
"use strict";

(function FoodNoiseEmergency() {

  // ── Constants ──────────────────────────────────────────────────────────────
  const API = ["localhost","127.0.0.1","0.0.0.0"].includes(location.hostname)
    ? "http://localhost:5000"
    : "https://api.curabook.com";

  const PROTOCOL_DURATION = 5 * 60; // 5 minutes in seconds

  const GHRELIN_MESSAGES = [
    {
      head: "This is ghrelin surge — not a willpower failure.",
      body: "GLP-1 medications suppress ghrelin, the hunger hormone. When your dose reduces or stops, ghrelin doesn't return to baseline — it overshoots. What you're feeling is your hypothalamus executing a survival program. It's biology."
    },
    {
      head: "Your lizard brain is in overdrive.",
      body: "The prefrontal cortex — your rational decision-making center — is being overridden by subcortical hunger signals. This is a documented physiological response to GLP-1 cessation, not a character flaw. It typically peaks 7–14 days post-dose."
    },
    {
      head: "Food noise is a GLP-1 withdrawal symptom.",
      body: "Clinical trial data shows ghrelin levels surge 20–40% above baseline after GLP-1 cessation. What feels like obsessive craving is your body demanding caloric intake at a hormonal level. You are not failing. Your medication is wearing off."
    },
    {
      head: "The 'lizard brain' is seeking — and it's lying to you.",
      body: "Ghrelin signals don't distinguish between real hunger and rebound hunger. The feeling of urgency is the signal — not a measure of how much you need to eat. A 35g protein response blunts ghrelin by approximately 25% within 20 minutes."
    }
  ];

  const INTERVENTIONS = [
    {
      id: "breath",
      icon: "🫁",
      label: "4-7-8 Breath",
      desc: "Activates parasympathetic nervous system. Counteracts the stress-hunger loop.",
      instruction: "Inhale 4 counts → hold 7 → exhale 8. Repeat 4 times.",
      time: "90 seconds"
    },
    {
      id: "protein",
      icon: "💪",
      label: "Protein Bridge",
      desc: "35g+ protein blunts ghrelin 25% within 20 minutes via CCK and PYY release.",
      instruction: null, // Dynamic — filled from goal weight
      time: "Now"
    },
    {
      id: "walk",
      icon: "🚶",
      label: "5-Minute Walk",
      desc: "Movement triggers GLP-1 endogenous production and breaks the food thought loop.",
      instruction: "Walk 5 minutes — outside if possible. The thought interruption alone helps.",
      time: "5 minutes"
    },
    {
      id: "cold",
      icon: "💧",
      label: "Cold Water",
      desc: "Cold water triggers gastric stretch receptors and mildly suppresses ghrelin.",
      instruction: "Drink one full glass of cold water slowly. Wait 10 minutes.",
      time: "2 minutes"
    }
  ];

  // ── Helpers ────────────────────────────────────────────────────────────────
  const el = id => document.getElementById(id);
  const toast = (msg, type = "ok") => {
    const c = el("toasts");
    if (!c) return;
    const t = document.createElement("div");
    t.className = `toast toast-${type}`;
    t.textContent = msg;
    c.appendChild(t);
    setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; setTimeout(() => t.remove(), 300); }, 3500);
  };

  async function getAuthToken() {
    // Try script.js session() first
    if (typeof session === "function") {
      try {
        const s = await session();
        if (s?.access_token) return s.access_token;
      } catch (e) {}
    }
    // localStorage fallback
    try {
      const keys = Object.keys(localStorage).filter(k => k.startsWith("sb-") && k.endsWith("-auth-token"));
      for (const key of keys) {
        const p = JSON.parse(localStorage.getItem(key) || "{}");
        if (p?.access_token) return p.access_token;
      }
    } catch (e) {}
    return null;
  }

  function getGoalWeight() {
    return parseFloat(localStorage.getItem("phi_goal_wt") || "165");
  }

  function calcProteinTarget(goalWt) {
    return Math.round(goalWt * 0.545 * 10) / 10;
  }

  // ── Inject styles ──────────────────────────────────────────────────────────
  function injectStyles() {
    if (el("fne-styles")) return;
    const style = document.createElement("style");
    style.id = "fne-styles";
    style.textContent = `
      /* ── Food Noise Emergency Button ── */
      .fne-trigger-btn {
        width:100%; min-height:52px;
        display:flex; align-items:center; justify-content:center; gap:10px;
        background:linear-gradient(135deg,#c8402a,#a33020);
        border:none; border-radius:10px; cursor:pointer;
        font-family:var(--sans); font-size:.88rem; font-weight:700;
        color:white; letter-spacing:.02em; transition:all .15s;
        box-shadow:0 4px 16px rgba(200,64,42,.35);
        position:relative; overflow:hidden;
      }
      .fne-trigger-btn::before {
        content:''; position:absolute; inset:0;
        background:linear-gradient(135deg,rgba(255,255,255,.1),transparent);
        pointer-events:none;
      }
      .fne-trigger-btn:hover { transform:translateY(-1px); box-shadow:0 6px 20px rgba(200,64,42,.5); }
      .fne-trigger-btn:active { transform:translateY(0); }
      .fne-trigger-btn .fne-pulse {
        width:8px; height:8px; border-radius:50%; background:rgba(255,255,255,.8);
        animation:fnePulse 1.8s ease infinite; flex-shrink:0;
      }
      @keyframes fnePulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.3;transform:scale(1.4)} }

      /* ── Emergency Overlay ── */
      .fne-overlay {
        position:fixed; inset:0; z-index:9998;
        background:rgba(0,0,0,.85);
        backdrop-filter:blur(8px);
        display:flex; align-items:flex-end; justify-content:center;
        padding:0; animation:fneIn .25s ease;
      }
      @keyframes fneIn { from{opacity:0} to{opacity:1} }
      @media(min-width:640px) { .fne-overlay { align-items:center; padding:20px; } }

      .fne-panel {
        width:100%; max-width:500px; max-height:96dvh;
        background:var(--surface,#111318); border-radius:20px 20px 0 0;
        overflow-y:auto; position:relative;
        border:1px solid rgba(255,255,255,.08);
        animation:fneSlide .3s cubic-bezier(.4,0,.2,1);
      }
      @media(min-width:640px) { .fne-panel { border-radius:20px; } }
      @keyframes fneSlide { from{transform:translateY(30px);opacity:0} to{transform:translateY(0);opacity:1} }

      /* Header */
      .fne-header {
        padding:24px 24px 20px;
        background:linear-gradient(135deg,rgba(200,64,42,.15),rgba(200,64,42,.05));
        border-bottom:1px solid rgba(255,255,255,.06);
        position:sticky; top:0; z-index:5;
        backdrop-filter:blur(12px);
      }
      .fne-header-top { display:flex; align-items:flex-start; justify-content:space-between; margin-bottom:12px; }
      .fne-close {
        width:32px; height:32px; display:flex; align-items:center; justify-content:center;
        background:rgba(255,255,255,.08); border:none; border-radius:8px;
        color:rgba(255,255,255,.6); cursor:pointer; transition:all .15s; font-size:.85rem;
        flex-shrink:0;
      }
      .fne-close:hover { background:rgba(255,255,255,.15); color:white; }
      .fne-title-chip {
        display:inline-flex; align-items:center; gap:7px; padding:5px 12px;
        background:rgba(200,64,42,.2); border:1px solid rgba(200,64,42,.35);
        border-radius:20px; font-size:.68rem; font-weight:700; color:#f87171;
        letter-spacing:.08em; text-transform:uppercase; margin-bottom:8px;
      }
      .fne-title-chip .fne-pulse { width:5px; height:5px; border-radius:50%; background:#f87171; }
      .fne-headline { font-size:1.35rem; font-weight:700; color:white; line-height:1.2; margin-bottom:4px; }
      .fne-sub { font-size:.82rem; color:rgba(255,255,255,.5); line-height:1.5; }

      /* Timer */
      .fne-timer-wrap {
        display:flex; align-items:center; gap:12px; margin-top:14px;
      }
      .fne-timer-ring {
        position:relative; width:52px; height:52px; flex-shrink:0;
      }
      .fne-timer-ring svg { transform:rotate(-90deg); }
      .fne-timer-ring circle { fill:none; stroke-width:3; }
      .fne-timer-track { stroke:rgba(255,255,255,.1); }
      .fne-timer-fill { stroke:#4ade80; stroke-linecap:round; transition:stroke-dashoffset .9s linear; }
      .fne-timer-num {
        position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
        font-size:.7rem; font-weight:700; color:white; font-family:var(--mono,'monospace');
      }
      .fne-timer-label { font-size:.8rem; color:rgba(255,255,255,.6); line-height:1.4; }
      .fne-timer-label strong { color:white; display:block; font-size:.88rem; }

      /* Body */
      .fne-body { padding:20px 24px 24px; }

      /* Validation message */
      .fne-validation {
        padding:16px; background:rgba(200,64,42,.08);
        border:1px solid rgba(200,64,42,.2); border-radius:12px;
        margin-bottom:20px;
      }
      .fne-validation-head { font-size:.88rem; font-weight:700; color:white; margin-bottom:5px; }
      .fne-validation-body { font-size:.78rem; color:rgba(255,255,255,.55); line-height:1.65; }

      /* Ghrelin meter */
      .fne-ghrelin-wrap { margin-bottom:20px; }
      .fne-ghrelin-label { font-size:.7rem; font-weight:700; color:rgba(255,255,255,.4); text-transform:uppercase; letter-spacing:.1em; margin-bottom:8px; }
      .fne-ghrelin-bar { height:6px; background:rgba(255,255,255,.08); border-radius:3px; overflow:hidden; margin-bottom:4px; }
      .fne-ghrelin-fill { height:100%; border-radius:3px; background:linear-gradient(90deg,#4ade80,#fbbf24,#f87171); transition:width 1.2s ease; }
      .fne-ghrelin-ticks { display:flex; justify-content:space-between; font-size:.62rem; color:rgba(255,255,255,.3); }

      /* Interventions */
      .fne-section-label {
        font-size:.7rem; font-weight:700; color:rgba(255,255,255,.35);
        text-transform:uppercase; letter-spacing:.1em; margin-bottom:12px;
        display:flex; align-items:center; gap:8px;
      }
      .fne-section-label::after { content:''; flex:1; height:1px; background:rgba(255,255,255,.07); }

      .fne-interventions { display:flex; flex-direction:column; gap:8px; margin-bottom:20px; }
      .fne-intervention {
        display:flex; align-items:flex-start; gap:12px;
        padding:14px; border:1px solid rgba(255,255,255,.07);
        border-radius:12px; background:rgba(255,255,255,.03);
        cursor:pointer; transition:all .15s; text-align:left; width:100%;
      }
      .fne-intervention:hover { background:rgba(255,255,255,.06); border-color:rgba(255,255,255,.12); }
      .fne-intervention.active { background:rgba(74,222,128,.08); border-color:rgba(74,222,128,.25); }
      .fne-int-icon { font-size:1.4rem; flex-shrink:0; margin-top:2px; }
      .fne-int-body { flex:1; }
      .fne-int-head { font-size:.84rem; font-weight:600; color:white; margin-bottom:2px; display:flex; align-items:center; gap:6px; }
      .fne-int-time { font-size:.66rem; color:rgba(255,255,255,.4); font-family:var(--mono,'monospace'); }
      .fne-int-desc { font-size:.74rem; color:rgba(255,255,255,.45); line-height:1.5; }
      .fne-int-instruction { font-size:.78rem; color:rgba(74,222,128,.85); margin-top:6px; font-weight:500; display:none; }
      .fne-intervention.active .fne-int-instruction { display:block; }

      /* Ask PHI button */
      .fne-ask-btn {
        width:100%; padding:14px; background:rgba(0,212,200,.12);
        border:1.5px solid rgba(0,212,200,.3); border-radius:12px;
        color:var(--signal,#00d4c8); font-family:var(--sans); font-size:.85rem; font-weight:600;
        cursor:pointer; transition:all .15s; display:flex; align-items:center; justify-content:center; gap:8px;
        margin-bottom:10px;
      }
      .fne-ask-btn:hover { background:rgba(0,212,200,.2); border-color:rgba(0,212,200,.5); }

      .fne-log-note { font-size:.7rem; color:rgba(255,255,255,.25); text-align:center; }

      /* Protein suggestions */
      .fne-protein-list { display:grid; grid-template-columns:1fr 1fr; gap:6px; margin-top:8px; }
      .fne-protein-item {
        padding:10px 12px; background:rgba(255,255,255,.04);
        border:1px solid rgba(255,255,255,.07); border-radius:8px;
        font-size:.74rem; color:rgba(255,255,255,.7); line-height:1.4;
      }
      .fne-protein-item strong { color:var(--signal,#00d4c8); display:block; font-size:.68rem; }

      /* Cockpit section */
      .fne-cockpit-section {
        padding:14px 15px 16px;
        border-bottom:1px solid var(--border,rgba(255,255,255,.07));
      }

      /* Noise level quick-tap */
      .fne-noise-quick { display:flex; gap:4px; margin-bottom:8px; }
      .fne-noise-btn {
        flex:1; padding:8px 4px; border:1.5px solid var(--border,rgba(255,255,255,.07));
        border-radius:6px; font-family:var(--sans); font-size:.7rem; font-weight:600;
        cursor:pointer; transition:all .15s; text-align:center; background:var(--surface-2,#1a1d24); color:var(--text-2,#9aa3b0);
      }
      .fne-noise-btn.selected { background:rgba(200,64,42,.15); border-color:rgba(200,64,42,.4); color:#f87171; }
      .fne-noise-btn:hover:not(.selected) { border-color:rgba(255,255,255,.2); }
    `;
    document.head.appendChild(style);
  }

  // ── Inject cockpit section ─────────────────────────────────────────────────
  function injectCockpitSection() {
    if (el("fne-cockpit-section")) return;
    const cockpit = el("cockpit");
    if (!cockpit) { setTimeout(injectCockpitSection, 500); return; }

    const section = document.createElement("section");
    section.className = "cp-section fne-cockpit-section";
    section.id = "fne-cockpit-section";

    const gw = getGoalWeight();
    const protein = calcProteinTarget(gw);

    section.innerHTML = `
      <div class="cp-section-hd">
        <h2 class="cp-section-title">Food Noise Emergency</h2>
        <span class="cp-section-badge" style="color:#f87171">ACTIVE</span>
      </div>
      <p class="cp-caption" style="margin-bottom:10px;">When ghrelin surge hits — tap before you eat. This is biology, not willpower.</p>

      <div style="font-size:.7rem;font-weight:700;color:var(--text-3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;">Quick Log — Noise Level</div>
      <div class="fne-noise-quick" id="fneNoiseQuick">
        <button class="fne-noise-btn" data-level="3">3<br><span style="font-size:.6rem;opacity:.6">Mild</span></button>
        <button class="fne-noise-btn" data-level="5">5<br><span style="font-size:.6rem;opacity:.6">Moderate</span></button>
        <button class="fne-noise-btn" data-level="7">7<br><span style="font-size:.6rem;opacity:.6">High</span></button>
        <button class="fne-noise-btn" data-level="9">9<br><span style="font-size:.6rem;opacity:.6">Intense</span></button>
      </div>

      <button class="fne-trigger-btn" id="fneEmergencyBtn">
        <span class="fne-pulse"></span>
        <span>Food Noise Emergency Protocol</span>
        <i class="fa-solid fa-shield-halved"></i>
      </button>
      <div style="font-size:.68rem;color:var(--text-3);text-align:center;margin-top:6px;">Tap when ghrelin surge is intense. Triggers 5-min intervention.</div>
    `;

    // Insert as first section or before existing Ghrelin section
    const ghrelinSection = cockpit.querySelector('.cp-section:last-child');
    cockpit.insertBefore(section, ghrelinSection || cockpit.firstChild);

    // Wire quick noise log
    section.querySelectorAll('.fne-noise-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        section.querySelectorAll('.fne-noise-btn').forEach(b => b.classList.remove('selected'));
        btn.classList.add('selected');
        const level = parseInt(btn.dataset.level);
        await logNoiseLevel(level);
        toast(`Food noise ${level}/10 logged`, "ok");
        // Sync the main cockpit slider if it exists
        const mainSlider = el("noiseSlider");
        if (mainSlider) mainSlider.value = level;
      });
    });

    // Wire emergency button
    el("fneEmergencyBtn").addEventListener('click', openEmergencyProtocol);
  }

  // ── Log to API ─────────────────────────────────────────────────────────────
  async function logNoiseLevel(level) {
    const token = await getAuthToken();
    if (!token) return;
    const date = new Date().toISOString().slice(0, 10);
    try {
      await fetch(`${API}/api/behavioral-logs`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
        body: JSON.stringify({ date, metric_name: "food_noise", value: level, unit: "1-10", notes: "food_noise_emergency_protocol" }),
        signal: AbortSignal.timeout(8000),
      });
    } catch (e) { console.warn("[FNE] Log error:", e); }
  }

  // ── Open emergency overlay ────────────────────────────────────────────────
  function openEmergencyProtocol() {
    if (el("fne-overlay")) return;
    
    const gw = getGoalWeight();
    const proteinTarget = calcProteinTarget(gw);
    const msg = GHRELIN_MESSAGES[Math.floor(Math.random() * GHRELIN_MESSAGES.length)];
    const noiseLevel = parseInt(el("fneNoiseQuick")?.querySelector('.selected')?.dataset.level || "7");

    const overlay = document.createElement("div");
    overlay.className = "fne-overlay";
    overlay.id = "fne-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-label", "Food Noise Emergency Protocol");

    // Build protein intervention text
    const proteinSources = [
      { name: "Greek yogurt (1.5 cups)", grams: 26 },
      { name: "4oz chicken breast", grams: 35 },
      { name: "Whey protein scoop", grams: 25 },
      { name: "2 large eggs + string cheese", grams: 20 },
      { name: "Cottage cheese (¾ cup)", grams: 21 },
      { name: "3oz salmon", grams: 21 },
    ].slice(0, 4);

    INTERVENTIONS[1].instruction = `Get ~${Math.min(proteinTarget, 35)}g protein NOW. With your goal weight (${gw} lbs) your target is ${proteinTarget}g/day.`;

    overlay.innerHTML = `
      <div class="fne-panel" id="fne-panel">
        <!-- Header with timer -->
        <div class="fne-header">
          <div class="fne-header-top">
            <div>
              <div class="fne-title-chip"><span class="fne-pulse"></span> Ghrelin Surge Active</div>
              <div class="fne-headline">5-Minute Shield Protocol</div>
              <div class="fne-sub">This interrupt breaks the ghrelin loop. Give it 5 minutes.</div>
            </div>
            <button class="fne-close" id="fne-close-btn" aria-label="Close"><i class="fa-solid fa-xmark"></i></button>
          </div>
          <div class="fne-timer-wrap">
            <div class="fne-timer-ring">
              <svg width="52" height="52" viewBox="0 0 52 52">
                <circle class="fne-timer-track" cx="26" cy="26" r="21" stroke-dasharray="132" stroke-dashoffset="0"/>
                <circle class="fne-timer-fill" id="fne-timer-circle" cx="26" cy="26" r="21" stroke-dasharray="132" stroke-dashoffset="0"/>
              </svg>
              <div class="fne-timer-num" id="fne-timer-num">5:00</div>
            </div>
            <div class="fne-timer-label">
              <strong id="fne-timer-status">Protocol running</strong>
              The timer measures how long you've held the line against the surge.
            </div>
          </div>
        </div>

        <div class="fne-body">
          <!-- Biological validation -->
          <div class="fne-validation">
            <div class="fne-validation-head">🧬 ${msg.head}</div>
            <div class="fne-validation-body">${msg.body}</div>
          </div>

          <!-- Ghrelin meter -->
          <div class="fne-ghrelin-wrap">
            <div class="fne-ghrelin-label">Logged ghrelin level</div>
            <div class="fne-ghrelin-bar"><div class="fne-ghrelin-fill" id="fne-ghrelin-fill" style="width:${noiseLevel * 10}%"></div></div>
            <div class="fne-ghrelin-ticks"><span>Quiet</span><span>Moderate</span><span>Relentless</span></div>
          </div>

          <!-- Interventions -->
          <div class="fne-section-label">Pick one — or do all four</div>
          <div class="fne-interventions" id="fne-interventions">
            ${INTERVENTIONS.map(iv => `
              <button class="fne-intervention" data-id="${iv.id}">
                <span class="fne-int-icon">${iv.icon}</span>
                <div class="fne-int-body">
                  <div class="fne-int-head">
                    ${iv.label}
                    <span class="fne-int-time">${iv.time}</span>
                  </div>
                  <div class="fne-int-desc">${iv.desc}</div>
                  <div class="fne-int-instruction">${iv.instruction || ''}</div>
                  ${iv.id === 'protein' ? `
                    <div class="fne-protein-list">
                      ${proteinSources.map(s => `<div class="fne-protein-item"><strong>${s.grams}g</strong>${s.name}</div>`).join('')}
                    </div>
                  ` : ''}
                </div>
              </button>
            `).join('')}
          </div>

          <!-- Ask PHI button -->
          <button class="fne-ask-btn" id="fne-ask-phi-btn">
            <i class="fa-solid fa-comments"></i>
            Ask PHI: "My food noise is at ${noiseLevel}/10 — what do I do right now?"
          </button>

          <div class="fne-log-note">
            Noise level ${noiseLevel}/10 logged to your Metabolic Shield.
          </div>
        </div>
      </div>
    `;

    document.body.appendChild(overlay);

    // Close handlers
    el("fne-close-btn").addEventListener("click", closeEmergencyProtocol);
    overlay.addEventListener("click", e => { if (e.target === overlay) closeEmergencyProtocol(); });
    document.addEventListener("keydown", fneEscapeHandler);

    // Toggle interventions
    overlay.querySelectorAll(".fne-intervention").forEach(btn => {
      btn.addEventListener("click", () => {
        btn.classList.toggle("active");
      });
    });

    // Ask PHI
    el("fne-ask-phi-btn").addEventListener("click", () => {
      closeEmergencyProtocol();
      // Switch to chat view
      if (typeof switchView === "function") switchView("chat");
      // Delay slightly for view transition
      setTimeout(async () => {
        const msg = `My food noise is at ${noiseLevel}/10 right now — it feels relentless. I've been off my GLP-1 and the ghrelin rebound is intense. What should I do right now to get through the next 20 minutes? Give me the biology-based protocol.`;
        if (typeof sendMessage === "function") {
          await sendMessage(msg);
        } else {
          // Fallback: fill the textarea
          const ta = el("chatInput");
          if (ta) { ta.value = msg; ta.dispatchEvent(new Event("input")); ta.focus(); }
        }
      }, 300);
    });

    // Log noise level and start timer
    logNoiseLevel(noiseLevel);
    startTimer();
  }

  function closeEmergencyProtocol() {
    const overlay = el("fne-overlay");
    if (overlay) {
      overlay.style.opacity = "0";
      overlay.style.transition = "opacity .2s";
      setTimeout(() => overlay.remove(), 200);
    }
    document.removeEventListener("keydown", fneEscapeHandler);
    stopTimer();
  }

  function fneEscapeHandler(e) {
    if (e.key === "Escape") closeEmergencyProtocol();
  }

  // ── Timer ─────────────────────────────────────────────────────────────────
  let timerInterval = null;
  let timerRemaining = PROTOCOL_DURATION;

  function startTimer() {
    timerRemaining = PROTOCOL_DURATION;
    updateTimerDisplay();
    timerInterval = setInterval(() => {
      timerRemaining--;
      updateTimerDisplay();
      if (timerRemaining <= 0) {
        stopTimer();
        onTimerComplete();
      }
    }, 1000);
  }

  function stopTimer() {
    if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
  }

  function updateTimerDisplay() {
    const numEl = el("fne-timer-num");
    const circleEl = el("fne-timer-circle");
    if (!numEl) return;

    const mins = Math.floor(timerRemaining / 60);
    const secs = timerRemaining % 60;
    numEl.textContent = `${mins}:${secs.toString().padStart(2, '0')}`;

    if (circleEl) {
      const circumference = 132;
      const pct = timerRemaining / PROTOCOL_DURATION;
      const offset = circumference * (1 - pct);
      circleEl.style.strokeDashoffset = offset;
      // Color shifts from green to amber as time runs out
      circleEl.style.stroke = pct > 0.5 ? '#4ade80' : pct > 0.25 ? '#fbbf24' : '#f87171';
    }
  }

  function onTimerComplete() {
    const statusEl = el("fne-timer-status");
    if (statusEl) statusEl.textContent = "5 minutes held ✓";
    const numEl = el("fne-timer-num");
    if (numEl) { numEl.textContent = "✓"; numEl.style.color = '#4ade80'; }
    toast("5-minute protocol complete. How's the noise level now? Log it below.", "ok");
  }

  // ── Init ───────────────────────────────────────────────────────────────────
  function init() {
    injectStyles();
    // Wait for cockpit to exist
    const wait = () => {
      if (el("cockpit")) {
        injectCockpitSection();
      } else {
        setTimeout(wait, 400);
      }
    };
    wait();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    // Delay slightly to let script.js fully initialize
    setTimeout(init, 800);
  }

})();