"""Local test runs must not send LangSmith traces.

A developer's .env can set LANGCHAIN_TRACING_V2=true; tests/conftest.py forces tracing off
before load_dotenv() so that value never reaches a test run (it used up the trace quota).
"""

import os

from src.config import Settings


def test_tracing_variables_are_off_during_tests() -> None:
    if os.environ.get("COMPOSER_TEST_TRACING") == "1":
        return
    for name in ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGSMITH_TRACING_V2"):
        assert os.environ.get(name) == "false", name


def test_settings_see_tracing_off_even_with_a_tracing_dotenv() -> None:
    if os.environ.get("COMPOSER_TEST_TRACING") == "1":
        return
    assert Settings().langchain_tracing_v2 is False
