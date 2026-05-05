/**
 * payment_patch.js — Curabook PHI
 * 
 * Drop this after script.js in app.html:
 *   <script src="payment_patch.js"></script>
 * 
 * Overrides showUpgradeModal() and initiateRazorpayCheckout()
 * with corrected $49/mo, $180/yr, $99/mo Clinical pricing
 * matching the index.html landing page exactly.
 * 
 * Also:
 *   - Reads /api/payment/config on load to check if Razorpay is configured
 *   - Shows "contact us" fallback if keys not set (graceful degradation)
 *   - Aligns plan names: "Shield Core" and "Shield Clinical"
 */
"use strict";

// ── Override prices (must match payment_routes.py PLAN_PRICING) ──────────────
const PLAN_CONFIG = {
  monthly:  { label: "Shield Core",         price: "$49/mo",  desc: "Unlimited reports + full health memory" },
  annual:   { label: "Shield Core Annual",  price: "$15/mo",  desc: "Billed $180/yr — save 32% vs monthly" },
  clinical: { label: "Shield Clinical",     price: "$99/mo",  desc: "Everything + PA support + body composition" },
};

// ── Check payment config on load ──────────────────────────────────────────────
let _rzpConfigured = false;
let _rzpPublicKey  = "";

async function _loadPaymentConfig() {
  try {
    const h = await headers();
    const res = await fetch(API + "/api/payment/config", h ? { headers: h } : {});
    if (res.ok) {
      const d = await res.json();
      _rzpConfigured = d.razorpay_configured === true;
      _rzpPublicKey  = d.razorpay_key_id || "";
    }
  } catch (e) {
    _rzpConfigured = false;
  }
}

// Load on boot
document.addEventListener("DOMContentLoaded", () => {
  _loadPaymentConfig().catch(() => {});
});

// ── Override showUpgradeModal ─────────────────────────────────────────────────
window.showUpgradeModal = function(reason = "manual") {
  const existing = document.getElementById("upgradeModal");
  if (existing) existing.remove();

  const modal = document.createElement("div");
  modal.id = "upgradeModal";
  modal.style.cssText = `
    position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:9999;
    display:flex;align-items:center;justify-content:center;padding:20px;
    animation:fadeIn .2s ease;
  `;

  const notConfigured = !_rzpConfigured;

  modal.innerHTML = `
    <div style="background:var(--surface);border:1px solid var(--border-2);border-radius:20px;
      padding:32px;max-width:500px;width:100%;box-shadow:0 24px 60px rgba(0,0,0,.5);
      animation:slideUp .25s ease;position:relative;max-height:90vh;overflow-y:auto;">
      <button onclick="closeUpgradeModal()" style="position:absolute;top:16px;right:16px;
        color:var(--text-3);font-size:1.2rem;background:none;border:none;cursor:pointer;
        width:32px;height:32px;display:flex;align-items:center;justify-content:center;
        border-radius:8px;transition:all .15s;"
        onmouseover="this.style.background='var(--surface-2)'"
        onmouseout="this.style.background='none'">✕</button>

      <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px;">
        <div style="width:44px;height:44px;background:linear-gradient(135deg,var(--signal),var(--signal-2));
          border-radius:12px;display:flex;align-items:center;justify-content:center;
          font-family:var(--serif);font-size:1.2rem;color:#0a0b0e;font-weight:600;
          box-shadow:0 4px 16px var(--signal-glow);flex-shrink:0;">φ</div>
        <div>
          <h2 style="font-family:var(--serif);font-size:1.3rem;font-weight:400;margin-bottom:2px;">
            Upgrade Your Metabolic Shield
          </h2>
          <p style="font-size:.75rem;color:var(--text-3);">Unlock unlimited reports, full health memory &amp; more</p>
        </div>
      </div>

      ${reason === "upload" ? `
        <div style="background:var(--amber-dim);border:1px solid rgba(251,191,36,.3);
          border-radius:10px;padding:11px 14px;margin-bottom:18px;font-size:.81rem;color:var(--amber);
          display:flex;align-items:center;gap:8px;">
          <i class="fa-solid fa-triangle-exclamation"></i>
          <span><strong>Free tier limit reached</strong> — You've used your 1 free report upload.</span>
        </div>` : ""}

      <!-- Plan grid: Free / Shield Core / Shield Clinical -->
      <div style="display:grid;grid-template-columns:1fr 1.05fr 1fr;gap:8px;margin-bottom:18px;">

        <!-- Free -->
        <div style="background:var(--surface-2);border:1px solid var(--border);border-radius:12px;padding:14px;">
          <div style="font-size:.62rem;font-weight:700;color:var(--text-3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Free</div>
          <div style="font-family:var(--mono);font-size:1.4rem;font-weight:500;color:var(--text-2);margin-bottom:10px;">$0</div>
          <div style="font-size:.73rem;color:var(--text-3);line-height:1.8;">
            ✓ Unlimited chat<br>
            <span style="opacity:.5">✗ 1 report only</span><br>
            <span style="opacity:.5">✗ No marker memory</span>
          </div>
        </div>

        <!-- Shield Core (featured) -->
        <div style="background:rgba(0,212,200,.06);border:1.5px solid var(--signal);border-radius:12px;padding:14px;position:relative;">
          <div style="position:absolute;top:-10px;left:50%;transform:translateX(-50%);
            background:var(--signal);color:#0a0b0e;font-size:.58rem;font-weight:700;
            padding:2px 10px;border-radius:20px;letter-spacing:.08em;white-space:nowrap;">POPULAR</div>
          <div style="font-size:.62rem;font-weight:700;color:var(--signal);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Shield Core</div>
          <div style="font-family:var(--mono);font-size:1.4rem;font-weight:500;color:var(--signal);margin-bottom:2px;">$49<span style="font-size:.7rem;color:var(--text-3)">/mo</span></div>
          <div style="font-size:.65rem;color:var(--text-3);margin-bottom:10px;">or $180/yr — save 32%</div>
          <div style="font-size:.73rem;color:var(--text-2);line-height:1.8;">
            ✓ Unlimited reports<br>
            ✓ Full health memory<br>
            ✓ Cliff alerts + PA<br>
            ✓ Doctor prep briefs
          </div>
        </div>

        <!-- Shield Clinical -->
        <div style="background:var(--surface-2);border:1px solid var(--border);border-radius:12px;padding:14px;">
          <div style="font-size:.62rem;font-weight:700;color:var(--text-3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Clinical</div>
          <div style="font-family:var(--mono);font-size:1.4rem;font-weight:500;color:var(--text-2);margin-bottom:10px;">$99<span style="font-size:.7rem;color:var(--text-3)">/mo</span></div>
          <div style="font-size:.73rem;color:var(--text-3);line-height:1.8;">
            ✓ Everything in Core<br>
            ✓ PA builder<br>
            ✓ Body composition<br>
            ✓ Correlation engine
          </div>
        </div>
      </div>

      ${notConfigured ? `
        <div style="background:var(--danger-dim);border:1px solid rgba(248,113,113,.3);
          border-radius:10px;padding:12px 14px;margin-bottom:12px;font-size:.8rem;color:var(--danger);">
          <strong>Payments not configured yet.</strong><br>
          Add <code>RAZORPAY_KEY_ID</code> and <code>RAZORPAY_KEY_SECRET</code> to your .env to enable upgrades.
        </div>
        <a href="mailto:support@curabook.com" style="display:block;text-align:center;
          padding:12px;background:var(--signal);color:#0a0b0e;border-radius:10px;
          font-size:.88rem;font-weight:700;text-decoration:none;">
          Contact us to upgrade →
        </a>
      ` : `
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:8px;">
          <button id="rzpMonthlyBtn"  onclick="initiateRazorpayCheckout('monthly')"  style="${_btnStyle('signal')}">$49/mo</button>
          <button id="rzpAnnualBtn"   onclick="initiateRazorpayCheckout('annual')"   style="${_btnStyle('surface')}">$180/yr</button>
          <button id="rzpClinicalBtn" onclick="initiateRazorpayCheckout('clinical')" style="${_btnStyle('surface')}">$99/mo</button>
        </div>
        <p style="font-size:.67rem;color:var(--text-3);text-align:center;margin-top:6px;">
          Secure via Razorpay · No card stored · Cancel anytime
        </p>
      `}
    </div>
  `;
  modal.addEventListener("click", e => { if (e.target === modal) closeUpgradeModal(); });
  document.body.appendChild(modal);
};

function _btnStyle(type) {
  if (type === 'signal') {
    return `padding:12px;background:var(--signal);color:#0a0b0e;border:none;border-radius:10px;
      font-size:.84rem;font-weight:700;cursor:pointer;font-family:var(--sans);
      box-shadow:0 3px 12px var(--signal-glow);transition:all .15s;`;
  }
  return `padding:12px;background:var(--surface-2);color:var(--text);
    border:1.5px solid var(--border-2);border-radius:10px;
    font-size:.84rem;font-weight:600;cursor:pointer;font-family:var(--sans);transition:all .15s;`;
}

// ── Override initiateRazorpayCheckout ─────────────────────────────────────────
window.initiateRazorpayCheckout = async function(plan = "monthly") {
  const btnMap = { monthly: "rzpMonthlyBtn", annual: "rzpAnnualBtn", clinical: "rzpClinicalBtn" };
  const btnId = btnMap[plan] || "rzpMonthlyBtn";
  const btn = document.getElementById(btnId);
  const planCfg = PLAN_CONFIG[plan] || PLAN_CONFIG.monthly;
  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner" style="animation:spin .7s linear infinite"></i>'; }

  const h = await headers();
  if (!h) { toast("Please sign in to upgrade.", "err"); _resetBtn(btn, planCfg.price); return; }

  try {
    const res = await fetch(API + "/api/payment/razorpay/order", {
      method: "POST",
      headers: h,
      body: JSON.stringify({ plan })
    });
    const data = await res.json();

    if (!res.ok || data.error) {
      toast(data.error || "Payment setup failed.", "err");
      _resetBtn(btn, planCfg.price);
      return;
    }

    // Load Razorpay checkout script
    if (typeof Razorpay === "undefined") {
      await new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = "https://checkout.razorpay.com/v1/checkout.js";
        s.onload  = resolve;
        s.onerror = () => reject(new Error("Razorpay script failed"));
        document.head.appendChild(s);
      });
    }

    const options = {
      key:         data.razorpay_key_id,
      amount:      data.amount,
      currency:    data.currency || "USD",
      name:        "Curabook PHI",
      description: data.description || planCfg.desc,
      order_id:    data.order_id,
      subscription_id: data.subscription_id,
      handler: async function(response) {
        try {
          const verifyH = await headers();
          if (!verifyH) return;
          const vRes = await fetch(API + "/api/payment/razorpay/verify", {
            method: "POST",
            headers: verifyH,
            body: JSON.stringify({
              order_id:        data.order_id || "",
              payment_id:      response.razorpay_payment_id,
              signature:       response.razorpay_signature,
              subscription_id: data.subscription_id || response.razorpay_subscription_id || "",
              plan,
            })
          });
          const vData = await vRes.json();
          if (vRes.ok && vData.success) {
            _userPlan         = plan;
            _reportsRemaining = 9999;
            _renderPlanBadge();
            toast(`🎉 ${vData.message || "Welcome to PHI " + planCfg.label + "!"}`, "ok");
            closeUpgradeModal();
            setTimeout(() => location.reload(), 2000);
          } else {
            toast(vData.error || "Payment verification failed. Contact support@curabook.com", "err");
          }
        } catch (e) {
          toast("Verification error. Please email support@curabook.com", "err");
        }
        _resetBtn(btn, planCfg.price);
      },
      prefill: { email: typeof _user !== "undefined" && _user ? _user.email : "" },
      theme:   { color: "#00d4c8" },
      modal:   {
        ondismiss: () => {
          _resetBtn(btn, planCfg.price);
          toast("Payment cancelled.", "info");
        }
      }
    };

    const rzp = new Razorpay(options);
    rzp.open();

  } catch (e) {
    console.error("[RAZORPAY]", e);
    toast("Payment unavailable. Please try again.", "err");
    _resetBtn(btn, planCfg.price);
  }
};

function _resetBtn(btn, label) {
  if (btn) { btn.disabled = false; btn.innerHTML = label; }
}