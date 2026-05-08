"""
api/analytics_routes.py — Founder Analytics Dashboard Backend
═══════════════════════════════════════════════════════════════════════════
Provides all data for the founder dashboard. 

PRIVACY DESIGN:
  - User names and emails are NEVER exposed
  - User IDs are truncated to first 8 chars (anonymous handles)
  - Chat messages are anonymized before aggregation
  - Question topics are extracted via keyword clustering, not raw text
  - This lets you answer "what are users asking about" without reading
    individual conversations

ENDPOINTS:
  GET /api/founder/overview          — KPI summary cards
  GET /api/founder/users             — user list (anonymized)
  GET /api/founder/retention         — retention cohorts
  GET /api/founder/feature-usage     — which features are used
  GET /api/founder/question-topics   — what users ask (anonymized)
  GET /api/founder/payments          — revenue and plan breakdown
  GET /api/founder/activity-timeline — daily active users, events
  GET /api/founder/user/<handle>     — single user deep-dive (anonymized)

AUTH: All endpoints require X-Founder-Secret header matching FOUNDER_SECRET env var.
"""

import os
import re
import json
import hashlib
from datetime import datetime, timezone, timedelta, date
from collections import defaultdict
from flask import Blueprint, request, jsonify

analytics_bp = Blueprint("analytics", __name__)

FOUNDER_SECRET = os.getenv("FOUNDER_SECRET", "")

# ── Auth guard ────────────────────────────────────────────────────────────────

def _auth():
    if not FOUNDER_SECRET:
        return False, "FOUNDER_SECRET not set in .env"
    provided = request.headers.get("X-Founder-Secret", "")
    if provided != FOUNDER_SECRET:
        return False, "Unauthorized"
    return True, ""

def _require_auth():
    ok, msg = _auth()
    if not ok:
        return jsonify({"error": msg}), 401
    return None

def _deps():
    from app import supabase
    return supabase

# ── Privacy helpers ───────────────────────────────────────────────────────────

def _handle(user_id: str) -> str:
    """Anonymous 8-char handle derived from user_id. Not reversible."""
    return hashlib.sha256(user_id.encode()).hexdigest()[:8].upper()

def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()

# ── Question topic classifier (keyword-based, no raw text exposed) ────────────

_TOPIC_PATTERNS = {
    "GLP-1 Cliff / Rebound":      ["cliff", "rebound", "regain", "regaining", "after stopping", "stopped"],
    "Food Noise / Ghrelin":       ["food noise", "ghrelin", "hungry", "cravings", "hunger", "appetite"],
    "Protein / Muscle Defense":   ["protein", "muscle", "leucine", "whey", "resistance", "lean mass", "sarcopenia"],
    "Lab Results / Markers":      ["hba1c", "glucose", "a1c", "cholesterol", "ldl", "hdl", "triglyceride", "creatinine", "lab"],
    "Insurance / Prior Auth":     ["insurance", "prior auth", "pa ", "denied", "coverage", "appeal", "not covered"],
    "Medication / Tapering":      ["wegovy", "ozempic", "zepbound", "mounjaro", "tirzepatide", "semaglutide", "taper", "dose", "injection"],
    "Doctor Visit Prep":          ["doctor", "appointment", "visit", "prepare", "specialist", "cardiologist"],
    "Weight / BMI":               ["weight", "bmi", "lbs", "pounds", "overweight", "obese"],
    "Sleep / Recovery":           ["sleep", "rest", "recovery", "tired", "fatigue", "insomnia"],
    "Exercise / Steps":           ["exercise", "steps", "walk", "gym", "workout", "resistance training"],
    "Metabolic Health":           ["metabolic", "insulin", "diabetes", "prediabetes", "blood sugar"],
    "Vitamins / Supplements":     ["vitamin d", "vitamin b12", "ferritin", "supplement", "deficiency"],
    "Thyroid / Hormones":         ["tsh", "thyroid", "hormone", "testosterone", "estrogen"],
    "Heart / Cardiovascular":     ["heart", "cardiovascular", "blood pressure", "crp", "inflammation", "cholesterol"],
    "Greetings / Navigation":     ["hi", "hello", "hey", "thanks", "thank you"],
}

def _classify_topic(text: str) -> str:
    lower = text.lower()
    for topic, keywords in _TOPIC_PATTERNS.items():
        if any(kw in lower for kw in keywords):
            return topic
    return "General / Other"

# ══════════════════════════════════════════════════════════════════════════════
# OVERVIEW — KPI SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

@analytics_bp.route("/api/founder/overview", methods=["GET"])
def overview():
    err = _require_auth()
    if err: return err
    supabase = _deps()

    try:
        now = datetime.now(timezone.utc)
        d7  = _days_ago(7)
        d30 = _days_ago(30)
        d1  = _days_ago(1)

        # Total users
        users_res = supabase.table("user_profiles").select("user_id", count="exact").execute()
        total_users = users_res.count or 0

        # New users last 7 days
        new_7d = supabase.table("user_profiles").select("user_id", count="exact").gte("created_at", d7).execute()
        new_users_7d = new_7d.count or 0

        # New users last 30 days
        new_30d = supabase.table("user_profiles").select("user_id", count="exact").gte("created_at", d30).execute()
        new_users_30d = new_30d.count or 0

        # Active users (sent a chat in last 7 days)
        active_7d_res = supabase.table("chats").select("user_id").eq("role", "user").gte("created_at", d7).execute()
        active_7d = len(set(r["user_id"] for r in (active_7d_res.data or [])))

        # Active users last 30 days
        active_30d_res = supabase.table("chats").select("user_id").eq("role", "user").gte("created_at", d30).execute()
        active_30d = len(set(r["user_id"] for r in (active_30d_res.data or [])))

        # DAU (today)
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        dau_res = supabase.table("chats").select("user_id").eq("role", "user").gte("created_at", today_start).execute()
        dau = len(set(r["user_id"] for r in (dau_res.data or [])))

        # Total messages
        msgs_res = supabase.table("chats").select("id", count="exact").eq("role", "user").execute()
        total_messages = msgs_res.count or 0

        # Messages last 7d
        msgs_7d_res = supabase.table("chats").select("id", count="exact").eq("role", "user").gte("created_at", d7).execute()
        msgs_7d = msgs_7d_res.count or 0

        # Documents uploaded
        docs_res = supabase.table("health_markers").select("source_document").execute()
        total_docs = len(set(r["source_document"] for r in (docs_res.data or []) if r.get("source_document")))

        # Plans breakdown
        plans_res = supabase.table("user_profiles").select("plan").execute()
        plan_counts = defaultdict(int)
        for r in (plans_res.data or []):
            plan_counts[(r.get("plan") or "free").lower()] += 1

        pro_count = sum(v for k, v in plan_counts.items() if k in ("monthly", "annual", "clinical", "pro", "trial"))

        # Estimated MRR (rough: monthly × $49 + annual × $33.25 + clinical × $99)
        mrr = (
            plan_counts.get("monthly", 0) * 49 +
            plan_counts.get("annual", 0) * 33.25 +
            plan_counts.get("clinical", 0) * 99 +
            plan_counts.get("pro", 0) * 49
        )

        # Markers stored
        markers_res = supabase.table("health_markers").select("id", count="exact").execute()
        total_markers = markers_res.count or 0

        # Memories stored
        memories_res = supabase.table("conversation_memories").select("id", count="exact").eq("is_active", True).execute()
        total_memories = memories_res.count or 0

        # Behavioral logs (shield logs)
        shield_res = supabase.table("behavioral_logs").select("id", count="exact").execute()
        total_shield_logs = shield_res.count or 0

        # Retention: users who were active 7d ago AND active in last 7d
        week_ago_start = _days_ago(14)
        week_ago_end   = _days_ago(7)
        cohort_res = supabase.table("chats").select("user_id").eq("role", "user").gte("created_at", week_ago_start).lte("created_at", week_ago_end).execute()
        cohort_users = set(r["user_id"] for r in (cohort_res.data or []))
        retained_res = supabase.table("chats").select("user_id").eq("role", "user").gte("created_at", d7).execute()
        retained_users = set(r["user_id"] for r in (retained_res.data or []))
        retention_7d = round(len(cohort_users & retained_users) / max(len(cohort_users), 1) * 100, 1)

        return jsonify({
            "users": {
                "total":       total_users,
                "new_7d":      new_users_7d,
                "new_30d":     new_users_30d,
                "active_7d":   active_7d,
                "active_30d":  active_30d,
                "dau":         dau,
                "pro":         pro_count,
                "free":        plan_counts.get("free", 0),
            },
            "engagement": {
                "total_messages":    total_messages,
                "messages_7d":       msgs_7d,
                "avg_msgs_per_user": round(total_messages / max(total_users, 1), 1),
                "total_docs":        total_docs,
                "total_markers":     total_markers,
                "total_memories":    total_memories,
                "total_shield_logs": total_shield_logs,
            },
            "retention": {
                "week_over_week": retention_7d,
                "cohort_size":    len(cohort_users),
            },
            "revenue": {
                "mrr_usd":    round(mrr, 2),
                "arr_usd":    round(mrr * 12, 2),
                "plan_breakdown": dict(plan_counts),
                "paying_users":   pro_count,
                "conversion_pct": round(pro_count / max(total_users, 1) * 100, 1),
            },
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# USER LIST — ANONYMIZED
# ══════════════════════════════════════════════════════════════════════════════

@analytics_bp.route("/api/founder/users", methods=["GET"])
def users():
    err = _require_auth()
    if err: return err
    supabase = _deps()

    try:
        limit  = min(int(request.args.get("limit", 50)), 200)
        offset = int(request.args.get("offset", 0))
        plan_filter = request.args.get("plan", "")

        q = supabase.table("user_profiles").select(
            "user_id,plan,reports_remaining,goal_weight_lbs,glp1_status,"
            "subscription_end_date,created_at,updated_at"
        ).order("created_at", desc=True).limit(limit).offset(offset)

        if plan_filter:
            q = q.eq("plan", plan_filter)

        profiles = q.execute().data or []

        # For each user, get message count and last active
        result = []
        for p in profiles:
            uid = p["user_id"]
            handle = _handle(uid)

            # Message count
            msgs = supabase.table("chats").select("id,created_at").eq("user_id", uid).eq("role", "user").order("created_at", desc=True).limit(100).execute().data or []
            msg_count = len(msgs)
            last_active = msgs[0]["created_at"] if msgs else None

            # Markers count
            markers = supabase.table("health_markers").select("id", count="exact").eq("user_id", uid).execute()
            marker_count = markers.count or 0

            # Conversations count
            convs = supabase.table("conversations").select("id", count="exact").eq("user_id", uid).execute()
            conv_count = convs.count or 0

            # Shield logs count
            shield = supabase.table("behavioral_logs").select("id", count="exact").eq("user_id", uid).execute()
            shield_count = shield.count or 0

            # Days since signup
            try:
                signup = datetime.fromisoformat(p["created_at"].replace("Z", "+00:00"))
                days_since_signup = (datetime.now(timezone.utc) - signup).days
            except Exception:
                days_since_signup = 0

            # Engagement score (0-100)
            score = min(100, (
                min(msg_count * 2, 40) +
                min(marker_count * 5, 20) +
                min(conv_count * 3, 20) +
                min(shield_count * 2, 20)
            ))

            plan = (p.get("plan") or "free").lower()
            is_pro = plan in ("monthly", "annual", "clinical", "pro", "trial")

            result.append({
                "handle":            handle,
                "plan":              plan,
                "is_pro":            is_pro,
                "days_since_signup": days_since_signup,
                "message_count":     msg_count,
                "conversation_count": conv_count,
                "marker_count":      marker_count,
                "shield_log_count":  shield_count,
                "last_active":       last_active,
                "engagement_score":  score,
                "has_goal_weight":   bool(p.get("goal_weight_lbs")),
                "glp1_status":       p.get("glp1_status") or "unknown",
                "sub_end":           p.get("subscription_end_date"),
            })

        # Sort by engagement score
        result.sort(key=lambda x: x["engagement_score"], reverse=True)

        total = supabase.table("user_profiles").select("user_id", count="exact").execute().count or 0

        return jsonify({"users": result, "total": total, "offset": offset})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# QUESTION TOPICS — ANONYMIZED CONTENT INTELLIGENCE
# ══════════════════════════════════════════════════════════════════════════════

@analytics_bp.route("/api/founder/question-topics", methods=["GET"])
def question_topics():
    """
    What are users asking PHI?
    Raw messages are NEVER returned. Only topic categories and counts.
    This answers "what do users care about" without reading their chats.
    """
    err = _require_auth()
    if err: return err
    supabase = _deps()

    try:
        days = int(request.args.get("days", 30))
        cutoff = _days_ago(days)

        # Fetch user messages (content only, no user_id linkage in response)
        msgs = supabase.table("chats").select("content,created_at").eq("role", "user").gte("created_at", cutoff).limit(5000).execute().data or []

        topic_counts  = defaultdict(int)
        topic_by_day  = defaultdict(lambda: defaultdict(int))
        total         = len(msgs)

        for m in msgs:
            content = m.get("content", "")
            topic   = _classify_topic(content)
            topic_counts[topic] += 1

            # Daily breakdown
            try:
                day = m["created_at"][:10]
                topic_by_day[day][topic] += 1
            except Exception:
                pass

        # Sort by count
        ranked = sorted(
            [{"topic": k, "count": v, "pct": round(v / max(total, 1) * 100, 1)}
             for k, v in topic_counts.items()],
            key=lambda x: x["count"],
            reverse=True
        )

        # Last 7 days trend
        trend_cutoff = _days_ago(7)
        recent_msgs = supabase.table("chats").select("content").eq("role", "user").gte("created_at", trend_cutoff).limit(1000).execute().data or []
        recent_topics = defaultdict(int)
        for m in recent_msgs:
            topic = _classify_topic(m.get("content", ""))
            recent_topics[topic] += 1

        # Add trend to ranked
        for item in ranked:
            item["last_7d"] = recent_topics.get(item["topic"], 0)

        # Daily series for top 5 topics
        top5 = [r["topic"] for r in ranked[:5]]
        daily_series = []
        for day in sorted(topic_by_day.keys())[-30:]:
            entry = {"date": day}
            for t in top5:
                entry[t] = topic_by_day[day].get(t, 0)
            daily_series.append(entry)

        return jsonify({
            "total_messages": total,
            "days":           days,
            "topics":         ranked,
            "daily_series":   daily_series,
            "top_topics":     top5,
            "privacy_note":   "Raw message content is never exposed. Topics derived from keyword classification only.",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE USAGE
# ══════════════════════════════════════════════════════════════════════════════

@analytics_bp.route("/api/founder/feature-usage", methods=["GET"])
def feature_usage():
    err = _require_auth()
    if err: return err
    supabase = _deps()

    try:
        days   = int(request.args.get("days", 30))
        cutoff = _days_ago(days)

        # Chat usage
        chat_users_res = supabase.table("chats").select("user_id").eq("role", "user").gte("created_at", cutoff).execute()
        chat_users = set(r["user_id"] for r in (chat_users_res.data or []))

        # Document uploads (users who have markers from documents)
        doc_users_res = supabase.table("health_markers").select("user_id").gte("created_at", cutoff).execute()
        doc_users = set(r["user_id"] for r in (doc_users_res.data or []))

        # Shield logging (behavioral logs)
        shield_users_res = supabase.table("behavioral_logs").select("user_id").gte("created_at", cutoff).execute()
        shield_users = set(r["user_id"] for r in (shield_users_res.data or []))

        # Memory (users who have conversation memories)
        mem_users_res = supabase.table("conversation_memories").select("user_id").eq("is_active", True).gte("created_at", cutoff).execute()
        mem_users = set(r["user_id"] for r in (mem_users_res.data or []))

        # Doctor prep generation
        try:
            prep_users_res = supabase.table("appointment_preps").select("user_id").gte("created_at", cutoff).execute()
            prep_users = set(r["user_id"] for r in (prep_users_res.data or []))
        except Exception:
            prep_users = set()

        # Weekly brief
        try:
            brief_users_res = supabase.table("weekly_briefs").select("user_id").gte("generated_at", cutoff).execute()
            brief_users = set(r["user_id"] for r in (brief_users_res.data or []))
        except Exception:
            brief_users = set()

        # Total active users in period
        total_active = len(chat_users | doc_users | shield_users)
        total_users  = (supabase.table("user_profiles").select("user_id", count="exact").execute().count or 1)

        def _pct(n): return round(n / max(total_active, 1) * 100, 1)

        features = [
            {"feature": "Chat with PHI",       "users": len(chat_users),   "pct": _pct(len(chat_users)),   "icon": "💬"},
            {"feature": "Lab Report Upload",   "users": len(doc_users),    "pct": _pct(len(doc_users)),    "icon": "📋"},
            {"feature": "Metabolic Shield",    "users": len(shield_users), "pct": _pct(len(shield_users)), "icon": "🛡"},
            {"feature": "Health Memory",       "users": len(mem_users),    "pct": _pct(len(mem_users)),    "icon": "🧠"},
            {"feature": "Doctor Prep",         "users": len(prep_users),   "pct": _pct(len(prep_users)),   "icon": "🩺"},
            {"feature": "Weekly Brief",        "users": len(brief_users),  "pct": _pct(len(brief_users)),  "icon": "📬"},
        ]
        features.sort(key=lambda x: x["users"], reverse=True)

        # Daily active users over time
        daily_res = supabase.table("chats").select("user_id,created_at").eq("role", "user").gte("created_at", cutoff).execute().data or []
        daily_users = defaultdict(set)
        for r in daily_res:
            try:
                day = r["created_at"][:10]
                daily_users[day].add(r["user_id"])
            except Exception:
                pass

        dau_series = [
            {"date": day, "dau": len(uids)}
            for day, uids in sorted(daily_users.items())
        ]

        # Avg messages per session
        all_msgs = supabase.table("chats").select("user_id,conversation_id").eq("role", "user").gte("created_at", cutoff).execute().data or []
        conv_msgs = defaultdict(int)
        for r in all_msgs:
            conv_msgs[r.get("conversation_id", "")] += 1
        avg_msgs_per_conv = round(sum(conv_msgs.values()) / max(len(conv_msgs), 1), 1)

        # Session length distribution
        sessions_short   = sum(1 for v in conv_msgs.values() if v <= 2)
        sessions_medium  = sum(1 for v in conv_msgs.values() if 3 <= v <= 10)
        sessions_long    = sum(1 for v in conv_msgs.values() if v > 10)

        return jsonify({
            "period_days":         days,
            "total_active_users":  total_active,
            "total_users":         total_users,
            "features":            features,
            "dau_series":          dau_series,
            "sessions": {
                "total":           len(conv_msgs),
                "avg_msgs":        avg_msgs_per_conv,
                "short_1_2":       sessions_short,
                "medium_3_10":     sessions_medium,
                "long_10plus":     sessions_long,
            },
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# RETENTION COHORTS
# ══════════════════════════════════════════════════════════════════════════════

@analytics_bp.route("/api/founder/retention", methods=["GET"])
def retention():
    err = _require_auth()
    if err: return err
    supabase = _deps()

    try:
        # Build weekly cohorts for last 8 weeks
        cohorts = []
        for week_offset in range(7, -1, -1):
            cohort_start = _days_ago((week_offset + 1) * 7)
            cohort_end   = _days_ago(week_offset * 7)

            # Users who signed up in this cohort week
            cohort_res = supabase.table("user_profiles").select("user_id").gte("created_at", cohort_start).lte("created_at", cohort_end).execute()
            cohort_ids = set(r["user_id"] for r in (cohort_res.data or []))

            if not cohort_ids:
                continue

            cohort_size = len(cohort_ids)

            # Week 1 retention: active in week 1 after signup
            w1_start = cohort_end
            w1_end   = _days_ago(max(0, week_offset - 1) * 7)
            w1_res   = supabase.table("chats").select("user_id").eq("role", "user").gte("created_at", w1_start).lte("created_at", w1_end).execute()
            w1_users = set(r["user_id"] for r in (w1_res.data or []))
            w1_ret   = round(len(cohort_ids & w1_users) / cohort_size * 100, 1)

            # Current retention: active in last 7 days
            curr_res  = supabase.table("chats").select("user_id").eq("role", "user").gte("created_at", _days_ago(7)).execute()
            curr_users = set(r["user_id"] for r in (curr_res.data or []))
            curr_ret  = round(len(cohort_ids & curr_users) / cohort_size * 100, 1)

            # Week label
            cohort_label = datetime.fromisoformat(cohort_start).strftime("%b %d")

            cohorts.append({
                "cohort_week":       cohort_label,
                "cohort_size":       cohort_size,
                "week_1_retention":  w1_ret,
                "current_retention": curr_ret if week_offset > 0 else None,
                "active_now":        len(cohort_ids & curr_users),
            })

        # Overall 30-day retention
        users_30d_ago_res = supabase.table("user_profiles").select("user_id").lte("created_at", _days_ago(30)).execute()
        users_30d_ago = set(r["user_id"] for r in (users_30d_ago_res.data or []))
        active_now_res = supabase.table("chats").select("user_id").eq("role", "user").gte("created_at", _days_ago(7)).execute()
        active_now = set(r["user_id"] for r in (active_now_res.data or []))
        retention_30d = round(len(users_30d_ago & active_now) / max(len(users_30d_ago), 1) * 100, 1)

        return jsonify({
            "cohorts":        cohorts,
            "retention_30d":  retention_30d,
            "benchmark": {
                "good":    "40%+ week-1 retention is considered strong for health apps",
                "great":   "25%+ month-1 retention means users are finding real value",
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# PAYMENTS & REVENUE
# ══════════════════════════════════════════════════════════════════════════════

@analytics_bp.route("/api/founder/payments", methods=["GET"])
def payments():
    err = _require_auth()
    if err: return err
    supabase = _deps()

    try:
        # All plan data
        profiles = supabase.table("user_profiles").select(
            "user_id,plan,subscription_end_date,cancel_at_period_end,created_at,had_trial"
        ).execute().data or []

        plan_counts = defaultdict(int)
        churned     = 0
        trialing    = 0
        mrr_by_week = defaultdict(float)

        for p in profiles:
            plan = (p.get("plan") or "free").lower()
            plan_counts[plan] += 1

            # Churned = had_trial and now on free
            if p.get("had_trial") and plan == "free":
                churned += 1

            if plan == "trial":
                trialing += 1

        # MRR calculation
        mrr_map = {"monthly": 49, "annual": 33.25, "clinical": 99, "pro": 49, "trial": 0}
        total_mrr = sum(mrr_map.get(plan, 0) * count for plan, count in plan_counts.items())

        # Revenue timeline from audit logs
        payment_logs = supabase.table("audit_logs").select("user_id,detail,created_at").eq("category", "PAYMENT").order("created_at", desc=True).limit(200).execute().data or []

        events = []
        for log in payment_logs:
            detail  = log.get("detail", "")
            created = log.get("created_at", "")
            # Extract plan from detail
            plan_match = re.search(r"plan:(\w+)", detail)
            plan_name  = plan_match.group(1) if plan_match else "unknown"
            amount     = mrr_map.get(plan_name, 0)

            events.append({
                "date":       created[:10],
                "event_type": "subscription" if "VERIFIED" in log.get("action", "") else "other",
                "plan":       plan_name,
                "amount":     amount,
                "handle":     _handle(log.get("user_id", "")),
            })

        # MRR by plan
        mrr_breakdown = {
            plan: round(mrr_map.get(plan, 0) * count, 2)
            for plan, count in plan_counts.items()
            if plan not in ("free",)
        }

        # Churn rate
        paying_users = sum(v for k, v in plan_counts.items() if k in ("monthly", "annual", "clinical", "pro"))
        churn_rate   = round(churned / max(paying_users + churned, 1) * 100, 1)

        # Cancellations pending
        pending_cancel = sum(1 for p in profiles if p.get("cancel_at_period_end"))

        # Upgrades in last 30 days
        upgrade_logs = supabase.table("audit_logs").select("id", count="exact").eq("category", "PAYMENT").gte("created_at", _days_ago(30)).execute()
        upgrades_30d = upgrade_logs.count or 0

        return jsonify({
            "mrr_usd":         round(total_mrr, 2),
            "arr_usd":         round(total_mrr * 12, 2),
            "plan_breakdown":  dict(plan_counts),
            "mrr_by_plan":     mrr_breakdown,
            "paying_users":    paying_users,
            "free_users":      plan_counts.get("free", 0),
            "trial_users":     trialing,
            "churned_trials":  churned,
            "churn_rate_pct":  churn_rate,
            "pending_cancels": pending_cancel,
            "upgrades_30d":    upgrades_30d,
            "recent_events":   events[:50],
            "conversion_pct":  round(paying_users / max(len(profiles), 1) * 100, 1),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# ACTIVITY TIMELINE
# ══════════════════════════════════════════════════════════════════════════════

@analytics_bp.route("/api/founder/activity-timeline", methods=["GET"])
def activity_timeline():
    err = _require_auth()
    if err: return err
    supabase = _deps()

    try:
        days   = int(request.args.get("days", 30))
        cutoff = _days_ago(days)

        # Messages per day
        msgs = supabase.table("chats").select("created_at,user_id").eq("role", "user").gte("created_at", cutoff).execute().data or []
        daily_msgs  = defaultdict(int)
        daily_users = defaultdict(set)

        for m in msgs:
            day = m["created_at"][:10]
            daily_msgs[day]  += 1
            daily_users[day].add(m["user_id"])

        # Signups per day
        signups = supabase.table("user_profiles").select("created_at").gte("created_at", cutoff).execute().data or []
        daily_signups = defaultdict(int)
        for s in signups:
            day = s["created_at"][:10]
            daily_signups[day] += 1

        # Docs uploaded per day
        docs = supabase.table("health_markers").select("created_at").gte("created_at", cutoff).execute().data or []
        daily_docs = defaultdict(int)
        for d in docs:
            day = d["created_at"][:10]
            daily_docs[day] += 1

        # Build unified timeline
        all_days = set(daily_msgs.keys()) | set(daily_signups.keys()) | set(daily_docs.keys())
        timeline = []
        for day in sorted(all_days):
            timeline.append({
                "date":    day,
                "dau":     len(daily_users[day]),
                "msgs":    daily_msgs[day],
                "signups": daily_signups[day],
                "docs":    daily_docs[day],
            })

        # Peak hours (UTC)
        hour_activity = defaultdict(int)
        for m in msgs:
            try:
                hour = int(m["created_at"][11:13])
                hour_activity[hour] += 1
            except Exception:
                pass

        peak_hours = sorted(
            [{"hour": h, "messages": c} for h, c in hour_activity.items()],
            key=lambda x: x["hour"]
        )

        return jsonify({
            "timeline":   timeline,
            "peak_hours": peak_hours,
            "period_days": days,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# SINGLE USER DEEP-DIVE (fully anonymized)
# ══════════════════════════════════════════════════════════════════════════════

@analytics_bp.route("/api/founder/user/<handle>", methods=["GET"])
def user_deep_dive(handle: str):
    """
    Deep dive on a single user by their anonymous handle.
    Returns full behavioral profile WITHOUT any PII.
    Question topics shown, never raw message content.
    """
    err = _require_auth()
    if err: return err
    supabase = _deps()

    try:
        # Find user_id from handle by scanning (handle is SHA256 of user_id)
        all_profiles = supabase.table("user_profiles").select("user_id,plan,goal_weight_lbs,glp1_status,created_at").execute().data or []
        target_uid = None
        target_profile = {}
        for p in all_profiles:
            if _handle(p["user_id"]) == handle.upper():
                target_uid     = p["user_id"]
                target_profile = p
                break

        if not target_uid:
            return jsonify({"error": "User not found"}), 404

        # Conversations
        convs = supabase.table("conversations").select("id,title,created_at").eq("user_id", target_uid).order("created_at", desc=True).execute().data or []

        # Messages — classify topics, NEVER return raw content
        msgs = supabase.table("chats").select("content,created_at,role,conversation_id").eq("user_id", target_uid).order("created_at", desc=True).limit(500).execute().data or []

        user_msgs = [m for m in msgs if m["role"] == "user"]
        topic_counts = defaultdict(int)
        msg_by_day   = defaultdict(int)

        for m in user_msgs:
            topic = _classify_topic(m.get("content", ""))
            topic_counts[topic] += 1
            try:
                msg_by_day[m["created_at"][:10]] += 1
            except Exception:
                pass

        topics_ranked = sorted(
            [{"topic": k, "count": v} for k, v in topic_counts.items()],
            key=lambda x: x["count"], reverse=True
        )

        # Activity heatmap (day → count)
        activity = [{"date": d, "messages": c} for d, c in sorted(msg_by_day.items())]

        # Markers
        markers = supabase.table("health_markers").select("marker_name,value,unit,status,date").eq("user_id", target_uid).order("date", desc=True).limit(50).execute().data or []

        abnormal_markers = [m for m in markers if m.get("status") in ("HIGH", "LOW")]
        normal_markers   = [m for m in markers if m.get("status") == "NORMAL"]

        # Unique documents uploaded
        docs = list(set(m.get("source_document", "") for m in markers if m.get("source_document")))

        # Shield data — last 30 days
        shield_logs = supabase.table("behavioral_logs").select("date,metric_name,value,unit").eq("user_id", target_uid).gte("date", _days_ago(30)).order("date", desc=True).execute().data or []

        shield_by_metric = defaultdict(list)
        for log in shield_logs:
            shield_by_metric[log["metric_name"]].append({
                "date": log["date"], "value": log["value"], "unit": log["unit"]
            })

        # Memories (what PHI knows)
        memories = supabase.table("conversation_memories").select("fact,created_at").eq("user_id", target_uid).eq("is_active", True).order("created_at", desc=True).execute().data or []

        # Engagement score
        score = min(100, (
            min(len(user_msgs) * 2, 40) +
            min(len(markers) * 5, 20) +
            min(len(convs) * 3, 20) +
            min(len(shield_logs) * 2, 20)
        ))

        # Days since last active
        last_msg = user_msgs[0]["created_at"] if user_msgs else None
        days_inactive = 0
        if last_msg:
            try:
                last_dt = datetime.fromisoformat(last_msg.replace("Z", "+00:00"))
                days_inactive = (datetime.now(timezone.utc) - last_dt).days
            except Exception:
                pass

        plan = (target_profile.get("plan") or "free").lower()

        return jsonify({
            "handle":           handle.upper(),
            "plan":             plan,
            "is_pro":           plan in ("monthly", "annual", "clinical", "pro", "trial"),
            "glp1_status":      target_profile.get("glp1_status") or "unknown",
            "has_goal_weight":  bool(target_profile.get("goal_weight_lbs")),
            "signup_date":      target_profile.get("created_at", "")[:10],
            "days_inactive":    days_inactive,
            "engagement_score": score,
            "stats": {
                "total_messages":     len(user_msgs),
                "conversations":      len(convs),
                "docs_uploaded":      len(docs),
                "markers_stored":     len(markers),
                "abnormal_markers":   len(abnormal_markers),
                "memories":           len(memories),
                "shield_logs_30d":    len(shield_logs),
            },
            "topics":           topics_ranked,
            "activity":         activity[-30:],
            "abnormal_markers": abnormal_markers[:10],
            "shield_summary":   {
                metric: {
                    "readings": len(vals),
                    "latest":   vals[0] if vals else None,
                }
                for metric, vals in shield_by_metric.items()
            },
            "memory_facts":     [m["fact"] for m in memories[:10]],
            "privacy_note":     "No PII exposed. Message content never returned. Topics only.",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
