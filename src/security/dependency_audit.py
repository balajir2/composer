"""Dependency-audit gate: fail CI on unreviewed advisories (audit P0-2 / P1-4).

Reads a `pip-audit -f json` or `npm audit --json` report and decides pass/fail
against a time-boxed allowlist. An allowlist entry must name an owner, a
rationale, the affected surface and an expiry no more than 365 days away, so a
suppression can never be permanent, anonymous or reasonless; once it expires
the advisory fails the build again until someone re-reviews it.

    python -m src.security.dependency_audit pypi pip-audit.json \
        --allowlist security/audit-allowlist.json --report pip-audit-report.md
    python -m src.security.dependency_audit npm npm-audit.json \
        --allowlist security/audit-allowlist.json

Scope: pip-audit has no severity field, so every advisory counts; for npm only
`high` and `critical` advisories gate the build (run `npm audit --omit=dev` so
dev-only tooling that never ships is out of scope).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

MAX_ALLOWLIST_DAYS = 365
_NPM_GATING_SEVERITIES = frozenset({"high", "critical"})
_REQUIRED_FIELDS = ("id", "ecosystem", "package", "owner", "rationale", "surface", "expires")


class AllowlistError(ValueError):
    """The allowlist file is malformed, expired, or too permissive."""


@dataclass(frozen=True)
class Finding:
    ecosystem: str
    package: str
    version: str
    ids: frozenset[str]
    severity: str | None
    title: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "ids", frozenset(i.upper() for i in self.ids))


@dataclass(frozen=True)
class AllowlistEntry:
    id: str
    ecosystem: str
    package: str
    owner: str
    rationale: str
    surface: str
    expires: date


@dataclass
class GateResult:
    unreviewed: list[Finding] = field(default_factory=lambda: [])
    expired: list[Finding] = field(default_factory=lambda: [])
    accepted: list[tuple[Finding, AllowlistEntry]] = field(default_factory=lambda: [])
    stale: list[AllowlistEntry] = field(default_factory=lambda: [])

    @property
    def passed(self) -> bool:
        return not self.unreviewed and not self.expired


def _as_dict(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def parse_pip_audit(report: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for dep_raw in _as_list(report.get("dependencies")):
        dep = _as_dict(dep_raw)
        vulns = [_as_dict(v) for v in _as_list(dep.get("vulns"))]
        if not vulns:
            continue
        ids: set[str] = set()
        titles: list[str] = []
        for vuln in vulns:
            ids.add(str(vuln.get("id", "")))
            ids.update(str(a) for a in _as_list(vuln.get("aliases")))
            if vuln.get("description"):
                titles.append(str(vuln["description"]).strip().splitlines()[0][:120])
        findings.append(
            Finding(
                ecosystem="pypi",
                package=str(dep.get("name", "")),
                version=str(dep.get("version", "")),
                ids=frozenset(i for i in ids if i),
                severity=None,
                title="; ".join(titles[:2]),
            )
        )
    return findings


def parse_npm_audit(report: dict[str, Any]) -> list[Finding]:
    findings: dict[tuple[str, frozenset[str]], Finding] = {}
    for node_raw in _as_dict(report.get("vulnerabilities")).values():
        for via_raw in _as_list(_as_dict(node_raw).get("via")):
            via = _as_dict(via_raw)  # plain strings just point at another package
            severity = str(via.get("severity", ""))
            if severity not in _NPM_GATING_SEVERITIES:
                continue
            url = str(via.get("url", ""))
            advisory_id = url.rstrip("/").rsplit("/", 1)[-1]
            ids = (
                frozenset({advisory_id}) if advisory_id else frozenset({f"NPM-{via.get('source')}"})
            )
            package = str(via.get("name", ""))
            findings[(package, ids)] = Finding(
                ecosystem="npm",
                package=package,
                version="",
                ids=ids,
                severity=severity,
                title=str(via.get("title", "")),
            )
    return list(findings.values())


def load_allowlist(path: Path, *, today: date) -> list[AllowlistEntry]:
    if not path.exists():
        return []
    raw = _as_dict(json.loads(path.read_text(encoding="utf-8")))
    entries: list[AllowlistEntry] = []
    seen: set[tuple[str, str, str]] = set()
    for item_raw in _as_list(raw.get("entries")):
        item = _as_dict(item_raw)
        for name in _REQUIRED_FIELDS:
            if name not in item:
                raise AllowlistError(f"allowlist entry is missing required field {name!r}: {item}")
        for name in ("id", "ecosystem", "package", "owner", "rationale", "surface"):
            if not str(item[name]).strip():
                raise AllowlistError(f"allowlist entry has a blank {name!r}: {item}")
        try:
            expires = date.fromisoformat(str(item["expires"]))
        except ValueError as exc:
            raise AllowlistError(
                f"allowlist entry {item['id']!r} has an invalid 'expires' "
                f"(need YYYY-MM-DD): {item['expires']!r}"
            ) from exc
        if expires < today:
            raise AllowlistError(
                f"allowlist entry {item['id']!r} expired on {expires} — re-review it or remove it"
            )
        if expires > today + timedelta(days=MAX_ALLOWLIST_DAYS):
            raise AllowlistError(
                f"allowlist entry {item['id']!r} expires {expires}, more than "
                f"{MAX_ALLOWLIST_DAYS} days out — exceptions must be time-boxed"
            )
        entry = AllowlistEntry(
            id=str(item["id"]).strip().upper(),
            ecosystem=str(item["ecosystem"]).strip(),
            package=str(item["package"]).strip(),
            owner=str(item["owner"]).strip(),
            rationale=str(item["rationale"]).strip(),
            surface=str(item["surface"]).strip(),
            expires=expires,
        )
        key = (entry.ecosystem, entry.package, entry.id)
        if key in seen:
            raise AllowlistError(f"duplicate allowlist entry for {key}")
        seen.add(key)
        entries.append(entry)
    return entries


def evaluate(
    findings: list[Finding], allowlist: list[AllowlistEntry], *, today: date
) -> GateResult:
    result = GateResult()
    used: set[AllowlistEntry] = set()
    for finding in findings:
        match = next(
            (
                e
                for e in allowlist
                if e.ecosystem == finding.ecosystem
                and e.package == finding.package
                and e.id in finding.ids
            ),
            None,
        )
        if match is None:
            result.unreviewed.append(finding)
        elif match.expires < today:
            result.expired.append(finding)
            used.add(match)
        else:
            result.accepted.append((finding, match))
            used.add(match)
    result.stale = [e for e in allowlist if e not in used]
    return result


def render_report(result: GateResult, *, ecosystem: str, today: date) -> str:
    lines = [f"# Dependency audit — {ecosystem} ({today})", ""]
    lines.append(f"**Result: {'PASS' if result.passed else 'FAIL'}**")
    lines.append("")

    def section(title: str, rows: list[str]) -> None:
        if rows:
            lines.extend([f"## {title}", "", *rows, ""])

    def fmt(f: Finding) -> str:
        sev = f" [{f.severity}]" if f.severity else ""
        return f"- `{f.package}` {f.version}{sev}: {', '.join(sorted(f.ids))} — {f.title}"

    section("UNREVIEWED advisories", [fmt(f) for f in result.unreviewed])
    section("Allowlist entries EXPIRED", [fmt(f) for f in result.expired])
    section(
        "Accepted (time-boxed exceptions)",
        [
            f"{fmt(f)}\n  - owner: {e.owner}; expires {e.expires}; surface: {e.surface}\n"
            f"  - rationale: {e.rationale}"
            for f, e in result.accepted
        ],
    )
    section(
        "Stale allowlist entries (no longer match anything — remove them)",
        [f"- `{e.package}` {e.id}" for e in result.stale],
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate CI on unreviewed dependency advisories.")
    parser.add_argument("ecosystem", choices=["pypi", "npm"])
    parser.add_argument("report", type=Path, help="pip-audit -f json / npm audit --json output")
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument("--report", dest="report_out", type=Path, default=None)
    parser.add_argument("--today", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)

    today: date = args.today or date.today()
    try:
        allowlist = [
            e for e in load_allowlist(args.allowlist, today=today) if e.ecosystem == args.ecosystem
        ]
    except AllowlistError as exc:
        print(f"ALLOWLIST ERROR: {exc}")
        return 2

    raw = _as_dict(json.loads(args.report.read_text(encoding="utf-8")))
    findings = parse_pip_audit(raw) if args.ecosystem == "pypi" else parse_npm_audit(raw)
    result = evaluate(findings, allowlist, today=today)

    for f in result.unreviewed:
        print(f"UNREVIEWED {f.package} {f.version} {sorted(f.ids)} {f.title}")
    for f in result.expired:
        print(f"EXPIRED-ALLOWLIST {f.package} {f.version} {sorted(f.ids)}")
    for e in result.stale:
        print(f"stale allowlist entry (remove): {e.package} {e.id}")
    print(
        f"{args.ecosystem}: {len(findings)} advisory package(s), {len(result.accepted)} accepted, "
        f"{len(result.unreviewed)} unreviewed, {len(result.expired)} expired — "
        f"{'PASS' if result.passed else 'FAIL'}"
    )
    if args.report_out is not None:
        args.report_out.write_text(
            render_report(result, ecosystem=args.ecosystem, today=today), encoding="utf-8"
        )
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
