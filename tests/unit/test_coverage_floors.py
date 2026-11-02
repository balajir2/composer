"""Tests for the per-module coverage-floor gate (audit P2-8).

An aggregate threshold lets one well-tested package hide an almost untested
security- or data-critical module (live LLM operations, migration writer,
vector-DB providers). The gate compares each listed module's combined
line+branch coverage in coverage.xml against its own floor.
"""

import json
from pathlib import Path

import pytest

from tests.coverage_floors import evaluate, main, module_coverage, parse_coverage_xml

# 6 statements, 4 covered; 2 branch points x 2 = 4 branches, 3 covered
# -> combined (4 + 3) / (6 + 4) = 70%.
_XML = """<?xml version="1.0" ?>
<coverage lines-valid="16" lines-covered="14" branches-valid="6" branches-covered="5">
  <packages><package name="api"><classes>
    <class name="llm_models_live.py" filename="api/llm_models_live.py">
      <lines>
        <line number="1" hits="1"/>
        <line number="2" hits="1"/>
        <line number="3" hits="0"/>
        <line number="4" hits="1" branch="true" condition-coverage="50% (1/2)"/>
        <line number="5" hits="0"/>
        <line number="6" hits="1" branch="true" condition-coverage="100% (2/2)"/>
      </lines>
    </class>
    <class name="admin_tools.py" filename="api/admin_tools.py">
      <lines>
        <line number="1" hits="1"/>
        <line number="2" hits="1"/>
      </lines>
    </class>
  </classes></package></packages>
</coverage>
"""


def _write_xml(tmp_path: Path) -> Path:
    path = tmp_path / "coverage.xml"
    path.write_text(_XML, encoding="utf-8")
    return path


def _write_floors(tmp_path: Path, floors: dict[str, object]) -> Path:
    path = tmp_path / "coverage-floors.json"
    path.write_text(json.dumps(floors), encoding="utf-8")
    return path


def test_module_coverage_combines_lines_and_branches(tmp_path: Path) -> None:
    report = parse_coverage_xml(_write_xml(tmp_path))

    assert module_coverage(report, "src/api/llm_models_live.py") == pytest.approx(70.0)  # pyright: ignore[reportUnknownMemberType]
    assert module_coverage(report, "src/api/admin_tools.py") == pytest.approx(100.0)  # pyright: ignore[reportUnknownMemberType]


def test_overall_coverage_uses_the_report_totals(tmp_path: Path) -> None:
    report = parse_coverage_xml(_write_xml(tmp_path))

    # (14 + 5) / (16 + 6)
    assert report.overall == pytest.approx(86.363, abs=0.01)  # pyright: ignore[reportUnknownMemberType]


def test_module_below_its_floor_fails(tmp_path: Path) -> None:
    report = parse_coverage_xml(_write_xml(tmp_path))

    result = evaluate(report, overall_floor=0, module_floors={"src/api/llm_models_live.py": 90})

    assert not result.passed
    assert [f.module for f in result.failures] == ["src/api/llm_models_live.py"]


def test_module_at_or_above_its_floor_passes(tmp_path: Path) -> None:
    report = parse_coverage_xml(_write_xml(tmp_path))

    result = evaluate(report, overall_floor=80, module_floors={"src/api/llm_models_live.py": 70})

    assert result.passed


def test_high_aggregate_cannot_hide_a_weak_module(tmp_path: Path) -> None:
    """The point of the gate: overall passes, the critical module still fails."""
    report = parse_coverage_xml(_write_xml(tmp_path))

    result = evaluate(report, overall_floor=50, module_floors={"src/api/llm_models_live.py": 95})

    assert not result.passed


def test_overall_below_floor_fails(tmp_path: Path) -> None:
    report = parse_coverage_xml(_write_xml(tmp_path))

    result = evaluate(report, overall_floor=95, module_floors={})

    assert not result.passed


def test_a_floor_for_a_module_missing_from_the_report_fails(tmp_path: Path) -> None:
    """A renamed/deleted file must not silently drop its protection."""
    report = parse_coverage_xml(_write_xml(tmp_path))

    result = evaluate(report, overall_floor=0, module_floors={"src/api/renamed.py": 50})

    assert not result.passed
    assert "not in coverage report" in result.failures[0].reason


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    xml = _write_xml(tmp_path)
    ok = _write_floors(tmp_path, {"overall": 80, "modules": {"src/api/admin_tools.py": 99}})
    assert main([str(xml), "--floors", str(ok)]) == 0

    bad = _write_floors(tmp_path, {"overall": 80, "modules": {"src/api/llm_models_live.py": 99}})
    assert main([str(xml), "--floors", str(bad)]) == 1
    assert "src/api/llm_models_live.py" in capsys.readouterr().out
