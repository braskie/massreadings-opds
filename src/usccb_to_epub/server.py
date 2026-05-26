from __future__ import annotations

import json
import mimetypes
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse
from xml.etree import ElementTree as ET

from .epub import build_book_files_for_date, slugify
from .scraper import fetch_readings_for_date
from .sync import eastern_today

ATOM_NS = "http://www.w3.org/2005/Atom"
OPDS_NS = "http://opds-spec.org/2010/catalog"
CATEGORY_GROUPS = (
    ("Daily Readings", "daily"),
    ("Sunday Readings", "sunday"),
    ("Special/Optional Readings", "special-optional"),
)


@dataclass(frozen=True)
class LibraryRecord:
    reading_date: str
    title: str
    slug: str
    lectionary: str | None
    source_url: str
    epub_file: str
    generated_at: str
    categories: tuple[str, ...]
    sections: list[dict[str, Any]]

    @property
    def summary(self) -> str:
        section_titles = ", ".join(section["heading"] for section in self.sections)
        if self.lectionary:
            return f"Lectionary {self.lectionary}; {section_titles}"
        return section_titles

    @property
    def entry_id(self) -> str:
        return f"urn:uuid:{self.slug}"


class LibraryStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def ensure_books(self, reading_date: date) -> list[Path]:
        existing = self.records_for_date(reading_date)
        if existing and all(self.epub_path(record.epub_file).exists() for record in existing):
            return [self.epub_path(record.epub_file) for record in existing]
        readings_items = fetch_readings_for_date(reading_date, cache_dir=self.root)
        files = build_book_files_for_date(readings_items, self.root)
        return [item.epub_path for item in files]

    def ensure_book(self, reading_date: date) -> Path:
        return self.ensure_books(reading_date)[0]

    def list_records(self) -> list[LibraryRecord]:
        records: list[LibraryRecord] = []
        for metadata_path in sorted(self.root.glob("*.json"), reverse=True):
            try:
                data = json.loads(metadata_path.read_text(encoding="utf-8"))
                records.append(
                    LibraryRecord(
                        reading_date=data["date"],
                        title=data["title"],
                        slug=str(data.get("slug") or slugify(f"{data['date']}-{data['title']}")),
                        lectionary=data.get("lectionary"),
                        source_url=data["source_url"],
                        epub_file=data["epub_file"],
                        generated_at=data["generated_at"],
                        categories=tuple(data.get("categories") or infer_categories(data["date"], False)),
                        sections=list(data.get("sections", [])),
                    )
                )
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                continue
        return sorted(records, key=lambda record: (record.reading_date, record.title), reverse=True)

    def records_for_date(self, reading_date: date) -> list[LibraryRecord]:
        date_text = reading_date.isoformat()
        return [record for record in self.list_records() if record.reading_date == date_text]

    def epub_path(self, epub_file: str) -> Path:
        return self.root / epub_file


class OPDSRequestHandler(BaseHTTPRequestHandler):
    library: LibraryStore

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in {"/", "/index.html"}:
            self.respond_html(self.render_index())
            return

        if path == "/opds.xml":
            self.respond_xml(self.render_opds_feed())
            return

        if path.startswith("/books/"):
            self.serve_book(path.removeprefix("/books/"))
            return

        if path.startswith("/generate/"):
            self.generate_book(path.removeprefix("/generate/"))
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def respond_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def respond_xml(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/atom+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def serve_book(self, epub_file: str) -> None:
        epub_file = unquote(epub_file)
        book_path = self.library.epub_path(epub_file)
        if not book_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "EPUB not found")
            return

        content_type = mimetypes.types_map.get(".epub", "application/epub+zip")
        data = book_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{book_path.name}"')
        self.end_headers()
        self.wfile.write(data)

    def generate_book(self, date_text: str) -> None:
        reading_date = parse_date_text(date_text)
        epub_path = self.library.ensure_book(reading_date)
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", f"/books/{quote(epub_path.name)}")
        self.end_headers()

    def render_index(self) -> str:
        records = self.library.list_records()
        return render_index_html(records, opds_href="/opds.xml", book_base_href="/books", include_generate=True)

    def render_opds_feed(self) -> str:
        records = self.library.list_records()
        base_url = self.base_url()
        return render_opds_feed_xml(records, base_url=base_url)

    def base_url(self) -> str:
        host = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
        return f"http://{host}"


class OPDSServer:
    def __init__(self, library_dir: Path, host: str = "127.0.0.1", port: int = 8000):
        self.library = LibraryStore(library_dir)
        self.host = host
        self.port = port
        self.httpd = ThreadingHTTPServer((host, port), OPDSRequestHandler)
        self.httpd.RequestHandlerClass.library = self.library

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def shutdown(self) -> None:
        self.httpd.shutdown()


def grouped_records(records: list[LibraryRecord]) -> list[tuple[str, list[LibraryRecord]]]:
    grouped: list[tuple[str, list[LibraryRecord]]] = []
    for title, category in CATEGORY_GROUPS:
        group_records = [record for record in records if category in record.categories]
        grouped.append((title, group_records))
    return grouped


def infer_categories(reading_date: str, special_optional: bool) -> tuple[str, ...]:
    parsed_date = date.fromisoformat(reading_date)
    categories = ["daily"]
    if parsed_date.weekday() == 6:
        categories.append("sunday")
    if special_optional:
        categories.append("special-optional")
    return tuple(categories)


def render_index_html(
    records: list[LibraryRecord],
    opds_href: str,
    book_base_href: str,
    include_generate: bool,
) -> str:
    if records:
        grouped_items = []
        for group_title, group_records in grouped_records(records):
            book_items = []
            for record in group_records:
                book_items.append(
                    f'<li><a href="{book_base_href.rstrip("/")}/{quote(record.epub_file)}">{escape_html(record.reading_date)} - {escape_html(record.title)}</a></li>'
                )
            grouped_items.append(
                f"""
    <section>
      <h2>{escape_html(group_title)}</h2>
      <ul>
        {''.join(book_items) if book_items else '<li>No EPUB files have been generated yet.</li>'}
      </ul>
    </section>"""
            )
        catalog_body = ''.join(grouped_items)
    else:
        catalog_body = "<p>No EPUB files have been generated yet.</p>"

    generate_link = '<p><a href="/generate/today">Generate today\'s reading</a></p>' if include_generate else ""

    return f"""<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <title>USCCB Daily Readings OPDS</title>
    <style>
      body {{ font-family: system-ui, sans-serif; line-height: 1.5; margin: 2rem; max-width: 50rem; }}
      code {{ background: #f3f3f3; padding: 0.1rem 0.25rem; border-radius: 0.25rem; }}
    </style>
  </head>
  <body>
    <h1>USCCB Daily Readings OPDS</h1>
    <p><a href=\"{escape_html(opds_href)}\">OPDS catalog</a></p>
    {generate_link}
    <h2>Available EPUB files</h2>
        {catalog_body}
  </body>
</html>
"""


def render_opds_feed_xml(records: list[LibraryRecord], base_url: str) -> str:
    updated = max(
        (record.generated_at for record in records),
        default=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )

    clean_base_url = base_url.rstrip("/")
    ET.register_namespace("", ATOM_NS)
    ET.register_namespace("opds", OPDS_NS)
    feed = ET.Element(f"{{{ATOM_NS}}}feed")
    ET.SubElement(feed, f"{{{ATOM_NS}}}title").text = "USCCB Daily Readings"
    ET.SubElement(feed, f"{{{ATOM_NS}}}id").text = "urn:uuid:0bcbf0a1-0f3d-4b11-8fd1-8d7b15cbd3d2"
    ET.SubElement(feed, f"{{{ATOM_NS}}}updated").text = updated
    ET.SubElement(
        feed,
        f"{{{ATOM_NS}}}link",
        {
            "rel": "self",
            "href": f"{clean_base_url}/opds.xml",
            "type": "application/atom+xml;profile=opds-catalog;kind=acquisition",
        },
    )

    for record in records:
        entry = ET.SubElement(feed, f"{{{ATOM_NS}}}entry")
        ET.SubElement(entry, f"{{{ATOM_NS}}}title").text = record.title
        ET.SubElement(entry, f"{{{ATOM_NS}}}id").text = record.entry_id
        ET.SubElement(entry, f"{{{ATOM_NS}}}updated").text = record.generated_at
        ET.SubElement(entry, f"{{{ATOM_NS}}}summary").text = record.summary
        for label, category in CATEGORY_GROUPS:
            if category not in record.categories:
                continue
            ET.SubElement(
                entry,
                f"{{{ATOM_NS}}}category",
                {
                    "term": category,
                    "label": label,
                },
            )
        ET.SubElement(
            entry,
            f"{{{ATOM_NS}}}link",
            {
                "rel": "alternate",
                "href": record.source_url,
                "type": "text/html",
            },
        )
        ET.SubElement(
            entry,
            f"{{{ATOM_NS}}}link",
            {
                "rel": "http://opds-spec.org/acquisition/open-access",
                "href": f"{clean_base_url}/books/{quote(record.epub_file)}",
                "type": "application/epub+zip",
            },
        )

    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(feed, encoding="unicode")


def publish_static_opds(library_dir: Path, output_dir: Path, site_url: str) -> tuple[Path, int]:
    library = LibraryStore(library_dir)
    records = library.list_records()

    books_dir = output_dir / "books"
    books_dir.mkdir(parents=True, exist_ok=True)

    for old_epub in books_dir.glob("*.epub"):
        old_epub.unlink()

    copied = 0
    for record in records:
        source = library.epub_path(record.epub_file)
        if not source.exists():
            continue
        shutil.copy2(source, books_dir / record.epub_file)
        copied += 1

    output_dir.joinpath("opds.xml").write_text(
        render_opds_feed_xml(records, base_url=site_url),
        encoding="utf-8",
    )
    output_dir.joinpath("index.html").write_text(
        render_index_html(records, opds_href="opds.xml", book_base_href="books", include_generate=False),
        encoding="utf-8",
    )
    output_dir.joinpath(".nojekyll").write_text("\n", encoding="utf-8")

    return output_dir, copied


def parse_date_text(value: str) -> date:
    cleaned = value.strip().lower()
    if cleaned == "today":
        return eastern_today()

    for format_string in ("%Y-%m-%d", "%m%d%y", "%Y%m%d"):
        try:
            return datetime.strptime(value, format_string).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {value}")


def escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
