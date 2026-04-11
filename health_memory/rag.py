"""
health_memory/rag.py
─────────────────────────────────────────────────────────────────────────────
PHI RAG (Retrieval-Augmented Generation) Pipeline

Slots into the existing chat pipeline in api/chat_routes.py as an optional
context layer ON TOP of the structured health memory from memory.py.

WHAT THIS ADDS:
  - health_memory contains structured marker data (LDL: 172, HbA1c: 5.6%)
  - RAG retrieves raw document CHUNKS that are semantically similar to the
    user's question — useful for radiology reports, discharge summaries,
    or any document where markers weren't extracted

EMBEDDING MODEL:
  Development (now): sentence-transformers all-MiniLM-L6-v2 (384 dims, free)
  Production (later): switch EMBED_PROVIDER=openai in .env to use
                      text-embedding-3-small (1536 dims, better quality)
  The Supabase documents table must match the dimension you use.
  Run the SQL at the bottom of this file if switching models.

HOW TO WIRE INTO chat_routes.py:
  # In _build_context(), after stored_block is built:
  from health_memory.rag import rag_search
  rag_block = rag_search(supabase, message, user_id, top_k=4)
  if rag_block:
      parts.insert(0, rag_block)   # prepend — highest priority context

SUPABASE TABLE REQUIRED:
  Run the SQL at the bottom of this file in Supabase SQL Editor.
  Your existing ingest.py already writes to this table.
"""

from __future__ import annotations
import os
from typing import Optional


# ── Embedding provider ────────────────────────────────────────────────────────
# Set EMBED_PROVIDER=openai in .env to switch to OpenAI in production.
# Default is sentence-transformers (free, runs locally, 384 dims).

_EMBED_PROVIDER  = os.getenv("EMBED_PROVIDER", "sentence_transformers")
_EMBED_DIM       = 384    # all-MiniLM-L6-v2
_EMBED_DIM_OAI   = 1536   # text-embedding-3-small
_OPENAI_EMBED_MODEL = "text-embedding-3-small"

# Module-level model cache (loaded once, reused across requests)
_st_model = None


def _get_embedding(text: str) -> list[float]:
    """
    Generate an embedding vector for the given text.

    Automatically picks the right provider:
      - EMBED_PROVIDER=openai  → OpenAI text-embedding-3-small (1536 dims)
      - anything else           → sentence-transformers all-MiniLM-L6-v2 (384 dims)

    Returns empty list on failure (caller treats this as "no RAG results").
    """
    text = text.strip()
    if not text:
        return []

    if _EMBED_PROVIDER == "openai":
        return _embed_openai(text)
    return _embed_local(text)


def _embed_openai(text: str) -> list[float]:
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        print("[RAG] EMBED_PROVIDER=openai but OPENAI_API_KEY not set — falling back to local")
        return _embed_local(text)
    try:
        from openai import OpenAI
        resp = OpenAI(api_key=openai_key).embeddings.create(
            model=_OPENAI_EMBED_MODEL,
            input=text[:8000],   # token limit safety
        )
        return resp.data[0].embedding
    except Exception as e:
        print(f"[RAG] OpenAI embedding error: {e}")
        return []


def _embed_local(text: str) -> list[float]:
    global _st_model
    if _st_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _st_model = SentenceTransformer("all-MiniLM-L6-v2")
            print("[RAG] Loaded sentence-transformers model")
        except ImportError:
            print("[RAG] sentence-transformers not installed. Add to requirements.txt.")
            return []
        except Exception as e:
            print(f"[RAG] Model load error: {e}")
            return []
    try:
        return _st_model.encode(text[:2000]).tolist()
    except Exception as e:
        print(f"[RAG] Local embedding error: {e}")
        return []


# ── Supabase similarity search ────────────────────────────────────────────────

def _search_documents(
    supabase,
    query_embedding: list[float],
    user_id:         str,
    top_k:           int   = 5,
    threshold:       float = 0.65,
) -> list[dict]:
    """
    Call the match_documents RPC in Supabase.
    Returns a list of {content, metadata, similarity} dicts.

    The RPC filters by user_id so users only get their own document chunks.
    Falls back gracefully if the function doesn't exist yet.
    """
    if not query_embedding:
        return []
    try:
        result = supabase.rpc("match_documents", {
            "query_embedding": query_embedding,
            "match_threshold":  threshold,
            "match_count":      top_k,
            "filter_user_id":   user_id,   # scoped to this user's documents
        }).execute()
        return result.data or []
    except Exception as e:
        err = str(e)
        # Gracefully handle: function not found, wrong dimension, RLS block
        if "match_documents" in err:
            print(f"[RAG] match_documents RPC not found — run the SQL setup. Error: {e}")
        elif "different vector" in err or "dimension" in err.lower():
            print(f"[RAG] Embedding dimension mismatch — check EMBED_PROVIDER setting. Error: {e}")
        else:
            print(f"[RAG] Search error: {e}")
        return []


# ── Context formatter ─────────────────────────────────────────────────────────

def _format_rag_block(docs: list[dict]) -> str:
    """Format retrieved chunks into a clear context block for the LLM."""
    if not docs:
        return ""

    lines = ["📄 RELEVANT DOCUMENT CONTEXT (retrieved via semantic search):"]
    for i, d in enumerate(docs, 1):
        source = (d.get("metadata") or {}).get("source", "uploaded document")
        sim    = d.get("similarity", 0)
        text   = (d.get("content") or "").strip()
        if text:
            lines.append(f"\n[Chunk {i} — source: {source}, relevance: {sim:.0%}]")
            lines.append(text[:800])   # limit per chunk to prevent context blow-up

    lines.append("\n[END DOCUMENT CONTEXT — use these verbatim values if referencing]")
    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

def rag_search(
    supabase,
    query:     str,
    user_id:   str,
    top_k:     int   = 4,
    threshold: float = 0.65,
) -> str:
    """
    Main entry point. Call this from chat_routes._build_context().

    Parameters
    ----------
    supabase   : Supabase client from app.py
    query      : The user's message / question
    user_id    : The authenticated user's ID (for RLS scoping)
    top_k      : Max number of document chunks to retrieve
    threshold  : Minimum cosine similarity (0–1). Lower = more results, less relevant.

    Returns
    -------
    Formatted string to inject into the LLM context, or "" if no results.
    """
    embedding = _get_embedding(query)
    if not embedding:
        return ""

    docs = _search_documents(supabase, embedding, user_id, top_k, threshold)
    if not docs:
        return ""

    print(f"[RAG] Retrieved {len(docs)} chunks for user {user_id[:8]}")
    return _format_rag_block(docs)


def ingest_text(
    supabase,
    user_id:   str,
    text:      str,
    source:    str = "",
    chunk_size: int = 1200,
    overlap:    int = 200,
) -> int:
    """
    Split text into overlapping chunks, embed each, and store in Supabase.

    Called automatically by document_routes._extract_and_store_markers()
    after a document is uploaded. You can also call it standalone.

    Returns the number of chunks stored.
    """
    chunks = _chunk_text(text, chunk_size, overlap)
    if not chunks:
        return 0

    stored = 0
    for chunk in chunks:
        embedding = _get_embedding(chunk)
        if not embedding:
            continue
        try:
            supabase.table("documents").insert({
                "user_id":   user_id,
                "content":   chunk,
                "metadata":  {"source": source or "uploaded_document"},
                "embedding": embedding,
            }).execute()
            stored += 1
        except Exception as e:
            print(f"[RAG] Ingest chunk error: {e}")

    print(f"[RAG] Ingested {stored}/{len(chunks)} chunks from '{source}'")
    return stored


def _chunk_text(text: str, max_chars: int = 1200, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks for embedding."""
    chunks, start = [], 0
    while start < len(text):
        end = start + max_chars
        chunks.append(text[start:end].strip())
        start = end - overlap
    return [c for c in chunks if len(c) >= 40]