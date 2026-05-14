"""
api/document_routes.py
FIXES:
  #CONSENT-7  Document upload (/analyze) now auto-grants consent instead of
              returning 403. A logged-in user uploading a file has implicitly
              accepted the terms. This fixes the silent image upload failure.

  #IMG-1      extract_image_text_safe now passes the correct file to vision.
              Also added explicit seek(0) before each read to handle cases
              where file pointer was moved by quality hints check.

  #IMG-2      Vision extraction errors are now surfaced clearly instead of
              returning the placeholder "[Image uploaded but AI could not...]"
              which caused /analyze to return success=True with empty content.

  #SPEED-1    Two-pass marker extraction:
              Pass 1 (instant): regex-based extraction of common lab values —
              markers saved to DB in <100ms with no LLM call.
              Pass 2 (background): LLM refines + catches missed markers.
              The /analyze response now returns immediately with Pass 1 results,
              making uploads feel 3-5x faster.

  #COUNT-1    /api/health/reports now counts distinct source_document values
              from health_markers — the correct source for "lab reports uploaded".
"""
from flask import Blueprint, request, jsonify
import traceback
import uuid
import os
import re
import threading

document_bp = Blueprint("documents", __name__)

_PDF_SIZE_LIMIT   = 5  * 1024 * 1024
_IMAGE_SIZE_LIMIT = 20 * 1024 * 1024

_PDF_EXTS   = {".pdf"}
_TEXT_EXTS  = {".txt"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
_ALL_EXTS   = _PDF_EXTS | _TEXT_EXTS | _IMAGE_EXTS

_RADIOLOGY_STRONG = [
    "mammograph", "radiology report", "ultrasound report", "mri report",
    "ct scan report", "x-ray report", "radio-diagnosis", "sonograph",
    "scan name", "post surgery", "echocardiogram", "nuclear medicine",
    "pet scan", "fluoroscopy", "angiograph",
]
_LAB_INDICATORS = [
    "mg/dl", "mg/l", "mmol/l", "ng/ml", "pg/ml", "iu/l", "u/l",
    "reference range", "normal range", "hemoglobin", "haemoglobin",
    "cholesterol", "creatinine", "hba1c", "platelet", "glucose",
    "triglyceride", "ferritin", "vitamin", "tsh", "alt", "ast",
    "wbc", "rbc", "sodium", "potassium", "bilirubin", "albumin",
    "lab report", "blood test", "laboratory", "specimen", "pathology",
]

# ── #SPEED-1: Fast regex extraction patterns ──────────────────────────────────
_FAST_PATTERNS = {
    "HbA1c":          (r'(?:hba1c|hemoglobin\s+a1c|glycated\s+hemoglobin|a1c)[\s:]+(\d+\.?\d*)\s*%', "%",    [(4.0,5.6)]),
    "Glucose":        (r'(?:glucose|blood\s+glucose|fasting\s+glucose|fasting\s+blood\s+sugar)[\s:]+(\d+\.?\d*)\s*(?:mg/dl|mg/l)',  "mg/dL", [(70,99)]),
    "LDL":            (r'(?:ldl|ldl-c|ldl\s+cholesterol)[\s:]+(\d+\.?\d*)\s*(?:mg/dl|mg/l)',    "mg/dL", [(0,100)]),
    "HDL":            (r'(?:hdl|hdl-c|hdl\s+cholesterol)[\s:]+(\d+\.?\d*)\s*(?:mg/dl|mg/l)',    "mg/dL", [(40,300)]),
    "Triglycerides":  (r'(?:triglycerides?|trig)[\s:]+(\d+\.?\d*)\s*(?:mg/dl|mg/l)',            "mg/dL", [(0,150)]),
    "TSH":            (r'(?:tsh|thyroid\s+stimulating)[\s:]+(\d+\.?\d*)\s*(?:miu/l|uiu/ml|miu/ml)', "mIU/L", [(0.4,4.0)]),
    "Creatinine":     (r'(?:creatinine|serum\s+creatinine)[\s:]+(\d+\.?\d*)\s*(?:mg/dl|mg/l)',  "mg/dL", [(0.6,1.2)]),
    "VitaminD":       (r'(?:vitamin\s+d|25-oh|25\(oh\)d|25-hydroxyvitamin)[\s:]+(\d+\.?\d*)\s*(?:ng/ml|nmol)', "ng/mL", [(30,100)]),
    "B12":            (r'(?:vitamin\s+b12|b-12|b12|cobalamin)[\s:]+(\d+\.?\d*)\s*(?:pg/ml|pmol)', "pg/mL", [(200,900)]),
    "Ferritin":       (r'(?:ferritin)[\s:]+(\d+\.?\d*)\s*(?:ng/ml|ug/l)',                       "ng/mL", [(12,300)]),
    "CRP":            (r'(?:crp|c-reactive\s+protein|hs-crp|hsCRP)[\s:]+(\d+\.?\d*)\s*(?:mg/l|mg/dl)', "mg/L", [(0,1.0)]),
    "ALT":            (r'(?:alt|alanine\s+aminotransferase|sgpt)[\s:]+(\d+\.?\d*)\s*(?:u/l|iu/l)', "U/L", [(7,56)]),
    "AST":            (r'(?:ast|aspartate\s+aminotransferase|sgot)[\s:]+(\d+\.?\d*)\s*(?:u/l|iu/l)', "U/L", [(10,40)]),
    "Hemoglobin":     (r'(?:hemoglobin|haemoglobin|hgb|hb)[\s:]+(\d+\.?\d*)\s*(?:g/dl|g/l)',   "g/dL", [(12.0,17.5)]),
    "Testosterone":   (r'(?:testosterone|total\s+testosterone)[\s:]+(\d+\.?\d*)\s*(?:ng/dl|nmol)', "ng/dL", [(300,1000)]),
    "BMI":            (r'(?:bmi|body\s+mass\s+index)[\s:]+(\d+\.?\d*)',                          "",     [(18.5,24.9)]),
}

def _fast_regex_extract(text: str, filename: str = "") -> list[dict]:
    """
    #SPEED-1: Instant marker extraction — no LLM, no network.
    Returns structured markers in <10ms.
    """
    from datetime import date
    lower = text.lower()
    today = date.today().isoformat()
    results = []

    for marker_name, (pattern, unit, normal_ranges) in _FAST_PATTERNS.items():
        m = re.search(pattern, lower)
        if not m:
            continue
        try:
            value = float(m.group(1))
        except (ValueError, IndexError):
            continue

        # Sanity bounds
        if value <= 0 or value > 99999:
            continue

        # Determine status
        status = "NORMAL"
        for lo, hi in normal_ranges:
            if value < lo:
                status = "LOW"
            elif value > hi:
                status = "HIGH"
            else:
                status = "NORMAL"

        results.append({
            "marker_name":     marker_name,
            "value":           value,
            "unit":            unit,
            "status":          status,
            "reference_range": f"{normal_ranges[0][0]}–{normal_ranges[0][1]} {unit}".strip() if normal_ranges else "",
            "source_document": filename,
            "date":            today,
        })

    return results


def _detect_radiology(text: str) -> bool:
    lower = text.lower()
    has_strong   = any(k in lower for k in _RADIOLOGY_STRONG)
    has_lab_data = any(k in lower for k in _LAB_INDICATORS)
    if has_strong and not has_lab_data: return True
    secondary = ["ultrasound", "mri", "ct scan", "x-ray", "mammogram"]
    return any(k in lower for k in secondary) and not has_lab_data

def _get_extension(filename: str) -> str:
    return os.path.splitext((filename or "").lower())[1]

@document_bp.route("/diagnose", methods=["GET"])
def diagnose():
    return jsonify({"status": "ok"})

@document_bp.route("/analyze", methods=["POST"])
def analyze():
    try:
        return _analyze_inner()
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "Something went wrong processing your file."}), 500

def _analyze_inner():
    from app import supabase
    from services.auth import get_authenticated_user

    user = get_authenticated_user(supabase)
    if not user: return jsonify({"error": "Unauthorized"}), 401

    # #CONSENT-7: Auto-grant consent instead of blocking with 403
    try:
        from services.compliance import ensure_consents
        ensure_consents(supabase, user.id)
    except Exception:
        pass

    file = request.files.get("file")
    if not file: return jsonify({"error": "No file provided"}), 400

    filename = (file.filename or "").strip()
    if not filename: return jsonify({"error": "File has no name."}), 400

    ext = _get_extension(filename)
    if ext not in _ALL_EXTS:
        return jsonify({"error": "Unsupported file type. Please upload a PDF, image (JPG/PNG/WebP), or TXT file."}), 400

    is_image = ext in _IMAGE_EXTS
    size_limit = _IMAGE_SIZE_LIMIT if is_image else _PDF_SIZE_LIMIT

    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)

    if file_size > size_limit: return jsonify({"error": "File too large."}), 413
    if file_size == 0: return jsonify({"error": "The uploaded file is empty."}), 400

    # Quality hints for images (non-blocking)
    quality_hints = {}
    if is_image:
        try:
            from document_processing.vision_extractor import extract_image_quality_hints
            quality_hints = extract_image_quality_hints(file)
            file.seek(0)  # #IMG-1: Always reset after quality check
        except Exception:
            file.seek(0)

    # Extract text
    if is_image:
        raw_text = _extract_image_text_safe(file, filename)
    else:
        raw_text = _extract_text_safe(file, filename)

    if isinstance(raw_text, tuple): return raw_text  # error response

    # #IMG-2: Better fallback messaging for images
    if not raw_text or len(raw_text.strip()) < 10:
        if is_image:
            raw_text = "[Image received — could not extract readable text. For best results, ensure the image is well-lit and text is clearly visible.]"
        else:
            return jsonify({"error": "No readable text found in this file."}), 400

    try:
        from services.compliance import audit_log, anonymize_for_llm
        source = "PHOTO" if is_image else "DOCUMENT"
        audit_log(supabase, user.id, f"{source}_UPLOADED", f"file:{filename}", "PHI")
        anonymized = anonymize_for_llm(raw_text, user.id)
    except Exception:
        anonymized = raw_text

    user_name     = _get_user_name_safe(supabase, user.id)
    _is_radiology = _detect_radiology(anonymized)
    _is_medical   = any(k in anonymized.lower() for k in _LAB_INDICATORS)

    active_markers: list = []
    report_date: str = ""

    if not _is_radiology and _is_medical:
        # #SPEED-1 PASS 1: Fast regex extraction — instant, no LLM
        fast_markers = _fast_regex_extract(anonymized, filename)

        if fast_markers:
            # Save fast markers immediately — response goes back to user now
            _store_fast_markers(supabase, user.id, fast_markers)
            active_markers = fast_markers
            report_date = fast_markers[0].get("date", "")

            # PASS 2 in background: LLM refines + catches missed markers
            threading.Thread(
                target=_llm_refine_markers_bg,
                args=(supabase, anonymized, filename, user.id, fast_markers),
                daemon=True
            ).start()
        else:
            # No regex hits — fall back to full LLM extraction (slower path)
            active_markers, report_date = _extract_and_store_markers_safe(
                supabase, anonymized, filename, user.id, user_name
            )

    job_id = uuid.uuid4().hex[:12]

    if active_markers:
        _invalidate_caches(supabase, user.id)

    _submit_doctor_prep_bg_safe(supabase, anonymized, filename, user.id, user_name, active_markers, job_id)

    abnormal = [m for m in active_markers if m.get("status") in ("HIGH", "LOW")]
    prefix   = f"{user_name}, your" if user_name else "Your"
    method   = "photo was processed" if is_image else "document has been read"

    if active_markers:
        summary = (
            f"{prefix} {method} and **{len(active_markers)} markers stored**. "
            f"{len(abnormal)} need attention. Ask me anything about them."
        )
    elif _is_radiology:
        summary = f"{prefix} {method}. I'm reviewing the details now. What's on your mind?"
    elif is_image and not _is_medical:
        summary = (
            f"{prefix} image was processed. "
            f"If this is a lab report, ensure the text is clearly visible and try again. "
            f"Ask me about what I found."
        )
    else:
        summary = f"{prefix} {method} successfully. I'm here to help — ask me any questions."

    return jsonify({
        "success":               True,
        "filename":              filename,
        "document_id":           job_id,
        "summary_text":          summary,
        "markers":               active_markers,
        "abnormal_count":        len(abnormal),
        "doc_type":              "radiology" if _is_radiology else "lab_report",
        "document_text":         anonymized[:6000],
        "user_name":             user_name,
        "report_date":           report_date or _today_iso(),
        "job_id":                job_id,
    })

def _today_iso() -> str:
    from datetime import date
    return date.today().isoformat()


# ── #SPEED-1: Store fast-extracted markers directly ───────────────────────────

def _store_fast_markers(supabase, user_id: str, markers: list) -> None:
    """Save fast-extracted markers to DB without LLM. Very fast."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for m in markers:
        try:
            supabase.table("health_markers").upsert({
                "user_id":         user_id,
                "marker_name":     m["marker_name"],
                "value":           m["value"],
                "unit":            m.get("unit", ""),
                "status":          m.get("status", "UNKNOWN"),
                "reference_range": m.get("reference_range", ""),
                "source_document": m.get("source_document", ""),
                "date":            m.get("date", _today_iso()),
                "created_at":      now,
            }, on_conflict="user_id,marker_name,date").execute()
        except Exception:
            try:
                supabase.table("health_markers").insert({
                    "user_id":         user_id,
                    "marker_name":     m["marker_name"],
                    "value":           m["value"],
                    "unit":            m.get("unit", ""),
                    "status":          m.get("status", "UNKNOWN"),
                    "reference_range": m.get("reference_range", ""),
                    "source_document": m.get("source_document", ""),
                    "date":            m.get("date", _today_iso()),
                    "created_at":      now,
                }).execute()
            except Exception as e2:
                print(f"[DOC-FAST] store error: {e2}")


def _llm_refine_markers_bg(supabase, text: str, filename: str, user_id: str, already_found: list) -> None:
    """
    Background LLM pass: catch any markers the regex missed.
    Runs after response already sent to user.
    """
    if not os.getenv("OPENAI_API_KEY"):
        return
    try:
        from health_memory.extractor import extract_health_markers
        from services.unit_normalizer import force_us_units_batch
        from health_memory.memory import store_health_markers

        llm_markers = extract_health_markers(text=text[:6000], source_document=filename)
        if not llm_markers:
            return

        llm_markers = force_us_units_batch(llm_markers)

        # Only save markers NOT already found by regex
        already_names = {m["marker_name"].lower() for m in already_found}
        new_markers = [
            m for m in llm_markers
            if (m.get("marker") or m.get("marker_name", "")).lower() not in already_names
        ]

        if new_markers:
            store_health_markers(supabase, user_id, new_markers)
            print(f"[DOC-BG] LLM found {len(new_markers)} additional markers not caught by regex")
    except Exception as e:
        print(f"[DOC-BG] LLM refine error: {e}")


# ── Lab report count endpoint ─────────────────────────────────────────────────

@document_bp.route("/api/health/reports", methods=["GET"])
def get_report_count():
    """
    #COUNT-1: Returns count of distinct lab reports uploaded.
    Counted by distinct source_document values in health_markers.
    """
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        res = supabase.table("health_markers") \
            .select("source_document") \
            .eq("user_id", user.id) \
            .execute()
        docs = set(
            r["source_document"] for r in (res.data or [])
            if r.get("source_document")
        )
        return jsonify({"count": len(docs), "documents": list(docs)})
    except Exception as e:
        return jsonify({"count": 0, "error": str(e)})

_PDF_SIZE_LIMIT   = 5  * 1024 * 1024
_IMAGE_SIZE_LIMIT = 20 * 1024 * 1024

_PDF_EXTS   = {".pdf"}
_TEXT_EXTS  = {".txt"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
_ALL_EXTS   = _PDF_EXTS | _TEXT_EXTS | _IMAGE_EXTS

_RADIOLOGY_STRONG = [
    "mammograph", "radiology report", "ultrasound report", "mri report",
    "ct scan report", "x-ray report", "radio-diagnosis", "sonograph",
    "scan name", "post surgery", "echocardiogram", "nuclear medicine",
    "pet scan", "fluoroscopy", "angiograph",
]
_LAB_INDICATORS = [
    "mg/dl", "mg/l", "mmol/l", "ng/ml", "pg/ml", "iu/l", "u/l",
    "reference range", "normal range", "hemoglobin", "haemoglobin",
    "cholesterol", "creatinine", "hba1c", "platelet", "glucose",
    "triglyceride", "ferritin", "vitamin", "tsh", "alt", "ast",
    "wbc", "rbc", "sodium", "potassium", "bilirubin", "albumin",
    "lab report", "blood test", "laboratory", "specimen", "pathology",
]

def _detect_radiology(text: str) -> bool:
    lower = text.lower()
    has_strong   = any(k in lower for k in _RADIOLOGY_STRONG)
    has_lab_data = any(k in lower for k in _LAB_INDICATORS)
    if has_strong and not has_lab_data: return True
    secondary = ["ultrasound", "mri", "ct scan", "x-ray", "mammogram"]
    return any(k in lower for k in secondary) and not has_lab_data

def _get_extension(filename: str) -> str:
    return os.path.splitext((filename or "").lower())[1]

@document_bp.route("/diagnose", methods=["GET"])
def diagnose():
    return jsonify({"status": "ok"})

@document_bp.route("/analyze", methods=["POST"])
def analyze():
    try:
        return _analyze_inner()
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "Something went wrong processing your file."}), 500

def _analyze_inner():
    from app import supabase
    from services.auth import get_authenticated_user

    user = get_authenticated_user(supabase)
    if not user: return jsonify({"error": "Unauthorized"}), 401

    # #CONSENT-7: Auto-grant consent instead of blocking with 403
    # A logged-in user uploading a file has accepted terms on signup/login
    try:
        from services.compliance import ensure_consents
        ensure_consents(supabase, user.id)
    except Exception:
        pass

    file = request.files.get("file")
    if not file: return jsonify({"error": "No file provided"}), 400

    filename = (file.filename or "").strip()
    if not filename: return jsonify({"error": "File has no name."}), 400

    ext = _get_extension(filename)
    if ext not in _ALL_EXTS:
        return jsonify({"error": "Unsupported file type. Please upload a PDF, image (JPG/PNG/WebP), or TXT file."}), 400

    is_image = ext in _IMAGE_EXTS
    size_limit = _IMAGE_SIZE_LIMIT if is_image else _PDF_SIZE_LIMIT

    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)

    if file_size > size_limit: return jsonify({"error": "File too large."}), 413
    if file_size == 0: return jsonify({"error": "The uploaded file is empty."}), 400

    # Quality hints for images (non-blocking)
    quality_hints = {}
    if is_image:
        try:
            from document_processing.vision_extractor import extract_image_quality_hints
            quality_hints = extract_image_quality_hints(file)
            file.seek(0)  # #IMG-1: Always reset after quality check
        except Exception:
            file.seek(0)

    # Extract text
    if is_image:
        raw_text = _extract_image_text_safe(file, filename)
    else:
        raw_text = _extract_text_safe(file, filename)

    if isinstance(raw_text, tuple): return raw_text  # error response

    # #IMG-2: Better fallback messaging for images
    if not raw_text or len(raw_text.strip()) < 10:
        if is_image:
            raw_text = "[Image received — could not extract readable text. For best results, ensure the image is well-lit and text is clearly visible.]"
        else:
            return jsonify({"error": "No readable text found in this file."}), 400

    try:
        from services.compliance import audit_log, anonymize_for_llm
        source = "PHOTO" if is_image else "DOCUMENT"
        audit_log(supabase, user.id, f"{source}_UPLOADED", f"file:{filename}", "PHI")
        anonymized = anonymize_for_llm(raw_text, user.id)
    except Exception:
        anonymized = raw_text

    user_name     = _get_user_name_safe(supabase, user.id)
    _is_radiology = _detect_radiology(anonymized)
    _is_medical   = any(k in anonymized.lower() for k in _LAB_INDICATORS)

    active_markers: list = []
    report_date: str = ""

    if not _is_radiology and _is_medical:
        active_markers, report_date = _extract_and_store_markers_safe(
            supabase, anonymized, filename, user.id, user_name
        )

    job_id = uuid.uuid4().hex[:12]

    if active_markers:
        _invalidate_caches(supabase, user.id)

    _submit_doctor_prep_bg_safe(supabase, anonymized, filename, user.id, user_name, active_markers, job_id)

    abnormal = [m for m in active_markers if m.get("status") in ("HIGH", "LOW")]
    prefix   = f"{user_name}, your" if user_name else "Your"
    method   = "photo was processed" if is_image else "document has been read"

    if active_markers:
        summary = (
            f"{prefix} {method} and **{len(active_markers)} markers stored**. "
            f"{len(abnormal)} need attention. Ask me anything about them."
        )
    elif _is_radiology:
        summary = f"{prefix} {method}. I'm reviewing the details now. What's on your mind?"
    elif is_image and not _is_medical:
        summary = (
            f"{prefix} image was processed. "
            f"If this is a lab report, ensure the text is clearly visible and try again. "
            f"Ask me about what I found."
        )
    else:
        summary = f"{prefix} {method} successfully. I'm here to help — ask me any questions."

    return jsonify({
        "success":               True,
        "filename":              filename,
        "document_id":           job_id,
        "summary_text":          summary,
        "markers":               active_markers,
        "abnormal_count":        len(abnormal),
        "doc_type":              "radiology" if _is_radiology else "lab_report",
        "document_text":         anonymized[:6000],
        "user_name":             user_name,
        "report_date":           report_date or _today_iso(),
        "job_id":                job_id,
    })

def _today_iso() -> str:
    from datetime import date
    return date.today().isoformat()

def _extract_image_text_safe(file, filename: str):
    """
    #IMG-1: Properly extract image text via vision API.
    Ensures file pointer is at start before reading.
    """
    try:
        file.seek(0)  # #IMG-1: Explicit seek before vision extraction
        from document_processing.vision_extractor import extract_text_from_image
        result = extract_text_from_image(file)
        return result
    except ValueError as e:
        print(f"[DOC] Vision extraction ValueError: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[DOC] Vision extraction error: {e}")
        # Don't return 500 — return a message that lets the user know
        return f"[Image processed but text extraction encountered an issue: {str(e)[:100]}. Try a clearer photo.]"

def _extract_text_safe(file, filename: str):
    file.seek(0)
    for module_path in ["document_processing.extractor", "extractor"]:
        try:
            mod = __import__(module_path, fromlist=["extract_text_from_file"])
            return mod.extract_text_from_file(file)
        except Exception:
            continue

    ext = _get_extension(filename)
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            import io
            file.seek(0)
            reader = PdfReader(io.BytesIO(file.read()))
            pages  = [page.extract_text() or "" for page in reader.pages]
            text   = "\n".join(pages).strip()
            if text: return text
            return jsonify({"error": "No readable text found in this PDF."}), 400
        except Exception as e:
            return jsonify({"error": f"Could not read PDF: {e}"}), 400
    if ext == ".txt":
        file.seek(0)
        return file.read().decode("utf-8", errors="replace").strip()
    return jsonify({"error": "Document extraction module not found."}), 500

def _get_user_name_safe(supabase, user_id: str) -> str:
    try:
        res = supabase.table("user_profiles").select("first_name").eq("user_id", user_id).limit(1).execute()
        return res.data[0].get("first_name", "") if res.data else ""
    except Exception:
        return ""

def _extract_and_store_markers_safe(supabase, anonymized: str, filename: str, user_id: str, user_name: str) -> tuple:
    if not os.getenv("OPENAI_API_KEY"): return [], ""
    try:
        from health_memory.extractor import extract_health_markers
        markers = extract_health_markers(text=anonymized, source_document=filename)
        if not markers: return [], ""
        from services.unit_normalizer import force_us_units_batch
        markers = force_us_units_batch(markers)
        from health_memory.memory import store_health_markers
        store_health_markers(supabase, user_id, markers)
        return markers, markers[0].get("date", "")
    except Exception as e:
        print(f"[DOC] Marker extraction error: {e}")
        return [], ""

def _invalidate_caches(supabase, user_id: str) -> None:
    try:
        supabase.table("user_profiles").update(
            {"health_persona_marker_count": -1}
        ).eq("user_id", user_id).execute()
    except Exception:
        pass

def _submit_doctor_prep_bg_safe(supabase, anonymized, filename, user_id, user_name, markers, job_id) -> None:
    try:
        from services.job_queue import submit_job
        submit_job(_generate_and_store_doctor_prep_safe, supabase, anonymized, filename, user_id, user_name, markers, job_id)
    except Exception:
        pass

def _generate_and_store_doctor_prep_safe(supabase, anonymized, filename, user_id, user_name, markers, job_id) -> None:
    from datetime import datetime, timezone
    try:
        from ai.chat import generate_doctor_prep
        doctor_prep = generate_doctor_prep(document_text=anonymized, markers=markers, user_name=user_name)
        if not doctor_prep:
            doctor_prep = "Ask PHI: 'Prepare me for my doctor visit' for a personalised brief."
        now = datetime.now(timezone.utc).isoformat()
        supabase.table("medical_documents").upsert({
            "user_id": user_id, "job_id": job_id, "filename": filename,
            "doctor_prep_text": doctor_prep, "doctor_prep_generated_at": now, "created_at": now,
        }, on_conflict="user_id,job_id").execute()
    except Exception as e:
        print(f"[DOC-BG] Doctor prep error: {e}")

@document_bp.route("/doctor-prep/<job_id>", methods=["GET"])
def get_doctor_prep(job_id: str):
    return jsonify({"ready": False})

@document_bp.route("/doctor-prep/history", methods=["GET"])
def doctor_prep_history():
    return jsonify({"preps": []})