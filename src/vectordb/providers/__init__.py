"""Vector-db provider implementations (Phase 6e).

Each provider module exports a top-level async function:

    async def query(
        embedding: list[float],
        config: QueryConfig,
    ) -> list[VectorDbResult]:
        ...

The executor holds a dict map {provider_name: query_fn} for dispatch.
See ADR-0020.
"""
