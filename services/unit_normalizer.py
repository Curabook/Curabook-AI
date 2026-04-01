"""
services/unit_normalizer.py
─────────────────────────────────────────────────────────────────────────────
Unit normalization for health markers from Indian lab reports.

Problem:
  Indian lab reports (SRL, Thyrocare, Dr. Lal, Apollo, AIIMS) mix units.
  The same marker can appear in mmol/L (international standard) or mg/dL
  (US/India standard) on different reports, or even on the same page.

  Example failures without normalization:
    Glucose  = 7.2  mmol/L  →  reference <100 mg/dL  →  flagged LOW  (wrong)
    Glucose  = 7.2  mmol/L  →  converted = 130 mg/dL →  NORMAL       (correct)

    Hemoglobin = 120 g/L   →  reference 12-15 g/dL  →  flagged HIGH (wrong)
    Hemoglobin = 120 g/L   →  converted = 12.0 g/dL →  NORMAL       (correct)

This module:
  1. Detects the unit in the extracted marker
  2. Detects the unit implied by the reference range
  3. Converts the value to match the reference range unit
  4. Flags the normalization in the marker dict for audit/display

Usage:
  from services.unit_normalizer import normalize_marker
  marker = normalize_marker(marker)
  # marker now has value in the same unit as its reference_range
"""

from __future__ import annotations
import re


# ── Conversion table ──────────────────────────────────────────────────────────
# Format: (from_unit_pattern, to_unit_pattern, multiply_by, canonical_to_unit)
#
# To convert FROM → TO: new_value = old_value * multiply_by
# The "canonical" unit is what the reference ranges in Indian labs use.

_CONVERSIONS: list[tuple[str, str, float, str]] = [
    # Glucose: mmol/L → mg/dL
    (r"mmol/l",          r"mg/dl",      18.018,  "mg/dL"),
    # Glucose reverse: mg/dL → mmol/L (less common but occurs)
    (r"mg/dl.*glucose",  r"mmol/l",     0.05551, "mmol/L"),

    # Cholesterol, LDL, HDL, Triglycerides: mmol/L → mg/dL
    (r"mmol/l",          r"mg/dl",      38.67,   "mg/dL"),   # cholesterol
    # (same conversion factor — caller must pass marker name for disambiguation)

    # Hemoglobin: g/L → g/dL
    (r"g/l(?!.*dl)",     r"g/dl",       0.1,     "g/dL"),

    # Creatinine: μmol/L or umol/L → mg/dL
    (r"[uμ]mol/l",       r"mg/dl",      0.01131, "mg/dL"),
    # Uric acid: μmol/L → mg/dL
    (r"[uμ]mol/l",       r"mg/dl",      0.01681, "mg/dL"),

    # Vitamin D: nmol/L → ng/mL
    (r"nmol/l",          r"ng/ml",      0.4006,  "ng/mL"),
    # Vitamin D reverse: ng/mL → nmol/L
    (r"ng/ml",           r"nmol/l",     2.496,   "nmol/L"),

    # Vitamin B12 / Folate: pmol/L → pg/mL
    (r"pmol/l",          r"pg/ml",      1.355,   "pg/mL"),

    # Ferritin: μg/L → ng/mL (they are numerically equivalent, just different notation)
    (r"[uμ]g/l",         r"ng/ml",      1.0,     "ng/mL"),

    # TSH: μIU/mL and mIU/L are the same — just normalize notation
    (r"miu/l",           r"miu/ml",     1.0,     "mIU/mL"),
    (r"[uμ]iu/ml",       r"miu/ml",     1.0,     "mIU/mL"),

    # Bilirubin: μmol/L → mg/dL
    (r"[uμ]mol/l",       r"mg/dl",      0.05848, "mg/dL"),
]

# Per-marker conversion factors to handle ambiguity
# (mmol/L → mg/dL depends on the molecule's molecular weight)
_MARKER_CONVERSION: dict[str, float] = {
    "glucose":          18.018,
    "blood glucose":    18.018,
    "fasting glucose":  18.018,
    "ldl":              38.67,
    "hdl":              38.67,
    "cholesterol":      38.67,
    "total cholesterol":38.67,
    "triglycerides":    88.57,
    "triglyceride":     88.57,
    "creatinine":       0.01131,
    "uric acid":        0.01681,
    "bilirubin":        0.05848,
    "calcium":          0.2495,
    "phosphorus":       3.097,
    "magnesium":        0.2431,
    "urea":             6.006,
    "bun":              2.801,   # BUN (blood urea nitrogen) mmol/L → mg/dL
}

# Canonical units per marker (what Indian reference ranges are usually expressed in)
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
    Normalize a marker's value and unit to match its reference range unit.

    Input marker dict (from extractor):
      { "marker": "Glucose", "value": 7.2, "unit": "mmol/L",
        "reference_range": "70-100", ... }

    Output (modified in place + returned):
      { ..., "value": 129.7, "unit": "mg/dL",
        "original_value": 7.2, "original_unit": "mmol/L",
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
        # Can't determine reference unit — can't normalize safely
        return marker

    # Step 2 — check if value unit and reference unit match
    unit_lower    = unit.lower().replace(" ", "")
    ref_unit_lower = ref_unit.lower().replace(" ", "")

    if _units_match(unit_lower, ref_unit_lower):
        # Already matching — no conversion needed
        marker["unit_normalized"] = False
        return marker

    # Step 3 — find the correct conversion factor for this marker
    factor = _get_conversion_factor(marker_name, unit_lower, ref_unit_lower)

    if factor is None:
        # No known conversion — flag as mismatch but don't convert
        marker["unit_mismatch_possible"] = True
        marker["unit_normalized"]        = False
        print(f"[UNIT] Cannot convert {unit} → {ref_unit} for {marker_name} — flagged")
        return marker

    # Step 4 — convert
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
        f"{original_value} {original_unit} → {new_value} {ref_unit} "
        f"(factor: {factor})"
    )

    # Step 5 — recompute status after normalization
    from health_memory.extractor import _compute_status
    marker["status"] = _compute_status(new_value, ref_range)

    return marker


def normalize_markers(markers: list[dict]) -> list[dict]:
    """Normalize a list of markers. Returns the same list, modified in place."""
    return [normalize_marker(m) for m in markers]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _infer_reference_unit(ref_range: str, marker_name: str) -> str | None:
    """
    Infer the unit the reference range is expressed in.

    Strategy:
    1. Check if the reference range contains an explicit unit (rare but occurs)
    2. Fall back to the canonical unit for this marker type
    3. Infer from the numeric magnitude of the reference range

    Returns the inferred unit string or None if unknown.
    """
    if not ref_range:
        return None

    # Check canonical lookup first
    for key, canonical in _CANONICAL_UNITS.items():
        if key in marker_name:
            return canonical

    # Infer from reference range magnitude for common cases
    # Glucose: if reference range contains numbers like 70-100 or >126 → mg/dL
    #          if reference range contains numbers like 3.9-6.1 → mmol/L
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
    """Return True if two unit strings represent the same unit."""
    # Normalize common variations
    _normalize = {
        "μg/dl": "ug/dl",
        "µg/dl": "ug/dl",
        "μmol/l": "umol/l",
        "µmol/l": "umol/l",
        "μiu/ml": "uiu/ml",
        "µiu/ml": "uiu/ml",
        "ng/ml":  "ng/ml",
        "ng/dl":  "ng/dl",
        "pg/ml":  "pg/ml",
    }
    a = _normalize.get(unit_a, unit_a)
    b = _normalize.get(unit_b, unit_b)
    return a == b


def _get_conversion_factor(
    marker_name: str,
    from_unit:   str,
    to_unit:     str,
) -> float | None:
    """
    Return the multiplication factor to convert from_unit → to_unit for
    the given marker. Returns None if no conversion is known.
    """
    # Look up per-marker factor
    for key, factor in _MARKER_CONVERSION.items():
        if key in marker_name:
            # Check direction
            if "mmol" in from_unit and "mg" in to_unit:
                return factor
            if "mg" in from_unit and "mmol" in to_unit:
                return round(1.0 / factor, 6) if factor != 0 else None

    # Generic conversions
    if "g/l" in from_unit and "g/dl" in to_unit:
        return 0.1
    if "g/dl" in from_unit and "g/l" in to_unit:
        return 10.0
    if "nmol/l" in from_unit and "ng/ml" in to_unit:
        return 0.4006
    if "ng/ml" in from_unit and "nmol/l" in to_unit:
        return 2.496
    if "pmol/l" in from_unit and "pg/ml" in to_unit:
        return 1.355
    if "ug/l" in from_unit and "ng/ml" in to_unit:
        return 1.0    # μg/L and ng/mL are numerically equivalent
    if "miu/l" in from_unit and "miu/ml" in to_unit:
        return 1.0    # mIU/L = mIU/mL

    return None