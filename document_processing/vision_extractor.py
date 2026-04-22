"""
document_processing/vision_extractor.py
─────────────────────────────────────────────────────────────────────────────
Vision-AI OCR Pipeline for Photo Lab Reports

Converts JPEG/PNG photos of paper lab reports into structured text using
GPT-4o-mini vision. This is the "Zero Tax" entry point for mobile users who
photograph their paper reports rather than scanning them.

Fallback chain:
  1. GPT-4o-mini vision (best quality, requires OPENAI_API_KEY)
  2. pytesseract local OCR (free, lower quality on photos)
  3. ValueError with human-readable message

Design rules (matches extractor.py conventions):
  - All imports are LAZY — module loads even if openai/PIL are missing
  - Every failure path raises ValueError with a clear user-facing message
  - Images are NEVER stored — base64 is processed in-memory and discarded
  - PII anonymization happens downstream in compliance.py (not here)

Supported formats:
  .jpg, .jpeg, .png, .webp, .heic (HEIC converted via pillow-heif if available)

Usage:
    from document_processing.vision_extractor import extract_text_from_image
    text = extract_text_from_image(file_storage)  # Flask FileStorage object
"""

from __future__ import annotations
import io
import os
import base64
import re

# ── Accepted image MIME types → base64 content-type strings ──────────────────
_IMAGE_MIME_MAP = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".webp": "image/webp",
    ".heic": "image/jpeg",  # will be converted via pillow-heif
}

# ── Vision prompt — tuned for lab report structure ────────────────────────────
_VISION_SYSTEM = """\
You are a medical document OCR specialist. Extract ALL text from this lab report image.

CRITICAL RULES:
1. Transcribe every character exactly as it appears — do not interpret or summarize.
2. Preserve the original layout: marker name | value | unit | reference range | status (HIGH/LOW/NORMAL).
3. Include the report date, lab name, and any section headers.
4. If a value is partially obscured, write [UNCLEAR] for that field.
5. Output ONLY the raw transcribed text — no commentary, no JSON, no markdown.
"""

_VISION_USER = """\
Please transcribe this lab report image exactly. Return only the raw text content.
"""

# Medical keyword gate (same threshold as extractor.py)
_MEDICAL_KEYWORDS = [
    "mg/dl", "mg/l", "g/dl", "g/l", "mmol/l", "nmol/l", "ng/ml",
    "pg/ml", "iu/l", "u/l", "miu/ml", "%", "hemoglobin", "haemoglobin",
    "hba1c", "glucose", "cholesterol", "triglyceride", "creatinine",
    "ferritin", "albumin", "bilirubin", "platelet", "tsh", "vitamin",
    "alt", "ast", "ldl", "hdl", "wbc", "rbc", "reference range",
    "normal range", "lab report", "laboratory", "blood test", "specimen",
    "result", "patient", "report date",
]
_MIN_MEDICAL_KW = 4


def _is_medical_content(text: str) -> bool:
    lower = text.lower()
    count = sum(1 for kw in _MEDICAL_KEYWORDS if kw in lower)
    return count >= _MIN_MEDICAL_KW


def _convert_heic_to_jpeg(data: bytes) -> bytes:
    """Convert HEIC to JPEG bytes via pillow-heif. Raises ImportError if unavailable."""
    try:
        import pillow_heif
        from PIL import Image
        pillow_heif.register_heif_opener()
        img = Image.open(io.BytesIO(data))
        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=92)
        return out.getvalue()
    except ImportError:
        raise ImportError(
            "HEIC conversion requires pillow-heif. "
            "Add 'pillow-heif' to requirements.txt and redeploy."
        )


def _resize_for_vision(data: bytes, max_px: int = 2048) -> bytes:
    """
    Resize image to max_px on the longest side for Vision API cost control.
    Lab reports are text-heavy — 2048px preserves all readable content.
    Returns original bytes if PIL is unavailable.
    """
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        w, h = img.size
        if max(w, h) <= max_px:
            return data
        scale  = max_px / max(w, h)
        new_w  = int(w * scale)
        new_h  = int(h * scale)
        img    = img.resize((new_w, new_h), Image.LANCZOS)
        out    = io.BytesIO()
        fmt    = "JPEG" if img.mode == "RGB" else "PNG"
        img.convert("RGB").save(out, format=fmt, quality=92)
        print(f"[VISION] Resized {w}×{h} → {new_w}×{new_h}")
        return out.getvalue()
    except ImportError:
        return data  # Pillow not installed — proceed with original


def _to_base64(data: bytes, mime: str) -> str:
    return base64.b64encode(data).decode("utf-8")


# ── Primary: GPT-4o-mini Vision ───────────────────────────────────────────────

def _extract_via_gpt4o_vision(image_data: bytes, mime_type: str) -> str:
    """
    Use GPT-4o-mini's vision capability to OCR the lab report image.
    Cost estimate: ~$0.002 per 2048px image (vision tokens + completion).
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY is required for photo lab report analysis. "
            "Set OPENAI_API_KEY in your environment or use PDF upload instead."
        )

    try:
        from openai import OpenAI
    except ImportError:
        raise ValueError(
            "openai package not installed. Add 'openai' to requirements.txt and redeploy."
        )

    # Resize for cost control before encoding
    image_data = _resize_for_vision(image_data)
    b64        = _to_base64(image_data, mime_type)
    data_url   = f"data:{mime_type};base64,{b64}"

    client = OpenAI(api_key=api_key)

    try:
        resp = client.chat.completions.create(
            model    = "gpt-4o-mini",
            messages = [
                {"role": "system", "content": _VISION_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "text",      "text": _VISION_USER},
                        {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                    ],
                },
            ],
            max_tokens  = 3000,
            temperature = 0.0,
        )
        text = (resp.choices[0].message.content or "").strip()
        print(f"[VISION] GPT-4o-mini extracted {len(text)} chars")
        return text
    except Exception as e:
        raise ValueError(f"Vision API call failed: {e}")


# ── Fallback: pytesseract local OCR ──────────────────────────────────────────

def _extract_via_tesseract(image_data: bytes) -> str:
    """
    Local OCR fallback using pytesseract.
    Quality is acceptable for printed lab reports, poor for handwriting.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        raise ValueError(
            f"Local OCR requires pytesseract and Pillow: {e}. "
            "Set OPENAI_API_KEY for better quality Vision OCR."
        )

    try:
        img  = Image.open(io.BytesIO(image_data))
        text = pytesseract.image_to_string(img, lang="eng", config="--psm 6")
        print(f"[VISION] Tesseract extracted {len(text)} chars")
        return text.strip()
    except Exception as e:
        raise ValueError(f"Local OCR failed: {e}")


# ── Public API ────────────────────────────────────────────────────────────────

def is_image_file(filename: str) -> bool:
    """Return True if the filename extension is a supported image type."""
    ext = os.path.splitext((filename or "").lower())[1]
    return ext in _IMAGE_MIME_MAP


def extract_text_from_image(file_storage) -> str:
    """
    Extract text from an uploaded image file (Flask FileStorage object).

    Pipeline:
      1. Read and validate bytes
      2. HEIC → JPEG conversion if needed
      3. Attempt GPT-4o-mini Vision (best quality)
      4. Fall back to pytesseract if Vision unavailable
      5. Validate output is medical content

    Raises ValueError with user-facing message on any unrecoverable failure.
    """
    filename = (file_storage.filename or "").strip().lower()
    ext      = os.path.splitext(filename)[1]
    mime     = _IMAGE_MIME_MAP.get(ext)

    if not mime:
        raise ValueError(
            f"Unsupported image format '{ext}'. "
            "Please upload JPG, PNG, WebP, or HEIC."
        )

    # Read bytes
    try:
        file_storage.seek(0)
        data = file_storage.read()
    except Exception as e:
        raise ValueError(f"Could not read image file: {e}")

    if not data:
        raise ValueError("The uploaded image is empty.")

    if len(data) > 20 * 1024 * 1024:
        raise ValueError(
            f"Image too large ({len(data)/1024/1024:.1f} MB). Maximum 20 MB for photos."
        )

    # HEIC conversion
    if ext == ".heic":
        try:
            data = _convert_heic_to_jpeg(data)
            mime = "image/jpeg"
            print("[VISION] HEIC converted to JPEG")
        except ImportError as e:
            raise ValueError(str(e))

    # Try GPT-4o-mini Vision first
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            text = _extract_via_gpt4o_vision(data, mime)
            if len(text.strip()) > 30:
                if _is_medical_content(text):
                    print(f"[VISION] GPT-4o-mini: medical content confirmed ({len(text)} chars)")
                    return text
                else:
                    print("[VISION] GPT-4o-mini output lacks medical keywords — checking...")
                    # Still return it — downstream extractor will validate
                    if len(text) > 100:
                        return text
        except ValueError as e:
            print(f"[VISION] GPT-4o-mini failed: {e} — trying Tesseract")

    # Tesseract fallback
    try:
        text = _extract_via_tesseract(data)
        if len(text.strip()) < 30:
            raise ValueError(
                "Could not read text from this photo. "
                "Please ensure good lighting, the report is flat and fully visible, "
                "and try a PDF upload if available."
            )
        return text
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(
            f"Image OCR failed: {e}. "
            "Try uploading a PDF scan or ensure OPENAI_API_KEY is configured."
        )


def extract_image_quality_hints(file_storage) -> dict:
    """
    Analyse image quality before extraction.
    Returns hints the frontend can display to guide re-upload.
    Non-blocking — never raises.
    """
    hints = {
        "ok":      True,
        "issues":  [],
        "tips":    [],
    }
    try:
        from PIL import Image
        file_storage.seek(0)
        img  = Image.open(io.BytesIO(file_storage.read()))
        w, h = img.size

        if min(w, h) < 600:
            hints["issues"].append("Image resolution is low")
            hints["tips"].append("Move closer to the document and retake the photo")
            hints["ok"] = False

        if max(w, h) > 8000:
            hints["issues"].append("Image is very large — will be resized automatically")

        # Check brightness (grayscale mean)
        gray = img.convert("L")
        import struct
        pixels    = list(gray.getdata())
        avg_lum   = sum(pixels) / len(pixels)
        if avg_lum < 60:
            hints["issues"].append("Image appears too dark")
            hints["tips"].append("Improve lighting or increase brightness before uploading")
            hints["ok"] = False
        elif avg_lum > 240:
            hints["issues"].append("Image appears overexposed")
            hints["tips"].append("Reduce glare or avoid direct flash on the document")

    except Exception:
        pass  # Non-critical — never block upload

    finally:
        try:
            file_storage.seek(0)
        except Exception:
            pass

    return hints