"""
api/document_routes.py — Memory-connected document analysis
─────────────────────────────────────────────────────────────────────────────
Key fix: markers are stored SYNCHRONOUSLY before returning the response.
Previously they were only stored in the background job — meaning the user
could upload a report, ask a question immediately, and PHI would say it
has no data because the background job hadn't finished yet.

Now: markers are stored within seconds of upload. Background job handles
doctor prep generation (the slow LLM call) without blocking.
"""

from flask import Blueprint, request, jsonify

document_bp = Blueprint("documents", __name__)

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB


@document_bp.route("/analyze", methods=["POST"])
def analyze():
    from app import supabase, groq_client
    from services.auth        import get_authenticated_user
    from services.compliance  import verify_user_consent, audit_log, anonymize_for_llm, check_baa_compliance
    from document_processing.extractor import extract_text_from_file
    from health_memory.extractor        import extract_health_markers
    from health_memory.memory           import store_health_markers
    from ai.explainer                   import explain_markers

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    if not verify_user_consent(supabase, user.id, "document_processing"):
        return jsonify({"error": "Document processing consent required"}), 403

    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file provided"}), 400

    # File size check
    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    if file_size > MAX_FILE_SIZE:
        return jsonify({
            "error": f"File too large. Maximum 5MB. Your file is {file_size // 1024 // 1024:.1f}MB."
        }), 413

    # ── KEY FIX: always reset stream right before extraction ─────────────────
    # The seek(0) above is for size-checking only. Anything between here and
    # extract_text_from_file() could consume the stream — this guarantees a
    # clean read regardless of what happens in between.
    file.seek(0)

    # Extract text
    try:
        raw_text = extract_text_from_file(file)
    except ValueError as e:
        # FIX: log the real error server-side so you can debug it
        print(f"[ANALYZE] Extraction ValueError: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        # FIX: log full traceback, not just the exception message
        import traceback
        print(f"[ANALYZE] Extraction unexpected error: {e}")
        traceback.print_exc()
        return jsonify({"error": "Could not read this file. Please ensure it is a clear, readable PDF."}), 400

    if not raw_text or not raw_text.strip():
        return jsonify({"error": "No readable text found in this file."}), 400

    audit_log(supabase, user.id, "DOCUMENT_UPLOADED",
              f"file:{file.filename} chars:{len(raw_text)} size:{file_size}", "PHI")

    anonymized = anonymize_for_llm(raw_text, user.id)

    # Get user name for personalisation
    user_name = ""
    try:
        res = supabase.table("user_profiles").select("first_name")\
            .eq("user_id", user.id).limit(1).execute()
        if res.data:
            user_name = res.data[0].get("first_name", "")
    except Exception:
        pass

    # Detect radiology vs lab report
    _lower = anonymized.lower()
    _is_radiology = any(k in _lower for k in [
        "mammograph", "radiology", "ultrasound", "mri", "ct scan", "x-ray",
        "impression", "findings:", "bilateral", "scan name", "radio-diagnosis",
        "final report", "discharge summary", "post surgery", "sonograph", "echocardiogram",
    ])

    # ── SYNCHRONOUS marker extraction and storage ─────────────────────────────
    markers   = []
    explained = []

    if not _is_radiology:
        try:
            markers = extract_health_markers(
                text            = anonymized,
                groq_client     = groq_client if check_baa_compliance() else None,
                source_document = file.filename,
            )

            if markers:
                explained = explain_markers(markers, groq_client, user_name)

                stored = store_health_markers(supabase, user.id, explained or markers)
                if stored:
                    audit_log(supabase, user.id, "HEALTH_MARKERS_STORED_SYNC",
                              f"{stored} markers from {file.filename}", "PHI")

                print(f"[ANALYZE] Stored {stored} markers synchronously for user {user.id[:8]}")

        except Exception as extraction_err:
            print(f"[ANALYZE] Marker extraction error: {extraction_err}")
            import traceback; traceback.print_exc()

    # ── Background: doctor prep (the slow LLM call) ───────────────────────────
    import uuid
    from services.job_queue import submit_job
    job_id = uuid.uuid4().hex[:12]

    submit_job(
        _generate_and_store_doctor_prep,
        supabase, groq_client,
        anonymized, file.filename, user.id, user_name,
        explained or markers, job_id,
    )

    # Build response
    abnormal = [m for m in (explained or markers) if m.get("status") in ("HIGH", "LOW")]
    normal   = [m for m in (explained or markers) if m.get("status") == "NORMAL"]
    prefix   = f"{user_name}, your" if user_name else "Your"

    if explained or markers:
        summary = (
            f"{prefix} report has been read and **{len(explained or markers)} markers stored** to your health memory. "
            f"{len(abnormal)} need attention. Ask any questions below."
        )
    elif _is_radiology:
        summary = f"{prefix} report has been read. Ask PHI any questions about it below."
    else:
        summary = f"{prefix} report has been read. No lab values detected — ask PHI about it below."

    return jsonify({
        "success":               True,
        "filename":              file.filename,
        "summary_text":          summary,
        "markers":               explained or markers,
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


def _generate_and_store_doctor_prep(
    supabase, groq_client,
    anonymized: str, filename: str, user_id: str,
    user_name: str, markers: list, job_id: str,
) -> None:
    """Background: generate doctor prep and store it permanently."""
    from services.compliance import audit_log
    from ai.chat import generate_doctor_prep
    from datetime import datetime, timezone

    print(f"[BG-PREP] Starting: {filename} user={user_id[:8]} job={job_id}")
    try:
        doctor_prep = generate_doctor_prep(
            document_text = anonymized,
            markers       = markers,
            user_name     = user_name,
        )
        if not doctor_prep:
            doctor_prep = "Doctor prep unavailable. Ask PHI: 'Prepare me for my doctor visit'."

        now = datetime.now(timezone.utc).isoformat()
        supabase.table("medical_documents").upsert({
            "user_id":                   user_id,
            "job_id":                    job_id,
            "filename":                  filename,
            "doctor_prep_text":          doctor_prep,
            "doctor_prep_generated_at":  now,
            "created_at":                now,
        }, on_conflict="user_id,job_id").execute()

        audit_log(supabase, user_id, "DOCTOR_PREP_STORED",
                  f"file:{filename} job:{job_id}", "PHI")
        print(f"[BG-PREP] Done: {filename} job={job_id}")

    except Exception as e:
        import traceback
        print(f"[BG-PREP] FAILED: {e}")
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
            {
                "job_id":       r["job_id"],
                "filename":     r.get("filename", ""),
                "summary":      (r.get("doctor_prep_text") or "")[:200] + "…",
                "full_text":    r.get("doctor_prep_text", ""),
                "generated_at": r.get("doctor_prep_generated_at", ""),
            }
            for r in (res.data or []) if r.get("doctor_prep_text")
        ]
        audit_log(supabase, user.id, "DOCTOR_PREP_HISTORY_ACCESSED",
                  f"{len(preps)} preps", "PHI")
        return jsonify({"preps": preps})
    except Exception as e:
        print(f"[DOCTOR PREP HISTORY] Error: {e}")
        return jsonify({"preps": []})