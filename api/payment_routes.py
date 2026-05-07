"""
api/payment_routes.py — Razorpay Payment Integration
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

# ── Canonical pricing (FIXED ANNUAL PRICING) ─────────────────────────────────
PLAN_PRICING = {
    "monthly":  {"amount": 4900,  "currency": "USD", "description": "PHI Shield Core Monthly — $49/mo"},
    "annual":   {"amount": 39900, "currency": "USD", "description": "PHI Shield Core Annual — $399/yr"},
    "clinical": {"amount": 9900,  "currency": "USD", "description": "PHI Shield Clinical Monthly — $99/mo"},
}

PLAN_DISPLAY = {
    "free":     "PHI Free",
    "monthly":  "Shield Core",
    "annual":   "Shield Core Annual",
    "clinical": "Shield Clinical",
    "pro":      "Shield Core",
}

_PRO_PLANS = {"monthly", "annual", "clinical", "pro"}


def _razorpay_client():
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise RuntimeError("Razorpay not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env")
    try:
        import razorpay
        return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    except ImportError:
        raise RuntimeError("razorpay package not installed.")


def _deps():
    from app import supabase
    from services.auth import get_authenticated_user
    from services.compliance import audit_log
    return supabase, get_authenticated_user, audit_log


def _is_pro(plan: str) -> bool:
    return (plan or "").lower() in _PRO_PLANS


@payment_bp.route("/api/payment/config", methods=["GET"])
def payment_config():
    return jsonify({
        "razorpay_configured": bool(RAZORPAY_KEY_ID),
        "razorpay_key_id":     RAZORPAY_KEY_ID,
        "plans": {
            "monthly":  {"amount": 49,  "currency": "USD", "label": "Shield Core — $49/mo"},
            "annual":   {"amount": 399, "currency": "USD", "label": "Shield Core Annual — $399/yr"},
            "clinical": {"amount": 99,  "currency": "USD", "label": "Shield Clinical — $99/mo"},
        },
    })


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

        return jsonify({
            "plan":              plan,
            "plan_display":      PLAN_DISPLAY.get(plan, plan.title()),
            "is_pro":            _is_pro(plan),
            "reports_remaining": int(remaining if remaining is not None else 1),
            "has_billing":       bool(row.get("razorpay_subscription_id")),
            "razorpay_configured": bool(RAZORPAY_KEY_ID),
        })
    except Exception as e:
        return jsonify({"plan": "free", "is_pro": False, "reports_remaining": 1, "razorpay_configured": bool(RAZORPAY_KEY_ID)})


@payment_bp.route("/api/payment/razorpay/order", methods=["POST"])
def create_razorpay_order():
    supabase, get_user, audit = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    if not RAZORPAY_KEY_ID:
        return jsonify({"error": "Payment not configured."}), 503

    data = request.json or {}
    plan = (data.get("plan", "monthly") or "monthly").lower()
    if plan not in PLAN_PRICING:
        return jsonify({"error": f"Invalid plan '{plan}'"}), 400

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
            subscription = client.subscription.create({
                "plan_id":         plan_id,
                "customer_notify": 1,
                "total_count":     1 if plan == "annual" else 12, # Razorpay sets cycles
                "notes":           {"user_id": user.id, "plan": plan},
            })
            supabase.table("user_profiles").upsert({
                "user_id": user.id,
                "razorpay_pending_subscription": subscription["id"],
            }, on_conflict="user_id").execute()

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
            order = client.order.create({
                "amount":          pricing["amount"],
                "currency":        pricing["currency"],
                "payment_capture": 1,
                "notes":           {"user_id": user.id, "plan": plan},
            })
            return jsonify({
                "razorpay_key_id": RAZORPAY_KEY_ID,
                "order_id":        order["id"],
                "amount":          pricing["amount"],
                "currency":        pricing["currency"],
                "description":     pricing["description"],
                "plan":            plan,
                "mode":            "one_time",
            })

    except Exception as e:
        return jsonify({"error": "Could not create payment session."}), 500


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

    if not payment_id or not signature:
        return jsonify({"error": "Missing payment_id or signature"}), 400

    try:
        message = f"{payment_id}|{subscription_id}" if subscription_id else f"{order_id}|{payment_id}"
        expected_sig = hmac.new(
            RAZORPAY_KEY_SECRET.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected_sig, signature):
            return jsonify({"error": "Payment verification failed."}), 400

        _activate_pro(supabase, user.id, plan, payment_id, subscription_id)
        
        return jsonify({
            "success":      True,
            "plan":         plan,
            "plan_display": PLAN_DISPLAY.get(plan, "Pro"),
            "message":      f"Welcome to PHI {PLAN_DISPLAY.get(plan, 'Pro')}!",
        })
    except Exception as e:
        return jsonify({"error": "Verification failed."}), 500


@payment_bp.route("/api/payment/razorpay/webhook", methods=["POST"])
def razorpay_webhook():
    if not RAZORPAY_WEBHOOK_SECRET:
        return jsonify({"error": "Webhook not configured"}), 503

    payload = request.get_data()
    signature = request.headers.get("X-Razorpay-Signature", "")

    expected = hmac.new(RAZORPAY_WEBHOOK_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, signature):
        return jsonify({"error": "Invalid signature"}), 400

    try:
        event = json.loads(payload)
        event_type = event.get("event", "")
        entity = event.get("payload", {}).get("subscription", {}).get("entity", {})
        
        from app import supabase
        if event_type in ("subscription.activated", "subscription.charged"):
            notes = entity.get("notes", {})
            if "user_id" in notes:
                _activate_pro(supabase, notes["user_id"], notes.get("plan", "monthly"), "", entity.get("id", ""))
        
        elif event_type in ("subscription.cancelled", "subscription.completed", "subscription.expired"):
            notes = entity.get("notes", {})
            user_id = notes.get("user_id") or _user_id_from_subscription(supabase, entity.get("id", ""))
            if user_id:
                _downgrade_to_free(supabase, user_id)

    except Exception:
        pass

    return jsonify({"received": True})

def _activate_pro(supabase, user_id: str, plan: str, payment_id: str = "", subscription_id: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat()
    normalized_plan = plan if plan in _PRO_PLANS else "monthly"
    
    update_data = {
        "user_id": user_id,
        "plan": normalized_plan,
        "reports_remaining": 9999,
        "updated_at": now,
    }
    if subscription_id: update_data["razorpay_subscription_id"] = subscription_id
    if payment_id: update_data["razorpay_last_payment_id"] = payment_id

    supabase.table("user_profiles").upsert(update_data, on_conflict="user_id").execute()

def _downgrade_to_free(supabase, user_id: str) -> None:
    supabase.table("user_profiles").upsert({
        "user_id": user_id,
        "plan": "free",
        "reports_remaining": 1,
        "razorpay_subscription_id": None,
    }, on_conflict="user_id").execute()

def _user_id_from_subscription(supabase, subscription_id: str) -> str:
    try:
        res = supabase.table("user_profiles").select("user_id").eq("razorpay_subscription_id", subscription_id).limit(1).execute()
        if res.data: return res.data[0]["user_id"]
    except Exception: pass
    return ""