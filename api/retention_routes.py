"""
api/retention_routes.py
═══════════════════════════════════════════════════════════════════════════
Retention & Engagement Endpoints

  POST /api/weekly-brief          — generate/fetch weekly brief
  GET  /api/weekly-brief/latest   — get most recent brief
  POST /api/appointment-prep      — generate pre-appointment brief
  GET  /api/appointment-prep/list — list saved appointment briefs
  POST /api/emotion-check         — classify emotion (for frontend use)
  POST /api/milestone-check       — check for milestone alerts

Register in app.py:
  from api.retention_routes import retention_bp
  app.register_blueprint(retention_bp)
═══════════════════════════════════════════════════════════════════════════
"""

from flask import Blueprint, request, jsonify

retention_bp = Blueprint("retention", __name__)


def _deps():
    from app import supabase, groq_client
    from services.auth       import get_authenticated_user
    from services.compliance import audit_log, verify_user_consent
    return supabase, groq_client, get_authenticated_user, audit_log, verify_user_consent


# ── Weekly Brief ──────────────────────────────────────────────────────────────

@retention_bp.route("/api/weekly-brief", methods=["POST"])
def get_weekly_brief():
    supabase, groq_client, get_user, audit, verify = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    if not verify(supabase, user.id, "ai_processing"):
        return jsonify({"error": "AI processing consent required"}), 403

    force = (request.json or {}).get("force", False)

    try:
        from services.weekly_brief import generate_weekly_brief
        
        # Get user name
        name = ""
        try:
            res = supabase.table("user_profiles").select("first_name").eq("user_id", user.id).limit(1).execute()
            if res.data:
                name = res.data[0].get("first_name", "")
        except Exception:
            pass

        brief = generate_weekly_brief(supabase, user.id, name, force=force)
        
        if not brief:
            return jsonify({
                "available": False,
                "reason": "No health data yet — upload a lab report to receive weekly briefs."
            })
        
        audit(supabase, user.id, "WEEKLY_BRIEF_GENERATED", "via API", "PHI")
        return jsonify({"available": True, "brief": brief})
    
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"Brief generation failed: {type(e).__name__}"}), 500


@retention_bp.route("/api/weekly-brief/latest", methods=["GET"])
def get_latest_brief():
    supabase, _, get_user, _, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        res = (supabase.table("weekly_briefs")
               .select("subject,headline,full_text,brief_json,generated_at")
               .eq("user_id", user.id)
               .order("generated_at", desc=True)
               .limit(1).execute())
        
        if not res.data:
            return jsonify({"available": False})
        
        import json
        row = res.data[0]
        brief_data = json.loads(row.get("brief_json", "{}"))
        return jsonify({"available": True, "brief": brief_data})
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Appointment Prep ──────────────────────────────────────────────────────────

@retention_bp.route("/api/appointment-prep", methods=["POST"])
def appointment_prep():
    supabase, groq_client, get_user, audit, verify = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    if not verify(supabase, user.id, "ai_processing"):
        return jsonify({"error": "AI processing consent required"}), 403

    body            = request.json or {}
    appointment_date = body.get("appointment_date", "")
    specialist_type  = body.get("specialist_type", "primary care")

    if not appointment_date:
        return jsonify({"error": "appointment_date required (YYYY-MM-DD)"}), 400

    try:
        from services.weekly_brief import generate_preappointment_prep

        name = ""
        try:
            res = supabase.table("user_profiles").select("first_name").eq("user_id", user.id).limit(1).execute()
            if res.data:
                name = res.data[0].get("first_name", "")
        except Exception:
            pass

        prep = generate_preappointment_prep(
            supabase, user.id, appointment_date, specialist_type, name
        )

        # Store in DB
        try:
            supabase.table("appointment_preps").insert({
                "user_id":          user.id,
                "appointment_date": appointment_date,
                "specialist_type":  specialist_type,
                "brief_text":       prep.get("formatted", ""),
                "brief_json":       __import__("json").dumps(prep),
                "created_at":       __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            }).execute()
        except Exception as e:
            print(f"[APPT_PREP] Store error (non-fatal): {e}")

        audit(supabase, user.id, "APPOINTMENT_PREP_GENERATED",
              f"date:{appointment_date} specialist:{specialist_type}", "PHI")
        return jsonify({"prep": prep})

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"Prep generation failed: {type(e).__name__}"}), 500


@retention_bp.route("/api/appointment-prep/list", methods=["GET"])
def list_appointment_preps():
    supabase, _, get_user, _, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        res = (supabase.table("appointment_preps")
               .select("id,appointment_date,specialist_type,brief_text,created_at")
               .eq("user_id", user.id)
               .order("appointment_date", desc=True)
               .limit(20).execute())
        return jsonify({"preps": res.data or []})
    except Exception as e:
        return jsonify({"preps": []})


# ── Emotion Check (for frontend) ──────────────────────────────────────────────

@retention_bp.route("/api/emotion-check", methods=["POST"])
def emotion_check():
    """
    Classify the emotional state of a message.
    Used by the frontend to show empathy-aware UI elements.
    """
    supabase, _, get_user, _, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    message = (request.json or {}).get("message", "")
    if not message:
        return jsonify({"error": "message required"}), 400

    try:
        from ai.emotional_layer import classify_emotion
        signal = classify_emotion(message)
        return jsonify({
            "primary":       signal.primary,
            "intensity":     signal.intensity,
            "food_noise":    signal.food_noise,
            "identity_threat": signal.identity_threat,
            "autonomy_loss": signal.autonomy_loss,
        })
    except Exception as e:
        return jsonify({"primary": "neutral", "intensity": "low"})


# ── Milestone Alerts ──────────────────────────────────────────────────────────

@retention_bp.route("/api/milestone-check", methods=["GET"])
def milestone_check():
    """
    Check for milestone notifications the user should see.
    Called on dashboard load.
    
    Returns alerts like:
      - "Your A1C has been normal for 3 months straight"
      - "You've uploaded 5 reports — your trend data is now meaningful"
      - "It's been 8 weeks since your last lab report"
    """
    supabase, _, get_user, _, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        from health_memory.memory import get_latest_markers, get_health_trends, get_user_markers
        from datetime import date, timedelta

        latest   = get_latest_markers(supabase, user.id)
        trends   = get_health_trends(supabase, user.id)
        all_mkrs = get_user_markers(supabase, user.id, limit=500)
        
        milestones = []
        today = date.today()

        # Milestone 1: All markers normal for the first time
        if latest and all(v.get("status") == "NORMAL" for v in latest.values()):
            milestones.append({
                "type":    "positive",
                "icon":    "✓",
                "title":   "All markers in normal range",
                "message": f"All {len(latest)} of your tracked markers are currently within normal range. That's a meaningful baseline.",
                "cta":     "View your health picture",
            })

        # Milestone 2: Significant improvement
        big_improvements = [
            t for t in trends
            if not t["concerning"] and t["pct_change"] >= 20
        ]
        for t in big_improvements[:1]:
            milestones.append({
                "type":    "positive",
                "icon":    "↑",
                "title":   f"{t['marker']} improved {t['pct_change']:.0f}%",
                "message": f"Your {t['marker']} has moved from {t['first_val']} to {t['last_val']} {t['unit']} since {t['from_date']}. That kind of change reflects real, sustained effort.",
                "cta":     f"Ask PHI about your {t['marker']} trend",
            })

        # Milestone 3: Stale data warning (no upload in 8+ weeks)
        if latest:
            most_recent = max((v.get("date", "") for v in latest.values()), default="")
            if most_recent:
                try:
                    days_since = (today - date.fromisoformat(most_recent[:10])).days
                    if days_since >= 56:
                        milestones.append({
                            "type":    "nudge",
                            "icon":    "⏳",
                            "title":   f"Last report was {days_since // 7} weeks ago",
                            "message": f"Your most recent lab data is from {most_recent[:10]}. Uploading a new report now would give PHI enough data to detect meaningful trends.",
                            "cta":     "Upload a new report",
                        })
                except (ValueError, TypeError):
                    pass

        # Milestone 4: Enough data for trends (5th report)
        report_count = len(set(m.get("source_document", "") for m in all_mkrs if m.get("source_document")))
        if report_count == 5:
            milestones.append({
                "type":    "positive",
                "icon":    "📊",
                "title":   "5 reports — trend data is now meaningful",
                "message": "With 5 reports in your record, PHI can now detect statistically meaningful trends across your markers. Your health picture is getting clearer.",
                "cta":     "View your trends",
            })

        # Milestone 5: Concerning trend that's been there 3+ months
        long_trends = [
            t for t in trends
            if t["concerning"] and t.get("from_date")
        ]
        for t in long_trends[:1]:
            try:
                days = (today - date.fromisoformat(t["from_date"])).days
                if days >= 90:
                    milestones.append({
                        "type":    "warning",
                        "icon":    "⚠",
                        "title":   f"{t['marker']} has been worsening for {days // 30} months",
                        "message": f"Your {t['marker']} has moved {t['pct_change']}% in the wrong direction since {t['from_date']}. This is the conversation to have at your next appointment.",
                        "cta":     f"Prepare for your appointment",
                    })
            except (ValueError, TypeError):
                pass

        return jsonify({"milestones": milestones, "count": len(milestones)})

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"milestones": [], "count": 0})