"""
ai/system_prompt_v2.py
─────────────────────────────────────────────────────────────────────────────
Re-export shim so chat_routes.py can import from system_prompt_v2
without crashing. All real logic lives in system_prompt.py.

This file existing is what prevents the ImportError that breaks
every single chat response in production.
─────────────────────────────────────────────────────────────────────────────
"""

from ai.system_prompt import (
    build_phi_messages,
    validate_response,
    detect_hallucination_risk,
    check_prompt_injection,
    MANDATORY_DISCLAIMER,
    PHI_CORE_SYSTEM,
)

__all__ = [
    "build_phi_messages",
    "validate_response",
    "detect_hallucination_risk",
    "check_prompt_injection",
    "MANDATORY_DISCLAIMER",
    "PHI_CORE_SYSTEM",
]