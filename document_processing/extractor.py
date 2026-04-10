"""
document_processing/extractor.py
─────────────────────────────────────────────────────────────────────────────
Raw file → plain text extraction with OCR fallback.

KEY RULES in this file:
  1. NO top-level imports that can fail (pypdf, pdf2image, pytesseract).
     All optional imports are lazy — inside the function that needs them.
     This means the module always loads successfully even if a package is
     missing. The error surfaces at call time with a clear message.

  2. _is_useful_text() uses a two-stage check:
       Stage 1 — fast: character / letter count (guards truly empty output)
       Stage 2 — medical keywords: if < MIN_MEDICAL_KEYWORDS found,
                 the text is likely garbled encoding, not real lab text.
     IMPORTANT: if OCR dependencies are unavailable, Stage 1 is used alone.
     The keyword check only forces OCR when OCR is actually available.

  3. Every failure path raises ValueError with a human-readable message.
     document_routes.py catches ValueError and returns HTTP 400 (not 500).
"""

import io
import re


# ── Medical keywords for Stage-2 quality check ───────────────────────────────
_MEDICAL_KEYWORDS = [
    # Units
    "mg/dl", "mg/l", "g/dl", "g/l", "mmol/l", "nmol/l",
    "ng/ml", "pg/ml", "ug/ml", "iu/l", "u/l", "miu/ml", "%",
    # Marker names
    "hemoglobin", "haemoglobin", "hba1c", "glucose", "cholesterol",
    "triglyceride", "creatinine", "ferritin", "albumin", "bilirubin",
    "platelet", "sodium", "potassium", "tsh", "vitamin",
    "alt", "ast", "ldl", "hdl", "wbc", "rbc",
    # Report structure
    "reference range", "normal range", "lab report", "laboratory",
    "blood test", "specimen", "result", "patient", "report date",
]
_MIN_MEDICAL_KEYWORDS = 5


def _ocr_available() -> bool:
    """Check whether OCR dependencies are present without importing them."""
    try:
        import pdf2image   # noqa: F401
        import pytesseract # noqa: F401
        return True
    except ImportError:
        return False


def _count_medical_keywords(text: str) -> int:
    lower = text.lower()
    return sum(1 for kw in _MEDICAL_KEYWORDS if kw in lower)


def _is_useful_text(text: str) -> bool:
    """
    Stage 1: basic sanity (length + letters).
    Stage 2: medical keyword density — only applied when OCR is available,
             so we never force an OCR attempt when OCR will fail.
    """
    if not text or len(text) < 50:
        return False
    letter_count = sum(1 for c in text if c.isalpha())
    if letter_count < 20:
        return False

    # Stage 2 only applies when OCR fallback is actually usable
    if _ocr_available():
        kw_count = _count_medical_keywords(text)
        if kw_count < _MIN_MEDICAL_KEYWORDS:
            print(
                f"[EXTRACTOR] Only {kw_count} medical keywords in pypdf output "
                f"(need {_MIN_MEDICAL_KEYWORDS}) — triggering OCR"
            )
            return False

    return True


# ── Public API ────────────────────────────────────────────────────────────────

def extract_text_from_file(file_storage) -> str:
    """
    Extract text from an uploaded Flask FileStorage object.
    Always seek(0) defensively before reading.
    Raises ValueError with a human-readable message on failure.
    """
    filename = (file_storage.filename or "").lower()
    try:
        file_storage.seek(0)
    except Exception:
        pass

    if filename.endswith(".pdf"):
        return _extract_pdf(file_storage)
    elif filename.endswith(".txt"):
        return _extract_txt(file_storage)
    else:
        raise ValueError("Unsupported file type. Please upload a PDF or TXT file.")


# ── PDF extraction ────────────────────────────────────────────────────────────

def _extract_pdf(file_storage) -> str:
    # Lazy import — module loads even if pypdf is missing
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError(
            "pypdf is not installed on this server. "
            "Add 'pypdf' to requirements.txt and redeploy."
        )

    data = file_storage.read()
    if not data:
        raise ValueError(
            "PDF stream was empty. "
            "This is a server-side bug: the file stream was consumed before extraction."
        )

    # Step 1: try pypdf (instant for digital PDFs)
    try:
        reader = PdfReader(io.BytesIO(data))
        pages  = [page.extract_text() or "" for page in reader.pages]
        text   = "\n".join(pages).strip()
        if _is_useful_text(text):
            print(f"[EXTRACTOR] Digital PDF — {len(reader.pages)} pages, {len(text)} chars")
            return text
        else:
            print(f"[EXTRACTOR] pypdf output insufficient — trying OCR")
    except Exception as e:
        print(f"[EXTRACTOR] pypdf failed: {e} — trying OCR")

    # Step 2: OCR fallback
    return _ocr_pdf(data)


def _ocr_pdf(data: bytes) -> str:
    """Convert PDF pages to images and OCR each page."""
    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        raise ValueError(
            "This PDF appears to be a scanned image and requires OCR, "
            "but pdf2image is not installed on this server. "
            "Add 'pdf2image' to requirements.txt and install Tesseract on the system."
        )

    try:
        import pytesseract
    except ImportError:
        raise ValueError(
            "pytesseract is not installed. "
            "Add 'pytesseract' to requirements.txt and install Tesseract OCR on the system."
        )

    import sys, os
    if sys.platform == "win32":
        import shutil
        for path in [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                     r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]:
            if os.path.exists(path):
                pytesseract.pytesseract.tesseract_cmd = path
                break

    try:
        images = convert_from_bytes(data, dpi=300)
        print(f"[EXTRACTOR] OCR: {len(images)} page(s)")
        pages_text = []
        for i, img in enumerate(images):
            page_text = pytesseract.image_to_string(img, lang="eng")
            pages_text.append(page_text)
            print(f"[EXTRACTOR] OCR page {i+1}: {len(page_text)} chars")
        text = "\n".join(pages_text).strip()
        if not text:
            raise ValueError(
                "OCR found no text in this PDF. "
                "Please ensure the file is a clear, readable lab report."
            )
        print(f"[EXTRACTOR] OCR complete — {len(text)} chars")
        return text
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"OCR processing failed: {e}")


# ── TXT extraction ────────────────────────────────────────────────────────────

def _extract_txt(file_storage) -> str:
    try:
        return file_storage.read().decode("utf-8", errors="replace").strip()
    except Exception as e:
        raise ValueError(f"Text file read failed: {e}")


# ── Utilities ─────────────────────────────────────────────────────────────────

def chunk_text(text: str, max_chars: int = 1500, overlap: int = 200) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        end = start + max_chars
        chunks.append(text[start:end])
        start = end - overlap
    return [c.strip() for c in chunks if len(c.strip()) >= 50]