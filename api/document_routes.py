"""
api/document_routes.py — Production-hardened
─────────────────────────────────────────────────────────────────────────────
FIXES:
  #500-1  Top-level try/except guarantees JSON response ALWAYS — never empty
          body that causes "Unexpected end of input" in browser.
  #500-2  Import paths tried with fallback (document_processing.extractor →
          extractor) to handle different Render directory structures.
  #500-3  generate_doctor_prep() receives groq_client as first positional arg.
  #500-4  Every failure point has an explicit log message.
"""

from flask import Blueprint, request, jsonify
import traceback
import uuid

document_bp = Blueprint("documents", __name__)

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB


@document_bp.route("/analyze", methods=["POST"])
def analyze():
    """Always returns JSON — the top-level catch prevents empty response bodies."""
    try:
        return _analyze_inner()
    except Exception as e:
        print(f"[ANALYZE] Unhandled exception: {type(e).__name__}: {e}")
        traceback.print_exc()
        return jsonify({"error": "Something went wrong processing your file. Please try again."}), 500


def _analyze_inner():
    from app import supabase, groq_client
    from services.auth       import get_authenticated_user
    from services.compliance import verify_user_consent, audit_log, anonymize_for_llm

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    if not verify_user_consent(supabase, user.id, "document_processing"):
        return jsonify({"error": "Document processing consent required"}), 403

    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file provided"}), 400

    filename = (file.filename or "").strip()
    if not filename:
        return jsonify({"error": "File has no name."}), 400

    if not filename.lower().endswith((".pdf", ".txt")):
        return jsonify({"error": "Unsupported file type. Please upload a PDF or TXT file."}), 400

    # Size check
    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)

    if file_size > MAX_FILE_SIZE:
        return jsonify({"error": f"File too large. Max 5MB. Yours is {file_size // 1024 // 1024:.1f}MB."}), 413
    if file_size == 0:
        return jsonify({"error": "The uploaded file is empty."}), 400

    # ── Text extraction ───────────────────────────────────────────────────────
    raw_text = _extract_text(file, filename)
    if isinstance(raw_text, tuple):   # error response tuple
        return raw_text

    if len(raw_text.strip()) < 20:
        return jsonify({"error": "No readable text found. Try a clearer PDF or a plain text file."}), 400

    audit_log(supabase, user.id, "DOCUMENT_UPLOADED",
              f"file:{filename} chars:{len(raw_text)} size:{file_size}", "PHI")

    anonymized = anonymize_for_llm(raw_text, user.id)

    # User name for personalisation
    user_name = _get_user_name(supabase, user.id)

    # Is this radiology or a lab report?
    _lower = anonymized.lower()
    _is_radiology = any(k in _lower for k in [
        "mammograph", "radiology", "ultrasound", "mri", "ct scan", "x-ray",
        "impression", "findings:", "bilateral", "scan name", "radio-diagnosis",
        "final report", "discharge summary", "post surgery", "sonograph",
    ])

    # ── Synchronous marker extraction ─────────────────────────────────────────
    active_markers = []
    if not _is_radiology:
        active_markers = _extract_and_store_markers(supabase, groq_client, anonymized, filename, user.id, user_name)

    # ── Background doctor prep (non-blocking) ─────────────────────────────────
    job_id = uuid.uuid4().hex[:12]
    _submit_doctor_prep_bg(supabase, groq_client, anonymized, filename, user.id, user_name, active_markers, job_id)

    # Response
    abnormal = [m for m in active_markers if m.get("status") in ("HIGH", "LOW")]
    normal   = [m for m in active_markers if m.get("status") == "NORMAL"]
    prefix   = f"{user_name}, your" if user_name else "Your"

    if active_markers:
        summary = (
            f"{prefix} report has been read and **{len(active_markers)} markers stored**. "
            f"{len(abnormal)} need attention. Ask any questions below."
        )
    elif _is_radiology:
        summary = f"{prefix} report has been read. Ask PHI any questions about it below."
    else:
        summary = f"{prefix} report has been read. No lab values detected — ask PHI about it below."

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
        "processing":            False,
        "job_id":                job_id,
        "requires_confirmation": False,
    })


# ── Private helpers ───────────────────────────────────────────────────────────

def _extract_text(file, filename: str):
    """Returns text string or a jsonify error tuple."""
    file.seek(0)
    try:
        # Try primary module path
        try:
            from document_processing.extractor import extract_text_from_file
        except ImportError:
            from extractor import extract_text_from_file  # type: ignore
        return extract_text_from_file(file)
    except ValueError as e:
        print(f"[ANALYZE] Extraction ValueError: {e}")
        return jsonify({"error": str(e)}), 400
    except ImportError as e:
        print(f"[ANALYZE] Extractor import failed: {e}")
        return jsonify({"error": "Document extraction unavailable. Contact support."}), 500
    except Exception as e:
        print(f"[ANALYZE] Extraction error: {type(e).__name__}: {e}")
        traceback.print_exc()
        return jsonify({"error": "Could not read this file. Please ensure it is a clear, readable PDF."}), 400


def _get_user_name(supabase, user_id: str) -> str:
    try:
        res = supabase.table("user_profiles").select("first_name")\
            .eq("user_id", user_id).limit(1).execute()
        return (res.data[0].get("first_name", "") if res.data else "") or ""
    except Exception:
        return ""


def _extract_and_store_markers(supabase, groq_client, anonymized: str, filename: str, user_id: str, user_name: str) -> list:
    markers = []

    # Extract
    try:
        try:
            from health_memory.extractor import extract_health_markers
        except ImportError:
            from extractor import extract_health_markers  # type: ignore
        markers = extract_health_markers(text=anonymized, groq_client=groq_client, source_document=filename)
        print(f"[ANALYZE] Extracted {len(markers)} markers from {filename}")
    except Exception as e:
        print(f"[ANALYZE] Extraction failed (non-fatal): {type(e).__name__}: {e}")
        return []

    if not markers:
        return []

    # Explain
    explained = markers
    try:
        from ai.explainer import explain_markers
        explained = explain_markers(markers, groq_client, user_name)
    except Exception as e:
        print(f"[ANALYZE] Explainer failed (non-fatal): {type(e).__name__}: {e}")

    # Store
    try:
        from health_memory.memory import store_health_markers
        stored = store_health_markers(supabase, user_id, explained)
        print(f"[ANALYZE] ✅ Stored {stored} markers for user {user_id[:8]}")
    except Exception as e:
        print(f"[ANALYZE] Store failed (non-fatal): {type(e).__name__}: {e}")

    return explained


def _submit_doctor_prep_bg(supabase, groq_client, anonymized: str, filename: str, user_id: str, user_name: str, markers: list, job_id: str):
    try:
        from services.job_queue import submit_job
        submit_job(_generate_and_store_doctor_prep, supabase, groq_client, anonymized, filename, user_id, user_name, markers, job_id)
    except Exception as e:
        print(f"[ANALYZE] Background job submit error (non-fatal): {e}")


def _generate_and_store_doctor_prep(supabase, groq_client, anonymized, filename, user_id, user_name, markers, job_id):
    from datetime import datetime, timezone
    print(f"[BG-PREP] Starting: {filename} job={job_id}")
    try:
        from services.compliance import audit_log
        from ai.chat import generate_doctor_prep

        doctor_prep = generate_doctor_prep(
            groq_client   = groq_client,
            document_text = anonymized,
            markers       = markers,
            user_name     = user_name,
        )
        if not doctor_prep:
            doctor_prep = "Ask PHI: 'Prepare me for my doctor visit' for a personalized brief."

        now = datetime.now(timezone.utc).isoformat()
        supabase.table("medical_documents").upsert({
            "user_id":                  user_id,
            "job_id":                   job_id,
            "filename":                 filename,
            "doctor_prep_text":         doctor_prep,
            "doctor_prep_generated_at": now,
            "created_at":               now,
        }, on_conflict="user_id,job_id").execute()

        audit_log(supabase, user_id, "DOCTOR_PREP_STORED", f"file:{filename} job:{job_id}", "PHI")
        print(f"[BG-PREP] ✅ Done: {filename} job={job_id}")
    except Exception as e:
        print(f"[BG-PREP] ❌ FAILED: {type(e).__name__}: {e}")
        traceback.print_exc()


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
            .eq("user_id", user.id)
            .eq("job_id",  job_id)
            .limit(1)
            .execute()
        )
        if not res.data or not res.data[0].get("doctor_prep_text"):
            return jsonify({"ready": False})
        row = res.data[0]
        return jsonify({"ready": True, "doctor_prep": row["doctor_prep_text"],
                        "filename": row.get("filename", ""), "generated_at": row.get("doctor_prep_generated_at", "")})
    except Exception as e:
        print(f"[DOCTOR PREP] Fetch error: {e}")
        return jsonify({"ready": False})


@document_bp.route("/doctor-prep/history", methods=["GET"])
def doctor_prep_history():
    from app import supabase
    from services.auth       import get_authenticated_user
    from services.compliance import audit_log
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        res = (
            supabase.table("medical_documents")
            .select("job_id,filename,doctor_prep_text,doctor_prep_generated_at,created_at")
            .eq("user_id", user.id)
            .not_.is_("doctor_prep_text", "null")
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        preps = [
            {"job_id": r["job_id"], "filename": r.get("filename",""),
             "summary": (r.get("doctor_prep_text") or "")[:200]+"…",
             "full_text": r.get("doctor_prep_text",""),
             "generated_at": r.get("doctor_prep_generated_at","")}
            for r in (res.data or []) if r.get("doctor_prep_text")
        ]
        audit_log(supabase, user.id, "DOCTOR_PREP_HISTORY_ACCESSED", f"{len(preps)} preps", "PHI")
        return jsonify({"preps": preps})
    except Exception as e:
        print(f"[DOCTOR PREP HISTORY] Error: {e}")
        return jsonify({"preps": []})