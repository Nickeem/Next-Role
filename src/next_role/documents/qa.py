"""Checks on rendered files (specification §10): nothing lost, nothing reordered.

Every visible string from the display model must appear in the text extracted from the file,
in reading order. Comparison ignores case (templates may set headings in capitals),
whitespace, and dash and quote variants. Length over the target is a warning, not a failure:
text is never shrunk below the minimum size to fit.
"""

import io
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from pypdf import PdfReader

from next_role.documents.display import DisplayDocument

_FOLD = str.maketrans(
    {
        "\u2013": "-",
        "\u2014": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u00b7": " ",
    }
)


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_FOLD).lower()
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class QAResult:
    passed: bool
    missing: list[str] = field(default_factory=list)
    out_of_order: list[str] = field(default_factory=list)
    page_count: int | None = None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            # Counts only: the texts themselves are candidate data and stay out of the row.
            "missing": len(self.missing),
            "out_of_order": len(self.out_of_order),
            "page_count": self.page_count,
            "warnings": self.warnings,
        }


def check_text(expected: list[str], extracted: str) -> tuple[list[str], list[str]]:
    """Strings missing from ``extracted``, and strings found only out of order."""
    haystack = fold(extracted)
    missing, out_of_order = [], []
    position = 0
    for text in expected:
        needle = fold(text)
        if not needle:
            continue
        found = haystack.find(needle, position)
        if found >= 0:
            position = found + len(needle)
        elif needle in haystack:
            out_of_order.append(text)
        else:
            missing.append(text)
    return missing, out_of_order


def _result(
    display: DisplayDocument, extracted: str, pages: int | None, tokens: dict[str, Any]
) -> QAResult:
    missing, out_of_order = check_text(display.texts(), extracted)
    warnings = []
    if pages is not None:
        target = tokens[
            "target_letter_pages" if display.kind == "cover_letter" else "target_resume_pages"
        ]
        if pages > target:
            warnings.append(f"{pages} pages; the template targets {target}")
    return QAResult(
        passed=not missing and not out_of_order and bool(fold(extracted)),
        missing=missing,
        out_of_order=out_of_order,
        page_count=pages,
        warnings=warnings,
    )


def check_docx(display: DisplayDocument, data: bytes, tokens: dict[str, Any]) -> QAResult:
    from next_role.documents.docx_renderer import extract_text

    if not data:
        return QAResult(passed=False, warnings=["empty file"])
    return _result(display, "\n".join(extract_text(data)), None, tokens)


def check_pdf(display: DisplayDocument, data: bytes, tokens: dict[str, Any]) -> QAResult:
    if not data.startswith(b"%PDF"):
        return QAResult(passed=False, warnings=["not a PDF"])
    reader = PdfReader(io.BytesIO(data))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return _result(display, text, len(reader.pages), tokens)
