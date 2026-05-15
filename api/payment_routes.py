"""
api/payment_routes.py — PayPal Subscriptions Payment System
═══════════════════════════════════════════════════════════════════════════
Migrated from Razorpay → PayPal Subscriptions API.

PRICING:
  Shield Monthly  — $49/mo            (plan key: "monthly")
  Shield Annual   — $468/yr ($39/mo)  (plan key: "annual")
  Shield Clinical — $99/mo            (plan key: "clinical")
  Trial           — $0                (plan key: "trial")

HOW PAYPAL SUBSCRIPTIONS WORK:
  1. Frontend calls POST /api/payment/paypal/create-subscription
     → Backend creates subscription via PayPal API
     → Returns { subscription_id, approve_url }
  2. Frontend redirects user to approve_url (PayPal hosted checkout)
  3. User approves → PayPal redirects to FRONTEND_URL/payment/success
     with ?subscription_id=xxx
  4. Frontend calls POST /api/payment/paypal/capture with { subscription_id, plan }
     → Backend verifies subscription ACTIVE via PayPal API
     → Upgrades user plan in DB
  5. PayPal sends webhook events for renewals / cancellations
     → POST /api/payment/paypal/webhook

REQUIRED ENV VARS:
  PAYPAL_CLIENT_ID          — from PayPal Developer dashboard
  PAYPAL_CLIENT_SECRET      — from PayPal Developer dashboard
  PAYPAL_WEBHOOK_ID         — from PayPal Developer dashboard
  PAYPAL_PLAN_MONTHLY_ID    — Billing Plan ID P-xxx for $49/mo
  PAYPAL_PLAN_ANNUAL_ID     — Billing Plan ID P-xxx for $468/yr
  PAYPAL_PLAN_CLINICAL_ID   — Billing Plan ID P-xxx for $99/mo
  PAYPAL_ENV                — "sandbox" (default) or "live"
  FRONTEND_URL              — e.g. https://curabook.com
  ADMIN_SECRET              — for admin endpoints
  TRIAL_DAYS                — integer, 0 = no trial

DB MIGRATION NOTE:
  Run /api/payment/setup-tables once after deploying — adds
  paypal_subscription_id column to user_profiles.
═══════════════════════════════════════════════════════════════════════════
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone, timedelta
from flask import Blueprint, request, jsonify

payment_bp = Blueprint("payment", __name__)
logger = logging.getLogger("phi.payment")

# ── Env vars ──────────────────────────────────────────────────────────────────
PAYPAL_CLIENT_ID        = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET    = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_WEBHOOK_ID       = os.getenv("PAYPAL_WEBHOOK_ID", "")
PAYPAL_PLAN_MONTHLY_ID  = os.getenv("PAYPAL_PLAN_MONTHLY_ID", "")
PAYPAL_PLAN_ANNUAL_ID   = os.getenv("PAYPAL_PLAN_ANNUAL_ID", "")
PAYPAL_PLAN_CLINICAL_ID = os.getenv("PAYPAL_PLAN_CLINICAL_ID", "")
PAYPAL_ENV              = os.getenv("PAYPAL_ENV", "sandbox")   # "sandbox" | "live"
FRONTEND_URL            = os.getenv("FRONTEND_URL", "https://curabook.com")
ADMIN_SECRET            = os.getenv("ADMIN_SECRET", "")
CRON_SECRET             = os.getenv("CRON_SECRET", "")
TRIAL_DAYS              = int(os.getenv("TRIAL_DAYS", "0"))

_PAYPAL_BASE = (
    "https://api-m.sandbox.paypal.com"
    if PAYPAL_ENV == "sandbox"
    else "https://api-m.paypal.com"
)

# ── Plan config ───────────────────────────────────────────────────────────────
PLAN_PRICING = {
    "monthly":  {
        "amount":        49.00,
        "currency":      "USD",
        "description":   "Curabook PHI Shield — $49/mo",
        "interval_days": 31,
        "paypal_plan":   PAYPAL_PLAN_MONTHLY_ID,
    },
    "annual":   {
        "amount":        468.00,
        "currency":      "USD",
        "description":   "Curabook PHI Shield — $39/mo billed annually ($468/yr)",
        "interval_days": 365,
        "paypal_plan":   PAYPAL_PLAN_ANNUAL_ID,
    },
    "clinical": {
        "amount":        99.00,
        "currency":      "USD",
        "description":   "Curabook PHI Shield Clinical — $99/mo",
        "interval_days": 31,
        "paypal_plan":   PAYPAL_PLAN_CLINICAL_ID,
    },
    "trial":    {
        "amount":        0,
        "currency":      "USD",
        "description":   f"Curabook PHI Trial — {TRIAL_DAYS} days",
        "interval_days": TRIAL_DAYS,
        "paypal_plan":   "",
    },
}

PLAN_DISPLAY = {
    "free":     "PHI Free",
    "trial":    "PHI Trial",
    "monthly":  "Shield Core",
    "annual":   "Shield Core Annual",
    "clinical": "Shield Clinical",
    "pro":      "Shield Core",
}

_PRO_PLANS = {"monthly", "annual", "clinical", "pro", "trial"}


# ── Shared helpers ────────────────────────────────────────────────────────────

def _deps():
    from app import supabase
    from services.auth import get_authenticated_user
    from services.compliance import audit_log
    return supabase, get_authenticated_user, audit_log


def _is_pro(plan: str) -> bool:
    return (plan or "").lower() in _PRO_PLANS


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── PayPal API ────────────────────────────────────────────────────────────────

def _paypal_access_token() -> str:
    """OAuth2 client credentials token. Valid ~9 hours."""
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        raise RuntimeError("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET must be set in .env")
    resp = requests.post(
        f"{_PAYPAL_BASE}/v1/oauth2/token",
        headers={"Accept": "application/json"},
        data={"grant_type": "client_credentials"},
        auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _paypal_headers() -> dict:
    return {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {_paypal_access_token()}",
    }


def _paypal_get_subscription(subscription_id: str) -> dict:
    resp = requests.get(
        f"{_PAYPAL_BASE}/v1/billing/subscriptions/{subscription_id}",
        headers=_paypal_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _paypal_cancel_subscription(subscription_id: str, reason: str = "User requested cancellation") -> bool:
    try:
        resp = requests.post(
            f"{_PAYPAL_BASE}/v1/billing/subscriptions/{subscription_id}/cancel",
            headers=_paypal_headers(),
            json={"reason": reason},
            timeout=10,
        )
        return resp.status_code == 204
    except Exception as e:
        logger.warning(f"[PAY] PayPal cancel API call failed (non-fatal): {e}")
        return False


def _paypal_verify_webhook(headers: dict, body: bytes) -> bool:
    """
    Verify PayPal webhook using PayPal's own verification API.
    PayPal does not use HMAC — it uses a cert-based system verified server-side.
    """
    if not PAYPAL_WEBHOOK_ID:
        logger.error("[PAY] PAYPAL_WEBHOOK_ID not set — cannot verify webhook")
        return False
    try:
        payload = {
            "auth_algo":         headers.get("PAYPAL-AUTH-ALGO", ""),
            "cert_url":          headers.get("PAYPAL-CERT-URL", ""),
            "transmission_id":   headers.get("PAYPAL-TRANSMISSION-ID", ""),
            "transmission_sig":  headers.get("PAYPAL-TRANSMISSION-SIG", ""),
            "transmission_time": headers.get("PAYPAL-TRANSMISSION-TIME", ""),
            "webhook_id":        PAYPAL_WEBHOOK_ID,
            "webhook_event":     json.loads(body),
        }
        resp = requests.post(
            f"{_PAYPAL_BASE}/v1/notifications/verify-webhook-signature",
            headers=_paypal_headers(),
            json=payload,
            timeout=10,
        )
        return resp.json().get("verification_status") == "SUCCESS"
    except Exception as e:
        logger.error(f"[PAY] Webhook verification error: {e}")
        return False


# ── DB helpers ────────────────────────────────────────────────────────────────

def _activate_pro(supabase, user_id: str, plan: str, subscription_id: str = "") -> None:
    now             = datetime.now(timezone.utc)
    normalized_plan = plan if plan in _PRO_PLANS else "monthly"
    interval_days   = PLAN_PRICING.get(normalized_plan, {}).get("interval_days", 31)
    end_date        = (now + timedelta(days=interval_days)).isoformat()

    update_data = {
        "user_id":               user_id,
        "plan":                  normalized_plan,
        "reports_remaining":     9999,
        "subscription_end_date": end_date,
        "cancel_at_period_end":  False,
        "updated_at":            now.isoformat(),
    }
    if subscription_id:
        update_data["paypal_subscription_id"] = subscription_id

    supabase.table("user_profiles").upsert(update_data, on_conflict="user_id").execute()
    logger.info(f"[PAY] Activated {normalized_plan} for {user_id[:8]}, ends {end_date[:10]}")


def _downgrade_to_free(supabase, user_id: str) -> None:
    supabase.table("user_profiles").upsert({
        "user_id":               user_id,
        "plan":                  "free",
        "reports_remaining":     1,
        "paypal_subscription_id": None,
        "subscription_end_date": None,
        "cancel_at_period_end":  False,
        "updated_at":            _now_iso(),
    }, on_conflict="user_id").execute()
    logger.info(f"[PAY] Downgraded to free: {user_id[:8]}")


def _user_id_from_subscription(supabase, subscription_id: str) -> str:
    try:
        res = (supabase.table("user_profiles")
               .select("user_id")
               .eq("paypal_subscription_id", subscription_id)
               .limit(1)
               .execute())
        if res.data:
            return res.data[0]["user_id"]
    except Exception:
        pass
    return ""


def _log_payment_event(supabase, user_id: str, event: str, detail: str) -> None:
    try:
        supabase.table("audit_logs").insert({
            "user_id":    user_id,
            "action":     f"PAYMENT_{event}",
            "detail":     detail[:1000],
            "category":   "PAYMENT",
            "created_at": _now_iso(),
        }).execute()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

@payment_bp.route("/api/payment/config", methods=["GET"])
def payment_config():
    """Public config for frontend PayPal JS SDK initialization."""
    return jsonify({
        "paypal_configured": bool(PAYPAL_CLIENT_ID),
        "paypal_client_id":  PAYPAL_CLIENT_ID,
        "paypal_env":        PAYPAL_ENV,
        "plans": {
            "monthly":  {"amount": 49,  "currency": "USD", "label": "Shield — $49/mo",                    "interval": "monthly"},
            "annual":   {"amount": 468, "currency": "USD", "label": "Shield — $39/mo (billed annually)",  "interval": "annual", "saves": "Save 20%"},
            "clinical": {"amount": 99,  "currency": "USD", "label": "Shield Clinical — $99/mo",           "interval": "monthly"},
        },
        "trial_days": TRIAL_DAYS,
    })


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE ACCESS CHECK  ← NEW
# ══════════════════════════════════════════════════════════════════════════════

@payment_bp.route("/api/payment/feature-access", methods=["GET"])
def check_feature_access():
    """
    Check if the authenticated user has access to a specific feature.
    Used by frontend to show/hide gated features like PA Architect.

    Query param: ?feature=pa_architect
    Response: { allowed: bool, reason: str, required_plan: str|null }
    """
    supabase, get_user, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    feature = request.args.get("feature", "").strip()
    if not feature:
        return jsonify({"error": "feature parameter required"}), 400

    from services.payment_feature_access import check_user_feature_access, get_required_plan
    allowed, reason = check_user_feature_access(supabase, user.id, feature)

    return jsonify({
        "feature":          feature,
        "allowed":          allowed,
        "reason":           reason if not allowed else "",
        "required_plan":    get_required_plan(feature) if not allowed else None,
        "upgrade_required": not allowed,
        "upgrade_url":      f"/app?upgrade={get_required_plan(feature)}" if not allowed else None,
    })


# ══════════════════════════════════════════════════════════════════════════════
# PAYMENT STATUS
# ══════════════════════════════════════════════════════════════════════════════

def _is_free_all_enabled(supabase) -> bool:
    """Returns True if founder has toggled free-all on in app_config."""
    try:
        res = (supabase.table("app_config")
               .select("value")
               .eq("key", "free_all_enabled")
               .limit(1)
               .execute())
        return bool(res.data and res.data[0].get("value") == "true")
    except Exception as e:
        logger.warning(f"[PAY] free_all check error (defaulting False): {e}")
        return False


@payment_bp.route("/api/payment/status", methods=["GET"])
def payment_status():
    supabase, get_user, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        # FIX: check free-all FIRST
        if _is_free_all_enabled(supabase):
            return jsonify({
                "plan":                 "pro",
                "plan_display":         "PHI Free (All Access)",
                "is_pro":               True,
                "is_free_all":          True,
                "reports_remaining":    9999,
                "subscription_end":     None,
                "cancel_at_period_end": False,
                "has_billing":          False,
                "had_trial":            False,
                "paypal_configured":    bool(PAYPAL_CLIENT_ID),
            })

        res = (supabase.table("user_profiles")
               .select("plan,reports_remaining,paypal_subscription_id,"
                       "subscription_end_date,cancel_at_period_end,had_trial")
               .eq("user_id", user.id)
               .limit(1)
               .execute())

        if not res.data:
            return jsonify({
                "plan":              "free",
                "plan_display":      "PHI Free",
                "is_pro":            False,
                "reports_remaining": 1,
                "paypal_configured": bool(PAYPAL_CLIENT_ID),
            })

        row  = res.data[0]
        plan = (row.get("plan") or "free").lower()

        return jsonify({
            "plan":                 plan,
            "plan_display":         PLAN_DISPLAY.get(plan, plan.title()),
            "is_pro":               _is_pro(plan),
            "reports_remaining":    row.get("reports_remaining", 1),
            "subscription_end":     row.get("subscription_end_date", ""),
            "cancel_at_period_end": row.get("cancel_at_period_end", False),
            "has_billing":          bool(row.get("paypal_subscription_id")),
            "had_trial":            row.get("had_trial", False),
            "paypal_configured":    bool(PAYPAL_CLIENT_ID),
        })

    except Exception as e:
        logger.error(f"[PAY] Status error: {e}")
        return jsonify({"plan": "free", "is_pro": False, "paypal_configured": bool(PAYPAL_CLIENT_ID)})


# ══════════════════════════════════════════════════════════════════════════════
# FREE TRIAL
# ══════════════════════════════════════════════════════════════════════════════

@payment_bp.route("/api/payment/start-trial", methods=["POST"])
def start_trial():
    if not TRIAL_DAYS:
        return jsonify({"error": "Free trial is not currently available"}), 400

    supabase, get_user, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        res = (supabase.table("user_profiles")
               .select("plan,had_trial")
               .eq("user_id", user.id)
               .limit(1)
               .execute())

        if res.data:
            row = res.data[0]
            if row.get("had_trial"):
                return jsonify({"error": "You've already used your free trial"}), 400
            if _is_pro(row.get("plan", "free")):
                return jsonify({"error": "You already have an active plan"}), 400

        end_date = (datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS)).isoformat()
        supabase.table("user_profiles").upsert({
            "user_id":               user.id,
            "plan":                  "trial",
            "reports_remaining":     9999,
            "subscription_end_date": end_date,
            "had_trial":             True,
            "updated_at":            _now_iso(),
        }, on_conflict="user_id").execute()

        _log_payment_event(supabase, user.id, "TRIAL_STARTED",
                           f"days:{TRIAL_DAYS} ends:{end_date[:10]}")

        return jsonify({
            "success":    True,
            "plan":       "trial",
            "trial_ends": end_date,
            "trial_days": TRIAL_DAYS,
            "message":    f"Your {TRIAL_DAYS}-day trial is now active. Enjoy full access to Curabook PHI!",
        })

    except Exception as e:
        logger.error(f"[PAY] Trial start error: {e}")
        return jsonify({"error": "Could not start trial. Please try again."}), 500


# ══════════════════════════════════════════════════════════════════════════════
# CREATE PAYPAL SUBSCRIPTION — Step 1 of checkout
# ══════════════════════════════════════════════════════════════════════════════

@payment_bp.route("/api/payment/paypal/create-subscription", methods=["POST"])
def create_paypal_subscription():
    """
    Creates a PayPal subscription and returns the approval URL.
    Frontend redirects user to approve_url for PayPal hosted checkout.

    Request:  { "plan": "monthly" | "annual" | "clinical" }
    Response: { "subscription_id": "I-xxx", "approve_url": "https://paypal.com/..." }
    """
    supabase, get_user, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.json or {}
    plan = body.get("plan", "monthly").lower()

    if plan not in PLAN_PRICING or plan == "trial":
        return jsonify({"error": f"Invalid plan: {plan}"}), 400

    pricing = PLAN_PRICING[plan]
    plan_id = pricing["paypal_plan"]

    if not plan_id:
        return jsonify({
            "error": f"PayPal billing plan for '{plan}' is not configured. "
                     f"Set PAYPAL_PLAN_{plan.upper()}_ID in your .env"
        }), 503

    if not PAYPAL_CLIENT_ID:
        return jsonify({"error": "PayPal is not configured on this server"}), 503

    try:
        payload = {
            "plan_id":    plan_id,
            "quantity":   "1",
            "subscriber": {"email_address": user.email or ""},
            "application_context": {
                "brand_name":          "Curabook PHI",
                "locale":              "en-US",
                "shipping_preference": "NO_SHIPPING",
                "user_action":         "SUBSCRIBE_NOW",
                "payment_method": {
                    "payer_selected":  "PAYPAL",
                    "payee_preferred": "IMMEDIATE_PAYMENT_REQUIRED",
                },
                "return_url": f"{FRONTEND_URL}/payment/success?plan={plan}",
                "cancel_url": f"{FRONTEND_URL}/payment/cancel",
            },
            # custom_id lets webhook identify the user without a DB lookup
            "custom_id": f"{user.id}|{plan}",
        }

        resp = requests.post(
            f"{_PAYPAL_BASE}/v1/billing/subscriptions",
            headers=_paypal_headers(),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        subscription_id = data.get("id", "")
        approve_url = next(
            (link["href"] for link in data.get("links", []) if link.get("rel") == "approve"),
            None,
        )

        if not approve_url:
            raise ValueError("PayPal did not return an approval URL")

        logger.info(f"[PAY] Created subscription {subscription_id} for {user.id[:8]}, plan={plan}")

        return jsonify({
            "subscription_id": subscription_id,
            "approve_url":     approve_url,
            "plan":            plan,
        })

    except requests.HTTPError as e:
        error_body = {}
        try:
            error_body = e.response.json()
        except Exception:
            pass
        logger.error(f"[PAY] PayPal subscription creation failed: {e} — {error_body}")
        return jsonify({
            "error":  "Payment setup failed. Please try again.",
            "detail": error_body.get("message", str(e)),
        }), 500
    except Exception as e:
        logger.error(f"[PAY] create-subscription error: {e}")
        return jsonify({"error": "Payment setup failed. Please try again."}), 500


# ══════════════════════════════════════════════════════════════════════════════
# CAPTURE SUBSCRIPTION — Step 2 after PayPal redirect
# ══════════════════════════════════════════════════════════════════════════════

@payment_bp.route("/api/payment/paypal/capture", methods=["POST"])
def capture_paypal_subscription():
    """
    Called after user returns from PayPal approval page.
    Verifies subscription is ACTIVE, then upgrades user plan.

    Request:  { "subscription_id": "I-xxx", "plan": "monthly" }
    Response: { "success": true, "plan": "monthly", "message": "..." }
    """
    supabase, get_user, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    body            = request.json or {}
    subscription_id = body.get("subscription_id", "").strip()
    plan            = body.get("plan", "monthly").lower()

    if not subscription_id:
        return jsonify({"error": "subscription_id is required"}), 400
    if plan not in PLAN_PRICING or plan == "trial":
        return jsonify({"error": f"Invalid plan: {plan}"}), 400

    try:
        sub_data = _paypal_get_subscription(subscription_id)
        status   = sub_data.get("status", "")

        if status not in ("ACTIVE", "APPROVED"):
            return jsonify({
                "error":  f"Subscription is not active (status: {status}). "
                          "Please complete the PayPal approval and try again.",
                "status": status,
            }), 400

        # Prevent substitution attacks — verify custom_id matches this user
        custom_id = sub_data.get("custom_id", "")
        if custom_id and not custom_id.startswith(user.id):
            logger.warning(f"[PAY] custom_id mismatch for {user.id[:8]}")
            return jsonify({"error": "Subscription does not belong to this account"}), 403

        _activate_pro(supabase, user.id, plan, subscription_id=subscription_id)
        _log_payment_event(supabase, user.id, "SUBSCRIPTION_ACTIVATED",
                           f"plan:{plan} sub:{subscription_id}")

        return jsonify({
            "success":  True,
            "plan":     plan,
            "message":  f"Welcome to PHI {PLAN_DISPLAY.get(plan, 'Shield')}! Your plan is now active.",
            "amount":   PLAN_PRICING[plan]["amount"],
            "currency": PLAN_PRICING[plan]["currency"],
        })

    except requests.HTTPError as e:
        logger.error(f"[PAY] PayPal subscription verification failed: {e}")
        return jsonify({"error": "Could not verify payment with PayPal. Contact support@curabook.com"}), 500
    except Exception as e:
        logger.error(f"[PAY] Capture error: {e}")
        return jsonify({"error": "Payment activation failed. Contact support@curabook.com"}), 500


# ══════════════════════════════════════════════════════════════════════════════
# CANCEL SUBSCRIPTION
# ══════════════════════════════════════════════════════════════════════════════

@payment_bp.route("/api/payment/cancel", methods=["POST"])
def cancel_subscription():
    """
    Cancel subscription. Default: cancel at period end (user keeps access).
    Pass { "immediate": true } to downgrade immediately.
    """
    supabase, get_user, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    body      = request.json or {}
    immediate = body.get("immediate", False)

    try:
        res = (supabase.table("user_profiles")
               .select("plan,paypal_subscription_id,subscription_end_date")
               .eq("user_id", user.id)
               .limit(1)
               .execute())

        if not res.data:
            return jsonify({"error": "No subscription found"}), 404

        row      = res.data[0]
        plan     = row.get("plan", "free")
        sub_id   = row.get("paypal_subscription_id", "")
        end_date = row.get("subscription_end_date", "")

        if plan == "free":
            return jsonify({"error": "No active subscription to cancel"}), 400

        if sub_id:
            _paypal_cancel_subscription(sub_id)

        if immediate:
            _downgrade_to_free(supabase, user.id)
            _log_payment_event(supabase, user.id, "CANCELLED_IMMEDIATE", f"plan:{plan}")
            return jsonify({
                "success": True,
                "message": "Subscription cancelled. You've been moved to the free plan.",
                "plan":    "free",
            })
        else:
            supabase.table("user_profiles").upsert({
                "user_id":              user.id,
                "cancel_at_period_end": True,
                "updated_at":           _now_iso(),
            }, on_conflict="user_id").execute()
            _log_payment_event(supabase, user.id, "CANCELLED_AT_PERIOD_END",
                               f"plan:{plan} ends:{end_date}")
            return jsonify({
                "success":    True,
                "message":    (
                    f"Subscription cancelled. You keep full access until "
                    f"{end_date[:10] if end_date else 'your renewal date'}."
                ),
                "plan":       plan,
                "ends":       end_date,
                "cancel_eop": True,
            })

    except Exception as e:
        logger.error(f"[PAY] Cancel error: {e}")
        return jsonify({"error": "Could not cancel. Contact support@curabook.com"}), 500


# ══════════════════════════════════════════════════════════════════════════════
# BILLING HISTORY
# ══════════════════════════════════════════════════════════════════════════════

@payment_bp.route("/api/payment/billing", methods=["GET"])
def billing_history():
    supabase, get_user, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        res = (supabase.table("user_profiles")
               .select("plan,paypal_subscription_id,subscription_end_date,cancel_at_period_end")
               .eq("user_id", user.id)
               .limit(1)
               .execute())

        if not res.data:
            return jsonify({"payments": [], "plan": "free"})

        row    = res.data[0]
        plan   = row.get("plan", "free")
        sub_id = row.get("paypal_subscription_id", "")
        payments = []

        if sub_id and PAYPAL_CLIENT_ID:
            try:
                now       = datetime.now(timezone.utc)
                start_iso = (now - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
                end_iso   = now.strftime("%Y-%m-%dT%H:%M:%SZ")

                resp = requests.get(
                    f"{_PAYPAL_BASE}/v1/billing/subscriptions/{sub_id}/transactions",
                    headers=_paypal_headers(),
                    params={"start_time": start_iso, "end_time": end_iso},
                    timeout=10,
                )
                if resp.ok:
                    for tx in resp.json().get("transactions", []):
                        amt = tx.get("amount_with_breakdown", {}).get("gross_amount", {})
                        payments.append({
                            "id":         tx.get("id", ""),
                            "amount":     float(amt.get("value", 0)),
                            "currency":   amt.get("currency_code", "USD"),
                            "status":     tx.get("status", ""),
                            "created_at": tx.get("time", ""),
                        })
            except Exception as e:
                logger.warning(f"[PAY] Billing history fetch error (non-fatal): {e}")

        return jsonify({
            "plan":                 plan,
            "plan_display":         PLAN_DISPLAY.get(plan, plan.title()),
            "subscription_id":      sub_id,
            "subscription_end":     row.get("subscription_end_date", ""),
            "cancel_at_period_end": row.get("cancel_at_period_end", False),
            "payments":             payments,
        })

    except Exception as e:
        logger.error(f"[PAY] Billing error: {e}")
        return jsonify({"payments": [], "plan": "free"})


# ══════════════════════════════════════════════════════════════════════════════
# PAYPAL WEBHOOK
# ══════════════════════════════════════════════════════════════════════════════

@payment_bp.route("/api/payment/paypal/webhook", methods=["POST"])
def paypal_webhook():
    """
    Handles PayPal subscription lifecycle events.
    Register this URL in PayPal dashboard for these events:
      BILLING.SUBSCRIPTION.ACTIVATED
      BILLING.SUBSCRIPTION.RENEWED
      BILLING.SUBSCRIPTION.CANCELLED
      BILLING.SUBSCRIPTION.EXPIRED
      BILLING.SUBSCRIPTION.PAYMENT.FAILED
      PAYMENT.SALE.COMPLETED
    """
    payload = request.get_data()
    headers = dict(request.headers)

    if not _paypal_verify_webhook(headers, payload):
        logger.warning("[PAY] PayPal webhook signature invalid — rejected")
        return jsonify({"error": "Invalid webhook signature"}), 400

    try:
        event      = json.loads(payload)
        event_type = event.get("event_type", "")
        resource   = event.get("resource", {})

        logger.info(f"[PAY] PayPal webhook: {event_type}")

        from app import supabase

        if event_type in ("BILLING.SUBSCRIPTION.ACTIVATED", "BILLING.SUBSCRIPTION.RENEWED"):
            subscription_id = resource.get("id", "")
            custom_id       = resource.get("custom_id", "")

            user_id, plan = "", "monthly"
            if custom_id and "|" in custom_id:
                parts   = custom_id.split("|", 1)
                user_id = parts[0]
                plan    = parts[1] if len(parts) > 1 else "monthly"
            if not user_id:
                user_id = _user_id_from_subscription(supabase, subscription_id)

            if user_id:
                _activate_pro(supabase, user_id, plan, subscription_id=subscription_id)
                _log_payment_event(supabase, user_id, "SUBSCRIPTION_ACTIVATED",
                                   f"plan:{plan} sub:{subscription_id} event:{event_type}")

        elif event_type in ("BILLING.SUBSCRIPTION.CANCELLED", "BILLING.SUBSCRIPTION.EXPIRED"):
            subscription_id = resource.get("id", "")
            user_id         = _user_id_from_subscription(supabase, subscription_id)
            if user_id:
                _downgrade_to_free(supabase, user_id)
                _log_payment_event(supabase, user_id, "SUBSCRIPTION_CANCELLED",
                                   f"sub:{subscription_id} event:{event_type}")

        elif event_type == "BILLING.SUBSCRIPTION.PAYMENT.FAILED":
            subscription_id = resource.get("id", "")
            user_id         = _user_id_from_subscription(supabase, subscription_id)
            if user_id:
                _log_payment_event(supabase, user_id, "PAYMENT_FAILED",
                                   f"sub:{subscription_id}")
                # Don't downgrade yet — PayPal retries. Downgrade happens on CANCELLED/EXPIRED.

        elif event_type == "PAYMENT.SALE.COMPLETED":
            billing_agreement_id = resource.get("billing_agreement_id", "")
            if billing_agreement_id:
                user_id = _user_id_from_subscription(supabase, billing_agreement_id)
                if user_id:
                    _log_payment_event(supabase, user_id, "SALE_COMPLETED",
                                       f"sale:{resource.get('id','')} sub:{billing_agreement_id}")

    except Exception as e:
        logger.error(f"[PAY] Webhook processing error: {e}")
        return jsonify({"received": True, "error": str(e)})

    # Always return 200 so PayPal doesn't retry
    return jsonify({"received": True})


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN — GRANT PLAN MANUALLY
# ══════════════════════════════════════════════════════════════════════════════

@payment_bp.route("/api/payment/admin/grant", methods=["POST"])
def admin_grant_plan():
    """Body: { "user_id": "...", "plan": "monthly", "days": 31 }"""
    if not ADMIN_SECRET:
        return jsonify({"error": "Admin not configured"}), 503
    if request.headers.get("X-Admin-Secret") != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    supabase, _, _ = _deps()
    body    = request.json or {}
    user_id = body.get("user_id", "")
    plan    = body.get("plan", "monthly")
    days    = int(body.get("days", 31))

    if not user_id or plan not in _PRO_PLANS:
        return jsonify({"error": "user_id and valid plan required"}), 400

    try:
        end_date = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        supabase.table("user_profiles").upsert({
            "user_id":               user_id,
            "plan":                  plan,
            "reports_remaining":     9999,
            "subscription_end_date": end_date,
            "updated_at":            _now_iso(),
        }, on_conflict="user_id").execute()
        _log_payment_event(supabase, user_id, "ADMIN_GRANT", f"plan:{plan} days:{days}")
        return jsonify({"success": True, "plan": plan, "ends": end_date})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# SETUP TABLES
# ══════════════════════════════════════════════════════════════════════════════

@payment_bp.route("/api/payment/setup-tables", methods=["POST"])
def setup_missing_tables():
    """
    Creates missing DB tables and adds paypal_subscription_id column.
    Run once after deploying. Safe to re-run (IF NOT EXISTS).
    """
    if not ADMIN_SECRET:
        return jsonify({"error": "Admin not configured"}), 503
    if request.headers.get("X-Admin-Secret") != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    supabase, _, _ = _deps()

    missing_columns_sql = [
        "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS paypal_subscription_id text",
        "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS subscription_end_date timestamptz",
        "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS cancel_at_period_end boolean default false",
        "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS had_trial boolean default false",
        "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS goal_weight_lbs numeric",
        "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS current_weight_lbs numeric",
        "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS glp1_status text",
        "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS is_admin_granted boolean default false",
    ]

    missing_tables_sql = [
        """CREATE TABLE IF NOT EXISTS app_config (
            key        text PRIMARY KEY,
            value      text NOT NULL DEFAULT '',
            updated_at timestamptz DEFAULT now(),
            updated_by text
        )""",
        """CREATE TABLE IF NOT EXISTS audit_logs (
            id         bigserial PRIMARY KEY,
            user_id    text,
            action     text,
            detail     text,
            category   text,
            created_at timestamptz DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS appeal_packets (
            id           uuid primary key default gen_random_uuid(),
            user_id      uuid not null references auth.users(id) on delete cascade,
            medication   text, denial_reason text, score integer, packet text,
            created_at   timestamptz default now()
        )""",
        """CREATE TABLE IF NOT EXISTS weekly_briefs (
            id           uuid primary key default gen_random_uuid(),
            user_id      uuid not null references auth.users(id) on delete cascade,
            subject      text, headline text, full_text text, brief_json text,
            generated_at timestamptz default now()
        )""",
        """CREATE TABLE IF NOT EXISTS appointment_preps (
            id               uuid primary key default gen_random_uuid(),
            user_id          uuid not null references auth.users(id) on delete cascade,
            appointment_date date, specialist_type text default 'primary care',
            brief_text text, brief_json text, created_at timestamptz default now()
        )""",
        """CREATE TABLE IF NOT EXISTS glp1_onboarding (
            id uuid primary key default gen_random_uuid(),
            user_id uuid not null references auth.users(id) on delete cascade unique,
            glp1_status text, medication_name text, goal_weight_lbs numeric,
            primary_concern text, completed_at timestamptz default now()
        )""",
        """CREATE TABLE IF NOT EXISTS glp1_medications (
            id uuid primary key default gen_random_uuid(),
            user_id uuid not null references auth.users(id) on delete cascade,
            medication_name text not null, dose_mg numeric,
            frequency text default 'weekly', start_date date, end_date date,
            status text default 'active', stop_reason text, notes text,
            created_at timestamptz default now(), updated_at timestamptz default now(),
            unique(user_id, medication_name, start_date)
        )""",
        # Default config seeds
        "INSERT INTO app_config (key,value,updated_by) VALUES ('free_all_enabled','false','migration') ON CONFLICT (key) DO NOTHING",
        "INSERT INTO app_config (key,value,updated_by) VALUES ('maintenance_mode','false','migration') ON CONFLICT (key) DO NOTHING",
        # RLS
        "ALTER TABLE weekly_briefs     ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE appointment_preps ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE glp1_onboarding   ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE glp1_medications  ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE appeal_packets    ENABLE ROW LEVEL SECURITY",
        """DO $$ BEGIN CREATE POLICY own_briefs ON weekly_briefs FOR ALL USING (auth.uid()=user_id);
        EXCEPTION WHEN duplicate_object THEN NULL; END $$""",
        """DO $$ BEGIN CREATE POLICY own_appt ON appointment_preps FOR ALL USING (auth.uid()=user_id);
        EXCEPTION WHEN duplicate_object THEN NULL; END $$""",
        """DO $$ BEGIN CREATE POLICY own_onboard ON glp1_onboarding FOR ALL USING (auth.uid()=user_id);
        EXCEPTION WHEN duplicate_object THEN NULL; END $$""",
        """DO $$ BEGIN CREATE POLICY own_meds ON glp1_medications FOR ALL USING (auth.uid()=user_id);
        EXCEPTION WHEN duplicate_object THEN NULL; END $$""",
        """DO $$ BEGIN CREATE POLICY own_appeal ON appeal_packets FOR ALL USING (auth.uid()=user_id);
        EXCEPTION WHEN duplicate_object THEN NULL; END $$""",
        # Indexes
        "CREATE INDEX IF NOT EXISTS idx_briefs_user    ON weekly_briefs(user_id, generated_at desc)",
        "CREATE INDEX IF NOT EXISTS idx_appt_user      ON appointment_preps(user_id, appointment_date desc)",
        "CREATE INDEX IF NOT EXISTS idx_onboard_user   ON glp1_onboarding(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_meds_user      ON glp1_medications(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_paypal_sub     ON user_profiles(paypal_subscription_id)",
        "CREATE INDEX IF NOT EXISTS idx_appeal_user    ON appeal_packets(user_id, created_at desc)",
        "CREATE INDEX IF NOT EXISTS idx_audit_user     ON audit_logs(user_id, created_at desc)",
        "CREATE INDEX IF NOT EXISTS idx_audit_category ON audit_logs(category, created_at desc)",
    ]

    results = {"columns": [], "tables": [], "errors": []}

    for sql in missing_columns_sql:
        try:
            supabase.rpc("exec_sql", {"sql": sql}).execute()
            results["columns"].append(sql[:60])
        except Exception as e:
            results["errors"].append(f"Column: {str(e)[:100]}")

    for sql in missing_tables_sql:
        try:
            supabase.rpc("exec_sql", {"sql": sql}).execute()
            results["tables"].append(sql[:60])
        except Exception as e:
            results["errors"].append(f"Table: {str(e)[:100]}")

    return jsonify({
        "success": len(results["errors"]) == 0,
        "results": results,
        "note":    "Run SQL directly in Supabase SQL Editor if errors occur here.",
    })