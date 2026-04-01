"""
api/profile_routes.py
─────────────────────────────────────────────────────────────────────────────
User profile management — name, age, and any personalisation fields.

Endpoints
---------
  GET  /api/profile          — fetch the current user's profile
  POST /api/profile          — create or update profile
  GET  /api/summary/<doc_id> — fetch a stored report summary by document ID
  POST /api/report-summary   — generate a personalised summary for uploaded markers
"""

from flask import Blueprint, request, jsonify
from datetime import datetime, timezone

profile_bp = Blueprint("profile", __name__)


def _deps():
    from app import supabase, groq_client
    from services.auth       import get_authenticated_user
    from services.compliance import audit_log
    return supabase, groq_client, get_authenticated_user, audit_log


# ── GET / POST profile ────────────────────────────────────────────────────────

@profile_bp.route("/api/profile", methods=["GET"])
def get_profile():
    supabase, _, get_user, audit = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        res = (
            supabase.table("user_profiles")
            .select("*")
            .eq("user_id", user.id)
            .limit(1)
            .execute()
        )
        profile = res.data[0] if res.data else {}
        return jsonify(profile)
    except Exception as e:
        print(f"[PROFILE] Get error: {e}")
        return jsonify({}), 200  # Return empty profile, not 500


@profile_bp.route("/api/profile", methods=["POST"])
def save_profile():
    supabase, _, get_user, audit = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.json or {}

    # Whitelist only safe profile fields
    allowed = {"first_name", "last_name", "age", "date_of_birth", "gender", "timezone"}
    profile_data = {k: v for k, v in body.items() if k in allowed}

    if not profile_data:
        return jsonify({"error": "No valid profile fields provided"}), 400

    profile_data["user_id"]    = user.id
    profile_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        supabase.table("user_profiles").upsert(
            profile_data, on_conflict="user_id"
        ).execute()

        audit(supabase, user.id, "PROFILE_UPDATED",
              f"Fields: {', '.join(profile_data.keys())}", "METADATA")

        return jsonify({"success": True})
    except Exception as e:
        print(f"[PROFILE] Save error: {e}")
        return jsonify({"error": "Could not save profile"}), 500


# ── Report summary ────────────────────────────────────────────────────────────

@profile_bp.route("/api/report-summary", methods=["POST"])
def report_summary():
    """
    Generate a personalised, plain-language summary of a set of markers.

    Request body:
    {
        "markers": [ ...extracted marker dicts... ],
        "filename": "report.pdf"
    }

    Response:
    {
        "summary_text": "...",
        "markers":      [ ...markers with explanations attached... ],
        "abnormal_count": 2,
        "normal_count":   3
    }
    """
    supabase, groq_client, get_user, audit = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    from services.compliance import verify_user_consent
    if not verify_user_consent(supabase, user.id, "ai_processing"):
        return jsonify({"error": "AI processing consent required"}), 403

    body     = request.json or {}
    markers  = body.get("markers", [])
    filename = body.get("filename", "your report")

    if not markers:
        return jsonify({"error": "No markers provided"}), 400

    # Load user profile for personalisation
    user_name = ""
    try:
        res = (
            supabase.table("user_profiles")
            .select("first_name")
            .eq("user_id", user.id)
            .limit(1)
            .execute()
        )
        if res.data:
            user_name = res.data[0].get("first_name", "")
    except Exception:
        pass

    # Attach explanations
    from ai.explainer import explain_markers
    explained = explain_markers(markers, groq_client, user_name)

    # Count statuses
    abnormal = [m for m in explained if m.get("status") in ("HIGH", "LOW")]
    normal   = [m for m in explained if m.get("status") == "NORMAL"]

    # Build one-paragraph summary text
    summary_text = _build_summary_paragraph(explained, user_name, filename)

    audit(supabase, user.id, "REPORT_SUMMARY_GENERATED",
          f"file: {filename}, markers: {len(explained)}, abnormal: {len(abnormal)}", "PHI")

    return jsonify({
        "summary_text":   summary_text,
        "markers":        explained,
        "abnormal_count": len(abnormal),
        "normal_count":   len(normal),
        "user_name":      user_name,
    })


# ── Helper ────────────────────────────────────────────────────────────────────

def _build_summary_paragraph(
    markers:   list[dict],
    user_name: str,
    filename:  str,
) -> str:
    """Build a readable one-paragraph summary of the report."""
    prefix = f"{user_name}, your" if user_name else "Your"
    abnormal = [m for m in markers if m.get("status") in ("HIGH", "LOW")]

    if not abnormal:
        return (
            f"{prefix} report from {filename} looks generally healthy. "
            "All measured markers are within their reference ranges. "
            "Keep up your current lifestyle and schedule a regular check-up."
        )

    flags = []
    for m in abnormal:
        direction = "above" if m.get("status") == "HIGH" else "below"
        flags.append(f"{m['marker']} ({m['value']} {m.get('unit','')} — {direction} normal)")

    flag_str = ", ".join(flags)
    count    = len(abnormal)

    return (
        f"{prefix} report from {filename} shows {count} value{'s' if count > 1 else ''} "
        f"outside the normal range: {flag_str}. "
        "These results are worth discussing with your doctor. "
        "Curabook has stored these markers to track changes over time."
    )