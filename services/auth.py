"""
services/auth.py
─────────────────────────────────────────────────────────────────────────────
Authentication utilities — validates Supabase JWT bearer tokens.
"""

from flask import request


def get_authenticated_user(supabase):
    """
    Validate the Authorization: Bearer <token> header against Supabase.

    Returns the Supabase User object on success, or None on failure.
    Every protected endpoint calls this first.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return None

    for attempt in range(2):
        try:
            res = supabase.auth.get_user(token)
            return res.user
        except Exception as e:
            err = str(e)
            print(f"[AUTH] Token validation failed: {e}")
            if attempt == 0 and any(w in err.lower() for w in (
                "disconnect", "protocol", "connect", "terminated", "reset", "eof"
            )):
                print("[AUTH] Stale connection — retrying once…")
                continue
            return None
    return None