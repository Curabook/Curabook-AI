"""
services/payment_feature_access.py
Feature gating: free tier has limits, monthly/annual/pro have full access.
No trial tier. No clinical tier — PA Architect etc. are gated to any paid plan.
"""

_PRO_PLANS = {"monthly", "annual", "pro"}

_FEATURE_GATES = {
    "pa_architect":       _PRO_PLANS,
    "insurance_advocacy": _PRO_PLANS,
    "unlimited_reports":  _PRO_PLANS,
    "health_memory":      _PRO_PLANS,
    "doctor_prep":        _PRO_PLANS,
    "weekly_briefs":      _PRO_PLANS,
}

_REQUIRED_PLAN_LABEL = {
    "pa_architect":       "annual",
    "insurance_advocacy": "annual",
    "unlimited_reports":  "annual",
    "health_memory":      "annual",
    "doctor_prep":        "annual",
    "weekly_briefs":      "annual",
}


def _is_free_all_enabled(supabase) -> bool:
    try:
        cfg = supabase.table("app_config").select("value").eq("key", "free_all_enabled").limit(1).execute()
        return bool(cfg.data and cfg.data[0].get("value") == "true")
    except Exception:
        return False


def check_user_feature_access(supabase, user_id: str, feature: str) -> tuple[bool, str]:
    """
    Returns (allowed, reason). Checks the founder free-all override first,
    then checks the user's plan against the feature's required plans.
    """
    if _is_free_all_enabled(supabase):
        return True, ""

    gates = _FEATURE_GATES.get(feature)
    if not gates:
        return True, ""  # unknown feature — allow by default

    try:
        res = supabase.table("user_profiles").select("plan").eq("user_id", user_id).limit(1).execute()
        plan = (res.data[0].get("plan") or "free").lower() if res.data else "free"
    except Exception:
        return False, "Could not verify your plan. Please try again."

    if plan in gates:
        return True, ""

    return False, "This feature requires a Shield plan (Monthly or Annual). Upgrade to unlock."


def get_required_plan(feature: str) -> str:
    return _REQUIRED_PLAN_LABEL.get(feature, "annual")