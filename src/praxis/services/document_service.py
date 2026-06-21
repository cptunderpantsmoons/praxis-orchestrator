"""Document handling, analysis, and creation service.

Provides:
- analyze_document(): Extract text, structure, tables, metadata from .docx/.pdf/.xlsx
- create_report(): Generate a professional enterprise .docx report
- create_memo(): Generate a professional memo
- create_letter(): Generate a professional business letter

All created documents follow enterprise quality standards (see document_standards.py).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from praxis.services.document_standards import (
    COLOR_ACCENT,
    COLOR_PRIMARY,
    COLOR_TABLE_HEADER,
    DEFAULT_FONT,
    DEFAULT_FONT_SIZE,
    HEADING1_SIZE,
    HEADING2_SIZE,
    HEADING3_SIZE,
    LINE_SPACING,
    SPACING_AFTER_HEADING,
    SPACING_AFTER_PARAGRAPH,
    SPACING_BEFORE_HEADING,
    TITLE_SIZE,
    DocumentType,
    get_quality_report,
)

logger = logging.getLogger(__name__)

# DXA conversion helpers
PT_TO_DXA = 20  # 1pt = 20 DXA (twips)
INCH_TO_DXA = 1440
EMU_PER_INCH = 914400


class DocumentService:
    """Handles document analysis and creation.

    Supports .docx (read + write), .pdf (read), and .xlsx (read).
    Generated documents use enterprise-standard formatting.
    """

    def __init__(self, output_dir: str = "/tmp/praxis_documents") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── Document Analysis (Reading) ─────────────────────────────

    async def analyze_document(self, file_path: str) -> dict[str, Any]:
        """Analyze a document and extract its content and structure.

        Supports .docx, .pdf, and .xlsx files.

        Returns a dict with keys:
        - file_type, page_count, word_count, sections, tables, metadata
        - text (full extracted text)
        - quality_report (for .docx)
        """
        path = Path(file_path)
        if not path.exists():
            return {"error": f"File not found: {file_path}"}

        suffix = path.suffix.lower()
        if suffix == ".docx":
            return await self._analyze_docx(path)
        elif suffix == ".pdf":
            return await self._analyze_pdf(path)
        elif suffix in (".xlsx", ".xls"):
            return await self._analyze_xlsx(path)
        else:
            return {"error": f"Unsupported file type: {suffix}"}

    async def _analyze_docx(self, path: Path) -> dict[str, Any]:
        """Extract content and structure from a .docx file."""
        from docx import Document

        doc = Document(str(path))

        # Extract paragraphs with structure
        sections: list[dict] = []
        full_text_parts: list[str] = []
        heading_styles = {"Heading 1", "Heading 2", "Heading 3", "Title"}

        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue

            style_name = para.style.name if para.style else "Normal"
            is_heading = style_name in heading_styles or (
                style_name.startswith("Heading")
            )

            sections.append({
                "text": text,
                "style": style_name,
                "is_heading": is_heading,
                "heading_level": int(style_name[-1]) if style_name.startswith("Heading") and style_name[-1].isdigit() else 0,
            })
            full_text_parts.append(text)

        # Extract tables
        tables: list[dict] = []
        for table in doc.tables:
            rows_data = []
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                rows_data.append(cells)
            tables.append({
                "rows": len(rows_data),
                "columns": len(rows_data[0]) if rows_data else 0,
                "data": rows_data[:10],  # Limit to first 10 rows
            })

        # Metadata
        core_props = doc.core_properties
        metadata = {
            "author": core_props.author or "",
            "title": core_props.title or "",
            "subject": core_props.subject or "",
            "created": str(core_props.created) if core_props.created else "",
            "modified": str(core_props.modified) if core_props.modified else "",
            "revision": core_props.revision or 0,
        }

        full_text = "\n".join(full_text_parts)
        word_count = len(full_text.split())

        # Estimate page count (~500 words per page)
        page_count = max(1, (word_count + 499) // 500)

        # Check for header/footer
        has_header = False
        has_footer = False
        for section in doc.sections:
            if section.header and section.header.paragraphs:
                if any(p.text.strip() for p in section.header.paragraphs):
                    has_header = True
            if section.footer and section.footer.paragraphs:
                if any(p.text.strip() for p in section.footer.paragraphs):
                    has_footer = True

        # Font consistency check
        fonts_used: set[str] = set()
        for para in doc.paragraphs[:50]:  # Check first 50 paragraphs
            for run in para.runs:
                if run.font.name:
                    fonts_used.add(run.font.name)
        font_consistent = len(fonts_used) <= 2  # Allow body + heading font

        # Quality report — detect title from metadata or first paragraph
        sections_present = [s["text"].lower()[:50] for s in sections if s["is_heading"]]
        # Add "title" if document has a title property or a centered first paragraph
        if metadata.get("title") or any(
            p.alignment is not None and hasattr(p.alignment, 'name') and p.alignment.name == 'CENTER'
            for p in doc.paragraphs[:3] if p.text.strip()
        ):
            sections_present.append("title")
        # Add "body" if there are any non-heading paragraphs with content
        if any(not s["is_heading"] and len(s["text"]) > 20 for s in sections):
            sections_present.append("body")
        quality = get_quality_report(
            DocumentType.REPORT,
            sections_present,
            has_page_numbers=has_footer,
            has_header=has_header,
            has_footer=has_footer,
            page_count=page_count,
            word_count=word_count,
            has_tables=len(tables) > 0,
            font_consistent=font_consistent,
        )

        return {
            "file_type": "docx",
            "file_path": str(path),
            "page_count": page_count,
            "word_count": word_count,
            "sections": sections[:30],  # Limit for response size
            "table_count": len(tables),
            "tables": tables[:5],
            "metadata": metadata,
            "fonts_used": list(fonts_used),
            "text": full_text[:5000],  # First 5000 chars
            "quality": {
                "level": quality.overall_level.value,
                "score": round(quality.score, 2),
                "passed": quality.passed,
                "recommendations": quality.recommendations,
            },
        }

    async def _analyze_pdf(self, path: Path) -> dict[str, Any]:
        """Extract text from a PDF file."""
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = []
        full_text_parts = []

        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            pages.append({"page": i + 1, "char_count": len(text)})
            full_text_parts.append(text)

        full_text = "\n".join(full_text_parts)
        word_count = len(full_text.split())

        # Extract metadata
        meta = reader.metadata
        metadata = {}
        if meta:
            metadata = {
                "title": str(meta.get("/Title", "")) if meta.get("/Title") else "",
                "author": str(meta.get("/Author", "")) if meta.get("/Author") else "",
                "subject": str(meta.get("/Subject", "")) if meta.get("/Subject") else "",
                "creator": str(meta.get("/Creator", "")) if meta.get("/Creator") else "",
            }

        return {
            "file_type": "pdf",
            "file_path": str(path),
            "page_count": len(reader.pages),
            "word_count": word_count,
            "pages": pages[:20],
            "metadata": metadata,
            "text": full_text[:5000],
        }

    async def _analyze_xlsx(self, path: Path) -> dict[str, Any]:
        """Extract data from an Excel spreadsheet."""
        from openpyxl import load_workbook

        wb = load_workbook(str(path), read_only=True, data_only=True)

        sheets: list[dict] = []
        total_rows = 0

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows_data = []
            for i, row in enumerate(ws.iter_rows(max_row=20, values_only=True)):
                cells = [str(c) if c is not None else "" for c in row]
                rows_data.append(cells)
            row_count = ws.max_row or 0
            total_rows += row_count
            sheets.append({
                "name": sheet_name,
                "rows": row_count,
                "columns": ws.max_column or 0,
                "preview": rows_data[:10],
            })

        wb.close()

        return {
            "file_type": "xlsx",
            "file_path": str(path),
            "sheet_count": len(sheets),
            "total_rows": total_rows,
            "sheets": sheets[:5],
        }

    # ── Document Creation (Writing) ──────────────────────────────

    async def create_report(
        self,
        title: str,
        sections: list[dict[str, Any]],
        author: str = "Praxis AI",
        subject: str = "",
        include_toc: bool = True,
        include_page_numbers: bool = True,
    ) -> str:
        """Create a professional enterprise .docx report.

        Args:
            title: Document title.
            sections: List of {heading, content, level} dicts.
                      content can be a string or list of strings (paragraphs).
                      level: 1 for H1, 2 for H2, 3 for H3 (default 1).
            author: Document author.
            subject: Document subject.
            include_toc: Whether to include a table of contents.
            include_page_numbers: Whether to add page numbers in footer.

        Returns:
            Path to the generated .docx file.
        """
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches, Pt, RGBColor

        doc = Document()

        # Set page size (US Letter) and margins
        for section in doc.sections:
            section.page_width = Inches(8.5)
            section.page_height = Inches(11)
            section.top_margin = Inches(1)
            section.bottom_margin = Inches(1)
            section.left_margin = Inches(1)
            section.right_margin = Inches(1)

        # Configure default font
        style = doc.styles["Normal"]
        style.font.name = DEFAULT_FONT
        style.font.size = Pt(DEFAULT_FONT_SIZE)
        style.paragraph_format.line_spacing = LINE_SPACING
        style.paragraph_format.space_after = Pt(SPACING_AFTER_PARAGRAPH / PT_TO_DXA)

        # Configure heading styles
        for level, size, color in [
            (1, HEADING1_SIZE, COLOR_PRIMARY),
            (2, HEADING2_SIZE, COLOR_ACCENT),
            (3, HEADING3_SIZE, COLOR_ACCENT),
        ]:
            heading_style = doc.styles[f"Heading {level}"]
            heading_style.font.name = DEFAULT_FONT
            heading_style.font.size = Pt(size)
            heading_style.font.bold = True
            heading_style.font.color.rgb = RGBColor.from_string(color)
            heading_style.paragraph_format.space_before = Pt(SPACING_BEFORE_HEADING / PT_TO_DXA)
            heading_style.paragraph_format.space_after = Pt(SPACING_AFTER_HEADING / PT_TO_DXA)

        # Title
        title_para = doc.add_paragraph()
        title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title_run = title_para.add_run(title)
        title_run.font.name = DEFAULT_FONT
        title_run.font.size = Pt(TITLE_SIZE)
        title_run.font.bold = True
        title_run.font.color.rgb = RGBColor.from_string(COLOR_PRIMARY)

        # Author/date line
        meta_para = doc.add_paragraph()
        meta_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        meta_run = meta_para.add_run(f"{author}  |  {datetime.now().strftime('%B %d, %Y')}")
        meta_run.font.name = DEFAULT_FONT
        meta_run.font.size = Pt(10)
        meta_run.font.color.rgb = RGBColor.from_string("666666")

        doc.add_paragraph()  # Spacer

        # Table of contents (if requested and enough sections)
        if include_toc and len(sections) > 3:
            toc_heading = doc.add_paragraph()
            toc_run = toc_heading.add_run("Table of Contents")
            toc_run.font.name = DEFAULT_FONT
            toc_run.font.size = Pt(HEADING2_SIZE)
            toc_run.font.bold = True
            toc_run.font.color.rgb = RGBColor.from_string(COLOR_ACCENT)

            for section in sections:
                if section.get("level", 1) <= 2:
                    level = section.get("level", 1)
                    indent = "    " * (level - 1)
                    toc_para = doc.add_paragraph()
                    toc_run = toc_para.add_run(f"{indent}{section['heading']}")
                    toc_run.font.name = DEFAULT_FONT
                    toc_run.font.size = Pt(DEFAULT_FONT_SIZE if level == 1 else 10)

            doc.add_page_break()

        # Content sections
        for section in sections:
            heading = section.get("heading", "")
            content = section.get("content", "")
            level = section.get("level", 1)

            if heading:
                doc.add_heading(heading, level=min(level, 3))

            if isinstance(content, str):
                content = [content]

            for paragraph_text in content:
                if isinstance(paragraph_text, dict):
                    # Table data
                    if paragraph_text.get("type") == "table":
                        self._add_table(doc, paragraph_text)
                    elif paragraph_text.get("type") == "bullet":
                        p = doc.add_paragraph(style="List Bullet")
                        p.add_run(str(paragraph_text.get("text", "")))
                    elif paragraph_text.get("type") == "numbered":
                        p = doc.add_paragraph(style="List Number")
                        p.add_run(str(paragraph_text.get("text", "")))
                else:
                    p = doc.add_paragraph(str(paragraph_text))

        # Footer with page numbers
        if include_page_numbers:
            for section in doc.sections:
                footer = section.footer
                footer_para = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
                footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                footer_para.text = f"{title} — Page "

                # Add page number field
                run = footer_para.add_run()
                fldChar1 = self._make_field_char("begin")
                instrText = self._make_instr_text("PAGE")
                fldChar2 = self._make_field_char("end")
                run._r.append(fldChar1)
                run._r.append(instrText)
                run._r.append(fldChar2)

        # Set core properties
        doc.core_properties.author = author
        doc.core_properties.title = title
        doc.core_properties.subject = subject
        doc.core_properties.created = datetime.now()

        # Save
        filename = self._safe_filename(title) + ".docx"
        file_path = self.output_dir / filename
        doc.save(str(file_path))

        logger.info("document.created type=report title=%s path=%s", title[:50], file_path)
        return str(file_path)

    async def create_memo(
        self,
        to: str,
        sender: str,
        subject: str,
        body: str,
        date: str | None = None,
    ) -> str:
        """Create a professional memo .docx file."""
        sections = [
            {"heading": "MEMORANDUM", "content": [], "level": 1},
            {"heading": "", "content": [
                f"To: {to}",
                f"From: {sender}",
                f"Date: {date or datetime.now().strftime('%B %d, %Y')}",
                f"Subject: {subject}",
            ], "level": 1},
            {"heading": "", "content": [body], "level": 1},
        ]
        return await self.create_report(
            title=f"Memo: {subject}",
            sections=sections,
            author=sender,
            subject=subject,
            include_toc=False,
            include_page_numbers=False,
        )

    async def create_letter(
        self,
        recipient_name: str,
        recipient_address: str,
        sender_name: str,
        sender_address: str,
        subject: str,
        body: str,
        closing: str = "Sincerely",
    ) -> str:
        """Create a professional business letter .docx file."""
        sections = [
            {"heading": "", "content": [sender_address], "level": 1},
            {"heading": "", "content": [datetime.now().strftime("%B %d, %Y")], "level": 1},
            {"heading": "", "content": [f"{recipient_name}\n{recipient_address}"], "level": 1},
            {"heading": f"RE: {subject}", "content": [], "level": 1},
            {"heading": "", "content": [f"Dear {recipient_name},"], "level": 1},
            {"heading": "", "content": body.split("\n"), "level": 1},
            {"heading": "", "content": [closing + ",", sender_name], "level": 1},
        ]
        return await self.create_report(
            title=f"Letter: {subject}",
            sections=sections,
            author=sender_name,
            subject=subject,
            include_toc=False,
            include_page_numbers=False,
        )

    # ── Helpers ──────────────────────────────────────────────────

    def _add_table(self, doc: Any, table_data: dict) -> None:
        """Add a formatted table to the document."""
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.shared import Pt

        headers = table_data.get("headers", [])
        rows = table_data.get("rows", [])

        if not headers and not rows:
            return

        num_cols = len(headers) if headers else (len(rows[0]) if rows else 0)
        if num_cols == 0:
            return

        table = doc.add_table(rows=1 + len(rows), cols=num_cols)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER

        # Style the table
        try:
            table.style = "Table Grid"
        except Exception:
            pass

        # Header row
        for i, header in enumerate(headers):
            cell = table.rows[0].cells[i]
            cell.text = str(header)
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.bold = True
                    run.font.name = DEFAULT_FONT
                    run.font.size = Pt(DEFAULT_FONT_SIZE)
            # Shading
            self._set_cell_shading(cell, COLOR_TABLE_HEADER)

        # Data rows
        for row_idx, row_data in enumerate(rows):
            for col_idx, value in enumerate(row_data):
                if col_idx < num_cols:
                    cell = table.rows[row_idx + 1].cells[col_idx]
                    cell.text = str(value) if value is not None else ""
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            run.font.name = DEFAULT_FONT
                            run.font.size = Pt(DEFAULT_FONT_SIZE)

    def _set_cell_shading(self, cell: Any, color: str) -> None:
        """Set background shading on a table cell."""
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        shading = OxmlElement("w:shd")
        shading.set(qn("w:fill"), color)
        shading.set(qn("w:val"), "clear")
        cell._tc.get_or_add_tcPr().append(shading)

    def _make_field_char(self, field_type: str) -> Any:
        """Create a Word field character element (for page numbers)."""
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        char = OxmlElement("w:fldChar")
        char.set(qn("w:fldCharType"), field_type)
        return char

    def _make_instr_text(self, instr: str) -> Any:
        """Create a Word instruction text element."""
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        text = OxmlElement("w:instrText")
        text.set(qn("xml:space"), "preserve")
        text.text = f" {instr} "
        return text

    def _safe_filename(self, title: str) -> str:
        """Convert a title to a safe filename."""
        safe = re.sub(r"[^\w\s-]", "", title).strip()
        safe = re.sub(r"[-\s]+", "-", safe).lower()
        return safe[:80] if safe else "document"
