"""Per-module coverage floors (audit P2-8).

CI runs `pytest` (which writes coverage.xml via pytest-cov), then:

    python -m tests.coverage_floors coverage.xml --floors coverage-floors.json

Each module listed in coverage-floors.json must meet its own floor, measured as
combined line + branch coverage (the same number pytest-cov prints in its
"Cover" column). An aggregate threshold alone lets a well-tested package hide a
nearly untested critical path; a floor naming a file that no longer appears in
the report also fails, so renaming a module cannot silently drop its protection.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

_CONDITION = re.compile(r"\((\d+)/(\d+)\)")


@dataclass(frozen=True)
class Counts:
    covered: int
    total: int

    @property
    def percent(self) -> float:
        return 100.0 if self.total == 0 else 100.0 * self.covered / self.total


@dataclass(frozen=True)
class CoverageReport:
    overall: float
    modules: dict[str, Counts]


@dataclass(frozen=True)
class Failure:
    module: str
    reason: str


@dataclass
class FloorResult:
    failures: list[Failure]

    @property
    def passed(self) -> bool:
        return not self.failures


def parse_coverage_xml(path: Path) -> CoverageReport:
    root = ET.parse(path).getroot()
    modules: dict[str, Counts] = {}
    for cls in root.iter("class"):
        filename = cls.get("filename", "")
        covered = total = 0
        for line in cls.iter("line"):
            total += 1
            covered += 1 if int(line.get("hits", "0")) > 0 else 0
            match = _CONDITION.search(line.get("condition-coverage", ""))
            if line.get("branch") == "true" and match:
                covered += int(match.group(1))
                total += int(match.group(2))
        key = f"src/{filename}".replace("\\", "/")
        previous = modules.get(key)
        modules[key] = Counts(
            covered + (previous.covered if previous else 0),
            total + (previous.total if previous else 0),
        )
    lines_valid = int(root.get("lines-valid", "0"))
    lines_covered = int(root.get("lines-covered", "0"))
    branches_valid = int(root.get("branches-valid", "0"))
    branches_covered = int(root.get("branches-covered", "0"))
    overall = Counts(lines_covered + branches_covered, lines_valid + branches_valid).percent
    return CoverageReport(overall=overall, modules=modules)


def module_coverage(report: CoverageReport, module: str) -> float:
    return report.modules[module].percent


def evaluate(
    report: CoverageReport, *, overall_floor: float, module_floors: dict[str, float]
) -> FloorResult:
    failures: list[Failure] = []
    if report.overall < overall_floor:
        failures.append(
            Failure("overall", f"{report.overall:.1f}% is below the floor {overall_floor:.0f}%")
        )
    for module, floor in sorted(module_floors.items()):
        counts = report.modules.get(module)
        if counts is None:
            failures.append(Failure(module, "not in coverage report (renamed or deleted?)"))
        elif counts.percent < floor:
            failures.append(
                Failure(module, f"{counts.percent:.1f}% is below the floor {floor:.0f}%")
            )
    return FloorResult(failures)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enforce per-module coverage floors.")
    parser.add_argument("coverage_xml", type=Path)
    parser.add_argument("--floors", type=Path, required=True)
    args = parser.parse_args(argv)

    config = cast("dict[str, Any]", json.loads(args.floors.read_text(encoding="utf-8")))
    module_floors = {
        str(k): float(v) for k, v in cast("dict[str, Any]", config.get("modules", {})).items()
    }
    report = parse_coverage_xml(args.coverage_xml)
    result = evaluate(
        report, overall_floor=float(config.get("overall", 0)), module_floors=module_floors
    )

    for failure in result.failures:
        print(f"COVERAGE FLOOR FAILED {failure.module}: {failure.reason}")
    print(
        f"coverage: overall {report.overall:.1f}%, {len(module_floors)} module floor(s) checked"
        f" - {'PASS' if result.passed else 'FAIL'}"
    )
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
