/**
 * phi_app_fixes.js — Curabook PHI App Patches
 * 
 * Fixes applied:
 * 1. Welcome modal on first login (?welcome=1 or ?welcome=paid)
 * 2. Proper upgrade button in sidebar
 * 3. Trial expiry display + countdown
 * 4. Onboarding memory shown in cockpit
 * 5. Plan badge colors and upgrade CTA
 * 6. Google OAuth users who need onboarding — redirect handled
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
      <h2 style="font-family:var(--serif,'Georgia');font-size:1.6rem;font-weight:400;margin-bottom:10px;color:var(--ink,#0d0f12)">
        ${isPaid ? 'Welcome to Shield Core!' : "Welcome to Curabook PHI!"}
      </h2>
      <p style="font-size:.88rem;color:var(--ink-3,#888);line-height:1.7;margin-bottom:24px">
        ${isPaid 
          ? "Your plan is active. Upload your first lab report to unlock your Metabolic Shield™ score and start monitoring for GLP-1 rebound."
          : "PHI is ready. Upload your first lab report — tap the paperclip or the button below — and PHI will build your cliff risk picture in seconds."}
      </p>
      <button onclick="document.getElementById('welcomeModal').remove();handleUploadClick()" 
        style="width:100%;padding:13px;background:var(--signal,#00d4c8);color:#0a0b0e;border:none;
          border-radius:10px;font-size:.92rem;font-weight:700;cursor:pointer;font-family:var(--sans,'sans-serif');
          box-shadow:0 4px 16px rgba(0,212,200,.3);margin-bottom:10px">
        📎 Upload First Lab Report
      </button>
      <button onclick="document.getElementById('welcomeModal').remove()"
        style="width:100%;padding:11px;background:none;border:1px solid var(--border,rgba(0,0,0,.08));
          border-radius:10px;font-size:.84rem;color:var(--ink-3,#888);cursor:pointer;font-family:var(--sans,'sans-serif')">
        Explore first, upload later
      </button>
    </div>
  `;
  document.body.appendChild(modal);
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
}

// Check URL for welcome param
(function checkWelcomeParam() {
  const params = new URLSearchParams(window.location.search);
  const welcome = params.get('welcome');
  if (welcome) {
    // Clean URL
    const cleanUrl = window.location.pathname;
    window.history.replaceState({}, '', cleanUrl);
    // Show after slight delay so app loads
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
  btn.onclick = () => { closeSidebar?.(); showUpgradeModal('manual'); };
  sbFooter.insertBefore(btn, sbFooter.firstChild);
}

// ── 3. PLAN BADGE & TRIAL DISPLAY ─────────────────────────────────────────────
async function refreshPlanDisplay() {
  try {
    const s = await session?.();
    if (!s?.access_token) return;
    
    const res = await fetch(_API + '/api/payment/status', {
      headers: { 'Authorization': 'Bearer ' + s.access_token }
    });
    if (!res.ok) return;
    const data = await res.json();

    // Update plan badge
    const planEl = document.getElementById('userPlan');
    if (planEl) {
      const isPro = data.is_pro;
      const plan = data.plan || 'free';
      let planLabel = 'PHI Free';
      if (plan === 'monthly') planLabel = 'Shield $49/mo ✦';
      else if (plan === 'annual') planLabel = 'Shield Annual ✦';
      else if (plan === 'clinical') planLabel = 'Shield Clinical ✦';
      else if (plan === 'trial') planLabel = 'Trial Active';
      planEl.textContent = planLabel;
      planEl.style.color = isPro ? 'var(--signal,#00d4c8)' : 'var(--text-3,#566070)';
    }

    // Show trial countdown
    if (data.plan === 'trial' && data.subscription_end_date) {
      showTrialBanner(data.subscription_end_date);
    }

    // Hide upgrade button if already pro
    const upgradeBtn = document.getElementById('upgradeNavBtn');
    if (upgradeBtn) upgradeBtn.style.display = data.is_pro ? 'none' : '';

    // Update internal state
    if (typeof _userPlan !== 'undefined') {
      window._userPlan = data.plan || 'free';
      window._reportsRemaining = data.reports_remaining ?? 1;
    }

  } catch (e) {
    console.warn('[PLAN] Status refresh error:', e);
  }
}

function showTrialBanner(endDate) {
  if (document.getElementById('trialBanner')) return;
  try {
    const end = new Date(endDate);
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
    // Shift topbar down
    const topbar = document.getElementById('topbar');
    if (topbar) topbar.style.marginTop = banner.offsetHeight + 'px';
  } catch (e) {}
}

// ── 4. IN-APP UPGRADE MODAL ───────────────────────────────────────────────────
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
    ['fa-bell',          'Weekly health briefs'],
    ['fa-chart-line',    'Trend tracking'],
    ['fa-stethoscope',   'Doctor visit prep'],
  ];

  modal.innerHTML = `
    <div style="
      background:var(--surface,#111318);
      border:1px solid var(--border-2,rgba(255,255,255,.13));
      border-radius:20px;
      padding:28px 24px 24px;
      max-width:420px;width:100%;
      box-shadow:0 32px 80px rgba(0,0,0,.6);
      position:relative;overflow:hidden;
    ">
      <!-- top accent -->
      <div style="position:absolute;top:0;left:0;right:0;height:3px;
        background:linear-gradient(90deg,var(--signal,#00d4c8),#00a89e);"></div>

      <!-- close -->
      <button onclick="document.getElementById('upgradeModal').remove()" style="
        position:absolute;top:14px;right:14px;
        width:30px;height:30px;border-radius:8px;
        border:none;background:var(--surface-3,#23272f);
        color:var(--text-2,#9aa3b0);font-size:.85rem;
        cursor:pointer;display:flex;align-items:center;justify-content:center;
      ">✕</button>

      <!-- header -->
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:18px;padding-right:36px;">
        <div style="width:42px;height:42px;flex-shrink:0;
          background:linear-gradient(135deg,var(--signal,#00d4c8),#00a89e);
          border-radius:12px;display:flex;align-items:center;justify-content:center;
          font-family:Georgia,serif;font-size:1.15rem;color:#0a0b0e;font-weight:600;
          box-shadow:0 4px 16px rgba(0,212,200,.3);">φ</div>
        <div>
          <h3 style="font-size:1.1rem;font-weight:700;color:var(--text,#f0f2f5);margin-bottom:3px;line-height:1.2;">
            Upgrade to PHI Pro
          </h3>
          <p style="font-size:.73rem;color:var(--text-3,#566070);line-height:1.4;">
            Unlimited reports · Health memory · Insurance PA support
          </p>
        </div>
      </div>

      <!-- upload limit banner -->
      ${reason === 'upload' ? `
        <div style="background:rgba(251,191,36,.12);border:1px solid rgba(251,191,36,.3);
          border-radius:10px;padding:10px 13px;margin-bottom:16px;
          font-size:.8rem;color:#fbbf24;display:flex;align-items:center;gap:8px;">
          <span style="flex-shrink:0;">⚠</span>
          <span><strong>Free limit reached</strong> — upgrade to upload unlimited reports.</span>
        </div>` : ''}

      <!-- feature grid -->
      <div style="
        background:var(--surface-2,#1a1d24);
        border:1px solid var(--border,rgba(255,255,255,.07));
        border-radius:12px;padding:14px 16px;margin-bottom:18px;
      ">
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

      <!-- plan buttons — vertical so annual is always visible -->
      <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:14px;">

        <!-- Monthly -->
        <button id="upgMonthlyBtn" onclick="initUpgrade('monthly')" style="
          width:100%;padding:13px 16px;
          background:var(--signal,#00d4c8);color:#0a0b0e;
          border:none;border-radius:12px;
          font-size:.9rem;font-weight:700;cursor:pointer;font-family:inherit;
          box-shadow:0 4px 20px rgba(0,212,200,.35);transition:all .15s;
          display:flex;align-items:center;justify-content:space-between;
        ">
          <span>Shield — Monthly</span>
          <span style="font-size:1rem;letter-spacing:-.01em;">$49<span style="font-size:.72rem;font-weight:500;">/mo</span></span>
        </button>

        <!-- Annual — uses explicit opaque colors, never invisible in any theme -->
        <button id="upgAnnualBtn" onclick="initUpgrade('annual')" style="
          width:100%;padding:13px 16px;
          background:var(--surface-3,#23272f);
          color:var(--text,#f0f2f5);
          border:2px solid var(--border-2,rgba(255,255,255,.13));
          border-radius:12px;
          font-size:.9rem;font-weight:600;cursor:pointer;font-family:inherit;
          transition:all .15s;
          display:flex;align-items:center;justify-content:space-between;
        ">
          <span style="display:flex;align-items:center;gap:8px;">
            Shield — Annual
            <span style="font-size:.58rem;font-weight:700;background:#10b981;color:#fff;
              padding:2px 7px;border-radius:20px;letter-spacing:.04em;">SAVE 20%</span>
          </span>
          <span style="font-size:.85rem;letter-spacing:-.01em;color:var(--text,#111111);">
            $39<span style="font-size:.65rem;font-weight:500;color:var(--text-3,#9ca3af);">/mo</span>
            <span style="font-size:.65rem;color:var(--text-3,#9ca3af);margin-left:4px;">· $468/yr</span>
          </span>
        </button>

      </div>

      <div id="upgradeStatus" style="text-align:center;font-size:.78rem;color:var(--text-3,#566070);min-height:18px;margin-bottom:6px;"></div>
      <p style="font-size:.67rem;color:var(--text-3,#9ca3af);text-align:center;">
        🔒 Secure via PayPal · No card stored · Cancel anytime
      </p>
    </div>
  `;

  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);
};

async function initUpgrade(plan) {
  const statusEl = document.getElementById('upgradeStatus');
  const btn      = document.getElementById('upg' + plan.charAt(0).toUpperCase() + plan.slice(1) + 'Btn');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span style="animation:spin .7s linear infinite;display:inline-block;width:14px;height:14px;border:2px solid rgba(0,0,0,.2);border-top-color:#0a0b0e;border-radius:50%;vertical-align:middle;margin-right:6px"></span> Connecting to PayPal…'; }
  if (statusEl) statusEl.textContent = 'Setting up your subscription…';

  try {
    const s = await session?.();
    if (!s?.access_token) throw new Error('Please sign in first.');

    // Step 1 — create PayPal subscription, get approval URL
    const res = await fetch(_API + '/api/payment/paypal/create-subscription', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + s.access_token },
      body:    JSON.stringify({ plan }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || 'Payment setup failed.');
    const data = await res.json();
    if (!data.approve_url) throw new Error('PayPal did not return a checkout URL.');

    // Step 2 — redirect to PayPal hosted checkout
    // PayPal returns user to /payment/success?subscription_id=xxx&plan=xxx
    // That page calls /api/payment/paypal/capture to activate the plan.
    if (statusEl) statusEl.textContent = 'Redirecting to PayPal…';
    window.location.href = data.approve_url;

  } catch (e) {
    if (statusEl) statusEl.textContent = e.message;
    if (btn) {
      btn.disabled = false;
      btn.textContent = plan === 'monthly' ? 'Shield — $49/mo' : 'Shield Annual — $39/mo · $468/yr';
    }
    typeof toast === 'function' && toast(e.message, 'err');
  }
}
window.initUpgrade = initUpgrade;

// ── 5. INIT ALL FIXES ────────────────────────────────────────────────────────
function initAppFixes() {
  injectUpgradeButton();
  // Refresh plan display after auth is ready
  window.addEventListener('phi:authed', () => {
    setTimeout(() => {
      refreshPlanDisplay();
    }, 800);
  });
  // Also try after 2s in case event already fired
  setTimeout(() => refreshPlanDisplay(), 2000);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAppFixes);
} else {
  setTimeout(initAppFixes, 200);
}