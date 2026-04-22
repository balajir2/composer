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

    # keys
    p_keys = sub.add_parser("keys", help="LLM API key management (Phase 9e)")
    keys_sub = p_keys.add_subparsers(dest="keys_cmd", required=True)
    keys_sub.add_parser("list", help="list configured keys")
    p_set = keys_sub.add_parser("set", help="set/upsert one key")
    p_set.add_argument("provider")
    p_set.add_argument("value", help="plaintext key, or '-' to read from stdin")
    p_del = keys_sub.add_parser("delete", help="delete one key")
    p_del.add_argument("provider")
    p_sync = keys_sub.add_parser("sync", help="push all keys to deploy target")
    p_sync.add_argument("--target", default="vercel", choices=["vercel"])
    p_sync.add_argument(
        "--prune", action="store_true", help="remove tracked env vars not in Postgres"
    )

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

    if args.subcommand == "keys":
        from src.cli import keys as keys_mod

        if args.keys_cmd == "list":
            sys.exit(asyncio.run(keys_mod.keys_list()))
        if args.keys_cmd == "set":
            sys.exit(asyncio.run(keys_mod.keys_set(args.provider, args.value)))
        if args.keys_cmd == "delete":
            sys.exit(asyncio.run(keys_mod.keys_delete(args.provider)))
        if args.keys_cmd == "sync":
            sys.exit(asyncio.run(keys_mod.keys_sync(args.target, args.prune)))

    parser.error(f"unknown subcommand: {args.subcommand}")


if __name__ == "__main__":
    main()
