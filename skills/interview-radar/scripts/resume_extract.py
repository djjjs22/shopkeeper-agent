from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_MIN_TEXT_CHARS = 10


@dataclass
class ResumeExtraction:
    text: str
    needs_vision: bool
    asset_path: str


def extract_resume(path) -> ResumeExtraction:
    p = Path(path)
    ext = p.suffix.lower()
    if ext in _IMAGE_EXTS:
        return ResumeExtraction(text="", needs_vision=True, asset_path=str(p))
    if ext == ".pdf":
        reader = PdfReader(str(p))
        text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
        if len(text) < _MIN_TEXT_CHARS:
            return ResumeExtraction(text="", needs_vision=True, asset_path=str(p))
        return ResumeExtraction(text=text, needs_vision=False, asset_path=str(p))
    text = p.read_text(encoding="utf-8", errors="ignore").strip()
    needs_vision = len(text) < _MIN_TEXT_CHARS
    return ResumeExtraction(text=text, needs_vision=needs_vision, asset_path=str(p))
