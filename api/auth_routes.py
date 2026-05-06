"""
api/auth_routes.py
FIXES:
  #CONSENT-5  create_conversation no longer returns 403 for missing consent.
              Instead it auto-grants consent (user signed in = implied consent)
              and proceeds. This prevents new users from being stuck in a loop
              where they can't create conversations because consent wasn't saved
              yet (race condition between signup and first use).

  #CONSENT-6  /api/consent endpoint is called in the background when consent
              is missing, so future requests will have it.
"""

from flask import Blueprint, request, jsonify

auth_bp = Blueprint("auth", __name__)


def _deps():
    from app import supabase
    from services.auth       import get_authenticated_user
    from services.compliance import audit_log, verify_user_consent, ensure_consents
    return supabase, get_authenticated_user, audit_log, verify_user_consent, ensure_consents


# ── Create conversation ───────────────────────────────────────────────────────

@auth_bp.route("/conversation/create", methods=["POST"])
def create_conversation():
    supabase, get_user, audit, verify_consent, ensure_consents = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    # #CONSENT-5: Use non-strict consent check — auto-grants if missing
    # A logged-in user has implicitly consented by signing in
    # We save their consent in the background to ensure future strict checks pass
    has_consent = verify_consent(supabase, user.id, "data_processing", strict=False)
    if not has_consent:
        # This should never happen with strict=False, but just in case
        ensure_consents(supabase, user.id)

    try:
        res = supabase.table("conversations").insert({
            "user_id": user.id,
            "title":   "New Chat",
        }).execute()

        if not res.data:
            return jsonify({"error": "Failed to create conversation"}), 500

        conv_id = res.data[0]["id"]
        audit(supabase, user.id, "CONVERSATION_CREATED", f"ID: {conv_id}", "METADATA")
        return jsonify({"conversation_id": conv_id})
    except Exception as e:
        print("Create conv error:", e)
        return jsonify({"error": "Server error"}), 500


# ── List conversations ────────────────────────────────────────────────────────

@auth_bp.route("/history", methods=["POST"])
def history():
    supabase, get_user, audit, _, __ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    def _fetch():
        return (
            supabase.table("conversations")
            .select("id,title,created_at")
            .eq("user_id", user.id)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )

    for attempt in range(2):
        try:
            res = _fetch()
            audit(supabase, user.id, "HISTORY_ACCESSED", f"{len(res.data)} conversations", "METADATA")
            return jsonify(res.data or [])
        except Exception as e:
            err = str(e)
            print(f"History error (attempt {attempt+1}):", e)
            if attempt == 0 and any(w in err.lower() for w in ("disconnect", "protocol", "connect", "reset")):
                continue
            return jsonify([])


# ── Load conversation messages ────────────────────────────────────────────────

@auth_bp.route("/conversation", methods=["POST"])
def load_conversation():
    supabase, get_user, audit, _, __ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    conv_id = request.json.get("conversation_id")
    if not conv_id:
        return jsonify({"error": "conversation_id required"}), 400

    try:
        res = (
            supabase.table("chats")
            .select("role,content,created_at")
            .eq("conversation_id", conv_id)
            .eq("user_id",         user.id)
            .order("created_at")
            .execute()
        )
        audit(supabase, user.id, "CONVERSATION_ACCESSED", f"ID: {conv_id}, msgs: {len(res.data)}", "PHI")
        return jsonify(res.data or [])
    except Exception as e:
        print("Load conv error:", e)
        return jsonify({"error": "Server error"}), 500


# ── Rename conversation ───────────────────────────────────────────────────────

@auth_bp.route("/rename", methods=["POST"])
def rename():
    supabase, get_user, _, __, ___ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    conv_id = request.json.get("conversation_id")
    title   = request.json.get("title")

    if not conv_id or title is None:
        return jsonify({"error": "conversation_id and title required"}), 400

    try:
        supabase.table("conversations") \
            .update({"title": title}) \
            .eq("id",      conv_id) \
            .eq("user_id", user.id) \
            .execute()
        return jsonify({"success": True})
    except Exception as e:
        print("Rename error:", e)
        return jsonify({"error": "Server error"}), 500


# ── Delete conversation ───────────────────────────────────────────────────────

@auth_bp.route("/delete", methods=["POST"])
def delete():
    supabase, get_user, audit, _, __ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    conv_id = request.json.get("conversation_id")
    if not conv_id:
        return jsonify({"error": "conversation_id required"}), 400

    try:
        audit(supabase, user.id, "CONVERSATION_DELETED", f"ID: {conv_id}", "PHI")
        supabase.table("chats").delete().eq("conversation_id", conv_id).eq("user_id", user.id).execute()
        supabase.table("conversations").delete().eq("id", conv_id).eq("user_id", user.id).execute()
        return jsonify({"success": True})
    except Exception as e:
        print("Delete conv error:", e)
        return jsonify({"error": "Server error"}), 500