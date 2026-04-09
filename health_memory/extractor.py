"""
health_memory/extractor.py
─────────────────────────────────────────────────────────────────────────────
FIX #VERIFY-1  _verify_against_source() previously used strict string
               matching: if the LLM returned 5.7 but the PDF text had "5.70",
               the verification failed and the marker was flagged as
               unverified / set to UNKNOWN status — even though the value
               was perfectly correct.

               New logic:
               1. Try exact string match first (fast, zero false-positives).
               2. On miss: try numeric comparison with ±0.05 tolerance.
                  Scan all numeric tokens in the source text for any value
                  that rounds to the same value as the extracted number.
               3. On miss: try common formatting variants:
                  - strip trailing zeros  (5.70 → 5.7)
                  - integer form          (5.0  → 5)
                  - comma decimal         (5,7  → 5.7 in some European labs)
               If any variant matches, the marker is verified=True.
"""

import json
import re
from datetime import datetime, timezone


# ── Plausibility bounds ───────────────────────────────────────────────────────
_PLAUSIBILITY_BOUNDS: dict[str, tuple[float, float]] = {
    "hemoglobin":   (2.0,    25.0),
    "haemoglobin":  (2.0,    25.0),
    "hba1c":        (2.0,    20.0),
    "glucose":      (1.0,  1000.0),
    "cholesterol":  (1.0,  1500.0),
    "ldl":          (1.0,  1000.0),
    "hdl":          (0.1,   500.0),
    "triglyceride": (0.1,  5000.0),
    "creatinine":   (0.1,    30.0),
    "egfr":         (1.0,   200.0),
    "tsh":        (0.001,    50.0),
    "vitamin d":    (1.0,   300.0),
    "vitamin b12": (10.0,  5000.0),
    "ferritin":     (0.5, 10000.0),
    "alt":          (1.0,  5000.0),
    "ast":          (1.0,  5000.0),
    "uric acid":    (0.5,    30.0),
    "crp":         (0.01,  1000.0),
    "platelets":    (1.0,  2000.0),
    "wbc":          (0.1,   200.0),
    "rbc":          (0.5,    15.0),
}

_UNIT_MISMATCH_PAIRS = {
    "glucose":     {"mmol/l": 18.0, "mg/dl": 1.0},
    "cholesterol": {"mmol/l": 38.67, "mg/dl": 1.0},
    "hemoglobin":  {"g/l": 0.1, "g/dl": 1.0},
    "creatinine":  {"umol/l": 0.0113, "mg/dl": 1.0},
}

# Numeric tolerance for verification (FIX #VERIFY-1)
_VERIFY_TOLERANCE = 0.05


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
    if not text or not text.strip():
        return []

    if not _looks_like_medical_report(text):
        print(f"[EXTRACTOR] '{source_document}' does not look like a medical report — skipping")
        return []

    today = fallback_date or datetime.now(timezone.utc).date().isoformat()

    if groq_client:
        markers = _extract_via_llm(text, groq_client, source_document, today)
        if markers:
            verified = _verify_against_source(markers, text)
            return verified

    print("[EXTRACTOR] Falling back to regex extraction")
    return _extract_via_regex(text, source_document, today)


# ── FIX #VERIFY-1 ─────────────────────────────────────────────────────────────

def _verify_against_source(markers: list[dict], source_text: str) -> list[dict]:
    """
    Verify that each LLM-extracted numeric value actually appears in source_text.

    FIX #VERIFY-1: Uses numeric tolerance (±0.05) and formatting variants
    instead of strict string equality.  This handles:
      - Trailing zeros:       "5.70" in PDF vs 5.7 from LLM
      - Integer forms:        "12.0" in PDF vs 12.0 → also try "12"
      - Comma decimals:       "5,7"  in some European lab reports
      - Rounding differences: "0.820" vs 0.82

    Pre-computes a set of all numeric tokens from the source text once,
    then checks each marker value against that set — O(markers) not O(markers×text).
    """
    # Pre-extract all numeric tokens from source text once
    source_numbers = _extract_source_numbers(source_text)

    verified = []
    for m in markers:
        value = m.get("value")
        if value is None:
            continue

        try:
            fval = float(value)
        except (ValueError, TypeError):
            continue

        found = _value_in_source(fval, source_text, source_numbers)

        if not found:
            print(f"[EXTRACTOR] UNVERIFIED: {m.get('marker')}={value} not found in source — flagging")
            m["verified"] = False
            m["status"]   = "UNKNOWN"
        else:
            m["verified"] = True

        # Plausibility gate
        ok, reason = _check_plausibility(m.get("marker", ""), fval)
        if not ok:
            print(f"[EXTRACTOR] IMPLAUSIBLE: {reason} — skipping")
            continue

        m["unit_mismatch_possible"] = _check_unit_mismatch(
            m.get("marker", ""), fval, m.get("unit", "")
        )
        verified.append(m)

    kept = len(verified)
    orig = len(markers)
    if kept < orig:
        print(f"[EXTRACTOR] Verification: {kept}/{orig} markers passed")

    return verified


def _extract_source_numbers(source_text: str) -> set[float]:
    """
    Extract every numeric token from the source text as a set of floats.
    Handles comma-decimal notation (European: "5,70" → 5.70).
    Called once per document — result is reused for all markers.
    """
    numbers: set[float] = set()
    # Standard decimal numbers
    for tok in re.findall(r'\b\d+\.?\d*\b', source_text):
        try:
            numbers.add(float(tok))
        except ValueError:
            pass
    # Comma-decimal variants (e.g. "5,7" or "12,4")
    for tok in re.findall(r'\b\d+,\d+\b', source_text):
        try:
            numbers.add(float(tok.replace(",", ".")))
        except ValueError:
            pass
    return numbers


def _value_in_source(fval: float, source_text: str, source_numbers: set[float]) -> bool:
    """
    FIX #VERIFY-1: Check if fval (from LLM) can be found in source_text.

    Checks in order of specificity:
    1. Exact string match of the original value representation (fastest)
    2. Common formatting variants (trailing zeros, integer form)
    3. Numeric proximity — any source number within ±tolerance
    """
    val_str = str(fval)

    # 1. Exact string variants
    candidates = {val_str}
    if val_str.endswith(".0"):
        candidates.add(val_str[:-2])           # 12.0 → "12"
    # Strip trailing zeros after decimal: 5.70 → "5.7", 5.700 → "5.7"
    stripped = re.sub(r'(\.\d*?)0+$', r'\1', val_str).rstrip(".")
    candidates.add(stripped)

    for c in candidates:
        if c and c in source_text:
            return True

    # 2. Numeric tolerance against pre-extracted source numbers
    for src_num in source_numbers:
        if abs(src_num - fval) <= _VERIFY_TOLERANCE:
            return True

    return False


# ── Plausibility & unit checks (unchanged) ────────────────────────────────────

def _check_plausibility(marker_name: str, value: float) -> tuple[bool, str]:
    name_lower = str(marker_name).lower()
    for key, (lo, hi) in _PLAUSIBILITY_BOUNDS.items():
        if key in name_lower:
            if value < lo or value > hi:
                return False, f"{marker_name}={value} outside [{lo},{hi}]"
            return True, ""
    return True, ""


def _check_unit_mismatch(marker_name: str, value: float, unit: str) -> bool:
    name_lower = str(marker_name).lower()
    unit_lower = str(unit).lower().replace(" ", "")
    for key, conversions in _UNIT_MISMATCH_PAIRS.items():
        if key in name_lower:
            for unit_key in conversions:
                if unit_key in unit_lower:
                    if unit_key == "mmol/l" and value > 30:
                        return True
                    if unit_key == "mg/dl" and value < 3:
                        return True
    return False


# ── LLM path ──────────────────────────────────────────────────────────────────

def _extract_via_llm(
    text: str, groq_client, source_document: str, today: str,
) -> list[dict]:
    truncated = text[:6000]
    try:
        resp = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": f"Extract all health markers from this document:\n\n{truncated}"},
            ],
            temperature=0.0,
            max_tokens=2000,
        )
        raw = resp.choices[0].message.content.strip()
        raw = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return []
        return _normalise(parsed, source_document, today)
    except json.JSONDecodeError as e:
        print(f"[EXTRACTOR] JSON parse error: {e}")
        return []
    except Exception as e:
        print(f"[EXTRACTOR] LLM extraction error: {e}")
        return []


# ── Regex fallback ────────────────────────────────────────────────────────────

_REGEX_MARKER = re.compile(
    r"([A-Za-z][A-Za-z\s\(\)\-]+?)"
    r"\s*[:\|]?\s*(\d+\.?\d*)\s*([a-zA-Z/%µμ]+(?:/[a-zA-Z]+)?)"
    r"(?:\s*[\(\[<>]?\s*([\d\.\-<>]+)\s*[\)\]]?)?",
    re.IGNORECASE,
)

_KNOWN_MARKERS = {
    "ldl": "LDL Cholesterol", "hdl": "HDL Cholesterol",
    "total cholesterol": "Total Cholesterol", "triglyceride": "Triglycerides",
    "hba1c": "HbA1c", "hemoglobin a1c": "HbA1c",
    "fasting glucose": "Fasting Blood Glucose", "blood glucose": "Fasting Blood Glucose",
    "vitamin d": "Vitamin D (25-OH)", "25-oh": "Vitamin D (25-OH)",
    "vitamin b12": "Vitamin B12", "b12": "Vitamin B12",
    "ferritin": "Ferritin", "hemoglobin": "Hemoglobin", "haemoglobin": "Hemoglobin",
    "tsh": "TSH", "creatinine": "Creatinine", "egfr": "eGFR",
    "alt": "ALT", "ast": "AST", "crp": "CRP", "uric acid": "Uric Acid",
}


def _extract_via_regex(text: str, source_document: str, today: str) -> list[dict]:
    seen: set[str] = set()
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
        ok, reason = _check_plausibility(matched_name, value)
        if not ok:
            print(f"[EXTRACTOR REGEX] IMPLAUSIBLE: {reason} — skipping")
            continue
        unit      = m.group(3) or ""
        ref_range = m.group(4) or ""
        markers.append({
            "marker": matched_name, "value": value, "unit": unit,
            "reference_range": ref_range,
            "status": _compute_status(value, ref_range),
            "date": today, "source_document": source_document,
            "verified": True,
            "unit_mismatch_possible": _check_unit_mismatch(matched_name, value, unit),
        })
    return markers


# ── Normalisation helpers ─────────────────────────────────────────────────────

def _normalise(raw: list, source_document: str, today: str) -> list[dict]:
    seen: set[str] = set()
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
            "marker": marker, "value": value, "unit": unit,
            "reference_range": ref_range, "status": status,
            "date": date, "source_document": source_document,
            "verified": None, "unit_mismatch_possible": False,
        })
    return result


def _compute_status(value: float, ref_range: str) -> str:
    try:
        r = ref_range.strip()
        if not r: return "UNKNOWN"
        if r.startswith("<"):
            return "HIGH" if value > float(r[1:]) else "NORMAL"
        if r.startswith(">"):
            return "LOW"  if value < float(r[1:]) else "NORMAL"
        if "-" in r:
            lo, hi = r.split("-", 1)
            if float(value) < float(lo): return "LOW"
            if float(value) > float(hi): return "HIGH"
            return "NORMAL"
    except (ValueError, AttributeError):
        pass
    return "UNKNOWN"


def _looks_like_medical_report(text: str) -> bool:
    lower = text.lower()
    unit_kw = [
        "mg/dl","mg/l","mmol/l","ng/ml","pg/ml","ug/ml","u/l","iu/l","iu/ml",
        "g/dl","g/l","meq/l","10^3/ul","10^6/ul","miu/ml","nmol/l","pmol/l",
    ]
    if any(k in lower for k in unit_kw): return True
    marker_kw = [
        "hba1c","haemoglobin","hemoglobin","cholesterol","triglyceride","creatinine",
        "glucose","platelet","bilirubin","albumin","sodium","potassium","ferritin",
        "vitamin","tsh","sgot","sgpt","alt","ast","wbc","rbc","crp",
    ]
    if any(k in lower for k in marker_kw): return True
    struct_kw = [
        "reference range","normal range","lab report","blood test","pathology",
        "specimen","test result","investigation","report date","sample collected",
    ]
    if sum(1 for k in struct_kw if k in lower) >= 2: return True
    if len(re.findall(r'\d+\.\d+', text)) >= 5: return True
    clinical_kw = [
        "radiology","mammogram","ultrasound","mri","ct scan","x-ray","biopsy",
        "impression","findings","diagnosis","echocardiogram","ecg","endoscopy",
        "scan name","requisition","final report","discharge summary","prescription",
    ]
    if any(k in lower for k in clinical_kw): return True
    return False


def chunk_text(text: str, max_chars: int = 1500, overlap: int = 200) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        end = start + max_chars
        chunks.append(text[start:end])
        start = end - overlap
    return [c.strip() for c in chunks if len(c.strip()) >= 50]