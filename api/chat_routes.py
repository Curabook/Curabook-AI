import re
import os
import traceback
import unicodedata
import json
from datetime import datetime
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

def _sanitize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    return text.strip()[:MAX_MESSAGE_LEN]

def _sort_by_priority(markers: list) -> list:
    order = {"HIGH": 0, "LOW": 1, "NORMAL": 2, "UNKNOWN": 3}
    return sorted(markers, key=lambda x: order.get(str(x.get("status", "")).upper(), 3))

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
    lower = user_message.lower()
    noise_count = sum(1 for s in _GHRELIN_SIGNALS if s in lower)
    taper_count = sum(1 for s in _TAPER_SIGNALS if s in lower)

    parts = []
    if noise_count >= 2: parts.append("🚨 GHRELIN SURGE ACTIVE: User reporting food noise. APPLY FOOD NOISE PROTOCOL FIRST.")
    elif noise_count == 1: parts.append("⚠ Food noise signal detected. Validate as biology before clinical content.")
    if taper_count >= 1: parts.append("⚠ TAPER CONTEXT: User has stopped or is reducing GLP-1. Apply Maintenance overlay.")

    return "\n".join(parts) if parts else ""

def _build_context(supabase, user_id: str, user_message: str = "") -> tuple[str, bool]:
    stored_block = ""
    has_data = False

    try:
        from health_memory.memory import build_health_context_block
        stored_block = build_health_context_block(supabase, user_id) or ""
        has_data = bool(stored_block.strip())
    except Exception as e:
        print(f"[CHAT] Memory load non-fatal: {e}")

    rag_block = ""
    if not has_data and user_message.strip():
        try:
            from health_memory.rag import rag_search
            rag_block = rag_search(supabase, user_message, user_id, top_k=3, threshold=0.65) or ""
            if rag_block: has_data = True
        except Exception as e:
            print(f"[CHAT] RAG search non-fatal: {e}")

    if not has_data: return "", False

    parts = [p for p in [stored_block, rag_block] if p and p.strip()]
    header = (
        "╔══════════════════════════════════════════╗\n"
        "║  PHI HEALTH MEMORY — GLP-1 CLIFF ACTIVE  ║\n"
        "╚══════════════════════════════════════════╝\n"
        "RULES: Cite specific values. Never invent numbers.\n"
        "If marker missing: 'I don't have that data yet.'\n\n"
    )
    return header + "\n\n".join(parts), True

def _build_messages_safe(supabase, user_id, conversation_id, enriched_message, has_documents, health_context):
    try:
        from ai.system_prompt_v2 import build_phi_messages
        return build_phi_messages(supabase=supabase, user_id=user_id, conversation_id=conversation_id, user_message=enriched_message, has_documents=has_documents, health_context=health_context)
    except Exception as e:
        print(f"[CHAT] build_phi_messages failed, using fallback: {e}")

    try:
        from ai.chat import build_chat_messages
        return build_chat_messages(supabase=supabase, user_id=user_id, conversation_id=conversation_id, user_message=enriched_message, has_documents=has_documents, health_context=health_context)
    except Exception: pass

    system = "You are PHI, a GLP-1 cliff prevention co-pilot by Curabook. Specialize in preventing metabolic rebound after GLP-1 therapy."
    if health_context: system += f"\n\nHEALTH CONTEXT:\n{health_context[:3000]}"

    messages = [{"role": "system", "content": system}]
    try:
        res = (supabase.table("chats").select("role,content").eq("conversation_id", conversation_id).eq("user_id", user_id).order("created_at", desc=True).limit(8).execute())
        for row in reversed(res.data or []):
            if row.get("role") in ("user", "assistant") and row.get("content"):
                messages.append({"role": row["role"], "content": str(row["content"])[:1000]})
    except Exception: pass

    messages.append({"role": "user", "content": enriched_message})
    return messages

def _call_llm_safe(messages: list) -> str:
    if not messages: return "I couldn't process that request. Please try again."
    
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            from openai import OpenAI
            # FIX: 15s timeout prevents Vercel from hanging infinitely.
            client = OpenAI(api_key=openai_key, timeout=15.0)
            resp = client.chat.completions.create(
                model="gpt-4o-mini", 
                messages=messages, 
                temperature=0.35, 
                max_tokens=1200
            )
            return resp.choices[0].message.content.strip()
        except Exception as e: 
            print(f"[LLM ERROR] {e}")
            # FIX: Expose exact OpenAI error to the user so you know if your billing/key is stuck
            return f"⚠️ **OpenAI Error:** {str(e)}\n\n*(If you just added credits, you must generate a NEW API key in the OpenAI dashboard and add it to Vercel for it to work!)*"

    return "⚠️ I'm having trouble connecting to my AI engine. Please check your OPENAI_API_KEY."

def _extract_and_log_metrics(user_message: str, user_id: str, supabase):
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key: return
    
    lower = user_message.lower()
    if not any(kw in lower for kw in ["protein", "step", "sleep", "slept", "walk", "ate", "gram", "hr", "hour"]):
        return

    prompt = """
    The user is reporting daily health metrics in a chat. Extract quantifiable data for 'protein' (grams), 'steps' (count), or 'sleep' (hours).
    Return ONLY a strict JSON array of objects with exactly these keys: "metric_name" (must be 'protein', 'steps', or 'sleep'), "value" (number), "unit" (string).
    If no concrete numbers are reported, return an empty array [].
    """
    try:
        from openai import OpenAI
        client = OpenAI(api_key=openai_key, timeout=5.0)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": user_message[:600]}],
            temperature=0.0, max_tokens=150
        )
        
        raw = resp.choices[0].message.content.strip()
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        
        if match:
            metrics = json.loads(match.group(0))
            date_str = datetime.now().strftime("%Y-%m-%d")
            
            for m in metrics:
                name = m.get("metric_name")
                val = m.get("value")
                unit = m.get("unit", "")
                
                if name in ["protein", "steps", "sleep"] and isinstance(val, (int, float)):
                    supabase.table("behavioral_logs").insert({
                        "user_id": user_id, "date": date_str, "metric_name": name, "value": float(val), "unit": unit
                    }).execute()
    except Exception as e:
        print(f"[ACTION CONNECTION ERROR] Failed to extract metrics: {e}")

def _run_background_ops(supabase, user_id, conversation_id, user_message, ai_reply, doc_text_for_extraction):
    try:
        from ai.chat import save_chat_turn
        save_chat_turn(supabase, user_id, conversation_id, user_message, ai_reply)
    except Exception as e: print(f"[BG] Chat save error: {e}")

    facts = []
    try: facts = _extract_facts_quick(user_message, ai_reply)
    except Exception: pass

    if facts:
        try:
            from health_memory.memory import save_conversation_memory
            save_conversation_memory(supabase, user_id, facts, conversation_id)
        except Exception: pass

    try: _extract_and_log_metrics(user_message, user_id, supabase)
    except Exception: pass

    if doc_text_for_extraction:
        try: _extract_and_store_doc_markers(supabase, user_id, doc_text_for_extraction)
        except Exception: pass

def _extract_facts_quick(user_message: str, ai_reply: str) -> list[str]:
    lower = user_message.lower()
    facts = []
    if any(kw in lower for kw in ["stopped", "off meds", "discontinued"]):
        for med in ["zepbound", "wegovy", "ozempic", "mounjaro", "tirzepatide", "semaglutide"]:
            if med in lower:
                facts.append(f"User stopped {med.title()} (self-reported)")
                break

    if "goal weight" in lower:
        nums = re.findall(r'\b(\d{2,3})\s*(?:lbs?|pounds?)\b', lower)
        if nums: facts.append(f"User's goal weight is {nums[0]} lbs")

    openai_key = os.getenv("OPENAI_API_KEY")
    if not facts and openai_key and len(user_message) > 30:
        try:
            if any(kw in lower for kw in ["protein", "steps", "sleep", "weight", "glucose"]):
                from openai import OpenAI
                client = OpenAI(api_key=openai_key, timeout=5.0)
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "Extract 0-2 health facts. Return ONLY a JSON array of short strings."},
                        {"role": "user", "content": f"Extract: {user_message[:600]}"},
                    ],
                    temperature=0.0, max_tokens=150,
                )
                import json
                parsed = json.loads(resp.choices[0].message.content.strip().strip("```json").strip("```").strip())
                if isinstance(parsed, list): facts.extend([str(f)[:200] for f in parsed])
        except Exception: pass
    return facts[:3]

def _extract_and_store_doc_markers(supabase, user_id, doc_text):
    try:
        from health_memory.extractor import extract_health_markers
        from health_memory.memory import store_health_markers
        from services.unit_normalizer import force_us_units_batch
        markers = extract_health_markers(doc_text[:8000], "chat_upload")
        if markers:
            markers = force_us_units_batch(markers)
            store_health_markers(supabase, user_id, markers)
    except Exception: pass

@chat_bp.route("/chat", methods=["POST"])
def chat():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user: return jsonify({"error": "Unauthorized"}), 401

    try:
        from services.compliance import verify_user_consent
        if not verify_user_consent(supabase, user.id, "ai_processing"):
            supabase.table("user_consents").upsert({"user_id": user.id, "consent_type": "ai_processing", "consent_version": "v2.0", "is_active": True}, on_conflict="user_id,consent_type").execute()
            supabase.table("user_consents").upsert({"user_id": user.id, "consent_type": "data_processing", "consent_version": "v2.0", "is_active": True}, on_conflict="user_id,consent_type").execute()
    except Exception: pass

    data = request.json or {}
    message = _sanitize(data.get("message", ""))
    conversation_id = data.get("conversation_id", "")
    document_text = (data.get("document_text", "") or "")[:MAX_DOC_TEXT_LEN]
    has_documents = bool(data.get("has_documents", False))

    if not message or not conversation_id:
        return jsonify({"error": "Missing required fields"}), 400

    current_markers = []
    is_fresh_document = bool(document_text.strip()) and has_documents

    if is_fresh_document:
        try:
            from health_memory.extractor import extract_health_markers
            from services.unit_normalizer import force_us_units_batch
            raw = extract_health_markers(document_text)
            if raw: current_markers = _sort_by_priority(force_us_units_batch(raw))
        except Exception: pass

    health_context, has_health_data = _build_context(supabase, user.id, message)

    if current_markers and is_fresh_document:
        lines = ["📋 UPLOADED REPORT (this session):"]
        for m in current_markers:
            lines.append(f"  • {m.get('marker', m.get('marker_name', ''))}: {m.get('value', '')} {m.get('unit', '')} [{str(m.get('status', 'UNKNOWN')).upper()}]")
        health_context = "\n".join(lines) + "\n\n" + health_context
        has_health_data = True

    cliff_signal = _fast_cliff_context(message)
    if cliff_signal: health_context = cliff_signal + "\n\n" + health_context

    enriched_message = message
    if is_fresh_document and document_text.strip():
        enriched_message = f"The patient shared a document:\n[DOCUMENT_START]\n{document_text[:10000]}\n[DOCUMENT_END]\n\nQuestion: {message}\nUse exact values and flag cliff signals."

    messages = _build_messages_safe(supabase, user.id, conversation_id, enriched_message, has_documents or bool(document_text), health_context)
    reply = _call_llm_safe(messages)

    try:
        from ai.system_prompt_v2 import validate_response, detect_hallucination_risk
        if detect_hallucination_risk(reply, has_health_data):
            reply = "I want to give accurate information, but I don't have health data stored for you yet. Tap the 📎 button to upload a lab report."
        else:
            reply, _ = validate_response(reply, has_health_data)
    except Exception: pass

    final_reply = reply + MANDATORY_DISCLAIMER
    doc_for_bg = document_text if is_fresh_document and not current_markers else None
    
    # FIX: Run synchronously. Removed threading.Thread so Vercel doesn't deadlock the connection!
    _run_background_ops(supabase, user.id, conversation_id, message, final_reply, doc_for_bg)

    return jsonify({"reply": final_reply, "has_health_data": has_health_data, "markers_found": len(current_markers)})


@chat_bp.route("/chat/proactive-trigger", methods=["POST"])
def proactive_trigger():
    cron_secret = os.getenv("CRON_SECRET", "")
    if not cron_secret or request.headers.get("X-Cron-Secret", "") != cron_secret:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify({"success": True})


@chat_bp.route("/conversation/create", methods=["POST"])
def create_conversation():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user: return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    title = data.get("title", "New Conversation")
    try:
        res = supabase.table("conversations").insert({
            "user_id": user.id,
            "title": title
        }).execute()
        if res.data:
            return jsonify({"conversation_id": res.data[0]["id"]})
        return jsonify({"error": "Failed to create conversation"}), 500
    except Exception as e:
        print(f"[CHAT] Create error: {e}")
        return jsonify({"error": str(e)}), 500


@chat_bp.route("/history", methods=["POST"])
def get_history():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user: return jsonify({"error": "Unauthorized"}), 401

    try:
        res = supabase.table("conversations").select("id,title,created_at").eq("user_id", user.id).order("created_at", desc=True).execute()
        return jsonify(res.data or [])
    except Exception as e:
        print(f"[CHAT] History error: {e}")
        return jsonify({"error": str(e)}), 500


@chat_bp.route("/conversation", methods=["POST"])
def get_conversation():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user: return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    conv_id = data.get("conversation_id")
    if not conv_id: return jsonify({"error": "Missing conversation_id"}), 400

    try:
        res = supabase.table("chats").select("role,content,created_at").eq("conversation_id", conv_id).eq("user_id", user.id).order("created_at", desc=False).execute()
        return jsonify(res.data or [])
    except Exception as e:
        print(f"[CHAT] Fetch conversation error: {e}")
        return jsonify({"error": str(e)}), 500


@chat_bp.route("/rename", methods=["POST"])
def rename_conversation():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user: return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    conv_id = data.get("conversation_id")
    title = data.get("title")
    if not conv_id or not title: return jsonify({"error": "Missing parameters"}), 400

    try:
        supabase.table("conversations").update({"title": title[:50]}).eq("id", conv_id).eq("user_id", user.id).execute()
        return jsonify({"success": True})
    except Exception as e:
        print(f"[CHAT] Rename error: {e}")
        return jsonify({"error": str(e)}), 500


@chat_bp.route("/delete", methods=["POST"])
def delete_conversation():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user: return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    conv_id = data.get("conversation_id")
    if not conv_id: return jsonify({"error": "Missing conversation_id"}), 400

    try:
        supabase.table("conversations").delete().eq("id", conv_id).eq("user_id", user.id).execute()
        return jsonify({"success": True})
    except Exception as e:
        print(f"[CHAT] Delete error: {e}")
        return jsonify({"error": str(e)}), 500