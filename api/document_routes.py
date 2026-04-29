from flask import Blueprint, request, jsonify
import traceback
import uuid
import os

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

    try:
        from services.compliance import verify_user_consent
        if not verify_user_consent(supabase, user.id, "document_processing"):
            return jsonify({"error": "Document processing consent required"}), 403
    except Exception: pass

    file = request.files.get("file")
    if not file: return jsonify({"error": "No file provided"}), 400

    filename = (file.filename or "").strip()
    if not filename: return jsonify({"error": "File has no name."}), 400

    ext = _get_extension(filename)
    if ext not in _ALL_EXTS:
        return jsonify({"error": "Unsupported file type."}), 400

    is_image = ext in _IMAGE_EXTS
    size_limit = _IMAGE_SIZE_LIMIT if is_image else _PDF_SIZE_LIMIT

    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)

    if file_size > size_limit: return jsonify({"error": "File too large."}), 413
    if file_size == 0: return jsonify({"error": "The uploaded file is empty."}), 400

    quality_hints = {}
    if is_image:
        try:
            from document_processing.vision_extractor import extract_image_quality_hints
            quality_hints = extract_image_quality_hints(file)
            file.seek(0)
        except Exception: pass

    if is_image: raw_text = _extract_image_text_safe(file, filename)
    else: raw_text = _extract_text_safe(file, filename)

    if isinstance(raw_text, tuple): return raw_text

    # 3. SMART FIX: Accept short descriptions for general photos
    if not raw_text or len(raw_text.strip()) < 2:
        if is_image: raw_text = "[Image uploaded but the AI could not identify any context.]"
        else: return jsonify({"error": "No readable text found."}), 400

    try:
        from services.compliance import audit_log, anonymize_for_llm
        source = "PHOTO" if is_image else "DOCUMENT"
        audit_log(supabase, user.id, f"{source}_UPLOADED", f"file:{filename}", "PHI")
        anonymized = anonymize_for_llm(raw_text, user.id)
    except Exception: anonymized = raw_text

    user_name     = _get_user_name_safe(supabase, user.id)
    _is_radiology = _detect_radiology(anonymized)
    
    # 4. COST-SAVING GATEKEEPER: Does this text actually have medical data?
    _is_medical = any(k in anonymized.lower() for k in _LAB_INDICATORS)

    active_markers: list = []
    report_date: str = ""

    # 5. COST-SAVING GATEKEEPER: Only extract if it looks medical!
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

    # 6. EMPATHY FIX: Supportive conversational summaries based on the research
    if active_markers:
        summary = (f"{prefix} {method} and **{len(active_markers)} markers stored**. "
                   f"{len(abnormal)} need attention. I'm here to help you understand them—ask me anything.")
    elif _is_radiology:
        summary = f"{prefix} {method}. I'm reviewing the details now. What's on your mind?"
    else:
        summary = f"{prefix} image was processed successfully. I'm here to help—ask me any questions about it."

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
    try:
        from document_processing.vision_extractor import extract_text_from_image
        return extract_text_from_image(file)
    except ValueError as e: return jsonify({"error": str(e)}), 400
    except Exception as e: return jsonify({"error": f"Could not process this photo."}), 400

def _extract_text_safe(file, filename: str):
    file.seek(0)
    for module_path in ["document_processing.extractor", "extractor"]:
        try:
            mod = __import__(module_path, fromlist=["extract_text_from_file"])
            return mod.extract_text_from_file(file)
        except Exception: continue

    ext = _get_extension(filename)
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            import io
            reader = PdfReader(io.BytesIO(file.read()))
            pages  = [page.extract_text() or "" for page in reader.pages]
            text   = "\n".join(pages).strip()
            if text: return text
            return jsonify({"error": "No readable text found in this PDF."}), 400
        except Exception as e: return jsonify({"error": f"Could not read PDF: {e}"}), 400
    if ext == ".txt":
        return file.read().decode("utf-8", errors="replace").strip()
    return jsonify({"error": "Document extraction module not found."}), 500

def _get_user_name_safe(supabase, user_id: str) -> str:
    try:
        res = supabase.table("user_profiles").select("first_name").eq("user_id", user_id).limit(1).execute()
        return res.data[0].get("first_name", "") if res.data else ""
    except Exception: return ""

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
    except Exception: return [], ""

def _invalidate_caches(supabase, user_id: str) -> None:
    try: supabase.table("user_profiles").update({"health_persona_marker_count": -1}).eq("user_id", user_id).execute()
    except Exception: pass

def _submit_doctor_prep_bg_safe(supabase, anonymized, filename, user_id, user_name, markers, job_id) -> None:
    try:
        from services.job_queue import submit_job
        submit_job(_generate_and_store_doctor_prep_safe, supabase, anonymized, filename, user_id, user_name, markers, job_id)
    except Exception: pass

def _generate_and_store_doctor_prep_safe(supabase, anonymized, filename, user_id, user_name, markers, job_id) -> None:
    from datetime import datetime, timezone
    try:
        from ai.chat import generate_doctor_prep
        doctor_prep = generate_doctor_prep(document_text=anonymized, markers=markers, user_name=user_name)
        if not doctor_prep: doctor_prep = "Ask PHI: 'Prepare me for my doctor visit' for a personalised brief."
        now = datetime.now(timezone.utc).isoformat()
        supabase.table("medical_documents").upsert({
            "user_id": user_id, "job_id": job_id, "filename": filename,
            "doctor_prep_text": doctor_prep, "doctor_prep_generated_at": now, "created_at": now,
        }, on_conflict="user_id,job_id").execute()
    except Exception: pass

@document_bp.route("/doctor-prep/<job_id>", methods=["GET"])
def get_doctor_prep(job_id: str):
    return jsonify({"ready": False})

@document_bp.route("/doctor-prep/history", methods=["GET"])
def doctor_prep_history():
    return jsonify({"preps": []})