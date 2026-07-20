"""FileWriteExecutor — the `file-write` node type.

Writes substituted content to a file via a FileStorageProvider, converting
Markdown -> DOCX/PDF as needed. Only Markdown is implemented so far; the
docx/pdf conversion modules land in later tasks (see docs/archive/
phase-history/plans/2026-07-11-file-storage-provider-framework-plan.md,
Tasks 6-7).
"""

from pathlib import Path
from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import FileWriteNode
from src.executors.base import register_executor
from src.storage_providers.base import FileStorageProvider
from src.storage_providers.local import LocalFilesystemProvider
from src.variable_substitution import substitute

_PROVIDERS: dict[str, type[FileStorageProvider]] = {
    "local": LocalFilesystemProvider,
}

_EXTENSIONS = {"md": "md", "docx": "docx", "pdf": "pdf", "html": "html"}


class UnknownStorageProviderError(ValueError):
    """Raised when a file-write/file-trigger node names an unregistered provider."""


class InvalidDestinationError(ValueError):
    """Raised when a file-write node's substituted destinationPath is empty
    or blank. Path("") resolves to the process's current working directory,
    so silently accepting it would let the node write into whatever
    directory the server happened to be launched from — fail loudly instead.
    """


class InvalidFilenameError(ValueError):
    """Raised when a file-write node's substituted filename is not a bare
    filename — e.g. it contains a path separator, is a `..` traversal
    segment, or is empty.

    `filename` and `destinationPath` are both substituted from workflow
    state (see src/variable_substitution.py's `substitute()`), which can
    carry prior node outputs (http/agent/extract/mcp results) originating
    from untrusted external data — mirroring why src/executors/http.py
    validates substituted URLs via validate_outbound_url() before use.
    A bare filename can never legitimately need path segments; a workflow
    that wants a nested output directory should express that via
    destinationPath instead.
    """


def _validate_filename(node_id: str, filename: str) -> None:
    if not filename or not filename.strip():
        raise InvalidFilenameError(f"file-write node {node_id!r} resolved to an empty filename")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise InvalidFilenameError(
            f"file-write node {node_id!r} filename {filename!r} must be a bare filename "
            "with no path separators or '..' segments — use destinationPath to control "
            "the output directory"
        )


@register_executor("file-write")
class FileWriteExecutor:
    def __init__(self, node: FileWriteNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        provider_cls = _PROVIDERS.get(self.node.data.provider)
        if provider_cls is None:
            raise UnknownStorageProviderError(
                f"file-write node {self.node.id!r} has unknown provider "
                f"{self.node.data.provider!r}; registered: {sorted(_PROVIDERS)}"
            )

        destination = substitute(self.node.data.destination_path or "", state)
        if not destination or not destination.strip():
            raise InvalidDestinationError(
                f"file-write node {self.node.id!r} resolved to an empty destinationPath; "
                "refusing to write into the process's current working directory"
            )

        filename = substitute(self.node.data.filename or "output", state)
        _validate_filename(self.node.id, filename)

        content = substitute(self.node.data.content or "", state)
        fmt = self.node.data.format

        converted = _convert(content, fmt)
        ext = _EXTENSIONS[fmt]
        full_filename = f"{filename}.{ext}"

        provider = provider_cls()
        await provider.write_file(destination, full_filename, converted)

        # str(Path(...)) rather than naive f-string join: normalizes to the
        # platform's native separator so this matches str(tmp_path / name)
        # in tests (and the paths LocalFilesystemProvider itself writes to)
        # on both POSIX and Windows.
        written_path = str(Path(destination) / full_filename)
        return {
            "variables": {"lastOutput": written_path},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"filename": full_filename, "format": fmt},
                    "output": written_path,
                }
            },
        }


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


__all__ = [
    "FileWriteExecutor",
    "InvalidDestinationError",
    "InvalidFilenameError",
    "UnknownStorageProviderError",
]
