"""Turn an uploaded file into plain text. The object store keeps the original bytes."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

_DOCX_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MEDIA_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def media_type_for(filename: str) -> str:
    return _MEDIA_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")


def extract_text(filename: str, body: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md", ".markdown", ".csv"}:
        text = _decode(body)
    elif suffix == ".docx":
        text = _docx_text(body)
    elif suffix == ".pdf":
        text = _pdf_text(body)
    else:
        kind = suffix or filename or "file"
        raise ValueError(f"unsupported file type: {kind}")
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("file has no text")
    return cleaned


def _decode(body: bytes) -> str:
    try:
        return body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("file is not utf-8 text") from exc


def _docx_text(body: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(body)) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise ValueError("could not read the docx") from exc
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{_DOCX_NS}p"):
        pieces = [node.text or "" for node in paragraph.iter(f"{_DOCX_NS}t")]
        line = "".join(pieces).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def _pdf_text(body: bytes) -> str:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:
        raise ValueError("pdf support is not installed") from exc
    try:
        reader = PdfReader(BytesIO(body))
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, OSError, ValueError, TypeError) as exc:
        raise ValueError("could not read the pdf") from exc
    return "\n".join(page.strip() for page in pages if page.strip())
