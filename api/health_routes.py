"""
api/health_routes.py — Complete with memory context API + behavioral logs stub
─────────────────────────────────────────────────────────────────────────────
FIX B-04: _compute_status imported and called with correct signature.
FIX LOGS-1: Added /api/behavioral-logs GET+POST stub so the frontend
            cockpit can log protein, steps, sleep, and food_noise without
            a 404. The full implementation lives in intelligence_routes.py
            (/api/behavioral-logs), but this stub handles the POST from
            script.js's logShieldData() and logNoiseLevel() gracefully
            when the intelligence blueprint isn't registered or available.
            If the full route IS registered, these stubs are never reached
            (Flask matches the first registered route). Safe to keep both.

Endpoints:
  GET  /api/health-timeline     — chronological marker readings for charts
  GET  /api/health-markers      — latest value per marker
  GET  /api/health-insights     — AI-generated insights (cached 24h)
  GET  /api/dashboard           — full dashboard data (stats + feed + trends)
  POST /api/doctor-brief        — generate doctor visit prep
  GET  /api/memory/context      — structured JSON health context
  GET  /api/memory/facts        — conversation memory facts
  GET  /api/behavioral-logs     — fetch behavioral logs (stub/fallback)
  POST /api/behavioral-logs     — store behavioral log entry (stub/fallback)
"""

from flask import Blueprint, request, jsonify
from datetime import datetime, timezone

health_bp = Blueprint("health", __name__)


def _resolve_status(value, reference_range: str, existing_status: str = "") -> str:
    """
    FIX B-04: Local copy of status resolution — avoids importing a private
    function from another module. Keeps this module self-contained.
    """
    if existing_status in ("HIGH", "LOW", "NORMAL"):
        return existing_status
    try:
        if not reference_range or value is None:
            return "UNKNOWN"
        v = float(value)
        r = str(reference_range).strip()
        if r.startswith("<"):
            return "HIGH" if v > float(r[1:]) else "NORMAL"
        if r.startswith(">"):
            return "LOW"  if v < float(r[1:]) else "NORMAL"
        if "-" in r:
            lo, hi = r.split("-", 1)
            if v < float(lo): return "LOW"
            if v > float(hi): return "HIGH"
            return "NORMAL"
    except (ValueError, AttributeError, TypeError):
        pass
    return "UNKNOWN"


# ── Health timeline ───────────────────────────────────────────────────────────

@health_bp.route("/api/health-timeline", methods=["GET"])
def health_timeline():
    from app import supabase
    from services.auth        import get_authenticated_user
    from services.compliance  import audit_log
    from health_memory.memory import get_health_timeline

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    marker_filter = request.args.get("marker")
    timeline = get_health_timeline(supabase, user.id, marker_name=marker_filter)
    audit_log(supabase, user.id, "HEALTH_TIMELINE_ACCESSED",
              f"marker:{marker_filter or 'all'} rows:{len(timeline)}", "PHI")
    return jsonify(timeline)


# ── Health markers (latest per marker) ───────────────────────────────────────

@health_bp.route("/api/health-markers", methods=["GET"])
def health_markers():
    from app import supabase
    from services.auth        import get_authenticated_user
    from services.compliance  import audit_log
    from health_memory.memory import get_latest_markers

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    latest = get_latest_markers(supabase, user.id)
    audit_log(supabase, user.id, "HEALTH_MARKERS_ACCESSED",
              f"{len(latest)} markers", "PHI")
    return jsonify(list(latest.values()))


# ── Health insights (AI-generated) ───────────────────────────────────────────

@health_bp.route("/api/health-insights", methods=["GET"])
def health_insights():
    from app import supabase, groq_client
    from services.auth       import get_authenticated_user
    from services.compliance import audit_log
    from insights.engine     import generate_insights

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    force   = request.args.get("force", "0") == "1"
    results = generate_insights(supabase, user.id, groq_client, force=force)
    audit_log(supabase, user.id, "INSIGHTS_ACCESSED",
              f"{len(results)} insights", "PHI")
    return jsonify(results)


# ── Dashboard ─────────────────────────────────────────────────────────────────

@health_bp.route("/api/dashboard", methods=["GET"])
def health_dashboard():
    from app import supabase
    from services.auth       import get_authenticated_user
    from services.compliance import audit_log
    from insights.engine     import get_health_dashboard

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    def _nudge(d):
        if d.get("abnormal_count", 0) > 0:
            return "You have markers needing attention — ask PHI what to do."
        if d.get("total_markers", 0) == 0:
            return "Upload your first report to unlock insights."
        return "You're doing well — want deeper insights?"

    try:
        dashboard = get_health_dashboard(supabase, user.id)
        dashboard["nudge"] = _nudge(dashboard)
        audit_log(supabase, user.id, "DASHBOARD_ACCESSED",
                  f"abnormal:{dashboard.get('abnormal_count', 0)}", "PHI")
        return jsonify(dashboard)
    except Exception as e:
        print(f"[DASHBOARD] Error: {e}")
        return jsonify({
            "abnormal_markers": [], "trends": [], "latest_markers": [],
            "feed": [], "total_markers": 0, "abnormal_count": 0,
            "nudge": "Upload your first report to unlock insights.",
        })


# ── Doctor brief ──────────────────────────────────────────────────────────────

@health_bp.route("/api/doctor-brief", methods=["POST"])
def doctor_brief():
    from app import supabase, groq_client
    from services.auth        import get_authenticated_user
    from services.compliance  import audit_log, verify_user_consent
    from health_memory.memory import get_latest_markers

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    if not verify_user_consent(supabase, user.id, "ai_processing"):
        return jsonify({"error": "AI processing consent required"}), 403

    body        = request.json or {}
    symptoms    = body.get("symptoms",    [])[:10]
    medications = body.get("medications", [])[:10]
    notes       = (body.get("notes", "") or "")[:500]

    latest = get_latest_markers(supabase, user.id)

    labs_text = "\n".join(
        f"  • {name}: {m['value']} {m.get('unit','')} "
        f"(ref {m.get('reference_range','')}) [{m.get('date','')}] [{m.get('status','')}]"
        for name, m in sorted(latest.items())
    ) or "  No lab results stored yet."

    prompt = f"""Create a structured doctor visit prep summary.

RECENT LAB RESULTS:
{labs_text}

SYMPTOMS: {', '.join(symptoms) or 'None reported'}
MEDICATIONS: {', '.join(medications) or 'None reported'}
NOTES: {notes or 'None'}

Sections:
1. Lab Highlights — flag EVERY abnormal value with its number
2. Symptoms to mention
3. Medications to review
4. Questions to ask the doctor — specific to these results

Plain language. End: "⚕️ For informational purposes only. Always follow your doctor's advice."
"""
    from ai.chat import call_llm
    brief = call_llm(groq_client, [{"role": "user", "content": prompt}], max_tokens=1200)

    if not brief:
        brief = "Unable to generate brief — AI service unavailable. Please try again."

    audit_log(supabase, user.id, "DOCTOR_BRIEF_GENERATED",
              f"symptoms:{len(symptoms)} meds:{len(medications)}", "PHI")
    return jsonify({"brief": brief})


# ── Structured memory context (JSON) ─────────────────────────────────────────

@health_bp.route("/api/memory/context", methods=["GET"])
def memory_context():
    """
    Returns the user's complete health context as structured JSON.
    FIX B-04: Uses local _resolve_status() instead of importing private
    _compute_status from health_memory.memory.
    """
    from app import supabase
    from services.auth        import get_authenticated_user
    from services.compliance  import audit_log
    from health_memory.memory import (
        get_latest_markers, get_health_trends,
        get_conversation_memories, get_user_markers,
    )

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        latest    = get_latest_markers(supabase, user.id)
        trends    = get_health_trends(supabase, user.id)
        memories  = get_conversation_memories(supabase, user.id)
        all_marks = get_user_markers(supabase, user.id, limit=1000)

        if not latest and not memories:
            return jsonify({
                "conditions":         [],
                "recent_metrics":     {},
                "trends":             [],
                "alerts":             [],
                "conversation_facts": [],
                "summary":            "No health data stored yet. Upload a report to begin.",
                "has_data":           False,
                "total_markers":      0,
                "report_count":       0,
                "last_updated":       None,
            })

        conditions = []
        alerts     = []
        for name, m in sorted(latest.items()):
            status = _resolve_status(
                m.get("value"), m.get("reference_range", ""), m.get("status", "")
            )
            if status in ("HIGH", "LOW"):
                direction = "above" if status == "HIGH" else "below"
                conditions.append(
                    f"{name} is {status} — "
                    f"{m['value']} {m.get('unit','')} ({direction} normal range {m.get('reference_range','')})"
                )
                concerning_trend = any(
                    t["marker"] == name and t["concerning"]
                    for t in trends
                )
                alerts.append({
                    "marker":   name,
                    "value":    m.get("value"),
                    "unit":     m.get("unit", ""),
                    "status":   status,
                    "ref":      m.get("reference_range", ""),
                    "date":     m.get("date", ""),
                    "severity": "high" if concerning_trend else "medium",
                    "message":  (
                        f"{name} is {status} at {m['value']} {m.get('unit','')}. "
                        f"Normal range: {m.get('reference_range','')}. "
                        f"{'⚠ Also showing a worsening trend.' if concerning_trend else ''}"
                    ).strip(),
                    "is_stale": m.get("is_stale", False),
                })

        recent_metrics = {
            name: {
                "value":           m.get("value"),
                "unit":            m.get("unit", ""),
                "reference_range": m.get("reference_range", ""),
                "status":          _resolve_status(
                                       m.get("value"),
                                       m.get("reference_range", ""),
                                       m.get("status", "")
                                   ),
                "date":            m.get("date", ""),
                "days_old":        m.get("days_old", 0),
                "is_stale":        m.get("is_stale", False),
            }
            for name, m in latest.items()
        }

        report_count = len(set(
            m.get("source_document", "")
            for m in all_marks
            if m.get("source_document")
        ))

        last_updated = max(
            (m.get("date", "") for m in latest.values()),
            default=None
        )

        abnormal_count = len(conditions)
        concerning_ct  = len([t for t in trends if t["concerning"]])
        if abnormal_count == 0:
            summary = f"All {len(latest)} tracked markers are within normal range."
        else:
            trend_note = f" {concerning_ct} showing a worsening trend." if concerning_ct else ""
            summary    = (
                f"{abnormal_count} marker{'s' if abnormal_count > 1 else ''} need attention.{trend_note} "
                f"Data from {report_count} report{'s' if report_count > 1 else ''}."
            )

        audit_log(supabase, user.id, "MEMORY_CONTEXT_ACCESSED",
                  f"markers:{len(latest)} trends:{len(trends)}", "PHI")

        return jsonify({
            "conditions":         conditions,
            "recent_metrics":     recent_metrics,
            "trends":             trends,
            "alerts":             sorted(alerts, key=lambda a: (0 if a["severity"] == "high" else 1, a["marker"])),
            "conversation_facts": memories,
            "summary":            summary,
            "has_data":           True,
            "total_markers":      len(latest),
            "report_count":       report_count,
            "last_updated":       last_updated,
        })

    except Exception as e:
        import traceback
        print(f"[MEMORY CONTEXT] Error: {e}")
        traceback.print_exc()
        return jsonify({"error": "Failed to load health context"}), 500


# ── Conversation memory facts ─────────────────────────────────────────────────

@health_bp.route("/api/memory/facts", methods=["GET"])
def memory_facts():
    from app import supabase
    from services.auth        import get_authenticated_user
    from health_memory.memory import get_conversation_memories

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    facts = get_conversation_memories(supabase, user.id)
    return jsonify({"facts": facts, "count": len(facts)})


# ── Delete a conversation memory fact ─────────────────────────────────────────

@health_bp.route("/api/memory/facts/<fact_id>", methods=["DELETE"])
def delete_memory_fact(fact_id: str):
    from app import supabase
    from services.auth       import get_authenticated_user
    from services.compliance import audit_log

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        supabase.table("conversation_memories")\
            .update({"is_active": False})\
            .eq("id",      fact_id)\
            .eq("user_id", user.id)\
            .execute()
        audit_log(supabase, user.id, "MEMORY_FACT_DELETED", f"id:{fact_id}", "PHI")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Behavioral Logs — Stub/Fallback ──────────────────────────────────────────
# FIX-LOGS-1: These endpoints handle the cockpit's logShieldData() and
# logNoiseLevel() calls. The full implementation is in intelligence_routes.py.
# This stub is a safe fallback if that blueprint isn't registered, and also
# acts as documentation of the expected request/response shape.

@health_bp.route("/api/behavioral-logs", methods=["GET"])
def behavioral_logs_get():
    """
    Fetch behavioral logs for the authenticated user.
    Full implementation: intelligence_routes.py /api/behavioral-logs GET
    This stub provides a safe fallback.
    """
    from app import supabase
    from services.auth import get_authenticated_user

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    metric = request.args.get("metric", "")
    days   = min(int(request.args.get("days", 30)), 365)

    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=days)).isoformat()

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
        return jsonify(res.data or [])
    except Exception as e:
        # If the table doesn't exist yet, return empty rather than 500
        if "does not exist" in str(e).lower():
            return jsonify([])
        return jsonify({"error": str(e)}), 500


@health_bp.route("/api/behavioral-logs", methods=["POST"])
def behavioral_logs_post():
    """
    Store a behavioral log entry (protein, steps, sleep, food_noise, weight).

    Expected body:
    {
        "date":        "2026-04-20",
        "metric_name": "protein",
        "value":       95.0,
        "unit":        "g",
        "notes":       ""       (optional)
    }

    Full implementation: intelligence_routes.py /api/behavioral-logs POST
    This stub provides a safe fallback for the cockpit.
    """
    from app import supabase
    from services.auth import get_authenticated_user

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

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

    # Validate metric names to prevent unexpected writes
    VALID_METRICS = {"protein", "steps", "sleep", "stress", "weight", "food_noise",
                     "calories", "water", "exercise_minutes"}
    if metric_name.lower() not in VALID_METRICS:
        # Accept it anyway but log a warning — don't block the frontend
        print(f"[BEHAVIORAL-LOGS] Unknown metric: {metric_name} (user: {user.id[:8]})")

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
        return jsonify({"success": True, "id": res.data[0].get("id") if res.data else None})
    except Exception as e:
        err_str = str(e).lower()
        if "does not exist" in err_str:
            # behavioral_logs table not yet created — return 200 silently
            # so the frontend cockpit doesn't show errors
            print(f"[BEHAVIORAL-LOGS] Table not found — run schema.sql first. Silently ignoring.")
            return jsonify({"success": False, "reason": "behavioral_logs table not created yet"}), 200
        print(f"[BEHAVIORAL-LOGS] Insert error: {e}")
        return jsonify({"error": f"Could not save log: {type(e).__name__}"}), 500