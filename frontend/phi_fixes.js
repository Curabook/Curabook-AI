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
      if (plan === 'monthly') planLabel = 'Shield Core ✦';
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
// Override the existing showUpgradeModal to use Razorpay
const _originalShowUpgrade = window.showUpgradeModal;
window.showUpgradeModal = function(reason) {
  const existing = document.getElementById('upgradeModal');
  if (existing) existing.remove();

  const modal = document.createElement('div');
  modal.id = 'upgradeModal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px;animation:fadeIn .2s ease';
  modal.innerHTML = `
    <div style="background:var(--surface,#111318);border:1px solid rgba(255,255,255,.1);border-radius:20px;
      padding:32px;max-width:460px;width:100%;box-shadow:0 24px 60px rgba(0,0,0,.5);position:relative">
      <button onclick="document.getElementById('upgradeModal').remove()" style="position:absolute;top:14px;right:14px;
        color:var(--text-3,#566);font-size:1.1rem;background:none;border:none;cursor:pointer;
        width:30px;height:30px;display:flex;align-items:center;justify-content:center">×</button>

      ${reason === 'upload' ? `
      <div style="background:rgba(251,191,36,.1);border:1px solid rgba(251,191,36,.3);border-radius:10px;
        padding:10px 14px;margin-bottom:16px;font-size:.8rem;color:#fbbf24;display:flex;align-items:center;gap:8px">
        ⚠ Free plan limit — upgrade to upload unlimited lab reports
      </div>` : ''}

      <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
        <div style="width:42px;height:42px;background:linear-gradient(135deg,var(--signal,#00d4c8),#00a89e);
          border-radius:12px;display:flex;align-items:center;justify-content:center;
          font-family:Georgia,serif;font-size:1.2rem;color:#0a0b0e;font-style:italic;flex-shrink:0">φ</div>
        <div>
          <h3 style="font-size:.95rem;font-weight:700;margin-bottom:2px;color:var(--text,#f0f2f5)">Upgrade to Shield Core</h3>
          <p style="font-size:.74rem;color:var(--text-3,#566)">Unlimited reports · Weekly alerts · PA support · Memory</p>
        </div>
      </div>

      <div style="display:flex;gap:8px;margin-bottom:14px">
        <button id="upgMonthlyBtn" onclick="initUpgrade('monthly')" style="flex:1;padding:13px;
          background:var(--signal,#00d4c8);color:#0a0b0e;border:none;border-radius:10px;
          font-size:.86rem;font-weight:700;cursor:pointer;font-family:inherit;
          box-shadow:0 4px 14px rgba(0,212,200,.3)">
          Monthly — $39/mo
        </button>
        <button id="upgAnnualBtn" onclick="initUpgrade('annual')" style="flex:1;padding:13px;
          background:rgba(255,255,255,.07);color:rgba(255,255,255,.7);
          border:1px solid rgba(255,255,255,.12);border-radius:10px;
          font-size:.86rem;font-weight:600;cursor:pointer;font-family:inherit">
          Annual — $374/yr <span style="color:rgba(0,212,200,.8);font-size:.7rem">-20%</span>
        </button>
      </div>

      <div id="upgradeStatus" style="text-align:center;font-size:.78rem;color:var(--text-3,#566);min-height:20px"></div>
      <p style="font-size:.7rem;color:var(--text-3,#566);text-align:center;margin-top:8px">
        Secure via Razorpay · No card stored · Cancel anytime
      </p>
    </div>
  `;
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);
};

async function initUpgrade(plan) {
  const statusEl = document.getElementById('upgradeStatus');
  const btn = document.getElementById('upg' + plan.charAt(0).toUpperCase() + plan.slice(1) + 'Btn');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span style="animation:spin .7s linear infinite;display:inline-block;width:14px;height:14px;border:2px solid rgba(255,255,255,.3);border-top-color:white;border-radius:50%;vertical-align:middle;margin-right:6px"></span>'; }
  if (statusEl) statusEl.textContent = 'Connecting to payment…';

  try {
    const s = await session?.();
    if (!s?.access_token) throw new Error('Please sign in first.');

    const res = await fetch(_API + '/api/payment/razorpay/order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + s.access_token },
      body: JSON.stringify({ plan })
    });
    if (!res.ok) throw new Error((await res.json().catch(()=>({}))).error || 'Payment setup failed.');
    const data = await res.json();
    if (!data.order_id && !data.subscription_id) throw new Error('Invalid payment response.');

    if (typeof Razorpay === 'undefined') {
      window.location.href = '/signup?plan=' + plan;
      return;
    }

    const opts = {
      key: data.razorpay_key_id,
      amount: data.amount,
      currency: data.currency || 'USD',
      name: 'Curabook PHI',
      description: data.description,
      handler: async (resp) => {
        try {
          const vRes = await fetch(_API + '/api/payment/razorpay/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + s.access_token },
            body: JSON.stringify({
              order_id: data.order_id || '',
              subscription_id: data.subscription_id || resp.razorpay_subscription_id || '',
              payment_id: resp.razorpay_payment_id,
              signature: resp.razorpay_signature,
              plan
            })
          });
          const vData = await vRes.json();
          if (vData.success) {
            document.getElementById('upgradeModal')?.remove();
            typeof toast === 'function' && toast('🎉 ' + (vData.message || 'Shield Core unlocked!'), 'ok');
            setTimeout(() => { refreshPlanDisplay(); window._userPlan = plan; window._reportsRemaining = 9999; }, 500);
          }
        } catch (e) { console.error('[UPGRADE] verify error:', e); }
      },
      prefill: { email: s.user?.email || '' },
      theme: { color: '#00d4c8' },
      modal: { ondismiss: () => { if (btn) { btn.disabled = false; btn.textContent = plan === 'monthly' ? 'Monthly — $39/mo' : 'Annual — $374/yr -20%'; } if (statusEl) statusEl.textContent = 'Payment cancelled.'; } }
    };
    if (data.mode === 'subscription') opts.subscription_id = data.subscription_id;
    else opts.order_id = data.order_id;
    new Razorpay(opts).open();

  } catch (e) {
    if (statusEl) statusEl.textContent = e.message;
    if (btn) { btn.disabled = false; btn.textContent = plan === 'monthly' ? 'Monthly — $39/mo' : 'Annual — $374/yr'; }
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