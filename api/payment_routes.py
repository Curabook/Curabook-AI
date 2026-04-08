"""
api/payment_routes.py — Stripe integration
FIXES:
  #BUG-3  FRONTEND_URL had "http://https://" double protocol — Stripe
          success/cancel redirects always broken. Fixed to clean default.
"""
import os
from flask import Blueprint, request, jsonify

payment_bp = Blueprint("payment", __name__)

STRIPE_SECRET_KEY     = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_MONTHLY  = os.getenv("STRIPE_PRICE_MONTHLY", "")
STRIPE_PRICE_ANNUAL   = os.getenv("STRIPE_PRICE_ANNUAL", "")
# FIX #BUG-3: was "http://https://curabook.com.onrender.com:5500" (double protocol, wrong host)
FRONTEND_URL          = os.getenv("FRONTEND_URL", "https://curabook.com")


def _stripe():
    if not STRIPE_SECRET_KEY:
        raise RuntimeError("STRIPE_SECRET_KEY not set in environment")
    import stripe as _s
    _s.api_key = STRIPE_SECRET_KEY
    return _s


def _deps():
    from app import supabase
    from services.auth       import get_authenticated_user
    from services.compliance import audit_log
    return supabase, get_authenticated_user, audit_log


@payment_bp.route("/api/payment/checkout", methods=["POST"])
def create_checkout():
    supabase, get_user, audit = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data     = request.json or {}
    plan     = data.get("plan", "monthly")
    price_id = STRIPE_PRICE_ANNUAL if plan == "annual" else STRIPE_PRICE_MONTHLY

    if not price_id:
        return jsonify({"error": "Payment not configured yet."}), 503

    try:
        stripe      = _stripe()
        customer_id = _get_or_create_customer(stripe, supabase, user)

        session = stripe.checkout.Session.create(
            customer             = customer_id,
            payment_method_types = ["card"],
            line_items           = [{"price": price_id, "quantity": 1}],
            mode                 = "subscription",
            success_url          = f"{FRONTEND_URL}/?payment=success",
            cancel_url           = f"{FRONTEND_URL}/?payment=cancelled",
            metadata             = {"user_id": user.id, "plan": plan},
            allow_promotion_codes = True,
        )
        audit(supabase, user.id, "CHECKOUT_INITIATED", f"plan={plan}", "BILLING")
        return jsonify({"checkout_url": session.url, "session_id": session.id})
    except Exception as e:
        print(f"[PAYMENT] Checkout error: {e}")
        return jsonify({"error": "Could not create payment session."}), 500


@payment_bp.route("/api/payment/portal", methods=["POST"])
def customer_portal():
    supabase, get_user, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        stripe      = _stripe()
        customer_id = _get_stripe_customer_id(supabase, user.id)
        if not customer_id:
            return jsonify({"error": "No billing account found."}), 404
        session = stripe.billing_portal.Session.create(
            customer=customer_id, return_url=f"{FRONTEND_URL}/")
        return jsonify({"portal_url": session.url})
    except Exception as e:
        return jsonify({"error": "Could not open billing portal."}), 500


@payment_bp.route("/api/payment/status", methods=["GET"])
def payment_status():
    supabase, get_user, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        res = supabase.table("user_profiles").select("plan,reports_remaining,stripe_customer_id")\
            .eq("user_id", user.id).limit(1).execute()
        if not res.data:
            return jsonify({"plan": "free", "reports_remaining": 1, "is_pro": False})
        row = res.data[0]
        plan = row.get("plan", "free")
        return jsonify({
            "plan": plan,
            "is_pro": plan in ("pro", "annual"),
            "reports_remaining": row.get("reports_remaining", 1),
            "has_billing": bool(row.get("stripe_customer_id")),
        })
    except Exception as e:
        return jsonify({"plan": "free", "reports_remaining": 1, "is_pro": False})


@payment_bp.route("/api/payment/webhook", methods=["POST"])
def stripe_webhook():
    if not STRIPE_WEBHOOK_SECRET:
        return jsonify({"error": "Webhook not configured"}), 503
    payload = request.get_data()
    sig     = request.headers.get("Stripe-Signature", "")
    try:
        stripe = _stripe()
        event  = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        print(f"[WEBHOOK] Signature failed: {e}")
        return jsonify({"error": "Invalid signature"}), 400

    import app as _app
    supabase   = _app.supabase
    event_type = event["type"]
    print(f"[WEBHOOK] {event_type}")

    if event_type in ("checkout.session.completed", "invoice.payment_succeeded"):
        obj     = event["data"]["object"]
        user_id = (obj.get("metadata") or {}).get("user_id")
        if user_id:
            _activate_pro(supabase, user_id)
    elif event_type in ("customer.subscription.deleted", "invoice.payment_failed"):
        obj    = event["data"]["object"]
        cus_id = obj.get("customer")
        if cus_id:
            user_id = _user_id_from_customer(supabase, cus_id)
            if user_id:
                _downgrade_to_free(supabase, user_id)

    return jsonify({"received": True})


def _get_or_create_customer(stripe, supabase, user):
    customer_id = _get_stripe_customer_id(supabase, user.id)
    if customer_id:
        return customer_id
    customer = stripe.Customer.create(email=user.email, metadata={"user_id": user.id})
    try:
        supabase.table("user_profiles").upsert(
            {"user_id": user.id, "stripe_customer_id": customer.id},
            on_conflict="user_id").execute()
    except Exception as e:
        print(f"[PAYMENT] Save customer ID error: {e}")
    return customer.id


def _get_stripe_customer_id(supabase, user_id):
    try:
        res = supabase.table("user_profiles").select("stripe_customer_id")\
            .eq("user_id", user_id).limit(1).execute()
        if res.data:
            return res.data[0].get("stripe_customer_id", "")
    except Exception:
        pass
    return ""


def _user_id_from_customer(supabase, customer_id):
    try:
        res = supabase.table("user_profiles").select("user_id")\
            .eq("stripe_customer_id", customer_id).limit(1).execute()
        if res.data:
            return res.data[0]["user_id"]
    except Exception:
        pass
    return ""


def _activate_pro(supabase, user_id):
    try:
        supabase.table("user_profiles").upsert(
            {"user_id": user_id, "plan": "pro", "reports_remaining": 9999},
            on_conflict="user_id").execute()
        print(f"[WEBHOOK] ✅ Pro activated: {user_id[:8]}")
    except Exception as e:
        print(f"[WEBHOOK] Activate error: {e}")


def _downgrade_to_free(supabase, user_id):
    try:
        supabase.table("user_profiles").upsert(
            {"user_id": user_id, "plan": "free", "reports_remaining": 1},
            on_conflict="user_id").execute()
        print(f"[WEBHOOK] Downgraded: {user_id[:8]}")
    except Exception as e:
        print(f"[WEBHOOK] Downgrade error: {e}")