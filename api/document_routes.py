# api/document_routes.py — v2.0 Vision Edition
#
# NEW IN THIS VERSION:
#
# FIX-VISION-1: Photo lab report support (.jpg, .jpeg, .png, .webp, .heic)
#   Uses GPT-4o-mini Vision to extract text from photographed paper reports.
#   Falls back to pytesseract if OPENAI_API_KEY is unavailable.
#   Zero-Tax entry for mobile users who can't scan PDFs.
#
# FIX-MOBILE-2: File size limit raised to 20MB for high-res phone photos.
#   PDF limit remains 5MB. Image limit is 20MB (compressed before Vision API).
#
# PRESERVED FIXES FROM v1.0:
#   FIX-A: force_us_units_batch() on all markers before storage (US unit enforcement)
#   FIX-B: groq_client=None crash guard + OpenAI fallback
#   FIX-C: Unhandled exception → JSON 500 with CORS
#   FIX-D: pypdf ImportError → HTTP 400
#   FIX-E: Persona cache invalidation after marker storage

from flask import Blueprint, request, jsonify
import traceback
import uuid
import os

document_bp = Blueprint("documents", __name__)

# ── File size limits ──────────────────────────────────────────────────────────
_PDF_SIZE_LIMIT   = 5  * 1024 * 1024   # 5 MB — PDF text extraction
_IMAGE_SIZE_LIMIT = 20 * 1024 * 1024   # 20 MB — phone photos can be large

# ── Accepted file extensions ──────────────────────────────────────────────────
_PDF_EXTS   = {".pdf"}
_TEXT_EXTS  = {".txt"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
_ALL_EXTS   = _PDF_EXTS | _TEXT_EXTS | _IMAGE_EXTS

# ── Radiology detection (unchanged from v1.0) ─────────────────────────────────
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


# ── Diagnostic endpoint ───────────────────────────────────────────────────────

@document_bp.route("/diagnose", methods=["GET"])
def diagnose():
    """Health check for all optional dependencies including Vision."""
    results = {}

    def _try(label, fn):
        try:
            fn()
            results[label] = {"ok": True}
        except Exception as e:
            results[label] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    for mod in ["pypdf", "pdf2image", "pytesseract", "groq", "openai",
                "supabase", "PIL", "pillow_heif"]:
        _try(mod, lambda m=mod: __import__(m))

    for mod, sym in [
        ("document_processing.extractor",        "extract_text_from_file"),
        ("document_processing.vision_extractor",  "extract_text_from_image"),
        ("health_memory.extractor",               "extract_health_markers"),
        ("health_memory.memory",                  "store_health_markers"),
        ("ai.chat",                               "generate_doctor_prep"),
        ("ai.explainer",                          "explain_markers"),
        ("services.compliance",                   "audit_log"),
        ("services.unit_normalizer",              "force_us_units_batch"),
    ]:
        _try(mod, lambda m=mod, s=sym: getattr(__import__(m, fromlist=[s]), s))

    # Check vision capability
    results["vision_ai"] = {
        "ok":      bool(os.getenv("OPENAI_API_KEY")),
        "method":  "gpt-4o-mini vision" if os.getenv("OPENAI_API_KEY") else "pytesseract fallback",
    }

    all_ok = all(v["ok"] for v in results.values() if isinstance(v, dict) and "ok" in v)
    return jsonify({"status": "all_ok" if all_ok else "some_failed", "imports": results}), (200 if all_ok else 500)


# ── Main analyze route ────────────────────────────────────────────────────────

@document_bp.route("/analyze", methods=["POST"])
def analyze():
    """
    Process an uploaded medical document.

    Accepted formats:
      - PDF  (.pdf)  — digital or scanned (with OCR fallback)
      - Text (.txt)  — plain-text lab reports
      - Image (.jpg, .jpeg, .png, .webp, .heic) — phone photos of paper reports
    """
    try:
        return _analyze_inner()
    except Exception as e:
        print(f"[ANALYZE] Unhandled {type(e).__name__}: {e}")
        traceback.print_exc()
        return jsonify({
            "error":      "Something went wrong processing your file. Please try again.",
            "error_type": type(e).__name__,
        }), 500


def _analyze_inner():
    from app import supabase, groq_client
    from services.auth import get_authenticated_user

    # ── Auth ──────────────────────────────────────────────────────────────────
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    # ── Consent ───────────────────────────────────────────────────────────────
    try:
        from services.compliance import verify_user_consent
        if not verify_user_consent(supabase, user.id, "document_processing"):
            return jsonify({"error": "Document processing consent required"}), 403
    except Exception as e:
        print(f"[ANALYZE] Consent check non-fatal: {e}")

    # ── File validation ───────────────────────────────────────────────────────
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file provided"}), 400

    filename = (file.filename or "").strip()
    if not filename:
        return jsonify({"error": "File has no name."}), 400

    ext = _get_extension(filename)
    if ext not in _ALL_EXTS:
        return jsonify({
            "error": (
                f"Unsupported file type '{ext}'. "
                "Please upload a PDF, TXT, or photo (JPG, PNG, WebP, HEIC)."
            )
        }), 400

    # ── Determine file type category ──────────────────────────────────────────
    is_image = ext in _IMAGE_EXTS
    size_limit = _IMAGE_SIZE_LIMIT if is_image else _PDF_SIZE_LIMIT

    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)

    if file_size > size_limit:
        limit_mb = size_limit / 1024 / 1024
        actual_mb = file_size / 1024 / 1024
        return jsonify({
            "error": f"File too large ({actual_mb:.1f} MB). Max {limit_mb:.0f} MB for {'photos' if is_image else 'documents'}."
        }), 413

    if file_size == 0:
        return jsonify({"error": "The uploaded file is empty."}), 400

    # ── Image quality pre-check (non-blocking) ────────────────────────────────
    quality_hints = {}
    if is_image:
        try:
            from document_processing.vision_extractor import extract_image_quality_hints
            quality_hints = extract_image_quality_hints(file)
            file.seek(0)  # Reset after quality check
        except Exception:
            pass

    # ── Text extraction — route by file type ──────────────────────────────────
    if is_image:
        raw_text = _extract_image_text_safe(file, filename)
    else:
        raw_text = _extract_text_safe(file, filename)

    if isinstance(raw_text, tuple):
        return raw_text  # error response tuple

    if len(raw_text.strip()) < 20:
        if is_image:
            return jsonify({
                "error": (
                    "No readable text found in this photo. "
                    "Tips: ensure good lighting, hold the camera steady, "
                    "make sure the entire report is visible, and avoid glare. "
                    "A PDF scan will give the best results."
                ),
                "quality_hints": quality_hints,
            }), 400
        return jsonify({
            "error": "No readable text found. Try a clearer PDF or a plain-text file."
        }), 400

    # ── Audit + anonymize ─────────────────────────────────────────────────────
    try:
        from services.compliance import audit_log, anonymize_for_llm
        source = "PHOTO" if is_image else "DOCUMENT"
        audit_log(supabase, user.id, f"{source}_UPLOADED",
                  f"file:{filename} chars:{len(raw_text)} size:{file_size}", "PHI")
        anonymized = anonymize_for_llm(raw_text, user.id)
    except Exception as e:
        print(f"[ANALYZE] Audit/anonymize non-fatal: {e}")
        anonymized = raw_text

    user_name     = _get_user_name_safe(supabase, user.id)
    _is_radiology = _detect_radiology(anonymized)
    print(f"[ANALYZE] {'Radiology' if _is_radiology else 'Lab report'} detected from {'photo' if is_image else 'document'}: {filename}")

    # ── Marker extraction + US unit normalization ─────────────────────────────
    active_markers: list = []
    report_date: str = ""

    if not _is_radiology:
        active_markers, report_date = _extract_and_store_markers_safe(
            supabase, groq_client, anonymized, filename, user.id, user_name
        )

    # ── Background jobs ───────────────────────────────────────────────────────
    job_id = uuid.uuid4().hex[:12]

    if active_markers:
        _invalidate_caches(supabase, user.id)

    _submit_doctor_prep_bg_safe(
        supabase, groq_client, anonymized, filename,
        user.id, user_name, active_markers, job_id,
    )

    # ── Build response ────────────────────────────────────────────────────────
    abnormal = [m for m in active_markers if m.get("status") in ("HIGH", "LOW")]
    normal   = [m for m in active_markers if m.get("status") == "NORMAL"]
    prefix   = f"{user_name}, your" if user_name else "Your"
    method   = "photo was scanned" if is_image else "report has been read"

    if active_markers:
        vision_note = " (extracted from photo via Vision AI)" if is_image else ""
        summary = (
            f"{prefix} {method} and **{len(active_markers)} markers stored**{vision_note}. "
            f"{len(abnormal)} need attention — all values in US units. "
            "Ask any questions below."
        )
    elif _is_radiology:
        summary = f"{prefix} {method}. Ask PHI any questions about it below."
    else:
        summary = (
            f"{prefix} {method}. No lab values detected — "
            "ask PHI about it below."
        )

    return jsonify({
        "success":               True,
        "filename":              filename,
        "document_id":           job_id,
        "summary_text":          summary,
        "markers":               active_markers,
        "abnormal_count":        len(abnormal),
        "normal_count":          len(normal),
        "doc_type":              "radiology" if _is_radiology else "lab_report",
        "document_text":         anonymized[:6000],
        "doctor_prep":           "",
        "user_name":             user_name,
        "report_date":           report_date or _today_iso(),
        "processing":            False,
        "job_id":                job_id,
        "persona_refresh":       bool(active_markers),
        "requires_confirmation": False,
        "units":                 "US",
        "extraction_method":     "vision_ai" if is_image else "text",
        "quality_hints":         quality_hints,
    })


# ── Safe helpers ──────────────────────────────────────────────────────────────

def _today_iso() -> str:
    from datetime import date
    return date.today().isoformat()


def _extract_image_text_safe(file, filename: str):
    """
    FIX-VISION-1: Extract text from a photo of a lab report.
    Uses GPT-4o-mini Vision API with pytesseract fallback.
    """
    try:
        from document_processing.vision_extractor import extract_text_from_image
        return extract_text_from_image(file)
    except ImportError:
        # Module not found — try direct tesseract
        try:
            import pytesseract
            from PIL import Image
            import io
            file.seek(0)
            data = file.read()
            img  = Image.open(io.BytesIO(data))
            text = pytesseract.image_to_string(img, lang="eng")
            return text.strip()
        except ImportError:
            return jsonify({
                "error": (
                    "Photo OCR requires OPENAI_API_KEY (recommended) or pytesseract. "
                    "Please configure your environment or upload a PDF instead."
                )
            }), 400
        except Exception as e:
            return jsonify({"error": f"Could not read this photo: {e}"}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[ANALYZE] Image extraction error: {type(e).__name__}: {e}")
        return jsonify({
            "error": (
                f"Could not process this photo ({type(e).__name__}). "
                "Ensure good lighting, a flat document, and try PDF upload for best results."
            )
        }), 400


def _extract_text_safe(file, filename: str):
    """Extract text from PDF or TXT files."""
    file.seek(0)

    for module_path in ["document_processing.extractor", "extractor"]:
        try:
            mod        = __import__(module_path, fromlist=["extract_text_from_file"])
            extract_fn = mod.extract_text_from_file
            try:
                return extract_fn(file)
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
            except ImportError as e:
                return jsonify({
                    "error": (
                        f"PDF processing library not installed: {e}. "
                        "Add 'pypdf' to requirements.txt and redeploy."
                    )
                }), 400
            except Exception as e:
                print(f"[ANALYZE] Extraction error: {type(e).__name__}: {e}")
                return jsonify({
                    "error": f"Could not read this file ({type(e).__name__}). "
                             "Please ensure it is a clear, readable PDF."
                }), 400
        except ImportError:
            continue

    # Direct pypdf fallback
    ext = _get_extension(filename)
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            import io
            file.seek(0)
            data   = file.read()
            reader = PdfReader(io.BytesIO(data))
            pages  = [page.extract_text() or "" for page in reader.pages]
            text   = "\n".join(pages).strip()
            if text:
                return text
            return jsonify({"error": "No text found in this PDF. It may be a scanned image — try uploading a photo instead."}), 400
        except ImportError:
            return jsonify({
                "error": "pypdf is not installed. Add 'pypdf' to requirements.txt and redeploy."
            }), 400
        except Exception as e:
            return jsonify({"error": f"Could not read PDF: {e}"}), 400

    if ext == ".txt":
        try:
            file.seek(0)
            return file.read().decode("utf-8", errors="replace").strip()
        except Exception as e:
            return jsonify({"error": f"Could not read text file: {e}"}), 400

    return jsonify({"error": "Document extraction module not found. Check server setup."}), 500


def _get_user_name_safe(supabase, user_id: str) -> str:
    try:
        res = (supabase.table("user_profiles")
               .select("first_name").eq("user_id", user_id).limit(1).execute())
        return (res.data[0].get("first_name", "") if res.data else "") or ""
    except Exception:
        return ""


def _extract_and_store_markers_safe(
    supabase, groq_client, anonymized: str, filename: str,
    user_id: str, user_name: str,
) -> tuple:
    """
    Extract markers, normalize to US units (FIX-A), then store.

    Supports both text-extracted and vision-extracted content.
    The Vision AI output is already human-readable text — the same
    marker extractor works for both sources.
    """
    markers, report_date = [], ""

    # Guard: need at least one AI backend
    has_ai = bool(groq_client) or bool(os.getenv("OPENAI_API_KEY"))
    if not has_ai:
        print("[ANALYZE] No AI client available — skipping marker extraction.")
        return [], ""

    try:
        from health_memory.extractor import extract_health_markers
    except ImportError as e:
        print(f"[ANALYZE] health_memory.extractor import failed: {e}")
        return [], ""

    # Step 1: Extract raw markers via LLM
    try:
        markers = extract_health_markers(
            text=anonymized,
            groq_client=groq_client,
            source_document=filename,
        )
        if markers:
            report_date = markers[0].get("date", "") or ""
        print(f"[ANALYZE] Extracted {len(markers)} raw markers from {filename}")
    except Exception as e:
        print(f"[ANALYZE] Extraction non-fatal: {type(e).__name__}: {e}")
        return [], ""

    if not markers:
        return [], report_date

    # Step 2: FIX-A — Normalize to US units BEFORE anything else
    try:
        from services.unit_normalizer import force_us_units_batch
        markers   = force_us_units_batch(markers)
        converted = sum(1 for m in markers if m.get("us_converted"))
        if converted:
            print(f"[ANALYZE] US unit normalization: {converted}/{len(markers)} markers converted")
    except ImportError as e:
        print(f"[ANALYZE] unit_normalizer import failed (non-fatal): {e}")
    except Exception as e:
        print(f"[ANALYZE] US unit normalization non-fatal: {e}")

    # Step 3: Explain markers (has its own 8-second timeout guard)
    explained = markers
    try:
        from ai.explainer import explain_markers
        explained = explain_markers(markers, groq_client, user_name)
    except Exception as e:
        print(f"[ANALYZE] Explainer non-fatal: {type(e).__name__}: {e}")

    # Step 4: Store (already US-normalized)
    try:
        from health_memory.memory import store_health_markers
        stored = store_health_markers(supabase, user_id, explained)
        print(f"[ANALYZE] Stored {stored} US-normalized markers for {user_id[:8]}")
    except Exception as e:
        print(f"[ANALYZE] Store non-fatal: {type(e).__name__}: {e}")

    # Step 5: RAG ingest for semantic search
    try:
        from health_memory.rag import ingest_text
        ingest_text(supabase=supabase, user_id=user_id, text=anonymized, source=filename)
    except Exception as e:
        print(f"[ANALYZE] RAG ingest non-fatal: {type(e).__name__}: {e}")

    return explained, report_date


def _invalidate_caches(supabase, user_id: str) -> None:
    """Invalidate the 6-hour persona cache and insights cache after new upload."""
    try:
        supabase.table("user_profiles").update({
            "health_persona_marker_count": -1,
        }).eq("user_id", user_id).execute()
    except Exception as e:
        print(f"[ANALYZE] Persona cache invalidate non-fatal: {e}")

    try:
        supabase.table("health_insights").delete().eq("user_id", user_id).execute()
    except Exception as e:
        print(f"[ANALYZE] Insights cache clear non-fatal: {e}")


def _submit_doctor_prep_bg_safe(
    supabase, groq_client, anonymized, filename,
    user_id, user_name, markers, job_id,
) -> None:
    try:
        from services.job_queue import submit_job
        submit_job(
            _generate_and_store_doctor_prep_safe,
            supabase, groq_client, anonymized, filename,
            user_id, user_name, markers, job_id,
        )
    except Exception as e:
        print(f"[ANALYZE] BG submit non-fatal: {e}")


def _generate_and_store_doctor_prep_safe(
    supabase, groq_client, anonymized, filename,
    user_id, user_name, markers, job_id,
) -> None:
    from datetime import datetime, timezone
    print(f"[BG-PREP] Starting: {filename} job={job_id}")
    try:
        from services.compliance import audit_log
        from ai.chat import generate_doctor_prep

        doctor_prep = generate_doctor_prep(
            groq_client=groq_client,
            document_text=anonymized,
            markers=markers,
            user_name=user_name,
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

        audit_log(supabase, user_id, "DOCTOR_PREP_STORED",
                  f"file:{filename} job:{job_id}", "PHI")
        print(f"[BG-PREP] Done: {filename} job={job_id}")
    except Exception as e:
        print(f"[BG-PREP] {type(e).__name__}: {e}")
        traceback.print_exc()


# ── Doctor prep fetch endpoints ───────────────────────────────────────────────

@document_bp.route("/doctor-prep/<job_id>", methods=["GET"])
def get_doctor_prep(job_id: str):
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        res = (
            supabase.table("medical_documents")
            .select("doctor_prep_text,doctor_prep_generated_at,filename")
            .eq("user_id", user.id).eq("job_id", job_id).limit(1).execute()
        )
        if not res.data or not res.data[0].get("doctor_prep_text"):
            return jsonify({"ready": False})
        row = res.data[0]
        return jsonify({
            "ready":        True,
            "doctor_prep":  row["doctor_prep_text"],
            "filename":     row.get("filename", ""),
            "generated_at": row.get("doctor_prep_generated_at", ""),
        })
    except Exception as e:
        print(f"[DOCTOR PREP] Fetch error: {e}")
        return jsonify({"ready": False})


@document_bp.route("/doctor-prep/history", methods=["GET"])
def doctor_prep_history():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        res = (
            supabase.table("medical_documents")
            .select("job_id,filename,doctor_prep_text,doctor_prep_generated_at,created_at")
            .eq("user_id", user.id)
            .not_.is_("doctor_prep_text", "null")
            .order("created_at", desc=True).limit(20).execute()
        )
        preps = [
            {
                "job_id":       r["job_id"],
                "filename":     r.get("filename", ""),
                "summary":      (r.get("doctor_prep_text") or "")[:200] + "…",
                "full_text":    r.get("doctor_prep_text", ""),
                "generated_at": r.get("doctor_prep_generated_at", ""),
            }
            for r in (res.data or []) if r.get("doctor_prep_text")
        ]
        return jsonify({"preps": preps})
    except Exception as e:
        print(f"[DOCTOR PREP HISTORY] Error: {e}")
        return jsonify({"preps": []})