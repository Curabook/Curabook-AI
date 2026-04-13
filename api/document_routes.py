"""
api/document_routes.py — Bulletproof edition
─────────────────────────────────────────────────────────────────────────────
FIX #RADIOLOGY: Lab reports falsely detected as radiology (words like
"final report", "findings:", "impression") — now requires STRONG radiology
keywords AND absence of lab data indicators.

FIX #PERSONA-REFRESH (Step 4 — Launch): After successful marker storage,
immediately queues generate_recursive_summary(force_refresh=True) in the
background job queue. This ensures the Health Persona shown on the welcome
screen reflects the newly uploaded report without any cache delay.
"""

from flask import Blueprint, request, jsonify
import traceback
import uuid

document_bp = Blueprint("documents", __name__)

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB

# ── Radiology detection keywords ──────────────────────────────────────────────
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
    has_strong_radiology = any(k in lower for k in _RADIOLOGY_STRONG)
    has_lab_data         = any(k in lower for k in _LAB_INDICATORS)

    if has_strong_radiology and not has_lab_data:
        return True

    secondary_radiology = ["ultrasound", "mri", "ct scan", "x-ray", "mammogram"]
    has_secondary = any(k in lower for k in secondary_radiology)
    if has_secondary and not has_lab_data:
        return True

    return False


# ── Diagnostic endpoint ───────────────────────────────────────────────────────

@document_bp.route("/diagnose", methods=["GET"])
def diagnose():
    results = {}

    def _try(label, fn):
        try:
            fn()
            results[label] = {"ok": True}
        except Exception as e:
            results[label] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    _try("pypdf",                   lambda: __import__("pypdf"))
    _try("pdf2image",               lambda: __import__("pdf2image"))
    _try("pytesseract",             lambda: __import__("pytesseract"))
    _try("groq",                    lambda: __import__("groq"))
    _try("openai",                  lambda: __import__("openai"))
    _try("supabase",                lambda: __import__("supabase"))

    _try("document_processing.extractor",
         lambda: __import__("document_processing.extractor", fromlist=["extract_text_from_file"]))
    _try("health_memory.extractor",
         lambda: __import__("health_memory.extractor", fromlist=["extract_health_markers"]))
    _try("health_memory.memory",
         lambda: __import__("health_memory.memory", fromlist=["store_health_markers"]))
    _try("ai.chat",
         lambda: __import__("ai.chat", fromlist=["generate_doctor_prep", "call_llm"]))
    _try("ai.explainer",
         lambda: __import__("ai.explainer", fromlist=["explain_markers"]))
    _try("services.job_queue",
         lambda: __import__("services.job_queue", fromlist=["submit_job"]))
    _try("services.compliance",
         lambda: __import__("services.compliance", fromlist=["audit_log", "anonymize_for_llm"]))

    all_ok = all(v["ok"] for v in results.values())
    return jsonify({
        "status":  "all_ok" if all_ok else "some_failed",
        "imports": results,
    }), 200 if all_ok else 500


# ── Main analyze route ────────────────────────────────────────────────────────

@document_bp.route("/analyze", methods=["POST"])
def analyze():
    try:
        return _analyze_inner()
    except Exception as e:
        print(f"[ANALYZE] ❌ Unhandled {type(e).__name__}: {e}")
        traceback.print_exc()
        return jsonify({
            "error":      "Something went wrong processing your file. Please try again.",
            "error_type": type(e).__name__,
        }), 500


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

    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)

    if file_size > MAX_FILE_SIZE:
        return jsonify({
            "error": f"File too large. Max 5 MB. Yours is {file_size / 1024 / 1024:.1f} MB."
        }), 413
    if file_size == 0:
        return jsonify({"error": "The uploaded file is empty."}), 400

    raw_text = _extract_text(file, filename)
    if isinstance(raw_text, tuple):
        return raw_text

    if len(raw_text.strip()) < 20:
        return jsonify({
            "error": "No readable text found. Try a clearer PDF or a plain-text file."
        }), 400

    audit_log(supabase, user.id, "DOCUMENT_UPLOADED",
              f"file:{filename} chars:{len(raw_text)} size:{file_size}", "PHI")

    anonymized = anonymize_for_llm(raw_text, user.id)
    user_name  = _get_user_name(supabase, user.id)

    # ── Radiology detection ───────────────────────────────────────────────────
    _is_radiology = _detect_radiology(anonymized)
    if _is_radiology:
        print(f"[ANALYZE] Detected as radiology/imaging report: {filename}")
    else:
        print(f"[ANALYZE] Detected as lab report — proceeding with marker extraction: {filename}")

    # ── Marker extraction + storage (synchronous) ─────────────────────────────
    active_markers = []
    if not _is_radiology:
        active_markers = _extract_and_store_markers(
            supabase, groq_client, anonymized, filename, user.id, user_name
        )

    # ── FIX #PERSONA-REFRESH: Refresh persona immediately after new markers ───
    # (Step 4 — Launch Readiness)
    # Queued in background so /analyze response is never blocked.
    if active_markers:
        _queue_persona_refresh(supabase, user.id)

    # ── Background: doctor prep (non-blocking) ────────────────────────────────
    job_id = uuid.uuid4().hex[:12]
    _submit_doctor_prep_bg(
        supabase, groq_client, anonymized, filename,
        user.id, user_name, active_markers, job_id
    )

    # ── Build response ────────────────────────────────────────────────────────
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
        summary = (
            f"{prefix} report has been read. No lab values detected — "
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
        "processing":            False,
        "job_id":                job_id,
        "requires_confirmation": False,
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_text(file, filename: str):
    file.seek(0)
    extract_fn = None

    try:
        from document_processing.extractor import extract_text_from_file
        extract_fn = extract_text_from_file
    except ImportError:
        pass

    if extract_fn is None:
        try:
            from extractor import extract_text_from_file as _fn
            extract_fn = _fn
        except ImportError:
            pass

    if extract_fn is None:
        print("[ANALYZE] ❌ Cannot import extract_text_from_file from any path")
        return jsonify({
            "error": "Document extraction module not found. Check server setup."
        }), 500

    try:
        return extract_fn(file)
    except ValueError as e:
        print(f"[ANALYZE] Extraction ValueError: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[ANALYZE] Extraction error: {type(e).__name__}: {e}")
        traceback.print_exc()
        return jsonify({
            "error": f"Could not read this file ({type(e).__name__}). "
                     "Please ensure it is a clear, readable PDF."
        }), 400


def _get_user_name(supabase, user_id: str) -> str:
    try:
        res = (supabase.table("user_profiles")
               .select("first_name").eq("user_id", user_id).limit(1).execute())
        return (res.data[0].get("first_name", "") if res.data else "") or ""
    except Exception:
        return ""


def _extract_and_store_markers(
    supabase, groq_client, anonymized: str, filename: str,
    user_id: str, user_name: str,
) -> list:
    extract_fn = None
    try:
        from health_memory.extractor import extract_health_markers
        extract_fn = extract_health_markers
    except ImportError:
        pass

    if extract_fn is None:
        print("[ANALYZE] health_memory.extractor not found — no marker extraction")
        return []

    markers = []
    try:
        markers = extract_fn(
            text=anonymized, groq_client=groq_client, source_document=filename
        )
        print(f"[ANALYZE] Extracted {len(markers)} markers from {filename}")
    except Exception as e:
        print(f"[ANALYZE] Extraction non-fatal: {type(e).__name__}: {e}")
        return []

    if not markers:
        return []

    explained = markers
    try:
        from ai.explainer import explain_markers
        explained = explain_markers(markers, groq_client, user_name)
    except Exception as e:
        print(f"[ANALYZE] Explainer non-fatal: {type(e).__name__}: {e}")

    try:
        from health_memory.memory import store_health_markers
        stored = store_health_markers(supabase, user_id, explained)
        print(f"[ANALYZE] ✅ Stored {stored} markers for {user_id[:8]}")
    except Exception as e:
        print(f"[ANALYZE] Store non-fatal: {type(e).__name__}: {e}")

    return explained


def _queue_persona_refresh(supabase, user_id: str) -> None:
    """
    FIX #PERSONA-REFRESH (Step 4):
    Queue an immediate persona regeneration after new markers are stored.
    Runs in the background worker — never blocks the /analyze response.
    The refreshed persona is cached in user_profiles.health_persona_text
    and injected into the NEXT chat turn automatically.
    """
    try:
        from services.job_queue import submit_job
        from health_memory.persona import generate_recursive_summary

        submit_job(generate_recursive_summary, supabase, user_id, force_refresh=True)
        print(f"[ANALYZE] 🔄 Persona refresh queued for {user_id[:8]}")
    except Exception as e:
        print(f"[ANALYZE] Persona refresh queue (non-fatal): {e}")


def _submit_doctor_prep_bg(
    supabase, groq_client, anonymized: str, filename: str,
    user_id: str, user_name: str, markers: list, job_id: str,
):
    try:
        from services.job_queue import submit_job
        submit_job(
            _generate_and_store_doctor_prep,
            supabase, groq_client, anonymized, filename,
            user_id, user_name, markers, job_id,
        )
    except Exception as e:
        print(f"[ANALYZE] BG submit non-fatal: {e}")


def _generate_and_store_doctor_prep(
    supabase, groq_client, anonymized, filename,
    user_id, user_name, markers, job_id,
):
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
        print(f"[BG-PREP] ✅ Done: {filename} job={job_id}")
    except Exception as e:
        print(f"[BG-PREP] ❌ {type(e).__name__}: {e}")
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
        audit_log(supabase, user.id, "DOCTOR_PREP_HISTORY_ACCESSED",
                  f"{len(preps)} preps", "PHI")
        return jsonify({"preps": preps})
    except Exception as e:
        print(f"[DOCTOR PREP HISTORY] Error: {e}")
        return jsonify({"preps": []})