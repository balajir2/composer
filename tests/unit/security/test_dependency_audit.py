"""Tests for the dependency-audit gate (audit P0-2 / P1-4).

The gate turns `pip-audit` / `npm audit` JSON into a pass/fail decision with a
time-boxed allowlist: every exception needs an owner, a rationale, the
affected surface and an expiry, so a suppression can never be permanent,
anonymous, or reasonless. The fixtures below are deliberately vulnerable
reports — they prove each gate actually fails.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from src.security.dependency_audit import (
    AllowlistError,
    Finding,
    evaluate,
    load_allowlist,
    main,
    parse_npm_audit,
    parse_pip_audit,
)

TODAY = date(2026, 11, 2)


def _entry(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "PYSEC-2026-1325",
        "ecosystem": "pypi",
        "package": "ecdsa",
        "owner": "balajirajan@gmail.com",
        "rationale": "ECDSA signing is never used; only HS256 sign and RS256 verify.",
        "surface": "python-jose -> ecdsa (transitive)",
        "expires": "2027-02-01",
    }
    base.update(overrides)
    return base


def _write(tmp_path: Path, entries: list[dict[str, Any]]) -> Path:
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return path


PIP_REPORT: dict[str, Any] = {
    "dependencies": [
        {"name": "clean", "version": "1.0", "vulns": []},
        {"name": "skipped", "version": "1.0", "skip_reason": "not on PyPI"},
        {
            "name": "ecdsa",
            "version": "0.19.2",
            "vulns": [
                {
                    "id": "PYSEC-2026-1325",
                    "aliases": ["CVE-2024-23342", "GHSA-wj6h-64fc-37mp"],
                    "fix_versions": [],
                    "description": "Minerva timing attack",
                }
            ],
        },
        {
            "name": "pypdf",
            "version": "6.10.2",
            "vulns": [{"id": "GHSA-aaaa-bbbb-cccc", "aliases": [], "fix_versions": ["6.21.4"]}],
        },
    ]
}

NPM_REPORT: dict[str, Any] = {
    "vulnerabilities": {
        "next": {
            "name": "next",
            "severity": "critical",
            "via": [
                {
                    "source": 1,
                    "name": "next",
                    "title": "Auth bypass in middleware",
                    "url": "https://github.com/advisories/GHSA-f82v-jwr5-mpqh",
                    "severity": "critical",
                    "range": "<14.2.35",
                },
                {
                    "source": 2,
                    "name": "next",
                    "title": "Minor info leak",
                    "url": "https://github.com/advisories/GHSA-low1-low1-low1",
                    "severity": "low",
                    "range": "<14.2.0",
                },
            ],
        },
        "postcss": {
            "name": "postcss",
            "severity": "high",
            "via": ["next"],
        },
    }
}


def test_parse_pip_audit_reports_only_vulnerable_packages_with_all_aliases() -> None:
    findings = parse_pip_audit(PIP_REPORT)

    assert {(f.package, f.version) for f in findings} == {("ecdsa", "0.19.2"), ("pypdf", "6.10.2")}
    ecdsa = next(f for f in findings if f.package == "ecdsa")
    assert ecdsa.ids == frozenset({"PYSEC-2026-1325", "CVE-2024-23342", "GHSA-WJ6H-64FC-37MP"})
    assert ecdsa.ecosystem == "pypi"


def test_parse_npm_audit_keeps_only_high_and_critical_advisories() -> None:
    findings = parse_npm_audit(NPM_REPORT)

    assert [(f.package, f.severity) for f in findings] == [("next", "critical")]
    assert "GHSA-F82V-JWR5-MPQH" in findings[0].ids
    assert findings[0].ecosystem == "npm"


def test_unreviewed_finding_fails_the_gate() -> None:
    result = evaluate(parse_pip_audit(PIP_REPORT), [], today=TODAY)

    assert not result.passed
    assert {f.package for f in result.unreviewed} == {"ecdsa", "pypdf"}


def test_allowlisted_finding_matched_by_alias_passes(tmp_path: Path) -> None:
    allowlist = load_allowlist(
        _write(tmp_path, [_entry(), _entry(id="GHSA-aaaa-bbbb-cccc", package="pypdf")]),
        today=TODAY,
    )

    result = evaluate(parse_pip_audit(PIP_REPORT), allowlist, today=TODAY)

    assert result.passed
    assert result.unreviewed == []


def test_allowlist_entry_for_a_different_package_does_not_suppress(tmp_path: Path) -> None:
    allowlist = load_allowlist(_write(tmp_path, [_entry(package="someone-else")]), today=TODAY)

    result = evaluate(parse_pip_audit(PIP_REPORT), allowlist, today=TODAY)

    assert not result.passed


def test_expired_allowlist_entry_fails_even_though_it_matches(tmp_path: Path) -> None:
    allowlist = load_allowlist(_write(tmp_path, [_entry(expires="2026-11-02")]), today=TODAY)

    result = evaluate(parse_pip_audit(PIP_REPORT), allowlist, today=date(2026, 11, 3))

    assert not result.passed
    assert [f.package for f in result.expired] == ["ecdsa"]


def test_stale_allowlist_entries_are_reported_but_do_not_fail(tmp_path: Path) -> None:
    allowlist = load_allowlist(
        _write(tmp_path, [_entry(id="GHSA-gone-gone-gone", package="gone")]), today=TODAY
    )

    result = evaluate([], allowlist, today=TODAY)

    assert result.passed
    assert [e.id for e in result.stale] == ["GHSA-GONE-GONE-GONE"]


@pytest.mark.parametrize("missing", ["id", "package", "owner", "rationale", "surface", "expires"])
def test_allowlist_entry_missing_a_required_field_is_rejected(tmp_path: Path, missing: str) -> None:
    entry = _entry()
    del entry[missing]

    with pytest.raises(AllowlistError, match=missing):
        load_allowlist(_write(tmp_path, [entry]), today=TODAY)


@pytest.mark.parametrize("field", ["owner", "rationale", "surface"])
def test_allowlist_entry_with_blank_field_is_rejected(tmp_path: Path, field: str) -> None:
    with pytest.raises(AllowlistError, match=field):
        load_allowlist(_write(tmp_path, [_entry(**{field: "   "})]), today=TODAY)


def test_allowlist_entry_already_expired_is_rejected_at_load(tmp_path: Path) -> None:
    with pytest.raises(AllowlistError, match="expired"):
        load_allowlist(_write(tmp_path, [_entry(expires="2026-10-01")]), today=TODAY)


def test_allowlist_entry_expiring_too_far_out_is_rejected(tmp_path: Path) -> None:
    """No permanent exceptions: the maximum lifetime is one year."""
    with pytest.raises(AllowlistError, match="365"):
        load_allowlist(_write(tmp_path, [_entry(expires="2028-01-01")]), today=TODAY)


def test_allowlist_entry_with_unparseable_expiry_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(AllowlistError, match="expires"):
        load_allowlist(_write(tmp_path, [_entry(expires="never")]), today=TODAY)


def test_duplicate_allowlist_entries_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(AllowlistError, match="duplicate"):
        load_allowlist(_write(tmp_path, [_entry(), _entry()]), today=TODAY)


def test_cli_exits_nonzero_on_an_unreviewed_vulnerability(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = tmp_path / "pip.json"
    report.write_text(json.dumps(PIP_REPORT), encoding="utf-8")
    allowlist = _write(tmp_path, [])
    out = tmp_path / "report.md"

    code = main(["pypi", str(report), "--allowlist", str(allowlist), "--report", str(out)])

    assert code == 1
    assert "ecdsa" in out.read_text(encoding="utf-8")
    assert "UNREVIEWED" in capsys.readouterr().out


def test_cli_exits_zero_when_everything_is_reviewed(tmp_path: Path) -> None:
    report = tmp_path / "pip.json"
    report.write_text(json.dumps(PIP_REPORT), encoding="utf-8")
    allowlist = _write(
        tmp_path,
        [_entry(expires="2027-01-15"), _entry(id="GHSA-aaaa-bbbb-cccc", package="pypdf")],
    )

    code = main(
        ["pypi", str(report), "--allowlist", str(allowlist), "--today", "2026-11-02"],
    )

    assert code == 0


def test_cli_checks_npm_reports_too(tmp_path: Path) -> None:
    report = tmp_path / "npm.json"
    report.write_text(json.dumps(NPM_REPORT), encoding="utf-8")
    allowlist = _write(tmp_path, [])

    assert main(["npm", str(report), "--allowlist", str(allowlist)]) == 1


def test_finding_ids_are_case_normalised() -> None:
    finding = Finding(
        ecosystem="pypi",
        package="x",
        version="1",
        ids=frozenset({"ghsa-abcd-efgh-ijkl"}),
        severity=None,
        title="",
    )

    assert "GHSA-ABCD-EFGH-IJKL" in finding.ids
