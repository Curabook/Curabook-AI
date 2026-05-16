"""
api/health_routes.py
─────────────────────────────────────────────────────────────────────────────
FIX-HEALTH-MEMORY: /api/memory/facts now calls get_memories_fresh() which
  always hits the DB directly (no cache). This is the endpoint script.js
  calls at startup to pre-load memories into _cachedMemories[].

FIX-LAB-REPORTS: /api/lab-reports now returns real uploaded report data
  from health_markers (distinct source_document values) AND medical_documents
  table. The previous /doctor-prep/history stub always returned [] — this
  caused the "Lab Reports" view in app.html to always show empty.

FIX B-04 (preserved): _compute_status imported and called correctly.
FIX LOGS-1 (preserved): /api/behavioral-logs GET+POST stub for cockpit.

Endpoints:
  GET  /api/health-timeline     — chronological marker readings for charts
  GET  /api/health-markers      — latest value per marker
  GET  /api/health-insights     — AI-generated insights (cached 24h)
  GET  /api/dashboard           — full dashboard data (stats + feed + trends)
  POST /api/doctor-brief        — generate doctor visit prep
  GET  /api/memory/context      — structured JSON health context
  GET  /api/memory/facts        — conversation memory facts (FRESH, no cache)
  DELETE /api/memory/facts/<id> — soft-delete a memory fact
  GET  /api/behavioral-logs     — fetch behavioral logs
  POST /api/behavioral-logs     — store behavioral log entry
  GET  /api/lab-reports         — FIXED: returns real uploaded report list
  GET  /api/health/reports      — count of distinct reports uploaded
"""

from flask import Blueprint, request, jsonify
from datetime import datetime, timezone

health_bp = Blueprint("health", __name__)


def _resolve_status(value, reference_range: str, existing_status: str = "") -> str:
    """Local status resolution — avoids importing private function from another module."""
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
            return "LOW" if v < float(r[1:]) else "NORMAL"
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
    from ai.chat              import call_llm

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
    brief = call_llm([{"role": "user", "content": prompt}], max_tokens=1200)

    if not brief:
        brief = "Unable to generate brief — AI service unavailable. Please try again."

    audit_log(supabase, user.id, "DOCTOR_BRIEF_GENERATED",
              f"symptoms:{len(symptoms)} meds:{len(medications)}", "PHI")
    return jsonify({"brief": brief})


# ── FIX-LAB-REPORTS: Real lab reports endpoint ───────────────────────────────

@health_bp.route("/api/lab-reports", methods=["GET"])
def lab_reports():
    """
    FIX: This endpoint replaces the old /doctor-prep/history stub that always
    returned []. script.js calls this for the Lab Reports view in app.html.

    Returns a list of unique uploaded documents with their marker counts,
    abnormal marker counts, upload date, and the markers associated with each.

    Data sources:
      1. health_markers (distinct source_document values) — always available
      2. medical_documents table — richer metadata if it exists
    """
    from app import supabase
    from services.auth       import get_authenticated_user
    from services.compliance import audit_log

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        # Fetch all markers with source_document
        markers_res = (
            supabase.table("health_markers")
            .select("marker_name,value,unit,status,date,source_document,created_at")
            .eq("user_id", user.id)
            .order("created_at", desc=True)
            .limit(1000)
            .execute()
        )
        all_markers = markers_res.data or []

        # Group markers by source_document
        from collections import defaultdict
        docs: dict = defaultdict(lambda: {
            "markers": [],
            "uploaded_at": None,
            "date": None,
        })

        for m in all_markers:
            src = m.get("source_document", "").strip()
            if not src:
                src = "Uploaded Report"
            docs[src]["markers"].append(m)
            # Track earliest created_at and latest date
            cat = m.get("created_at", "")
            dt  = m.get("date", "")
            if cat and (docs[src]["uploaded_at"] is None or cat > docs[src]["uploaded_at"]):
                docs[src]["uploaded_at"] = cat
            if dt and (docs[src]["date"] is None or dt > docs[src]["date"]):
                docs[src]["date"] = dt

        # Try to enrich with medical_documents table (may not exist)
        doc_meta = {}
        try:
            doc_res = (
                supabase.table("medical_documents")
                .select("filename,job_id,doctor_prep_text,created_at")
                .eq("user_id", user.id)
                .order("created_at", desc=True)
                .limit(100)
                .execute()
            )
            for d in (doc_res.data or []):
                fname = d.get("filename", "")
                if fname:
                    doc_meta[fname] = d
        except Exception:
            pass  # Table may not exist — non-fatal

        # Build result list
        result = []
        for filename, data in docs.items():
            markers   = data["markers"]
            abnormal  = [m for m in markers if m.get("status") in ("HIGH", "LOW")]
            normal    = [m for m in markers if m.get("status") == "NORMAL"]
            uploaded  = data["uploaded_at"] or ""
            rep_date  = data["date"] or ""
            meta      = doc_meta.get(filename, {})

            # Build tags
            tags = []
            if abnormal:
                tags.append({"label": f"{len(abnormal)} flagged", "type": "hi"})
            if normal:
                tags.append({"label": f"{len(normal)} normal", "type": "ok"})
            tags.append({"label": "Lab", "type": "info"})

            result.append({
                "filename":          filename,
                "report_date":       rep_date[:10] if rep_date else "",
                "uploaded_at":       uploaded[:10] if uploaded else "",
                "marker_count":      len(markers),
                "abnormal_count":    len(abnormal),
                "normal_count":      len(normal),
                "tags":              tags,
                "has_doctor_prep":   bool(meta.get("doctor_prep_text")),
                "job_id":            meta.get("job_id", ""),
                "abnormal_markers":  [
                    {"name": m["marker_name"], "value": m["value"],
                     "unit": m.get("unit",""), "status": m.get("status","")}
                    for m in abnormal[:5]
                ],
            })

        # Sort by upload date descending
        result.sort(key=lambda r: r["uploaded_at"] or r["report_date"] or "", reverse=True)

        audit_log(supabase, user.id, "LAB_REPORTS_ACCESSED",
                  f"{len(result)} reports", "PHI")
        return jsonify({"reports": result, "total": len(result)})

    except Exception as e:
        print(f"[LAB-REPORTS] Error: {e}")
        return jsonify({"reports": [], "total": 0, "error": str(e)})


# ── Doctor prep history (kept for backward compat, now returns real data) ────

@health_bp.route("/api/doctor-prep/history", methods=["GET"])
def doctor_prep_history():
    """
    FIX: Was a stub returning []. Now returns real doctor prep briefs
    from medical_documents table, falling back to lab report list.
    """
    from app import supabase
    from services.auth import get_authenticated_user

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    preps = []
    try:
        res = (
            supabase.table("medical_documents")
            .select("filename,job_id,doctor_prep_text,doctor_prep_generated_at,created_at")
            .eq("user_id", user.id)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        preps = [
            {
                "filename":    r.get("filename", "Lab Report"),
                "job_id":      r.get("job_id", ""),
                "has_brief":   bool(r.get("doctor_prep_text")),
                "generated_at": r.get("doctor_prep_generated_at") or r.get("created_at", ""),
            }
            for r in (res.data or [])
        ]
    except Exception:
        pass  # Table may not exist

    return jsonify({"preps": preps})


@health_bp.route("/doctor-prep/<job_id>", methods=["GET"])
def get_doctor_prep(job_id: str):
    from app import supabase
    from services.auth import get_authenticated_user

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        res = (
            supabase.table("medical_documents")
            .select("doctor_prep_text,doctor_prep_generated_at,filename")
            .eq("user_id", user.id)
            .eq("job_id", job_id)
            .limit(1)
            .execute()
        )
        if res.data and res.data[0].get("doctor_prep_text"):
            row = res.data[0]
            return jsonify({
                "ready":      True,
                "brief":      row["doctor_prep_text"],
                "generated":  row.get("doctor_prep_generated_at", ""),
                "filename":   row.get("filename", ""),
            })
    except Exception:
        pass

    return jsonify({"ready": False})


# ── Report count endpoint ─────────────────────────────────────────────────────

@health_bp.route("/api/health/reports", methods=["GET"])
def get_report_count():
    """
    Returns count of distinct lab reports uploaded.
    Counted by distinct source_document values in health_markers.
    """
    from app import supabase
    from services.auth import get_authenticated_user

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        res = supabase.table("health_markers") \
            .select("source_document") \
            .eq("user_id", user.id) \
            .execute()
        docs = set(
            r["source_document"] for r in (res.data or [])
            if r.get("source_document")
        )

        # Also count medical_documents
        try:
            med_res = supabase.table("medical_documents") \
                .select("filename") \
                .eq("user_id", user.id) \
                .execute()
            for r in (med_res.data or []):
                if r.get("filename"):
                    docs.add(r["filename"])
        except Exception:
            pass

        return jsonify({"count": len(docs), "documents": list(docs)})
    except Exception as e:
        return jsonify({"count": 0, "error": str(e)})


# ── Structured memory context (JSON) ─────────────────────────────────────────

@health_bp.route("/api/memory/context", methods=["GET"])
def memory_context():
    """Returns the user's complete health context as structured JSON."""
    from app import supabase
    from services.auth        import get_authenticated_user
    from services.compliance  import audit_log
    from health_memory.memory import (
        get_latest_markers, get_health_trends,
        get_memories_fresh, get_user_markers,
    )

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        latest    = get_latest_markers(supabase, user.id)
        trends    = get_health_trends(supabase, user.id)
        # FIX-HEALTH-MEMORY: always fresh, no cache
        memories  = get_memories_fresh(supabase, user.id)
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
            summary = (
                f"{abnormal_count} marker{'s' if abnormal_count > 1 else ''} need attention.{trend_note} "
                f"Data from {report_count} report{'s' if report_count > 1 else ''}."
            )

        audit_log(supabase, user.id, "MEMORY_CONTEXT_ACCESSED",
                  f"markers:{len(latest)} trends:{len(trends)} memories:{len(memories)}", "PHI")

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
    """
    FIX-HEALTH-MEMORY: Always returns fresh facts (no cache).
    Called by script.js at startup to pre-load _cachedMemories[].
    """
    from app import supabase
    from services.auth        import get_authenticated_user
    from health_memory.memory import get_memories_fresh

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    facts = get_memories_fresh(supabase, user.id, limit=15)
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
        # Invalidate cache after deletion
        from health_memory.memory import _invalidate_context_cache
        _invalidate_context_cache(user.id)
        audit_log(supabase, user.id, "MEMORY_FACT_DELETED", f"id:{fact_id}", "PHI")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Behavioral Logs ───────────────────────────────────────────────────────────

@health_bp.route("/api/behavioral-logs", methods=["GET"])
def behavioral_logs_get():
    """Fetch behavioral logs for the authenticated user."""
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
        res = q.execute()
        return jsonify(res.data or [])
    except Exception as e:
        if "does not exist" in str(e).lower():
            return jsonify([])
        return jsonify({"error": str(e)}), 500


@health_bp.route("/api/behavioral-logs", methods=["POST"])
def behavioral_logs_post():
    """
    Store a behavioral log entry.

    Expected body:
    {
        "date":        "2026-05-04",
        "metric_name": "protein",
        "value":       95.0,
        "unit":        "g",
        "notes":       ""
    }
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

    VALID_METRICS = {
        "protein", "steps", "sleep", "stress", "weight", "food_noise",
        "calories", "water", "exercise_minutes"
    }
    if metric_name.lower() not in VALID_METRICS:
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
            print("[BEHAVIORAL-LOGS] Table not found — run schema.sql first.")
            return jsonify({"success": False, "reason": "behavioral_logs table not created yet"}), 200
        print(f"[BEHAVIORAL-LOGS] Insert error: {e}")
        return jsonify({"error": f"Could not save log: {type(e).__name__}"}), 500

# ══════════════════════════════════════════════════════════════════════════════
# TAPER TRACKER
# ══════════════════════════════════════════════════════════════════════════════

_HALF_LIVES = {
    "semaglutide": 7.0,   # Wegovy, Ozempic — 7-day half-life
    "tirzepatide": 5.0,   # Zepbound, Mounjaro — 5-day half-life
}

def _compute_drug_level(last_dose_date: str, half_life_days: float) -> dict:
    """
    Compute % drug still active and a 7-day hunger forecast.
    Returns dict with pct_active, hunger_forecast list, peak_hunger_day.
    """
    from datetime import date as _date
    import math

    try:
        last = _date.fromisoformat(last_dose_date)
    except Exception:
        return {"pct_active": 0, "hunger_forecast": [], "days_since_dose": 0}

    today = _date.today()
    days_since = (today - last).days

    def pct(days):
        return round(100 * (0.5 ** (days / half_life_days)), 1)

    pct_active = pct(days_since)

    # 7-day forecast from today
    forecast = []
    for offset in range(7):
        d = days_since + offset
        level = pct(d)
        # Hunger spikes when drug drops below 60% and is falling
        hunger = "low" if level > 70 else "moderate" if level > 45 else "high"
        forecast.append({
            "day_offset": offset,
            "date": (today.replace(day=today.day + offset)).isoformat() if offset == 0
                    else None,
            "pct": level,
            "hunger": hunger,
        })

    # Which day in forecast has peak hunger (lowest drug level)
    peak_day = max(range(7), key=lambda i: -forecast[i]["pct"])

    return {
        "pct_active":    pct_active,
        "days_since_dose": days_since,
        "hunger_forecast": forecast,
        "peak_hunger_day": peak_day,
    }


@health_bp.route("/api/taper", methods=["GET"])
def get_taper():
    """Fetch the user's active taper plan."""
    from app import supabase
    from services.auth import get_authenticated_user

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        res = (
            supabase.table("glp1_taper_plans")
            .select("*")
            .eq("user_id", user.id)
            .eq("is_active", True)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not res.data:
            return jsonify({"plan": None})

        plan = res.data[0]
        med  = plan.get("medication", "semaglutide").lower()
        hl   = _HALF_LIVES.get(med, 7.0)
        last = plan.get("last_dose_date", "")

        if last:
            drug_data = _compute_drug_level(last, hl)
        else:
            drug_data = {"pct_active": None, "hunger_forecast": [], "days_since_dose": None}

        return jsonify({"plan": {**plan, **drug_data}})
    except Exception as e:
        err = str(e)
        if "does not exist" in err.lower():
            return jsonify({"plan": None, "setup_required": True})
        print(f"[TAPER] GET error: {e}")
        return jsonify({"plan": None})


@health_bp.route("/api/taper", methods=["POST"])
def save_taper():
    """Create or update a taper plan. Also handles 'log_dose' action."""
    from app import supabase
    from services.auth import get_authenticated_user
    from datetime import date, timedelta

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    body   = request.json or {}
    action = body.get("action", "save")  # "save" | "log_dose" | "stop"

    today = date.today().isoformat()

    if action == "log_dose":
        # Just update last_dose_date to today and recalculate next
        try:
            res = (
                supabase.table("glp1_taper_plans")
                .select("id,medication,frequency_days")
                .eq("user_id", user.id)
                .eq("is_active", True)
                .limit(1)
                .execute()
            )
            if not res.data:
                return jsonify({"error": "No active plan"}), 404

            plan = res.data[0]
            freq = plan.get("frequency_days", 7)
            next_dose = (date.today() + timedelta(days=freq)).isoformat()

            supabase.table("glp1_taper_plans").update({
                "last_dose_date": today,
                "next_dose_date": next_dose,
                "updated_at":     datetime.now(timezone.utc).isoformat(),
            }).eq("id", plan["id"]).execute()

            med  = plan.get("medication", "semaglutide").lower()
            hl   = _HALF_LIVES.get(med, 7.0)
            drug = _compute_drug_level(today, hl)
            return jsonify({"success": True, "next_dose_date": next_dose, **drug})
        except Exception as e:
            print(f"[TAPER] log_dose error: {e}")
            return jsonify({"error": str(e)}), 500

    if action == "stop":
        try:
            supabase.table("glp1_taper_plans").update({
                "is_active":  False,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("user_id", user.id).eq("is_active", True).execute()
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # action == "save" — create or update plan
    medication    = str(body.get("medication", "semaglutide")).lower().strip()
    current_dose  = body.get("current_dose")
    dose_unit     = str(body.get("dose_unit", "mg")).strip()
    frequency     = int(body.get("frequency_days", 7))
    taper_type    = str(body.get("taper_type", "stretch")).strip()  # stretch | stepdown
    last_dose     = str(body.get("last_dose_date", today))[:10]
    target_weeks  = int(body.get("target_weeks", 9))

    from datetime import timedelta
    next_dose     = (date.fromisoformat(last_dose) + timedelta(days=frequency)).isoformat()

    row = {
        "user_id":       user.id,
        "medication":    medication,
        "current_dose":  float(current_dose) if current_dose else None,
        "dose_unit":     dose_unit,
        "frequency_days": frequency,
        "taper_type":    taper_type,
        "last_dose_date": last_dose,
        "next_dose_date": next_dose,
        "target_weeks":  target_weeks,
        "is_active":     True,
        "updated_at":    datetime.now(timezone.utc).isoformat(),
    }

    try:
        # Deactivate any existing plan first
        supabase.table("glp1_taper_plans").update({"is_active": False}) \
            .eq("user_id", user.id).eq("is_active", True).execute()

        row["created_at"] = datetime.now(timezone.utc).isoformat()
        supabase.table("glp1_taper_plans").insert(row).execute()

        hl   = _HALF_LIVES.get(medication, 7.0)
        drug = _compute_drug_level(last_dose, hl)
        return jsonify({"success": True, "next_dose_date": next_dose, **drug})
    except Exception as e:
        print(f"[TAPER] save error: {e}")
        return jsonify({"error": str(e)}), 500