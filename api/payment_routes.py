"""
api/payment_routes.py — Razorpay International Integration
════════════════════════════════════════════════════════════════════════════
Replaces Stripe with Razorpay for international payments.

PLAN STRUCTURE:
  Free tier  — 1 report upload, unlimited chat, no marker memory in AI
  Pro monthly — $20/month — unlimited reports, full AI memory, PA support
  Pro annual  — $180/year ($15/month) — same as monthly + discount

ENDPOINTS:
  POST /api/payment/razorpay/order   — create Razorpay order
  POST /api/payment/razorpay/verify  — verify payment signature
  POST /api/payment/razorpay/webhook — handle Razorpay webhooks
  GET  /api/payment/status           — get user's current plan
  POST /api/payment/portal           — cancel/manage subscription info

REQUIRED ENV VARS:
  RAZORPAY_KEY_ID      — from Razorpay dashboard (rzp_live_xxx or rzp_test_xxx)
  RAZORPAY_KEY_SECRET  — from Razorpay dashboard
  RAZORPAY_PLAN_MONTHLY_ID — Razorpay plan ID for monthly subscription
  RAZORPAY_PLAN_ANNUAL_ID  — Razorpay plan ID for annual subscription
  FRONTEND_URL         — e.g. https://curabook.com

SETUP STEPS (Razorpay Dashboard):
  1. Create a Product → Plans → Monthly ($20) and Annual ($180)
  2. Copy Plan IDs into env vars above
  3. Add webhook URL: https://api.curabook.com/api/payment/razorpay/webhook
  4. Subscribe to events: subscription.activated, subscription.charged,
     subscription.cancelled, payment.failed
════════════════════════════════════════════════════════════════════════════
"""

import os
import hmac
import hashlib
import json
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify

payment_bp = Blueprint("payment", __name__)

RAZORPAY_KEY_ID           = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET       = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET   = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
RAZORPAY_PLAN_MONTHLY_ID  = os.getenv("RAZORPAY_PLAN_MONTHLY_ID", "")
RAZORPAY_PLAN_ANNUAL_ID   = os.getenv("RAZORPAY_PLAN_ANNUAL_ID", "")
FRONTEND_URL              = os.getenv("FRONTEND_URL", "https://curabook.com")

# Pricing in paise (1 USD = ~83 INR, but Razorpay supports USD directly)
# Set currency to USD for international; INR for India
PLAN_PRICING = {
    "monthly": {"amount": 2000, "currency": "USD", "description": "PHI Pro Monthly"},
    "annual":  {"amount": 18000, "currency": "USD", "description": "PHI Pro Annual"},
}


def _razorpay_client():
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise RuntimeError("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET not set in environment")
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
    """Return current user plan and upload quota."""
    supabase, get_user, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        res = (supabase.table("user_profiles")
               .select("plan,reports_remaining,razorpay_subscription_id,razorpay_customer_id")
               .eq("user_id", user.id).limit(1).execute())

        if not res.data:
            # New user — create free profile
            supabase.table("user_profiles").upsert({
                "user_id": user.id,
                "plan": "free",
                "reports_remaining": 1,
            }, on_conflict="user_id").execute()
            return jsonify({
                "plan": "free",
                "is_pro": False,
                "reports_remaining": 1,
                "has_billing": False,
            })

        row = res.data[0]
        plan = row.get("plan", "free")
        is_pro = plan in ("pro", "annual")
        return jsonify({
            "plan": plan,
            "is_pro": is_pro,
            "reports_remaining": row.get("reports_remaining", 1),
            "has_billing": bool(row.get("razorpay_subscription_id")),
        })
    except Exception as e:
        print(f"[PAYMENT] Status error: {e}")
        return jsonify({"plan": "free", "is_pro": False, "reports_remaining": 1})


# ── POST /api/payment/razorpay/order ─────────────────────────────────────────
@payment_bp.route("/api/payment/razorpay/order", methods=["POST"])
def create_razorpay_order():
    """
    Create a Razorpay subscription order.

    For subscriptions, Razorpay uses a two-step flow:
      1. Create subscription → get subscription_id
      2. Frontend opens Razorpay checkout with subscription_id
      3. User pays → webhook fires → we activate Pro

    If plan IDs are not configured, falls back to one-time payment order.
    """
    supabase, get_user, audit = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    plan = data.get("plan", "monthly")

    if not RAZORPAY_KEY_ID:
        return jsonify({"error": "Payment not configured yet. Contact support."}), 503

    try:
        client = _razorpay_client()
        pricing = PLAN_PRICING.get(plan, PLAN_PRICING["monthly"])

        # Try subscription flow first (requires plan IDs)
        plan_id = RAZORPAY_PLAN_MONTHLY_ID if plan == "monthly" else RAZORPAY_PLAN_ANNUAL_ID

        if plan_id:
            # Subscription-based billing
            subscription = client.subscription.create({
                "plan_id": plan_id,
                "customer_notify": 1,
                "total_count": 120 if plan == "annual" else 12,  # max cycles
                "notes": {
                    "user_id": user.id,
                    "plan": plan,
                    "email": user.email,
                }
            })

            # Store subscription ID for webhook matching
            try:
                supabase.table("user_profiles").upsert({
                    "user_id": user.id,
                    "razorpay_pending_subscription": subscription["id"],
                }, on_conflict="user_id").execute()
            except Exception:
                pass

            audit(supabase, user.id, "RAZORPAY_SUBSCRIPTION_CREATED",
                  f"plan={plan} sub={subscription['id']}", "BILLING")

            return jsonify({
                "razorpay_key_id": RAZORPAY_KEY_ID,
                "subscription_id": subscription["id"],
                "amount": pricing["amount"],
                "currency": pricing["currency"],
                "plan": plan,
                "mode": "subscription",
            })

        else:
            # Fallback: one-time payment order (no plan IDs set)
            order = client.order.create({
                "amount": pricing["amount"],
                "currency": pricing["currency"],
                "payment_capture": 1,
                "notes": {
                    "user_id": user.id,
                    "plan": plan,
                    "email": user.email,
                }
            })

            audit(supabase, user.id, "RAZORPAY_ORDER_CREATED",
                  f"plan={plan} order={order['id']}", "BILLING")

            return jsonify({
                "razorpay_key_id": RAZORPAY_KEY_ID,
                "order_id": order["id"],
                "amount": pricing["amount"],
                "currency": pricing["currency"],
                "plan": plan,
                "mode": "one_time",
            })

    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        print(f"[PAYMENT] Razorpay order error: {e}")
        return jsonify({"error": "Could not create payment session."}), 500


# ── POST /api/payment/razorpay/verify ────────────────────────────────────────
@payment_bp.route("/api/payment/razorpay/verify", methods=["POST"])
def verify_razorpay_payment():
    """
    Verify Razorpay payment signature after successful checkout.
    Called by the frontend handler function after payment completes.

    Verifies HMAC-SHA256 signature to prevent spoofing.
    Activates Pro immediately on success.
    """
    supabase, get_user, audit = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.json or {}
    order_id      = body.get("order_id", "")
    payment_id    = body.get("payment_id", "")
    signature     = body.get("signature", "")
    subscription_id = body.get("subscription_id", "")
    plan          = body.get("plan", "monthly")

    if not RAZORPAY_KEY_SECRET:
        return jsonify({"error": "Payment not configured"}), 503

    try:
        # Verify signature
        if subscription_id:
            # Subscription payment verification
            message = f"{payment_id}|{subscription_id}"
        else:
            # One-time payment verification
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
            return jsonify({"error": "Payment verification failed."}), 400

        # Signature valid — activate Pro
        _activate_pro(supabase, user.id, plan, payment_id, subscription_id)
        audit(supabase, user.id, "RAZORPAY_PAYMENT_VERIFIED",
              f"plan={plan} payment={payment_id}", "BILLING")

        return jsonify({
            "success": True,
            "plan": plan,
            "message": "Welcome to PHI Pro!",
        })

    except Exception as e:
        print(f"[PAYMENT] Verification error: {e}")
        return jsonify({"error": "Verification failed. Please contact support."}), 500


# ── POST /api/payment/razorpay/webhook ───────────────────────────────────────
@payment_bp.route("/api/payment/razorpay/webhook", methods=["POST"])
def razorpay_webhook():
    """
    Handle Razorpay webhook events.

    Razorpay signs webhooks with X-Razorpay-Signature header.
    We verify this before processing any event.

    Handled events:
      - subscription.activated   → activate Pro
      - subscription.charged     → extend Pro (recurring payment)
      - subscription.cancelled   → downgrade to Free
      - subscription.completed   → downgrade to Free
      - payment.failed           → log, do NOT downgrade (grace period)
    """
    if not RAZORPAY_WEBHOOK_SECRET:
        print("[WEBHOOK] RAZORPAY_WEBHOOK_SECRET not set — skipping verification")
        return jsonify({"received": True}), 200

    payload   = request.get_data()
    signature = request.headers.get("X-Razorpay-Signature", "")

    # Verify webhook signature
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

    event_type = event.get("event", "")
    entity     = event.get("payload", {}).get("subscription", {}).get("entity", {})
    payment_entity = event.get("payload", {}).get("payment", {}).get("entity", {})

    print(f"[WEBHOOK] Razorpay event: {event_type}")

    if event_type in ("subscription.activated", "subscription.charged"):
        notes   = entity.get("notes", {})
        user_id = notes.get("user_id", "")
        plan    = notes.get("plan", "monthly")
        sub_id  = entity.get("id", "")
        if user_id:
            _activate_pro(supabase, user_id, plan, "", sub_id)
            print(f"[WEBHOOK] ✅ Pro activated for {user_id[:8]}")

    elif event_type in ("subscription.cancelled", "subscription.completed",
                        "subscription.expired"):
        notes   = entity.get("notes", {})
        user_id = notes.get("user_id", "")
        sub_id  = entity.get("id", "")
        if not user_id:
            # Lookup by subscription ID
            user_id = _user_id_from_subscription(supabase, sub_id)
        if user_id:
            _downgrade_to_free(supabase, user_id)
            print(f"[WEBHOOK] Downgraded: {user_id[:8]}")

    elif event_type == "payment.failed":
        # Log only — grace period before downgrade
        notes   = payment_entity.get("notes", {})
        user_id = notes.get("user_id", "")
        if user_id:
            try:
                supabase.table("audit_logs").insert({
                    "user_id": user_id,
                    "action": "RAZORPAY_PAYMENT_FAILED",
                    "detail": f"payment_id={payment_entity.get('id', '')}",
                    "category": "BILLING",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
            except Exception:
                pass
        print(f"[WEBHOOK] Payment failed for user {user_id[:8] if user_id else 'unknown'}")

    return jsonify({"received": True})


# ── POST /api/payment/portal ──────────────────────────────────────────────────
@payment_bp.route("/api/payment/portal", methods=["POST"])
def customer_portal():
    """
    Return subscription management info.
    Razorpay doesn't have a hosted portal like Stripe, so we return
    the Razorpay dashboard URL and subscription details.
    """
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

        # For Razorpay, subscriptions are managed via their dashboard
        # You can also use Razorpay's subscription management API
        return jsonify({
            "subscription_id": sub_id,
            "manage_url": "https://dashboard.razorpay.com",
            "cancel_instructions": (
                "To cancel your subscription, visit the Razorpay portal "
                "or contact support@curabook.com"
            ),
        })
    except Exception as e:
        return jsonify({"error": "Could not load billing details."}), 500


# ── POST /api/payment/cancel ──────────────────────────────────────────────────
@payment_bp.route("/api/payment/cancel", methods=["POST"])
def cancel_subscription():
    """Cancel active Razorpay subscription at end of current period."""
    supabase, get_user, audit = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        res = (supabase.table("user_profiles")
               .select("razorpay_subscription_id")
               .eq("user_id", user.id).limit(1).execute())

        if not res.data or not res.data[0].get("razorpay_subscription_id"):
            return jsonify({"error": "No active subscription."}), 404

        sub_id = res.data[0]["razorpay_subscription_id"]
        client = _razorpay_client()

        # Cancel at end of billing cycle (cancel_at_cycle_end=1)
        client.subscription.cancel(sub_id, {"cancel_at_cycle_end": 1})

        audit(supabase, user.id, "RAZORPAY_SUBSCRIPTION_CANCEL_REQUESTED",
              f"sub={sub_id}", "BILLING")

        return jsonify({
            "success": True,
            "message": "Subscription cancelled. You'll retain Pro access until the end of your billing period.",
        })

    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        print(f"[PAYMENT] Cancel error: {e}")
        return jsonify({"error": "Could not cancel subscription. Please contact support."}), 500


# ── Helpers ───────────────────────────────────────────────────────────────────

def _activate_pro(supabase, user_id: str, plan: str, payment_id: str = "",
                  subscription_id: str = "") -> None:
    """Activate Pro plan for a user."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        update_data = {
            "user_id": user_id,
            "plan": plan if plan in ("monthly", "annual") else "pro",
            "reports_remaining": 9999,
            "updated_at": now,
        }
        if subscription_id:
            update_data["razorpay_subscription_id"] = subscription_id
        if payment_id:
            update_data["razorpay_last_payment_id"] = payment_id

        supabase.table("user_profiles").upsert(
            update_data, on_conflict="user_id"
        ).execute()

        # Log to audit
        supabase.table("audit_logs").insert({
            "user_id": user_id,
            "action": "PRO_ACTIVATED",
            "detail": f"plan={plan} payment={payment_id} sub={subscription_id}",
            "category": "BILLING",
            "created_at": now,
        }).execute()

        print(f"[PAYMENT] ✅ Pro activated: {user_id[:8]} plan={plan}")
    except Exception as e:
        print(f"[PAYMENT] Activate error: {e}")


def _downgrade_to_free(supabase, user_id: str) -> None:
    """Downgrade user to free plan."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        supabase.table("user_profiles").upsert({
            "user_id": user_id,
            "plan": "free",
            "reports_remaining": 1,
            "razorpay_subscription_id": None,
            "updated_at": now,
        }, on_conflict="user_id").execute()

        supabase.table("audit_logs").insert({
            "user_id": user_id,
            "action": "DOWNGRADED_TO_FREE",
            "detail": "Subscription ended or cancelled",
            "category": "BILLING",
            "created_at": now,
        }).execute()

        print(f"[PAYMENT] Downgraded to free: {user_id[:8]}")
    except Exception as e:
        print(f"[PAYMENT] Downgrade error: {e}")


def _user_id_from_subscription(supabase, subscription_id: str) -> str:
    """Look up user_id from a Razorpay subscription ID."""
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