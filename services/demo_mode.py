"""
services/demo_mode.py
─────────────────────────────────────────────────────────
Guest/demo mode for testing PHI without registration.

How it works:
- A demo session is a temporary in-memory user object
- Demo users get full access to chat and document analysis
- Nothing is stored permanently (memory cleared on restart)
- No Supabase writes — no DB required for demo
- Demo users are identified by a session cookie (UUID)
- Backend reads DEMO_MODE=true from .env to enable
"""

import os
import uuid
from datetime import datetime, timezone
from collections import defaultdict

# ── Toggle ────────────────────────────────────────────────────────────────────
def is_demo_mode() -> bool:
    return os.getenv("DEMO_MODE", "false").lower() == "true"

# ── In-memory store for demo sessions ─────────────────────────────────────────
# Structure: { session_id: { "user": DemoUser, "markers": [], "chats": {conv_id: []} } }
_demo_sessions: dict = {}
_demo_chat_history: dict = defaultdict(list)   # conv_id → list of {role, content}

DEMO_SESSION_HEADER = "X-Demo-Session"


class DemoUser:
    """Mimics a Supabase User object so all routes work unchanged."""
    def __init__(self, session_id: str):
        self.id        = "demo-" + session_id
        self.email     = "demo@curabook.ai"
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.app_metadata = {"provider": "demo"}


def get_or_create_demo_session(request) -> "DemoUser | None":
    """
    Extract demo session ID from header or create a new one.
    Returns a DemoUser that works as a drop-in for real Supabase users.
    """
    if not is_demo_mode():
        return None

    session_id = request.headers.get(DEMO_SESSION_HEADER, "")
    if not session_id or session_id not in _demo_sessions:
        session_id = uuid.uuid4().hex[:16]
        _demo_sessions[session_id] = {
            "user":    DemoUser(session_id),
            "markers": [],
            "convs":   {},
        }

    return _demo_sessions[session_id]["user"]


def get_demo_user_id(request) -> str:
    user = get_or_create_demo_session(request)
    return user.id if user else ""


# ── No-op replacements for DB operations in demo mode ────────────────────────

def demo_audit_log(*args, **kwargs):
    pass  # Don't write to DB

def demo_verify_consent(*args, **kwargs) -> bool:
    return True  # Always consented in demo

def demo_build_health_context(supabase, user_id: str) -> str:
    """Return in-memory markers as context block for demo users."""
    sid = user_id.replace("demo-", "")
    session = _demo_sessions.get(sid, {})
    markers = session.get("markers", [])
    if not markers:
        return ""
    lines = ["=== DEMO HEALTH MEMORY (this session only) ==="]
    for m in markers:
        lines.append(f"  {m.get('marker_name','')}: {m.get('value','')} {m.get('unit','')}")
    lines.append("=============================================")
    return "\n".join(lines)

def demo_store_markers(user_id: str, markers: list):
    """Store markers in memory for this demo session."""
    sid = user_id.replace("demo-", "")
    if sid in _demo_sessions:
        _demo_sessions[sid]["markers"].extend(markers)

def demo_get_latest_markers(user_id: str) -> dict:
    sid = user_id.replace("demo-", "")
    session = _demo_sessions.get(sid, {})
    markers = session.get("markers", [])
    latest = {}
    for m in markers:
        name = m.get("marker_name", m.get("marker", ""))
        if name and name not in latest:
            latest[name] = m
    return latest

def demo_save_chat(user_id: str, conv_id: str, user_msg: str, ai_reply: str):
    key = f"{user_id}::{conv_id}"
    _demo_chat_history[key].append({"role": "user",      "content": user_msg})
    _demo_chat_history[key].append({"role": "assistant", "content": ai_reply})

def demo_load_history(user_id: str, conv_id: str) -> list:
    key = f"{user_id}::{conv_id}"
    return _demo_chat_history.get(key, [])

def demo_get_stats(user_id: str) -> dict:
    sid = user_id.replace("demo-", "")
    session = _demo_sessions.get(sid, {})
    markers = session.get("markers", [])
    abnormal = [m for m in markers if m.get("status") in ("HIGH", "LOW")]
    return {
        "total_markers":  len(set(m.get("marker_name","") for m in markers)),
        "abnormal_count": len(abnormal),
        "document_count": len(set(m.get("source_document","") for m in markers if m.get("source_document"))),
    }