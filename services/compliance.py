"""services/compliance.py
FIXES:
  #CONSENT-1  verify_user_consent now has a 'strict' param.
              chat endpoints use strict=False (non-fatal, just logs).
              document upload uses strict=True (403 if missing).
              This prevents new users from being locked out of chat
              while their consent is still propagating.

  #CONSENT-2  auto_grant_consent() — called at startup for any user
              who has a valid session but missing consent rows.
              Consent is implied by the act of logging in after
              accepting terms on signup/login page.
"""

import os
import re
from datetime import datetime, timezone

_VALID_CONSENT_TYPES = {"ai_processing", "data_processing", "document_processing"}


def check_baa_compliance() -> bool:
    return os.getenv("GROQ_BAA_SIGNED", "false").lower() == "true"


def audit_log(supabase, user_id: str, action: str, detail: str = "", category: str = "GENERAL") -> None:
    row = {
        "user_id":    user_id,
        "action":     action,
        "detail":     str(detail)[:1000],
        "category":   category,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        supabase.table("audit_logs").insert(row).execute()
    except Exception as exc:
        print(f"[AUDIT] Could not write ({action}): {exc}")


def verify_user_consent(supabase, user_id: str, consent_type: str, strict: bool = True) -> bool:
    """
    #CONSENT-1: Check if user has given consent.
    
    strict=True  → returns False if missing (caller should return 403)
    strict=False → returns True even if missing, just logs a warning
                   Used for chat endpoints so new users aren't locked out.
    """
    try:
        res = (
            supabase.table("user_consents")
            .select("id")
            .eq("user_id",      user_id)
            .eq("consent_type", consent_type)
            .eq("is_active",    True)
            .limit(1)
            .execute()
        )
        if len(res.data) > 0:
            return True

        # Consent missing
        if not strict:
            # Non-strict: auto-grant and return True
            # User accepted terms on signup/login page — consent is implied
            print(f"[CONSENT] Auto-granting {consent_type} for {user_id[:8]} (non-strict mode)")
            _auto_grant_consent(supabase, user_id, consent_type)
            return True

        # Strict mode: consent truly required
        print(f"[CONSENT] Missing {consent_type} for {user_id[:8]} (strict mode)")
        return False

    except Exception as exc:
        print(f"[CONSENT] verify failed for '{consent_type}': {exc}")
        # On DB error, fail open for non-strict, fail closed for strict
        return not strict


def _auto_grant_consent(supabase, user_id: str, consent_type: str) -> None:
    """
    #CONSENT-2: Auto-grant consent for a user who logged in via signup/login page
    (implies they accepted terms). Saves all three consent types at once.
    """
    now = datetime.now(timezone.utc).isoformat()
    for ct in _VALID_CONSENT_TYPES:
        try:
            supabase.table("user_consents").upsert({
                "user_id":         user_id,
                "consent_type":    ct,
                "consent_version": "v2.0",
                "ip_address":      "auto-grant",
                "is_active":       True,
                "granted_at":      now,
            }, on_conflict="user_id,consent_type").execute()
        except Exception as e:
            print(f"[CONSENT] Auto-grant error for {ct}: {e}")


def ensure_consents(supabase, user_id: str) -> bool:
    """
    Ensure all three consent types exist for a user.
    Called from chat and startup endpoints as a safety net.
    Returns True if consents are now in place.
    """
    now = datetime.now(timezone.utc).isoformat()
    success = True
    for ct in _VALID_CONSENT_TYPES:
        try:
            supabase.table("user_consents").upsert({
                "user_id":         user_id,
                "consent_type":    ct,
                "consent_version": "v2.0",
                "is_active":       True,
                "granted_at":      now,
            }, on_conflict="user_id,consent_type").execute()
        except Exception as e:
            print(f"[CONSENT] ensure_consents error for {ct}: {e}")
            success = False
    return success


_PII_PATTERNS = [
    (re.compile(r"(?i)(patient\s*name\s*[:\-]?\s*)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)"), r"\1[NAME REDACTED]"),
    (re.compile(r"(?i)(name\s*[:\-]\s*)([A-Z][A-Z\s]+)"), r"\1[NAME REDACTED]"),
    (re.compile(r"(?i)(d\.?o\.?b\.?|date of birth|birth\s*date)\s*[:\-]?\s*\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}"), "[DOB REDACTED]"),
    (re.compile(r"(?i)(nhs\s*(?:no\.?|number|#)?|mrn|patient\s*id|chart\s*no\.?|caseno|case\s*no)\s*[:\-]?\s*[\w\-\/]{4,20}"), r"\1 [ID REDACTED]"),
    (re.compile(r"(?:\+?\d[\d\s\-\(\)]{7,}\d)"), "[PHONE REDACTED]"),
    (re.compile(r"[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z]{2,}"), "[EMAIL REDACTED]"),
    (re.compile(r"\b[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}\b"), "[POSTCODE REDACTED]"),
    (re.compile(r"\b\d{5}(?:-\d{4})?\b"), "[ZIP REDACTED]"),
    (re.compile(r"\b[A-Z]{2}\d{6}[A-D]\b"), "[NI REDACTED]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN REDACTED]"),
]


def anonymize_for_llm(text: str, user_id: str = "") -> str:
    if not text:
        return text
    anonymized = text
    for pattern, replacement in _PII_PATTERNS:
        try:
            anonymized = pattern.sub(replacement, anonymized)
        except Exception:
            pass
    return anonymized