from typing import Any

from src.builder.diagnostics import REDACT_MARKER, build_diagnosis


class _FakeDb:
    def __init__(self, row: Any) -> None:
        self._row: Any = row
        self.workflowexecution = self

    async def find_unique(self, **kwargs: Any) -> Any:
        return self._row


def _row():
    return type(
        "Row",
        (),
        {
            "status": "failed",
            "error": "boom",
            "nodeResults": {"a": {"output": "x"}},
            "variables": {"apiKey": "SECRET", "safe": "ok"},
        },
    )()


async def test_build_diagnosis_redacts_secret_keys():
    diag = await build_diagnosis(_FakeDb(_row()), "exec-1")
    assert diag is not None
    assert diag.status == "failed"
    assert diag.error == "boom"
    assert diag.variables_tail["apiKey"] == REDACT_MARKER
    assert diag.variables_tail["safe"] == "ok"


async def test_build_diagnosis_returns_none_for_missing_row():
    assert await build_diagnosis(_FakeDb(None), "nope") is None
