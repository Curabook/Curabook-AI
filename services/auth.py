"""
services/auth.py
─────────────────────────────────────────────────────────────────────────────
Authentication utilities — strictly validates Supabase JWT bearer tokens.
"""

from flask import request

def get_authenticated_user(supabase):
    """
    Validate the Authorization: Bearer <token> header statelessly against Supabase.
    Returns the Supabase User object on success, or None on failure.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None

    # Safely extract the token
    parts = auth_header.split(" ", 1)
    if len(parts) < 2:
        return None
        
    token = parts[1].strip()
    if not token:
        return None

    # 100% Stateless Validation:
    # get_user(token) directly verifies the JWT signature without relying on 
    # the server's cached session, which is what causes random logouts.
    for attempt in range(2):
        try:
            res = supabase.auth.get_user(token)
            if res and hasattr(res, 'user') and res.user:
                return res.user
        except Exception as e:
            err = str(e).lower()
            print(f"[AUTH] Token validation failed on attempt {attempt+1}: {e}")
            
            # Catch common network/protocol drops and retry once
            if attempt == 0 and any(w in err for w in ("disconnect", "protocol", "connect", "terminated", "reset", "eof")):
                print("[AUTH] Stale connection — retrying once…")
                continue
            
            # If the token is genuinely expired or invalid, return None so the 
            # frontend knows to execute a silent refresh.
            return None
            
    return None