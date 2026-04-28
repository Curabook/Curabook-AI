import re
import os
import unicodedata
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify

chat_bp = Blueprint("chat", __name__)

MAX_MESSAGE_LEN  = 2000
MAX_DOC_TEXT_LEN = 20_000

MANDATORY_DISCLAIMER = (
    "\n\n---\n"
    "⚕️ *PHI is an educational wellness tool. It does not provide medical "
    "diagnoses or prescriptions. Always consult your healthcare provider.*"
)

def _sanitize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    return text.strip()[:MAX_MESSAGE_LEN]

def _build_messages_safe(supabase, user_id, conversation_id, enriched_message, health_context):
    system = "You are PHI, a GLP-1 cliff prevention co-pilot by Curabook. Specialize in preventing metabolic rebound after GLP-1 therapy. Keep responses concise."
    if health_context: system += f"\n\nHEALTH CONTEXT:\n{health_context[:3000]}"

    messages = [{"role": "system", "content": system}]
    try:
        res = (supabase.table("chats").select("role,content").eq("conversation_id", conversation_id).eq("user_id", user_id).order("created_at", desc=True).limit(6).execute())
        for row in reversed(res.data or []):
            if row.get("role") in ("user", "assistant") and row.get("content"):
                messages.append({"role": row["role"], "content": str(row["content"])[:800]})
    except Exception: pass

    messages.append({"role": "user", "content": enriched_message})
    return messages

def _call_llm_safe(messages: list) -> str:
    if not messages: return "I couldn't process that request."
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            from openai import OpenAI
            # 8 Second timeout ensures Vercel never crashes
            client = OpenAI(api_key=openai_key, timeout=8.0) 
            resp = client.chat.completions.create(
                model="gpt-4o-mini", 
                messages=messages, 
                temperature=0.35, 
                max_tokens=800
            )
            return resp.choices[0].message.content.strip()
        except Exception as e: 
            return f"⚠️ **Connection Error:** {str(e)}"
    return "⚠️ Please check your OPENAI_API_KEY."

# FAST REGEX EXTRACTOR - ZERO API CALLS!
def _extract_and_log_metrics_fast(user_message: str, user_id: str, supabase):
    lower = user_message.lower()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # Extract Protein (e.g. "120g protein", "protein 120")
    protein_match = re.search(r'(\d{2,3})\s*(?:g|grams?)\s*(?:of\s*)?protein|protein\s*(?::\s*)?(\d{2,3})', lower)
    if protein_match:
        val = protein_match.group(1) or protein_match.group(2)
        supabase.table("behavioral_logs").insert({"user_id": user_id, "date": date_str, "metric_name": "protein", "value": float(val), "unit": "g"}).execute()

    # Extract Steps (e.g. "5000 steps")
    steps_match = re.search(r'(\d{1,2}(?:,\d{3})|\d{3,5})\s*steps', lower)
    if steps_match:
        val = steps_match.group(1).replace(",", "")
        supabase.table("behavioral_logs").insert({"user_id": user_id, "date": date_str, "metric_name": "steps", "value": float(val), "unit": "steps"}).execute()

    # Extract Sleep (e.g. "7.5 hours sleep", "slept 8 hrs")
    sleep_match = re.search(r'(\d{1,2}(?:\.\d)?)\s*(?:hours?|hrs?)\s*(?:of\s*)?sleep|slept\s*(?:for\s*)?(\d{1,2}(?:\.\d)?)', lower)
    if sleep_match:
        val = sleep_match.group(1) or sleep_match.group(2)
        supabase.table("behavioral_logs").insert({"user_id": user_id, "date": date_str, "metric_name": "sleep", "value": float(val), "unit": "hours"}).execute()

def _run_background_ops_fast(supabase, user_id, conversation_id, user_message, ai_reply):
    try:
        supabase.table("chats").insert([
            {"user_id": user_id, "conversation_id": conversation_id, "role": "user", "content": user_message},
            {"user_id": user_id, "conversation_id": conversation_id, "role": "assistant", "content": ai_reply}
        ]).execute()
    except Exception: pass

    try: _extract_and_log_metrics_fast(user_message, user_id, supabase)
    except Exception: pass

@chat_bp.route("/chat", methods=["POST"])
def chat():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user: return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    message = _sanitize(data.get("message", ""))
    conversation_id = data.get("conversation_id", "")
    
    if not message or not conversation_id:
        return jsonify({"error": "Missing required fields"}), 400

    # Grab basic health context if available (No slow RAG searches)
    health_context = ""
    try:
        from health_memory.memory import build_health_context_block
        health_context = build_health_context_block(supabase, user.id) or ""
    except Exception: pass

    messages = _build_messages_safe(supabase, user.id, conversation_id, message, health_context)
    reply = _call_llm_safe(messages)
    
    final_reply = reply + MANDATORY_DISCLAIMER
    
    # Fast synchronous save
    _run_background_ops_fast(supabase, user.id, conversation_id, message, final_reply)

    return jsonify({"reply": final_reply, "has_health_data": bool(health_context), "markers_found": 0})

@chat_bp.route("/conversation/create", methods=["POST"])
def create_conversation():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user: return jsonify({"error": "Unauthorized"}), 401
    title = (request.json or {}).get("title", "New Conversation")
    try:
        res = supabase.table("conversations").insert({"user_id": user.id, "title": title}).execute()
        if res.data: return jsonify({"conversation_id": res.data[0]["id"]})
        return jsonify({"error": "Failed to create conversation"}), 500
    except Exception as e: return jsonify({"error": str(e)}), 500

@chat_bp.route("/history", methods=["POST"])
def get_history():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user: return jsonify({"error": "Unauthorized"}), 401
    try:
        res = supabase.table("conversations").select("id,title,created_at").eq("user_id", user.id).order("created_at", desc=True).execute()
        return jsonify(res.data or [])
    except Exception as e: return jsonify({"error": str(e)}), 500

@chat_bp.route("/conversation", methods=["POST"])
def get_conversation():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user: return jsonify({"error": "Unauthorized"}), 401
    conv_id = (request.json or {}).get("conversation_id")
    if not conv_id: return jsonify({"error": "Missing conversation_id"}), 400
    try:
        res = supabase.table("chats").select("role,content,created_at").eq("conversation_id", conv_id).eq("user_id", user.id).order("created_at", desc=False).execute()
        return jsonify(res.data or [])
    except Exception as e: return jsonify({"error": str(e)}), 500

@chat_bp.route("/delete", methods=["POST"])
def delete_conversation():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user: return jsonify({"error": "Unauthorized"}), 401
    conv_id = (request.json or {}).get("conversation_id")
    if not conv_id: return jsonify({"error": "Missing conversation_id"}), 400
    try:
        supabase.table("conversations").delete().eq("id", conv_id).eq("user_id", user.id).execute()
        return jsonify({"success": True})
    except Exception as e: return jsonify({"error": str(e)}), 500