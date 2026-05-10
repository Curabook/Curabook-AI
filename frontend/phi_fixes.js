/**
 * phi_fixes.js — Curabook PHI App Patches v2
 *
 * FIXED IN THIS VERSION:
 * 1. All Razorpay references removed — PayPal only
 * 2. Upgrade modal now uses PayPal create-subscription flow
 * 3. Doctor prep stub replaced — real brief shown in modal
 * 4. Appointment Prep UI added to cockpit
 * 5. PA Architect surface added (calls /api/advocacy)
 * 6. Welcome modal, plan badge, trial banner preserved
 */
"use strict";

const _API = window.location.hostname === 'localhost' ? 'http://localhost:5000' : 'https://api.curabook.com';

// ── 1. WELCOME MODAL ──────────────────────────────────────────────────────────
function showWelcomeModal(type) {
  const existing = document.getElementById('welcomeModal');
  if (existing) existing.remove();

  const isPaid = type === 'paid';
  const modal = document.createElement('div');
  modal.id = 'welcomeModal';
  modal.style.cssText = `
    position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:9999;
    display:flex;align-items:center;justify-content:center;padding:20px;
    animation:fadeIn .25s ease;
  `;
  modal.innerHTML = `
    <div style="background:var(--surface,#fff);border-radius:20px;padding:36px;max-width:440px;width:100%;
      box-shadow:0 24px 60px rgba(0,0,0,.2);text-align:center;animation:slideUp .3s ease">
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
        style="width:100%;padding:13px;background:var(--signal,#00d4c8);color:#0a0b0e;border:none;
          border-radius:10px;font-size:.92rem;font-weight:700;cursor:pointer;font-family:var(--sans,'sans-serif');
          box-shadow:0 4px 16px rgba(0,212,200,.3);margin-bottom:10px">
        📎 Upload First Lab Report
      </button>
      <button onclick="document.getElementById('welcomeModal').remove()"
        style="width:100%;padding:11px;background:none;border:1px solid var(--border,rgba(0,0,0,.08));
          border-radius:10px;font-size:.84rem;color:var(--text-2,#888);cursor:pointer;font-family:var(--sans,'sans-serif')">
        Explore first, upload later
      </button>
    </div>
  `;
  document.body.appendChild(modal);
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
}

(function checkWelcomeParam() {
  const params  = new URLSearchParams(window.location.search);
  const welcome = params.get('welcome');
  if (welcome) {
    window.history.replaceState({}, '', window.location.pathname);
    setTimeout(() => showWelcomeModal(welcome), 1200);
  }
})();

// ── 2. UPGRADE BUTTON IN SIDEBAR ─────────────────────────────────────────────
function injectUpgradeButton() {
  const sbFooter = document.querySelector('.sb-footer');
  if (!sbFooter || document.getElementById('upgradeNavBtn')) return;

  const btn = document.createElement('button');
  btn.id = 'upgradeNavBtn';
  btn.innerHTML = `
    <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" style="flex-shrink:0">
      <path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5M2 12l10 5 10-5"/>
    </svg>
    <span>Upgrade Plan</span>
  `;
  btn.style.cssText = `
    width:100%;display:flex;align-items:center;gap:8px;
    padding:0 14px;min-height:44px;border:1.5px solid var(--signal,#00d4c8);
    background:rgba(0,212,200,.06);border-radius:var(--r,10px);
    color:var(--signal,#00d4c8);font-size:.82rem;font-family:var(--sans,'sans-serif');
    font-weight:600;cursor:pointer;margin-bottom:8px;transition:all .15s;
  `;
  btn.onmouseenter = () => btn.style.background = 'rgba(0,212,200,.12)';
  btn.onmouseleave = () => btn.style.background = 'rgba(0,212,200,.06)';
  btn.onclick = () => { if (typeof closeSidebar === 'function') closeSidebar(); showUpgradeModal('manual'); };
  sbFooter.insertBefore(btn, sbFooter.firstChild);
}

// ── 3. PLAN BADGE ─────────────────────────────────────────────────────────────
async function refreshPlanDisplay() {
  try {
    const s = await (typeof session === 'function' ? session() : Promise.resolve(null));
    if (!s?.access_token) return;

    const res = await fetch(_API + '/api/payment/status', {
      headers: { 'Authorization': 'Bearer ' + s.access_token }
    });
    if (!res.ok) return;
    const data = await res.json();

    const planEl = document.getElementById('userPlan');
    if (planEl) {
      const plan = data.plan || 'free';
      const labels = {
        monthly: 'Shield $49/mo ✦', annual: 'Shield Annual ✦',
        clinical: 'Shield Clinical ✦', trial: 'Trial Active', free: 'PHI Free',
      };
      planEl.textContent   = labels[plan] || 'PHI Free';
      planEl.style.color   = data.is_pro ? 'var(--signal,#00d4c8)' : 'var(--text-3,#566070)';
    }

    if (data.plan === 'trial' && data.subscription_end_date) {
      showTrialBanner(data.subscription_end_date);
    }

    const upgradeBtn = document.getElementById('upgradeNavBtn');
    if (upgradeBtn) upgradeBtn.style.display = data.is_pro ? 'none' : '';

    if (typeof _userPlan !== 'undefined') {
      window._userPlan         = data.plan || 'free';
      window._reportsRemaining = data.reports_remaining ?? 1;
    }
  } catch (e) {
    console.warn('[PLAN] Status refresh error:', e);
  }
}

function showTrialBanner(endDate) {
  if (document.getElementById('trialBanner')) return;
  try {
    const end  = new Date(endDate);
    const days = Math.ceil((end - Date.now()) / 86400000);
    if (days <= 0) return;

    const banner = document.createElement('div');
    banner.id = 'trialBanner';
    banner.style.cssText = `
      position:fixed;top:0;left:0;right:0;z-index:200;
      background:linear-gradient(90deg,var(--signal,#00d4c8),#00a89e);
      color:#0a0b0e;text-align:center;padding:9px 20px;font-size:.8rem;font-weight:600;
      display:flex;align-items:center;justify-content:center;gap:12px;
    `;
    banner.innerHTML = `
      <span>⏰ Trial active — ${days} day${days !== 1 ? 's' : ''} remaining</span>
      <button onclick="showUpgradeModal('trial');document.getElementById('trialBanner').remove()"
        style="background:rgba(0,0,0,.15);border:none;border-radius:6px;padding:4px 12px;
          color:#0a0b0e;font-size:.75rem;font-weight:700;cursor:pointer;font-family:inherit">
        Upgrade Now
      </button>
      <button onclick="document.getElementById('trialBanner').remove()"
        style="background:none;border:none;color:rgba(0,0,0,.5);cursor:pointer;font-size:1.1rem;padding:0 4px">
        ×
      </button>
    `;
    document.body.prepend(banner);
    const topbar = document.getElementById('topbar');
    if (topbar) topbar.style.marginTop = banner.offsetHeight + 'px';
  } catch (e) {}
}

// ── 4. UPGRADE MODAL — PayPal only ───────────────────────────────────────────
window.showUpgradeModal = function(reason) {
  const existing = document.getElementById('upgradeModal');
  if (existing) existing.remove();

  const modal = document.createElement('div');
  modal.id = 'upgradeModal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.78);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px;animation:fadeIn .2s ease';

  const features = [
    ['fa-file-medical',  'Unlimited lab reports'],
    ['fa-brain',         'Full health memory'],
    ['fa-shield-halved', 'Insurance PA support'],
    ['fa-bell',          'Weekly health briefs (emailed)'],
    ['fa-chart-line',    'Trend tracking'],
    ['fa-stethoscope',   'Doctor visit prep'],
  ];

  modal.innerHTML = `
    <div style="
      background:var(--surface,#111318);
      border:1px solid var(--border-2,rgba(255,255,255,.13));
      border-radius:20px; padding:28px 24px 24px;
      max-width:420px; width:100%;
      box-shadow:0 32px 80px rgba(0,0,0,.6);
      position:relative; overflow:hidden;
    ">
      <div style="position:absolute;top:0;left:0;right:0;height:3px;
        background:linear-gradient(90deg,var(--signal,#00d4c8),#00a89e);"></div>

      <button onclick="closeUpgradeModal()" style="
        position:absolute;top:14px;right:14px;width:30px;height:30px;border-radius:8px;
        border:none;background:var(--surface-3,#23272f);
        color:var(--text-2,#9aa3b0);font-size:.85rem;cursor:pointer;
        display:flex;align-items:center;justify-content:center;">✕</button>

      <div style="display:flex;align-items:center;gap:12px;margin-bottom:18px;padding-right:36px;">
        <div style="width:42px;height:42px;flex-shrink:0;
          background:linear-gradient(135deg,var(--signal,#00d4c8),#00a89e);
          border-radius:12px;display:flex;align-items:center;justify-content:center;
          font-family:Georgia,serif;font-size:1.15rem;color:#0a0b0e;font-weight:600;
          box-shadow:0 4px 16px rgba(0,212,200,.3);">φ</div>
        <div>
          <h3 style="font-size:1.1rem;font-weight:700;color:var(--text,#f0f2f5);margin-bottom:3px;line-height:1.2;">
            Upgrade to PHI Shield
          </h3>
          <p style="font-size:.73rem;color:var(--text-3,#566070);line-height:1.4;">
            Unlimited reports · Health memory · Insurance PA support
          </p>
        </div>
      </div>

      ${reason === 'upload' ? `
        <div style="background:rgba(251,191,36,.12);border:1px solid rgba(251,191,36,.3);
          border-radius:10px;padding:10px 13px;margin-bottom:16px;
          font-size:.8rem;color:#fbbf24;display:flex;align-items:center;gap:8px;">
          <span style="flex-shrink:0;">⚠</span>
          <span><strong>Free limit reached</strong> — upgrade to upload unlimited reports.</span>
        </div>` : ''}

      <div style="background:var(--surface-2,#1a1d24);border:1px solid var(--border,rgba(255,255,255,.07));
        border-radius:12px;padding:14px 16px;margin-bottom:18px;">
        <div style="font-size:.62rem;font-weight:700;color:var(--text-3,#566070);
          text-transform:uppercase;letter-spacing:.1em;margin-bottom:10px;">What you unlock</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:7px 12px;">
          ${features.map(([icon, label]) => `
            <div style="display:flex;align-items:center;gap:7px;font-size:.78rem;color:var(--text-2,#9aa3b0);">
              <i class="fa-solid ${icon}" style="color:var(--signal,#00d4c8);font-size:.7rem;width:14px;text-align:center;flex-shrink:0;"></i>
              ${label}
            </div>`).join('')}
        </div>
      </div>

      <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:14px;">

        <button id="ppMonthlyBtn" onclick="initiatePayPalCheckout('monthly')" style="
          width:100%;padding:13px 16px;
          background:var(--signal,#00d4c8);color:#0a0b0e;
          border:none;border-radius:12px;
          font-size:.9rem;font-weight:700;cursor:pointer;font-family:inherit;
          box-shadow:0 4px 20px rgba(0,212,200,.35);transition:all .15s;
          display:flex;align-items:center;justify-content:space-between;">
          <span>Shield Monthly</span>
          <span style="font-family:monospace;font-size:1rem;">$49<span style="font-size:.72rem;font-weight:500;">/mo</span></span>
        </button>

        <button id="ppAnnualBtn" onclick="initiatePayPalCheckout('annual')" style="
          width:100%;padding:13px 16px;
          background:var(--surface-3,#23272f);color:var(--text,#f0f2f5);
          border:2px solid var(--border-2,rgba(255,255,255,.13));border-radius:12px;
          font-size:.9rem;font-weight:600;cursor:pointer;font-family:inherit;
          transition:all .15s;
          display:flex;align-items:center;justify-content:space-between;">
          <span style="display:flex;align-items:center;gap:8px;">
            Shield Annual
            <span style="font-size:.58rem;font-weight:700;background:#10b981;color:#fff;
              padding:2px 7px;border-radius:20px;letter-spacing:.04em;">SAVE 20%</span>
          </span>
          <span style="font-family:monospace;font-size:.85rem;">$39<span style="font-size:.65rem;font-weight:500;">/mo · $468/yr</span></span>
        </button>
      </div>

      <div id="upgradeStatus" style="text-align:center;font-size:.78rem;color:var(--text-3,#566070);min-height:18px;margin-bottom:6px;"></div>
      <p style="font-size:.67rem;color:var(--text-3,#9ca3af);text-align:center;">
        🔒 Secure via PayPal · Cancel anytime
      </p>
    </div>
  `;

  modal.addEventListener('click', e => { if (e.target === modal) closeUpgradeModal(); });
  document.body.appendChild(modal);
};

function closeUpgradeModal() {
  const m = document.getElementById('upgradeModal');
  if (m) m.remove();
}

// ── 5. PAYPAL CHECKOUT (replaces all Razorpay code) ──────────────────────────
async function initiatePayPalCheckout(plan) {
  const statusEl = document.getElementById('upgradeStatus');
  const btnId    = plan === 'annual' ? 'ppAnnualBtn' : 'ppMonthlyBtn';
  const btn      = document.getElementById(btnId);
  const origHtml = btn ? btn.innerHTML : '';

  if (btn) {
    btn.disabled  = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i> Connecting to PayPal…';
  }
  if (statusEl) statusEl.textContent = 'Setting up your subscription…';

  try {
    const s = await (typeof session === 'function' ? session() : Promise.resolve(null));
    if (!s?.access_token) throw new Error('Please sign in first.');

    const res = await fetch(_API + '/api/payment/paypal/create-subscription', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + s.access_token },
      body:    JSON.stringify({ plan }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Payment setup failed. Please try again.');
    }

    const data = await res.json();
    if (!data.approve_url) throw new Error('PayPal did not return a checkout URL.');

    if (statusEl) statusEl.textContent = 'Redirecting to PayPal…';

    // Redirect to PayPal hosted checkout.
    // PayPal returns to /payment/success?subscription_id=xxx&plan=xxx
    // That page calls /api/payment/paypal/capture to activate the plan.
    window.location.href = data.approve_url;

  } catch (e) {
    if (statusEl) statusEl.textContent = e.message;
    if (btn) { btn.disabled = false; btn.innerHTML = origHtml; }
    if (typeof toast === 'function') toast(e.message, 'err');
  }
}

// Keep the old name working in case anything calls it
window.initUpgrade = initiatePayPalCheckout;

// ── 6. DOCTOR PREP MODAL — real content ───────────────────────────────────────
async function openDoctorPrepModal() {
  // Show modal with loading state
  const existing = document.getElementById('doctorPrepModal');
  if (existing) existing.remove();

  const modal = document.createElement('div');
  modal.id = 'doctorPrepModal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px;animation:fadeIn .2s ease';
  modal.innerHTML = `
    <div style="background:var(--surface,#111318);border:1px solid var(--border-2,rgba(255,255,255,.13));
      border-radius:20px;padding:28px 24px;max-width:600px;width:100%;
      max-height:85vh;overflow-y:auto;box-shadow:0 32px 80px rgba(0,0,0,.6);position:relative;">
      <button onclick="document.getElementById('doctorPrepModal').remove()"
        style="position:absolute;top:14px;right:14px;width:30px;height:30px;border-radius:8px;
          border:none;background:var(--surface-3,#23272f);color:var(--text-2,#9aa3b0);
          font-size:.85rem;cursor:pointer;display:flex;align-items:center;justify-content:center;">✕</button>
      <h3 style="font-size:1.1rem;font-weight:700;color:var(--text,#f0f2f5);margin-bottom:6px;">
        🩺 Doctor Visit Prep
      </h3>
      <p style="font-size:.78rem;color:var(--text-3,#566070);margin-bottom:18px;">
        PHI-generated brief from your stored lab data
      </p>
      <div id="doctorPrepContent" style="font-size:.85rem;color:var(--text-2,#9aa3b0);line-height:1.7;">
        <div style="display:flex;align-items:center;gap:8px;padding:20px 0;color:var(--signal,#00d4c8);">
          <i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i>
          Generating your brief from stored lab data…
        </div>
      </div>
    </div>
  `;
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);

  const contentEl = document.getElementById('doctorPrepContent');

  try {
    const s = await (typeof session === 'function' ? session() : Promise.resolve(null));
    if (!s?.access_token) throw new Error('Please sign in first.');

    // Try getting latest prep from history first
    const histRes = await fetch(_API + '/api/appointment-prep/list', {
      headers: { 'Authorization': 'Bearer ' + s.access_token }
    });
    if (histRes.ok) {
      const histData = await histRes.json();
      if (histData.preps && histData.preps.length > 0) {
        const latest = histData.preps[0];
        contentEl.innerHTML = `
          <div style="background:var(--surface-2,#1a1d24);border-radius:10px;padding:16px;
            white-space:pre-wrap;font-family:monospace;font-size:.76rem;
            color:var(--text-2,#9aa3b0);line-height:1.75;max-height:400px;overflow-y:auto;">
${latest.brief_text || 'Brief text not available.'}
          </div>
          <p style="font-size:.7rem;color:var(--text-3,#566070);margin-top:10px;">
            Generated ${new Date(latest.created_at).toLocaleDateString()}
          </p>
          <button onclick="generateFreshDoctorPrep()" style="margin-top:12px;padding:9px 18px;
            background:var(--signal,#00d4c8);color:#0a0b0e;border:none;border-radius:8px;
            font-size:.8rem;font-weight:700;cursor:pointer;">Generate Fresh Brief</button>
        `;
        return;
      }
    }

    // No existing brief — generate one now via /api/doctor-brief
    const briefRes = await fetch(_API + '/api/doctor-brief', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + s.access_token },
      body:    JSON.stringify({ symptoms: [], medications: [], notes: '' }),
    });

    if (!briefRes.ok) {
      const err = await briefRes.json().catch(() => ({}));
      throw new Error(err.error || 'Could not generate brief. Upload a lab report first.');
    }

    const briefData = await briefRes.json();
    const briefText = briefData.brief || 'No brief content returned.';

    contentEl.innerHTML = `
      <div style="background:var(--surface-2,#1a1d24);border-radius:10px;padding:16px;
        white-space:pre-wrap;font-family:var(--sans,'sans-serif');font-size:.82rem;
        color:var(--text,#f0f2f5);line-height:1.75;max-height:420px;overflow-y:auto;">
${briefText}
      </div>
      <div style="display:flex;gap:8px;margin-top:12px;">
        <button onclick="navigator.clipboard&&navigator.clipboard.writeText(document.querySelector('#doctorPrepContent pre, #doctorPrepContent div div').innerText);toast&&toast('Copied ✓')"
          style="padding:9px 18px;background:var(--signal,#00d4c8);color:#0a0b0e;border:none;
            border-radius:8px;font-size:.8rem;font-weight:700;cursor:pointer;">
          Copy Brief
        </button>
        <button onclick="generateFreshDoctorPrep()"
          style="padding:9px 18px;background:var(--surface-3,#23272f);color:var(--text-2,#9aa3b0);
            border:1px solid var(--border,rgba(255,255,255,.07));border-radius:8px;
            font-size:.8rem;cursor:pointer;">
          Regenerate
        </button>
      </div>
    `;
  } catch(e) {
    contentEl.innerHTML = `
      <div style="color:var(--danger,#f87171);font-size:.82rem;padding:12px 0;">
        ${e.message}
      </div>
      <p style="font-size:.76rem;color:var(--text-3,#566070);margin-top:8px;">
        Upload a lab report first, then generate your doctor brief.
      </p>
    `;
  }
}

async function generateFreshDoctorPrep() {
  const contentEl = document.getElementById('doctorPrepContent');
  if (!contentEl) return;

  contentEl.innerHTML = `<div style="display:flex;align-items:center;gap:8px;padding:20px 0;color:var(--signal,#00d4c8);">
    <i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i>
    Generating fresh brief…
  </div>`;

  try {
    const s = await (typeof session === 'function' ? session() : Promise.resolve(null));
    if (!s?.access_token) throw new Error('Session expired.');

    const res = await fetch(_API + '/api/doctor-brief', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + s.access_token },
      body:    JSON.stringify({ symptoms: [], medications: [], notes: '' }),
    });

    if (!res.ok) throw new Error('Brief generation failed.');
    const data = await res.json();

    contentEl.innerHTML = `
      <div style="background:var(--surface-2,#1a1d24);border-radius:10px;padding:16px;
        white-space:pre-wrap;font-family:var(--sans,'sans-serif');font-size:.82rem;
        color:var(--text,#f0f2f5);line-height:1.75;max-height:420px;overflow-y:auto;">
${data.brief || 'No content returned.'}
      </div>
      <button onclick="navigator.clipboard&&navigator.clipboard.writeText(document.querySelector('#doctorPrepContent div div').innerText);toast&&toast('Copied ✓')"
        style="margin-top:12px;padding:9px 18px;background:var(--signal,#00d4c8);color:#0a0b0e;
          border:none;border-radius:8px;font-size:.8rem;font-weight:700;cursor:pointer;">
        Copy Brief
      </button>
    `;
  } catch(e) {
    contentEl.innerHTML = `<div style="color:var(--danger,#f87171);font-size:.82rem;">${e.message}</div>`;
  }
}

window.openDoctorPrepModal = openDoctorPrepModal;

// ── 7. APPOINTMENT PREP UI ───────────────────────────────────────────────────
async function openAppointmentPrepModal() {
  const existing = document.getElementById('apptPrepModal');
  if (existing) existing.remove();

  const modal = document.createElement('div');
  modal.id = 'apptPrepModal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px;animation:fadeIn .2s ease';

  const today     = new Date();
  const nextWeek  = new Date(today.getTime() + 7 * 86400000);
  const defaultDt = nextWeek.toISOString().slice(0, 10);

  modal.innerHTML = `
    <div style="background:var(--surface,#111318);border:1px solid var(--border-2,rgba(255,255,255,.13));
      border-radius:20px;padding:28px 24px;max-width:500px;width:100%;
      max-height:90vh;overflow-y:auto;box-shadow:0 32px 80px rgba(0,0,0,.6);position:relative;">
      <button onclick="document.getElementById('apptPrepModal').remove()"
        style="position:absolute;top:14px;right:14px;width:30px;height:30px;border-radius:8px;
          border:none;background:var(--surface-3,#23272f);color:var(--text-2,#9aa3b0);
          font-size:.85rem;cursor:pointer;display:flex;align-items:center;justify-content:center;">✕</button>

      <h3 style="font-size:1.1rem;font-weight:700;color:var(--text,#f0f2f5);margin-bottom:6px;">📅 Appointment Prep</h3>
      <p style="font-size:.78rem;color:var(--text-3,#566070);margin-bottom:20px;">
        PHI builds a tailored one-page clinical brief 48 hours before your visit.
      </p>

      <div id="apptPrepContent">
        <div style="margin-bottom:14px;">
          <label style="display:block;font-size:.72rem;font-weight:700;color:var(--text-3,#566070);
            text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">Appointment date</label>
          <input type="date" id="apptDate" value="${defaultDt}"
            style="width:100%;padding:10px 12px;background:var(--surface-2,#1a1d24);
              border:1px solid var(--border,rgba(255,255,255,.07));border-radius:8px;
              color:var(--text,#f0f2f5);font-size:.88rem;outline:none;">
        </div>
        <div style="margin-bottom:20px;">
          <label style="display:block;font-size:.72rem;font-weight:700;color:var(--text-3,#566070);
            text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">Specialist type</label>
          <select id="apptSpecialist"
            style="width:100%;padding:10px 12px;background:var(--surface-2,#1a1d24);
              border:1px solid var(--border,rgba(255,255,255,.07));border-radius:8px;
              color:var(--text,#f0f2f5);font-size:.88rem;outline:none;cursor:pointer;">
            <option value="primary care">Primary Care / GP</option>
            <option value="endocrinologist">Endocrinologist</option>
            <option value="cardiologist">Cardiologist</option>
            <option value="obesity medicine">Obesity Medicine</option>
            <option value="nephrologist">Nephrologist</option>
            <option value="other">Other specialist</option>
          </select>
        </div>
        <button onclick="generateAppointmentPrep()" id="apptPrepBtn"
          style="width:100%;padding:13px;background:var(--signal,#00d4c8);color:#0a0b0e;
            border:none;border-radius:10px;font-size:.9rem;font-weight:700;cursor:pointer;">
          Generate My Brief
        </button>
      </div>
    </div>
  `;

  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);
}

async function generateAppointmentPrep() {
  const btn       = document.getElementById('apptPrepBtn');
  const date      = document.getElementById('apptDate')?.value;
  const specialist = document.getElementById('apptSpecialist')?.value || 'primary care';

  if (!date) { if (typeof toast === 'function') toast('Please select an appointment date.', 'info'); return; }

  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i> Generating…'; }

  try {
    const s = await (typeof session === 'function' ? session() : Promise.resolve(null));
    if (!s?.access_token) throw new Error('Session expired.');

    const res = await fetch(_API + '/api/appointment-prep', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + s.access_token },
      body:    JSON.stringify({ appointment_date: date, specialist_type: specialist }),
    });

    if (!res.ok) throw new Error('Could not generate prep brief.');
    const data = await res.json();
    const prep = data.prep || {};
    const text = prep.formatted || 'No content returned.';

    const contentEl = document.getElementById('apptPrepContent');
    if (!contentEl) return;

    contentEl.innerHTML = `
      <div style="background:var(--surface-2,#1a1d24);border-radius:10px;padding:16px;
        white-space:pre-wrap;font-family:var(--sans,'sans-serif');font-size:.8rem;
        color:var(--text,#f0f2f5);line-height:1.75;max-height:420px;overflow-y:auto;margin-bottom:12px;">
${text}
      </div>
      <div style="display:flex;gap:8px;">
        <button onclick="navigator.clipboard&&navigator.clipboard.writeText(document.querySelector('#apptPrepContent div').innerText);toast&&toast('Copied ✓')"
          style="flex:1;padding:10px;background:var(--signal,#00d4c8);color:#0a0b0e;border:none;
            border-radius:8px;font-size:.82rem;font-weight:700;cursor:pointer;">
          Copy Brief
        </button>
        <button onclick="openAppointmentPrepModal()"
          style="flex:1;padding:10px;background:var(--surface-3,#23272f);color:var(--text-2,#9aa3b0);
            border:1px solid var(--border,rgba(255,255,255,.07));border-radius:8px;
            font-size:.82rem;cursor:pointer;">
          New Date
        </button>
      </div>
    `;
  } catch(e) {
    if (btn) { btn.disabled = false; btn.innerHTML = 'Generate My Brief'; }
    if (typeof toast === 'function') toast(e.message, 'err');
  }
}

window.openAppointmentPrepModal = openAppointmentPrepModal;

// ── 8. PA ARCHITECT SURFACE ───────────────────────────────────────────────────
async function openPAArchitectModal() {
  const existing = document.getElementById('paModal');
  if (existing) existing.remove();

  const modal = document.createElement('div');
  modal.id = 'paModal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px;animation:fadeIn .2s ease';

  modal.innerHTML = `
    <div style="background:var(--surface,#111318);border:1px solid var(--border-2,rgba(255,255,255,.13));
      border-radius:20px;padding:28px 24px;max-width:600px;width:100%;
      max-height:90vh;overflow-y:auto;box-shadow:0 32px 80px rgba(0,0,0,.6);position:relative;">
      <button onclick="document.getElementById('paModal').remove()"
        style="position:absolute;top:14px;right:14px;width:30px;height:30px;border-radius:8px;
          border:none;background:var(--surface-3,#23272f);color:var(--text-2,#9aa3b0);
          font-size:.85rem;cursor:pointer;display:flex;align-items:center;justify-content:center;">✕</button>

      <h3 style="font-size:1.1rem;font-weight:700;color:var(--text,#f0f2f5);margin-bottom:6px;">
        🛡 Insurance PA Architect
      </h3>
      <p style="font-size:.78rem;color:var(--text-3,#566070);margin-bottom:20px;">
        PHI builds a prior authorization support packet from your actual stored lab data.
        Share this with your provider.
      </p>

      <div style="margin-bottom:14px;">
        <label style="display:block;font-size:.72rem;font-weight:700;color:var(--text-3,#566070);
          text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">Medication</label>
        <select id="paMedication"
          style="width:100%;padding:10px 12px;background:var(--surface-2,#1a1d24);
            border:1px solid var(--border,rgba(255,255,255,.07));border-radius:8px;
            color:var(--text,#f0f2f5);font-size:.88rem;outline:none;cursor:pointer;">
          <option value="GLP-1">GLP-1 (general)</option>
          <option value="Wegovy (semaglutide)">Wegovy (semaglutide)</option>
          <option value="Zepbound (tirzepatide)">Zepbound (tirzepatide)</option>
          <option value="Ozempic (semaglutide)">Ozempic (semaglutide)</option>
          <option value="Mounjaro (tirzepatide)">Mounjaro (tirzepatide)</option>
        </select>
      </div>

      <button onclick="generatePAPacket()" id="paBtn"
        style="width:100%;padding:13px;background:var(--signal,#00d4c8);color:#0a0b0e;
          border:none;border-radius:10px;font-size:.9rem;font-weight:700;cursor:pointer;margin-bottom:14px;">
        Build PA Packet from My Lab Data
      </button>

      <div id="paContent"></div>

      <p style="font-size:.68rem;color:var(--text-3,#566070);margin-top:10px;line-height:1.6;">
        ⚕️ This is an informational support document generated from your stored health data.
        Share with your healthcare provider — they make all clinical decisions.
      </p>
    </div>
  `;

  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);
}

async function generatePAPacket() {
  const btn      = document.getElementById('paBtn');
  const med      = document.getElementById('paMedication')?.value || 'GLP-1';
  const content  = document.getElementById('paContent');

  if (btn)     { btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i> Building packet from your labs…'; }
  if (content) content.innerHTML = '';

  try {
    const s = await (typeof session === 'function' ? session() : Promise.resolve(null));
    if (!s?.access_token) throw new Error('Session expired.');

    const res = await fetch(`${_API}/api/advocacy?medication=${encodeURIComponent(med)}&raw=false`, {
      headers: { 'Authorization': 'Bearer ' + s.access_token }
    });

    if (res.status === 403) {
      throw new Error('AI processing consent required. Go to Settings to enable it.');
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Could not generate PA packet. Upload lab reports first.');
    }

    const data = await res.json();

    const strength = data.evidence_strength || 'moderate';
    const strengthColor = { strong: '#4ade80', moderate: '#fbbf24', limited: '#f87171' }[strength] || '#fbbf24';
    const packet   = data.pa_packet || 'No packet content returned.';
    const missing  = (data.missing_data || []).slice(0, 4);
    const steps    = (data.next_steps || []).slice(0, 4);

    if (content) content.innerHTML = `
      <div style="background:rgba(0,0,0,.2);border-radius:8px;padding:10px 12px;margin-bottom:12px;
        display:flex;align-items:center;gap:10px;">
        <div style="font-size:.72rem;color:var(--text-3,#566070);text-transform:uppercase;letter-spacing:.06em;">Evidence strength</div>
        <div style="font-size:.88rem;font-weight:700;color:${strengthColor};">${strength.toUpperCase()}</div>
      </div>

      ${missing.length ? `
        <div style="margin-bottom:12px;">
          <div style="font-size:.7rem;font-weight:700;color:var(--amber,#fbbf24);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">
            ⚠ Would strengthen your case
          </div>
          ${missing.map(m => `<div style="font-size:.78rem;color:var(--text-2,#9aa3b0);padding:4px 0;border-bottom:1px solid rgba(255,255,255,.04);">• ${m}</div>`).join('')}
        </div>` : ''}

      <div style="background:var(--surface-2,#1a1d24);border-radius:10px;padding:14px;
        white-space:pre-wrap;font-size:.75rem;color:var(--text-2,#9aa3b0);
        line-height:1.75;max-height:380px;overflow-y:auto;margin-bottom:12px;
        font-family:monospace;">${packet}</div>

      ${steps.length ? `
        <div style="margin-bottom:12px;">
          <div style="font-size:.7rem;font-weight:700;color:var(--signal,#00d4c8);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">
            Next steps
          </div>
          ${steps.map((s, i) => `<div style="font-size:.78rem;color:var(--text-2,#9aa3b0);padding:4px 0;">${i+1}. ${s}</div>`).join('')}
        </div>` : ''}

      <button onclick="navigator.clipboard&&navigator.clipboard.writeText(document.querySelector('#paContent div[style*=monospace]').innerText);toast&&toast('PA packet copied ✓')"
        style="width:100%;padding:10px;background:var(--signal,#00d4c8);color:#0a0b0e;
          border:none;border-radius:8px;font-size:.84rem;font-weight:700;cursor:pointer;">
        Copy PA Packet for Provider
      </button>
    `;
  } catch(e) {
    if (content) content.innerHTML = `<div style="color:var(--danger,#f87171);font-size:.82rem;">${e.message}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = 'Build PA Packet from My Lab Data'; }
  }
}

window.openPAArchitectModal = openPAArchitectModal;

// ── 9. INJECT COCKPIT QUICK-ACTIONS ──────────────────────────────────────────
function injectCockpitQuickActions() {
  const cockpit = document.getElementById('cockpit');
  if (!cockpit || document.getElementById('quickActionsSection')) return;

  const section = document.createElement('section');
  section.className = 'cp-section';
  section.id = 'quickActionsSection';
  section.innerHTML = `
    <div class="cp-section-hd">
      <h2 class="cp-section-title">Quick Actions</h2>
    </div>
    <div style="display:flex;flex-direction:column;gap:7px;">
      <button onclick="openDoctorPrepModal()"
        style="width:100%;min-height:44px;padding:0 14px;display:flex;align-items:center;gap:9px;
          background:var(--surface-2,#1a1d24);border:1px solid var(--border,rgba(255,255,255,.07));
          border-radius:8px;color:var(--text-2,#9aa3b0);font-size:.82rem;cursor:pointer;
          font-family:var(--sans,'sans-serif');font-weight:500;transition:all .15s;"
        onmouseover="this.style.borderColor='var(--signal,#00d4c8)';this.style.color='var(--signal,#00d4c8)'"
        onmouseout="this.style.borderColor='var(--border,rgba(255,255,255,.07))';this.style.color='var(--text-2,#9aa3b0)'">
        <i class="fa-solid fa-stethoscope" style="width:16px;text-align:center;"></i>
        Doctor Visit Brief
      </button>
      <button onclick="openAppointmentPrepModal()"
        style="width:100%;min-height:44px;padding:0 14px;display:flex;align-items:center;gap:9px;
          background:var(--surface-2,#1a1d24);border:1px solid var(--border,rgba(255,255,255,.07));
          border-radius:8px;color:var(--text-2,#9aa3b0);font-size:.82rem;cursor:pointer;
          font-family:var(--sans,'sans-serif');font-weight:500;transition:all .15s;"
        onmouseover="this.style.borderColor='var(--signal,#00d4c8)';this.style.color='var(--signal,#00d4c8)'"
        onmouseout="this.style.borderColor='var(--border,rgba(255,255,255,.07))';this.style.color='var(--text-2,#9aa3b0)'">
        <i class="fa-solid fa-calendar-check" style="width:16px;text-align:center;"></i>
        Appointment Prep
      </button>
      <button onclick="openPAArchitectModal()"
        style="width:100%;min-height:44px;padding:0 14px;display:flex;align-items:center;gap:9px;
          background:var(--surface-2,#1a1d24);border:1px solid var(--border,rgba(255,255,255,.07));
          border-radius:8px;color:var(--text-2,#9aa3b0);font-size:.82rem;cursor:pointer;
          font-family:var(--sans,'sans-serif');font-weight:500;transition:all .15s;"
        onmouseover="this.style.borderColor='var(--signal,#00d4c8)';this.style.color='var(--signal,#00d4c8)'"
        onmouseout="this.style.borderColor='var(--border,rgba(255,255,255,.07))';this.style.color='var(--text-2,#9aa3b0)'">
        <i class="fa-solid fa-shield-halved" style="width:16px;text-align:center;"></i>
        Insurance PA Architect
      </button>
    </div>
  `;

  // Insert as the first section after the close button
  const closeBtn = document.getElementById('cockpitCloseBtn');
  if (closeBtn && closeBtn.nextSibling) {
    cockpit.insertBefore(section, closeBtn.nextSibling);
  } else {
    const firstSection = cockpit.querySelector('.cp-section');
    if (firstSection) cockpit.insertBefore(section, firstSection);
    else cockpit.appendChild(section);
  }
}

// ── 10. INIT ──────────────────────────────────────────────────────────────────
function initAppFixes() {
  injectUpgradeButton();
  injectCockpitQuickActions();

  window.addEventListener('phi:authed', () => {
    setTimeout(() => refreshPlanDisplay(), 800);
  });
  setTimeout(() => refreshPlanDisplay(), 2000);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAppFixes);
} else {
  setTimeout(initAppFixes, 200);
}