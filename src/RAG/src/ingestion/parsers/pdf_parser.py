from __future__ import annotations

from io import BytesIO
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    from pypdf import PdfReader
except ModuleNotFoundError:  # pragma: no cover - depends on optional runtime package
    PdfReader = None

from src.utils.preprocess import normalize_whitespace


logger = logging.getLogger(__name__)


def extract_pdf_text(
    data: bytes,
    max_pages: int | None = 20,
    enable_ocr: bool = False,
    ocr_lang: str = "kor+eng",
) -> str:
    if PdfReader is None:
        raise RuntimeError("pypdf is required to parse PDF attachments. Install requirements.txt first.")
    reader = PdfReader(BytesIO(data))
    page_texts: list[str] = []
    pages = reader.pages[:max_pages] if max_pages else reader.pages
    for page in pages:
        text = page.extract_text() or ""
        if text.strip():
            page_texts.append(text)
    text = normalize_whitespace("\n".join(page_texts))
    if text or not enable_ocr:
        return text
    return extract_pdf_text_with_ocr(data, max_pages=max_pages, lang=ocr_lang)


def extract_pdf_text_with_ocr(data: bytes, max_pages: int | None = 3, lang: str = "kor+eng") -> str:
    pdftoppm = shutil.which("pdftoppm")
    tesseract = shutil.which("tesseract")
    if not pdftoppm or not tesseract:
        logger.info("PDF OCR skipped because pdftoppm or tesseract is not installed.")
        return ""
    if not _has_tesseract_languages(lang):
        logger.info("PDF OCR skipped because requested tesseract languages are unavailable: %s", lang)
        return ""

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        pdf_path = temp_path / "source.pdf"
        output_prefix = temp_path / "page"
        pdf_path.write_bytes(data)

        command = [pdftoppm, "-png", "-r", "220"]
        if max_pages:
            command.extend(["-f", "1", "-l", str(max_pages)])
        command.extend([str(pdf_path), str(output_prefix)])
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            logger.warning("PDF to image conversion failed: %s", exc.stderr or exc)
            return ""

        texts: list[str] = []
        for image_path in sorted(temp_path.glob("page-*.png")):
            text = extract_image_text_with_ocr(image_path.read_bytes(), lang=lang)
            if text:
                texts.append(text)
        return normalize_whitespace("\n".join(texts))


def extract_image_text_with_ocr(data: bytes, lang: str = "kor+eng") -> str:
    tesseract = shutil.which("tesseract")
    if not tesseract:
        logger.info("Image OCR skipped because tesseract is not installed.")
        return ""
    if not _has_tesseract_languages(lang):
        logger.info("Image OCR skipped because requested tesseract languages are unavailable: %s", lang)
        return ""

    with tempfile.TemporaryDirectory() as temp_dir:
        image_path = Path(temp_dir) / "source-image"
        image_path.write_bytes(data)
        command = [tesseract, str(image_path), "stdout", "-l", lang, "--psm", "6"]
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            logger.warning("Image OCR failed: %s", exc.stderr or exc)
            return ""
        return normalize_whitespace(result.stdout)


def _has_tesseract_languages(lang: str) -> bool:
    tesseract = shutil.which("tesseract")
    if not tesseract:
        return False
    requested = {part for part in re.split(r"[+ ]+", lang) if part}
    if not requested:
        return True
    try:
        result = subprocess.run([tesseract, "--list-langs"], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        return False
    available = set(result.stdout.splitlines()[1:])
    return requested.issubset(available)


def is_pdf_url(url: str, name: str | None = None) -> bool:
    target = f"{name or ''} {url}".lower()
    return ".pdf" in target or "pdf" in target


__all__ = ["extract_pdf_text", "extract_image_text_with_ocr", "is_pdf_url"]
