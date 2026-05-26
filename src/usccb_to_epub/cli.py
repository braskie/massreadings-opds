from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from .epub import build_book_files
from .scraper import fetch_readings
from .server import OPDSServer, parse_date_text, publish_static_opds


DEFAULT_LIBRARY_DIR = Path("data") / "library"


def build_command(args: argparse.Namespace) -> int:
    reading_date = parse_date_text(args.date) if args.date else date.today()
    readings = fetch_readings(reading_date)
    files = build_book_files(readings, args.output_dir)
    print(files.epub_path)
    print(files.metadata_path)
    return 0


def serve_command(args: argparse.Namespace) -> int:
    server = OPDSServer(args.library_dir, host=args.host, port=args.port)
    if args.build_today:
        server.library.ensure_book(date.today())
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

    build_parser = subparsers.add_parser("build", help="Build an EPUB for a date")
    build_parser.add_argument("--date", help="Date to fetch, accepts YYYY-MM-DD, MMDDYY, YYYYMMDD, or today")
    build_parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_LIBRARY_DIR,
        help="Directory that stores the generated EPUB and metadata files",
    )
    build_parser.set_defaults(func=build_command)

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
