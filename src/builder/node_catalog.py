"""LLM-facing catalog of every Composer node type.

Derived by hand from src/engine/workflow.py's Pydantic models — one curated
purpose line and credential hint per type. The test above asserts this stays
in sync with the node-type set so a new node type can't be silently missing
from the builder's vocabulary.
"""

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
