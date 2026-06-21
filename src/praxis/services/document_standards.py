"""Enterprise document quality standards.

Defines what makes a professional enterprise document — formatting rules,
structural requirements, and validation criteria. Used by DocumentService
to ensure generated documents meet enterprise standards.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DocumentType(str, Enum):
    REPORT = "report"
    MEMO = "memo"
    LETTER = "letter"
    EXECUTIVE_SUMMARY = "executive_summary"
    FINANCIAL_ANALYSIS = "financial_analysis"
    INVOICE = "invoice"
    POLICY = "policy"
    PROPOSAL = "proposal"


class QualityLevel(str, Enum):
    EXCELLENT = "excellent"
    GOOD = "good"
    ACCEPTABLE = "acceptable"
    POOR = "poor"
    UNACCEPTABLE = "unacceptable"


@dataclass
class QualityCheck:
    """A single quality criterion check result."""
    name: str
    passed: bool
    detail: str = ""
    severity: str = "error"  # error, warning, info


@dataclass
class DocumentQualityReport:
    """Aggregate quality assessment of a document."""
    overall_level: QualityLevel
    score: float  # 0.0 to 1.0
    checks: list[QualityCheck] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True if no error-severity checks failed."""
        return all(c.passed for c in self.checks if c.severity == "error")


# ── Enterprise formatting standards ──────────────────────────────

# Font standards (Arial is universally supported and enterprise-standard)
DEFAULT_FONT = "Arial"
DEFAULT_FONT_SIZE = 11  # pt
HEADING1_SIZE = 16
HEADING2_SIZE = 14
HEADING3_SIZE = 12
TITLE_SIZE = 20

# Page layout (US Letter)
PAGE_WIDTH_DXA = 12240  # 8.5 inches
PAGE_HEIGHT_DXA = 15840  # 11 inches
MARGIN_DXA = 1440  # 1 inch
CONTENT_WIDTH_DXA = PAGE_WIDTH_DXA - 2 * MARGIN_DXA  # 9360

# Spacing
SPACING_BEFORE_HEADING = 240  # 12pt before
SPACING_AFTER_HEADING = 120  # 6pt after
SPACING_AFTER_PARAGRAPH = 120  # 6pt after body paragraphs
LINE_SPACING = 1.15  # 1.15x line spacing for readability

# Colors
COLOR_PRIMARY = "1F4E79"  # Dark blue
COLOR_ACCENT = "2E75B6"  # Medium blue
COLOR_TABLE_HEADER = "D5E8F0"  # Light blue
COLOR_TABLE_BORDER = "CCCCCC"  # Light grey


# ── Required sections by document type ───────────────────────────

REQUIRED_SECTIONS: dict[DocumentType, list[str]] = {
    DocumentType.REPORT: [
        "title",
        "executive_summary",
        "body",
        "conclusion",
    ],
    DocumentType.MEMO: [
        "header",  # TO / FROM / DATE / RE
        "body",
    ],
    DocumentType.LETTER: [
        "sender_address",
        "date",
        "recipient_address",
        "salutation",
        "body",
        "closing",
    ],
    DocumentType.EXECUTIVE_SUMMARY: [
        "title",
        "summary",
        "key_findings",
        "recommendations",
    ],
    DocumentType.FINANCIAL_ANALYSIS: [
        "title",
        "executive_summary",
        "methodology",
        "analysis",
        "conclusion",
    ],
    DocumentType.PROPOSAL: [
        "title",
        "executive_summary",
        "scope",
        "approach",
        "timeline",
        "pricing",
    ],
}


def validate_document_structure(
    doc_type: DocumentType,
    sections_present: list[str],
) -> list[QualityCheck]:
    """Validate that a document has all required sections for its type."""
    checks: list[QualityCheck] = []
    required = REQUIRED_SECTIONS.get(doc_type, [])

    # Normalize: lowercase, replace spaces with underscores, strip
    present_normalized = set()
    for s in sections_present:
        norm = s.lower().strip().replace(" ", "_").replace("-", "_")
        present_normalized.add(norm)
        # Also add the raw form for partial matching
        present_normalized.add(s.lower().strip())

    for section in required:
        if section in present_normalized or any(section in s for s in present_normalized):
            checks.append(QualityCheck(
                name=f"section:{section}",
                passed=True,
                detail=f"Required section '{section}' is present",
            ))
        else:
            checks.append(QualityCheck(
                name=f"section:{section}",
                passed=False,
                detail=f"Required section '{section}' is MISSING",
                severity="error",
            ))

    return checks


def get_quality_report(
    doc_type: DocumentType,
    sections_present: list[str],
    has_page_numbers: bool = False,
    has_header: bool = False,
    has_footer: bool = False,
    has_toc: bool = False,
    page_count: int = 0,
    word_count: int = 0,
    has_tables: bool = False,
    font_consistent: bool = True,
) -> DocumentQualityReport:
    """Generate a comprehensive quality report for a document."""
    checks: list[QualityCheck] = []

    # Structural checks
    checks.extend(validate_document_structure(doc_type, sections_present))

    # Formatting checks
    if page_count > 2 and not has_page_numbers:
        checks.append(QualityCheck(
            name="formatting:page_numbers",
            passed=False,
            detail="Multi-page document lacks page numbers",
            severity="warning",
        ))
    else:
        checks.append(QualityCheck(
            name="formatting:page_numbers",
            passed=True,
            detail="Page numbers present or not needed",
        ))

    if page_count > 3 and not has_toc:
        checks.append(QualityCheck(
            name="formatting:toc",
            passed=False,
            detail="Document >3 pages lacks a table of contents",
            severity="warning",
        ))
    else:
        checks.append(QualityCheck(
            name="formatting:toc",
            passed=True,
            detail="TOC present or not needed",
        ))

    if not font_consistent:
        checks.append(QualityCheck(
            name="formatting:font_consistency",
            passed=False,
            detail="Multiple fonts detected — use consistent font throughout",
            severity="warning",
        ))
    else:
        checks.append(QualityCheck(
            name="formatting:font_consistency",
            passed=True,
            detail="Font is consistent throughout",
        ))

    # Content checks
    if word_count < 50:
        checks.append(QualityCheck(
            name="content:length",
            passed=False,
            detail=f"Document is very short ({word_count} words)",
            severity="warning",
        ))
    else:
        checks.append(QualityCheck(
            name="content:length",
            passed=True,
            detail=f"Document length is adequate ({word_count} words)",
        ))

    # Score calculation
    error_count = sum(1 for c in checks if not c.passed and c.severity == "error")
    warning_count = sum(1 for c in checks if not c.passed and c.severity == "warning")
    total = len(checks)
    passed = sum(1 for c in checks if c.passed)
    score = passed / total if total > 0 else 0.0

    if error_count > 0:
        level = QualityLevel.UNACCEPTABLE
    elif warning_count >= 3:
        level = QualityLevel.POOR
    elif warning_count >= 1:
        level = QualityLevel.ACCEPTABLE
    elif score >= 0.95:
        level = QualityLevel.EXCELLENT
    else:
        level = QualityLevel.GOOD

    # Recommendations
    recommendations: list[str] = []
    for check in checks:
        if not check.passed and check.severity == "error":
            recommendations.append(f"Add missing section: {check.name.split(':')[1]}")
        elif not check.passed:
            recommendations.append(check.detail)

    return DocumentQualityReport(
        overall_level=level,
        score=score,
        checks=checks,
        recommendations=recommendations,
    )
