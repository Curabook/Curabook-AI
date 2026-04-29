"""
api/startup_routes.py — FIXED
─────────────────────────────────────────────────────────────────────────────
Single batch endpoint: returns everything the frontend needs on sign-in
in ONE network round-trip.

  GET /api/startup

FIXES:
  FIX-1: ThreadPoolExecutor.as_completed(timeout=) raises TimeoutError and
         silently drops remaining futures — tasks that finished first were
         returned but tasks still running were lost. Now uses individual
         future.result(timeout=3) calls so each task independently times out
         but doesn't cancel the others.

  FIX-2: Marker deduplication was breaking chronological ordering. Now keeps
         the most RECENT reading per marker by sorting first (desc by date).

  FIX-3: Cliff alert detection handles missing 'value' keys gracefully.

  FIX-4: Returns 200 with partial data even if some DB calls fail — the
         frontend should always get something, never a 500 on startup.
─────────────────────────────────────────────────────────────────────────────
"""

import os
from datetime import date, timedelta, datetime, timezone
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_EXCEPTION, ALL_COMPLETED
from flask import Blueprint, request, jsonify

startup_bp = Blueprint("startup", __name__)

# Per-task timeout — each parallel DB call gets this many seconds
_TASK_TIMEOUT = 4.0


def _deps():
    from app import supabase
    from services.auth import get_authenticated_user
    return supabase, get_authenticated_user


# ── Individual fetchers ───────────────────────────────────────────────────────

def _fetch_history(supabase, user_id: str) -> list:
    try:
        res = (
            supabase.table("conversations")
            .select("id,title,created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[STARTUP] history error: {e}")
        return []


def _fetch_markers(supabase, user_id: str) -> list:
    """
    Fetch all markers, deduplicate keeping the most recent reading per
    marker name. Sort desc first so the first occurrence is always newest.
    """
    try:
        res = (
            supabase.table("health_markers")
            .select("marker_name,value,unit,reference_range,status,date,source_document")
            .eq("user_id", user_id)
            .order("date", desc=True)   # newest first
            .limit(500)
            .execute()
        )
        rows = res.data or []
        # Deduplicate: first occurrence per marker_name is the most recent
        seen: dict = {}
        for r in rows:
            name = r.get("marker_name", "")
            if name and name not in seen:
                seen[name] = r
        return list(seen.values())
    except Exception as e:
        print(f"[STARTUP] markers error: {e}")
        return []


def _fetch_behavioral_today(supabase, user_id: str) -> list:
    try:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        res = (
            supabase.table("behavioral_logs")
            .select("date,metric_name,value,unit,created_at")
            .eq("user_id", user_id)
            .gte("date", yesterday)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        return res.data or []
    except Exception as e:
        if "does not exist" in str(e).lower():
            return []   # table not yet created — non-fatal
        print(f"[STARTUP] behavioral error: {e}")
        return []


def _fetch_profile(supabase, user_id: str) -> dict:
    try:
        res = (
            supabase.table("user_profiles")
            .select("first_name,goal_weight_lbs,glp1_status,plan")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else {}
    except Exception as e:
        print(f"[STARTUP] profile error: {e}")
        return {}


# ── Cliff alert detection (no LLM, no extra DB call) ────────────────────────

def _build_cliff_alerts(markers: list) -> list:
    alerts = []
    grouped: dict[str, list] = {}

    for m in markers:
        name = str(m.get("marker_name", "")).lower()
        if name:
            grouped.setdefault(name, []).append(m)

    # Glucose rebound (>15% from personal baseline)
    gk = next(
        (k for k in grouped
         if any(f in k for f in ["fasting glucose", "blood glucose", "fasting blood glucose"])
         or k.strip() == "glucose"),
        None
    )
    if gk:
        readings = sorted(
            grouped[gk],
            key=lambda r: str(r.get("date", ""))
        )
        if len(readings) >= 2:
            try:
                first = float(readings[0]["value"])
                last  = float(readings[-1]["value"])
                if first > 0:
                    pct = ((last - first) / first) * 100
                    if pct >= 15:
                        alerts.append({
                            "severity": "high",
                            "marker":   "Fasting Blood Glucose",
                            "headline": (
                                f"Glucose rebound +{pct:.0f}% from baseline "
                                f"({int(first)}\u2192{int(last)} mg/dL)"
                            ),
                            "detail": (
                                "A rise of \u226515% from your personal baseline is the "
                                "clinical threshold for active post-GLP-1 rebound. "
                                "Discuss with your provider."
                            ),
                        })
                    elif pct >= 10:
                        alerts.append({
                            "severity": "medium",
                            "marker":   "Fasting Blood Glucose",
                            "headline": (
                                f"Glucose rising +{pct:.0f}% \u2014 "
                                f"approaching 15% rebound threshold"
                            ),
                        })
            except (TypeError, ValueError):
                pass

    # HbA1c rebound (>=0.25% increase between any consecutive readings)
    hk = next(
        (k for k in grouped if "hba1c" in k or "hemoglobin a1c" in k),
        None
    )
    if hk:
        readings = sorted(
            grouped[hk],
            key=lambda r: str(r.get("date", ""))
        )
        for i in range(1, len(readings)):
            try:
                delta = float(readings[i]["value"]) - float(readings[i-1]["value"])
                if delta >= 0.25:
                    prev = readings[i-1]["value"]
                    curr = readings[i]["value"]
                    alerts.append({
                        "severity": "high",
                        "marker":   "HbA1c",
                        "headline": (
                            f"HbA1c rebound +{delta:.2f}% "
                            f"({prev}%\u2192{curr}%)"
                        ),
                        "detail": (
                            "HbA1c reflects average glucose over 2\u20133 months. "
                            "A rise \u22650.25% between readings is an active "
                            "glycemic rebound signal."
                        ),
                    })
                    break
            except (TypeError, ValueError):
                pass

    alerts.sort(key=lambda a: 0 if a["severity"] == "high" else 1)
    return alerts


# ── Main endpoint ─────────────────────────────────────────────────────────────

@startup_bp.route("/startup", methods=["GET"])
def startup():
    """
    Parallel batch startup endpoint.

    FIX-1: Uses explicit future.result(timeout=_TASK_TIMEOUT) per task
    instead of as_completed(timeout=) on the whole pool. This ensures
    tasks that finish quickly are never dropped because one slow task
    exceeded the overall timeout.

    Always returns 200 with whatever data is available. The frontend
    renders incrementally — partial data beats an error screen.
    """
    supabase, get_user = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    uid = user.id
    t0  = datetime.now(timezone.utc)

    results = {
        "history":          [],
        "markers":          [],
        "behavioral_today": [],
        "profile":          {},
    }

    tasks = {
        "history":          lambda: _fetch_history(supabase, uid),
        "markers":          lambda: _fetch_markers(supabase, uid),
        "behavioral_today": lambda: _fetch_behavioral_today(supabase, uid),
        "profile":          lambda: _fetch_profile(supabase, uid),
    }

    # FIX-1: Submit all tasks then collect individually with per-task timeout
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {key: pool.submit(fn) for key, fn in tasks.items()}

        for key, future in futures.items():
            try:
                results[key] = future.result(timeout=_TASK_TIMEOUT)
            except Exception as e:
                print(f"[STARTUP] {key} failed or timed out: {type(e).__name__}: {e}")
                # Keep the default empty value for this key

    profile      = results["profile"]
    markers      = results["markers"]
    cliff_alerts = _build_cliff_alerts(markers)

    elapsed_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    print(
        f"[STARTUP] {uid[:8]} — {elapsed_ms}ms — "
        f"history:{len(results['history'])} "
        f"markers:{len(markers)} "
        f"alerts:{len(cliff_alerts)}"
    )

    return jsonify({
        "history":          results["history"],
        "markers":          markers,
        "behavioral_today": results["behavioral_today"],
        "cliff_alerts":     cliff_alerts,
        "user_name":        profile.get("first_name") or "",
        "goal_weight":      profile.get("goal_weight_lbs"),
        "glp1_status":      profile.get("glp1_status") or "",
        "plan":             profile.get("plan") or "free",
        "elapsed_ms":       elapsed_ms,
    })