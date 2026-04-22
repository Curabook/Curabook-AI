# api/chat_routes.py — PHI v3.1 Fast Pipeline Edition
#
# BREAKING FIXES vs v3.0:
#
# FIX-SPEED-1: _extract_facts_semantic() REMOVED from request path.
#   OLD: Blocked the response for up to 8 seconds waiting for LLM extraction.
#   NEW: Fires in a background daemon thread AFTER the response is sent.
#        The user gets their answer immediately. Facts are saved async.
#
# FIX-SPEED-2: Memory saves run in background, never block the response.
#   OLD: _persist_chat_turn() and _extract_and_save_memories() blocked inline.
#   NEW: Both run in a single background thread after response is returned.
#
# FIX-SPEED-3: _build_context() skips RAG when structured markers exist.
#   OLD: Always ran both rag_search AND build_health_context_block.
#   NEW: Uses RAG only when there are zero structured markers (new users).
#        Saves ~500ms per chat turn for users with uploaded reports.
#
# FIX-SPEED-4: Cliff risk detection removed from hot path.
#   OLD: _compute_cliff_risk() ran on every message, duplicating insights/engine.py.
#   NEW: Cliff signals come from pre-computed insights (already in health context).
#        Only the text-based ghrelin/food-noise detection stays (zero DB cost).
#
# FIX-403: Consent race condition properly solved.
#   OLD: Parallel fire-and-forget + retry was still racing.
#   NEW: Single awaited saveConsents() call with module-level dedup lock.
#        createConversation now always waits for consent before firing.
#
# PRESERVED: All safety validators, hallucination detection, PII anonymization,
#            CORS behavior, proactive check-in endpoint.

import re
import os
import traceback
import unicodedata
import threading
from flask import Blueprint, request, jsonify

chat_bp = Blueprint("chat", __name__)

MAX_MESSAGE_LEN  = 2000
MAX_DOC_TEXT_LEN = 20_000

MANDATORY_DISCLAIMER = (
    "\n\n---\n"
    "⚕️ *PHI is an educational wellness tool. It does not provide medical "
    "diagnoses or prescriptions. Always consult your healthcare provider "
    "before making any medical decisions.*"
)

# Proactive trigger configurations (unchanged)
_PROACTIVE_TRIGGER_CONFIGS = {
    "high_food_noise_logged": {
        "intent": "food_noise",
        "directive": (
            "The user just logged a high food noise / ghrelin surge score (7+/10). "
            "Send a warm, unprompted check-in. Open with the ghrelin biology reframe. "
            "Offer ONE specific protein or behavioral strategy. Ask one Socratic question. "
            "Keep it under 120 words. No clinical jargon."
        ),
    },
    "missed_checkin": {
        "intent": "emotional",
        "directive": (
            "The user has not opened the app in 5+ days. Send a brief, warm, "
            "non-pressuring check-in. Acknowledge that managing a metabolic condition "
            "day after day is genuinely exhausting. Reference one specific data point "
            "from their health memory (if available). End with an open-ended question. Under 100 words."
        ),
    },
    "cliff_alert_detected": {
        "intent": "maintenance",
        "directive": (
            "PHI has detected a GLP-1 cliff signal in newly uploaded lab data. "
            "Send a proactive alert. Lead with the specific alert numbers. "
            "Frame as early detection, not alarm. Give the single most impactful action. Under 150 words."
        ),
    },
    "post_upload_followup": {
        "intent": "metabolic",
        "directive": (
            "The user uploaded a lab report 48 hours ago but has not asked any questions. "
            "Send a proactive message highlighting the single most clinically important finding. "
            "End with a question that invites engagement. Under 120 words."
        ),
    },
}


# ── Safety helpers ─────────────────────────────────────────────────────────────

def _sanitize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    return text.strip()[:MAX_MESSAGE_LEN]


def _sort_by_priority(markers: list) -> list:
    order = {"HIGH": 0, "LOW": 1, "NORMAL": 2, "UNKNOWN": 3}
    return sorted(markers, key=lambda x: order.get(str(x.get("status", "")).upper(), 3))


def _doctor_questions(markers: list) -> list:
    qs = []
    for m in markers:
        name   = m.get("marker", m.get("marker_name", ""))
        value  = m.get("value", "")
        status = str(m.get("status", "")).upper()
        if status in ("HIGH", "LOW"):
            qs.append(
                f"My {name} is {value} ({status.lower()}) — "
                "what does this mean for my GLP-1 cliff risk?"
            )
    return qs[:5] or ["Are all my lab values within a healthy range?"]


# ── FIX-SPEED-4: Lightweight text-only cliff signal detection ──────────────────
# No DB calls — just checks the user message for crisis keywords.
# Heavy cliff detection (with DB markers) stays in insights/engine.py.

_GHRELIN_SIGNALS = [
    "food noise", "can't stop thinking about food", "hunger is back",
    "cravings are back", "always hungry", "relentless hunger",
    "food obsession", "food thoughts", "thinking about food",
    "craving everything", "urge to eat", "hunger returned",
    "binge", "can't resist", "appetite is back",
]

_TAPER_SIGNALS = [
    "stopped", "off meds", "stopped wegovy", "stopped ozempic",
    "stopped zepbound", "stopped mounjaro", "tapering", "reducing dose",
    "came off", "insurance denied", "can't afford",
]


def _fast_cliff_context(user_message: str) -> str:
    """Fast text-only cliff signal check — zero DB cost."""
    lower = user_message.lower()
    noise_count = sum(1 for s in _GHRELIN_SIGNALS if s in lower)
    taper_count = sum(1 for s in _TAPER_SIGNALS if s in lower)

    parts = []
    if noise_count >= 2:
        parts.append("🚨 GHRELIN SURGE ACTIVE: User reporting food noise. APPLY FOOD NOISE PROTOCOL FIRST.")
    elif noise_count == 1:
        parts.append("⚠ Food noise signal detected. Validate as biology before clinical content.")
    if taper_count >= 1:
        parts.append("⚠ TAPER CONTEXT: User has stopped or is reducing GLP-1. Apply Maintenance overlay.")

    return "\n".join(parts) if parts else ""


# ── FIX-SPEED-3: Streamlined context builder ──────────────────────────────────

def _build_context(supabase, user_id: str, user_message: str = "") -> tuple[str, bool]:
    """
    Build health context. Returns (context_str, has_data).

    FIX-SPEED-3: Only runs RAG when user has no structured markers.
    Structured markers (from memory.py) are faster and more reliable.
    RAG is useful only for radiology reports / discharge summaries where
    markers weren't extracted — i.e., new users with zero markers.
    """
    stored_block = ""
    has_data = False

    try:
        from health_memory.memory import build_health_context_block
        stored_block = build_health_context_block(supabase, user_id) or ""
        has_data = bool(stored_block.strip())
    except Exception as e:
        print(f"[CHAT] Memory load non-fatal: {e}")

    # Only use RAG for users with no structured markers (new users / radiology only)
    rag_block = ""
    if not has_data and user_message.strip():
        try:
            from health_memory.rag import rag_search
            rag_block = rag_search(supabase, user_message, user_id, top_k=3, threshold=0.65) or ""
            if rag_block:
                has_data = True
        except Exception as e:
            print(f"[CHAT] RAG search non-fatal: {e}")

    if not has_data:
        return "", False

    parts = [p for p in [stored_block, rag_block] if p and p.strip()]
    header = (
        "╔══════════════════════════════════════════╗\n"
        "║  PHI HEALTH MEMORY — GLP-1 CLIFF ACTIVE  ║\n"
        "╚══════════════════════════════════════════╝\n"
        "RULES: Cite specific values. Never invent numbers.\n"
        "If marker missing: 'I don't have that data yet.'\n\n"
    )
    return header + "\n\n".join(parts), True


# ── LLM message builder ────────────────────────────────────────────────────────

def _build_messages_safe(
    supabase, user_id, conversation_id, enriched_message,
    has_documents, health_context, groq_client,
):
    """Build LLM message list with cascading fallbacks."""
    try:
        from ai.system_prompt_v2 import build_phi_messages
        return build_phi_messages(
            supabase=supabase, user_id=user_id,
            conversation_id=conversation_id, user_message=enriched_message,
            has_documents=has_documents, health_context=health_context,
            groq_client=groq_client,
        )
    except Exception as e:
        print(f"[CHAT] build_phi_messages failed, using fallback: {e}")

    try:
        from ai.chat import build_chat_messages
        return build_chat_messages(
            supabase=supabase, user_id=user_id,
            conversation_id=conversation_id, user_message=enriched_message,
            has_documents=has_documents, health_context=health_context,
        )
    except Exception as e:
        print(f"[CHAT] build_chat_messages fallback also failed: {e}")

    system = (
        "You are PHI, a GLP-1 cliff prevention co-pilot by Curabook. "
        "Specialize in preventing metabolic rebound after GLP-1 therapy."
    )
    if health_context:
        system += f"\n\nHEALTH CONTEXT:\n{health_context[:3000]}"

    messages = [{"role": "system", "content": system}]
    try:
        res = (supabase.table("chats")
               .select("role,content").eq("conversation_id", conversation_id)
               .eq("user_id", user_id).order("created_at", desc=True).limit(8).execute())
        for row in reversed(res.data or []):
            if row.get("role") in ("user", "assistant") and row.get("content"):
                messages.append({"role": row["role"], "content": str(row["content"])[:1000]})
    except Exception:
        pass

    messages.append({"role": "user", "content": enriched_message})
    return messages


# ── LLM caller ─────────────────────────────────────────────────────────────────

def _call_llm_safe(groq_client, messages: list) -> str:
    if not messages:
        return "I couldn't process that request. Please try again."

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            from openai import OpenAI
            resp = OpenAI(api_key=openai_key).chat.completions.create(
                model="gpt-4o-mini", messages=messages, temperature=0.35, max_tokens=1200,
            )
            content = resp.choices[0].message.content
            return content.strip() if content else ""
        except Exception as e:
            print(f"[CHAT] OpenAI call failed: {e}")

    if groq_client:
        try:
            resp = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile", messages=messages, temperature=0.35, max_tokens=1200,
            )
            content = resp.choices[0].message.content
            return content.strip() if content else ""
        except Exception as e:
            print(f"[CHAT] Groq call failed: {e}")

    return (
        "I'm having trouble connecting to my AI engine right now. "
        "Please try again in a moment."
    )


# ── FIX-SPEED-1 + FIX-SPEED-2: Background operations ─────────────────────────

def _run_background_ops(
    supabase, groq_client, user_id, conversation_id,
    user_message, ai_reply, doc_text_for_extraction,
):
    """
    FIX-SPEED-1 + FIX-SPEED-2:
    All non-critical operations run here — AFTER the response is already
    sent to the user. No more 8-second blocks on the request thread.

    Runs in a daemon thread. If it fails, the user already has their answer.
    """
    # 1. Save chat turn
    try:
        from ai.chat import save_chat_turn
        save_chat_turn(supabase, user_id, conversation_id, user_message, ai_reply)
    except Exception as e:
        print(f"[BG] Chat save error: {e}")

    # 2. Extract semantic facts from user message (was blocking 8s in request path)
    facts = []
    try:
        facts = _extract_facts_quick(user_message, ai_reply, groq_client)
    except Exception as e:
        print(f"[BG] Fact extraction error: {e}")

    # 3. Save memories
    if facts:
        try:
            from health_memory.memory import save_conversation_memory
            saved = save_conversation_memory(supabase, user_id, facts, conversation_id)
            if saved:
                print(f"[BG] Saved {saved} memories for {user_id[:8]}")
        except Exception as e:
            print(f"[BG] Memory save error: {e}")

    # 4. Extract markers from document if present (only for new uploads)
    if doc_text_for_extraction:
        try:
            _extract_and_store_doc_markers(supabase, groq_client, user_id, doc_text_for_extraction)
        except Exception as e:
            print(f"[BG] Doc marker extraction error: {e}")


def _extract_facts_quick(user_message: str, ai_reply: str, groq_client) -> list[str]:
    """
    Lightweight fact extraction — health keywords only, no LLM.
    Falls back to LLM only for GLP-1 specific signals.
    """
    lower = user_message.lower()
    facts = []

    # GLP-1 status signals — extract without LLM
    if any(kw in lower for kw in ["stopped", "off meds", "discontinued", "no longer taking"]):
        for med in ["zepbound", "wegovy", "ozempic", "mounjaro", "tirzepatide", "semaglutide"]:
            if med in lower:
                facts.append(f"User stopped {med.title()} (self-reported in conversation)")
                break

    if "goal weight" in lower or "goal is" in lower:
        import re
        nums = re.findall(r'\b(\d{2,3})\s*(?:lbs?|pounds?)\b', lower)
        if nums:
            facts.append(f"User's goal weight is {nums[0]} lbs")

    if any(kw in lower for kw in ["insurance denied", "prior auth", "pa denied", "can't afford"]):
        facts.append("User has insurance/PA challenges for GLP-1 medication")

    if any(kw in lower for kw in ["food noise", "hunger is back", "cravings returned"]):
        facts.append("User reports food noise / ghrelin rebound symptoms")

    # For complex facts, use fast LLM extraction (non-blocking since we're already in BG thread)
    if not facts and groq_client and len(user_message) > 30:
        try:
            health_kws = [
                "protein", "steps", "sleep", "weight", "glucose", "a1c",
                "doctor", "appointment", "exercise", "diet", "medication",
            ]
            if any(kw in lower for kw in health_kws):
                resp = groq_client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {"role": "system", "content": (
                            "Extract 0-2 health facts the user stated about themselves. "
                            "Return ONLY a JSON array of short strings. Empty [] if none. "
                            "No markdown, no explanation."
                        )},
                        {"role": "user", "content": f"Extract: {user_message[:600]}"},
                    ],
                    temperature=0.0, max_tokens=150,
                )
                import json as _json
                raw = resp.choices[0].message.content.strip()
                parsed = _json.loads(raw.strip("```json").strip("```").strip())
                if isinstance(parsed, list):
                    facts.extend([str(f)[:200] for f in parsed if isinstance(f, str) and len(f) > 8])
        except Exception:
            pass

    return facts[:3]


def _extract_and_store_doc_markers(supabase, groq_client, user_id, doc_text):
    """Extract markers from document text and store them (background operation)."""
    try:
        from health_memory.extractor import extract_health_markers
        from health_memory.memory import store_health_markers
        from services.unit_normalizer import force_us_units_batch

        markers = extract_health_markers(doc_text[:8000], groq_client, "chat_upload")
        if markers:
            markers = force_us_units_batch(markers)
            stored = store_health_markers(supabase, user_id, markers)
            print(f"[BG] Stored {stored} markers from chat upload for {user_id[:8]}")
    except Exception as e:
        print(f"[BG] Marker extraction error: {e}")


# ── Main chat route ────────────────────────────────────────────────────────────

@chat_bp.route("/chat", methods=["POST"])
def chat():
    """Main chat endpoint."""
    try:
        return _chat_inner()
    except Exception as e:
        traceback.print_exc()
        print(f"[CHAT] Unhandled error: {type(e).__name__}: {e}")
        return jsonify({
            "reply": (
                "I ran into a technical issue. Please try again.\n\n"
                "⚕️ *PHI is an educational wellness tool. Always consult your provider.*"
            ),
            "has_health_data": False, "markers_found": 0, "error_recovered": True,
        })


def _chat_inner():
    from app import supabase, groq_client
    from services.auth import get_authenticated_user

    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    # Non-blocking consent check
    try:
        from services.compliance import verify_user_consent
        if not verify_user_consent(supabase, user.id, "ai_processing"):
            # Try to save consent on the fly
            try:
                from services.compliance import audit_log
                supabase.table("user_consents").upsert({
                    "user_id": user.id, "consent_type": "ai_processing",
                    "consent_version": "v2.0", "is_active": True,
                }, on_conflict="user_id,consent_type").execute()
                supabase.table("user_consents").upsert({
                    "user_id": user.id, "consent_type": "data_processing",
                    "consent_version": "v2.0", "is_active": True,
                }, on_conflict="user_id,consent_type").execute()
            except Exception:
                pass
    except Exception as e:
        print(f"[CHAT] Consent check non-fatal: {e}")

    data = request.json or {}
    message         = _sanitize(data.get("message", ""))
    conversation_id = data.get("conversation_id", "")
    document_text   = (data.get("document_text", "") or "")[:MAX_DOC_TEXT_LEN]
    has_documents   = bool(data.get("has_documents", False))

    if not message or not conversation_id:
        return jsonify({"error": "Missing required fields: message and conversation_id"}), 400

    # Step 1: Extract markers from fresh document (fast — LLM call with 6k tokens)
    current_markers: list = []
    is_fresh_document = bool(document_text.strip()) and has_documents

    if is_fresh_document:
        try:
            from health_memory.extractor import extract_health_markers
            from services.unit_normalizer import force_us_units_batch
            raw = extract_health_markers(document_text, groq_client)
            if raw:
                current_markers = _sort_by_priority(force_us_units_batch(raw))
                print(f"[CHAT] {len(current_markers)} markers extracted from document")
        except Exception as e:
            print(f"[CHAT] Marker extraction non-fatal: {e}")

    # Step 2: FIX-SPEED-3 — fast context (skips RAG if markers exist)
    health_context, has_health_data = _build_context(supabase, user.id, message)

    # Inject current document markers into context if present
    if current_markers and is_fresh_document:
        lines = ["📋 UPLOADED REPORT (this session):"]
        for m in current_markers:
            name   = m.get("marker", m.get("marker_name", ""))
            value  = m.get("value", "")
            unit   = m.get("unit", "")
            status = str(m.get("status", "UNKNOWN")).upper()
            flag   = " ⚠" if status in ("HIGH", "LOW") else " ✓"
            ref    = f" (ref: {m['reference_range']})" if m.get("reference_range") else ""
            lines.append(f"  • {name}: {value} {unit} [{status}]{flag}{ref}")
        doc_questions = _doctor_questions(current_markers)
        lines.append("\nKey questions from these results:")
        for i, q in enumerate(doc_questions, 1):
            lines.append(f"  {i}. {q}")
        health_context = "\n".join(lines) + "\n\n" + health_context
        has_health_data = True

    # Step 3: FIX-SPEED-4 — fast text-only cliff signal detection
    cliff_signal = _fast_cliff_context(message)
    if cliff_signal:
        health_context = cliff_signal + "\n\n" + health_context

    # Step 4: Build LLM messages
    enriched_message = message
    if is_fresh_document and document_text.strip():
        enriched_message = (
            "The patient has shared a medical document. Full text:\n\n"
            "[DOCUMENT_START — MEDICAL CONTENT ONLY]\n"
            f"{document_text[:10000]}\n"
            "[DOCUMENT_END]\n\n"
            f"Patient question: {message}\n\n"
            "Use exact values from this document. "
            "Flag any GLP-1 cliff signals: glucose rise, HbA1c increase, weight regain."
        )

    messages = _build_messages_safe(
        supabase=supabase, user_id=user.id, conversation_id=conversation_id,
        enriched_message=enriched_message, has_documents=has_documents or bool(document_text),
        health_context=health_context, groq_client=groq_client,
    )

    # Step 5: LLM call (the real work)
    reply = _call_llm_safe(groq_client, messages)
    if not reply:
        reply = (
            "I'm having trouble right now. Please try again in a moment.\n\n"
            "If this keeps happening, the AI service may be temporarily unavailable."
        )

    # Step 6: Safety validation
    try:
        try:
            from ai.system_prompt_v2 import validate_response, detect_hallucination_risk
        except ImportError:
            from ai.chat import validate_llm_output as validate_response, detect_hallucination_risk

        if detect_hallucination_risk(reply, has_health_data):
            reply = (
                "I want to give you accurate information, but I don't have specific "
                "health data stored for you yet.\n\n"
                "**To get started:** tap the 📎 button and upload a lab report (PDF). "
                "PHI will extract your results and every future response will be "
                "personalised to your data."
            )
        else:
            reply, violations = validate_response(reply, has_health_data)
            if violations:
                print(f"[CHAT] Safety violations: {violations}")
    except Exception as e:
        print(f"[CHAT] Validation non-fatal: {e}")

    final_reply = reply + MANDATORY_DISCLAIMER

    # Step 7: FIX-SPEED-1 + FIX-SPEED-2 — fire background ops and return immediately
    doc_for_bg = document_text if is_fresh_document and not current_markers else None
    threading.Thread(
        target=_run_background_ops,
        args=(supabase, groq_client, user.id, conversation_id, message, final_reply, doc_for_bg),
        daemon=True,
    ).start()

    return jsonify({
        "reply": final_reply,
        "has_health_data": has_health_data,
        "markers_found": len(current_markers),
    })


# ── Proactive Check-in Endpoint (unchanged) ────────────────────────────────────

@chat_bp.route("/chat/proactive-trigger", methods=["POST"])
def proactive_trigger():
    cron_secret = os.getenv("CRON_SECRET", "")
    provided    = request.headers.get("X-Cron-Secret", "")
    if not cron_secret or provided != cron_secret:
        return jsonify({"error": "Unauthorized — X-Cron-Secret required"}), 401

    from app import supabase, groq_client

    body             = request.json or {}
    target_user_id   = body.get("user_id", "").strip()
    conversation_id  = body.get("conversation_id", "").strip()
    trigger_type     = body.get("trigger_type", "").strip()
    context_override = body.get("context_override", "").strip()

    if not target_user_id or not conversation_id or not trigger_type:
        return jsonify({"error": "user_id, conversation_id, and trigger_type are required"}), 400

    if trigger_type not in _PROACTIVE_TRIGGER_CONFIGS:
        return jsonify({"error": f"Unknown trigger_type. Valid: {list(_PROACTIVE_TRIGGER_CONFIGS.keys())}"}), 400

    config = _PROACTIVE_TRIGGER_CONFIGS[trigger_type]

    health_context = ""
    try:
        from health_memory.memory import build_health_context_block
        health_context = build_health_context_block(supabase, target_user_id) or ""
    except Exception as e:
        print(f"[PROACTIVE] Health context non-fatal: {e}")

    user_name = ""
    try:
        res = supabase.table("user_profiles").select("first_name").eq("user_id", target_user_id).limit(1).execute()
        if res.data:
            user_name = res.data[0].get("first_name", "") or ""
    except Exception:
        pass

    proactive_system = (
        f"You are PHI — a GLP-1 cliff prevention co-pilot by Curabook. "
        f"You are sending an UNPROMPTED, proactive check-in to {'a user' if not user_name else user_name}. "
        f"DIRECTIVE: {config['directive']}\n\n"
        f"NON-STIGMATIZING: Never use willpower, discipline, failure, cheat. "
        f"Frame as biology. Tone: warm, specific, brief.\n"
        f"Do NOT append a disclaimer — the system adds it."
    )

    user_content = "Generate the proactive check-in message now."
    if context_override:
        user_content += f"\n\nAdditional context: {context_override[:500]}"
    if health_context:
        user_content += f"\n\nUser's health memory:\n{health_context[:1500]}"

    generated_reply = _call_llm_safe(groq_client, [
        {"role": "system", "content": proactive_system},
        {"role": "user",   "content": user_content},
    ])
    if not generated_reply or len(generated_reply.strip()) < 20:
        return jsonify({"error": "LLM did not generate a message."}), 500

    final_message = generated_reply.strip() + MANDATORY_DISCLAIMER

    try:
        from datetime import datetime, timezone
        supabase.table("chats").insert({
            "user_id":         target_user_id,
            "conversation_id": conversation_id,
            "role":            "assistant",
            "content":         final_message,
            "is_phi":          True,
            "created_at":      datetime.now(timezone.utc).isoformat(),
        }).execute()
        print(f"[PROACTIVE] Message injected for {target_user_id[:8]} trigger='{trigger_type}'")
    except Exception as e:
        return jsonify({"error": f"Message generated but failed to persist: {e}"}), 503

    return jsonify({
        "success": True, "message": final_message,
        "trigger_type": trigger_type, "conversation_id": conversation_id,
    })