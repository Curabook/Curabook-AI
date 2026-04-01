"""
api/health_routes.py — Complete with memory context API
─────────────────────────────────────────────────────────────────────────────
Endpoints:
  GET  /api/health-timeline     — chronological marker readings for charts
  GET  /api/health-markers      — latest value per marker
  GET  /api/health-insights     — AI-generated insights (cached 24h)
  GET  /api/dashboard           — full dashboard data (stats + feed + trends)
  POST /api/doctor-brief        — generate doctor visit prep
  GET  /api/memory/context      — structured JSON health context (NEW)
  GET  /api/memory/facts        — conversation memory facts (NEW)
"""

from flask import Blueprint, request, jsonify

health_bp = Blueprint("health", __name__)


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


# ── NEW: Structured memory context (JSON) ─────────────────────────────────────

@health_bp.route("/api/memory/context", methods=["GET"])
def memory_context():
    """
    Returns the user's complete health context as structured JSON.

    Response shape:
    {
        "conditions":      ["LDL Cholesterol is HIGH", ...],
        "recent_metrics":  {"LDL Cholesterol": {"value": 172, "unit": "mg/dL", ...}},
        "trends":          [{"marker": "LDL Cholesterol", "direction": "rising", ...}],
        "alerts":          [{"marker": "...", "severity": "high", "message": "..."}],
        "conversation_facts": ["User takes Vitamin D supplements", ...],
        "summary":         "3 markers need attention. LDL has risen 21% over 9 months.",
        "has_data":        true,
        "total_markers":   15,
        "report_count":    3,
        "last_updated":    "2025-03-15"
    }

    Used by:
    - Frontend dashboard
    - External integrations
    - Debugging memory issues
    """
    from app import supabase
    from services.auth        import get_authenticated_user
    from services.compliance  import audit_log
    from health_memory.memory import (
        get_latest_markers, get_health_trends,
        get_conversation_memories, get_user_markers, _compute_status,
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

        # Build conditions list (all abnormal markers)
        conditions = []
        alerts     = []
        for name, m in sorted(latest.items()):
            status = _compute_status(
                m.get("value"), m.get("reference_range", ""), m.get("status", "")
            )
            if status in ("HIGH", "LOW"):
                direction = "above" if status == "HIGH" else "below"
                conditions.append(
                    f"{name} is {status} — "
                    f"{m['value']} {m.get('unit','')} ({direction} normal range {m.get('reference_range','')})"
                )
                # Build alert
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

        # Build recent_metrics dict (all latest values)
        recent_metrics = {
            name: {
                "value":           m.get("value"),
                "unit":            m.get("unit", ""),
                "reference_range": m.get("reference_range", ""),
                "status":          _compute_status(
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

        # Count unique source documents
        report_count = len(set(
            m.get("source_document", "")
            for m in all_marks
            if m.get("source_document")
        ))

        # Most recent data date
        last_updated = max(
            (m.get("date", "") for m in latest.values()),
            default=None
        )

        # Build summary sentence
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


# ── NEW: Conversation memory facts ────────────────────────────────────────────

@health_bp.route("/api/memory/facts", methods=["GET"])
def memory_facts():
    """
    Returns all active conversation memory facts for the user.
    These are health facts PHI learned from past conversations.
    """
    from app import supabase
    from services.auth        import get_authenticated_user
    from health_memory.memory import get_conversation_memories

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    facts = get_conversation_memories(supabase, user.id)
    return jsonify({"facts": facts, "count": len(facts)})


# ── NEW: Delete a conversation memory fact ────────────────────────────────────

@health_bp.route("/api/memory/facts/<fact_id>", methods=["DELETE"])
def delete_memory_fact(fact_id: str):
    """
    Deactivate a specific conversation memory fact.
    User can manage what PHI remembers about them.
    """
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