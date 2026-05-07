"""
services/billing_guard.py
─────────────────────────────────────────────────────────────────────────────
Middleware to enforce subscription limits and protect premium endpoints.
"""

from functools import wraps
from flask import jsonify
from services.auth import get_authenticated_user

def requires_pro(supabase):
    """Decorator to block Free users from accessing Premium features."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = get_authenticated_user(supabase)
            if not user:
                return jsonify({"error": "Unauthorized"}), 401
            
            # Fetch user plan
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
    
    # Pro plans have unlimited uploads (set to 9999 in payment_routes)
    if plan in ["monthly", "annual", "clinical", "pro"]:
        return True, ""
        
    # Free plan logic
    if remaining <= 0:
        return False, "Free plan limit reached. Please upgrade to upload more lab reports."
        
    return True, ""

def consume_upload_credit(supabase, user_id):
    """Call this AFTER a successful document upload to reduce the free credit."""
    supabase.rpc('decrement_report_credit', {'uid': user_id}).execute()