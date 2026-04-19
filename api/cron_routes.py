import os
from flask import Blueprint, request, jsonify
from datetime import datetime, timezone
 
cron_bp = Blueprint("cron", __name__)
 
CRON_SECRET = os.getenv("CRON_SECRET", "")
 
 
def _verify_cron():
    """Verify the X-Cron-Secret header. Returns True if authorized."""
    if not CRON_SECRET:
        print("[CRON] WARNING: CRON_SECRET not set — all cron requests will be rejected")
        return False
    provided = request.headers.get("X-Cron-Secret", "")
    return provided == CRON_SECRET
 
 
@cron_bp.route("/api/cron/weekly-briefs", methods=["GET"])
def run_weekly_briefs():
    """
    Generate weekly health briefs for all eligible users.
 
    Eligibility criteria:
      - User has at least 1 health marker stored
      - No brief generated for this user in the past 7 days
      - User has ai_processing consent
 
    Returns a summary of how many briefs were generated.
    """
    if not _verify_cron():
        return jsonify({"error": "Unauthorized — X-Cron-Secret required"}), 401
 
    from app import supabase
    from services.weekly_brief import generate_weekly_brief
    from services.compliance import verify_user_consent
 
    print(f"[CRON] Weekly brief run starting at {datetime.now(timezone.utc).isoformat()}")
 
    generated = 0
    skipped   = 0
    errors    = 0
 
    try:
        # Get all users who have health markers
        users_with_data = (
            supabase.table("health_markers")
            .select("user_id")
            .execute()
        )
        # Deduplicate
        user_ids = list({row["user_id"] for row in (users_with_data.data or [])})
        print(f"[CRON] Found {len(user_ids)} users with health data")
 
        for user_id in user_ids:
            try:
                # Check AI consent
                if not verify_user_consent(supabase, user_id, "ai_processing"):
                    skipped += 1
                    continue
 
                # Get user name
                name = ""
                try:
                    prof = (supabase.table("user_profiles")
                            .select("first_name").eq("user_id", user_id).limit(1).execute())
                    if prof.data:
                        name = prof.data[0].get("first_name", "") or ""
                except Exception:
                    pass
 
                brief = generate_weekly_brief(supabase, user_id, name)
                if brief:
                    generated += 1
                    # TODO: send email via Supabase Edge Functions or SendGrid
                    # For now, brief is stored in DB via _store_brief() inside generate_weekly_brief
                    print(f"[CRON] Brief generated for {user_id[:8]}: {brief.get('subject','')[:50]}")
                else:
                    skipped += 1  # Already sent this week or insufficient data
 
            except Exception as e:
                errors += 1
                print(f"[CRON] Error for {user_id[:8]}: {type(e).__name__}: {e}")
 
    except Exception as e:
        print(f"[CRON] Fatal error: {e}")
        return jsonify({"error": str(e)}), 500
 
    summary = {
        "run_at":    datetime.now(timezone.utc).isoformat(),
        "generated": generated,
        "skipped":   skipped,
        "errors":    errors,
        "total":     len(user_ids) if "user_ids" in dir() else 0,
    }
    print(f"[CRON] Complete: {summary}")
    return jsonify(summary)
 
 
@cron_bp.route("/api/cron/send-brief/<user_id>", methods=["POST"])
def send_brief_for_user(user_id: str):
    """
    Force-generate a weekly brief for a specific user.
    Used for testing and manual triggers.
    """
    if not _verify_cron():
        return jsonify({"error": "Unauthorized"}), 401
 
    from app import supabase
    from services.weekly_brief import generate_weekly_brief
 
    force = request.json.get("force", True) if request.json else True
 
    name = ""
    try:
        prof = (supabase.table("user_profiles")
                .select("first_name").eq("user_id", user_id).limit(1).execute())
        if prof.data:
            name = prof.data[0].get("first_name", "") or ""
    except Exception:
        pass
 
    brief = generate_weekly_brief(supabase, user_id, name, force=force)
    if not brief:
        return jsonify({"success": False, "reason": "No health data or consent missing"}), 200
 
    return jsonify({"success": True, "brief": brief})