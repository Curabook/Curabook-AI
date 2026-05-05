"""
api/auth_routes.py
─────────────────────────────────────────────────────────────────────────────
Conversation management endpoints.

FIX-CONFLICT-1: Removed duplicate routes that also exist in chat_routes.py.
  auth_routes.py ONLY handles: /conversation/create, /history, /conversation,
  /rename, /delete — but chat_routes.py NOW owns all of these.
  auth_routes.py is now a pure passthrough / legacy shim that does NOT
  register competing routes, preventing Werkzeug AssertionError on startup.

FIX-CONFLICT-2: Blueprint prefix set to empty string to avoid double-path.
"""

from flask import Blueprint, request, jsonify

auth_bp = Blueprint("auth", __name__)


def _deps():
    from app import supabase
    from services.auth       import get_authenticated_user
    from services.compliance import audit_log, verify_user_consent
    return supabase, get_authenticated_user, audit_log, verify_user_consent


# NOTE: All conversation CRUD routes (/history, /conversation, /rename, /delete,
# /conversation/create) are registered in chat_routes.py (chat_bp).
# auth_routes.py only provides routes that have no equivalent elsewhere.

# ── Consent verification endpoint ────────────────────────────────────────────

@auth_bp.route("/api/verify-session", methods=["GET", "POST"])
def verify_session():
    """
    Lightweight session check — returns user info if token is valid.
    Used by the frontend to validate auth state on page load.
    """
    supabase, get_user, audit, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"authenticated": False}), 401
    return jsonify({
        "authenticated": True,
        "user_id":       user.id,
        "email":         user.email,
    })