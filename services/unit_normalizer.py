"""
services/unit_normalizer.py  —  US-Unit Enforcement Edition
─────────────────────────────────────────────────────────────────────────────
ADDITIONS vs. previous version:

  #US-UNITS-1  force_us_units() — new public function.
               Converts a marker dict from any incoming unit to the D2C
               US-standard output unit (lbs, mg/dL, %, °F).
               Called by document_routes._analyze_inner() before storing
               markers and before returning them to the frontend.

  #US-UNITS-2  _US_CANONICAL_UNITS — maps all markers to their US D2C unit
               preference. Indian lab reports (mmol/L glucose) and UK reports
               (g/L haemoglobin) are both normalized to US output.

  #US-UNITS-3  format_value_us() — formats a numeric value with its US unit
               for display, including comma formatting for weight in lbs.

PRESERVED FIXES:
  #UNIT-1  reference_range converted to new unit before status re-computation.
  #UNIT-2  If reference_range conversion is not possible, preserve original status.
"""

from __future__ import annotations
import re


# ── Per-marker conversion factors (FROM → TO canonical US unit) ──────────────
_MARKER_CONVERSION: dict[str, float] = {
    "glucose":           18.018,   # mmol/L → mg/dL
    "blood glucose":     18.018,
    "fasting glucose":   18.018,
    "ldl":               38.67,    # mmol/L → mg/dL
    "hdl":               38.67,
    "cholesterol":       38.67,
    "total cholesterol": 38.67,
    "triglycerides":     88.57,    # mmol/L → mg/dL
    "triglyceride":      88.57,
    "creatinine":        0.01131,  # µmol/L → mg/dL
    "uric acid":         0.01681,  # µmol/L → mg/dL
    "bilirubin":         0.05848,  # µmol/L → mg/dL
    "calcium":           0.2495,   # mmol/L → mg/dL
    "phosphorus":        3.097,    # mmol/L → mg/dL
    "magnesium":         0.2431,   # mmol/L → mg/dL
    "urea":              6.006,    # mmol/L → mg/dL
    "bun":               2.801,    # mmol/L → mg/dL
    "weight_kg_to_lbs":  2.20462,  # kg → lbs
    "temp_c_to_f":       None,     # special: F = C×9/5+32
}

# ── #US-UNITS-2: US D2C canonical output units ────────────────────────────────
_US_CANONICAL_UNITS: dict[str, str] = {
    # Glucose & diabetes
    "glucose":                   "mg/dL",
    "blood glucose":             "mg/dL",
    "fasting glucose":           "mg/dL",
    "fasting blood glucose":     "mg/dL",
    "hba1c":                     "%",
    "hemoglobin a1c":            "%",

    # Lipids
    "ldl":                       "mg/dL",
    "ldl cholesterol":           "mg/dL",
    "hdl":                       "mg/dL",
    "hdl cholesterol":           "mg/dL",
    "cholesterol":               "mg/dL",
    "total cholesterol":         "mg/dL",
    "triglycerides":             "mg/dL",
    "triglyceride":              "mg/dL",

    # Blood count
    "hemoglobin":                "g/dL",   # NOT g/L
    "haemoglobin":               "g/dL",
    "rbc":                       "M/µL",
    "wbc":                       "K/µL",
    "platelets":                 "K/µL",

    # Kidney
    "creatinine":                "mg/dL",
    "egfr":                      "mL/min/1.73m²",
    "bun":                       "mg/dL",
    "urea":                      "mg/dL",
    "uric acid":                 "mg/dL",

    # Liver
    "alt":                       "U/L",
    "ast":                       "U/L",
    "alp":                       "U/L",
    "ggt":                       "U/L",
    "bilirubin":                 "mg/dL",
    "albumin":                   "g/dL",

    # Vitamins & minerals
    "vitamin d":                 "ng/mL",  # NOT nmol/L
    "vitamin d (25-oh)":         "ng/mL",
    "vitamin b12":               "pg/mL",  # NOT pmol/L
    "ferritin":                  "ng/mL",
    "iron":                      "µg/dL",
    "calcium":                   "mg/dL",
    "magnesium":                 "mg/dL",
    "phosphorus":                "mg/dL",
    "sodium":                    "mEq/L",
    "potassium":                 "mEq/L",

    # Hormones
    "tsh":                       "mIU/L",
    "free t4":                   "ng/dL",
    "free t3":                   "pg/mL",
    "cortisol":                  "µg/dL",
    "testosterone":              "ng/dL",
    "estradiol":                 "pg/mL",

    # Inflammation
    "crp":                       "mg/L",
    "c-reactive protein":        "mg/L",

    # Body metrics — US units for D2C
    "weight":                    "lbs",    # NOT kg
    "body weight":               "lbs",
    "bmi":                       "kg/m²",  # stays metric (universal standard)

    # Temperature
    "temperature":               "°F",     # NOT °C
    "body temperature":          "°F",
}

# ── Conversions needed for US D2C output ─────────────────────────────────────
# (source_unit → us_unit, factor)
_INCOMING_TO_US: dict[str, dict[str, tuple]] = {
    # Glucose: mmol/L → mg/dL
    "glucose":        {"mmol/l": ("mg/dL", 18.018)},
    "blood glucose":  {"mmol/l": ("mg/dL", 18.018)},
    "fasting glucose":{"mmol/l": ("mg/dL", 18.018)},

    # Lipids: mmol/L → mg/dL
    "ldl":            {"mmol/l": ("mg/dL", 38.67)},
    "hdl":            {"mmol/l": ("mg/dL", 38.67)},
    "cholesterol":    {"mmol/l": ("mg/dL", 38.67)},
    "total cholesterol": {"mmol/l": ("mg/dL", 38.67)},
    "triglycerides":  {"mmol/l": ("mg/dL", 88.57)},

    # Hemoglobin: g/L → g/dL
    "hemoglobin":     {"g/l": ("g/dL", 0.1)},
    "haemoglobin":    {"g/l": ("g/dL", 0.1)},

    # Vitamin D: nmol/L → ng/mL
    "vitamin d":      {"nmol/l": ("ng/mL", 0.4006)},
    "vitamin d (25-oh)": {"nmol/l": ("ng/mL", 0.4006)},

    # Vitamin B12: pmol/L → pg/mL
    "vitamin b12":    {"pmol/l": ("pg/mL", 1.355)},

    # Creatinine: µmol/L → mg/dL
    "creatinine":     {"umol/l": ("mg/dL", 0.01131), "µmol/l": ("mg/dL", 0.01131)},

    # Uric acid: µmol/L → mg/dL
    "uric acid":      {"umol/l": ("mg/dL", 0.01681), "µmol/l": ("mg/dL", 0.01681)},

    # Weight: kg → lbs
    "weight":         {"kg": ("lbs", 2.20462)},
    "body weight":    {"kg": ("lbs", 2.20462)},

    # Temperature: °C → °F  (special formula)
    "temperature":    {"°c": ("°F", None), "c": ("°F", None)},
    "body temperature": {"°c": ("°F", None)},
}


# ══════════════════════════════════════════════════════════════════════════════
# #US-UNITS-1: PUBLIC US-UNIT ENFORCEMENT
# ══════════════════════════════════════════════════════════════════════════════

def force_us_units(marker: dict) -> dict:
    """
    #US-UNITS-1: Force a marker dict to US D2C output units.

    This is the D2C output enforcement function — called before any marker
    is returned to the frontend or stored in the database.

    Handles:
      - mmol/L glucose → mg/dL (Indian / UK lab reports)
      - g/L haemoglobin → g/dL (UK lab reports)
      - nmol/L Vitamin D → ng/mL (European lab reports)
      - pmol/L Vitamin B12 → pg/mL (European lab reports)
      - µmol/L creatinine/uric acid → mg/dL
      - kg body weight → lbs (non-US weight reports)
      - °C temperature → °F

    Modifies the marker in-place AND returns it.
    Sets marker["us_converted"] = True if conversion occurred.
    """
    marker_name = str(marker.get("marker_name", marker.get("marker", ""))).strip().lower()
    value       = marker.get("value")
    unit        = str(marker.get("unit", "")).strip().lower().replace(" ", "")

    if value is None or not marker_name or not unit:
        return marker

    # Find conversion table for this marker
    for key, conversions in _INCOMING_TO_US.items():
        if key not in marker_name:
            continue
        if unit not in conversions:
            continue

        target_unit, factor = conversions[unit]

        # Special case: temperature °C → °F
        if factor is None:
            try:
                f_val = float(value) * 9 / 5 + 32
                marker["original_value"] = value
                marker["original_unit"]  = marker.get("unit", "")
                marker["value"]          = round(f_val, 1)
                marker["unit"]           = "°F"
                marker["us_converted"]   = True
                print(f"[US-UNITS] {marker_name}: {value}°C → {f_val:.1f}°F")
            except (ValueError, TypeError):
                pass
            return marker

        # Standard multiplicative conversion
        try:
            original_val = float(value)
            new_val      = round(original_val * factor, 2)
            marker["original_value"] = original_val
            marker["original_unit"]  = marker.get("unit", "")
            marker["value"]          = new_val
            marker["unit"]           = target_unit
            marker["us_converted"]   = True
            print(f"[US-UNITS] {marker_name}: {original_val} {marker['original_unit']} → {new_val} {target_unit} (×{factor})")

            # Convert reference range to match new unit
            ref_range = str(marker.get("reference_range", "")).strip()
            if ref_range:
                converted_ref = _convert_ref_range_us(ref_range, factor)
                if converted_ref:
                    marker["reference_range"] = converted_ref
                    # Recompute status
                    from health_memory.extractor import _compute_status
                    marker["status"] = _compute_status(new_val, converted_ref)

        except (ValueError, TypeError) as e:
            print(f"[US-UNITS] Conversion error for {marker_name}: {e}")

        return marker

    return marker


def force_us_units_batch(markers: list[dict]) -> list[dict]:
    """Force US units on a list of markers. Returns the same list, modified."""
    return [force_us_units(m) for m in markers]


def _convert_ref_range_us(ref_range: str, factor: float) -> str | None:
    """Convert a reference range string by multiplying all numeric values by factor."""
    if not ref_range or factor <= 0:
        return None
    try:
        r = ref_range.strip()
        if r.startswith("<"):
            return f"<{round(float(r[1:]) * factor, 2)}"
        if r.startswith(">"):
            return f">{round(float(r[1:]) * factor, 2)}"
        if "-" in r:
            parts = r.split("-", 1)
            lo = round(float(parts[0].strip()) * factor, 2)
            hi = round(float(parts[1].strip()) * factor, 2)
            return f"{lo}-{hi}"
    except (ValueError, AttributeError):
        pass
    return None


# ── #US-UNITS-3: US display formatting ───────────────────────────────────────

def format_value_us(value, unit: str, marker_name: str = "") -> str:
    """
    #US-UNITS-3: Format a numeric value with its US unit for display.
    Applies comma formatting for weight values in lbs.

    Examples:
      format_value_us(215.5, "lbs", "weight")   → "215.5 lbs"
      format_value_us(172, "mg/dL", "LDL")      → "172 mg/dL"
      format_value_us(5.9, "%", "HbA1c")        → "5.9%"
    """
    try:
        num = float(value)
    except (ValueError, TypeError):
        return f"{value} {unit}".strip()

    unit_lower = unit.lower()

    if "lbs" in unit_lower or "lb" == unit_lower:
        return f"{num:,.1f} lbs"
    if unit == "%":
        return f"{num}%"
    if unit in ("mg/dL", "mg/dl"):
        return f"{int(num) if num == int(num) else num} mg/dL"
    if unit in ("ng/mL", "ng/ml"):
        return f"{num} ng/mL"
    if unit in ("pg/mL", "pg/ml"):
        return f"{int(num) if num == int(num) else num} pg/mL"
    if unit in ("mIU/L", "miu/l"):
        return f"{num} mIU/L"
    if unit in ("g/dL", "g/dl"):
        return f"{num} g/dL"
    if "°F" in unit:
        return f"{num}°F"

    return f"{num} {unit}".strip()


def get_us_canonical_unit(marker_name: str) -> str | None:
    """Return the US D2C canonical unit for a given marker name."""
    lower = marker_name.strip().lower()
    # Exact match first
    if lower in _US_CANONICAL_UNITS:
        return _US_CANONICAL_UNITS[lower]
    # Fragment match
    for key, unit in _US_CANONICAL_UNITS.items():
        if key in lower:
            return unit
    return None


# ══════════════════════════════════════════════════════════════════════════════
# PRESERVED: Original normalize_marker() with UNIT-1 and UNIT-2 fixes
# ══════════════════════════════════════════════════════════════════════════════

def normalize_marker(marker: dict) -> dict:
    """
    Normalize a marker's value AND reference_range to match the same unit.

    Fix #UNIT-1: After converting the value, also convert the reference_range.
    Fix #UNIT-2: If we cannot convert the reference_range, skip status
    re-computation and preserve the original status.

    After normalize_marker(), call force_us_units() to ensure US output.
    """
    value        = marker.get("value")
    unit         = str(marker.get("unit", "")).strip()
    ref_range    = str(marker.get("reference_range", "")).strip()
    marker_name  = str(marker.get("marker", "")).strip().lower()

    if value is None or not unit:
        return marker

    ref_unit = _infer_reference_unit(ref_range, marker_name)
    if not ref_unit:
        return marker

    unit_lower     = unit.lower().replace(" ", "")
    ref_unit_lower = ref_unit.lower().replace(" ", "")

    if _units_match(unit_lower, ref_unit_lower):
        marker["unit_normalized"] = False
        return marker

    factor = _get_conversion_factor(marker_name, unit_lower, ref_unit_lower)
    if factor is None:
        marker["unit_mismatch_possible"] = True
        marker["unit_normalized"]        = False
        print(f"[UNIT] Cannot convert {unit} → {ref_unit} for {marker_name} — flagged")
        return marker

    original_value = value
    original_unit  = unit
    new_value      = round(float(value) * factor, 2)

    marker["original_value"]  = original_value
    marker["original_unit"]   = original_unit
    marker["value"]           = new_value
    marker["unit"]            = ref_unit
    marker["unit_normalized"] = True

    print(f"[UNIT] Normalized {marker_name}: {original_value} {original_unit} → {new_value} {ref_unit} (×{factor})")

    converted_ref_range = _convert_reference_range(ref_range, factor, original_unit, ref_unit, marker_name)

    if converted_ref_range is not None:
        marker["reference_range"] = converted_ref_range
        from health_memory.extractor import _compute_status as _cs
        marker["status"] = _cs(new_value, converted_ref_range)
        print(f"[UNIT] Ref range converted: '{ref_range}' → '{converted_ref_range}'")
    else:
        print(
            f"[UNIT] Cannot convert ref_range '{ref_range}' for {marker_name} — "
            f"preserving original status '{marker.get('status', 'UNKNOWN')}'"
        )

    return marker


def normalize_markers(markers: list[dict]) -> list[dict]:
    """Normalize + enforce US units on a list of markers."""
    normalized = [normalize_marker(m) for m in markers]
    return force_us_units_batch(normalized)


# ── Reference range conversion ────────────────────────────────────────────────

def _convert_reference_range(
    ref_range:     str,
    factor:        float,
    original_unit: str,
    target_unit:   str,
    marker_name:   str,
) -> str | None:
    if not ref_range or factor == 0:
        return None
    ref_stripped = ref_range.strip()
    if _ref_range_matches_unit(ref_stripped, target_unit, marker_name):
        return ref_stripped
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
    nums = re.findall(r'\d+\.?\d*', ref_range)
    if not nums:
        return True
    try:
        max_ref    = max(float(n) for n in nums)
        unit_lower = unit.lower().replace(" ", "")
        if "glucose" in marker_name and "mg/dl" in unit_lower:
            return max_ref > 10
        if ("hemoglobin" in marker_name or "haemoglobin" in marker_name) and "g/dl" in unit_lower:
            return max_ref < 25
        if any(k in marker_name for k in ("ldl", "hdl", "cholesterol")) and "mg/dl" in unit_lower:
            return max_ref > 10
    except (ValueError, TypeError):
        pass
    return False


def _infer_reference_unit(ref_range: str, marker_name: str) -> str | None:
    if not ref_range:
        return None
    # Check canonical US units first
    for key, canonical in _US_CANONICAL_UNITS.items():
        if key in marker_name:
            return canonical
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
    for key, factor in _MARKER_CONVERSION.items():
        if key in marker_name:
            if "mmol" in from_unit and "mg" in to_unit:
                return factor
            if "mg" in from_unit and "mmol" in to_unit:
                return round(1.0 / factor, 6) if factor != 0 else None
    if "g/l" in from_unit and "g/dl" in to_unit:      return 0.1
    if "g/dl" in from_unit and "g/l" in to_unit:       return 10.0
    if "nmol/l" in from_unit and "ng/ml" in to_unit:   return 0.4006
    if "ng/ml" in from_unit and "nmol/l" in to_unit:   return 2.496
    if "pmol/l" in from_unit and "pg/ml" in to_unit:   return 1.355
    if "ug/l" in from_unit and "ng/ml" in to_unit:     return 1.0
    if "miu/l" in from_unit and "miu/ml" in to_unit:   return 1.0
    return None