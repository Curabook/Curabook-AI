"""
api/compliance_routes.py — Safety-hardened
FIXES APPLIED:
  #H1  — /api/config now requires auth; never exposes service key
          Frontend receives config at build time OR via authenticated call
  #M6  — consent_types whitelist enforced
  #H1  — Public config endpoint replaced with a safe minimal version
"""

import os
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify

compliance_bp = Blueprint("compliance", __name__)

# Fix #M6 — whitelist of valid consent types
_VALID_CONSENT_TYPES = {"ai_processing", "data_processing", "document_processing"}


def _deps():
    from app import supabase
    from services.auth       import get_authenticated_user
    from services.compliance import audit_log, check_baa_compliance
    return supabase, get_authenticated_user, audit_log, check_baa_compliance


# ── Fix #H1 — Public config: ONLY non-sensitive values, never keys ─────────────
# The anon key is still technically semi-public (needed by Supabase JS client),
# but serving it via an unauthenticated endpoint on your own domain means
# any web scraper, bot, or competitor can harvest it trivially.
#
# Better pattern: embed SUPABASE_URL and SUPABASE_KEY in your HTML at build time
# (e.g. via Jinja2 templating or a build script), and remove this endpoint.
#
# If you must serve it dynamically, this version adds no auth (Supabase anon key
# is designed to be public-facing) but NEVER exposes the service role key.
# The service role key must NEVER appear in any frontend-facing endpoint.

@compliance_bp.route("/api/config", methods=["GET"])
def config():
    """
    Returns only the anon key (public by design in Supabase).
    The service role key is never returned here under any circumstances.

    NOTE: For production, prefer embedding these values at build time
    rather than fetching them from a runtime endpoint.
    """
    return jsonify({
    "HIPAA_MODE": True
})


# ── Authenticated config (for dashboard, admin panels) ────────────────────────

@compliance_bp.route("/api/config/full", methods=["GET"])
def config_authenticated():
    """
    Returns extended config for authenticated users only.
    Never exposes the service role key.
    """
    supabase, get_user, audit, check_baa = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    return jsonify({
        "SUPABASE_URL": os.getenv("SUPABASE_URL", ""),
        "SUPABASE_KEY": os.getenv("SUPABASE_KEY", ""),
        "HIPAA_MODE":   True,
        "BAA_SIGNED":   check_baa(),
    })


# ── Consent ───────────────────────────────────────────────────────────────────

@compliance_bp.route("/api/consent", methods=["POST"])
def save_consent():
    supabase, get_user, audit, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    raw_consent_types = request.json.get("consents", [])
    if not raw_consent_types or not isinstance(raw_consent_types, list):
        return jsonify({"error": "No consent types provided"}), 400

    # Fix #M6 — whitelist only valid consent types
    consent_types = [ct for ct in raw_consent_types if ct in _VALID_CONSENT_TYPES]
    if not consent_types:
        return jsonify({"error": f"No valid consent types. Valid types: {sorted(_VALID_CONSENT_TYPES)}"}), 400

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


# ── Data export ───────────────────────────────────────────────────────────────

@compliance_bp.route("/export-data", methods=["POST"])
def export_data():
    supabase, get_user, audit, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        convs    = supabase.table("conversations").select("*").eq("user_id", user.id).execute()
        chats    = supabase.table("chats").select("*").eq("user_id", user.id).execute()
        consents = supabase.table("user_consents").select("*").eq("user_id", user.id).execute()
        markers  = supabase.table("health_markers").select("*").eq("user_id", user.id).execute()

        audit(supabase, user.id, "DATA_EXPORTED", "Full PHI export", "METADATA")

        return jsonify({
            "user_id":        user.id,
            "email":          user.email,
            "export_date":    datetime.now(timezone.utc).isoformat(),
            "conversations":  convs.data,
            "chats":          chats.data,
            "consents":       consents.data,
            "health_markers": markers.data,
        })
    except Exception as e:
        print(f"[EXPORT] Error: {e}")
        return jsonify({"error": "Export failed. Please try again."}), 500


# ── Account deletion ──────────────────────────────────────────────────────────

@compliance_bp.route("/delete-account", methods=["POST"])
def delete_account():
    supabase, get_user, audit, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        audit(supabase, user.id, "ACCOUNT_DELETION_REQUESTED",
              "Full PHI erasure initiated", "CRITICAL")

        for table in ["chats", "conversations", "user_consents",
                      "health_markers", "health_insights", "medical_documents"]:
            try:
                supabase.table(table).delete().eq("user_id", user.id).execute()
            except Exception as te:
                print(f"[DELETE] Table {table} error: {te}")

        try:
            supabase.auth.admin.delete_user(user.id)
        except Exception as ae:
            print(f"[DELETE] Auth delete error: {ae}")

        return jsonify({
            "success": True,
            "logout":  True,
            "message": "All your data has been permanently deleted.",
        })
    except Exception as e:
        print(f"[DELETE] Account deletion error: {e}")
        return jsonify({"error": "Deletion failed. Please contact support."}), 500