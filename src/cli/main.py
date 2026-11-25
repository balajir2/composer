"""Composer CLI — `composer <subcommand>` dispatcher.

Subcommands (one per Phase 9 sub-phase):
- migrate:   OAB->Composer data migration (Phase 9b)
- reconcile: post-migration user-ownership reconciliation (Phase 9b)
- keys:      LLM API key management + Vercel sync (Phase 9e)
- checkpoints: copy paused runs between the old and official checkpoint savers
- watch:     poll a folder and trigger a production workflow (2026-07-11)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from typing import TYPE_CHECKING, Any, NoReturn, TypeVar

if TYPE_CHECKING:
    from collections.abc import Coroutine

_T = TypeVar("_T")


def _run(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run a command coroutine. psycopg async cannot use Windows' Proactor loop, so on win32 use a
    selector loop (same pattern as the Alembic env)."""
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(coro)
    return asyncio.run(coro)


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

    # checkpoints
    p_cp = sub.add_parser("checkpoints", help="Checkpoint tools (phase 5)")
    cp_sub = p_cp.add_subparsers(dest="checkpoints_cmd", required=True)
    p_copy = cp_sub.add_parser("copy", help="copy paused runs between the two checkpoint savers")
    p_copy.add_argument("--to", required=True, choices=["postgres", "legacy"])
    group = p_copy.add_mutually_exclusive_group(required=True)
    group.add_argument("--execution", action="append", default=[], help="execution id (repeatable)")
    group.add_argument("--all-waiting", action="store_true", help="every waiting_approval run")
    p_copy.add_argument("--force", action="store_true", help="copy runs in any status")

    # watch
    p_watch = sub.add_parser("watch", help="Poll a folder and trigger a production workflow")
    p_watch.add_argument(
        "--api-url", required=True, help="Backend base URL, e.g. https://api.example.com"
    )
    p_watch.add_argument("--slug", required=True, help="Production workflow's externalSlug")
    p_watch.add_argument("--api-key", required=True, help="ck_... API key")
    p_watch.add_argument("--provider", default="local", choices=["local"])
    p_watch.add_argument("--source", required=True, help="Source folder to watch")
    p_watch.add_argument("--dest", required=True, help="Destination folder on successful claim")
    p_watch.add_argument("--error", required=True, help="Destination folder on failed claim")
    p_watch.add_argument("--target-var", required=True, help="Start input variable to populate")
    p_watch.add_argument("--interval", type=int, default=30, help="Poll interval in seconds")

    return parser


def main(argv: list[str] | None = None) -> NoReturn:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "migrate":
        from src.migration.runner import run_migration

        exit_code = _run(run_migration(export_dir=args.export_dir, dry_run=args.dry_run))
        sys.exit(exit_code)
    if args.subcommand == "reconcile":
        from src.migration.reconcile import run_reconcile

        exit_code = _run(run_reconcile(email=args.email))
        sys.exit(exit_code)

    if args.subcommand == "keys":
        from src.cli import keys as keys_mod

        if args.keys_cmd == "list":
            sys.exit(_run(keys_mod.keys_list()))
        if args.keys_cmd == "set":
            sys.exit(_run(keys_mod.keys_set(args.provider, args.value)))
        if args.keys_cmd == "delete":
            sys.exit(_run(keys_mod.keys_delete(args.provider)))
        if args.keys_cmd == "sync":
            sys.exit(_run(keys_mod.keys_sync(args.target, args.prune)))

    if args.subcommand == "checkpoints":
        from src.cli.checkpoints import checkpoints_copy

        sys.exit(_run(checkpoints_copy(args.to, args.execution, args.all_waiting, args.force)))

    if args.subcommand == "watch":
        from src.cli.watch import WatchConfig, run_watch_loop

        config = WatchConfig(
            workflow_api_url=args.api_url,
            external_slug=args.slug,
            api_key=args.api_key,
            provider=args.provider,
            source_path=args.source,
            dest_path=args.dest,
            error_path=args.error,
            target_input_variable=args.target_var,
            poll_interval_seconds=args.interval,
        )
        with contextlib.suppress(KeyboardInterrupt):
            _run(run_watch_loop(config))
        sys.exit(0)

    parser.error(f"unknown subcommand: {args.subcommand}")


if __name__ == "__main__":
    main()
