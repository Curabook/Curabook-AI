"""
services/billing_guard.py
Plans: free, monthly, annual, pro (founder/admin grant only — same tier as paid).
Free tier = limited reports + limited daily chats. No trial. No clinical tier.
"""

from functools import wraps
from flask import jsonify
from datetime import datetime, timezone
from services.auth import get_authenticated_user

FREE_REPORT_LIMIT = 3          # total lifetime lab report uploads on free plan
FREE_CHAT_DAILY_LIMIT = 15

_PRO_PLANS = ["monthly", "annual", "pro"]

def requires_pro(supabase):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = get_authenticated_user(supabase)
            if not user:
                return jsonify({"error": "Unauthorized"}), 401

            res = supabase.table("user_profiles").select("plan").eq("user_id", user.id).execute()
            if not res.data:
                return jsonify({"error": "Profile not found"}), 404

            plan = res.data[0].get("plan", "free").lower()
            if plan not in _PRO_PLANS:
                return jsonify({
                    "error": "Upgrade required",
                    "message": "This feature is locked to Shield plans. Upgrade to unlock."
                }), 403

            return f(*args, **kwargs)
        return decorated_function
    return decorator


def check_upload_limit(supabase, user_id):
    """Call before processing a PDF/report upload."""
    res = supabase.table("user_profiles").select("plan, reports_remaining").eq("user_id", user_id).execute()
    if not res.data:
        return False, "User profile not found."

    data = res.data[0]
    plan = data.get("plan", "free")

    if plan in _PRO_PLANS:
        return True, ""

    remaining = data.get("reports_remaining", FREE_REPORT_LIMIT)
    if remaining is None:
        remaining = FREE_REPORT_LIMIT

    if remaining <= 0:
        return False, f"Free plan limit reached ({FREE_REPORT_LIMIT} lab reports). Upgrade to Shield for unlimited uploads."

    return True, ""


def consume_upload_credit(supabase, user_id):
    """Call after a successful document upload."""
    try:
        supabase.rpc('decrement_report_credit', {'uid': user_id}).execute()
    except Exception:
        res = supabase.table("user_profiles").select("reports_remaining").eq("user_id", user_id).execute()
        if res.data:
            remaining = max(0, (res.data[0].get("reports_remaining") or FREE_REPORT_LIMIT) - 1)
            supabase.table("user_profiles").update({"reports_remaining": remaining}).eq("user_id", user_id).execute()


def check_chat_limit(supabase, user_id):
    """Call in chat_routes.py before processing an LLM response."""
    res = supabase.table("user_profiles").select("plan").eq("user_id", user_id).execute()
    if not res.data:
        return False, "User profile not found."

    plan = res.data[0].get("plan", "free").lower()

    if plan in _PRO_PLANS:
        return True, ""

    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    chats_res = supabase.table("chats")\
        .select("id", count="exact")\
        .eq("user_id", user_id)\
        .eq("role", "user")\
        .gte("created_at", today)\
        .execute()

    msg_count = chats_res.count if chats_res.count is not None else 0
    if msg_count >= FREE_CHAT_DAILY_LIMIT:
        return False, f"Free plan daily limit reached ({FREE_CHAT_DAILY_LIMIT} messages/day). Upgrade to Shield for unlimited chat."

    return True, ""