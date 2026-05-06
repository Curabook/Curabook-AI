"""
api/payment_routes.py — Razorpay Payment Integration (FULLY FIXED v2)
════════════════════════════════════════════════════════════════════════════
FIXES IN THIS VERSION:

  #PAY-FIX-1  hmac.new() is Python 2. Python 3 uses hmac.new() correctly
              with positional args: hmac.new(key_bytes, msg_bytes, hashlib.sha256)
              The previous code used hmac.HMAC() which is the class directly —
              both actually work in Python 3, but the .hexdigest() call was
              being made on the constructor call result, not an HMAC instance.
              Fixed to use the module-level hmac.new() which is unambiguous.

  #PAY-FIX-2  Price alignment: frontend index.html shows $49/mo, $180/yr, $99/mo
              Clinical. The upgrade modal in script.js was showing $20/mo.
              All prices now canonical: monthly=$49, annual=$180, clinical=$99.
              Razorpay amounts: 4900, 18000, 9900 (cents/smallest unit USD).

  #PAY-FIX-3  Webhook secret guard: if RAZORPAY_WEBHOOK_SECRET is not set,
              webhook endpoint returns 503 (not 200). Never allow unsigned
              webhooks to activate pro plans.

  #PAY-FIX-4  _activate_pro now also accepts "pro" plan directly for manual
              activations. plan "monthly" → stored as "monthly" (not "pro").

  #PAY-FIX-5  /api/payment/status now initializes free users who don't have
              a profile row yet — prevents null plan causing upgrade loops.

  #PAY-FIX-6  Added /api/payment/manual-activate endpoint for admin use
              (protected by CRON_SECRET header) to manually set a user pro.

PRICING (canonical, matches index.html):
  Shield Core Monthly:  $49/mo   → Razorpay amount: 4900
  Shield Core Annual:   $180/yr  → Razorpay amount: 18000 ($15/mo)
  Shield Clinical:      $99/mo   → Razorpay amount: 9900

PLAN NAMES (stored in user_profiles.plan):
  "free"     → 1 report upload, unlimited chat
  "monthly"  → Shield Core Monthly ($49/mo)
  "annual"   → Shield Core Annual ($180/yr)
  "clinical" → Shield Clinical ($99/mo)
  "pro"      → legacy alias, treated same as "monthly"
════════════════════════════════════════════════════════════════════════════
"""

import os
import hmac
import hashlib
import json
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify

payment_bp = Blueprint("payment", __name__)

# ── Env vars ──────────────────────────────────────────────────────────────────
RAZORPAY_KEY_ID           = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET       = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET   = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
RAZORPAY_PLAN_MONTHLY_ID  = os.getenv("RAZORPAY_PLAN_MONTHLY_ID", "")
RAZORPAY_PLAN_ANNUAL_ID   = os.getenv("RAZORPAY_PLAN_ANNUAL_ID", "")
RAZORPAY_PLAN_CLINICAL_ID = os.getenv("RAZORPAY_PLAN_CLINICAL_ID", "")
FRONTEND_URL              = os.getenv("FRONTEND_URL", "https://curabook.com")
CRON_SECRET               = os.getenv("CRON_SECRET", "")

# ── Canonical pricing (#PAY-FIX-2) ───────────────────────────────────────────
# Razorpay amounts are in smallest currency unit (USD cents: 4900 = $49.00)
PLAN_PRICING = {
    "monthly":  {"amount": 4900,  "currency": "USD", "description": "PHI Shield Core Monthly — $49/mo"},
    "annual":   {"amount": 18000, "currency": "USD", "description": "PHI Shield Core Annual — $180/yr ($15/mo)"},
    "clinical": {"amount": 9900,  "currency": "USD", "description": "PHI Shield Clinical Monthly — $99/mo"},
}

PLAN_DISPLAY = {
    "free":     "PHI Free",
    "monthly":  "Shield Core",
    "annual":   "Shield Core Annual",
    "clinical": "Shield Clinical",
    "pro":      "Shield Core",   # legacy alias
}

# All plans that get pro features
_PRO_PLANS = {"monthly", "annual", "clinical", "pro"}


def _razorpay_client():
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise RuntimeError("Razorpay not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env")
    try:
        import razorpay
        return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    except ImportError:
        raise RuntimeError("razorpay package not installed. Run: pip install razorpay")


def _deps():
    from app import supabase
    from services.auth import get_authenticated_user
    from services.compliance import audit_log
    return supabase, get_authenticated_user, audit_log


def _is_pro(plan: str) -> bool:
    return (plan or "").lower() in _PRO_PLANS


# ── GET /api/payment/config ───────────────────────────────────────────────────
@payment_bp.route("/api/payment/config", methods=["GET"])
def payment_config():
    """Frontend calls this before showing upgrade UI."""
    return jsonify({
        "razorpay_configured": bool(RAZORPAY_KEY_ID),
        "razorpay_key_id":     RAZORPAY_KEY_ID,
        "plans": {
            "monthly":  {"amount": 49,  "currency": "USD", "label": "Shield Core — $49/mo",       "annual_equivalent": None},
            "annual":   {"amount": 180, "currency": "USD", "label": "Shield Core Annual — $180/yr", "annual_equivalent": 15},
            "clinical": {"amount": 99,  "currency": "USD", "label": "Shield Clinical — $99/mo",    "annual_equivalent": None},
        },
        "free_features": [
            "1 lab report upload",
            "Unlimited chat with PHI",
            "Initial Metabolic Shield score",
        ],
        "pro_features": [
            "Unlimited lab report uploads",
            "Full Metabolic Shield monitoring",
            "Weekly cliff & rebound alerts",
            "Muscle Defense Protocol",
            "Doctor visit prep brief",
            "Insurance PA support",
            "Behavior-to-marker tracking",
        ],
    })


# ── GET /api/payment/status ───────────────────────────────────────────────────
@payment_bp.route("/api/payment/status", methods=["GET"])
def payment_status():
    supabase, get_user, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        res = (supabase.table("user_profiles")
               .select("plan,reports_remaining,razorpay_subscription_id")
               .eq("user_id", user.id).limit(1).execute())

        if not res.data:
            # #PAY-FIX-5: Initialize profile for brand-new users
            supabase.table("user_profiles").upsert({
                "user_id":           user.id,
                "plan":              "free",
                "reports_remaining": 1,
            }, on_conflict="user_id").execute()
            return jsonify({
                "plan":              "free",
                "plan_display":      "PHI Free",
                "is_pro":            False,
                "reports_remaining": 1,
                "has_billing":       False,
                "razorpay_configured": bool(RAZORPAY_KEY_ID),
            })

        row  = res.data[0]
        plan = (row.get("plan") or "free").lower()
        remaining = row.get("reports_remaining", 1)
        if remaining is None:
            remaining = 1

        return jsonify({
            "plan":              plan,
            "plan_display":      PLAN_DISPLAY.get(plan, plan.title()),
            "is_pro":            _is_pro(plan),
            "reports_remaining": int(remaining),
            "has_billing":       bool(row.get("razorpay_subscription_id")),
            "razorpay_configured": bool(RAZORPAY_KEY_ID),
        })
    except Exception as e:
        print(f"[PAYMENT] Status error: {e}")
        return jsonify({"plan": "free", "is_pro": False, "reports_remaining": 1, "razorpay_configured": bool(RAZORPAY_KEY_ID)})


# ── POST /api/payment/razorpay/order ─────────────────────────────────────────
@payment_bp.route("/api/payment/razorpay/order", methods=["POST"])
def create_razorpay_order():
    supabase, get_user, audit = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    if not RAZORPAY_KEY_ID:
        return jsonify({
            "error": "Payment not configured.",
            "message": "Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in your .env file.",
        }), 503

    data = request.json or {}
    plan = (data.get("plan", "monthly") or "monthly").lower()
    if plan not in PLAN_PRICING:
        return jsonify({"error": f"Invalid plan '{plan}'. Valid: {list(PLAN_PRICING.keys())}"}), 400

    pricing = PLAN_PRICING[plan]

    try:
        client = _razorpay_client()

        plan_id_map = {
            "monthly":  RAZORPAY_PLAN_MONTHLY_ID,
            "annual":   RAZORPAY_PLAN_ANNUAL_ID,
            "clinical": RAZORPAY_PLAN_CLINICAL_ID,
        }
        plan_id = plan_id_map.get(plan, "")

        if plan_id:
            # Subscription flow (recurring billing)
            subscription = client.subscription.create({
                "plan_id":         plan_id,
                "customer_notify": 1,
                "total_count":     120 if plan == "annual" else 12,
                "notes":           {"user_id": user.id, "plan": plan, "email": user.email or ""},
            })
            # Store pending subscription
            try:
                supabase.table("user_profiles").upsert({
                    "user_id":                       user.id,
                    "razorpay_pending_subscription": subscription["id"],
                }, on_conflict="user_id").execute()
            except Exception as e:
                print(f"[PAYMENT] Pending sub save error (non-fatal): {e}")

            audit(supabase, user.id, "RAZORPAY_SUBSCRIPTION_CREATED",
                  f"plan={plan} sub={subscription['id']}", "BILLING")

            return jsonify({
                "razorpay_key_id": RAZORPAY_KEY_ID,
                "subscription_id": subscription["id"],
                "amount":          pricing["amount"],
                "currency":        pricing["currency"],
                "description":     pricing["description"],
                "plan":            plan,
                "mode":            "subscription",
            })

        else:
            # One-time order fallback (no Razorpay plan IDs configured)
            order = client.order.create({
                "amount":          pricing["amount"],
                "currency":        pricing["currency"],
                "payment_capture": 1,
                "notes":           {"user_id": user.id, "plan": plan, "email": user.email or ""},
            })
            audit(supabase, user.id, "RAZORPAY_ORDER_CREATED",
                  f"plan={plan} order={order['id']}", "BILLING")

            return jsonify({
                "razorpay_key_id": RAZORPAY_KEY_ID,
                "order_id":        order["id"],
                "amount":          pricing["amount"],
                "currency":        pricing["currency"],
                "description":     pricing["description"],
                "plan":            plan,
                "mode":            "one_time",
            })

    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        print(f"[PAYMENT] Order creation error: {e}")
        return jsonify({"error": "Could not create payment session. Please try again."}), 500


# ── POST /api/payment/razorpay/verify ────────────────────────────────────────
@payment_bp.route("/api/payment/razorpay/verify", methods=["POST"])
def verify_razorpay_payment():
    supabase, get_user, audit = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    body            = request.json or {}
    order_id        = body.get("order_id", "")
    payment_id      = body.get("payment_id", "")
    signature       = body.get("signature", "")
    subscription_id = body.get("subscription_id", "")
    plan            = (body.get("plan", "monthly") or "monthly").lower()

    if not RAZORPAY_KEY_SECRET:
        return jsonify({"error": "Payment not configured"}), 503

    if not payment_id or not signature:
        return jsonify({"error": "Missing payment_id or signature"}), 400

    try:
        # #PAY-FIX-1: Correct Python 3 hmac usage
        # hmac.new(key: bytes, msg: bytes, digestmod) → HMAC instance
        if subscription_id:
            message = f"{payment_id}|{subscription_id}"
        else:
            message = f"{order_id}|{payment_id}"

        expected_sig = hmac.new(
            RAZORPAY_KEY_SECRET.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected_sig, signature):
            print(f"[PAYMENT] Signature mismatch for user {user.id[:8]}")
            audit(supabase, user.id, "RAZORPAY_SIGNATURE_FAILED",
                  f"payment={payment_id}", "BILLING")
            return jsonify({"error": "Payment verification failed. Please contact support."}), 400

        # Signature valid — activate plan
        _activate_pro(supabase, user.id, plan, payment_id, subscription_id)
        audit(supabase, user.id, "RAZORPAY_PAYMENT_VERIFIED",
              f"plan={plan} payment={payment_id}", "BILLING")

        return jsonify({
            "success":      True,
            "plan":         plan,
            "plan_display": PLAN_DISPLAY.get(plan, "Pro"),
            "message":      f"Welcome to PHI {PLAN_DISPLAY.get(plan, 'Pro')}! Unlimited access unlocked.",
        })

    except Exception as e:
        print(f"[PAYMENT] Verification error: {e}")
        return jsonify({"error": "Verification failed. Please contact support@curabook.com"}), 500


# ── POST /api/payment/razorpay/webhook ───────────────────────────────────────
@payment_bp.route("/api/payment/razorpay/webhook", methods=["POST"])
def razorpay_webhook():
    # #PAY-FIX-3: Require webhook secret — never allow unsigned webhooks
    if not RAZORPAY_WEBHOOK_SECRET:
        print("[WEBHOOK] RAZORPAY_WEBHOOK_SECRET not set — rejecting all webhook requests")
        return jsonify({"error": "Webhook not configured"}), 503

    payload   = request.get_data()
    signature = request.headers.get("X-Razorpay-Signature", "")

    if not signature:
        return jsonify({"error": "Missing signature"}), 400

    # #PAY-FIX-1: Correct hmac usage
    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        payload,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        print("[WEBHOOK] Invalid Razorpay webhook signature")
        return jsonify({"error": "Invalid signature"}), 400

    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid JSON"}), 400

    from app import supabase

    event_type     = event.get("event", "")
    entity         = event.get("payload", {}).get("subscription", {}).get("entity", {})
    payment_entity = event.get("payload", {}).get("payment", {}).get("entity", {})

    print(f"[WEBHOOK] Razorpay event: {event_type}")

    if event_type in ("subscription.activated", "subscription.charged"):
        notes   = entity.get("notes", {})
        user_id = notes.get("user_id", "")
        plan    = notes.get("plan", "monthly")
        sub_id  = entity.get("id", "")
        if user_id:
            _activate_pro(supabase, user_id, plan, "", sub_id)
            print(f"[WEBHOOK] Plan activated via webhook: {user_id[:8]} → {plan}")

    elif event_type in ("subscription.cancelled", "subscription.completed", "subscription.expired"):
        notes   = entity.get("notes", {})
        user_id = notes.get("user_id", "")
        sub_id  = entity.get("id", "")
        if not user_id:
            user_id = _user_id_from_subscription(supabase, sub_id)
        if user_id:
            _downgrade_to_free(supabase, user_id)
            print(f"[WEBHOOK] Downgraded to free: {user_id[:8]}")

    elif event_type == "payment.failed":
        notes   = payment_entity.get("notes", {})
        user_id = notes.get("user_id", "")
        if user_id:
            try:
                supabase.table("audit_logs").insert({
                    "user_id":    user_id,
                    "action":     "RAZORPAY_PAYMENT_FAILED",
                    "detail":     f"payment_id={payment_entity.get('id', '')}",
                    "category":   "BILLING",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
            except Exception:
                pass

    return jsonify({"received": True})


# ── POST /api/payment/portal ──────────────────────────────────────────────────
@payment_bp.route("/api/payment/portal", methods=["POST"])
def customer_portal():
    supabase, get_user, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        res = (supabase.table("user_profiles")
               .select("razorpay_subscription_id,plan")
               .eq("user_id", user.id).limit(1).execute())

        if not res.data or not res.data[0].get("razorpay_subscription_id"):
            return jsonify({
                "error": "No active subscription found.",
                "message": "You're on the free plan. Upgrade to access billing management.",
            }), 404

        sub_id = res.data[0]["razorpay_subscription_id"]
        plan   = res.data[0].get("plan", "monthly")

        return jsonify({
            "subscription_id":     sub_id,
            "plan":                plan,
            "plan_display":        PLAN_DISPLAY.get(plan, plan.title()),
            "manage_url":          "https://dashboard.razorpay.com",
            "cancel_endpoint":     "/api/payment/cancel",
            "cancel_instructions": (
                "To cancel, click 'Cancel Subscription' in your account settings "
                "or email support@curabook.com"
            ),
        })
    except Exception as e:
        print(f"[PAYMENT] Portal error: {e}")
        return jsonify({"error": "Could not load billing details."}), 500


# ── POST /api/payment/cancel ──────────────────────────────────────────────────
@payment_bp.route("/api/payment/cancel", methods=["POST"])
def cancel_subscription():
    supabase, get_user, audit = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        res = (supabase.table("user_profiles")
               .select("razorpay_subscription_id")
               .eq("user_id", user.id).limit(1).execute())

        if not res.data or not res.data[0].get("razorpay_subscription_id"):
            return jsonify({"error": "No active subscription found."}), 404

        sub_id = res.data[0]["razorpay_subscription_id"]
        client = _razorpay_client()
        client.subscription.cancel(sub_id, {"cancel_at_cycle_end": 1})

        audit(supabase, user.id, "RAZORPAY_SUBSCRIPTION_CANCEL_REQUESTED",
              f"sub={sub_id}", "BILLING")

        return jsonify({
            "success": True,
            "message": "Subscription cancelled. Pro access continues until end of billing period.",
        })

    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        print(f"[PAYMENT] Cancel error: {e}")
        return jsonify({"error": "Could not cancel. Please email support@curabook.com"}), 500


# ── POST /api/payment/manual-activate (#PAY-FIX-6) ───────────────────────────
@payment_bp.route("/api/payment/manual-activate", methods=["POST"])
def manual_activate():
    """
    Admin endpoint to manually set a user's plan.
    Protected by CRON_SECRET header (same as cron routes).
    Use for: customer support, failed payment recovery, trials.
    """
    provided_secret = request.headers.get("X-Cron-Secret", "")
    if not CRON_SECRET or provided_secret != CRON_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    data    = request.json or {}
    user_id = data.get("user_id", "")
    plan    = (data.get("plan", "monthly") or "monthly").lower()

    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    if plan not in {*_PRO_PLANS, "free"}:
        return jsonify({"error": f"Invalid plan: {plan}"}), 400

    try:
        from app import supabase
        if plan == "free":
            _downgrade_to_free(supabase, user_id)
        else:
            _activate_pro(supabase, user_id, plan, "manual", "")
        return jsonify({"success": True, "user_id": user_id, "plan": plan})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Helpers ───────────────────────────────────────────────────────────────────

def _activate_pro(supabase, user_id: str, plan: str, payment_id: str = "",
                  subscription_id: str = "") -> None:
    """Activate a pro plan for a user. All plan names stored as-is."""
    now = datetime.now(timezone.utc).isoformat()
    valid_plans = {*_PRO_PLANS}
    normalized_plan = plan if plan in valid_plans else "monthly"

    try:
        update_data = {
            "user_id":           user_id,
            "plan":              normalized_plan,
            "reports_remaining": 9999,
            "updated_at":        now,
        }
        if subscription_id:
            update_data["razorpay_subscription_id"] = subscription_id
        if payment_id and payment_id != "manual":
            update_data["razorpay_last_payment_id"] = payment_id

        supabase.table("user_profiles").upsert(
            update_data, on_conflict="user_id"
        ).execute()

        supabase.table("audit_logs").insert({
            "user_id":    user_id,
            "action":     "PRO_ACTIVATED",
            "detail":     f"plan={normalized_plan} payment={payment_id} sub={subscription_id}",
            "category":   "BILLING",
            "created_at": now,
        }).execute()

        print(f"[PAYMENT] ✅ Plan activated: {user_id[:8]} → {normalized_plan}")
    except Exception as e:
        print(f"[PAYMENT] Activate error: {e}")


def _downgrade_to_free(supabase, user_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        supabase.table("user_profiles").upsert({
            "user_id":                    user_id,
            "plan":                       "free",
            "reports_remaining":          1,
            "razorpay_subscription_id":   None,
            "updated_at":                 now,
        }, on_conflict="user_id").execute()

        supabase.table("audit_logs").insert({
            "user_id":    user_id,
            "action":     "DOWNGRADED_TO_FREE",
            "detail":     "Subscription ended or cancelled",
            "category":   "BILLING",
            "created_at": now,
        }).execute()
    except Exception as e:
        print(f"[PAYMENT] Downgrade error: {e}")


def _user_id_from_subscription(supabase, subscription_id: str) -> str:
    try:
        res = (supabase.table("user_profiles")
               .select("user_id")
               .eq("razorpay_subscription_id", subscription_id)
               .limit(1).execute())
        if res.data:
            return res.data[0]["user_id"]
    except Exception:
        pass
    return ""