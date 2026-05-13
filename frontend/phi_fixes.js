/**
 * phi_fixes.js — Curabook PHI App Patches v3
 *
 * FIXED IN THIS VERSION:
 * 1. Quick Actions moved to SIDEBAR (removed from cockpit)
 * 2. Sync Watch Screenshot uses Vision AI to read wearable data → fills shield
 * 3. Appeal page auto-fetches stored markers + allows manual additions
 * 4. Insurance PA Architect auto-populates from health memory + manual fields
 * 5. PayPal checkout flow preserved
 * 6. Doctor prep, appointment prep all work automatically
 */
"use strict";

const _API = window.location.hostname === 'localhost' ? 'http://localhost:5000' : 'https://api.curabook.com';

// ── Shared session helper ─────────────────────────────────────────────────────
async function _getSession() {
  if (typeof session === 'function') {
    try { return await session(); } catch (e) {}
  }
  return null;
}

async function _authHeaders() {
  const s = await _getSession();
  if (!s?.access_token) return null;
  return { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + s.access_token };
}

// ── 1. WELCOME MODAL ──────────────────────────────────────────────────────────
function showWelcomeModal(type) {
  const existing = document.getElementById('welcomeModal');
  if (existing) existing.remove();
  const isPaid = type === 'paid';
  const modal = document.createElement('div');
  modal.id = 'welcomeModal';
  modal.style.cssText = `position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px;animation:fadeIn .25s ease;`;
  modal.innerHTML = `
    <div style="background:var(--surface,#fff);border-radius:20px;padding:36px;max-width:440px;width:100%;box-shadow:0 24px 60px rgba(0,0,0,.2);text-align:center;animation:slideUp .3s ease">
      <div style="font-size:3rem;margin-bottom:16px">${isPaid ? '🎉' : '👋'}</div>
      <h2 style="font-family:var(--serif,'Georgia');font-size:1.6rem;font-weight:400;margin-bottom:10px;color:var(--text,#111)">
        ${isPaid ? 'Welcome to Shield Core!' : "Welcome to Curabook PHI!"}
      </h2>
      <p style="font-size:.88rem;color:var(--text-2,#888);line-height:1.7;margin-bottom:24px">
        ${isPaid
          ? "Your plan is active. Upload your first lab report to unlock your Metabolic Shield™ score."
          : "PHI is ready. Upload your first lab report — tap the paperclip — and PHI will build your cliff risk picture in seconds."}
      </p>
      <button onclick="document.getElementById('welcomeModal').remove();handleUploadClick()"
        style="width:100%;padding:13px;background:var(--signal,#00d4c8);color:#0a0b0e;border:none;border-radius:10px;font-size:.92rem;font-weight:700;cursor:pointer;font-family:var(--sans,'sans-serif');box-shadow:0 4px 16px rgba(0,212,200,.3);margin-bottom:10px">
        📎 Upload First Lab Report
      </button>
      <button onclick="document.getElementById('welcomeModal').remove()"
        style="width:100%;padding:11px;background:none;border:1px solid var(--border,rgba(0,0,0,.08));border-radius:10px;font-size:.84rem;color:var(--text-2,#888);cursor:pointer;font-family:var(--sans,'sans-serif')">
        Explore first, upload later
      </button>
    </div>
  `;
  document.body.appendChild(modal);
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
}

(function checkWelcomeParam() {
  const params = new URLSearchParams(window.location.search);
  const welcome = params.get('welcome');
  if (welcome) {
    window.history.replaceState({}, '', window.location.pathname);
    setTimeout(() => showWelcomeModal(welcome), 1200);
  }
})();

// ── 2. PLAN BADGE — reads globals script.js already sets; zero extra fetches ──
// script.js sets window._userPlan after /startup completes.
// performance_patch.js may also set window.__startupCache.
// We NEVER fire /api/payment/status here — that was causing the speed regression.
function refreshPlanDisplay() {
  try {
    const plan = window._userPlan
      || window.__startupCache?.plan
      || window.__startupCache?.subscription?.plan
      || null;

    if (!plan) {
      // Globals not ready yet — retry once, still no extra fetch
      setTimeout(() => {
        const p2 = window._userPlan || window.__startupCache?.plan;
        if (p2) _applyPlanToUI(p2, window.__startupCache || {});
      }, 1500);
      return;
    }
    _applyPlanToUI(plan, window.__startupCache || {});
  } catch (e) { console.warn('[PLAN]', e); }
}

function _applyPlanToUI(plan, cache) {
  const isPro = plan && plan !== 'free' && plan !== 'trial';
  const planEl = document.getElementById('userPlan');
  if (planEl) {
    const labels = { monthly:'Shield $49/mo ✦', annual:'Shield Annual ✦', clinical:'Shield Clinical ✦', trial:'Trial Active', free:'PHI Free' };
    planEl.textContent = labels[plan] || 'PHI Free';
    planEl.style.color = isPro ? 'var(--signal,#00d4c8)' : 'var(--text-3,#566070)';
  }
  if (plan === 'trial' && cache.subscription_end_date) showTrialBanner(cache.subscription_end_date);
  const pill = document.getElementById('upgradeNavBtn');
  if (pill) pill.style.display = isPro ? 'none' : 'inline-block';
}

function showTrialBanner(endDate) {
  if (document.getElementById('trialBanner')) return;
  try {
    const days = Math.ceil((new Date(endDate) - Date.now()) / 86400000);
    if (days <= 0) return;
    const banner = document.createElement('div');
    banner.id = 'trialBanner';
    banner.style.cssText = `position:fixed;top:0;left:0;right:0;z-index:200;background:linear-gradient(90deg,var(--signal,#00d4c8),#00a89e);color:#0a0b0e;text-align:center;padding:9px 20px;font-size:.8rem;font-weight:600;display:flex;align-items:center;justify-content:center;gap:12px;`;
    banner.innerHTML = `<span>⏰ Trial active — ${days} day${days !== 1 ? 's' : ''} remaining</span><button onclick="showUpgradeModal('trial');document.getElementById('trialBanner').remove()" style="background:rgba(0,0,0,.15);border:none;border-radius:6px;padding:4px 12px;color:#0a0b0e;font-size:.75rem;font-weight:700;cursor:pointer;font-family:inherit">Upgrade Now</button><button onclick="document.getElementById('trialBanner').remove()" style="background:none;border:none;color:rgba(0,0,0,.5);cursor:pointer;font-size:1.1rem;padding:0 4px">×</button>`;
    document.body.prepend(banner);
  } catch (e) {}
}

// ── 3. UPGRADE MODAL — PayPal ─────────────────────────────────────────────────
window.showUpgradeModal = function(reason) {
  const existing = document.getElementById('upgradeModal');
  if (existing) existing.remove();
  const modal = document.createElement('div');
  modal.id = 'upgradeModal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.78);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px;animation:fadeIn .2s ease';
  const features = [['fa-file-medical','Unlimited lab reports'],['fa-brain','Full health memory'],['fa-shield-halved','Insurance PA support'],['fa-bell','Weekly health briefs'],['fa-chart-line','Trend tracking'],['fa-stethoscope','Doctor visit prep']];
  modal.innerHTML = `
    <div style="background:var(--surface,#111318);border:1px solid var(--border-2,rgba(255,255,255,.13));border-radius:20px;padding:28px 24px 24px;max-width:420px;width:100%;box-shadow:0 32px 80px rgba(0,0,0,.6);position:relative;overflow:hidden;">
      <div style="position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--signal,#00d4c8),#00a89e);"></div>
      <button onclick="closeUpgradeModal()" style="position:absolute;top:14px;right:14px;width:30px;height:30px;border-radius:8px;border:none;background:var(--surface-3,#23272f);color:var(--text-2,#9aa3b0);font-size:.85rem;cursor:pointer;display:flex;align-items:center;justify-content:center;">✕</button>
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:18px;padding-right:36px;">
        <div style="width:42px;height:42px;flex-shrink:0;background:linear-gradient(135deg,var(--signal,#00d4c8),#00a89e);border-radius:12px;display:flex;align-items:center;justify-content:center;font-family:var(--serif,'Georgia');font-size:1.15rem;color:#0a0b0e;font-weight:600;box-shadow:0 4px 16px rgba(0,212,200,.3);">φ</div>
        <div><h2 style="font-size:1.1rem;font-weight:700;color:var(--text,#f0f2f5);margin-bottom:3px;line-height:1.2;">Upgrade to PHI Shield</h2><p style="font-size:.73rem;color:var(--text-3,#566070);line-height:1.4;">Unlimited reports · Health memory · Insurance PA support</p></div>
      </div>
      ${reason === 'upload' ? `<div style="background:rgba(251,191,36,.12);border:1px solid rgba(251,191,36,.3);border-radius:10px;padding:10px 13px;margin-bottom:16px;font-size:.8rem;color:var(--amber,#fbbf24);display:flex;align-items:center;gap:8px;"><i class="fa-solid fa-triangle-exclamation" style="flex-shrink:0;"></i><span><strong>Free limit reached</strong> — upgrade to upload unlimited reports.</span></div>` : ''}
      <div style="background:var(--surface-2,#1a1d24);border:1px solid var(--border,rgba(255,255,255,.07));border-radius:12px;padding:14px 16px;margin-bottom:18px;">
        <div style="font-size:.62rem;font-weight:700;color:var(--text-3,#566070);text-transform:uppercase;letter-spacing:.1em;margin-bottom:10px;">What you unlock</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:7px 12px;">
          ${features.map(([icon,label]) => `<div style="display:flex;align-items:center;gap:7px;font-size:.78rem;color:var(--text-2,#9aa3b0);"><i class="fa-solid ${icon}" style="color:var(--signal,#00d4c8);font-size:.7rem;width:14px;text-align:center;flex-shrink:0;"></i>${label}</div>`).join('')}
        </div>
      </div>
      <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:14px;">
        <button id="ppMonthlyBtn" onclick="initiatePayPalCheckout('monthly')" style="width:100%;padding:13px 16px;background:var(--signal,#00d4c8);color:#0a0b0e;border:none;border-radius:12px;font-size:.9rem;font-weight:700;cursor:pointer;font-family:inherit;box-shadow:0 4px 20px rgba(0,212,200,.35);transition:all .15s;display:flex;align-items:center;justify-content:space-between;"><span>Shield Monthly</span><span style="font-family:monospace;font-size:1rem;">$49<span style="font-size:.72rem;font-weight:500;">/mo</span></span></button>
        <button id="ppAnnualBtn" onclick="initiatePayPalCheckout('annual')" style="width:100%;padding:13px 16px;background:var(--surface-3,#23272f);color:var(--text,#f0f2f5);border:2px solid var(--border-2,rgba(255,255,255,.13));border-radius:12px;font-size:.9rem;font-weight:600;cursor:pointer;font-family:inherit;transition:all .15s;display:flex;align-items:center;justify-content:space-between;"><span style="display:flex;align-items:center;gap:8px;">Shield Annual<span style="font-size:.58rem;font-weight:700;background:#10b981;color:#0a0b0e;padding:2px 7px;border-radius:20px;letter-spacing:.04em;">SAVE 20%</span></span><span style="font-family:monospace;font-size:.85rem;">$39<span style="font-size:.65rem;font-weight:500;color:var(--text-2);">/mo · $468/yr</span></span></button>
        <button id="ppClinicalBtn" onclick="initiatePayPalCheckout('clinical')" style="width:100%;padding:13px 16px;background:var(--surface-3,#23272f);color:var(--text,#f0f2f5);border:2px solid rgba(139,92,246,.35);border-radius:12px;font-size:.9rem;font-weight:600;cursor:pointer;font-family:inherit;transition:all .15s;display:flex;align-items:center;justify-content:space-between;position:relative;overflow:hidden;"><div style="position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,#8b5cf6,#6d28d9);"></div><span style="display:flex;align-items:center;gap:8px;">Shield Clinical<span style="font-size:.58rem;font-weight:700;background:rgba(139,92,246,.2);color:#a78bfa;border:1px solid rgba(139,92,246,.3);padding:2px 7px;border-radius:20px;letter-spacing:.04em;">PA ARCHITECT</span></span><span style="font-family:monospace;font-size:1rem;color:#a78bfa;">$99<span style="font-size:.72rem;font-weight:500;color:var(--text-2);">/mo</span></span></button>
      </div>
      <div style="font-size:.68rem;color:var(--text-3,#566070);text-align:center;line-height:1.6;margin-bottom:10px;padding:8px 10px;background:rgba(139,92,246,.05);border:1px solid rgba(139,92,246,.12);border-radius:8px;"><strong style="color:#a78bfa;">Clinical Promise:</strong> If your appeal is denied after using our packet, email us. We'll generate a revised packet for your next submission — free.</div>
      <div id="upgradeStatus" style="text-align:center;font-size:.78rem;color:var(--text-3,#566070);min-height:18px;margin-bottom:6px;"></div>
      <p style="font-size:.67rem;color:var(--text-3,#9ca3af);text-align:center;">🔒 Secure via PayPal · Cancel anytime</p>
    </div>
  `;
  modal.addEventListener('click', e => { if (e.target === modal) closeUpgradeModal(); });
  document.body.appendChild(modal);
};

function closeUpgradeModal() { document.getElementById('upgradeModal')?.remove(); }

async function initiatePayPalCheckout(plan) {
  const PLAN_LABELS = { monthly: 'Shield Core', annual: 'Shield Core — Annual', clinical: 'Shield Clinical' };
  const btnId = plan === 'clinical' ? 'ppClinicalBtn' : plan === 'annual' ? 'ppAnnualBtn' : 'ppMonthlyBtn';
  const btn = document.getElementById(btnId);
  const statusEl = document.getElementById('upgradeStatus');
  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i> Connecting…'; }
  if (statusEl) statusEl.textContent = 'Setting up your subscription…';
  try {
    const h = await _authHeaders();
    if (!h) throw new Error('Please sign in first.');
    const res = await fetch(_API + '/api/payment/paypal/create-subscription', { method: 'POST', headers: h, body: JSON.stringify({ plan }) });
    if (!res.ok) { const err = await res.json().catch(() => ({})); throw new Error(err.error || 'Payment setup failed.'); }
    const data = await res.json();
    if (!data.approve_url) throw new Error('PayPal did not return a checkout URL.');
    if (statusEl) statusEl.textContent = 'Redirecting to PayPal…';
    window.location.href = data.approve_url;
  } catch (e) {
    if (statusEl) statusEl.textContent = e.message;
    if (btn) { btn.disabled = false; btn.innerHTML = plan === 'annual' ? 'Shield Annual' : 'Shield Monthly'; }
  }
}
window.initUpgrade = initiatePayPalCheckout;

// ══════════════════════════════════════════════════════════════════════════════
// 4. SYNC WATCH SCREENSHOT — Vision AI reads wearable data → fills shield
// ══════════════════════════════════════════════════════════════════════════════
async function processWatchScreenshot(file) {
  if (!file) return;
  if (typeof toast === 'function') toast('📸 Reading wearable screenshot…', 'info');

  try {
    const h = await _authHeaders();
    if (!h) { if (typeof toast === 'function') toast('Please sign in first.', 'err'); return; }

    const authHeader = { 'Authorization': h['Authorization'] };
    const today = new Date().toISOString().slice(0, 10);

    // ── Step 1: Send image to /analyze to extract text ───────────────────────
    // /analyze runs GPT-4o Vision + OCR and returns { document_text, summary, markers }
    const formData = new FormData();
    formData.append('file', file, file.name || 'wearable.jpg');
    const analyzeRes = await fetch(_API + '/analyze', { method: 'POST', headers: authHeader, body: formData });
    if (!analyzeRes.ok) {
      const errText = await analyzeRes.text().catch(() => String(analyzeRes.status));
      throw new Error('Could not read screenshot (' + errText + ')');
    }
    const analyzeData = await analyzeRes.json();

    // Gather everything /analyze returned as one text blob
    const rawText = [
      analyzeData.document_text || '',
      analyzeData.summary || '',
      analyzeData.extracted_text || '',
      analyzeData.analysis || ''
    ].filter(Boolean).join('\n');

    // ── Step 2: Ask GPT via /chat to extract metrics as JSON ─────────────────
    // This is 100x more reliable than regex — GPT understands any app's format
    const convId = window._convId || ('watch-sync-' + Date.now());
    const extractPrompt = `Below is text extracted from a wearable/fitness app screenshot.
Extract ONLY these values and return ONLY a raw JSON object with no markdown, no explanation:
{ "steps": <integer or null>, "sleep": <decimal hours or null>, "protein": <integer grams or null>, "weight": <decimal lbs or null>, "calories": <integer or null>, "heart_rate": <integer bpm or null> }
If a value is not present in the text, use null. Do not guess.

TEXT:
${rawText.slice(0, 2000)}`;

    const chatRes = await fetch(_API + '/chat', {
      method: 'POST',
      headers: h,
      body: JSON.stringify({ conversation_id: convId, message: extractPrompt, document_text: '', has_documents: false })
    });

    let metrics = {};
    if (chatRes.ok) {
      const chatData = await chatRes.json();
      const reply = chatData.reply || chatData.message || '';
      try {
        // Strip any accidental markdown fences
        const clean = reply.replace(/```json|```/gi, '').trim();
        const parsed = JSON.parse(clean);
        // Only accept reasonable values
        if (parsed.steps != null && parsed.steps >= 0 && parsed.steps <= 100000) metrics.steps = Math.round(parsed.steps);
        if (parsed.sleep != null && parsed.sleep >= 0 && parsed.sleep <= 14) metrics.sleep = parseFloat(parsed.sleep.toFixed(1));
        if (parsed.protein != null && parsed.protein >= 0 && parsed.protein <= 500) metrics.protein = Math.round(parsed.protein);
        if (parsed.weight != null && parsed.weight >= 50 && parsed.weight <= 700) metrics.weight = parseFloat(parsed.weight.toFixed(1));
        if (parsed.calories != null && parsed.calories >= 0 && parsed.calories <= 10000) metrics.calories = Math.round(parsed.calories);
        if (parsed.heart_rate != null && parsed.heart_rate >= 30 && parsed.heart_rate <= 250) metrics.heart_rate = Math.round(parsed.heart_rate);
      } catch (_) {
        // JSON parse failed — fall back to regex on the raw text
        metrics = _parseWatchMetrics(rawText + '\n' + reply);
      }
    } else {
      // /chat failed — try regex directly on analyze output
      metrics = _parseWatchMetrics(rawText);
    }

    // ── Also absorb any structured markers /analyze returned ─────────────────
    if (analyzeData.markers && Array.isArray(analyzeData.markers)) {
      analyzeData.markers.forEach(m => {
        const name = (m.marker_name || m.name || '').toLowerCase();
        const val = parseFloat(m.value);
        if (isNaN(val)) return;
        if (/step/.test(name) && metrics.steps == null) metrics.steps = Math.round(val);
        else if (/sleep/.test(name) && metrics.sleep == null) metrics.sleep = val;
        else if (/protein/.test(name) && metrics.protein == null) metrics.protein = Math.round(val);
        else if (/weight/.test(name) && metrics.weight == null) metrics.weight = val;
        else if (/calori/.test(name) && metrics.calories == null) metrics.calories = Math.round(val);
        else if (/heart|bpm/.test(name) && metrics.heart_rate == null) metrics.heart_rate = Math.round(val);
      });
    }

    if (Object.keys(metrics).length === 0) {
      if (typeof toast === 'function') toast('No metrics found — try a clearer screenshot showing steps, sleep, or protein.', 'info');
      return;
    }

    // ── Fill shield inputs ────────────────────────────────────────────────────
    const logged = [];
    if (metrics.steps != null) {
      const el = document.getElementById('inputSteps');
      if (el) { el.value = metrics.steps; logged.push(metrics.steps.toLocaleString() + ' steps'); }
    }
    if (metrics.sleep != null) {
      const el = document.getElementById('inputSleep');
      if (el) { el.value = metrics.sleep; logged.push(metrics.sleep + 'h sleep'); }
    }
    if (metrics.protein != null) {
      const el = document.getElementById('inputProtein');
      if (el) { el.value = metrics.protein; logged.push(metrics.protein + 'g protein'); }
    }
    if (metrics.weight != null) {
      const el = document.getElementById('inputGoalWt');
      if (el && !el.value) { el.value = metrics.weight; logged.push(metrics.weight + ' lbs'); }
    }

    // ── Trigger shield re-render ──────────────────────────────────────────────
    if (typeof updateShield === 'function') await updateShield();
    else if (typeof renderShield === 'function') {
      renderShield(
        parseFloat(document.getElementById('inputProtein')?.value) || 0,
        parseFloat(document.getElementById('inputSteps')?.value) || 0,
        parseFloat(document.getElementById('inputSleep')?.value) || 0,
        today
      );
    }

    // ── Log to behavioral API ─────────────────────────────────────────────────
    await _logWatchMetrics(metrics, today, h);

    if (logged.length > 0) {
      if (typeof toast === 'function') toast('✓ Shield updated: ' + logged.join(', '), 'ok');
      const lastEl = document.getElementById('shieldLastLogged');
      if (lastEl) lastEl.textContent = 'Synced from wearable: ' + new Date().toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
    } else {
      if (typeof toast === 'function') toast('Screenshot processed but no shield metrics found.', 'info');
    }

  } catch (err) {
    console.error('[WATCH-SYNC]', err);
    if (typeof toast === 'function') toast('Watch sync failed: ' + err.message, 'err');
  }
}
function _parseWatchMetrics(text) {
  // Fallback-only regex parser. Covers Apple Health, Samsung Health, Fitbit,
  // Garmin, MyFitnessPal, Cronometer, Whoop, Google Fit common formats.
  const m = {};
  const t = text;

  // ── Steps ──────────────────────────────────────────────────────────────────
  // "8,432 steps" | "Steps: 8432" | "8432 Steps" | "steps\n8,432" | "8.4K steps"
  const stepsPatterns = [
    /(\d{1,3}(?:,\d{3})+)\s*steps?/i,            // 8,432 steps
    /(\d{4,6})\s*steps?/i,                       // 8432 steps
    /steps?\s*[:\-]?\s*(\d{1,3}(?:,\d{3})*|\d+)/i, // Steps: 8432
    /(\d+(?:\.\d)?)\s*[Kk]\s*steps?/i,            // 8.4K steps (handle below)
  ];
  for (const p of stepsPatterns) {
    const match = t.match(p);
    if (match) {
      let val = match[1].includes('K') || match[1].includes('k')
        ? parseFloat(match[1]) * 1000
        : parseInt(match[1].replace(/,/g, ''));
      if (val >= 0 && val <= 100000) { m.steps = Math.round(val); break; }
    }
  }
  // K-steps special case: "8.4K"
  if (!m.steps) {
    const k = t.match(/(\d+(?:\.\d)?)\s*[Kk]\s*steps?/i);
    if (k) { const v = parseFloat(k[1]) * 1000; if (v <= 100000) m.steps = Math.round(v); }
  }

  // ── Sleep ──────────────────────────────────────────────────────────────────
  // "7h 23m" | "7.5h" | "7 hrs sleep" | "Sleep: 7h 30m" | "7:30 sleep"
  // Handle "Xh Ym" → convert to decimal hours
  const hmMatch = t.match(/(\d+)\s*h(?:ours?|rs?)?\s*(\d+)\s*m(?:in)?/i)
    || t.match(/sleep\s*[:\-]?\s*(\d+)\s*h[^\d]*(\d+)\s*m/i);
  if (hmMatch) {
    const val = parseInt(hmMatch[1]) + parseInt(hmMatch[2]) / 60;
    if (val >= 0 && val <= 14) m.sleep = parseFloat(val.toFixed(1));
  }
  if (!m.sleep) {
    const sleepPatterns = [
      /(\d+(?:\.\d+)?)\s*h(?:ours?|rs?)?\s*(?:of\s+)?sleep/i,
      /sleep\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*h/i,
      /slept\s*[:\-]?\s*(\d+(?:\.\d+)?)/i,
      /(\d+(?:\.\d+)?)\s*hours?\s*slept/i,
    ];
    for (const p of sleepPatterns) {
      const match = t.match(p);
      if (match) {
        const val = parseFloat(match[1]);
        if (val >= 0 && val <= 14) { m.sleep = val; break; }
      }
    }
  }

  // ── Protein ────────────────────────────────────────────────────────────────
  // "Protein 142g" | "protein: 142 g" | "142g protein" | "Protein\n142"
  const proteinPatterns = [
    /protein\s*[:\-]?\s*(\d+)\s*g/i,
    /(\d+)\s*g\s*(?:of\s+)?protein/i,
    /protein\s*[:\-]?\s*(\d+)(?!\s*\d)/i,   // "Protein: 142" with no unit
  ];
  for (const p of proteinPatterns) {
    const match = t.match(p);
    if (match) {
      const val = parseInt(match[1]);
      if (val >= 0 && val <= 500) { m.protein = val; break; }
    }
  }

  // ── Calories ───────────────────────────────────────────────────────────────
  const calMatch = t.match(/(\d{3,5})\s*(?:kcal|cal(?:ories?)?)/i)
    || t.match(/(?:kcal|cal(?:ories?)?)\s*[:\-]?\s*(\d{3,5})/i)
    || t.match(/(?:active|burned|energy)\s*[:\-]?\s*(\d{3,5})/i);
  if (calMatch) { const v = parseInt(calMatch[1]); if (v <= 10000) m.calories = v; }

  // ── Heart rate ─────────────────────────────────────────────────────────────
  const hrMatch = t.match(/(\d{2,3})\s*bpm/i)
    || t.match(/heart\s*rate\s*[:\-]?\s*(\d{2,3})/i)
    || t.match(/(?:resting|avg|average)\s*(?:hr|heart)\s*[:\-]?\s*(\d{2,3})/i);
  if (hrMatch) { const v = parseInt(hrMatch[1]); if (v >= 30 && v <= 250) m.heart_rate = v; }

  // ── Weight ─────────────────────────────────────────────────────────────────
  const wtMatch = t.match(/(\d{2,3}(?:\.\d)?)\s*(?:lbs?|pounds?)/i)
    || t.match(/weight\s*[:\-]?\s*(\d{2,3}(?:\.\d)?)/i);
  if (wtMatch) { const v = parseFloat(wtMatch[1]); if (v >= 50 && v <= 700) m.weight = v; }

  return m;
}
async function _logWatchMetrics(metrics, date, headers) {
  const metricsToLog = ['protein', 'steps', 'sleep'];
  for (const metric of metricsToLog) {
    if (metrics[metric] !== undefined) {
      try {
        await fetch(_API + '/api/behavioral-logs', {
          method: 'POST', headers,
          body: JSON.stringify({ date, metric_name: metric, value: metrics[metric], unit: metric === 'steps' ? 'steps' : metric === 'sleep' ? 'hours' : 'g', notes: 'synced_from_wearable_screenshot' })
        });
      } catch (e) { console.warn('[WATCH-SYNC] Log error:', e); }
    }
  }
}

// ── Wire sync wearable button ──────────────────────────────────────────────────
function initSyncWearable() {
  // script.js wires syncWearableBtn like this:
  //   mobile  → cameraInput.click()   (but holds old node ref in closure)
  //   desktop → fileInput.click()     (wrong input entirely)
  //
  // Fix: create our own dedicated hidden input, then REPLACE the button's
  // click with onclick (overwrites all prior addEventListener clicks) so
  // only our handler fires. No cloneNode needed.
  function _wire() {
    const btn = document.getElementById('syncWearableBtn');
    if (!btn) { setTimeout(_wire, 300); return; }

    // Create a dedicated input that only this feature uses
    let inp = document.getElementById('_watchSyncInput');
    if (!inp) {
      inp = document.createElement('input');
      inp.type = 'file';
      inp.id = '_watchSyncInput';
      inp.accept = 'image/*';
      inp.style.display = 'none';
      document.body.appendChild(inp);
    }

    // onclick overwrites ALL previous click listeners on the element
    btn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      inp.value = ''; // reset so same file can be re-selected
      inp.click();
    };

    inp.addEventListener('change', async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      e.target.value = '';
      await processWatchScreenshot(file);
    });
  }
  _wire();
}

// ══════════════════════════════════════════════════════════════════════════════
// 5. DOCTOR PREP MODAL — real content from stored data
// ══════════════════════════════════════════════════════════════════════════════
async function openDoctorPrepModal() {
  const existing = document.getElementById('doctorPrepModal');
  if (existing) existing.remove();
  const modal = document.createElement('div');
  modal.id = 'doctorPrepModal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px;animation:fadeIn .2s ease';
  modal.innerHTML = `
    <div style="background:var(--surface,#111318);border:1px solid var(--border-2,rgba(255,255,255,.13));border-radius:20px;padding:28px 24px;max-width:600px;width:100%;max-height:85vh;overflow-y:auto;box-shadow:0 32px 80px rgba(0,0,0,.6);position:relative;">
      <button onclick="document.getElementById('doctorPrepModal').remove()" style="position:absolute;top:14px;right:14px;width:30px;height:30px;border-radius:8px;border:none;background:var(--surface-3,#23272f);color:var(--text-2,#9aa3b0);font-size:.85rem;cursor:pointer;display:flex;align-items:center;justify-content:center;">✕</button>
      <h3 style="font-size:1.1rem;font-weight:700;color:var(--text,#f0f2f5);margin-bottom:6px;">🩺 Doctor Visit Prep</h3>
      <p style="font-size:.78rem;color:var(--text-3,#566070);margin-bottom:18px;">PHI-generated brief from your stored lab data</p>
      <div id="doctorPrepContent" style="font-size:.85rem;color:var(--text-2,#9aa3b0);line-height:1.7;">
        <div style="display:flex;align-items:center;gap:8px;padding:20px 0;color:var(--signal,#00d4c8);"><i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i> Generating your brief from stored lab data…</div>
      </div>
    </div>
  `;
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);
  const contentEl = document.getElementById('doctorPrepContent');
  try {
    const h = await _authHeaders();
    if (!h) throw new Error('Please sign in first.');
    const briefRes = await fetch(_API + '/api/doctor-brief', { method: 'POST', headers: h, body: JSON.stringify({ symptoms: [], medications: [], notes: '' }) });
    if (!briefRes.ok) { const err = await briefRes.json().catch(() => ({})); throw new Error(err.error || 'Could not generate brief. Upload a lab report first.'); }
    const briefData = await briefRes.json();
    const briefText = briefData.brief || 'No brief content returned.';
    contentEl.innerHTML = `
      <div style="background:var(--surface-2,#1a1d24);border-radius:10px;padding:16px;white-space:pre-wrap;font-family:var(--sans,'sans-serif');font-size:.82rem;color:var(--text,#f0f2f5);line-height:1.75;max-height:420px;overflow-y:auto;">${escHtml(briefText)}</div>
      <div style="display:flex;gap:8px;margin-top:12px;">
        <button onclick="navigator.clipboard&&navigator.clipboard.writeText(document.querySelector('#doctorPrepContent div').innerText);typeof toast==='function'&&toast('Copied ✓')" style="flex:1;padding:10px;background:var(--signal,#00d4c8);color:#0a0b0e;border:none;border-radius:8px;font-size:.82rem;font-weight:700;cursor:pointer;">Copy Brief</button>
        <button onclick="generateFreshDoctorPrep()" style="flex:1;padding:10px;background:var(--surface-3,#23272f);color:var(--text-2,#9aa3b0);border:1px solid var(--border,rgba(255,255,255,.07));border-radius:8px;font-size:.82rem;cursor:pointer;">Regenerate</button>
      </div>
    `;
  } catch (e) {
    contentEl.innerHTML = `<div style="color:var(--danger,#f87171);font-size:.82rem;">${e.message}</div><p style="font-size:.76rem;color:var(--text-3,#566070);margin-top:8px;">Upload a lab report first, then generate your doctor brief.</p>`;
  }
}

async function generateFreshDoctorPrep() {
  const contentEl = document.getElementById('doctorPrepContent');
  if (!contentEl) return;
  contentEl.innerHTML = `<div style="display:flex;align-items:center;gap:8px;padding:20px 0;color:var(--signal,#00d4c8);"><i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i> Generating fresh brief…</div>`;
  try {
    const h = await _authHeaders();
    if (!h) throw new Error('Session expired.');
    const res = await fetch(_API + '/api/doctor-brief', { method: 'POST', headers: h, body: JSON.stringify({ symptoms: [], medications: [], notes: '' }) });
    if (!res.ok) throw new Error('Brief generation failed.');
    const data = await res.json();
    contentEl.innerHTML = `<div style="background:var(--surface-2,#1a1d24);border-radius:10px;padding:16px;white-space:pre-wrap;font-family:var(--sans,'sans-serif');font-size:.82rem;color:var(--text,#f0f2f5);line-height:1.75;max-height:420px;overflow-y:auto;">${escHtml(data.brief||'No content returned.')}</div><button onclick="navigator.clipboard&&navigator.clipboard.writeText(document.querySelector('#doctorPrepContent div').innerText);typeof toast==='function'&&toast('Copied ✓')" style="margin-top:12px;padding:9px 18px;background:var(--signal,#00d4c8);color:#0a0b0e;border:none;border-radius:8px;font-size:.8rem;font-weight:700;cursor:pointer;">Copy Brief</button>`;
  } catch (e) { contentEl.innerHTML = `<div style="color:var(--danger,#f87171);font-size:.82rem;">${e.message}</div>`; }
}
window.openDoctorPrepModal = openDoctorPrepModal;

// ══════════════════════════════════════════════════════════════════════════════
// 6. APPOINTMENT PREP MODAL
// ══════════════════════════════════════════════════════════════════════════════
async function openAppointmentPrepModal() {
  const existing = document.getElementById('apptPrepModal');
  if (existing) existing.remove();
  const modal = document.createElement('div');
  modal.id = 'apptPrepModal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px;animation:fadeIn .2s ease';
  const defaultDt = new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 10);
  modal.innerHTML = `
    <div style="background:var(--surface,#111318);border:1px solid var(--border-2,rgba(255,255,255,.13));border-radius:20px;padding:28px 24px;max-width:500px;width:100%;max-height:90vh;overflow-y:auto;box-shadow:0 32px 80px rgba(0,0,0,.6);position:relative;">
      <button onclick="document.getElementById('apptPrepModal').remove()" style="position:absolute;top:14px;right:14px;width:30px;height:30px;border-radius:8px;border:none;background:var(--surface-3,#23272f);color:var(--text-2,#9aa3b0);font-size:.85rem;cursor:pointer;display:flex;align-items:center;justify-content:center;">✕</button>
      <h3 style="font-size:1.1rem;font-weight:700;color:var(--text,#f0f2f5);margin-bottom:6px;">📅 Appointment Prep</h3>
      <p style="font-size:.78rem;color:var(--text-3,#566070);margin-bottom:20px;">PHI builds a tailored one-page clinical brief from your lab data.</p>
      <div id="apptPrepContent">
        <div style="margin-bottom:14px;"><label style="display:block;font-size:.72rem;font-weight:700;color:var(--text-3,#566070);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">Appointment date</label><input type="date" id="apptDate" value="${defaultDt}" style="width:100%;padding:10px 12px;background:var(--surface-2,#1a1d24);border:1px solid var(--border,rgba(255,255,255,.07));border-radius:8px;color:var(--text,#f0f2f5);font-size:.88rem;outline:none;"></div>
        <div style="margin-bottom:20px;"><label style="display:block;font-size:.72rem;font-weight:700;color:var(--text-3,#566070);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">Specialist type</label><select id="apptSpecialist" style="width:100%;padding:10px 12px;background:var(--surface-2,#1a1d24);border:1px solid var(--border,rgba(255,255,255,.07));border-radius:8px;color:var(--text,#f0f2f5);font-size:.88rem;outline:none;cursor:pointer;"><option value="primary care">Primary Care / GP</option><option value="endocrinologist">Endocrinologist</option><option value="cardiologist">Cardiologist</option><option value="obesity medicine">Obesity Medicine</option><option value="nephrologist">Nephrologist</option><option value="other">Other specialist</option></select></div>
        <button onclick="generateAppointmentPrep()" id="apptPrepBtn" style="width:100%;padding:13px;background:var(--signal,#00d4c8);color:#0a0b0e;border:none;border-radius:10px;font-size:.9rem;font-weight:700;cursor:pointer;">Generate My Brief</button>
      </div>
    </div>
  `;
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);
}

async function generateAppointmentPrep() {
  const btn = document.getElementById('apptPrepBtn');
  const date = document.getElementById('apptDate')?.value;
  const specialist = document.getElementById('apptSpecialist')?.value || 'primary care';
  if (!date) { if (typeof toast === 'function') toast('Please select an appointment date.', 'info'); return; }
  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i> Generating…'; }
  try {
    const h = await _authHeaders();
    if (!h) throw new Error('Session expired.');
    const res = await fetch(_API + '/api/appointment-prep', { method: 'POST', headers: h, body: JSON.stringify({ appointment_date: date, specialist_type: specialist }) });
    if (!res.ok) throw new Error('Could not generate prep brief.');
    const data = await res.json();
    const prep = data.prep || {};
    const text = prep.formatted || 'No content returned.';
    const contentEl = document.getElementById('apptPrepContent');
    if (!contentEl) return;
    contentEl.innerHTML = `<div style="background:var(--surface-2,#1a1d24);border-radius:10px;padding:16px;white-space:pre-wrap;font-family:var(--sans,'sans-serif');font-size:.8rem;color:var(--text,#f0f2f5);line-height:1.75;max-height:420px;overflow-y:auto;margin-bottom:12px;">${escHtml(text)}</div><div style="display:flex;gap:8px;"><button onclick="navigator.clipboard&&navigator.clipboard.writeText(document.querySelector('#apptPrepContent div').innerText);typeof toast==='function'&&toast('Copied ✓')" style="flex:1;padding:10px;background:var(--signal,#00d4c8);color:#0a0b0e;border:none;border-radius:8px;font-size:.82rem;font-weight:700;cursor:pointer;">Copy Brief</button><button onclick="openAppointmentPrepModal()" style="flex:1;padding:10px;background:var(--surface-3,#23272f);color:var(--text-2,#9aa3b0);border:1px solid var(--border,rgba(255,255,255,.07));border-radius:8px;font-size:.82rem;cursor:pointer;">New Date</button></div>`;
  } catch (e) {
    if (btn) { btn.disabled = false; btn.innerHTML = 'Generate My Brief'; }
    if (typeof toast === 'function') toast(e.message, 'err');
  }
}
window.openAppointmentPrepModal = openAppointmentPrepModal;

// ══════════════════════════════════════════════════════════════════════════════
// 7. PA ARCHITECT — auto-fetches stored markers + manual additions
// ══════════════════════════════════════════════════════════════════════════════
async function openPAArchitectModal() {
  const existing = document.getElementById('paModal');
  if (existing) existing.remove();
  const modal = document.createElement('div');
  modal.id = 'paModal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px;animation:fadeIn .2s ease';
  modal.innerHTML = `
    <div style="background:var(--surface,#111318);border:1px solid var(--border-2,rgba(255,255,255,.13));border-radius:20px;padding:28px 24px;max-width:640px;width:100%;max-height:90vh;overflow-y:auto;box-shadow:0 32px 80px rgba(0,0,0,.6);position:relative;">
      <button onclick="document.getElementById('paModal').remove()" style="position:absolute;top:14px;right:14px;width:30px;height:30px;border-radius:8px;border:none;background:var(--surface-3,#23272f);color:var(--text-2,#9aa3b0);font-size:.85rem;cursor:pointer;display:flex;align-items:center;justify-content:center;">✕</button>
      <h3 style="font-size:1.1rem;font-weight:700;color:var(--text,#f0f2f5);margin-bottom:4px;">🛡 Insurance PA Architect</h3>
      <p style="font-size:.78rem;color:var(--text-3,#566070);margin-bottom:16px;">Auto-populated from your stored lab data. Add anything missing below.</p>

      <div id="paAutoData" style="background:var(--signal-dim,rgba(0,212,200,.08));border:1px solid rgba(0,212,200,.2);border-radius:10px;padding:12px 14px;margin-bottom:16px;font-size:.78rem;color:var(--text-2,#9aa3b0);">
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;color:var(--signal,#00d4c8);font-weight:600;font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;"><i class="fa-solid fa-database"></i> Auto-detected from your health memory</div>
        <div id="paAutoDataList"><i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i> Loading your stored data…</div>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px;">
        <div><label style="display:block;font-size:.7rem;font-weight:700;color:var(--text-3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px;">Medication</label><select id="paMedication" style="width:100%;padding:9px 11px;background:var(--surface-2,#1a1d24);border:1px solid var(--border,rgba(255,255,255,.07));border-radius:8px;color:var(--text,#f0f2f5);font-size:.84rem;outline:none;"><option value="Wegovy (semaglutide)">Wegovy</option><option value="Zepbound (tirzepatide)">Zepbound</option><option value="Ozempic (semaglutide)">Ozempic</option><option value="Mounjaro (tirzepatide)">Mounjaro</option><option value="GLP-1">GLP-1 (general)</option></select></div>
        <div><label style="display:block;font-size:.7rem;font-weight:700;color:var(--text-3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px;">Denial Reason</label><select id="paDenialReason" style="width:100%;padding:9px 11px;background:var(--surface-2,#1a1d24);border:1px solid var(--border,rgba(255,255,255,.07));border-radius:8px;color:var(--text,#f0f2f5);font-size:.84rem;outline:none;"><option value="not medically necessary">Not medically necessary</option><option value="step therapy required">Step therapy required</option><option value="BMI below threshold">BMI below threshold</option><option value="HbA1c not high enough">HbA1c not high enough</option><option value="not on formulary">Not on formulary</option><option value="coverage excluded">Coverage excluded</option></select></div>
      </div>

      <div style="margin-bottom:14px;">
        <label style="display:block;font-size:.7rem;font-weight:700;color:var(--text-3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px;">Add additional context <span style="font-weight:400;opacity:.6">(optional — supplements auto data)</span></label>
        <textarea id="paAdditionalContext" style="width:100%;padding:10px 12px;background:var(--surface-2,#1a1d24);border:1px solid var(--border,rgba(255,255,255,.07));border-radius:8px;color:var(--text,#f0f2f5);font-size:.84rem;outline:none;resize:vertical;min-height:60px;" placeholder="e.g. I was on Zepbound for 8 months and lost 27 lbs. Stopping caused my glucose to rebound significantly. My doctor supports continuation…"></textarea>
      </div>

      <div style="margin-bottom:16px;">
        <label style="display:block;font-size:.7rem;font-weight:700;color:var(--text-3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px;">Prior medications tried (check all that apply)</label>
        <div style="display:flex;flex-wrap:wrap;gap:6px;" id="paPriorMeds">
          ${['Metformin','Phentermine/Qsymia','Contrave','6+ month diet program','Bariatric evaluation'].map(m => `<label style="display:flex;align-items:center;gap:5px;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;padding:5px 10px;cursor:pointer;font-size:.76rem;color:var(--text-2);"><input type="checkbox" value="${m}" style="accent-color:var(--signal);"> ${m}</label>`).join('')}
        </div>
      </div>

      <button onclick="generatePAPacket()" id="paBtn" style="width:100%;padding:13px;background:var(--signal,#00d4c8);color:#0a0b0e;border:none;border-radius:10px;font-size:.9rem;font-weight:700;cursor:pointer;margin-bottom:14px;">Build PA Packet from My Data</button>
      <div id="paContent"></div>
      <p style="font-size:.68rem;color:var(--text-3,#566070);margin-top:10px;line-height:1.6;">⚕️ This is an informational support document from your stored data. Share with your provider — they make all clinical decisions.</p>
    </div>
  `;
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);

  // Auto-fetch stored health data
  _loadPAAutoData();
}

async function _loadPAAutoData() {
  const listEl = document.getElementById('paAutoDataList');
  if (!listEl) return;
  try {
    // ── Use cached startup data (script.js / performance_patch.js already fetched this) ──
    // Falls back to a single fetch only if cache is empty (e.g. first ever load)
    let markers = window.__cachedMarkers || window.__startupCache?.markers || null;
    let memories = window._cachedMemories || window.__startupCache?.memories || null;

    if (!markers || !memories) {
      const h = await _authHeaders();
      if (!h) { listEl.textContent = 'Sign in to load your stored data.'; return; }
      const [mRes, memRes] = await Promise.all([
        fetch(_API + '/api/health-markers', { headers: h }),
        fetch(_API + '/api/memory/facts', { headers: h }),
      ]);
      markers = mRes.ok ? await mRes.json() : [];
      const memData = memRes.ok ? await memRes.json() : {};
      memories = memData.facts || [];
      // Cache for subsequent calls this session
      window.__cachedMarkers = markers;
    }

    const dataPoints = [];
    const paMarkers = (markers || []).filter(m => /bmi|weight|hba1c|glucose|ldl|hdl|triglyceride|crp|blood pressure/i.test(m.marker_name || ''));
    paMarkers.slice(0, 6).forEach(m => {
      const status = m.status && m.status !== 'UNKNOWN' ? ` [${m.status}]` : '';
      const color = m.status === 'HIGH' ? 'var(--danger,#f87171)' : m.status === 'LOW' ? 'var(--amber,#fbbf24)' : 'var(--ok,#4ade80)';
      dataPoints.push(`<span style="color:${color}">${m.marker_name}: ${m.value} ${m.unit || ''}${status} (${m.date || ''})</span>`);
    });

    const memArr = Array.isArray(memories) ? memories : [];
    memArr.filter(f => /glp|wegovy|ozempic|zepbound|mounjaro|stopped|started|medication|insurance|goal weight/i.test(f))
      .slice(0, 3).forEach(m => dataPoints.push(`<span style="color:var(--text,#f0f2f5)">▸ ${escHtml(m)}</span>`));

    listEl.innerHTML = dataPoints.length
      ? dataPoints.join('<br>')
      : '<span style="color:var(--text-3);">No stored data found. Upload a lab report first to enable auto-population.</span>';

    // Auto-fill medication select
    const glpMemory = memArr.find(f => /wegovy|ozempic|zepbound|mounjaro/i.test(f));
    if (glpMemory) {
      const medSel = document.getElementById('paMedication');
      if (medSel) {
        if (/wegovy/i.test(glpMemory)) medSel.value = 'Wegovy (semaglutide)';
        else if (/zepbound/i.test(glpMemory)) medSel.value = 'Zepbound (tirzepatide)';
        else if (/ozempic/i.test(glpMemory)) medSel.value = 'Ozempic (semaglutide)';
        else if (/mounjaro/i.test(glpMemory)) medSel.value = 'Mounjaro (tirzepatide)';
      }
    }
    if (memArr.find(f => /insurance denied|prior auth denied|not covered/i.test(f))) {
      const denialSel = document.getElementById('paDenialReason');
      if (denialSel) denialSel.value = 'not medically necessary';
    }
  } catch (e) {
    if (listEl) listEl.innerHTML = `<span style="color:var(--text-3);">Could not load stored data: ${e.message}</span>`;
  }
}
async function generatePAPacket() {
  const btn = document.getElementById('paBtn');
  const content = document.getElementById('paContent');
  const med = document.getElementById('paMedication')?.value || 'GLP-1';
  const denialReason = document.getElementById('paDenialReason')?.value || 'not medically necessary';
  const additionalContext = document.getElementById('paAdditionalContext')?.value || '';
  const priorMeds = Array.from(document.querySelectorAll('#paPriorMeds input:checked')).map(c => c.value);

  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i> Building packet from your labs…'; }
  if (content) content.innerHTML = '';

  try {
    const h = await _authHeaders();
    if (!h) throw new Error('Session expired.');

    // Fetch advocacy brief from backend (uses all stored data automatically)
    const res = await fetch(`${_API}/api/advocacy?medication=${encodeURIComponent(med)}&raw=false`, { headers: h });

    let data = null;
    if (res.status === 403) {
      // Consent issue — try without advocacy endpoint
      throw new Error('Enable AI processing in settings to use this feature.');
    }

    if (res.ok) {
      data = await res.json();
    } else {
      // Fallback: use appeal endpoint (public, no auth required for base)
      const markers = await fetch(_API + '/api/health-markers', { headers: h }).then(r => r.json()).catch(() => []);
      const fallbackBody = {
        med, reason: 'weight management', denial_reason: denialReason,
        additional_context: additionalContext, prior_meds: priorMeds,
        comorbidities: [],
      };
      // Extract values from markers for the appeal
      markers.forEach(m => {
        const name = (m.marker_name || '').toLowerCase();
        if (/bmi/.test(name) && m.value) fallbackBody.bmi = parseFloat(m.value);
        if (/weight/.test(name) && m.value) fallbackBody.weight = parseFloat(m.value);
        if (/hba1c/.test(name) && m.value) fallbackBody.hba1c = parseFloat(m.value);
        if (/fasting.*glucose/.test(name) && m.value) fallbackBody.glucose = parseFloat(m.value);
        if (/^ldl/.test(name) && m.value) fallbackBody.ldl = parseFloat(m.value);
        if (/blood pressure|systolic/.test(name) && m.value) fallbackBody.bp = String(m.value);
      });

      const appealRes = await fetch(_API + '/api/appeal/generate', { method: 'POST', headers: h, body: JSON.stringify(fallbackBody) });
      if (appealRes.ok) data = await appealRes.json();
    }

    if (!data) throw new Error('Could not generate PA packet. Upload lab reports first.');

    const strength = data.evidence_strength || (data.score >= 70 ? 'strong' : data.score >= 45 ? 'moderate' : 'limited');
    const strengthColor = { strong: '#4ade80', moderate: '#fbbf24', limited: '#f87171' }[strength] || '#fbbf24';
    const packet = data.pa_packet || data.packet || 'No packet content returned.';
    const missing = (data.missing_data || data.missing || []).slice(0, 4);
    const steps = (data.next_steps || []).slice(0, 4);
    const facts = (data.clinical_facts || data.facts || []).slice(0, 6);
    const score = data.score || (strength === 'strong' ? 82 : strength === 'moderate' ? 55 : 30);

    content.innerHTML = `
      <div style="background:rgba(0,0,0,.2);border-radius:8px;padding:10px 12px;margin-bottom:12px;display:flex;align-items:center;justify-content:space-between;">
        <span style="font-size:.72rem;color:var(--text-3);text-transform:uppercase;letter-spacing:.06em;">Evidence strength</span>
        <div style="display:flex;align-items:center;gap:10px;">
          <div style="width:80px;height:5px;background:var(--surface-3);border-radius:3px;overflow:hidden;"><div style="width:${score}%;height:100%;background:${strengthColor};border-radius:3px;transition:width 1s ease;"></div></div>
          <span style="font-size:.88rem;font-weight:700;color:${strengthColor};">${strength.toUpperCase()}</span>
        </div>
      </div>
      ${facts.length ? `<div style="margin-bottom:12px;"><div style="font-size:.7rem;font-weight:700;color:var(--ok,#4ade80);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">Clinical Facts in Your Record</div>${facts.map(f => {const type = f.type || 'strong'; const icon = type === 'strong' ? '✓' : '⚠'; const color = type === 'strong' ? 'var(--ok,#4ade80)' : 'var(--amber,#fbbf24)'; return `<div style="display:flex;gap:8px;padding:6px 0;border-bottom:1px solid rgba(255,255,255,.04);font-size:.78rem;"><span style="color:${color};flex-shrink:0;">${f.icon||icon}</span><span style="color:var(--text-2,#9aa3b0);">${escHtml(f.text||f.value||'')}</span></div>`}).join('')}</div>` : ''}
      <div style="background:var(--surface-2,#1a1d24);border-radius:10px;padding:14px;white-space:pre-wrap;font-size:.75rem;color:var(--text-2,#9aa3b0);line-height:1.75;max-height:340px;overflow-y:auto;margin-bottom:12px;font-family:monospace;">${escHtml(packet)}</div>
      ${missing.length ? `<div style="margin-bottom:12px;"><div style="font-size:.7rem;font-weight:700;color:var(--amber,#fbbf24);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">⚠ Would strengthen your case</div>${missing.map(m => `<div style="font-size:.78rem;color:var(--text-2);padding:4px 0;border-bottom:1px solid rgba(255,255,255,.04);">• ${escHtml(m)}</div>`).join('')}</div>` : ''}
      ${steps.length ? `<div style="margin-bottom:12px;"><div style="font-size:.7rem;font-weight:700;color:var(--signal,#00d4c8);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">Next Steps</div>${steps.map((s, i) => `<div style="font-size:.78rem;color:var(--text-2);padding:4px 0;">${i+1}. ${escHtml(s)}</div>`).join('')}</div>` : ''}
      <div style="background:var(--surface-2,#1a1d24);border:1px solid var(--border,rgba(255,255,255,.07));border-radius:10px;padding:14px;margin-bottom:10px;">
        <div style="font-size:.68rem;font-weight:700;color:var(--signal,#00d4c8);text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px;">What to do now</div>
        <div style="display:flex;flex-direction:column;gap:8px;">
          <div style="display:flex;gap:10px;align-items:flex-start;font-size:.78rem;color:var(--text-2);">
            <span style="background:var(--signal,#00d4c8);color:#0a0b0e;width:18px;height:18px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:.6rem;font-weight:800;flex-shrink:0;margin-top:1px;">1</span>
            <span><strong style="color:var(--text);">Copy the packet below</strong> and send it to your doctor's office or pharmacy — ask them to submit it as-is to your insurer.</span>
          </div>
          <div style="display:flex;gap:10px;align-items:flex-start;font-size:.78rem;color:var(--text-2);">
            <span style="background:var(--signal,#00d4c8);color:#0a0b0e;width:18px;height:18px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:.6rem;font-weight:800;flex-shrink:0;margin-top:1px;">2</span>
            <span><strong style="color:var(--text);">Call your insurer</strong> (number on your card) and ask them to open or reopen a Prior Authorization case. Reference your medication by name.</span>
          </div>
          <div style="display:flex;gap:10px;align-items:flex-start;font-size:.78rem;color:var(--text-2);">
            <span style="background:var(--signal,#00d4c8);color:#0a0b0e;width:18px;height:18px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:.6rem;font-weight:800;flex-shrink:0;margin-top:1px;">3</span>
            <span><strong style="color:var(--text);">Upload any denial letter</strong> to Curabook as a document — PHI will extract the exact denial language and strengthen your next appeal.</span>
          </div>
        </div>
      </div>
      <div style="display:flex;gap:8px;">
        <button id="paCopyBtn" onclick="(function(){const txt=document.querySelector('#paContent div[style*=monospace]')?.innerText||'';navigator.clipboard&&navigator.clipboard.writeText(txt).then(()=>{const b=document.getElementById('paCopyBtn');if(b){b.textContent='Copied ✓';b.style.background='var(--ok,#4ade80)';setTimeout(()=>{b.textContent='Copy PA Packet';b.style.background='var(--signal,#00d4c8)'},2000);}}).catch(()=>{});if(typeof toast==='function')toast('PA packet copied ✓');})()" style="flex:1;padding:11px;background:var(--signal,#00d4c8);color:#0a0b0e;border:none;border-radius:8px;font-size:.84rem;font-weight:700;cursor:pointer;transition:background .2s;">Copy PA Packet</button>
        <button onclick="(function(){const txt=document.querySelector('#paContent div[style*=monospace]')?.innerText||'';const med=document.getElementById('paMedication')?.value||'GLP-1';const sub=encodeURIComponent('Prior Authorization Support — '+med);const body=encodeURIComponent('Dear Doctor,\n\nPlease find my PA support packet below for your review and submission.\n\n'+txt+'\n\nThank you.');window.open('mailto:?subject='+sub+'&body='+body);})()" style="padding:11px 14px;background:var(--surface-3,#23272f);color:var(--text-2);border:1px solid var(--border);border-radius:8px;font-size:.84rem;cursor:pointer;font-weight:600;white-space:nowrap;">Email to Doctor</button>
      </div>
    `;
  } catch (e) {
    if (content) content.innerHTML = `<div style="color:var(--danger,#f87171);font-size:.82rem;">${e.message}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = 'Build PA Packet from My Data'; }
  }
}
window.openPAArchitectModal = openPAArchitectModal;

// ══════════════════════════════════════════════════════════════════════════════
// 8. QUICK ACTIONS — injected into user dropdown (not sidebar history area)
// ══════════════════════════════════════════════════════════════════════════════

function injectQuickActionsIntoDropdown() {
  if (document.getElementById('quickActionsDropdownItems')) return;

  // Wait for user dropdown to exist
  const dropdown = document.getElementById('userDropdown');
  if (!dropdown) { setTimeout(injectQuickActionsIntoDropdown, 400); return; }

  // Inject styles for quick-action items inside dropdown
  if (!document.getElementById('qaDropdownStyles')) {
    const style = document.createElement('style');
    style.id = 'qaDropdownStyles';
    style.textContent = `
      .qa-dd-label {
        font-size:.6rem;font-weight:700;color:var(--text-3);
        text-transform:uppercase;letter-spacing:.1em;
        padding:8px 14px 4px;display:block;
      }
      .qa-dd-item {
        width:100%;min-height:40px;padding:0 14px;
        font-size:.82rem;color:var(--text-2);
        display:flex;align-items:center;gap:9px;
        transition:background .15s;text-align:left;font-weight:500;
        cursor:pointer;border:none;background:none;font-family:inherit;
      }
      .qa-dd-item:hover { background:var(--surface-2,#1a1d24); color:var(--text); }
      .qa-dd-item i { width:15px;text-align:center;font-size:.78rem;color:var(--text-3); }
      .qa-dd-item:hover i { color:var(--signal,#00d4c8); }
      .qa-dd-divider { height:1px;background:var(--border,rgba(255,255,255,.07));margin:2px 0; }
    `;
    document.head.appendChild(style);
  }

  // Build the quick action items block
  const wrap = document.createElement('div');
  wrap.id = 'quickActionsDropdownItems';
  wrap.innerHTML = `
    <div class="qa-dd-divider"></div>
    <span class="qa-dd-label">Quick Actions</span>
    <button class="qa-dd-item" onclick="openDoctorPrepModal();closeUserMenu()">
      <i class="fa-solid fa-stethoscope"></i><span>Doctor Visit Brief</span>
    </button>
    <button class="qa-dd-item" onclick="openAppointmentPrepModal();closeUserMenu()">
      <i class="fa-solid fa-calendar-check"></i><span>Appointment Prep</span>
    </button>
    <button class="qa-dd-item" onclick="openPAArchitectModal();closeUserMenu()">
      <i class="fa-solid fa-shield-halved"></i><span>Insurance PA Architect</span>
    </button>
    <div class="qa-dd-divider"></div>
  `;

  // Insert at the top of the dropdown, before the theme toggle
  dropdown.insertBefore(wrap, dropdown.firstChild);
}

// ── Remove quick actions from cockpit (keep cockpit clean) ───────────────────
function removeCockpitQuickActions() {
  ['quickActionsSection', 'sidebarQuickActions'].forEach(id => {
    document.getElementById(id)?.remove();
  });
}

// ── Upgrade button: small pill in the user-plan line, not a full nav-item ────
function injectUpgradeButton() {
  // Already injected or user is pro — skip
  if (document.getElementById('upgradeNavBtn')) return;
  const planEl = document.getElementById('userPlan');
  if (!planEl) return;

  const pill = document.createElement('button');
  pill.id = 'upgradeNavBtn';
  pill.style.cssText = `
    display:none;           /* shown only for free/trial users via refreshPlanDisplay */
    margin-left:6px;
    padding:1px 7px;
    background:var(--signal-dim,rgba(0,212,200,.1));
    border:1px solid rgba(0,212,200,.25);
    border-radius:20px;
    font-size:.58rem;font-weight:700;
    color:var(--signal,#00d4c8);
    cursor:pointer;
    font-family:inherit;
    vertical-align:middle;
    transition:all .15s;
    line-height:1.4;
  `;
  pill.textContent = 'Upgrade';
  pill.onclick = (e) => { e.stopPropagation(); closeUserMenu(); showUpgradeModal('manual'); };
  planEl.parentNode.insertBefore(pill, planEl.nextSibling);
}

// ── Helper: HTML escape ───────────────────────────────────────────────────────
function escHtml(str) {
  return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ══════════════════════════════════════════════════════════════════════════════
// INIT
// ══════════════════════════════════════════════════════════════════════════════
function initAppFixes() {
  setTimeout(() => {
    injectQuickActionsIntoDropdown();
    injectUpgradeButton();
    removeCockpitQuickActions();
    initSyncWearable();

    // Refresh plan display when script.js signals auth complete
    // Also listen for startup-complete event if script.js fires one
    window.addEventListener('phi:authed', () => refreshPlanDisplay());
    window.addEventListener('phi:startup', () => refreshPlanDisplay());
    // Single deferred attempt in case events already fired before this script loaded
    setTimeout(() => refreshPlanDisplay(), 800);
  }, 300);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAppFixes);
} else {
  initAppFixes();
}

// Clean up cockpit whenever it opens
document.addEventListener('click', e => {
  if (e.target.closest('#mobileCockpitBtn')) setTimeout(removeCockpitQuickActions, 100);
});