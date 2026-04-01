"""
document_processing/extractor.py
─────────────────────────────────────────────────────────────────────────────
Raw file → plain text extraction with OCR fallback.

Supports:
  • PDF  — digital text via pypdf (fast, free)
          → OCR fallback via pdf2image + pytesseract (scanned/image PDFs)
  • TXT  — UTF-8

Most Indian lab reports (SRL, Thyrocare, Dr Lal, Apollo) are scanned images.
pypdf alone returns empty string for these — OCR is essential.

Setup (one-time):
  pip install pypdf pdf2image pytesseract pillow
  Install Tesseract OCR engine:
    Windows : https://github.com/UB-Mannheim/tesseract/wiki
              Add to PATH: C:\\Program Files\\Tesseract-OCR
    Linux   : sudo apt install tesseract-ocr
    Mac     : brew install tesseract
"""

from pypdf import PdfReader
import io


def extract_text_from_file(file_storage) -> str:
    """
    Extract all text from an uploaded Flask FileStorage object.
    Tries digital extraction first, falls back to OCR for scanned PDFs.

    FIX: Always seek(0) before reading so this function is safe to call
    even if something upstream already read the stream.
    """
    filename = (file_storage.filename or "").lower()

    # ── KEY FIX: reset stream before reading ─────────────────────────────────
    # document_routes.py does seek(0) after the file-size check, but defensive
    # reset here makes this function safe regardless of caller state.
    try:
        file_storage.seek(0)
    except Exception:
        pass  # some stream types don't support seek — proceed and hope for the best

    if filename.endswith(".pdf"):
        return _extract_pdf(file_storage)
    elif filename.endswith(".txt"):
        return _extract_txt(file_storage)
    else:
        raise ValueError("Unsupported file type. Please upload a PDF or TXT file.")


# ── PDF extraction ────────────────────────────────────────────────────────────

def _extract_pdf(file_storage) -> str:
    data = file_storage.read()

    # Guard: empty stream means the caller didn't seek(0) before passing the file
    if not data:
        raise ValueError(
            "PDF stream was empty — the file could not be read. "
            "This is a server-side bug: file stream was already consumed before extraction."
        )

    # ── Step 1: try pypdf (instant, works for digital PDFs) ───────────────────
    try:
        reader = PdfReader(io.BytesIO(data))
        pages  = [page.extract_text() or "" for page in reader.pages]
        text   = "\n".join(pages).strip()
        if _is_useful_text(text):
            print(f"[EXTRACTOR] Digital PDF — {len(reader.pages)} pages, {len(text)} chars")
            return text
        else:
            print(f"[EXTRACTOR] pypdf returned {len(text)} chars — likely scanned. Trying OCR…")
    except Exception as e:
        print(f"[EXTRACTOR] pypdf failed: {e} — trying OCR…")

    # ── Step 2: OCR fallback (scanned/image PDFs) ─────────────────────────────
    return _ocr_pdf(data)


def _ocr_pdf(data: bytes) -> str:
    """Convert PDF pages to images then run Tesseract OCR on each page."""
    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        raise ValueError(
            "This PDF appears to be a scanned image and requires OCR. "
            "Please install: pip install pdf2image pytesseract\n"
            "And install Tesseract: https://github.com/UB-Mannheim/tesseract/wiki"
        )

    try:
        import pytesseract
    except ImportError:
        raise ValueError(
            "pytesseract is not installed. Run: pip install pytesseract\n"
            "And install Tesseract OCR: https://github.com/UB-Mannheim/tesseract/wiki"
        )

    # Windows: Tesseract is NOT automatically in PATH.
    import sys, os
    if sys.platform == "win32":
        import shutil
        _default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        _alt     = r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
        if os.path.exists(_default):
            pytesseract.pytesseract.tesseract_cmd = _default
        elif os.path.exists(_alt):
            pytesseract.pytesseract.tesseract_cmd = _alt
        elif not shutil.which("tesseract"):
            raise ValueError(
                "Tesseract OCR is not installed or not found.\n"
                "Download from: https://github.com/UB-Mannheim/tesseract/wiki\n"
                "Default path: C:\\Program Files\\Tesseract-OCR\\tesseract.exe\n"
                "After installing, restart the server."
            )

    try:
        images = convert_from_bytes(data, dpi=300)
        print(f"[EXTRACTOR] OCR: processing {len(images)} page(s)…")

        pages_text = []
        for i, img in enumerate(images):
            page_text = pytesseract.image_to_string(img, lang="eng")
            pages_text.append(page_text)
            print(f"[EXTRACTOR] OCR page {i+1}: {len(page_text)} chars")

        text = "\n".join(pages_text).strip()

        if not text:
            raise ValueError(
                "OCR could not extract any text from this PDF. "
                "Please ensure the file is a clear, readable lab report."
            )

        print(f"[EXTRACTOR] OCR complete — {len(text)} total chars")
        return text

    except ValueError:
        raise
    except Exception as e:
        # FIX: surface the real error instead of a generic message
        raise ValueError(f"OCR processing failed: {e}")


# ── TXT extraction ────────────────────────────────────────────────────────────

def _extract_txt(file_storage) -> str:
    try:
        data = file_storage.read()
        return data.decode("utf-8", errors="replace").strip()
    except Exception as e:
        raise ValueError(f"Text file read failed: {e}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_useful_text(text: str) -> bool:
    """
    Return True if pypdf extracted meaningful text (not just whitespace/garbage).

    FIX: Lowered thresholds from 100 chars / 50 letters to 50 chars / 20 letters.
    Some valid single-value lab reports (e.g. a glucose-only strip result) are
    short and were being incorrectly sent to the slow OCR path.
    """
    if not text or len(text) < 50:
        return False
    letter_count = sum(1 for c in text if c.isalpha())
    return letter_count > 20


def chunk_text(text: str, max_chars: int = 1500, overlap: int = 200) -> list[str]:
    """Split long text into overlapping chunks for embedding."""
    chunks = []
    start  = 0
    while start < len(text):
        end = start + max_chars
        chunks.append(text[start:end])
        start = end - overlap
    return [c.strip() for c in chunks if len(c.strip()) >= 50]