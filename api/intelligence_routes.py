"""
api/intelligence_routes.py
═══════════════════════════════════════════════════════════════════════════
New API endpoints exposing Tasks 1–3 to the frontend.

  GET  /api/persona              → Health Persona (200-word biography)
  POST /api/persona/refresh      → Force-regenerate persona
  GET  /api/advocacy             → PA support packet
  POST /api/correlate            → Cross-domain Observation Cards
  GET  /api/behavioral-logs      → Fetch behavioral log entries
  POST /api/behavioral-logs      → Add a behavioral log entry

Register in app.py:
  from api.intelligence_routes import intelligence_bp
  app.register_blueprint(intelligence_bp)
═══════════════════════════════════════════════════════════════════════════
"""

from flask import Blueprint, request, jsonify

intelligence_bp = Blueprint("intelligence", __name__)


def _deps():
    from app import supabase, groq_client
    from services.auth       import get_authenticated_user
    from services.compliance import audit_log, verify_user_consent
    return supabase, groq_client, get_authenticated_user, audit_log, verify_user_consent


# ── GET /api/persona ──────────────────────────────────────────────────────────

@intelligence_bp.route("/api/persona", methods=["GET"])
def get_persona():
    """
    Returns the user's Health Persona — a ≤200-word biography synthesized
    from their markers, conversation memory, and profile.

    Cached with 6-hour TTL. Automatically invalidated when marker count changes.
    """
    supabase, groq_client, get_user, audit, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        from health_memory.persona import generate_recursive_summary
        persona = generate_recursive_summary(supabase, user.id)
        audit(supabase, user.id, "PERSONA_ACCESSED", f"len:{len(persona)}", "PHI")
        return jsonify({"persona": persona, "user_id": user.id[:8]})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"Persona generation failed: {type(e).__name__}"}), 500


@intelligence_bp.route("/api/persona/refresh", methods=["POST"])
def refresh_persona():
    """Force-regenerate the Health Persona, bypassing cache."""
    supabase, groq_client, get_user, audit, verify = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    if not verify(supabase, user.id, "ai_processing"):
        return jsonify({"error": "AI processing consent required"}), 403

    try:
        from health_memory.persona import generate_recursive_summary
        persona = generate_recursive_summary(supabase, user.id, force_refresh=True)
        audit(supabase, user.id, "PERSONA_REFRESHED", f"len:{len(persona)}", "PHI")
        return jsonify({"persona": persona, "refreshed": True})
    except Exception as e:
        return jsonify({"error": f"Refresh failed: {type(e).__name__}"}), 500


# ── GET /api/advocacy ─────────────────────────────────────────────────────────

@intelligence_bp.route("/api/advocacy", methods=["GET"])
def get_advocacy_brief():
    """
    Generate a GLP-1 Prior Authorization Support Packet.

    Query params:
      medication  — medication name (default: "GLP-1")
      raw         — include raw PA-relevant markers (default: false)

    Returns structured PA packet with clinical facts, evidence strength,
    missing data, and next steps.

    INFORMATIONAL ONLY — for the user to share with their provider.
    """
    supabase, groq_client, get_user, audit, verify = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    if not verify(supabase, user.id, "ai_processing"):
        return jsonify({"error": "AI processing consent required"}), 403

    medication  = request.args.get("medication", "GLP-1")[:50]
    include_raw = request.args.get("raw", "false").lower() == "true"

    try:
        from health_memory.persona import generate_advocacy_brief
        result = generate_advocacy_brief(
            supabase, user.id,
            medication_name  = medication,
            include_raw_data = include_raw,
        )
        audit(supabase, user.id, "ADVOCACY_BRIEF_GENERATED",
              f"med:{medication} strength:{result.get('evidence_strength','?')}", "PHI")
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"Advocacy brief failed: {type(e).__name__}"}), 500


# ── POST /api/correlate ───────────────────────────────────────────────────────

@intelligence_bp.route("/api/correlate", methods=["POST"])
def correlate():
    """
    Cross-domain Correlation Engine.

    Request body:
      { "query": "why did my sugar spike on Monday?",
        "lookback_days": 90,      # optional, default 90
        "max_cards": 3 }          # optional, default 3

    Returns a list of ObservationCard dicts with plain-English findings
    and confidence scores.
    """
    supabase, _, get_user, audit, verify = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    if not verify(supabase, user.id, "ai_processing"):
        return jsonify({"error": "AI processing consent required"}), 403

    body          = request.json or {}
    query         = str(body.get("query", ""))[:500]
    lookback_days = min(int(body.get("lookback_days", 90)), 365)
    max_cards     = min(int(body.get("max_cards", 3)), 5)

    if not query:
        return jsonify({"error": "query is required"}), 400

    try:
        from health_memory.correlation import correlate_markers_with_behavior
        cards = correlate_markers_with_behavior(
            supabase, user.id, query,
            lookback_days = lookback_days,
            max_cards     = max_cards,
        )
        audit(supabase, user.id, "CORRELATION_RUN",
              f"query:'{query[:40]}' cards:{len(cards)}", "PHI")
        return jsonify({"observation_cards": cards, "query": query})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"Correlation failed: {type(e).__name__}"}), 500


# ── Behavioral logs ───────────────────────────────────────────────────────────

@intelligence_bp.route("/api/behavioral-logs", methods=["GET"])
def get_behavioral_logs():
    """
    Fetch behavioral logs for the authenticated user.

    Query params:
      metric  — filter by metric_name (e.g. "steps")
      days    — lookback window (default: 30, max: 365)
    """
    supabase, _, get_user, audit, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    metric      = request.args.get("metric", "")
    days        = min(int(request.args.get("days", 30)), 365)
    from datetime import date, timedelta
    cutoff      = (date.today() - timedelta(days=days)).isoformat()

    try:
        q = (supabase.table("behavioral_logs")
             .select("id,date,metric_name,value,unit,notes,created_at")
             .eq("user_id", user.id)
             .gte("date", cutoff)
             .order("date", desc=True)
             .limit(500))
        if metric:
            q = q.ilike("metric_name", f"%{metric}%")
        res  = q.execute()
        rows = res.data or []
        audit(supabase, user.id, "BEHAVIORAL_LOGS_ACCESSED",
              f"metric:{metric or 'all'} rows:{len(rows)}", "PHI")
        return jsonify(rows)
    except Exception as e:
        if "does not exist" in str(e).lower():
            return jsonify([])   # Table not created yet — return empty
        return jsonify({"error": str(e)}), 500


@intelligence_bp.route("/api/behavioral-logs", methods=["POST"])
def add_behavioral_log():
    """
    Add a behavioral log entry.

    Request body:
      { "date":        "2026-04-12",     # YYYY-MM-DD
        "metric_name": "steps",          # steps | food | sleep | stress | weight
        "value":       8500,
        "unit":        "steps",
        "notes":       "20min walk after dinner"  # optional
      }

    Supported metric_name values (not enforced, but expected by correlation engine):
      "steps"   — daily step count
      "food"    — caloric intake (value in kcal)
      "sleep"   — sleep duration (value in hours)
      "stress"  — stress level (value 1–10)
      "weight"  — body weight (value in lbs or kg, specify unit)
    """
    supabase, _, get_user, audit, verify = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    if not verify(supabase, user.id, "data_processing"):
        return jsonify({"error": "Data processing consent required"}), 403

    body        = request.json or {}
    date_str    = str(body.get("date", ""))[:10]
    metric_name = str(body.get("metric_name", ""))[:50].strip()
    value       = body.get("value")
    unit        = str(body.get("unit",  ""))[:20]
    notes       = str(body.get("notes", ""))[:500]

    if not date_str or not metric_name or value is None:
        return jsonify({"error": "date, metric_name, and value are required"}), 400

    try:
        float(value)
    except (TypeError, ValueError):
        return jsonify({"error": "value must be numeric"}), 400

    from datetime import datetime, timezone
    try:
        res = supabase.table("behavioral_logs").insert({
            "user_id":     user.id,
            "date":        date_str,
            "metric_name": metric_name,
            "value":       float(value),
            "unit":        unit,
            "notes":       notes,
            "created_at":  datetime.now(timezone.utc).isoformat(),
        }).execute()
        audit(supabase, user.id, "BEHAVIORAL_LOG_ADDED",
              f"metric:{metric_name} value:{value} date:{date_str}", "PHI")
        return jsonify({"success": True, "id": res.data[0].get("id") if res.data else None})
    except Exception as e:
        return jsonify({"error": f"Could not save log: {type(e).__name__}: {e}"}), 500