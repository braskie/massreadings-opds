from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlparse

from .epub import build_book_files_for_date
from .scraper import fetch_readings_for_date
from .server import OPDSServer, parse_date_text, publish_static_opds
from .sync import DEFAULT_FUTURE_DAYS, DEFAULT_PAST_DAYS, eastern_today, sync_library

DEFAULT_LIBRARY_DIR = Path("data") / "library"


def build_command(args: argparse.Namespace) -> int:
    reading_date = parse_date_text(args.date) if args.date else eastern_today()
    readings_items = fetch_readings_for_date(reading_date, cache_dir=args.output_dir, force_refresh=args.refresh)
    files = build_book_files_for_date(readings_items, args.output_dir)
    for item in files:
        print(item.epub_path)
        print(item.metadata_path)
    return 0


def sync_command(args: argparse.Namespace) -> int:
    anchor_date = parse_date_text(args.date) if args.date else None
    result = sync_library(
        args.library_dir,
        anchor_date=anchor_date,
        past_days=args.past_days,
        future_days=args.future_days,
        refresh=args.refresh,
    )
    print(f"Generated {len(result.generated)} artifacts")
    print(f"Pruned {len(result.pruned)} stale files")
    if result.failures:
        print("Failed dates:")
        for reading_date, message in result.failures:
            print(f"- {reading_date.isoformat()}: {message}")
    return 0


def serve_command(args: argparse.Namespace) -> int:
    server = OPDSServer(args.library_dir, host=args.host, port=args.port)
    if args.build_today:
        server.library.ensure_book(eastern_today())
    print(f"Serving OPDS catalog on http://{args.host}:{args.port}/opds.xml")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


def publish_static_command(args: argparse.Namespace) -> int:
    site_url = normalize_site_url(args.site_url)
    output_dir, copied = publish_static_opds(args.library_dir, args.output_dir, site_url)
    print(output_dir)
    print(f"Copied {copied} EPUB files")
    print(f"Catalog URL: {site_url.rstrip('/')}/opds.xml")
    return 0


def normalize_site_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid --site-url: {value}")
    return value.rstrip("/")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scrape USCCB readings and publish EPUB files through OPDS.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build EPUB files for a date")
    build_parser.add_argument("--date", help="Date to fetch, accepts YYYY-MM-DD, MMDDYY, YYYYMMDD, or today")
    build_parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_LIBRARY_DIR,
        help="Directory that stores the generated EPUB and metadata files",
    )
    build_parser.add_argument("--refresh", action="store_true", help="Ignore cached metadata and refresh from USCCB when possible")
    build_parser.set_defaults(func=build_command)

    sync_parser = subparsers.add_parser("sync", help="Build the rolling reading window and prune stale files")
    sync_parser.add_argument("--date", help="Anchor date for the rolling window; defaults to Eastern today")
    sync_parser.add_argument(
        "--library-dir",
        type=Path,
        default=DEFAULT_LIBRARY_DIR,
        help="Directory with generated EPUB and metadata files",
    )
    sync_parser.add_argument("--past-days", type=int, default=DEFAULT_PAST_DAYS, help="Days to keep before the anchor date")
    sync_parser.add_argument("--future-days", type=int, default=DEFAULT_FUTURE_DAYS, help="Days to keep after the anchor date")
    sync_parser.add_argument("--refresh", action="store_true", default=True, help="Refresh dates from USCCB before falling back to cache")
    sync_parser.add_argument("--no-refresh", action="store_false", dest="refresh", help="Use cached metadata when available")
    sync_parser.set_defaults(func=sync_command)

    serve_parser = subparsers.add_parser("serve", help="Serve an OPDS catalog")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Host address to bind")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port to bind")
    serve_parser.add_argument(
        "--library-dir",
        type=Path,
        default=DEFAULT_LIBRARY_DIR,
        help="Directory with generated EPUB and metadata files",
    )
    serve_parser.add_argument("--build-today", action="store_true", default=True, help="Build today's readings before serving")
    serve_parser.add_argument("--no-build-today", action="store_false", dest="build_today", help="Skip generating today's readings on startup")
    serve_parser.set_defaults(func=serve_command)

    publish_parser = subparsers.add_parser("publish-static", help="Build static OPDS files for GitHub Pages")
    publish_parser.add_argument(
        "--library-dir",
        type=Path,
        default=DEFAULT_LIBRARY_DIR,
        help="Directory with generated EPUB and metadata files",
    )
    publish_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs"),
        help="Directory to write static files (index.html, opds.xml, books/*)",
    )
    publish_parser.add_argument(
        "--site-url",
        required=True,
        help="Public base URL where static files will be hosted, e.g. https://user.github.io/repo",
    )
    publish_parser.set_defaults(func=publish_static_command)

    return parser


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()
    return args.func(args)
