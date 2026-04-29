from __future__ import annotations
import io
import os
import base64
import re

_IMAGE_MIME_MAP = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".webp": "image/webp",
    ".heic": "image/jpeg",
}

# 1. SMART FIX: Adaptive Prompt (Medical vs. General)
_VISION_SYSTEM = """\
You are an expert OCR and context specialist for a health AI platform.

CRITICAL RULES:
1. If this is a medical document or lab report: Transcribe every character exactly. Preserve layout. Do not summarize.
2. If this is NOT a medical document (e.g., a general photo, an app screenshot, food, etc.): Briefly describe exactly what the image shows so the main AI health assistant can understand the user's context.
3. Output ONLY the transcribed text or the description — no markdown formatting, no conversational filler.
"""

_VISION_USER = """\
Please process this image according to your system instructions. Return only the raw text or description.
"""

def _convert_heic_to_jpeg(data: bytes) -> bytes:
    try:
        import pillow_heif
        from PIL import Image
        pillow_heif.register_heif_opener()
        img = Image.open(io.BytesIO(data))
        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=92)
        return out.getvalue()
    except ImportError:
        raise ImportError("HEIC conversion requires pillow-heif.")

def _resize_for_vision(data: bytes, max_px: int = 2048) -> bytes:
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
        return out.getvalue()
    except ImportError:
        return data

def _to_base64(data: bytes, mime: str) -> str:
    return base64.b64encode(data).decode("utf-8")

def _extract_via_gpt4o_vision(image_data: bytes, mime_type: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is required for photo analysis.")
    try:
        from openai import OpenAI
    except ImportError:
        raise ValueError("openai package not installed.")

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
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        raise ValueError(f"Vision API call failed: {e}")

def _extract_via_tesseract(image_data: bytes) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        raise ValueError(f"Local OCR requires pytesseract and Pillow: {e}")
    try:
        img  = Image.open(io.BytesIO(image_data))
        return pytesseract.image_to_string(img, lang="eng", config="--psm 6").strip()
    except Exception as e:
        raise ValueError(f"Local OCR failed: {e}")

def is_image_file(filename: str) -> bool:
    ext = os.path.splitext((filename or "").lower())[1]
    return ext in _IMAGE_MIME_MAP

def extract_text_from_image(file_storage) -> str:
    filename = (file_storage.filename or "").strip().lower()
    ext      = os.path.splitext(filename)[1]
    mime     = _IMAGE_MIME_MAP.get(ext)

    if not mime:
        raise ValueError(f"Unsupported image format '{ext}'.")

    try:
        file_storage.seek(0)
        data = file_storage.read()
    except Exception as e:
        raise ValueError(f"Could not read image file: {e}")

    if not data:
        raise ValueError("The uploaded image is empty.")
    if len(data) > 20 * 1024 * 1024:
        raise ValueError(f"Image too large. Maximum 20 MB.")

    if ext == ".heic":
        try:
            data = _convert_heic_to_jpeg(data)
            mime = "image/jpeg"
        except ImportError as e:
            raise ValueError(str(e))

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            text = _extract_via_gpt4o_vision(data, mime)
            # 2. SMART FIX: Trust the AI and always return its output. No throwing away descriptions!
            if text: return text
        except ValueError as e:
            print(f"[VISION] GPT-4o-mini failed: {e} — trying Tesseract")

    try:
        text = _extract_via_tesseract(data)
        if len(text.strip()) < 10:
            raise ValueError("Could not read text from this photo.")
        return text
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Image OCR failed: {e}")

def extract_image_quality_hints(file_storage) -> dict:
    hints = {"ok": True, "issues": [], "tips": []}
    try:
        from PIL import Image
        file_storage.seek(0)
        img  = Image.open(io.BytesIO(file_storage.read()))
        w, h = img.size
        if min(w, h) < 600:
            hints["issues"].append("Image resolution is low")
            hints["tips"].append("Move closer to the document")
            hints["ok"] = False
        gray = img.convert("L")
        pixels = list(gray.getdata())
        avg_lum = sum(pixels) / len(pixels)
        if avg_lum < 60:
            hints["issues"].append("Image appears too dark")
            hints["ok"] = False
        elif avg_lum > 240:
            hints["issues"].append("Image appears overexposed")
    except Exception:
        pass
    finally:
        try: file_storage.seek(0)
        except Exception: pass
    return hints