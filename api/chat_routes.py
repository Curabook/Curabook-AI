"""
api/chat_routes.py — FIXED VERSION
Integrates FIX_2 (memory) + FIX_3 (smart shield)
"""
import re
import os
import traceback
import unicodedata
import json
import threading
import uuid
from datetime import datetime, date
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
                model="gpt-4o-mini", messages=messages, temperature=0.35, max_tokens=1200
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            print(f"[LLM ERROR] {e}")
            return f"⚠️ **OpenAI Error:** {str(e)}\n\n*(Check your API Key and billing status)*"
    return "⚠️ I'm having trouble connecting to my AI engine. Please check your OPENAI_API_KEY."


# ════════════════════════════════════════════════════════════════
# FIX_3: SMART SHIELD — behavioral parsing from messages
# ════════════════════════════════════════════════════════════════

def _parse_behavioral_from_message(user_message: str, document_text: str = "") -> dict:
    """Parse behavioral data from user message + document text."""
    combined = f"{user_message}\n{document_text}".lower()
    today = date.today().isoformat()
    metrics = {}

    report_signals = [
        "today", "this morning", "last night", "just had", "i ate",
        "i slept", "i walked", "i did", "logged", "screenshot",
        "my sleep", "my steps", "my protein", "ate", "steps were",
        "slept", "walked", "grams of protein",
    ]
    is_reporting = any(kw in combined for kw in report_signals) or bool(document_text.strip())
    if not is_reporting:
        return {}

    # Protein
    for pattern in [
        r'(\d{2,3})\s*(?:g|grams?)\s+(?:of\s+)?protein',
        r'protein[:\s]+(\d{2,3})\s*(?:g|grams?)',
        r'protein[:\s=]+(\d{2,3})',
    ]:
        m = re.search(pattern, combined)
        if m:
            val = int(m.group(1))
            if 10 <= val <= 400:
                metrics["protein"] = {"value": val, "unit": "g", "date": today}
                break

    # Steps
    for pattern in [
        r'(\d{3,6})\s+steps',
        r'steps[:\s]+(\d{3,6})',
        r'walked\s+(\d{3,6})',
    ]:
        m = re.search(pattern, combined)
        if m:
            val = int(m.group(1))
            if 0 <= val <= 100000:
                metrics["steps"] = {"value": val, "unit": "steps", "date": today}
                break

    # Sleep
    for pattern in [
        r'slept?\s+(\d+(?:\.\d)?)\s*(?:h(?:ours?)?|hrs?)',
        r'sleep[:\s]+(\d+(?:\.\d)?)\s*(?:h(?:ours?)?|hrs?)',
        r'(\d+(?:\.\d)?)\s*(?:h(?:ours?)?|hrs?)\s+(?:of\s+)?sleep',
    ]:
        m = re.search(pattern, combined)
        if m:
            val = float(m.group(1))
            if 0 <= val <= 14:
                metrics["sleep"] = {"value": round(val, 1), "unit": "hours", "date": today}
                break

    return metrics


def _store_behavioral_metrics(supabase, user_id: str, metrics: dict) -> dict:
    """Store parsed behavioral metrics to behavioral_logs, return what was stored."""
    if not metrics:
        return {}
    stored = {}
    for metric_name, data in metrics.items():
        try:
            supabase.table("behavioral_logs").insert({
                "user_id":     user_id,
                "date":        data["date"],
                "metric_name": metric_name,
                "value":       float(data["value"]),
                "unit":        data["unit"],
                "created_at":  datetime.now().isoformat(),
            }).execute()
            stored[metric_name] = data["value"]
            print(f"[SMART-SHIELD] {metric_name}: {data['value']} for {user_id[:8]}")
        except Exception as e:
            print(f"[SMART-SHIELD] Store error {metric_name}: {e}")
    return stored


# ════════════════════════════════════════════════════════════════
# FIX_2: MEMORY — improved extraction functions
# ════════════════════════════════════════════════════════════════

_HEALTH_FACT_KEYWORDS = [
    "stopped", "started", "taking", "wegovy", "zepbound", "ozempic", "mounjaro",
    "tirzepatide", "semaglutide", "metformin", "insulin", "glp-1", "glp1",
    "goal weight", "goal is", "i weigh", "my weight", "pounds", "lbs",
    "i have", "diagnosed", "diabetes", "prediabetes", "cholesterol",
    "doctor said", "my doctor", "insurance", "denied", "prior auth",
    "food noise", "hungry", "craving", "tired", "fatigue",
]

_TRIVIAL_PATTERNS = [
    r"^(hi|hello|hey|thanks|thank you|ok|okay|got it|sounds good)[\s!.?]*$",
    r"^(yes|no|sure|please|maybe)[\s!.?]*$",
    r"^\?+$",
]

def _should_extract_memory(user_message: str, ai_reply: str) -> bool:
    lower_user = user_message.lower().strip()
    lower_ai   = ai_reply.lower()
    for pattern in _TRIVIAL_PATTERNS:
        if re.match(pattern, lower_user, re.IGNORECASE):
            return False
    if len(lower_user) < 15 and not any(kw in lower_user for kw in _HEALTH_FACT_KEYWORDS):
        return False
    if any(kw in lower_user for kw in _HEALTH_FACT_KEYWORDS):
        return True
    ai_health_signals = [
        "protein target", "muscle defense", "glp-1", "wegovy", "zepbound",
        "ozempic", "your goal weight", "cliff alert", "rebound",
        "i've noted", "i've stored", "i'll remember",
    ]
    return any(sig in lower_ai for sig in ai_health_signals)

def _extract_facts_quick(user_message: str, ai_reply: str) -> list[str]:
    if not _should_extract_memory(user_message, ai_reply):
        return []

    lower = user_message.lower()
    facts = []

    # GLP-1 medication status
    for med in ["zepbound", "wegovy", "ozempic", "mounjaro", "tirzepatide", "semaglutide"]:
        if med in lower:
            if any(kw in lower for kw in ["stopped", "off", "discontinued", "ended", "quit", "coming off"]):
                facts.append(f"User stopped {med.title()} (self-reported)")
                break
            elif any(kw in lower for kw in ["taking", "started", "on ", "using", "injecting", "dose"]):
                facts.append(f"User is taking {med.title()} (self-reported)")
                break
            elif any(kw in lower for kw in ["tapering", "reducing", "every other week", "microdose"]):
                facts.append(f"User is tapering {med.title()} (self-reported)")
                break

    # Goal weight
    for pattern in [
        r'goal\s+weight\s+(?:is\s+)?(\d{2,3})\s*(?:lbs?|pounds?)',
        r'(\d{2,3})\s*(?:lbs?|pounds?)\s+(?:is\s+)?(?:my\s+)?(?:goal|target)',
        r'want\s+to\s+(?:be|weigh|get\s+to)\s+(\d{2,3})\s*(?:lbs?|pounds?)',
    ]:
        m = re.search(pattern, lower)
        if m:
            gw = int(m.group(1))
            if 80 <= gw <= 400:
                facts.append(f"User's goal weight is {gw} lbs (protein target: {round(gw * 0.545, 1)}g/day)")
                break

    # Insurance denial
    if any(kw in lower for kw in ["insurance denied", "prior auth denied", "pa denied"]):
        for med in ["zepbound", "wegovy", "ozempic", "mounjaro", "glp-1"]:
            if med in lower:
                facts.append(f"User's insurance denied prior authorization for {med.title()}")
                break
        else:
            facts.append("User's insurance denied prior authorization for GLP-1 medication")

    # LLM extraction for nuanced facts
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key and len(user_message) > 20:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key, timeout=8.0)
            prompt = (
                "Extract 0-3 PERMANENT health facts the USER revealed.\n"
                "PERMANENT = ongoing conditions, medications, stopped meds, diagnoses, "
                "health goals, insurance status for medications.\n"
                "NOT PERMANENT = questions, temporary feelings, today's food/steps/sleep.\n"
                "Return ONLY a JSON array of strings. No markdown, no extra text.\n"
                "Empty [] if no permanent facts."
            )
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"User said: {user_message[:600]}"}
                ],
                temperature=0.0, max_tokens=150,
            )
            raw = (resp.choices[0].message.content or "").strip()
            # FIX-MEM-2: Robust JSON parsing
            raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
            raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)
            match = re.search(r'\[.*\]', raw.strip(), re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    facts.extend([str(f)[:200] for f in parsed if isinstance(f, str) and len(f) > 8])
        except Exception as e:
            print(f"[MEMORY] Extraction error: {e}")

    seen = set()
    deduped = []
    for f in facts:
        key = f.lower()[:50]
        if key not in seen:
            seen.add(key)
            deduped.append(f)
    return deduped[:3]


def _extract_and_log_metrics(user_message: str, document_text: str, user_id: str, supabase):
    """Smart behavioral extraction — only logs when user is reporting today's data."""
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        return
    combined_text = f"User Message: {user_message}\nDocument/Image Text: {document_text}"
    lower = combined_text.lower()
    behavioral_kw = ["protein", "step", "sleep", "slept", "walk", "ate", "gram", "hr", "hour"]
    report_kw = ["today", "this morning", "just had", "i ate", "i slept", "screenshot", "routine", "logged"]
    has_behavioral = any(kw in lower for kw in behavioral_kw)
    has_report_context = any(kw in lower for kw in report_kw) or document_text.strip()
    if not (has_behavioral and has_report_context):
        return
    prompt = """Analyze this. Extract ONLY daily routine metrics being REPORTED FOR TODAY:
- 'protein' (grams today), 'steps' (steps today), 'sleep' (hours last night)
Only extract if user is actively reporting/logging. NOT from questions or goals.
Return ONLY a strict JSON array: [{"metric_name": "protein"|"steps"|"sleep", "value": number, "unit": string}]
Empty [] if nothing found."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=openai_key, timeout=10.0)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt},
                      {"role": "user", "content": combined_text[:2000]}],
            temperature=0.0, max_tokens=150
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)
        match = re.search(r'\[.*\]', raw.strip(), re.DOTALL)
        if not match:
            return
        metrics = json.loads(match.group(0))
        if not metrics:
            return
        date_str = date.today().isoformat()
        for m in metrics:
            name = m.get("metric_name")
            val  = m.get("value")
            unit = m.get("unit", "")
            if name in ["protein", "steps", "sleep"] and isinstance(val, (int, float)):
                try:
                    supabase.table("behavioral_logs").insert({
                        "user_id": user_id, "date": date_str,
                        "metric_name": name, "value": float(val), "unit": unit
                    }).execute()
                    print(f"[METRICS] {name}: {val} for {user_id[:8]}")
                except Exception as e:
                    print(f"[METRICS] Log error: {e}")
    except Exception as e:
        print(f"[METRICS ERROR] {e}")


# FIX-MEM-3: Robust 3-tier memory save
def _save_memory_robust(supabase, user_id: str, facts: list[str], conversation_id: str) -> int:
    if not facts:
        return 0
    now = datetime.now().isoformat()
    saved = 0
    for fact in facts:
        fact = fact.strip()
        if not fact or len(fact) < 8:
            continue
        # Tier 1: full insert
        try:
            supabase.table("conversation_memories").insert({
                "user_id": user_id, "fact": fact[:500],
                "source_conversation": conversation_id or None,
                "category": "health", "created_at": now, "is_active": True,
            }).execute()
            saved += 1
            continue
        except Exception as e1:
            pass
        # Tier 2: without FK
        try:
            supabase.table("conversation_memories").insert({
                "user_id": user_id, "fact": fact[:500],
                "category": "health", "created_at": now, "is_active": True,
            }).execute()
            saved += 1
            continue
        except Exception as e2:
            pass
        # Tier 3: minimal
        try:
            supabase.table("conversation_memories").insert({
                "user_id": user_id, "fact": fact[:500],
            }).execute()
            saved += 1
        except Exception as e3:
            print(f"[MEMORY] All tiers failed: {e3}")
    if saved > 0:
        try:
            from health_memory.memory import _invalidate_context_cache
            _invalidate_context_cache(user_id)
        except Exception:
            pass
    return saved


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
    """Background operations — runs after response is sent."""
    # 1. Save chat turn
    try:
        from ai.chat import save_chat_turn
        save_chat_turn(supabase, user_id, conversation_id, user_message, ai_reply)
    except Exception as e:
        print(f"[BG] Chat save error: {e}")

    # 2. Extract and store document markers
    if doc_text_for_extraction:
        try:
            _extract_and_store_doc_markers(supabase, user_id, doc_text_for_extraction)
        except Exception:
            pass

    # 3. Smart memory extraction (FIX_2)
    facts = []
    try:
        facts = _extract_facts_quick(user_message, ai_reply)
    except Exception as e:
        print(f"[BG] Fact extract error: {e}")

    if facts:
        try:
            saved = _save_memory_robust(supabase, user_id, facts, conversation_id)
            print(f"[BG] Memory: {saved} facts saved for {user_id[:8]}")
        except Exception as e:
            print(f"[BG] Memory save error: {e}")

    # 4. Behavioral metrics from LLM
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

    # ── FIX_3: Smart shield parsing from user message ─────────────────────
    shield_update = {}
    try:
        parsed_metrics = _parse_behavioral_from_message(message, document_text)
        if parsed_metrics:
            shield_update = _store_behavioral_metrics(supabase, user.id, parsed_metrics)
    except Exception as e:
        print(f"[SMART-SHIELD] Parse error: {e}")

    # Only send doc to background if not already processed into markers
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
        "markers_found": len(current_markers),
        "shield_update": shield_update,            # FIX_3: direct shield data
        "behavioral_logged": bool(shield_update),  # FIX_3: tells frontend to refresh
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
            "id": new_conv_id, "user_id": user.id, "title": title
        }).execute()
        return jsonify({"conversation_id": new_conv_id})
    except Exception as e:
        print(f"[CREATE CONV] {e}")
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