import asyncio

import pytest

from src.telemetry.context import bind_context, current_correlation


def test_context_nested_reset_and_rejects_unknown_fields() -> None:
    with bind_context(request_id="outer"):
        with bind_context(request_id="inner", execution_id="e"):
            assert current_correlation().request_id == "inner"
        assert current_correlation().request_id == "outer"
        assert current_correlation().execution_id is None
    assert current_correlation().request_id is None
    with pytest.raises((TypeError, ValueError)), bind_context(payload="secret"):
        pass


@pytest.mark.asyncio
async def test_context_isolated_across_tasks_and_cleaned_on_cancel() -> None:
    async def worker(value: str) -> str | None:
        with bind_context(request_id=value):
            await asyncio.sleep(0)
            return current_correlation().request_id

    assert await asyncio.gather(worker("a"), worker("b")) == ["a", "b"]
    assert current_correlation().request_id is None

    async def cancelled() -> None:
        with bind_context(request_id="cancelled"):
            await asyncio.sleep(100)

    task = asyncio.create_task(cancelled())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert current_correlation().request_id is None
