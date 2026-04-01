"""services/compliance.py"""

import os
import re
from datetime import datetime, timezone


def check_baa_compliance() -> bool:
    """
    BAA check — controls whether Groq LLM is used.
    For development/testing: set GROQ_BAA_SIGNED=true in .env
    For production US launch: get actual BAA signed with Groq at groq.com/legal
    """
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


def verify_user_consent(supabase, user_id: str, consent_type: str) -> bool:
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
        return len(res.data) > 0
    except Exception as exc:
        print(f"[CONSENT] verify failed for '{consent_type}': {exc}")
        return False


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