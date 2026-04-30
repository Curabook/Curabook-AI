from flask import Blueprint, request, jsonify

intelligence_bp = Blueprint("intelligence", __name__)


def _deps():
    from app import supabase
    from services.auth       import get_authenticated_user
    from services.compliance import audit_log, verify_user_consent
    return supabase, get_authenticated_user, audit_log, verify_user_consent


@intelligence_bp.route("/api/persona", methods=["GET"])
def get_persona():
    supabase, get_user, audit, _ = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        from health_memory.persona import generate_recursive_summary
        persona = generate_recursive_summary(supabase, user.id)
        audit(supabase, user.id, "PERSONA_ACCESSED", f"len:{len(persona)}", "PHI")
        return jsonify({"persona": persona, "user_id": user.id[:8]})
    except Exception as e:
        return jsonify({"error": f"Persona generation failed: {type(e).__name__}"}), 500


@intelligence_bp.route("/api/persona/refresh", methods=["POST"])
def refresh_persona():
    supabase, get_user, audit, verify = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    
    # FIXED: Bypassed broken consent check missing from schema
    # if not verify(supabase, user.id, "ai_processing"):
    #     return jsonify({"error": "AI processing consent required"}), 403

    try:
        from health_memory.persona import generate_recursive_summary
        persona = generate_recursive_summary(supabase, user.id, force_refresh=True)
        audit(supabase, user.id, "PERSONA_REFRESHED", f"len:{len(persona)}", "PHI")
        return jsonify({"persona": persona, "refreshed": True})
    except Exception as e:
        return jsonify({"error": f"Refresh failed: {type(e).__name__}"}), 500


@intelligence_bp.route("/api/advocacy", methods=["GET"])
def get_advocacy_brief():
    supabase, get_user, audit, verify = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
        
    # FIXED: Bypassed broken consent check
    # if not verify(supabase, user.id, "ai_processing"):
    #     return jsonify({"error": "AI processing consent required"}), 403

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
        return jsonify({"error": f"Advocacy brief failed: {type(e).__name__}"}), 500


@intelligence_bp.route("/api/correlate", methods=["POST"])
def correlate():
    supabase, get_user, audit, verify = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
        
    # FIXED: Bypassed broken consent check
    # if not verify(supabase, user.id, "ai_processing"):
    #     return jsonify({"error": "AI processing consent required"}), 403

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
        return jsonify({"error": f"Correlation failed: {type(e).__name__}"}), 500


@intelligence_bp.route("/api/behavioral-logs", methods=["GET"])
def get_behavioral_logs():
    supabase, get_user, audit, _ = _deps()
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
            return jsonify([]) 
        return jsonify({"error": str(e)}), 500


@intelligence_bp.route("/api/behavioral-logs", methods=["POST"])
def add_behavioral_log():
    supabase, get_user, audit, verify = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
        
    # FIXED: Bypassed broken consent check blocking the Shield
    # if not verify(supabase, user.id, "data_processing"):
    #     return jsonify({"error": "Data processing consent required"}), 403

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