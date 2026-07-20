# File-Write HTML Format Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fourth `file-write` format, `html`, that writes content bytes verbatim to a `.html` file — no Markdown parsing, no sanitization, unlike the existing `md`/`docx`/`pdf` formats which all route through a Markdown converter.

**Architecture:** One new branch in the existing `_convert()` dispatch, identical in shape to the existing `md` branch (raw UTF-8 encode, no transformation). One new `Literal` member on the schema. One new dropdown option in the Designer panel. No new files, no new abstractions — this is the smallest possible platform change.

**Tech Stack:** Python 3.11, Pydantic v2, pytest (backend). Next.js/React, vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-07-20-feature2-pdf-html-foundation-design.md` (Component 1).

---

## Scope note (platform vs. customer flow)

Per the platform/customer-flow boundary established in earlier work on this project: this format is generic — any workflow can use it for any HTML content. Nothing here references Macy's, Feature 2, or any specific report structure.

---

### Task 1: Backend — add the `html` format

**Files:**
- Modify: `src/engine/workflow.py:145` (`FileWriteNodeData.format`'s `Literal`)
- Modify: `src/executors/file_write.py` (`_EXTENSIONS` map, `_convert()`)
- Test: `tests/unit/executors/test_file_write.py`

**Step 1: Write the failing test**

Append to `tests/unit/executors/test_file_write.py`:

```python
async def test_writes_html_file_verbatim_no_conversion(tmp_path: Path) -> None:
    """Unlike md/docx/pdf, html is a raw passthrough — no Markdown parsing,
    no HTML stripping. Content containing real tags and a <style> block
    must survive byte-for-byte."""
    from src.engine.state import initial_state
    from src.executors.file_write import FileWriteExecutor

    state = initial_state()
    state["variables"]["name"] = "Ada"
    content = (
        "<!doctype html><html><head><style>@media print { .p { break-before: page; } "
        "}</style></head><body><h1>Report for {{name}}</h1><p class=\"p\">Body</p></body></html>"
    )
    node = _node(tmp_path, format="html", content=content)
    delta = await FileWriteExecutor(node).arun(state)

    written = tmp_path / "report.html"
    expected = content.replace("{{name}}", "Ada")
    assert written.read_text(encoding="utf-8") == expected
    assert delta["variables"]["lastOutput"] == str(written)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/executors/test_file_write.py -k html -v` (from the repo root; there's no local `.venv` if you're in a worktree — use the main repo's at `/d/GitHub/composer/.venv/Scripts/python.exe`; try `uv run pytest ...` first if `uv` is on your PATH)

Expected: FAIL — `KeyError: 'html'` (the `_EXTENSIONS` map has no `"html"` key yet), or a Pydantic `ValidationError` if the schema rejects `format="html"` first (whichever surfaces first).

- [ ] **Step 3: Add the schema literal**

In `src/engine/workflow.py`, at `FileWriteNodeData` (currently line 145):

```python
    format: Literal["md", "docx", "pdf", "html"] = "md"
```

- [ ] **Step 4: Add the executor branch**

In `src/executors/file_write.py`, update the `_EXTENSIONS` map:

```python
_EXTENSIONS = {"md": "md", "docx": "docx", "pdf": "pdf", "html": "html"}
```

And in `_convert()`, add a branch identical in shape to the existing `md` branch:

```python
def _convert(content: str, fmt: str) -> bytes:
    if fmt == "md":
        return content.encode("utf-8")
    if fmt == "html":
        return content.encode("utf-8")
    if fmt == "docx":
        from src.conversion.markdown_to_docx import markdown_to_docx

        return markdown_to_docx(content)
    if fmt == "pdf":
        from src.conversion.markdown_to_pdf import markdown_to_pdf

        return markdown_to_pdf(content)
    raise UnknownStorageProviderError(f"unknown format {fmt!r}")  # unreachable given Literal typing
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/executors/test_file_write.py -v`

Expected: PASS (all tests, including the new one and every pre-existing `md`/`docx`/`pdf` test — this is a purely additive change)

- [ ] **Step 6: Run the full backend test suite and lint/format checks**

Run: `.venv/Scripts/python.exe -m pytest -m "not integration" -q && .venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m ruff format --check src tests`

Expected: all clean. If `ruff format --check` flags anything, run `.venv/Scripts/python.exe -m ruff format <file>` on the affected file(s) and re-verify — don't skip this, it caused a real CI failure earlier on this project.

- [ ] **Step 7: Commit**

```bash
git add src/engine/workflow.py src/executors/file_write.py tests/unit/executors/test_file_write.py
git commit -m "$(cat <<'EOF'
feat(file-write): add raw html output format

Unlike md/docx/pdf, html is a passthrough - content bytes written
verbatim, no Markdown parsing, no HTML stripping. Needed for workflows
that produce genuine HTML with embedded charts/print CSS (the
Markdown-to-X converters deliberately block raw HTML for security,
which is the wrong tradeoff when the content IS meant to be HTML).

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Designer panel — add the dropdown option

**Files:**
- Modify: `frontend/components/composer/canvas/node-panels/file-write.tsx`
- Test: `frontend/components/composer/canvas/node-panels/file-write.test.tsx` (new — no test file currently exists for this panel)

- [ ] **Step 1: Write the failing test**

Create `frontend/components/composer/canvas/node-panels/file-write.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import FileWritePanel from "./file-write";

describe("FileWritePanel", () => {
  it("offers html as a format option and calls onChange when selected", () => {
    const onChange = vi.fn();
    render(<FileWritePanel data={{}} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Format"), {
      target: { value: "html" },
    });
    expect(onChange).toHaveBeenCalledWith({ format: "html" });
  });

  it("defaults to md and shows the existing format options too", () => {
    render(<FileWritePanel data={{}} onChange={vi.fn()} />);
    const select = screen.getByLabelText("Format") as HTMLSelectElement;
    expect(select.value).toBe("md");
    const optionValues = Array.from(select.options).map((o) => o.value);
    expect(optionValues).toEqual(["md", "docx", "pdf", "html"]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run components/composer/canvas/node-panels/file-write.test.tsx`

Expected: FAIL — the `html` option doesn't exist in the select yet, so `optionValues` won't match and the `fireEvent.change` assertion fails.

- [ ] **Step 3: Add the dropdown option**

In `frontend/components/composer/canvas/node-panels/file-write.tsx`, update the Format `NativeSelect`'s `options` array:

```tsx
            <NativeSelect
              id="fw-format"
              value={(data.format as string) ?? "md"}
              onValueChange={(v) => onChange({ format: v })}
              options={[
                { value: "md", label: "Markdown (.md)" },
                { value: "docx", label: "Word (.docx)" },
                { value: "pdf", label: "PDF (.pdf)" },
                { value: "html", label: "HTML (.html)" },
              ]}
            />
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run components/composer/canvas/node-panels/file-write.test.tsx`

Expected: PASS

- [ ] **Step 5: Run the full frontend test suite and type check**

Run (from `frontend/`): `npx vitest run && npx tsc --noEmit`

Expected: PASS, no regressions, no new type errors

- [ ] **Step 6: Commit**

```bash
git add frontend/components/composer/canvas/node-panels/file-write.tsx frontend/components/composer/canvas/node-panels/file-write.test.tsx
git commit -m "$(cat <<'EOF'
feat(file-write-panel): add HTML format option to the dropdown

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review notes

- **Spec coverage:** Component 1 of the spec (raw HTML passthrough format, schema + executor + panel) is fully covered by Tasks 1-2.
- **Placeholder scan:** none found — every step has complete code.
- **Type consistency:** `format: "html"` (Python `Literal` member), `_EXTENSIONS["html"]`, and the frontend dropdown's `value: "html"` all agree.
