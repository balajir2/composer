"""LLM-facing catalog of every Composer node type.

Derived by hand from src/engine/workflow.py's Pydantic models — one curated
purpose line and credential hint per type. The test above asserts this stays
in sync with the node-type set so a new node type can't be silently missing
from the builder's vocabulary.
"""

import re
from collections.abc import Mapping
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

NODE_PURPOSES: dict[str, str] = {
    "start": "workflow entry; declares input variables the caller must supply",
    "end": "workflow termination; may capture a final output",
    "note": "canvas annotation only; never executes",
    "file-trigger": "folder-watch / cloud-storage trigger config (visual only)",
    "file-write": "write generated content to a file (md/docx/pdf/html)",
    "agent": "multi-turn LLM agent with tools, MCP, and structured output",
    "mcp": "call tools from a configured MCP server (agent or single-call mode)",
    "http": "call an external HTTP API with method/headers/body",
    "extract": "single-shot structured extraction into a JSON schema",
    "transform": "run sandboxed Python (E2B) to transform state",
    "data-transform": "map/filter/reduce over a collection via expressions",
    "set-state": "write a literal or expression value into a variable",
    "if-else": "branch on a boolean expression (true/false)",
    "decision": "LLM/judgment-based branching (binary or N-way choice)",
    "while": "bounded loop (body/exit) over a boolean expression",
    "user-approval": "pause for a human approve/reject decision",
    "guardrails": "LLM PII/moderation/jailbreak/hallucination classifiers",
    "vector-db": "query or upsert a vector store (Pinecone/Qdrant/Chroma/Weaviate/Milvus)",
    "gamma-ai": "generate a slide deck or document via Gamma",
    "email": "send email via Resend",
    "arcade": "call an Arcade tool",
    "join-chunks": "concatenate text chunks with separator/prefix/suffix",
    "jira": "create/search/update/transition/comment Jira issues",
    "confluence": "create/update/get Confluence pages and properties",
    "download-pdf": "render HTML/Markdown to PDF and write via a storage provider",
    "join": "converge multiple branches into one downstream path",
}

NODE_CREDENTIALS: dict[str, list[str]] = {
    "agent": ["llm-provider"],
    "mcp": ["mcp-server"],
    "extract": ["llm-provider"],
    "decision": ["llm-provider"],
    "guardrails": ["llm-provider"],
    "vector-db": ["vector-db"],
    "gamma-ai": ["tool-provider"],
    "email": ["resend"],
    "arcade": ["tool-provider"],
    "jira": ["jira"],
    "confluence": ["confluence"],
    "file-trigger": ["google-drive"],
    "download-pdf": ["google-drive"],
}


def build_node_catalog() -> str:
    lines: list[str] = []
    for node_type, purpose in sorted(NODE_PURPOSES.items()):
        creds = ", ".join(NODE_CREDENTIALS.get(node_type, [])) or "none"
        lines.append(f"- `{node_type}` — {purpose} (credentials: {creds})")
    return "\n".join(lines)


# label is its own create_node argument; nodeName/nodeType are UI bookkeeping.
_GUIDE_SKIP = {"label", "nodeName", "nodeType"}
# Credential fields stay out of the guide: listing them invites the model to
# invent values, and credentials must come from the user (request_credential).
# Deliberately narrow: plain "...Key" fields such as stateKey are real settings.
_SECRET_FIELD = re.compile(r"(?i)token|apikey|secret|password")
# Bare `domain` / `email` (Jira, Confluence) name the account that owns the API
# token. They are connection identity, not workflow settings, so they come from
# the user with the token. Exact names only: emailTo, approverEmail are settings.
_CONNECTION_IDENTITY = {"domain", "email"}


def is_hidden_from_builder(field_name: str) -> bool:
    """True for credential and connection-identity fields the model must not fill or see."""
    return field_name in _CONNECTION_IDENTITY or bool(_SECRET_FIELD.search(field_name))


def _describe_type(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        inner = [a for a in get_args(annotation) if a is not type(None)]
        return _describe_type(inner[0]) if len(inner) == 1 else "value"
    if origin is Literal:
        return "|".join(str(a) for a in get_args(annotation))
    if annotation is bool:
        return "bool"
    if annotation is str:
        return "text"
    if annotation in (int, float):
        return "number"
    if annotation is list or origin is list:
        return "list"
    return "object"


def build_field_guide(node_classes: Mapping[str, type[BaseModel]]) -> str:
    """One line per node type: its `data` keys (camelCase), `*` = required.

    Generated from the Pydantic models so the prompt cannot drift from what
    create_node actually validates.
    """
    lines: list[str] = []
    for node_type in sorted(node_classes):
        data_cls = node_classes[node_type].model_fields["data"].annotation
        if not (isinstance(data_cls, type) and issubclass(data_cls, BaseModel)):
            continue
        fields = [
            f"{info.alias or name}{'*' if info.is_required() else ''} "
            f"({_describe_type(info.annotation)})"
            for name, info in data_cls.model_fields.items()
            if (info.alias or name) not in _GUIDE_SKIP
            and not is_hidden_from_builder(info.alias or name)
        ]
        lines.append(f"- `{node_type}`: " + (", ".join(fields) or "no fields"))
    return "\n".join(lines)


def describe_models(catalog: Mapping[str, str | None] | None) -> str:
    if not catalog:
        return "(none listed: leave `model` unset so the deployment default is used)"
    return "\n".join(
        f"- `{model_id}`" + (f" ({label})" if label else "")
        for model_id, label in sorted(catalog.items())
    )
