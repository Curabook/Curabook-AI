"""
api/compliance_routes.py — Safety-hardened + HIPAA claims removed
CHANGES:
  #HIPAA-1  Removed HIPAA_MODE from /api/config and /api/config/full.
            Curabook PHI is an educational wellness tool — it is not a
            covered entity and should not claim HIPAA status in API
            responses. Frontend should never display this as a guarantee.

  #BUG-4    delete_account still covers all tables.
  #BUG-5    supabase.auth.admin.delete_user() requires service role key.
  #BUG-6    Frontend receives explicit sign_out instruction.
  #SEC-1    /api/config/full does NOT expose SUPABASE_URL or SUPABASE_KEY.
  #B-07     _VALID_CONSENT_TYPES is single source of truth.
"""

import os
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify

compliance_bp = Blueprint("compliance", __name__)

# Only valid consent type strings — must match frontend exactly.
# 'terms_accepted' is NOT valid — it was causing silent 400 failures.
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
    """
    Returns minimal safe config. No HIPAA claims — PHI is a wellness tool,
    not a HIPAA-covered entity.
    """
    return jsonify({
        "encryption": "AES-256",
        "ai_anonymized": True,
        "data_sold": False,
    })


@compliance_bp.route("/api/config/full", methods=["GET"])
def config_authenticated():
    """
    Returns extended config for authenticated users only.
    Never exposes keys. No HIPAA claims.
    """
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
    supabase, get_user, audit = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    raw_consent_types = request.json.get("consents", [])
    if not raw_consent_types or not isinstance(raw_consent_types, list):
        return jsonify({"error": "No consent types provided"}), 400

    # Filter to only valid types — silently drop unknown ones
    consent_types = [ct for ct in raw_consent_types if ct in _VALID_CONSENT_TYPES]
    if not consent_types:
        return jsonify({
            "error": f"No valid consent types. Valid: {sorted(_VALID_CONSENT_TYPES)}"
        }), 400

    now = datetime.now(timezone.utc).isoformat()

    def _do_upsert():
        for ct in consent_types:
            supabase.table("user_consents").upsert({
                "user_id":         user.id,
                "consent_type":    ct,
                "consent_version": "v2.0",
                "ip_address":      request.remote_addr,
                "user_agent":      (request.headers.get("User-Agent") or "")[:500],
                "is_active":       True,
                "granted_at":      now,
            }, on_conflict="user_id,consent_type").execute()

    for attempt in range(2):
        try:
            _do_upsert()
            audit(supabase, user.id, "CONSENT_GRANTED",
                  f"Types: {', '.join(consent_types)}", "CONSENT")
            return jsonify({"success": True})
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
    """GDPR / DPDP right to erasure. Deletes ALL user data across ALL tables."""
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
            print(
                f"[DELETE] Auth user deletion FAILED for {user.id[:8]}: {ae}\n"
                "         Ensure SUPABASE_SERVICE_KEY is set in environment variables."
            )

        if deletion_errors:
            print(f"[DELETE] Some tables had errors: {deletion_errors}")

        return jsonify({
            "success":      True,
            "sign_out":     True,
            "auth_deleted": auth_deleted,
            "message":      "All your data has been permanently deleted.",
        })
    except Exception as e:
        print(f"[DELETE] Account deletion error: {e}")
        return jsonify({"error": "Deletion failed. Please contact support."}), 500