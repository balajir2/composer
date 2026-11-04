from langchain_core.tools import BaseTool

from src.builder.builder_tools import BuilderContext, make_builder_tools
from src.builder.draft import Draft


def _tools(ctx: BuilderContext) -> dict[str, BaseTool]:
    return {t.name: t for t in make_builder_tools(ctx)}


def test_create_and_connect_build_a_valid_draft() -> None:
    ctx = BuilderContext(draft=Draft(), events=[])
    t = _tools(ctx)
    assert "agent" in t["create_node"].invoke(
        {"node_type": "agent", "node_id": "a", "label": "A", "data": {}}
    )
    assert "ok" in t["create_node"].invoke(
        {"node_type": "start", "node_id": "s", "label": "S", "data": {}}
    )
    assert "ok" in t["create_node"].invoke(
        {"node_type": "end", "node_id": "e", "label": "E", "data": {}}
    )
    assert "ok" in t["connect_nodes"].invoke({"source": "s", "target": "a"})
    assert "ok" in t["connect_nodes"].invoke({"source": "a", "target": "e"})
    nodes, edges = ctx.draft.snapshot()
    assert {n.id for n in nodes} == {"s", "a", "e"}
    assert {(e.source, e.target) for e in edges} == {("s", "a"), ("a", "e")}


def test_create_node_with_bad_type_returns_error() -> None:
    ctx = BuilderContext(draft=Draft(), events=[])
    t = _tools(ctx)
    r = t["create_node"].invoke({"node_type": "nope", "node_id": "x", "label": "X", "data": {}})
    assert r.startswith("ERROR")


def test_request_credential_appends_event() -> None:
    ctx = BuilderContext(draft=Draft(), events=[])
    t = _tools(ctx)
    r = t["request_credential"].invoke(
        {"kind": "vector-db", "node_id": "a", "detail": "needs a Pinecone key"}
    )
    assert "credential" in r.lower()
    assert ctx.events == [
        {
            "type": "credential_request",
            "kind": "vector-db",
            "node_id": "a",
            "detail": "needs a Pinecone key",
            "resolvable_by": "user",
        }
    ]


def test_connect_nodes_ids_do_not_collide_after_removal() -> None:
    ctx = BuilderContext(draft=Draft(), events=[])
    t = _tools(ctx)
    for nid, ntype in [
        ("s", "start"),
        ("a", "agent"),
        ("b", "agent"),
        ("c", "agent"),
        ("d", "agent"),
        ("e", "end"),
    ]:
        t["create_node"].invoke(
            {"node_type": ntype, "node_id": nid, "label": nid.upper(), "data": {}}
        )
    t["connect_nodes"].invoke({"source": "a", "target": "b"})
    t["connect_nodes"].invoke({"source": "b", "target": "c"})
    t["connect_nodes"].invoke({"source": "c", "target": "d"})
    t["remove_node"].invoke({"node_id": "b"})
    t["connect_nodes"].invoke({"source": "a", "target": "c"})
    t["remove_node"].invoke({"node_id": "d"})
    t["connect_nodes"].invoke({"source": "a", "target": "e"})
    _, edges = ctx.draft.snapshot()
    assert {(e.source, e.target) for e in edges} == {("a", "c"), ("a", "e")}


def test_create_node_error_lists_valid_types_for_unknown_type() -> None:
    ctx = BuilderContext(draft=Draft(), events=[])
    r = _tools(ctx)["create_node"].invoke(
        {"node_type": "tavily", "node_id": "x", "label": "X", "data": {}}
    )
    assert r.startswith("ERROR")
    assert "agent" in r and "mcp" in r  # the model sees what it could have used


def test_create_node_error_explains_bad_node_id() -> None:
    ctx = BuilderContext(draft=Draft(), events=[])
    r = _tools(ctx)["create_node"].invoke(
        {"node_type": "agent", "node_id": "has space", "label": "X", "data": {}}
    )
    assert r.startswith("ERROR")
    assert "node_id" in r


def test_create_node_error_names_the_invalid_field() -> None:
    ctx = BuilderContext(draft=Draft(), events=[])
    r = _tools(ctx)["create_node"].invoke(
        {"node_type": "agent", "node_id": "a", "label": "A", "data": {"maxIterations": "lots"}}
    )
    assert r.startswith("ERROR")
    assert "maxIterations" in r
