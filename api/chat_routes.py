import re
import os
import traceback
import unicodedata
import json
import threading
import uuid
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
    "food noise", "can't stop thinking about food", "hunger is back", "cravings are back",
    "always hungry", "relentless hunger", "food obsession", "food thoughts",
    "thinking about food", "craving everything", "urge to eat", "hunger returned",
    "binge", "can't resist", "appetite is back"
]
_TAPER_SIGNALS = [
    "stopped", "off meds", "stopped wegovy", "stopped ozempic", "stopped zepbound",
    "stopped mounjaro", "tapering", "reducing dose", "came off", "insurance denied", "can't afford"
]

def _fast_cliff_context(user_message: str) -> str:
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

def _build_context(supabase, user_id: str, user_message: str = "") -> tuple[str, bool]:
    stored_block = ""
    has_data = False
    try:
        from health_memory.memory import build_health_context_block
        stored_block = build_health_context_block(supabase, user_id) or ""
        has_data = bool(stored_block.strip())
    except Exception:
        pass

    rag_block = ""
    if not has_data and user_message.strip():
        try:
            from health_memory.rag import rag_search
            rag_block = rag_search(supabase, user_message, user_id, top_k=3, threshold=0.65) or ""
            if rag_block:
                has_data = True
        except Exception:
            pass

    if not has_data:
        return "", False

    parts = [p for p in [stored_block, rag_block] if p and p.strip()]
    
    header = (
        "╔══════════════════════════════════════════╗\n"
        "║  PHI HEALTH MEMORY — GLP-1 CLIFF ACTIVE  ║\n"
        "╚══════════════════════════════════════════╝\n"
        "RULES: Cite specific values. Never invent numbers.\n"
        "TONE: Deeply empathetic. Validate food noise as biology, not willpower failure.\n"
        "If marker missing: 'I don't have that data yet.'\n\n"
    )
    return header + "\n\n".join(parts), True

def _build_messages_safe(supabase, user_id, conversation_id, enriched_message, has_documents, health_context):
    try:
        from ai.system_prompt_v2 import build_phi_messages
        return build_phi_messages(
            supabase=supabase, user_id=user_id,
            conversation_id=conversation_id, user_message=enriched_message,
            has_documents=has_documents, health_context=health_context
        )
    except Exception:
        pass

    try:
        from ai.chat import build_chat_messages
        return build_chat_messages(
            supabase=supabase, user_id=user_id,
            conversation_id=conversation_id, user_message=enriched_message,
            has_documents=has_documents, health_context=health_context
        )
    except Exception:
        pass

    system = "You are PHI, a deeply empathetic GLP-1 cliff prevention co-pilot by Curabook."
    if health_context:
        system += f"\n\nHEALTH CONTEXT:\n{health_context[:3000]}"
    messages = [{"role": "system", "content": system}]
    try:
        res = (supabase.table("chats").select("role,content")
               .eq("conversation_id", conversation_id).eq("user_id", user_id)
               .order("created_at", desc=True).limit(8).execute())
        for row in reversed(res.data or []):
            if row.get("role") in ("user", "assistant") and row.get("content"):
                messages.append({"role": row["role"], "content": str(row["content"])[:1000]})
    except Exception:
        pass
    messages.append({"role": "user", "content": enriched_message})
    return messages

def _call_llm_safe(messages: list) -> str:
    if not messages:
        return "I couldn't process that request. Please try again."
    
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key, timeout=60.0)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.35,
                max_tokens=1200
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            print(f"[LLM ERROR] {e}")
            return f"⚠️ **OpenAI Error:** {str(e)}\n\n*(Check your API Key and billing status)*"

    return "⚠️ I'm having trouble connecting to my AI engine. Please check your OPENAI_API_KEY."

# ─── FIXED: Smart behavioral extraction from combined user + doc text ──────────
# Bug was: _extract_and_log_metrics(user_message, user_id, supabase) — wrong arg order
# was being called as _extract_and_log_metrics(user_message, user_id, supabase) in bg ops

def _extract_and_log_metrics(user_message: str, document_text: str, user_id: str, supabase):
    """
    SMART: Only extract if user is explicitly reporting today's behavioral data.
    Don't extract from questions, medical reports, or historical context.
    """
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        return

    # Combined text for extraction
    combined_text = f"User Message: {user_message}\nDocument/Image Text: {document_text}"
    lower = combined_text.lower()

    # Quick check — only proceed if behavioral keywords present AND it's a report/statement
    behavioral_kw = ["protein", "step", "sleep", "slept", "walk", "ate", "gram", "hr", "hour"]
    report_kw = ["today", "this morning", "just had", "i ate", "i slept", "screenshot", "routine", "logged"]
    
    has_behavioral = any(kw in lower for kw in behavioral_kw)
    has_report_context = any(kw in lower for kw in report_kw) or document_text.strip()
    
    if not (has_behavioral and has_report_context):
        return

    prompt = """
Analyze this message and/or image text. Extract ONLY daily routine metrics being REPORTED FOR TODAY:
- 'protein' (grams consumed today)
- 'steps' (steps walked today)  
- 'sleep' (hours slept last night)

RULES:
- Only extract if the user is actively reporting/logging their data
- Do NOT extract from questions ("how much protein should I eat?")
- Do NOT extract from historical/goal data ("my goal is 90g protein")
- Do NOT extract from medical lab reports (those are lab markers, not behavioral logs)
- If unsure, return []

Return ONLY a strict JSON array: [{"metric_name": "protein"|"steps"|"sleep", "value": number, "unit": string}]
If no today-specific behavioral metrics found, return [].
"""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=openai_key, timeout=10.0)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": combined_text[:2000]}
            ],
            temperature=0.0,
            max_tokens=150
        )
        raw = resp.choices[0].message.content.strip()
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not match:
            return
        
        metrics = json.loads(match.group(0))
        if not metrics:
            return
            
        date_str = datetime.now().strftime("%Y-%m-%d")
        for m in metrics:
            name = m.get("metric_name")
            val  = m.get("value")
            unit = m.get("unit", "")
            if name in ["protein", "steps", "sleep"] and isinstance(val, (int, float)):
                try:
                    supabase.table("behavioral_logs").insert({
                        "user_id":     user_id,
                        "date":        date_str,
                        "metric_name": name,
                        "value":       float(val),
                        "unit":        unit
                    }).execute()
                    print(f"[METRICS] Logged {name}: {val} for {user_id[:8]}")
                except Exception as e:
                    print(f"[METRICS] Log error: {e}")
    except Exception as e:
        print(f"[METRICS ERROR] {e}")

# ─── SMART MEMORY: Context-aware, avoid noise ─────────────────────────────────

# Patterns indicating AI learned a permanent fact about the user
_AI_LEARNED_SIGNALS = [
    r"i'?ve (noted|recorded|saved|stored|updated|logged)",
    r"(noted|stored|saved|recorded) (that|your|this)",
    r"added to your (health memory|profile|records)",
    r"i'?ll (remember|keep in mind|note)",
]
_AI_LEARNED_RE = re.compile("|".join(_AI_LEARNED_SIGNALS), re.I)

# Context that means user revealed something permanent about themselves
_PERMANENT_FACT_KW = [
    "stopped", "started", "diagnosed", "taking", "my medication", "my condition",
    "i have", "i was told", "my doctor said", "my goal", "i'm trying",
    "insurance denied", "prior auth", "goal weight", "goal is",
    "i weigh", "my weight", "pounds", "kg", "bmi",
]

def _should_extract_memory(user_message: str, ai_reply: str) -> bool:
    """Only extract memory when there's strong signal user revealed permanent info."""
    lower_user = user_message.lower()
    lower_ai   = ai_reply.lower()
    
    # Must have either: AI confirmed it learned something, OR user stated a permanent fact
    ai_learned = bool(_AI_LEARNED_RE.search(lower_ai))
    user_stated_fact = any(kw in lower_user for kw in _PERMANENT_FACT_KW)
    
    return ai_learned or user_stated_fact

def _extract_facts_quick(user_message: str, ai_reply: str) -> list[str]:
    """
    Smart memory extraction — only when user revealed something permanent.
    Quality over quantity: 0 facts is better than 10 wrong ones.
    """
    # Gate: only extract if there's real signal
    if not _should_extract_memory(user_message, ai_reply):
        return []

    lower = user_message.lower()
    facts = []

    # High-confidence rule-based extraction first
    for med in ["zepbound", "wegovy", "ozempic", "mounjaro", "tirzepatide", "semaglutide"]:
        if med in lower and any(kw in lower for kw in ["stopped", "off", "discontinued", "ended"]):
            facts.append(f"User stopped {med.title()} (self-reported)")
            break
        if med in lower and any(kw in lower for kw in ["taking", "started", "on ", "using"]):
            facts.append(f"User is taking {med.title()} (self-reported)")
            break

    # Goal weight — high confidence regex
    gw_match = re.search(r'\b(\d{2,3})\s*(?:lbs?|pounds?)\b.*(?:goal|target|want to)', lower)
    gw_match2 = re.search(r'goal.*?\b(\d{2,3})\s*(?:lbs?|pounds?)\b', lower)
    gw = gw_match or gw_match2
    if gw:
        facts.append(f"User's goal weight is {gw.group(1)} lbs")

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key and len(user_message) > 20:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key, timeout=8.0)
            prompt = (
                "Extract 0-2 PERMANENT health facts the USER revealed about themselves.\n"
                "PERMANENT = ongoing conditions, medications, diagnoses, established goals, chronic symptoms.\n"
                "NOT PERMANENT = questions, temporary feelings, one-time events, today's food/steps.\n"
                "EXAMPLES of permanent: 'User has Type 2 diabetes', 'User stopped Wegovy 3 weeks ago', "
                "'User's goal weight is 158 lbs', 'User has history of high cholesterol'.\n"
                "EXAMPLES of NOT permanent: 'User ate 90g protein today', 'User asked about sleep', 'User feels tired'.\n"
                "Return ONLY a strict JSON array of strings. Empty [] if no permanent facts."
            )
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"User said: {user_message[:600]}"}
                ],
                temperature=0.0,
                max_tokens=120,
            )
            raw = resp.choices[0].message.content.strip()
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    facts.extend([str(f)[:200] for f in parsed if isinstance(f, str) and len(f) > 8])
        except Exception as e:
            print(f"[MEMORY] Extraction error: {e}")

    # Deduplicate and cap
    return list(set(facts))[:3]

def _extract_and_store_doc_markers(supabase, user_id, doc_text):
    try:
        from health_memory.extractor import extract_health_markers
        from health_memory.memory import store_health_markers
        from services.unit_normalizer import force_us_units_batch
        markers = extract_health_markers(doc_text[:8000], "chat_upload")
        if markers:
            markers = force_us_units_batch(markers)
            store_health_markers(supabase, user_id, markers)
            print(f"[CHAT] Stored {len(markers)} markers from doc upload for {user_id[:8]}")
    except Exception as e:
        print(f"[CHAT] Doc marker extract error: {e}")

def _run_background_ops(supabase, user_id, conversation_id, user_message, ai_reply, doc_text_for_extraction):
    """
    Background operations — runs after response is sent to user.
    Order matters: save chat → extract markers → extract facts → log metrics.
    """
    # 1. Save chat turn
    try:
        from ai.chat import save_chat_turn
        save_chat_turn(supabase, user_id, conversation_id, user_message, ai_reply)
    except Exception as e:
        print(f"[BG] Chat save error: {e}")

    # 2. Extract and store document markers (if doc was uploaded)
    if doc_text_for_extraction:
        try:
            _extract_and_store_doc_markers(supabase, user_id, doc_text_for_extraction)
        except Exception:
            pass

    # 3. Smart memory extraction — only permanent facts
    facts = []
    try:
        facts = _extract_facts_quick(user_message, ai_reply)
    except Exception as e:
        print(f"[BG] Fact extract error: {e}")

    if facts:
        try:
            from health_memory.memory import save_conversation_memory
            saved = save_conversation_memory(supabase, user_id, facts, conversation_id)
            print(f"[BG] Saved {saved} memory facts for {user_id[:8]}")
        except Exception as e:
            print(f"[BG] Memory save error: {e}")

    # 4. FIXED: Correct argument order — (user_message, document_text, user_id, supabase)
    # Only attempt if user is reporting behavioral data (not just asking questions)
    doc_text_for_metrics = doc_text_for_extraction or ""
    try:
        _extract_and_log_metrics(user_message, doc_text_for_metrics, user_id, supabase)
    except Exception as e:
        print(f"[BG] Metrics error: {e}")


@chat_bp.route("/chat", methods=["POST"])
def chat():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

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
            if raw:
                current_markers = _sort_by_priority(force_us_units_batch(raw))
        except Exception:
            pass

    health_context, has_health_data = _build_context(supabase, user.id, message)

    if current_markers and is_fresh_document:
        lines = ["📋 UPLOADED REPORT (this session):"]
        for m in current_markers:
            lines.append(f"  • {m.get('marker', m.get('marker_name', ''))}: {m.get('value', '')} {m.get('unit', '')} [{str(m.get('status', 'UNKNOWN')).upper()}]")
        health_context = "\n".join(lines) + "\n\n" + health_context
        has_health_data = True

    cliff_signal = _fast_cliff_context(message)
    if cliff_signal:
        health_context = cliff_signal + "\n\n" + health_context

    enriched_message = message
    if is_fresh_document and document_text.strip():
        enriched_message = (
            f"The patient shared an image/document:\n"
            f"[DOCUMENT_START]\n{document_text[:10000]}\n[DOCUMENT_END]\n\n"
            f"User Message: {message}\n"
            f"Acknowledge the image and answer the user directly."
        )

    messages = _build_messages_safe(
        supabase, user.id, conversation_id, enriched_message,
        has_documents or bool(document_text), health_context
    )
    reply = _call_llm_safe(messages)

    try:
        from ai.system_prompt_v2 import validate_response, detect_hallucination_risk
        if detect_hallucination_risk(reply, has_health_data):
            reply = (
                "I want to give accurate information, but I don't have health data stored "
                "for you yet. Tap the 📎 button to upload a lab report."
            )
        else:
            reply, _ = validate_response(reply, has_health_data)
    except Exception:
        pass

    final_reply = reply + MANDATORY_DISCLAIMER

    # Only send doc to background if it wasn't already processed into markers
    doc_for_bg = document_text if (is_fresh_document and not current_markers) else None

    bg_thread = threading.Thread(
        target=_run_background_ops,
        args=(supabase, user.id, conversation_id, message, final_reply, doc_for_bg)
    )
    bg_thread.daemon = True
    bg_thread.start()

    return jsonify({
        "reply": final_reply,
        "has_health_data": has_health_data,
        "markers_found": len(current_markers)
    })


@chat_bp.route("/conversation/create", methods=["POST"])
def create_conversation():
    from app import supabase
    from services.auth import get_authenticated_user
    
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    title = data.get("title", "New Conversation")
    new_conv_id = str(uuid.uuid4())
    
    try:
        supabase.table("conversations").insert({
            "id": new_conv_id,
            "user_id": user.id,
            "title": title
        }).execute()
        return jsonify({"conversation_id": new_conv_id})
    except Exception as e:
        print(f"[CREATE CONV WARNING] {e}")
        return jsonify({"conversation_id": new_conv_id})


@chat_bp.route("/history", methods=["POST"])
def get_history():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        res = supabase.table("conversations").select("id,title,created_at").eq("user_id", user.id).order("created_at", desc=True).execute()
        return jsonify(res.data or [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@chat_bp.route("/conversation", methods=["POST"])
def get_conversation():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    conv_id = data.get("conversation_id")
    if not conv_id:
        return jsonify({"error": "Missing conversation_id"}), 400

    try:
        res = supabase.table("chats").select("role,content,created_at").eq("conversation_id", conv_id).eq("user_id", user.id).order("created_at", desc=False).execute()
        return jsonify(res.data or [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@chat_bp.route("/rename", methods=["POST"])
def rename_conversation():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    conv_id = data.get("conversation_id")
    title = data.get("title")
    if not conv_id or not title:
        return jsonify({"error": "Missing parameters"}), 400

    try:
        supabase.table("conversations").update({"title": title[:50]}).eq("id", conv_id).eq("user_id", user.id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@chat_bp.route("/delete", methods=["POST"])
def delete_conversation():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    conv_id = (request.json or {}).get("conversation_id")
    if not conv_id:
        return jsonify({"error": "Missing conversation_id"}), 400

    try:
        try:
            supabase.table("conversation_memories").delete().eq("source_conversation", conv_id).execute()
        except Exception:
            pass
        supabase.table("conversations").delete().eq("id", conv_id).eq("user_id", user.id).execute()
        return jsonify({"success": True})
    except Exception as e:
        print(f"[DELETE ERROR] {e}")
        return jsonify({"error": str(e)}), 500