"""Tests for the DocumentService and document tool dispatch."""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from praxis.models.schemas import DocumentAnalysisResult, DocumentCreationResult

# ── DocumentService.create_report ──────────────────────────────

@pytest.mark.asyncio
async def test_create_report_generates_docx(tmp_path):
    """create_report produces a valid .docx file."""
    from praxis.services.document_service import DocumentService

    svc = DocumentService(output_dir=str(tmp_path))
    file_path = await svc.create_report(
        title="Test Report",
        sections=[
            {"heading": "Executive Summary", "content": "This is a test summary."},
            {"heading": "Findings", "content": "Finding 1.\nFinding 2."},
            {"heading": "Conclusion", "content": "All good."},
        ],
    )
    assert file_path.endswith(".docx")
    assert Path(file_path).exists()
    assert Path(file_path).stat().st_size > 0


@pytest.mark.asyncio
async def test_create_report_with_tables(tmp_path):
    """create_report handles table data in sections."""
    from praxis.services.document_service import DocumentService

    svc = DocumentService(output_dir=str(tmp_path))
    file_path = await svc.create_report(
        title="Financial Report",
        sections=[
            {"heading": "Revenue", "content": [
                "Total revenue for Q1.",
                {"type": "table", "headers": ["Quarter", "Revenue"], "rows": [["Q1", "$1M"], ["Q2", "$2M"]]},
            ]},
        ],
    )
    assert Path(file_path).exists()


@pytest.mark.asyncio
async def test_create_memo(tmp_path):
    """create_memo produces a valid .docx file."""
    from praxis.services.document_service import DocumentService

    svc = DocumentService(output_dir=str(tmp_path))
    file_path = await svc.create_memo(
        to="John Doe",
        sender="Jane Smith",
        subject="Project Update",
        body="The project is on track for Q3 delivery.",
    )
    assert Path(file_path).exists()


@pytest.mark.asyncio
async def test_create_letter(tmp_path):
    """create_letter produces a valid .docx file."""
    from praxis.services.document_service import DocumentService

    svc = DocumentService(output_dir=str(tmp_path))
    file_path = await svc.create_letter(
        recipient_name="John Doe",
        recipient_address="123 Main St\nSydney NSW 2000",
        sender_name="Jane Smith",
        sender_address="456 Business Ave\nMelbourne VIC 3000",
        subject="Contract Renewal",
        body="We are pleased to offer you a contract renewal.",
    )
    assert Path(file_path).exists()


@pytest.mark.asyncio
async def test_analyze_docx(tmp_path):
    """analyze_document extracts content from a .docx file."""
    from praxis.services.document_service import DocumentService

    svc = DocumentService(output_dir=str(tmp_path))

    # First create a document
    file_path = await svc.create_report(
        title="Analysis Test",
        sections=[
            {"heading": "Overview", "content": "This document tests the analysis functionality."},
            {"heading": "Details", "content": "More details here."},
        ],
    )

    # Then analyze it
    result = await svc.analyze_document(file_path)
    assert result["file_type"] == "docx"
    assert result["word_count"] > 0
    assert "quality" in result
    assert len(result["sections"]) >= 2


@pytest.mark.asyncio
async def test_analyze_nonexistent_file():
    """analyze_document returns error for non-existent file."""
    from praxis.services.document_service import DocumentService

    svc = DocumentService()
    result = await svc.analyze_document("/nonexistent/file.docx")
    assert "error" in result


@pytest.mark.asyncio
async def test_analyze_unsupported_type(tmp_path):
    """analyze_document returns error for unsupported file types."""
    from praxis.services.document_service import DocumentService

    test_file = tmp_path / "test.txt"
    test_file.write_text("Hello")
    svc = DocumentService()
    result = await svc.analyze_document(str(test_file))
    assert "error" in result


# ── Tool dispatch tests ────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_tool_create_report():
    """_execute_tool dispatches create_report and returns DocumentCreationResult."""
    from praxis.graph.nodes import _execute_tool

    mock_doc_svc = AsyncMock()
    mock_doc_svc.create_report = AsyncMock(return_value="/tmp/test_report.docx")

    name, result = await _execute_tool(
        {
            "name": "create_report",
            "title": "Q1 Financial Summary",
            "sections": "Executive Summary::Revenue grew 15%||Conclusion::Strong quarter",
        },
        document_service=mock_doc_svc,
    )

    assert name == "create_report"
    assert isinstance(result, DocumentCreationResult)
    assert result.success is True
    assert result.title == "Q1 Financial Summary"
    assert result.file_path == "/tmp/test_report.docx"


@pytest.mark.asyncio
async def test_execute_tool_create_report_without_service():
    """_execute_tool returns graceful degradation when document_service is None."""
    from praxis.graph.nodes import _execute_tool

    name, result = await _execute_tool(
        {"name": "create_report", "title": "Test", "sections": ""},
        document_service=None,
    )

    assert name == "create_report"
    assert isinstance(result, DocumentCreationResult)
    assert result.success is False


@pytest.mark.asyncio
async def test_execute_tool_analyze_document():
    """_execute_tool dispatches analyze_document and returns DocumentAnalysisResult."""
    from praxis.graph.nodes import _execute_tool

    mock_doc_svc = AsyncMock()
    mock_doc_svc.analyze_document = AsyncMock(return_value={
        "file_type": "docx",
        "page_count": 3,
        "word_count": 500,
        "text": "Document content here.",
        "quality": {"level": "good", "score": 0.85},
    })

    name, result = await _execute_tool(
        {"name": "analyze_document", "file_path": "/tmp/test.docx"},
        document_service=mock_doc_svc,
    )

    assert name == "analyze_document"
    assert isinstance(result, DocumentAnalysisResult)
    assert result.success is True
    assert result.file_type == "docx"
    assert result.page_count == 3
    assert result.word_count == 500
    assert result.quality_level == "good"
    assert result.quality_score == 0.85


@pytest.mark.asyncio
async def test_execute_tool_analyze_document_without_service():
    """_execute_tool returns graceful degradation when document_service is None."""
    from praxis.graph.nodes import _execute_tool

    name, result = await _execute_tool(
        {"name": "analyze_document", "file_path": "/tmp/test.docx"},
        document_service=None,
    )

    assert name == "analyze_document"
    assert isinstance(result, DocumentAnalysisResult)
    assert result.success is False


# ── Schema validation ──────────────────────────────────────────

def test_document_analysis_result_schema():
    """DocumentAnalysisResult validates with required fields."""
    result = DocumentAnalysisResult(
        file_path="/tmp/test.docx",
        file_type="docx",
        page_count=5,
        word_count=1000,
        quality_level="excellent",
        quality_score=0.95,
    )
    assert result.tool_name == "analyze_document"
    assert result.success is True
    assert result.file_type == "docx"


def test_document_creation_result_schema():
    """DocumentCreationResult validates with required fields."""
    result = DocumentCreationResult(
        file_path="/tmp/report.docx",
        document_type="report",
        title="Q1 Report",
    )
    assert result.tool_name == "create_document"
    assert result.success is True
    assert result.document_type == "report"


# ── Enterprise quality standards ──────────────────────────────

def test_quality_standards_validate_structure():
    """Quality standards correctly validate document structure."""
    from praxis.services.document_standards import (
        DocumentType,
        validate_document_structure,
    )

    checks = validate_document_structure(
        DocumentType.REPORT,
        ["title", "executive_summary", "body", "conclusion"],
    )
    assert all(c.passed for c in checks)


def test_quality_standards_detect_missing_sections():
    """Quality standards detect missing required sections."""
    from praxis.services.document_standards import (
        DocumentType,
        validate_document_structure,
    )

    checks = validate_document_structure(
        DocumentType.REPORT,
        ["title", "body"],  # Missing executive_summary and conclusion
    )
    failed = [c for c in checks if not c.passed]
    assert len(failed) == 2
    assert any("executive_summary" in c.name for c in failed)
    assert any("conclusion" in c.name for c in failed)


def test_quality_report_excellent():
    """Quality report returns EXCELLENT for a well-structured document."""
    from praxis.services.document_standards import (
        DocumentType,
        QualityLevel,
        get_quality_report,
    )

    report = get_quality_report(
        DocumentType.REPORT,
        ["title", "executive_summary", "body", "conclusion"],
        has_page_numbers=True,
        has_header=True,
        has_footer=True,
        has_toc=True,
        page_count=5,
        word_count=2000,
        has_tables=True,
        font_consistent=True,
    )
    assert report.overall_level == QualityLevel.EXCELLENT
    assert report.passed is True
