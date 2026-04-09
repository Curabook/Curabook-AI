"""
document_processing/extractor.py
─────────────────────────────────────────────────────────────────────────────
FIX #OCR-1  _is_useful_text() previously gated OCR purely on character /
            letter counts.  "Dirty" PDFs with junk glyphs, encoded fonts, or
            copy-protection artefacts can pass the char-count threshold while
            containing zero real medical text — so OCR was never triggered and
            the report came back as unreadable noise.

            New logic:
            1. Character count check is kept as the first gate (fast reject
               of truly empty streams).
            2. A medical-keyword scan is added as the decisive gate:
               if fewer than 5 recognised medical / lab-result keywords are
               present, the text is treated as useless and OCR is triggered.
            3. The keyword list covers units, marker names, and structural
               terms that appear in every real lab report.
"""

from pypdf import PdfReader
import io
import re


# ── Medical keywords that must appear in real lab text ───────────────────────
# Grouped by category so the threshold is meaningful:
#   units  (mg/dl, g/dl, iu/l …)
#   common marker names
#   structural report terms
_MEDICAL_KEYWORDS = [
    # Units — almost always present in a lab report
    "mg/dl", "mg/l", "g/dl", "g/l",
    "mmol/l", "nmol/l", "pmol/l", "umol/l",
    "ng/ml", "pg/ml", "ug/ml",
    "iu/l", "iu/ml", "u/l", "u/ml",
    "miu/l", "miu/ml",
    "meq/l", "fl", "pg",
    "%",
    # Common marker names
    "hemoglobin", "haemoglobin", "hba1c",
    "glucose", "cholesterol", "triglyceride",
    "creatinine", "urea", "bilirubin",
    "platelet", "leucocyte", "lymphocyte",
    "sodium", "potassium", "calcium",
    "tsh", "t3", "t4", "insulin",
    "ferritin", "albumin", "protein",
    "alt", "ast", "alp", "ggt",
    "ldl", "hdl",
    "vitamin", "b12", "folate",
    "uric acid", "egfr",
    "wbc", "rbc", "mcv", "mch",
    # Structural report terms
    "reference range", "normal range", "reference interval",
    "lab report", "laboratory", "blood test",
    "result", "value", "units", "method",
    "patient", "specimen", "collected",
    "report date", "sample",
]

_MIN_MEDICAL_KEYWORDS = 5   # require at least this many to trust pypdf output


def extract_text_from_file(file_storage) -> str:
    """
    Extract all text from an uploaded Flask FileStorage object.
    Tries digital extraction first, falls back to OCR for scanned /
    corrupted PDFs.  seek(0) is always called defensively before reading.
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
    data = file_storage.read()

    if not data:
        raise ValueError(
            "PDF stream was empty — the file could not be read. "
            "This is a server-side bug: file stream was already consumed before extraction."
        )

    # Step 1: try pypdf (fast, works for digital PDFs)
    try:
        reader = PdfReader(io.BytesIO(data))
        pages  = [page.extract_text() or "" for page in reader.pages]
        text   = "\n".join(pages).strip()
        if _is_useful_text(text):
            print(f"[EXTRACTOR] Digital PDF — {len(reader.pages)} pages, {len(text)} chars")
            return text
        else:
            kw_count = _count_medical_keywords(text)
            print(
                f"[EXTRACTOR] pypdf returned {len(text)} chars but only "
                f"{kw_count} medical keywords — triggering OCR"
            )
    except Exception as e:
        print(f"[EXTRACTOR] pypdf failed: {e} — trying OCR…")

    # Step 2: OCR fallback
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
                "Download from: https://github.com/UB-Mannheim/tesseract/wiki"
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
        raise ValueError(f"OCR processing failed: {e}")


# ── TXT extraction ────────────────────────────────────────────────────────────

def _extract_txt(file_storage) -> str:
    try:
        data = file_storage.read()
        return data.decode("utf-8", errors="replace").strip()
    except Exception as e:
        raise ValueError(f"Text file read failed: {e}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _count_medical_keywords(text: str) -> int:
    """
    Count how many distinct medical keywords appear in text (case-insensitive).
    Used to decide whether pypdf output is real lab text or junk.
    """
    lower = text.lower()
    return sum(1 for kw in _MEDICAL_KEYWORDS if kw in lower)


def _is_useful_text(text: str) -> bool:
    """
    FIX #OCR-1: Determine whether pypdf extracted real medical text.

    Two-stage check:
      Stage 1 (fast): minimum character and letter count.
                      Rejects truly empty or whitespace-only output immediately.
      Stage 2 (decisive): medical keyword count.
                      Even if Stage 1 passes, a "dirty" PDF with junk glyphs
                      will fail Stage 2 and trigger OCR.

    Previous implementation used only Stage 1, causing bad OCR bypass on
    corrupted/encoded PDFs.
    """
    # Stage 1: hard minimum (empty / all whitespace / single character)
    if not text or len(text) < 50:
        return False
    letter_count = sum(1 for c in text if c.isalpha())
    if letter_count < 20:
        return False

    # Stage 2: medical keyword density gate
    kw_count = _count_medical_keywords(text)
    if kw_count < _MIN_MEDICAL_KEYWORDS:
        return False

    return True


def chunk_text(text: str, max_chars: int = 1500, overlap: int = 200) -> list[str]:
    """Split long text into overlapping chunks for embedding."""
    chunks = []
    start  = 0
    while start < len(text):
        end = start + max_chars
        chunks.append(text[start:end])
        start = end - overlap
    return [c.strip() for c in chunks if len(c.strip()) >= 50]