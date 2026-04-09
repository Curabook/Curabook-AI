"""
services/unit_normalizer.py
─────────────────────────────────────────────────────────────────────────────
Unit normalization for health markers from Indian lab reports.

FIXES APPLIED:
  #UNIT-1  normalize_marker() now ALSO converts the reference_range string
           to the new unit before re-computing status.
           Previously: value 7.2 mmol/L → 130 mg/dL, but reference_range
           "70-100" (in mg/dL) → correct status.  ✓ (coincidentally right)
           But: value 120 g/L → 12.0 g/dL, reference_range "12.0-15.5" (g/dL)
           If reference_range was stored as "120-155" (g/L), the old code
           re-compared 12.0 against "120-155" → flagged LOW incorrectly.
           Fix: detect if ref_range is in old unit and convert it too.

  #UNIT-2  If reference_range conversion is not possible (unknown range format
           or different unit), skip status re-computation and preserve the
           original status. Never set a false "CRITICAL" flag due to unit confusion.
"""

from __future__ import annotations
import re


# ── Per-marker conversion factors ────────────────────────────────────────────
_MARKER_CONVERSION: dict[str, float] = {
    "glucose":           18.018,
    "blood glucose":     18.018,
    "fasting glucose":   18.018,
    "ldl":               38.67,
    "hdl":               38.67,
    "cholesterol":       38.67,
    "total cholesterol": 38.67,
    "triglycerides":     88.57,
    "triglyceride":      88.57,
    "creatinine":        0.01131,
    "uric acid":         0.01681,
    "bilirubin":         0.05848,
    "calcium":           0.2495,
    "phosphorus":        3.097,
    "magnesium":         0.2431,
    "urea":              6.006,
    "bun":               2.801,
}

# Canonical units per marker (what Indian reference ranges are expressed in)
_CANONICAL_UNITS: dict[str, str] = {
    "glucose":           "mg/dL",
    "blood glucose":     "mg/dL",
    "fasting glucose":   "mg/dL",
    "hba1c":             "%",
    "ldl":               "mg/dL",
    "hdl":               "mg/dL",
    "cholesterol":       "mg/dL",
    "total cholesterol": "mg/dL",
    "triglycerides":     "mg/dL",
    "triglyceride":      "mg/dL",
    "hemoglobin":        "g/dL",
    "haemoglobin":       "g/dL",
    "creatinine":        "mg/dL",
    "uric acid":         "mg/dL",
    "vitamin d":         "ng/mL",
    "vitamin b12":       "pg/mL",
    "ferritin":          "ng/mL",
    "tsh":               "mIU/mL",
    "bilirubin":         "mg/dL",
    "alt":               "U/L",
    "ast":               "U/L",
    "egfr":              "mL/min/1.73m²",
    "calcium":           "mg/dL",
}


def normalize_marker(marker: dict) -> dict:
    """
    Normalize a marker's value AND reference_range to match the same unit.

    Fix #UNIT-1: After converting the value, also convert the reference_range
    string so that status re-computation compares like-for-like.

    Fix #UNIT-2: If we cannot convert the reference_range (unknown format,
    cannot parse numbers), skip status re-computation entirely and preserve
    the original status. A preserved status is always safer than a false flag.

    Input:
      { "marker": "Glucose", "value": 7.2, "unit": "mmol/L",
        "reference_range": "70-100", ... }

    Output (modified in place + returned):
      { ..., "value": 129.7, "unit": "mg/dL",
        "original_value": 7.2, "original_unit": "mmol/L",
        "reference_range": "70-100",    ← ref_range was already in mg/dL
        "unit_normalized": True }
    """
    value        = marker.get("value")
    unit         = str(marker.get("unit", "")).strip()
    ref_range    = str(marker.get("reference_range", "")).strip()
    marker_name  = str(marker.get("marker", "")).strip().lower()

    if value is None or not unit:
        return marker

    # Step 1 — detect what unit the reference range implies
    ref_unit = _infer_reference_unit(ref_range, marker_name)
    if not ref_unit:
        return marker

    # Step 2 — check if value unit and reference unit already match
    unit_lower     = unit.lower().replace(" ", "")
    ref_unit_lower = ref_unit.lower().replace(" ", "")

    if _units_match(unit_lower, ref_unit_lower):
        marker["unit_normalized"] = False
        return marker

    # Step 3 — find conversion factor
    factor = _get_conversion_factor(marker_name, unit_lower, ref_unit_lower)
    if factor is None:
        marker["unit_mismatch_possible"] = True
        marker["unit_normalized"]        = False
        print(f"[UNIT] Cannot convert {unit} → {ref_unit} for {marker_name} — flagged")
        return marker

    # Step 4 — convert VALUE
    original_value = value
    original_unit  = unit
    new_value      = round(float(value) * factor, 2)

    marker["original_value"]  = original_value
    marker["original_unit"]   = original_unit
    marker["value"]           = new_value
    marker["unit"]            = ref_unit
    marker["unit_normalized"] = True

    print(
        f"[UNIT] Normalized {marker_name}: "
        f"{original_value} {original_unit} → {new_value} {ref_unit} (×{factor})"
    )

    # Step 5 — FIX #UNIT-1: convert reference_range to new unit if it's in old unit
    # Detect whether the ref_range was written in the OLD unit or NEW unit.
    # Strategy: parse the numbers from ref_range, check if they "look like" old-unit values.
    # If yes, convert them. If not, the ref_range was already in the target unit.
    converted_ref_range = _convert_reference_range(ref_range, factor, original_unit, ref_unit, marker_name)

    # Step 6 — FIX #UNIT-2: only recompute status if we could convert the ref_range
    if converted_ref_range is not None:
        # We successfully converted (or confirmed) the reference range
        marker["reference_range"] = converted_ref_range
        from health_memory.extractor import _compute_status as _cs
        marker["status"] = _cs(new_value, converted_ref_range)
        print(f"[UNIT] Ref range converted: '{ref_range}' → '{converted_ref_range}'")
    else:
        # FIX #UNIT-2: Can't safely convert ref_range — preserve original status
        # Do NOT re-compute, because comparing new_value against the old-unit ref_range
        # would produce a meaningless / false status flag.
        print(
            f"[UNIT] Cannot convert ref_range '{ref_range}' for {marker_name} — "
            f"preserving original status '{marker.get('status', 'UNKNOWN')}'"
        )

    return marker


def normalize_markers(markers: list[dict]) -> list[dict]:
    """Normalize a list of markers. Returns the same list, modified in place."""
    return [normalize_marker(m) for m in markers]


# ── Reference range conversion ────────────────────────────────────────────────

def _convert_reference_range(
    ref_range:     str,
    factor:        float,
    original_unit: str,
    target_unit:   str,
    marker_name:   str,
) -> str | None:
    """
    Attempt to convert the reference_range string from original_unit to target_unit.

    Returns the converted string if successful, or None if:
      - The format is unrecognizable
      - The numbers are already in target unit (no conversion needed)
      - Conversion would produce implausible values

    Handles formats: "70-100", "<100", ">40", "0.4-4.0"
    """
    if not ref_range or factor == 0:
        return None

    ref_stripped = ref_range.strip()

    # Detect if ref_range is already in the TARGET unit (not the old unit).
    # Heuristic: if the numbers in ref_range already "fit" the target-unit scale
    # for this marker, they don't need conversion.
    if _ref_range_matches_unit(ref_stripped, target_unit, marker_name):
        # Already in target unit — return as-is (no conversion needed)
        return ref_stripped

    # Try to convert the numbers in the ref_range string
    try:
        if ref_stripped.startswith("<"):
            old_val = float(ref_stripped[1:])
            return f"<{round(old_val * factor, 2)}"

        if ref_stripped.startswith(">"):
            old_val = float(ref_stripped[1:])
            return f">{round(old_val * factor, 2)}"

        if "-" in ref_stripped:
            parts = ref_stripped.split("-", 1)
            lo = float(parts[0].strip())
            hi = float(parts[1].strip())
            return f"{round(lo * factor, 2)}-{round(hi * factor, 2)}"

    except (ValueError, AttributeError):
        pass

    return None


def _ref_range_matches_unit(ref_range: str, unit: str, marker_name: str) -> bool:
    """
    Heuristic check: do the numbers in ref_range look like they're already
    in the target unit (not the original unit that needs conversion)?

    Used to avoid double-converting a reference range that was already written
    in the canonical unit.
    """
    # Extract the first numeric value from the reference range
    nums = re.findall(r'\d+\.?\d*', ref_range)
    if not nums:
        return True  # No numbers — treat as already correct, skip conversion

    try:
        max_ref = max(float(n) for n in nums)
        unit_lower = unit.lower().replace(" ", "")

        # mg/dL glucose: typically 70–126 range → numbers are large (>10)
        if "glucose" in marker_name and "mg/dl" in unit_lower:
            return max_ref > 10   # mmol/L values are < 10; mg/dL values are > 10

        # g/dL hemoglobin: typically 12–17 range
        if ("hemoglobin" in marker_name or "haemoglobin" in marker_name) and "g/dl" in unit_lower:
            return max_ref < 25   # g/L values would be 120–170; g/dL are 12–17

        # mg/dL cholesterol/LDL/HDL: typically 40–300 range
        if any(k in marker_name for k in ("ldl", "hdl", "cholesterol")) and "mg/dl" in unit_lower:
            return max_ref > 10   # mmol/L values < 10; mg/dL values > 10

    except (ValueError, TypeError):
        pass

    return False  # Default: assume conversion is needed


# ── Helpers ───────────────────────────────────────────────────────────────────

def _infer_reference_unit(ref_range: str, marker_name: str) -> str | None:
    if not ref_range:
        return None

    # Check canonical lookup first
    for key, canonical in _CANONICAL_UNITS.items():
        if key in marker_name:
            return canonical

    # Infer from reference range magnitude for common cases
    nums = re.findall(r'\d+\.?\d*', ref_range)
    if nums:
        try:
            max_ref = max(float(n) for n in nums)
            if "glucose" in marker_name or "blood sugar" in marker_name:
                return "mg/dL" if max_ref > 20 else "mmol/L"
            if "cholesterol" in marker_name or "ldl" in marker_name or "hdl" in marker_name:
                return "mg/dL" if max_ref > 10 else "mmol/L"
            if "hemoglobin" in marker_name or "haemoglobin" in marker_name:
                return "g/dL" if max_ref < 25 else "g/L"
        except (ValueError, TypeError):
            pass

    return None


def _units_match(unit_a: str, unit_b: str) -> bool:
    _normalize = {
        "μg/dl": "ug/dl", "µg/dl": "ug/dl",
        "μmol/l": "umol/l", "µmol/l": "umol/l",
        "μiu/ml": "uiu/ml", "µiu/ml": "uiu/ml",
    }
    a = _normalize.get(unit_a, unit_a)
    b = _normalize.get(unit_b, unit_b)
    return a == b


def _get_conversion_factor(marker_name: str, from_unit: str, to_unit: str) -> float | None:
    # Look up per-marker factor
    for key, factor in _MARKER_CONVERSION.items():
        if key in marker_name:
            if "mmol" in from_unit and "mg" in to_unit:
                return factor
            if "mg" in from_unit and "mmol" in to_unit:
                return round(1.0 / factor, 6) if factor != 0 else None

    # Generic conversions
    if "g/l" in from_unit and "g/dl" in to_unit:      return 0.1
    if "g/dl" in from_unit and "g/l" in to_unit:       return 10.0
    if "nmol/l" in from_unit and "ng/ml" in to_unit:   return 0.4006
    if "ng/ml" in from_unit and "nmol/l" in to_unit:   return 2.496
    if "pmol/l" in from_unit and "pg/ml" in to_unit:   return 1.355
    if "ug/l" in from_unit and "ng/ml" in to_unit:     return 1.0
    if "miu/l" in from_unit and "miu/ml" in to_unit:   return 1.0

    return None