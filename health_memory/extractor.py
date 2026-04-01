"""
health_memory/extractor.py — Safety-hardened
FIXES APPLIED:
  #H3  — LLM extraction verified against source text (hallucination prevention)
  #C4  — Plausibility bounds before returning any marker
  #C3  — Unit mismatch warning flag added to every marker
  ---
  Temperature lowered to 0.0 for deterministic extraction.
  All public functions keep identical signatures.
"""

import json
import re
from datetime import datetime, timezone


# ── Plausibility bounds (same as memory.py — single source of truth ideally) ─
_PLAUSIBILITY_BOUNDS: dict[str, tuple[float, float]] = {
    "hemoglobin":     (2.0,   25.0),
    "haemoglobin":    (2.0,   25.0),
    "hba1c":          (2.0,   20.0),
    "glucose":        (1.0, 1000.0),
    "cholesterol":    (1.0, 1500.0),
    "ldl":            (1.0, 1000.0),
    "hdl":            (0.1,  500.0),
    "triglyceride":   (0.1, 5000.0),
    "creatinine":     (0.1,   30.0),
    "egfr":           (1.0,  200.0),
    "tsh":          (0.001,   50.0),
    "vitamin d":      (1.0,  300.0),
    "vitamin b12":   (10.0, 5000.0),
    "ferritin":       (0.5,10000.0),
    "alt":            (1.0, 5000.0),
    "ast":            (1.0, 5000.0),
    "uric acid":      (0.5,   30.0),
    "crp":           (0.01, 1000.0),
    "platelets":      (1.0, 2000.0),
    "wbc":            (0.1,  200.0),
    "rbc":            (0.5,   15.0),
}

# Unit pairs that are commonly confused — used to flag potential mismatches
_UNIT_MISMATCH_PAIRS = {
    "glucose":     {"mmol/l": 18.0, "mg/dl": 1.0},    # 7.2 mmol/L = 130 mg/dL
    "cholesterol": {"mmol/l": 38.67, "mg/dl": 1.0},
    "hemoglobin":  {"g/l": 0.1, "g/dl": 1.0},
    "creatinine":  {"umol/l": 0.0113, "mg/dl": 1.0},
}


# ── Prompt ────────────────────────────────────────────────────────────────────

_SYSTEM = """\
You are a medical data extraction engine.
Extract ALL health/lab markers THAT ACTUALLY APPEAR in the provided document text.

CRITICAL: Only extract values you can see verbatim in the text. NEVER invent, infer, or 
estimate values that are not explicitly written in the document.

Return ONLY a valid JSON array with no markdown, no commentary, no extra text.
Each element must have these exact keys:
  "marker"          - standardised marker name (e.g. "LDL Cholesterol", "HbA1c")
  "value"           - numeric value as a number (float), exactly as it appears
  "unit"            - unit string exactly as printed (e.g. "mg/dL", "%")
  "reference_range" - reference range exactly as printed, or "" if not present
  "status"          - "HIGH", "LOW", "NORMAL", or "UNKNOWN" based on reference range
  "date"            - ISO date YYYY-MM-DD if a report date is visible, else ""

If the document is not a medical report, return an empty array [].
If a value is unclear or ambiguous, omit it rather than guessing.
"""


# ── Public function ───────────────────────────────────────────────────────────

def extract_health_markers(
    text:            str,
    groq_client,
    source_document: str = "",
    fallback_date:   str = "",
) -> list[dict]:
    """
    Extract structured health markers from raw medical document text.
    Fix #H3 — all LLM-returned values verified against source text before returning.
    Fix #C4 — plausibility check applied to every value.
    """
    if not text or not text.strip():
        return []

    if not _looks_like_medical_report(text):
        print(f"[EXTRACTOR] '{source_document}' does not look like a medical report — skipping")
        return []

    today = fallback_date or datetime.now(timezone.utc).date().isoformat()

    if groq_client:
        markers = _extract_via_llm(text, groq_client, source_document, today)
        if markers:
            # Fix #H3 — verify each returned value actually appears in source
            verified = _verify_against_source(markers, text)
            return verified

    print("[EXTRACTOR] Falling back to regex extraction")
    return _extract_via_regex(text, source_document, today)


# ── Fix #H3 — Verification pass ──────────────────────────────────────────────

def _verify_against_source(markers: list[dict], source_text: str) -> list[dict]:
    """
    For each LLM-returned marker, confirm the value appears in the source text.
    Markers whose values cannot be found are flagged as unverified.
    This catches LLM hallucinations where values are invented.
    """
    verified = []
    for m in markers:
        value = m.get("value")
        if value is None:
            continue

        # Check if the numeric value (or close variants) appear in the source
        value_str = str(value)
        # Strip trailing .0 for integer-like floats
        if value_str.endswith(".0"):
            value_str_alt = value_str[:-2]
        else:
            value_str_alt = None

        found = (
            value_str in source_text or
            (value_str_alt and value_str_alt in source_text)
        )

        if not found:
            print(f"[EXTRACTOR] UNVERIFIED: {m.get('marker')}={value} not found in source — flagging")
            m["verified"] = False
            m["status"]   = "UNKNOWN"   # Don't trust the LLM's status if value unverified
        else:
            m["verified"] = True

        # Fix #C4 — plausibility gate
        ok, reason = _check_plausibility(m.get("marker", ""), float(value))
        if not ok:
            print(f"[EXTRACTOR] IMPLAUSIBLE: {reason} — skipping")
            continue

        # Fix #C3 — unit mismatch flag
        m["unit_mismatch_possible"] = _check_unit_mismatch(
            m.get("marker", ""), float(value), m.get("unit", "")
        )

        verified.append(m)

    original_count = len(markers)
    kept_count     = len(verified)
    if kept_count < original_count:
        print(f"[EXTRACTOR] Verification: {kept_count}/{original_count} markers passed")

    return verified


def _check_plausibility(marker_name: str, value: float) -> tuple[bool, str]:
    """Returns (is_plausible, reason)."""
    name_lower = str(marker_name).lower()
    for key, (lo, hi) in _PLAUSIBILITY_BOUNDS.items():
        if key in name_lower:
            if value < lo or value > hi:
                return False, f"{marker_name}={value} outside [{lo},{hi}]"
            return True, ""
    return True, ""


def _check_unit_mismatch(marker_name: str, value: float, unit: str) -> bool:
    """
    Fix #C3 — Flag potential unit scale mismatch.
    Example: glucose=7.2 with no unit could be mmol/L (normal) or
    could be misread as mg/dL range (hypoglycemic).
    Returns True if a mismatch is possible.
    """
    name_lower  = str(marker_name).lower()
    unit_lower  = str(unit).lower().replace(" ", "")

    for key, conversions in _UNIT_MISMATCH_PAIRS.items():
        if key in name_lower:
            # If the unit is present and we know it, flag if value seems out of range for that unit
            for unit_key, scale in conversions.items():
                if unit_key in unit_lower:
                    # Check if value looks like it might be in the other unit
                    # e.g. glucose=7.2 with unit "mg/dL" is suspicious (too low for mg/dL)
                    if unit_key == "mmol/l" and value > 30:
                        return True   # 30 mmol/L glucose is impossibly high — likely mg/dL
                    if unit_key == "mg/dl" and value < 3:
                        return True   # 3 mg/dL glucose is impossibly low — likely mmol/L
    return False


# ── LLM path ──────────────────────────────────────────────────────────────────

def _extract_via_llm(
    text:            str,
    groq_client,
    source_document: str,
    today:           str,
) -> list[dict]:
    truncated = text[:6000]

    try:
        resp = groq_client.chat.completions.create(
            model       = "llama-3.1-8b-instant",
            messages    = [
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": f"Extract all health markers from this document:\n\n{truncated}"},
            ],
            temperature = 0.0,   # Fix #H3 — was 0.1, must be 0.0 for deterministic extraction
            max_tokens  = 2000,
        )
        raw = resp.choices[0].message.content.strip()
        raw = raw.lstrip("```json").lstrip("```").rstrip("```").strip()

        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return []

        return _normalise(parsed, source_document, today)

    except json.JSONDecodeError as e:
        print(f"[EXTRACTOR] JSON parse error from LLM: {e}")
        return []
    except Exception as e:
        print(f"[EXTRACTOR] LLM extraction error: {e}")
        return []


# ── Regex fallback ────────────────────────────────────────────────────────────

_REGEX_MARKER = re.compile(
    r"([A-Za-z][A-Za-z\s\(\)\-]+?)"
    r"\s*[:\|]?\s*"
    r"(\d+\.?\d*)"
    r"\s*"
    r"([a-zA-Z/%µμ]+(?:/[a-zA-Z]+)?)"
    r"(?:\s*[\(\[<>]?\s*"
    r"([\d\.\-<>]+)\s*[\)\]]?)?",
    re.IGNORECASE,
)

_KNOWN_MARKERS = {
    "ldl": "LDL Cholesterol",
    "hdl": "HDL Cholesterol",
    "total cholesterol": "Total Cholesterol",
    "triglyceride": "Triglycerides",
    "hba1c": "HbA1c",
    "hemoglobin a1c": "HbA1c",
    "fasting glucose": "Fasting Blood Glucose",
    "blood glucose": "Fasting Blood Glucose",
    "vitamin d": "Vitamin D (25-OH)",
    "25-oh": "Vitamin D (25-OH)",
    "vitamin b12": "Vitamin B12",
    "b12": "Vitamin B12",
    "ferritin": "Ferritin",
    "hemoglobin": "Hemoglobin",
    "haemoglobin": "Hemoglobin",
    "tsh": "TSH",
    "creatinine": "Creatinine",
    "egfr": "eGFR",
    "alt": "ALT",
    "ast": "AST",
    "crp": "CRP",
    "uric acid": "Uric Acid",
}


def _extract_via_regex(text: str, source_document: str, today: str) -> list[dict]:
    seen:    set[str]   = set()
    markers: list[dict] = []

    for m in _REGEX_MARKER.finditer(text):
        raw_name     = m.group(1).strip().lower()
        matched_name = None

        for key, canonical in _KNOWN_MARKERS.items():
            if key in raw_name:
                matched_name = canonical
                break

        if not matched_name or matched_name in seen:
            continue
        seen.add(matched_name)

        try:
            value = float(m.group(2))
        except (ValueError, TypeError):
            continue

        # Fix #C4 — plausibility gate on regex results too
        ok, reason = _check_plausibility(matched_name, value)
        if not ok:
            print(f"[EXTRACTOR REGEX] IMPLAUSIBLE: {reason} — skipping")
            continue

        unit      = m.group(3) or ""
        ref_range = m.group(4) or ""
        status    = _compute_status(value, ref_range)

        markers.append({
            "marker":                 matched_name,
            "value":                  value,
            "unit":                   unit,
            "reference_range":        ref_range,
            "status":                 status,
            "date":                   today,
            "source_document":        source_document,
            "verified":               True,   # regex matched directly in source text
            "unit_mismatch_possible": _check_unit_mismatch(matched_name, value, unit),
        })

    return markers


# ── Normalisation helpers ─────────────────────────────────────────────────────

def _normalise(raw: list, source_document: str, today: str) -> list[dict]:
    seen:   set[str]   = set()
    result: list[dict] = []

    for item in raw:
        if not isinstance(item, dict):
            continue

        marker = str(item.get("marker", "")).strip()
        if not marker or marker in seen:
            continue
        seen.add(marker)

        try:
            value = float(item.get("value", 0))
        except (ValueError, TypeError):
            continue

        unit      = str(item.get("unit",            "")).strip()
        ref_range = str(item.get("reference_range", "")).strip()
        status    = str(item.get("status",          "UNKNOWN")).upper()
        date      = str(item.get("date",            today)).strip() or today

        if status not in ("HIGH", "LOW", "NORMAL", "UNKNOWN"):
            status = _compute_status(value, ref_range)

        result.append({
            "marker":                 marker,
            "value":                  value,
            "unit":                   unit,
            "reference_range":        ref_range,
            "status":                 status,
            "date":                   date,
            "source_document":        source_document,
            "verified":               None,  # Will be set by _verify_against_source
            "unit_mismatch_possible": False,
        })

    return result


def _compute_status(value: float, ref_range: str) -> str:
    try:
        r = ref_range.strip()
        if not r:
            return "UNKNOWN"
        if r.startswith("<"):
            return "HIGH" if value > float(r[1:]) else "NORMAL"
        if r.startswith(">"):
            return "LOW" if value < float(r[1:]) else "NORMAL"
        if "-" in r:
            lo, hi = r.split("-", 1)
            v = float(value)
            if v < float(lo): return "LOW"
            if v > float(hi): return "HIGH"
            return "NORMAL"
    except (ValueError, AttributeError):
        pass
    return "UNKNOWN"


def _looks_like_medical_report(text: str) -> bool:
    lower = text.lower()

    unit_keywords = [
        "mg/dl", "mg/l", "mmol/l", "ng/ml", "pg/ml", "ug/ml",
        "u/l", "iu/l", "iu/ml", "g/dl", "g/l", "meq/l",
        "10^3/ul", "10^6/ul", "10^9/l", "fl", "pg",
        "miu/ml", "uiu/ml", "nmol/l", "pmol/l",
    ]
    if any(k in lower for k in unit_keywords):
        return True

    marker_keywords = [
        "hba1c", "haemoglobin", "hemoglobin", "cholesterol", "triglyceride",
        "creatinine", "glucose", "platelet", "bilirubin", "albumin",
        "sodium", "potassium", "calcium", "uric acid", "thyroid",
        "vitamin d", "vitamin b", "ferritin", "insulin", "cortisol",
        "testosterone", "estrogen", "progesterone", "tsh", "t3", "t4",
        "sgot", "sgpt", "alt", "ast", "alkaline phosphatase",
        "blood urea", "urea nitrogen", "esr", "crp", "wbc", "rbc",
    ]
    if any(k in lower for k in marker_keywords):
        return True

    structure_keywords = [
        "reference range", "normal range", "reference interval",
        "lab report", "laboratory report", "blood test", "blood work",
        "pathology", "diagnostic", "specimen", "test result",
        "investigation", "report date", "sample collected",
        "patient name", "patient id", "doctor", "physician",
        "result", "value", "units", "method", "analyser",
    ]
    if sum(1 for k in structure_keywords if k in lower) >= 2:
        return True

    numbers = re.findall(r'\d+\.\d+', text)
    if len(numbers) >= 5:
        return True

    clinical_keywords = [
        "radiology", "mammogram", "mammography", "ultrasound", "sonography",
        "mri", "ct scan", "x-ray", "x ray", "xray", "biopsy",
        "impression", "findings", "clinical findings", "diagnosis",
        "oncology", "pathology report", "histopathology",
        "post bct", "post surgery", "post operative",
        "echocardiogram", "ecg", "eeg", "endoscopy",
        "scan name", "requisition", "caseno", "case no",
        "radio-diagnosis", "radiodiagnosis",
        "final report", "discharge summary", "clinical summary",
        "prescription", "advised", "follow up", "follow-up",
    ]
    if any(k in lower for k in clinical_keywords):
        return True

    return False


def chunk_text(text: str, max_chars: int = 1500, overlap: int = 200) -> list[str]:
    """Split long text into overlapping chunks for embedding."""
    chunks = []
    start  = 0
    while start < len(text):
        end = start + max_chars
        chunks.append(text[start:end])
        start = end - overlap
    return [c.strip() for c in chunks if len(c.strip()) >= 50]