"""The ``sync`` and ``serve`` commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from madridfines.cache import CacheURL
from madridfines.catalog import CatalogMonth
from madridfines.errors import CacheError, MadridError
from madridfines.store import FineStore
from madridfines.sync import perform_sync


def build_parser() -> argparse.ArgumentParser:
    """Command-line parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", default="data", help="Folder for the Parquet store")
    common.add_argument("--cache-dir", default=None, help="Folder for the HTTP cache")
    common.add_argument(
        "--obsolescence",
        type=int,
        default=7,
        help="Days a downloaded file stays current",
    )
    parser = argparse.ArgumentParser(
        prog="madridfines",
        description="Download and browse Madrid traffic fines.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    sync = commands.add_parser("sync", parents=[common], help="Download months from the portal")
    sync.add_argument("--year", type=int, default=None)
    sync.add_argument("--month", type=int, default=None)
    sync.add_argument("--from", dest="start", default=None, help="From YYYY-MM, inclusive")
    sync.add_argument(
        "--refresh",
        action="store_true",
        help="Download again even when the local copy is still current",
    )

    serve = commands.add_parser("serve", parents=[common], help="Open the browse page")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns an exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "sync":
            if args.start and (args.year is not None or args.month is not None):
                parser.error("Use --from or --year, not both")
            if args.month is not None and args.year is None:
                parser.error("--month requires --year")
            written = perform_sync(
                FineStore(Path(args.data_dir)),
                _cache(args),
                year=args.year,
                month=args.month,
                start=args.start,
                refresh=args.refresh,
            )
            _print_sync(written, args)
            return 0
        _serve(args)
        return 0
    except (MadridError, CacheError) as exc:
        print(f"Error: {exc}")
        return 1


def _cache(args: argparse.Namespace) -> CacheURL:
    return CacheURL(
        "madridfines",
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        obsolescence=args.obsolescence,
    )


def _print_sync(written: list[CatalogMonth], args: argparse.Namespace) -> None:
    if not written:
        print("No new months. The local copy is still current.")
        return
    for entry in written:
        print(f"Saved {entry.month:02d}/{entry.year} to {args.data_dir}")


def _serve(args: argparse.Namespace) -> None:
    import uvicorn

    from madridfines.web.app import create_app

    app = create_app(
        Path(args.data_dir),
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        obsolescence=args.obsolescence,
    )
    uvicorn.run(app, host=args.host, port=args.port)
