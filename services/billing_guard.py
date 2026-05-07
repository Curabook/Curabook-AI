"""
services/billing_guard.py
─────────────────────────────────────────────────────────────────────────────
Middleware to enforce subscription limits and protect premium endpoints.
"""

from functools import wraps
from flask import jsonify
from datetime import datetime, timezone
from services.auth import get_authenticated_user

def requires_pro(supabase):
    """Decorator to block Free users from accessing Premium features."""
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
            if plan not in ["monthly", "annual", "clinical", "pro"]:
                return jsonify({
                    "error": "Upgrade required", 
                    "message": "This feature is locked to Shield Core and Clinical plans."
                }), 403
                
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def check_upload_limit(supabase, user_id):
    """Call this right before processing a PDF upload."""
    res = supabase.table("user_profiles").select("plan, reports_remaining").eq("user_id", user_id).execute()
    if not res.data:
        return False, "User profile not found."
        
    data = res.data[0]
    plan = data.get("plan", "free")
    remaining = data.get("reports_remaining", 0)
    
    if plan in ["monthly", "annual", "clinical", "pro"]:
        return True, ""
        
    if remaining <= 0:
        return False, "Free plan limit reached. Please upgrade to upload more lab reports."
        
    return True, ""

def consume_upload_credit(supabase, user_id):
    """Call this AFTER a successful document upload."""
    supabase.rpc('decrement_report_credit', {'uid': user_id}).execute()

def check_chat_limit(supabase, user_id):
    """Call this in your chat_routes.py before processing an LLM response."""
    res = supabase.table("user_profiles").select("plan").eq("user_id", user_id).execute()
    if not res.data:
        return False, "User profile not found."
        
    plan = res.data[0].get("plan", "free").lower()
    
    # Pro plans have unlimited messaging
    if plan in ["monthly", "annual", "clinical", "pro"]:
        return True, ""
        
    # Free users are capped at 15 messages per rolling day
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    chats_res = supabase.table("chats")\
        .select("id", count="exact")\
        .eq("user_id", user_id)\
        .eq("role", "user")\
        .gte("created_at", today)\
        .execute()
        
    msg_count = chats_res.count if chats_res.count is not None else 0
    if msg_count >= 15:
        return False, "Free plan daily limit reached (15 messages/day). Please upgrade to Shield Core for unlimited chat."
        
    return True, ""