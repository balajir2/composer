"""Markdown -> DOCX renderer for the file-write node.

Purpose-built for the structured, table-heavy document shape this node
targets (BRDs and similar) — not a general-purpose, full-fidelity Markdown
converter. Walks markdown-it-py's token stream and maps the constructs that
matter for that shape (headings, paragraphs, bullet/numbered lists, tables,
bold/italic) onto python-docx calls. See
docs/archive/phase-history/plans/2026-07-11-file-storage-provider-framework-plan.md
Task 6 for the scope/fidelity trade-off this makes against a pandoc-based
approach.
"""

import io
from typing import TYPE_CHECKING, Any

from docx import Document
from markdown_it import MarkdownIt
from markdown_it.token import Token

if TYPE_CHECKING:
    from docx.document import Document as DocumentType

# `html: False` disables CommonMark's raw-HTML passthrough rules
# (html_block/html_inline). Without it, bracket placeholders extremely
# common in the BRD-style documents this node targets -- `<Client Name>`,
# `<INSERT DATE>` -- parse as (unrecognized) `html_inline` tokens, which
# `_add_inline_runs` below has no handler for and silently drops, instead
# of rendering as literal text. See docs/archive/phase-history/plans/
# 2026-07-11-file-storage-provider-framework-plan.md Task 6/7 follow-up.
_md = MarkdownIt("commonmark", {"html": False}).enable("table")


def _add_inline_runs(paragraph: Any, children: list[Token]) -> None:
    """Render inline tokens (text/strong/em) onto a docx paragraph as runs."""
    bold = False
    italic = False
    for child in children:
        if child.type == "strong_open":
            bold = True
        elif child.type == "strong_close":
            bold = False
        elif child.type == "em_open":
            italic = True
        elif child.type == "em_close":
            italic = False
        elif child.type == "text":
            run = paragraph.add_run(child.content)
            run.bold = bold
            run.italic = italic
        elif child.type == "code_inline":
            run = paragraph.add_run(child.content)
            run.bold = bold
            run.italic = italic
            run.font.name = "Consolas"
        elif child.type in ("softbreak", "hardbreak"):
            paragraph.add_run(" ")


def markdown_to_docx(content: str) -> bytes:
    tokens = _md.parse(content)
    doc: DocumentType = Document()

    i = 0
    list_stack: list[str] = []  # tracks "bullet" | "ordered" for nesting depth
    while i < len(tokens):
        tok = tokens[i]

        if tok.type == "heading_open":
            level = int(tok.tag[1])  # "h1" -> 1
            inline = tokens[i + 1]
            para = doc.add_heading(level=min(level, 9))
            _add_inline_runs(para, inline.children or [])
            i += 3  # heading_open, inline, heading_close
            continue

        if tok.type == "paragraph_open":
            # Inside a list item, markdown-it-py still wraps the item's text
            # in paragraph_open/inline/paragraph_close (just marked
            # `hidden=True` for HTML rendering) — it does NOT emit a bare
            # `inline` token directly under list_item_open. Verified against
            # the installed markdown-it-py directly; do not "simplify" this
            # back to a standalone `inline`-token branch, it won't fire.
            inline = tokens[i + 1]
            if list_stack:
                style = "List Bullet" if list_stack[-1] == "bullet" else "List Number"
                para = doc.add_paragraph(style=style)
            else:
                para = doc.add_paragraph()
            _add_inline_runs(para, inline.children or [])
            i += 3
            continue

        if tok.type in ("bullet_list_open", "ordered_list_open"):
            list_stack.append("bullet" if tok.type == "bullet_list_open" else "ordered")
            i += 1
            continue
        if tok.type in ("bullet_list_close", "ordered_list_close"):
            list_stack.pop()
            i += 1
            continue

        if tok.type == "list_item_open":
            i += 1
            continue
        if tok.type == "list_item_close":
            i += 1
            continue

        if tok.type == "table_open":
            # Cells hold their *parsed* inline children here (not the raw
            # `.content` source string) so `_add_inline_runs` can strip
            # markdown syntax and apply bold/italic run formatting exactly
            # like headings/paragraphs/list items do below.
            rows: list[list[list[Token]]] = []
            j = i + 1
            while tokens[j].type != "table_close":
                if tokens[j].type == "tr_open":
                    row: list[list[Token]] = []
                    k = j + 1
                    while tokens[k].type != "tr_close":
                        if tokens[k].type == "inline":
                            row.append(tokens[k].children or [])
                        k += 1
                    rows.append(row)
                    j = k
                j += 1
            if rows:
                table = doc.add_table(rows=len(rows), cols=len(rows[0]))
                table.style = "Table Grid"
                for r_idx, row in enumerate(rows):
                    for c_idx, cell_children in enumerate(row):
                        cell_para = table.rows[r_idx].cells[c_idx].paragraphs[0]
                        _add_inline_runs(cell_para, cell_children)
            i = j + 1
            continue

        i += 1

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


__all__ = ["markdown_to_docx"]
