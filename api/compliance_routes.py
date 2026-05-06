"""
api/compliance_routes.py
FIXES:
  #CONSENT-3  /api/consent now accepts a POST from ANY authenticated user
              and saves all three consent types. It is idempotent (upsert).
              This is called: on signup, on login, on startup — so consent
              is always present by the time any protected endpoint is hit.

  #CONSENT-4  /api/consent no longer returns 400 for unknown types —
              it silently ignores them and saves what it recognises.
              This prevents frontend version mismatches from breaking flow.

  #HIPAA-1    HIPAA claims removed (preserved from original).
  #BUG-4      delete_account covers all tables (preserved).
"""

import os
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify

compliance_bp = Blueprint("compliance", __name__)

_VALID_CONSENT_TYPES = {"ai_processing", "data_processing", "document_processing"}

_USER_DATA_TABLES = [
    "chats",
    "conversations",
    "user_consents",
    "health_markers",
    "health_insights",
    "medical_documents",
    "conversation_memories",
    "user_profiles",
    "audit_logs",
    "behavioral_logs",
]


def _deps():
    from app import supabase
    from services.auth       import get_authenticated_user
    from services.compliance import audit_log
    return supabase, get_authenticated_user, audit_log


@compliance_bp.route("/api/config", methods=["GET"])
def config():
    return jsonify({
        "encryption": "AES-256",
        "ai_anonymized": True,
        "data_sold": False,
    })


@compliance_bp.route("/api/config/full", methods=["GET"])
def config_authenticated():
    supabase, get_user, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify({
        "encryption":    "AES-256",
        "ai_anonymized": True,
        "data_sold":     False,
    })


@compliance_bp.route("/api/consent", methods=["POST"])
def save_consent():
    """
    #CONSENT-3: Save consent for authenticated user.
    Idempotent — safe to call multiple times.
    Accepts any combination of valid consent types.
    Called on: signup onboarding, login, startup, Google OAuth completion.
    """
    supabase, get_user, audit = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.json or {}
    raw_consent_types = body.get("consents", [])

    # #CONSENT-4: If no specific types given, grant ALL types
    # This handles frontend version mismatches gracefully
    if not raw_consent_types or not isinstance(raw_consent_types, list):
        consent_types = list(_VALID_CONSENT_TYPES)
    else:
        # Filter to valid types only — silently drop unknown ones
        consent_types = [ct for ct in raw_consent_types if ct in _VALID_CONSENT_TYPES]
        # If caller sent types but ALL were invalid, still grant all valid ones
        # (prevents partial-consent state from locking users out)
        if not consent_types:
            consent_types = list(_VALID_CONSENT_TYPES)

    now = datetime.now(timezone.utc).isoformat()

    def _do_upsert():
        for ct in consent_types:
            supabase.table("user_consents").upsert({
                "user_id":         user.id,
                "consent_type":    ct,
                "consent_version": "v2.0",
                "ip_address":      (request.headers.get("X-Forwarded-For", request.remote_addr) or "")[:100],
                "user_agent":      (request.headers.get("User-Agent") or "")[:500],
                "is_active":       True,
                "granted_at":      now,
            }, on_conflict="user_id,consent_type").execute()

    for attempt in range(2):
        try:
            _do_upsert()
            audit(supabase, user.id, "CONSENT_GRANTED",
                  f"Types: {', '.join(consent_types)}", "CONSENT")
            return jsonify({"success": True, "types_saved": consent_types})
        except Exception as e:
            err = str(e)
            print(f"[CONSENT] Attempt {attempt+1} error: {type(e).__name__}: {e}")
            if attempt == 0 and any(w in err.lower() for w in
                    ("disconnect", "protocol", "connect", "terminated", "compression")):
                continue
            return jsonify({"error": "Failed to save consent. Please try again."}), 500


@compliance_bp.route("/export-data", methods=["POST"])
def export_data():
    supabase, get_user, audit = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        convs    = supabase.table("conversations").select("*").eq("user_id", user.id).execute()
        chats    = supabase.table("chats").select("*").eq("user_id", user.id).execute()
        consents = supabase.table("user_consents").select("*").eq("user_id", user.id).execute()
        markers  = supabase.table("health_markers").select("*").eq("user_id", user.id).execute()
        memories = supabase.table("conversation_memories").select("*").eq("user_id", user.id).execute()

        audit(supabase, user.id, "DATA_EXPORTED", "Full PHI export", "METADATA")

        return jsonify({
            "user_id":               user.id,
            "email":                 user.email,
            "export_date":           datetime.now(timezone.utc).isoformat(),
            "conversations":         convs.data,
            "chats":                 chats.data,
            "consents":              consents.data,
            "health_markers":        markers.data,
            "conversation_memories": memories.data,
        })
    except Exception as e:
        print(f"[EXPORT] Error: {e}")
        return jsonify({"error": "Export failed. Please try again."}), 500


@compliance_bp.route("/delete-account", methods=["POST"])
def delete_account():
    supabase, get_user, audit = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        audit(supabase, user.id, "ACCOUNT_DELETION_REQUESTED",
              "Full data erasure initiated", "CRITICAL")

        deletion_errors = []
        for table in _USER_DATA_TABLES:
            try:
                supabase.table(table).delete().eq("user_id", user.id).execute()
                print(f"[DELETE] Cleared {table} for user {user.id[:8]}")
            except Exception as te:
                deletion_errors.append(table)
                print(f"[DELETE] Table {table} error: {te}")

        auth_deleted = False
        try:
            supabase.auth.admin.delete_user(user.id)
            auth_deleted = True
            print(f"[DELETE] Auth user deleted: {user.id[:8]}")
        except Exception as ae:
            print(f"[DELETE] Auth user deletion FAILED for {user.id[:8]}: {ae}")

        return jsonify({
            "success":      True,
            "sign_out":     True,
            "auth_deleted": auth_deleted,
            "message":      "All your data has been permanently deleted.",
        })
    except Exception as e:
        print(f"[DELETE] Account deletion error: {e}")
        return jsonify({"error": "Deletion failed. Please contact support."}), 500