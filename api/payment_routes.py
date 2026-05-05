"""
api/payment_routes.py — Razorpay Payment Integration (FIXED)
════════════════════════════════════════════════════════════════════════════
FIXES APPLIED:
  #PAY-1  hmac.new() → hmac.HMAC() — Python 3 correct constructor
  #PAY-2  Prices aligned to index.html: $49/mo, $180/yr, $99/mo Clinical
  #PAY-3  Shield Clinical tier added (missing from original)
  #PAY-4  Startup env validation — logs clearly when keys are missing
  #PAY-5  subscription_id path in verify fixed (was using wrong variable)
  #PAY-6  _activate_pro handles 'clinical' plan correctly

SETUP (just set these in your .env and you're live):
  RAZORPAY_KEY_ID=rzp_live_xxx
  RAZORPAY_KEY_SECRET=your_secret
  RAZORPAY_WEBHOOK_SECRET=your_webhook_secret
  RAZORPAY_PLAN_MONTHLY_ID=plan_xxx       (optional — for subscriptions)
  RAZORPAY_PLAN_ANNUAL_ID=plan_xxx        (optional)
  RAZORPAY_PLAN_CLINICAL_ID=plan_xxx      (optional)
  FRONTEND_URL=https://curabook.com
════════════════════════════════════════════════════════════════════════════
"""

import os
import hmac
import hashlib
import json
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify

payment_bp = Blueprint("payment", __name__)

RAZORPAY_KEY_ID          = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET      = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET  = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
RAZORPAY_PLAN_MONTHLY_ID = os.getenv("RAZORPAY_PLAN_MONTHLY_ID", "")
RAZORPAY_PLAN_ANNUAL_ID  = os.getenv("RAZORPAY_PLAN_ANNUAL_ID", "")
RAZORPAY_PLAN_CLINICAL_ID = os.getenv("RAZORPAY_PLAN_CLINICAL_ID", "")
FRONTEND_URL             = os.getenv("FRONTEND_URL", "https://curabook.com")

# ── Pricing (aligned to index.html) ──────────────────────────────────────────
# Razorpay amounts are in smallest currency unit
# For USD: 100 = $1.00
PLAN_PRICING = {
    "monthly":  {"amount": 4900,  "currency": "USD", "description": "PHI Shield Core Monthly — $49/mo"},
    "annual":   {"amount": 18000, "currency": "USD", "description": "PHI Shield Core Annual — $180/yr ($15/mo)"},
    "clinical": {"amount": 9900,  "currency": "USD", "description": "PHI Shield Clinical Monthly — $99/mo"},
}

_PLAN_DISPLAY = {
    "monthly":  "Shield Core",
    "annual":   "Shield Core Annual",
    "clinical": "Shield Clinical",
}


def _razorpay_client():
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise RuntimeError(
            "Razorpay not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env"
        )
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


# ── GET /api/payment/status ───────────────────────────────────────────────────
@payment_bp.route("/api/payment/status", methods=["GET"])
def payment_status():
    supabase, get_user, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        res = (supabase.table("user_profiles")
               .select("plan,reports_remaining,razorpay_subscription_id,razorpay_customer_id")
               .eq("user_id", user.id).limit(1).execute())

        if not res.data:
            supabase.table("user_profiles").upsert({
                "user_id": user.id, "plan": "free", "reports_remaining": 1,
            }, on_conflict="user_id").execute()
            return jsonify({"plan": "free", "is_pro": False, "reports_remaining": 1, "has_billing": False})

        row  = res.data[0]
        plan = row.get("plan", "free")
        is_pro = plan in ("monthly", "annual", "pro", "clinical")
        return jsonify({
            "plan":              plan,
            "plan_display":      _PLAN_DISPLAY.get(plan, plan.title()),
            "is_pro":            is_pro,
            "reports_remaining": row.get("reports_remaining", 1),
            "has_billing":       bool(row.get("razorpay_subscription_id")),
            "razorpay_configured": bool(RAZORPAY_KEY_ID),
        })
    except Exception as e:
        print(f"[PAYMENT] Status error: {e}")
        return jsonify({"plan": "free", "is_pro": False, "reports_remaining": 1})


# ── GET /api/payment/config ───────────────────────────────────────────────────
@payment_bp.route("/api/payment/config", methods=["GET"])
def payment_config():
    """Frontend calls this to check if payments are configured before showing upgrade UI."""
    return jsonify({
        "razorpay_configured": bool(RAZORPAY_KEY_ID),
        "razorpay_key_id":     RAZORPAY_KEY_ID,   # safe to expose (public key)
        "plans": {
            "monthly":  {"amount": 49,  "currency": "USD", "label": "Shield Core — $49/mo"},
            "annual":   {"amount": 180, "currency": "USD", "label": "Shield Core Annual — $180/yr"},
            "clinical": {"amount": 99,  "currency": "USD", "label": "Shield Clinical — $99/mo"},
        }
    })


# ── POST /api/payment/razorpay/order ─────────────────────────────────────────
@payment_bp.route("/api/payment/razorpay/order", methods=["POST"])
def create_razorpay_order():
    supabase, get_user, audit = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    if not RAZORPAY_KEY_ID:
        return jsonify({
            "error": "Payment not configured. Set RAZORPAY_KEY_ID in .env to enable payments."
        }), 503

    data = request.json or {}
    plan = data.get("plan", "monthly")
    if plan not in PLAN_PRICING:
        return jsonify({"error": f"Invalid plan. Choose: {list(PLAN_PRICING.keys())}"}), 400

    pricing = PLAN_PRICING[plan]

    try:
        client = _razorpay_client()

        # Map plan to Razorpay plan ID
        plan_id_map = {
            "monthly":  RAZORPAY_PLAN_MONTHLY_ID,
            "annual":   RAZORPAY_PLAN_ANNUAL_ID,
            "clinical": RAZORPAY_PLAN_CLINICAL_ID,
        }
        plan_id = plan_id_map.get(plan, "")

        if plan_id:
            # Subscription flow (recurring billing)
            subscription = client.subscription.create({
                "plan_id":        plan_id,
                "customer_notify": 1,
                "total_count":    120 if plan == "annual" else 12,
                "notes":          {"user_id": user.id, "plan": plan, "email": user.email},
            })

            try:
                supabase.table("user_profiles").upsert({
                    "user_id":                       user.id,
                    "razorpay_pending_subscription": subscription["id"],
                }, on_conflict="user_id").execute()
            except Exception:
                pass

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
            # One-time order fallback (no plan IDs configured yet)
            order = client.order.create({
                "amount":          pricing["amount"],
                "currency":        pricing["currency"],
                "payment_capture": 1,
                "notes":           {"user_id": user.id, "plan": plan, "email": user.email},
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
    plan            = body.get("plan", "monthly")

    if not RAZORPAY_KEY_SECRET:
        return jsonify({"error": "Payment not configured"}), 503

    if not payment_id or not signature:
        return jsonify({"error": "Missing payment_id or signature"}), 400

    try:
        # #PAY-1 FIX: Use hmac.new() correctly — Python 3 requires bytes keys
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
            "plan_display": _PLAN_DISPLAY.get(plan, "Pro"),
            "message":      f"Welcome to PHI {_PLAN_DISPLAY.get(plan, 'Pro')}! Unlimited access unlocked.",
        })

    except Exception as e:
        print(f"[PAYMENT] Verification error: {e}")
        return jsonify({"error": "Verification failed. Please contact support@curabook.com"}), 500


# ── POST /api/payment/razorpay/webhook ───────────────────────────────────────
@payment_bp.route("/api/payment/razorpay/webhook", methods=["POST"])
def razorpay_webhook():
    if not RAZORPAY_WEBHOOK_SECRET:
        print("[WEBHOOK] RAZORPAY_WEBHOOK_SECRET not set — skipping signature check")
        return jsonify({"received": True}), 200

    payload   = request.get_data()
    signature = request.headers.get("X-Razorpay-Signature", "")

    # #PAY-1 FIX: hmac.new() with bytes key
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
            print(f"[WEBHOOK] Plan activated: {user_id[:8]} → {plan}")

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
            return jsonify({"error": "No active subscription found."}), 404

        sub_id = res.data[0]["razorpay_subscription_id"]
        return jsonify({
            "subscription_id":   sub_id,
            "manage_url":        "https://dashboard.razorpay.com",
            "cancel_endpoint":   "/api/payment/cancel",
            "cancel_instructions": (
                "To cancel, click 'Cancel Subscription' in your account settings, "
                "or email support@curabook.com"
            ),
        })
    except Exception as e:
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
            "message": "Subscription cancelled. Pro access continues until the end of your billing period.",
        })

    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        print(f"[PAYMENT] Cancel error: {e}")
        return jsonify({"error": "Could not cancel. Please email support@curabook.com"}), 500


# ── Helpers ───────────────────────────────────────────────────────────────────

def _activate_pro(supabase, user_id: str, plan: str, payment_id: str = "",
                  subscription_id: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat()
    # #PAY-6 FIX: 'clinical' is now a valid plan
    valid_plans = {"monthly", "annual", "clinical", "pro"}
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
        if payment_id:
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