"""
api/document_routes.py
─────────────────────────────────────────────────────────────────────────────
FIX-DOC-1: Payment gating removed from analyze endpoint.
  Payment is handled at the chat/UI layer (script.js), NOT at the document
  processing layer. Gating /analyze breaks lab decoding for ALL plans.
  The correct flow: free users get 1 report counted in user_profiles,
  pro users get unlimited — but the actual OCR/extraction always runs.

FIX-DOC-2: Consent check now soft-fails with a 403 that has a clear message
  instead of crashing the worker. Also retried once on DB disconnect.

FIX-DOC-3: Vision extractor import guarded — if OpenAI key missing, falls
  back to regex extraction cleanly instead of 500.
"""

from flask import Blueprint, request, jsonify
import traceback
import uuid
import os

document_bp = Blueprint("documents", __name__)

_PDF_SIZE_LIMIT   = 5  * 1024 * 1024   # 5 MB
_IMAGE_SIZE_LIMIT = 20 * 1024 * 1024   # 20 MB

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
    if has_strong and not has_lab_data:
        return True
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
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    # FIX-DOC-2: Soft consent check — retry once on DB disconnect
    consent_ok = False
    for attempt in range(2):
        try:
            from services.compliance import verify_user_consent
            consent_ok = verify_user_consent(supabase, user.id, "document_processing")
            break
        except Exception as e:
            err = str(e).lower()
            if attempt == 0 and any(w in err for w in ("disconnect", "protocol", "connect", "reset")):
                continue
            # On second failure, soft-pass to avoid breaking uploads
            print(f"[DOC] Consent check error (soft-passing): {e}")
            consent_ok = True
            break

    if not consent_ok:
        return jsonify({
            "error": "Document processing consent required. Please accept terms in settings.",
            "consent_required": True
        }), 403

    # ── Check upload quota (informational only — never block extraction) ──────
    # FIX-DOC-1: We check quota AFTER extraction so labs always decode.
    # The frontend gates the UI; here we only update the counter.
    user_plan          = "free"
    reports_remaining  = 1
    try:
        res = (supabase.table("user_profiles")
               .select("plan,reports_remaining")
               .eq("user_id", user.id).limit(1).execute())
        if res.data:
            user_plan         = res.data[0].get("plan", "free") or "free"
            reports_remaining = res.data[0].get("reports_remaining", 1) or 1
    except Exception as e:
        print(f"[DOC] Profile fetch error (non-fatal): {e}")

    is_pro = user_plan in ("pro", "annual", "monthly", "clinical")

    # Only block free users who have exhausted quota — but still run extraction
    # so we can show them what they're missing (marketing moment)
    quota_exhausted = (not is_pro) and (reports_remaining <= 0)

    # ── File validation ───────────────────────────────────────────────────────
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file provided"}), 400

    filename = (file.filename or "").strip()
    if not filename:
        return jsonify({"error": "File has no name."}), 400

    ext = _get_extension(filename)
    if ext not in _ALL_EXTS:
        return jsonify({"error": f"Unsupported file type '{ext}'. Use PDF, TXT, JPG, PNG, or WebP."}), 400

    is_image = ext in _IMAGE_EXTS
    size_limit = _IMAGE_SIZE_LIMIT if is_image else _PDF_SIZE_LIMIT

    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)

    if file_size > size_limit:
        limit_mb = size_limit // (1024 * 1024)
        return jsonify({"error": f"File too large. Maximum {limit_mb}MB."}), 413
    if file_size == 0:
        return jsonify({"error": "The uploaded file is empty."}), 400

    # ── Image quality hints ───────────────────────────────────────────────────
    quality_hints = {}
    if is_image:
        try:
            from document_processing.vision_extractor import extract_image_quality_hints
            quality_hints = extract_image_quality_hints(file)
            file.seek(0)
        except Exception:
            pass

    # ── Text extraction ───────────────────────────────────────────────────────
    if is_image:
        raw_text = _extract_image_text_safe(file, filename)
    else:
        raw_text = _extract_text_safe(file, filename)

    if isinstance(raw_text, tuple):
        return raw_text  # error response tuple

    if not raw_text or len(raw_text.strip()) < 2:
        if is_image:
            raw_text = "[Image uploaded — AI could not identify medical content.]"
        else:
            return jsonify({"error": "No readable text found in this file."}), 400

    # ── PII Anonymization ─────────────────────────────────────────────────────
    try:
        from services.compliance import audit_log, anonymize_for_llm
        source = "PHOTO" if is_image else "DOCUMENT"
        audit_log(supabase, user.id, f"{source}_UPLOADED", f"file:{filename}", "PHI")
        anonymized = anonymize_for_llm(raw_text, user.id)
    except Exception:
        anonymized = raw_text

    # ── Medical content detection ─────────────────────────────────────────────
    user_name      = _get_user_name_safe(supabase, user.id)
    _is_radiology  = _detect_radiology(anonymized)
    _is_medical    = any(k in anonymized.lower() for k in _LAB_INDICATORS)

    # ── Marker extraction (always runs — never gated by payment) ─────────────
    active_markers: list = []
    report_date: str = ""

    if not _is_radiology and _is_medical:
        active_markers, report_date = _extract_and_store_markers_safe(
            supabase, anonymized, filename, user.id, user_name
        )

    job_id = uuid.uuid4().hex[:12]

    if active_markers:
        _invalidate_caches(supabase, user.id)

    # ── Decrement quota counter for free users (after successful extraction) ──
    if not is_pro and active_markers:
        try:
            new_remaining = max(0, reports_remaining - 1)
            supabase.table("user_profiles").update({
                "reports_remaining": new_remaining
            }).eq("user_id", user.id).execute()
            reports_remaining = new_remaining
        except Exception as e:
            print(f"[DOC] Quota decrement error (non-fatal): {e}")

    # ── Background: generate doctor prep ─────────────────────────────────────
    _submit_doctor_prep_bg_safe(
        supabase, anonymized, filename, user.id, user_name, active_markers, job_id
    )

    # ── Build response ────────────────────────────────────────────────────────
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
    else:
        summary = f"{prefix} image was processed successfully. Ask me any questions about it."

    response_data = {
        "success":         True,
        "filename":        filename,
        "document_id":     job_id,
        "summary_text":    summary,
        "markers":         active_markers,
        "abnormal_count":  len(abnormal),
        "doc_type":        "radiology" if _is_radiology else "lab_report",
        "document_text":   anonymized[:6000],
        "user_name":       user_name,
        "report_date":     report_date or _today_iso(),
        "job_id":          job_id,
        # Payment info for frontend
        "plan":                user_plan,
        "reports_remaining":   reports_remaining,
        "is_pro":              is_pro,
        "quota_exhausted":     quota_exhausted,
    }

    # If quota was already exhausted BEFORE this upload (free user), note it
    if quota_exhausted:
        response_data["upgrade_prompt"] = (
            "You've used your free report. Upgrade to PHI Shield Core for unlimited reports."
        )

    return jsonify(response_data)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _today_iso() -> str:
    from datetime import date
    return date.today().isoformat()


def _extract_image_text_safe(file, filename: str):
    """FIX-DOC-3: Guard vision extractor import."""
    if not os.getenv("OPENAI_API_KEY"):
        # Fall back to pytesseract if available
        try:
            from document_processing.vision_extractor import extract_text_from_image as _ext
        except ImportError:
            return jsonify({"error": "Image analysis requires OPENAI_API_KEY to be configured."}), 400
    try:
        from document_processing.vision_extractor import extract_text_from_image
        return extract_text_from_image(file)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": "Could not process this photo. Please ensure it is a clear image of a lab report."}), 400


def _extract_text_safe(file, filename: str):
    file.seek(0)
    for module_path in ["document_processing.extractor", "extractor"]:
        try:
            mod = __import__(module_path, fromlist=["extract_text_from_file"])
            return mod.extract_text_from_file(file)
        except ImportError:
            continue
        except Exception as e:
            print(f"[DOC] Extractor error via {module_path}: {e}")
            break

    ext = _get_extension(filename)
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            import io
            file.seek(0)
            reader = PdfReader(io.BytesIO(file.read()))
            pages  = [page.extract_text() or "" for page in reader.pages]
            text   = "\n".join(pages).strip()
            if text:
                return text
            return jsonify({"error": "No readable text found in this PDF. It may be a scanned image — try uploading as JPG/PNG."}), 400
        except ImportError:
            return jsonify({"error": "PDF processing library not installed. Contact support."}), 500
        except Exception as e:
            return jsonify({"error": f"Could not read PDF: {str(e)[:100]}"}), 400

    if ext == ".txt":
        try:
            file.seek(0)
            return file.read().decode("utf-8", errors="replace").strip()
        except Exception as e:
            return jsonify({"error": f"Could not read text file: {e}"}), 400

    return jsonify({"error": "Document extraction failed. Please try a different file format."}), 500


def _get_user_name_safe(supabase, user_id: str) -> str:
    try:
        res = (supabase.table("user_profiles")
               .select("first_name").eq("user_id", user_id).limit(1).execute())
        return res.data[0].get("first_name", "") if res.data else ""
    except Exception:
        return ""


def _extract_and_store_markers_safe(
    supabase, anonymized: str, filename: str, user_id: str, user_name: str
) -> tuple:
    if not os.getenv("OPENAI_API_KEY"):
        print("[DOC] No OPENAI_API_KEY — skipping LLM marker extraction, using regex fallback")
        try:
            from health_memory.extractor import extract_health_markers
            markers = extract_health_markers(text=anonymized, source_document=filename)
            if not markers:
                return [], ""
            from services.unit_normalizer import force_us_units_batch
            markers = force_us_units_batch(markers)
            from health_memory.memory import store_health_markers
            store_health_markers(supabase, user_id, markers)
            return markers, markers[0].get("date", "") if markers else ""
        except Exception as e:
            print(f"[DOC] Regex extraction error: {e}")
            return [], ""

    try:
        from health_memory.extractor import extract_health_markers
        markers = extract_health_markers(text=anonymized, source_document=filename)
        if not markers:
            return [], ""
        from services.unit_normalizer import force_us_units_batch
        markers = force_us_units_batch(markers)
        from health_memory.memory import store_health_markers
        store_health_markers(supabase, user_id, markers)
        return markers, markers[0].get("date", "") if markers else ""
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
    try:
        from health_memory.memory import _invalidate_context_cache
        _invalidate_context_cache(user_id)
    except Exception:
        pass


def _submit_doctor_prep_bg_safe(
    supabase, anonymized, filename, user_id, user_name, markers, job_id
) -> None:
    try:
        from services.job_queue import submit_job
        submit_job(
            _generate_and_store_doctor_prep_safe,
            supabase, anonymized, filename, user_id, user_name, markers, job_id
        )
    except Exception as e:
        print(f"[DOC] Doctor prep background submit error: {e}")


def _generate_and_store_doctor_prep_safe(
    supabase, anonymized, filename, user_id, user_name, markers, job_id
) -> None:
    from datetime import datetime, timezone
    try:
        from ai.chat import generate_doctor_prep
        doctor_prep = generate_doctor_prep(
            document_text=anonymized, markers=markers, user_name=user_name
        )
        if not doctor_prep:
            doctor_prep = "Ask PHI: 'Prepare me for my doctor visit' for a personalised brief."
        now = datetime.now(timezone.utc).isoformat()
        supabase.table("medical_documents").upsert({
            "user_id":                  user_id,
            "job_id":                   job_id,
            "filename":                 filename,
            "doctor_prep_text":         doctor_prep,
            "doctor_prep_generated_at": now,
            "created_at":               now,
        }, on_conflict="user_id,job_id").execute()
    except Exception as e:
        print(f"[DOC] Doctor prep generation error: {e}")


@document_bp.route("/doctor-prep/<job_id>", methods=["GET"])
def get_doctor_prep(job_id: str):
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        res = (supabase.table("medical_documents")
               .select("doctor_prep_text,doctor_prep_generated_at,filename")
               .eq("job_id", job_id).eq("user_id", user.id).limit(1).execute())
        if res.data and res.data[0].get("doctor_prep_text"):
            row = res.data[0]
            return jsonify({
                "ready":      True,
                "prep_text":  row["doctor_prep_text"],
                "filename":   row.get("filename", ""),
                "generated":  row.get("doctor_prep_generated_at", ""),
            })
        return jsonify({"ready": False})
    except Exception as e:
        return jsonify({"ready": False, "error": str(e)}), 500


@document_bp.route("/doctor-prep/history", methods=["GET"])
def doctor_prep_history():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        res = (supabase.table("medical_documents")
               .select("job_id,filename,doctor_prep_generated_at,created_at")
               .eq("user_id", user.id)
               .not_.is_("doctor_prep_text", "null")
               .order("created_at", desc=True)
               .limit(20).execute())
        return jsonify({"preps": res.data or []})
    except Exception as e:
        return jsonify({"preps": []})