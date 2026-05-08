"""
api/onboarding_routes.py
═══════════════════════════════════════════════════════════════════════════
Handles user onboarding data:
  POST /api/onboarding        — save GLP-1 status, goal weight, concern
  GET  /api/onboarding/status — check if user has completed onboarding
  POST /api/onboarding/memories — save memory facts from onboarding

These are called from signup.html after account creation to ensure
the AI has context from day one, regardless of signup method (email or Google).
"""

from flask import Blueprint, request, jsonify
from datetime import datetime, timezone

onboarding_bp = Blueprint("onboarding", __name__)


def _deps():
    from app import supabase
    from services.auth import get_authenticated_user
    from services.compliance import audit_log, ensure_consents
    return supabase, get_authenticated_user, audit_log, ensure_consents


@onboarding_bp.route("/api/onboarding", methods=["POST"])
def save_onboarding():
    """Save complete onboarding data and initial memory facts."""
    supabase, get_user, audit, ensure_consents = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.json or {}
    glp1_status  = str(body.get("glp1_status",  "") or "")[:50]
    med_name     = str(body.get("medication_name", "") or "")[:100]
    goal_weight  = body.get("goal_weight_lbs")
    concern      = str(body.get("primary_concern", "") or "")[:100]
    first_name   = str(body.get("first_name",   "") or "")[:80]
    plan         = str(body.get("plan",          "free") or "free")[:20]
    now          = datetime.now(timezone.utc).isoformat()

    # Ensure consents exist
    ensure_consents(supabase, user.id)

    # 1. Save / update user profile
    profile_data = {
        "user_id":     user.id,
        "glp1_status": glp1_status or None,
        "updated_at":  now,
    }
    if first_name:
        profile_data["first_name"] = first_name
    if goal_weight:
        try:
            profile_data["goal_weight_lbs"] = float(goal_weight)
        except (ValueError, TypeError):
            pass

    try:
        supabase.table("user_profiles").upsert(profile_data, on_conflict="user_id").execute()
    except Exception as e:
        print(f"[ONBOARDING] Profile save error: {e}")

    # 2. Save onboarding record
    try:
        onboarding_data = {
            "user_id":      user.id,
            "glp1_status":  glp1_status or None,
            "goal_weight_lbs": float(goal_weight) if goal_weight else None,
            "primary_concern": concern or None,
            "completed_at": now,
        }
        if med_name:
            onboarding_data["medication_name"] = med_name
        supabase.table("glp1_onboarding").upsert(onboarding_data, on_conflict="user_id").execute()
    except Exception as e:
        print(f"[ONBOARDING] Onboarding record error: {e}")

    # 3. Save initial memory facts
    facts = []

    status_labels = {
        "active":   "is currently taking",
        "tapering": "is tapering / reducing",
        "stopped":  "has stopped taking",
        "never":    "has never taken",
    }
    if glp1_status and glp1_status != "never":
        med_str = f" {med_name}" if med_name else " a GLP-1 medication"
        status_label = status_labels.get(glp1_status, glp1_status)
        facts.append(f"User {status_label}{med_str} (confirmed during signup onboarding)")

    if med_name and glp1_status:
        facts.append(f"User's GLP-1 medication: {med_name} — status: {glp1_status}")

    if goal_weight:
        try:
            gw = float(goal_weight)
            protein = round(gw * 0.545, 1)
            facts.append(
                f"User's goal weight is {gw} lbs — "
                f"Muscle Defense daily protein target: {protein}g/day, {round(protein/3, 1)}g per meal minimum "
                f"(set during signup onboarding)"
            )
        except (ValueError, TypeError):
            pass

    concern_labels = {
        "food_noise":    "User's primary concern: food noise / ghrelin surge returning after stopping GLP-1",
        "weight_regain": "User's primary concern: weight regain after stopping or reducing GLP-1",
        "insurance":     "User's primary concern: insurance denial / prior authorization for GLP-1 medication",
        "muscle_loss":   "User's primary concern: muscle loss and body composition changes",
        "labs":          "User's primary concern: understanding their lab results and health markers",
    }
    if concern and concern in concern_labels:
        facts.append(concern_labels[concern])

    saved_count = 0
    for fact in facts:
        try:
            supabase.table("conversation_memories").insert({
                "user_id":   user.id,
                "fact":      fact[:500],
                "category":  "onboarding",
                "is_active": True,
                "created_at": now,
            }).execute()
            saved_count += 1
        except Exception as e:
            print(f"[ONBOARDING] Memory save error: {e}")

    audit(supabase, user.id, "ONBOARDING_COMPLETED",
          f"glp1:{glp1_status} med:{med_name} gw:{goal_weight} concern:{concern} memories:{saved_count}",
          "METADATA")

    return jsonify({
        "success": True,
        "memories_saved": saved_count,
        "profile_saved": True,
    })


@onboarding_bp.route("/api/onboarding/status", methods=["GET"])
def onboarding_status():
    """Check if the user has completed onboarding."""
    supabase, get_user, _, __ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        res = (supabase.table("glp1_onboarding")
               .select("glp1_status,goal_weight_lbs,primary_concern,completed_at")
               .eq("user_id", user.id)
               .limit(1)
               .execute())
        if res.data:
            row = res.data[0]
            return jsonify({
                "completed": True,
                "glp1_status": row.get("glp1_status"),
                "goal_weight_lbs": row.get("goal_weight_lbs"),
                "primary_concern": row.get("primary_concern"),
                "completed_at": row.get("completed_at"),
            })
        return jsonify({"completed": False})
    except Exception as e:
        print(f"[ONBOARDING] Status check error: {e}")
        return jsonify({"completed": False})