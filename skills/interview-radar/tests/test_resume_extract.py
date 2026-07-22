from pathlib import Path

from scripts.resume_extract import extract_resume, ResumeExtraction

FIXTURE = Path(__file__).parent / "fixtures" / "sample_resume.pdf"


def test_pdf_extraction_returns_text():
    result = extract_resume(FIXTURE)
    assert isinstance(result, ResumeExtraction)
    assert "Skill" in result.text
    assert result.needs_vision is False


def test_image_resume_flags_vision(tmp_path):
    img = tmp_path / "resume.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    result = extract_resume(img)
    assert result.needs_vision is True
    assert result.asset_path == str(img)
    assert result.text == ""


def test_empty_pdf_flags_vision(tmp_path):
    blank = tmp_path / "blank.pdf"
    blank.write_bytes((FIXTURE.read_bytes().replace(b"Skill driven agent project Python RAG", b" ")))
    result = extract_resume(blank)
    assert result.needs_vision is True
