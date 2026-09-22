"""Regression guard: TypeSafe must never be wired into the shared LLM
dispatcher. If this test starts failing because someone added a
"typesafe" branch to _build_raw_chat_model, that's the bug, not this
test — see docs/superpowers/specs/2026-09-22-decision-node-design.md,
'TypeSafe is reachable from the Decision node only'."""

import pytest

from src.llm.providers import UnsupportedProviderError, build_chat_model


def test_typesafe_prefix_rejected_by_build_chat_model():
    with pytest.raises(UnsupportedProviderError):
        build_chat_model("typesafe/jev-latest")
