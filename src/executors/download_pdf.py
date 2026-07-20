"""DownloadPdfExecutor — the `download-pdf` node type.

Renders HTML or Markdown content to a PDF file and writes it via a
FileStorageProvider — the PDF-producing counterpart to `file-write`, but
as its own dedicated node type (more discoverable in the palette than a
buried file-write format option) since PDF rendering needs an explicit
inputFormat choice rather than sharing file-write's simple format
dropdown.

Destination/filename validation intentionally mirrors file_write.py's
identical guards as independent, local code rather than a cross-module
import — the logic is small (~10 lines) and this keeps the two node
types' modules decoupled. The SSRF-blocking PDF-rendering guarantee
(src/conversion/_pdf_security.py), which IS shared, is the one piece
where sharing matters: that's substantial, security-critical code where
a second copy could drift.
"""

from pathlib import Path
from typing import Any

from src.conversion.html_to_pdf import html_to_pdf
from src.conversion.markdown_to_pdf import markdown_to_pdf
from src.engine.state import WorkflowStateDict
from src.engine.workflow import DownloadPdfNode
from src.executors.base import register_executor
from src.storage_providers.base import FileStorageProvider
from src.storage_providers.local import LocalFilesystemProvider
from src.variable_substitution import substitute

_PROVIDERS: dict[str, type[FileStorageProvider]] = {
    "local": LocalFilesystemProvider,
}


class UnknownStorageProviderError(ValueError):
    """Raised when a download-pdf node names an unregistered provider."""


class InvalidDestinationError(ValueError):
    """Raised when a download-pdf node's substituted destinationPath is empty
    or blank. Path("") resolves to the process's current working directory,
    so silently accepting it would let the node write into whatever
    directory the server happened to be launched from — fail loudly instead.
    """


class InvalidFilenameError(ValueError):
    """Raised when a download-pdf node's substituted filename is not a bare
    filename — e.g. it contains a path separator, is a `..` traversal
    segment, or is empty.

    `filename` and `destinationPath` are both substituted from workflow
    state, which can carry prior node outputs (http/agent/extract/mcp
    results) originating from untrusted external data.
    """


def _validate_filename(node_id: str, filename: str) -> None:
    if not filename or not filename.strip():
        raise InvalidFilenameError(f"download-pdf node {node_id!r} resolved to an empty filename")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise InvalidFilenameError(
            f"download-pdf node {node_id!r} filename {filename!r} must be a bare filename "
            "with no path separators or '..' segments — use destinationPath to control "
            "the output directory"
        )


@register_executor("download-pdf")
class DownloadPdfExecutor:
    def __init__(self, node: DownloadPdfNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        provider_cls = _PROVIDERS.get(self.node.data.provider)
        if provider_cls is None:
            raise UnknownStorageProviderError(
                f"download-pdf node {self.node.id!r} has unknown provider "
                f"{self.node.data.provider!r}; registered: {sorted(_PROVIDERS)}"
            )

        destination = substitute(self.node.data.destination_path or "", state)
        if not destination or not destination.strip():
            raise InvalidDestinationError(
                f"download-pdf node {self.node.id!r} resolved to an empty destinationPath; "
                "refusing to write into the process's current working directory"
            )

        filename = substitute(self.node.data.filename or "output", state)
        _validate_filename(self.node.id, filename)

        content = substitute(self.node.data.content or "", state)

        if self.node.data.input_format == "markdown":
            pdf_bytes = markdown_to_pdf(content)
        else:
            pdf_bytes = html_to_pdf(content)

        full_filename = f"{filename}.pdf"
        provider = provider_cls()
        await provider.write_file(destination, full_filename, pdf_bytes)

        written_path = str(Path(destination) / full_filename)
        return {
            "variables": {"lastOutput": written_path},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {
                        "filename": full_filename,
                        "inputFormat": self.node.data.input_format,
                    },
                    "output": written_path,
                }
            },
        }


__all__ = [
    "DownloadPdfExecutor",
    "InvalidDestinationError",
    "InvalidFilenameError",
    "UnknownStorageProviderError",
]
