"""
health_memory/memory.py
─────────────────────────────────────────────────────────────────────────────
FIX #CACHE-CONTEXT: build_health_context_block() now caches the result in
    a module-level dict for 90 seconds. Previously this function made 4
    sequential DB calls on EVERY chat turn, adding ~300-500ms of DB latency
    to every single response. With caching, only the first request per
    90-second window hits the database.

FIX #DEMO-1  (preserved): user age and gender injected for sex-specific ranges.
FIX #MEM-1   (preserved): source_conversation fallback for older schemas.
FIX #MEM-2   (preserved): clean retry loop with full error transparency.
"""

from __future__ import annotations
from datetime import datetime, timezone, date
from typing import Optional
import time

_STALE_DAYS    = 180
_TREND_MIN_PCT = 10
_MAX_MEMORIES  = 15

# FIX #CACHE-CONTEXT: 90-second in-process cache for health context blocks
# Key: user_id, Value: (context_str, timestamp)
_context_cache: dict[str, tuple[str, float]] = {}
_CONTEXT_CACHE_TTL = 90  # seconds


def _invalidate_context_cache(user_id: str) -> None:
    """Call this after storing new markers to force context refresh."""
    _context_cache.pop(user_id, None)


# ══════════════════════════════════════════════════════════════════════════════
# Layer 1 — Marker Store
# ══════════════════════════════════════════════════════════════════════════════

def store_health_markers(supabase, user_id: str, markers: list[dict]) -> int:
    if not markers:
        return 0
    now, stored = datetime.now(timezone.utc).isoformat(), 0
    for m in markers:
        marker_name = m.get("marker", m.get("marker_name", "Unknown"))
        marker_date = m.get("date") or now[:10]
        source      = m.get("source_document", "")
        try:
            if (supabase.table("health_markers").select("id")
                    .eq("user_id", user_id).eq("marker_name", marker_name)
                    .eq("date", marker_date).eq("source_document", source)
                    .limit(1).execute()).data:
                continue
            supabase.table("health_markers").insert({
                "user_id": user_id, "marker_name": marker_name,
                "value": m.get("value"), "unit": m.get("unit", ""),
                "reference_range": m.get("reference_range", ""),
                "status": m.get("status", "UNKNOWN"),
                "date": marker_date, "source_document": source,
                "created_at": now,
            }).execute()
            stored += 1
        except Exception as e:
            print(f"[MEMORY] Store error for {marker_name}: {e}")
    if stored:
        # Invalidate context cache when new markers are stored
        _invalidate_context_cache(user_id)
        print(f"[MEMORY] Stored {stored}/{len(markers)} markers for {user_id[:8]}")
    return stored


def get_user_markers(
    supabase, user_id: str, limit: int = 500, marker_name: Optional[str] = None,
) -> list[dict]:
    try:
        q = (supabase.table("health_markers")
             .select("id,marker_name,value,unit,reference_range,status,date,source_document,created_at")
             .eq("user_id", user_id).order("date", desc=True).limit(limit))
        if marker_name:
            q = q.ilike("marker_name", f"%{marker_name}%")
        return q.execute().data or []
    except Exception as e:
        print(f"[MEMORY] Fetch error: {e}")
        return []


def get_latest_markers(supabase, user_id: str) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for m in get_user_markers(supabase, user_id, limit=500):
        name = m["marker_name"]
        if name not in latest:
            age = _days_old(m.get("date", ""))
            m["days_old"] = age
            m["is_stale"] = age > _STALE_DAYS
            latest[name] = m
    return latest


def get_health_timeline(
    supabase, user_id: str, marker_name: Optional[str] = None,
) -> list[dict]:
    try:
        q = (supabase.table("health_markers")
             .select("marker_name,value,unit,date,source_document")
             .eq("user_id", user_id).order("date", desc=False).limit(1000))
        if marker_name:
            q = q.ilike("marker_name", f"%{marker_name}%")
        return q.execute().data or []
    except Exception as e:
        print(f"[MEMORY] Timeline error: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Layer 2 — Trend Engine
# ══════════════════════════════════════════════════════════════════════════════

def get_health_trends(supabase, user_id: str) -> list[dict]:
    grouped: dict[str, list] = {}
    for m in get_user_markers(supabase, user_id, limit=1000):
        grouped.setdefault(m["marker_name"], []).append(m)
    trends = []
    for name, readings in grouped.items():
        if len(readings) < 2:
            continue
        readings.sort(key=lambda r: r.get("date", ""))
        try:
            first_val = float(readings[0]["value"])
            last_val  = float(readings[-1]["value"])
            if first_val == 0:
                continue
            pct = ((last_val - first_val) / abs(first_val)) * 100
            if abs(pct) < _TREND_MIN_PCT:
                continue
            direction  = "rising" if pct > 0 else "falling"
            severity   = "high" if abs(pct) >= 30 else "medium" if abs(pct) >= 15 else "low"
            status     = readings[-1].get("status", "UNKNOWN")
            concerning = (
                (status == "HIGH" and direction == "rising") or
                (status == "LOW"  and direction == "falling")
            )
            trends.append({
                "marker": name, "first_val": first_val, "last_val": last_val,
                "unit": readings[-1].get("unit", ""),
                "pct_change": round(abs(pct), 1), "direction": direction,
                "from_date": readings[0].get("date", ""),
                "to_date":   readings[-1].get("date", ""),
                "readings": len(readings), "severity": severity,
                "concerning": concerning, "status": status,
            })
        except (TypeError, ValueError):
            continue
    trends.sort(key=lambda t: (0 if t["concerning"] else 1, -t["pct_change"]))
    return trends


# ══════════════════════════════════════════════════════════════════════════════
# Layer 3 — Conversation Memory
# ══════════════════════════════════════════════════════════════════════════════

def save_conversation_memory(
    supabase, user_id: str, facts: list[str], conversation_id: str = "",
) -> int:
    if not facts:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    saved = 0
    dropped = 0

    for fact in facts:
        fact = fact.strip()
        if not fact or len(fact) < 10:
            continue

        fact_saved = False

        try:
            supabase.table("conversation_memories").insert({
                "user_id":              user_id,
                "fact":                 fact[:500],
                "source_conversation":  conversation_id or None,
                "created_at":           now,
                "is_active":            True,
            }).execute()
            fact_saved = True
        except Exception as e1:
            err_str = str(e1).lower()
            is_schema_error = any(kw in err_str for kw in [
                "source_conversation", "column", "does not exist", "unknown column"
            ])
            if is_schema_error:
                try:
                    supabase.table("conversation_memories").insert({
                        "user_id":    user_id,
                        "fact":       fact[:500],
                        "category":   "general",
                        "created_at": now,
                        "is_active":  True,
                    }).execute()
                    fact_saved = True
                except Exception as e2:
                    print(f"[MEMORY] Both insert attempts failed for fact '{fact[:40]}': {e2}")
            else:
                print(f"[MEMORY] Memory save error (non-schema): {e1}")

        if fact_saved:
            saved += 1
        else:
            dropped += 1

    if saved > 0 or dropped > 0:
        print(f"[MEMORY] Facts: {saved} saved, {dropped} dropped for {user_id[:8]}")

    # Invalidate context cache after saving new memories
    if saved > 0:
        _invalidate_context_cache(user_id)

    return saved


def get_conversation_memories(supabase, user_id: str) -> list[str]:
    try:
        res = (supabase.table("conversation_memories")
               .select("fact,created_at").eq("user_id", user_id)
               .eq("is_active", True).order("created_at", desc=True)
               .limit(_MAX_MEMORIES).execute())
        return [row["fact"] for row in (res.data or [])]
    except Exception as e:
        print(f"[MEMORY] Conversation memory fetch error: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Layer 3b — User Demographics
# ══════════════════════════════════════════════════════════════════════════════

def get_user_demographics(supabase, user_id: str) -> dict:
    try:
        res = (supabase.table("user_profiles")
               .select("age,gender,first_name")
               .eq("user_id", user_id).limit(1).execute())
        if res.data:
            row = res.data[0]
            return {
                "age":        row.get("age"),
                "gender":     row.get("gender"),
                "first_name": row.get("first_name", ""),
            }
    except Exception as e:
        print(f"[MEMORY] Demographics fetch error: {e}")
    return {}


# ══════════════════════════════════════════════════════════════════════════════
# Layer 4 — Smart Synthesis
# ══════════════════════════════════════════════════════════════════════════════

_INSULIN_RESISTANCE_TRIAD = {"hba1c", "fasting blood glucose", "triglycerides"}
_CARDIOVASCULAR_CLUSTER   = {"ldl cholesterol", "hdl cholesterol", "total cholesterol", "crp"}
_ANEMIA_CLUSTER           = {"hemoglobin", "ferritin", "vitamin b12"}


def _match_cluster(marker_name: str, cluster: set) -> bool:
    lower = marker_name.lower()
    return any(k in lower for k in cluster)


def synthesize_metabolic_story(latest: dict[str, dict], trends: list[dict]) -> str:
    if not latest:
        return ""
    stories = []

    ir = {k: v for k, v in latest.items() if _match_cluster(k, _INSULIN_RESISTANCE_TRIAD)}
    if len(ir) >= 2:
        abnormal_ir = {k: v for k, v in ir.items() if v.get("status") in ("HIGH", "LOW")}
        if abnormal_ir:
            parts = [f"{n} {m['value']} {m.get('unit','')} ({m.get('status','')})"
                     for n, m in abnormal_ir.items()]
            story = "🔴 INSULIN RESISTANCE PATTERN: " + ", ".join(parts)
            worse = [t for t in trends if _match_cluster(t["marker"], _INSULIN_RESISTANCE_TRIAD) and t["concerning"]]
            if worse:
                story += f" ⚠ Worsening: {worse[0]['marker']} moved {worse[0]['pct_change']}% wrong direction."
            stories.append(story)

    cv = {k: v for k, v in latest.items() if _match_cluster(k, _CARDIOVASCULAR_CLUSTER)}
    if cv:
        ldl_high = any(_match_cluster(k, {"ldl"}) and v.get("status") == "HIGH" for k, v in cv.items())
        crp_high = any(_match_cluster(k, {"crp"}) and v.get("status") == "HIGH" for k, v in cv.items())
        hdl_low  = any(_match_cluster(k, {"hdl"}) and v.get("status") == "LOW"  for k, v in cv.items())
        if ldl_high and crp_high:
            stories.append("🔴 COMPOUNDED CARDIOVASCULAR RISK: High LDL + elevated CRP")
        elif ldl_high and hdl_low:
            stories.append("🟡 LIPID IMBALANCE: High LDL + low HDL — dual cardiovascular risk")

    anemia = {k: v for k, v in latest.items() if _match_cluster(k, _ANEMIA_CLUSTER)}
    if anemia:
        low_anemia = {k: v for k, v in anemia.items() if v.get("status") == "LOW"}
        if len(low_anemia) >= 2:
            stories.append(f"🟡 ANEMIA CLUSTER: {', '.join(low_anemia.keys())} all below normal")

    return "\n🔬 PHI SYNTHESIS:\n" + "\n".join(stories) if stories else ""


# ══════════════════════════════════════════════════════════════════════════════
# Layer 5 — Narrative Context Builder (with 90s cache)
# ══════════════════════════════════════════════════════════════════════════════

def build_health_context_block(supabase, user_id: str) -> str:
    """
    Builds the complete health narrative for the LLM.
    FIX #CACHE-CONTEXT: Result cached for 90 seconds to avoid 4 sequential
    DB calls on every chat turn.
    """
    # Check cache first
    now_ts = time.monotonic()
    if user_id in _context_cache:
        cached_ctx, cached_ts = _context_cache[user_id]
        if now_ts - cached_ts < _CONTEXT_CACHE_TTL:
            print(f"[MEMORY] Context cache HIT for {user_id[:8]}")
            return cached_ctx

    # Cache miss — build from DB
    print(f"[MEMORY] Context cache MISS for {user_id[:8]} — querying DB")
    result = _build_context_from_db(supabase, user_id)

    # Cache the result
    _context_cache[user_id] = (result, now_ts)
    return result


def _build_context_from_db(supabase, user_id: str) -> str:
    """Inner function that actually queries the database."""
    try:
        latest   = get_latest_markers(supabase, user_id)
        trends   = get_health_trends(supabase, user_id)
        memories = get_conversation_memories(supabase, user_id)
        demo     = get_user_demographics(supabase, user_id)
    except Exception as e:
        print(f"[MEMORY] build_health_context_block fetch error: {e}")
        return ""

    if not latest and not memories and not trends:
        print(f"[MEMORY] No health data for {user_id[:8]}")
        return ""

    print(f"[MEMORY] Context: {len(latest)} markers, {len(trends)} trends, "
          f"{len(memories)} memories for {user_id[:8]}")

    lines = []
    today = date.today().isoformat()
    lines.append(f"╔══ PHI HEALTH MEMORY  [{today}] ══╗")

    age    = demo.get("age")
    gender = demo.get("gender", "")
    if age or gender:
        age_str    = f"{age}-year-old " if age else ""
        gender_str = str(gender).lower() if gender else ""
        lines.append(f"\n👤 PROFILE: {(age_str + gender_str).strip()}")
        if gender:
            lines.append("   ↳ Use sex-specific ranges for: Hemoglobin, Ferritin, Creatinine, TSH")

    synthesis = synthesize_metabolic_story(latest, trends)
    if synthesis:
        lines.append(synthesis)

    abnormal = {
        name_: m for name_, m in latest.items()
        if _compute_status(m.get("value"), m.get("reference_range", ""), m.get("status", "")) in ("HIGH", "LOW")
        and not m.get("is_stale")
    }
    if abnormal:
        lines.append(f"\n🚨 NEEDS ATTENTION ({len(abnormal)} markers):")
        for n, m in sorted(abnormal.items()):
            status = _compute_status(m.get("value"), m.get("reference_range", ""), m.get("status", ""))
            ref    = f"normal: {m['reference_range']}" if m.get("reference_range") else "no ref"
            age_   = _human_age(m.get("days_old", 0))
            lines.append(f"  • {n}: {m['value']} {m.get('unit','')} — {status} ({ref}) — {age_}")

    concerning = [t for t in trends if t["concerning"]]
    if concerning:
        lines.append("\n📈 WORSENING TRENDS:")
        for t in concerning[:5]:
            arrow = "↑" if t["direction"] == "rising" else "↓"
            lines.append(
                f"  • {t['marker']}: {arrow}{t['pct_change']}% "
                f"({t['first_val']}→{t['last_val']} {t['unit']}) "
                f"{t['from_date']}→{t['to_date']} ⚠"
            )

    improving = [t for t in trends if not t["concerning"] and t["pct_change"] >= 15]
    if improving:
        lines.append("\n✅ IMPROVING:")
        for t in improving[:3]:
            arrow = "↑" if t["direction"] == "rising" else "↓"
            lines.append(f"  • {t['marker']}: {arrow}{t['pct_change']}% ({t['first_val']}→{t['last_val']} {t['unit']}) ✓")

    normal = {
        n: m for n, m in latest.items()
        if _compute_status(m.get("value"), m.get("reference_range", ""), m.get("status", "")) == "NORMAL"
        and not m.get("is_stale")
    }
    if normal:
        parts = [f"{n.split()[0]} {m['value']}{m.get('unit','')}" for n, m in list(normal.items())[:8]]
        lines.append(f"\n✅ WITHIN RANGE ({len(normal)} markers):")
        lines.append("  " + ", ".join(parts) + (" ..." if len(normal) > 8 else ""))

    stale = {n: m for n, m in latest.items() if m.get("is_stale")}
    if stale:
        lines.append(f"\n⏳ HISTORICAL (>6 months): {', '.join(list(stale.keys())[:5])}")

    if memories:
        lines.append(f"\n💬 WHAT THIS PERSON HAS SHARED ({len(memories)} facts):")
        for fact in memories[:8]:
            lines.append(f"  • {fact}")

    sources      = {m.get("source_document", "") for m in latest.values() if m.get("source_document")}
    most_recent  = max((m.get("date", "") for m in latest.values()), default="")
    if sources:
        lines.append(f"\n📋 {len(sources)} report(s). Most recent: {most_recent}")

    lines.append("\n╚═══════════════════════════════════╝")
    lines.append(
        "RULES: Use exact values/dates. Sex-specific ranges where relevant. "
        "STALE = historical only. If marker not listed: 'I don't have that data yet.'"
    )
    return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _compute_status(value, reference_range: str, existing_status: str = "") -> str:
    if existing_status in ("HIGH", "LOW", "NORMAL"):
        return existing_status
    try:
        if not reference_range or value is None:
            return "UNKNOWN"
        v = float(value)
        r = str(reference_range).strip()
        if r.startswith("<"):  return "HIGH" if v > float(r[1:]) else "NORMAL"
        if r.startswith(">"):  return "LOW"  if v < float(r[1:]) else "NORMAL"
        if "-" in r:
            lo, hi = r.split("-", 1)
            if v < float(lo): return "LOW"
            if v > float(hi): return "HIGH"
            return "NORMAL"
    except (ValueError, AttributeError, TypeError):
        pass
    return "UNKNOWN"


def _days_old(date_str: str) -> int:
    try:
        return (date.today() - date.fromisoformat(str(date_str)[:10])).days if date_str else 0
    except (ValueError, TypeError):
        return 0


def _human_age(days: int) -> str:
    if days == 0:  return "today"
    if days == 1:  return "yesterday"
    if days < 7:   return f"{days} days ago"
    if days < 30:  return f"{days // 7} week{'s' if days >= 14 else ''} ago"
    if days < 365: return f"{days // 30} month{'s' if days >= 60 else ''} ago"
    return f"{days // 365} year{'s' if days >= 730 else ''} ago"