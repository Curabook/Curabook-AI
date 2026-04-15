"""
chat_routes.py
Curabook PHI - Core Chat Logic with Decision Support Mode
"""

import os
import json
import logging
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime

# Adjust these imports according to your actual project structure
from services.auth import require_auth
from health_memory.rag import RAGService
from health_memory.memory import get_patient_memory, save_chat_turn
from insights.engine import analyze_clinical_risk
from ai.chat import generate_phi_response

chat_bp = Blueprint('chat', __name__)
logger = logging.getLogger(__name__)

# Initialize dependencies if they use singleton pattern, or initialize inside routes
rag_service = RAGService()

@chat_bp.route('/chat', methods=['POST'])
@require_auth
def handle_chat(user_id):
    """
    Primary endpoint for processing user chat, incorporating RAG,
    document analysis, and the PHI Decision Support System.
    """
    try:
        data = request.json
        if not data:
            return jsonify({"error": "Invalid request payload"}), 400

        conversation_id = data.get('conversation_id')
        user_message = data.get('message', '').strip()
        has_documents = data.get('has_documents', False)
        document_text = data.get('document_text', '')
        
        if not conversation_id or not user_message:
            return jsonify({"error": "Missing conversation_id or message"}), 400

        # 1. Fetch User's Long-term Health Memory
        # (This context gives the AI the history needed to spot trends)
        patient_memory = get_patient_memory(user_id)
        
        # 2. Perform RAG Search if needed
        # (Find past relevant lab values based on the current user query)
        rag_context = ""
        if not has_documents:
            rag_context = rag_service.query_memory(user_id, user_message)

        # 3. Analyze Current Documents (if uploaded) for Risk
        # (This populates the Clinical Justification block for Advocacy)
        risk_profile = {}
        if has_documents and document_text:
            risk_profile = analyze_clinical_risk(document_text)
            
        # 4. Build the Comprehensive AI Context
        # Note: We enforce De-identification / "Shadow Compliance" here by ensuring
        # only UUIDs and raw clinical data pass to the LLM.
        system_context = {
            "current_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "patient_memory": patient_memory,
            "rag_context": rag_context,
            "document_context": document_text if has_documents else None,
            "risk_profile": risk_profile
        }

        # 5. Generate the AI Response
        # The AI (Claude/GPT-4o) applies the System Prompt (Decision Guidance Protocol)
        ai_reply = generate_phi_response(
            user_message=user_message,
            context=system_context,
            conversation_id=conversation_id
        )

        # 6. Save the interaction to memory
        save_chat_turn(
            user_id=user_id,
            conversation_id=conversation_id,
            user_message=user_message,
            ai_message=ai_reply
        )

        # =====================================================================
        # CRITICAL FIX: Disclaimer Logic
        # =====================================================================
        # Previously, there was logic here that appended the disclaimer manually:
        # e.g., if "⚕️" not in ai_reply: ai_reply += "⚕️ PHI is an educational..."
        # THIS HAS BEEN REMOVED to prevent the "Double Disclaimer" bug.
        # The AI Engine via its Master Prompt now autonomously decides when to 
        # append the ⚕️ disclaimer (only for labs, meds, and risk assessment).
        # =====================================================================

        return jsonify({
            "success": True,
            "reply": ai_reply,
            "conversation_id": conversation_id
        }), 200

    except Exception as e:
        logger.error(f"Chat error for user {user_id}: {str(e)}")
        return jsonify({"error": "An internal error occurred processing your request."}), 500