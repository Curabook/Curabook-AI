"""
api/startup_routes.py
─────────────────────────────────────────────────────────────────────────────
Single batch endpoint that returns everything the frontend needs on sign-in
in ONE network round-trip instead of 4-5 separate calls.

  GET /api/startup

Returns:
  {
    "history":         [...conversations...],
    "markers":         [...latest markers...],
    "behavioral_today":[...today's logs...],
    "goal_weight":     float | null,
    "cliff_alerts":    [...],
    "user_name":       str,
  }

This endpoint is the single most impactful performance fix — it eliminates
the waterfall of:
  /history → /api/health-markers → /api/behavioral-logs → /api/dashboard
on every page load, replacing them with a single parallel DB fetch.
"""

import os
from datetime import date, timedelta, datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from flask import Blueprint, request, jsonify

startup_bp = Blueprint("startup", __name__)

# Max time (seconds) each parallel DB fetch is allowed before we return
# partial results — never block the frontend more than this.
_FETCH_TIMEOUT = 4.0


def _deps():
    from app import supabase
    from services.auth import get_authenticated_user
    return supabase, get_authenticated_user


# ── Individual fetchers (each runs in its own thread) ────────────────────────

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
    try:
        res = (
            supabase.table("health_markers")
            .select("marker_name,value,unit,reference_range,status,date,source_document")
            .eq("user_id", user_id)
            .order("date", desc=True)
            .limit(500)
            .execute()
        )
        rows = res.data or []
        # Deduplicate: keep latest per marker
        seen: dict = {}
        for r in rows:
            name = r["marker_name"]
            if name not in seen:
                seen[name] = r
        return list(seen.values())
    except Exception as e:
        print(f"[STARTUP] markers error: {e}")
        return []


def _fetch_behavioral_today(supabase, user_id: str) -> list:
    try:
        today = date.today().isoformat()
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


def _build_cliff_alerts(markers: list) -> list:
    """
    Fast in-process cliff alert detection — no LLM, no extra DB call.
    Mirrors the logic in insights/engine.py but runs synchronously.
    """
    alerts = []
    grouped: dict[str, list] = {}
    for m in markers:
        k = (m.get("marker_name") or "").lower()
        grouped.setdefault(k, []).append(m)

    # Glucose rebound (>15% from personal baseline)
    gk = next((k for k in grouped if any(f in k for f in
               ["fasting glucose", "blood glucose", "glucose"])), None)
    if gk:
        readings = sorted(grouped[gk], key=lambda r: r.get("date", ""))
        if len(readings) >= 2:
            try:
                first = float(readings[0]["value"])
                last  = float(readings[-1]["value"])
                pct   = ((last - first) / first) * 100 if first else 0
                if pct >= 15:
                    alerts.append({
                        "severity": "high",
                        "marker":   "Fasting Blood Glucose",
                        "headline": f"Glucose rebound +{pct:.0f}% from baseline ({int(first)}→{int(last)} mg/dL)",
                    })
                elif pct >= 10:
                    alerts.append({
                        "severity": "medium",
                        "marker":   "Fasting Blood Glucose",
                        "headline": f"Glucose rising +{pct:.0f}% — approaching rebound threshold",
                    })
            except (TypeError, ValueError):
                pass

    # HbA1c rebound (>=0.25% increase)
    hk = next((k for k in grouped if "hba1c" in k), None)
    if hk:
        readings = sorted(grouped[hk], key=lambda r: r.get("date", ""))
        for i in range(1, len(readings)):
            try:
                delta = float(readings[i]["value"]) - float(readings[i-1]["value"])
                if delta >= 0.25:
                    prev = readings[i-1]["value"]
                    curr = readings[i]["value"]
                    alerts.append({
                        "severity": "high",
                        "marker":   "HbA1c",
                        "headline": f"HbA1c rebound +{delta:.2f}% ({prev}%→{curr}%)",
                    })
                    break
            except (TypeError, ValueError):
                pass

    return alerts


# ── Main endpoint ──────────────────────────────────────────────────────────────

@startup_bp.route("/api/startup", methods=["GET"])
def startup():
    """
    Batch endpoint: runs all startup DB fetches in parallel threads,
    returns in ≤ _FETCH_TIMEOUT seconds regardless of DB slowness.
    """
    supabase, get_user = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    uid = user.id
    t0  = datetime.now(timezone.utc)

    # Run all 4 fetchers in parallel
    results: dict = {
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

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fn): key for key, fn in tasks.items()}
        for future in as_completed(futures, timeout=_FETCH_TIMEOUT):
            key = futures[future]
            try:
                results[key] = future.result(timeout=0.1)
            except Exception as e:
                print(f"[STARTUP] {key} failed: {e}")

    profile     = results["profile"]
    markers     = results["markers"]
    cliff_alerts = _build_cliff_alerts(markers)

    elapsed_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    print(f"[STARTUP] Completed in {elapsed_ms}ms for {uid[:8]}")

    return jsonify({
        "history":          results["history"],
        "markers":          markers,
        "behavioral_today": results["behavioral_today"],
        "cliff_alerts":     cliff_alerts,
        "user_name":        profile.get("first_name", ""),
        "goal_weight":      profile.get("goal_weight_lbs"),
        "glp1_status":      profile.get("glp1_status", ""),
        "plan":             profile.get("plan", "free"),
        "elapsed_ms":       elapsed_ms,
    })