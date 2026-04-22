"""Composer CLI — `composer <subcommand>` dispatcher.

Subcommands (one per Phase 9 sub-phase):
- migrate:   OAB->Composer data migration (Phase 9b)
- reconcile: post-migration user-ownership reconciliation (Phase 9b)
- keys:      LLM API key management + Vercel sync (Phase 9e)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import NoReturn


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="composer", description="Composer CLI")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # migrate
    p_migrate = sub.add_parser("migrate", help="OAB->Composer data migration (Phase 9b)")
    p_migrate.add_argument(
        "--export-dir", required=True, help="Path to OAB Convex export directory"
    )
    p_migrate.add_argument(
        "--dry-run", action="store_true", help="Print planned writes without applying"
    )

    # reconcile
    p_reconcile = sub.add_parser("reconcile", help="Reconcile migrated rows to a Composer user")
    p_reconcile.add_argument(
        "--email", required=True, help="Email of the Composer user to claim rows for"
    )

    # keys (added in Task 7)
    # (subparsers for keys registered in Task 7; kept out here intentionally)

    return parser


def main(argv: list[str] | None = None) -> NoReturn:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "migrate":
        from src.migration.runner import run_migration

        exit_code = asyncio.run(run_migration(export_dir=args.export_dir, dry_run=args.dry_run))
        sys.exit(exit_code)
    if args.subcommand == "reconcile":
        from src.migration.reconcile import run_reconcile

        exit_code = asyncio.run(run_reconcile(email=args.email))
        sys.exit(exit_code)

    parser.error(f"unknown subcommand: {args.subcommand}")


if __name__ == "__main__":
    main()
