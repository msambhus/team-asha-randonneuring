#!/usr/bin/env python3
"""Extract personality traits from blog posts (WordPress URL or local PDF).

This is a stub created in Plan 02 to allow test collection.
Full implementation is in Plan 03.

Usage:
    # WordPress URL (Mihir's blog)
    python scripts/extract_personality_blog.py \
        --url https://unexpectedathlete.wordpress.com/2023/09/06/... \
        --rider-name Mihir

    # Local PDF (Venki's Google Drive PDF, downloaded manually)
    python scripts/extract_personality_blog.py \
        --pdf-path /path/to/venki_blog.pdf \
        --rider-name Venki
"""

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

try:
    import trafilatura
except ImportError:
    # Create a placeholder module with stub attributes so unittest.mock.patch
    # can patch them in tests even when the library is not installed.
    from types import ModuleType as _ModuleType

    def _trafilatura_not_installed(*args, **kwargs):
        raise ImportError("trafilatura is required: pip install trafilatura==2.0.0")

    _trafilatura_mod = _ModuleType('trafilatura')
    _trafilatura_mod.fetch_url = _trafilatura_not_installed  # type: ignore
    _trafilatura_mod.extract = _trafilatura_not_installed  # type: ignore
    trafilatura = _trafilatura_mod  # type: ignore

try:
    import pdfplumber
except ImportError:
    # Create a placeholder module with stub attributes so unittest.mock.patch
    # can patch pdfplumber.open in tests even when the library is not installed.
    from types import ModuleType as _ModuleType

    def _pdfplumber_not_installed(*args, **kwargs):
        raise ImportError("pdfplumber is required: pip install pdfplumber==0.11.9")

    _pdfplumber_mod = _ModuleType('pdfplumber')
    _pdfplumber_mod.open = _pdfplumber_not_installed  # type: ignore
    pdfplumber = _pdfplumber_mod  # type: ignore


def fetch_blog_text(url: str) -> str:
    """Fetch and extract article text from a blog URL using trafilatura.

    Args:
        url: Full URL to a blog post or article page.

    Returns:
        Extracted plain text content of the article.

    Raises:
        ValueError: If the URL cannot be fetched or no text is extractable.
        ImportError: If trafilatura is not installed.
    """
    if trafilatura is None:
        raise ImportError("trafilatura is required. Install with: pip install trafilatura==2.0.0")

    downloaded = trafilatura.fetch_url(url)
    if downloaded is None:
        raise ValueError(f"Failed to fetch: {url}")
    text = trafilatura.extract(downloaded)
    if not text:
        raise ValueError(f"No extractable text from: {url}")
    return text


def extract_pdf_text(pdf_path: str) -> str:
    """Extract text from a local PDF file using pdfplumber.

    Args:
        pdf_path: Local filesystem path to a PDF file.

    Returns:
        Concatenated plain text of all pages.

    Raises:
        ValueError: If no text can be extracted (e.g. scanned PDF).
        ImportError: If pdfplumber is not installed.
    """
    if pdfplumber is None:
        raise ImportError("pdfplumber is required. Install with: pip install pdfplumber==0.11.9")

    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text.strip())
    full_text = '\n\n'.join(pages)
    if not full_text.strip():
        raise ValueError(f"No text extracted from PDF: {pdf_path}. May be scanned.")
    return full_text
