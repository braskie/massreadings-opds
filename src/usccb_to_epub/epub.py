from __future__ import annotations

import json
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from .scraper import MassReadings, ReadingSection


@dataclass(frozen=True)
class BookFiles:
    epub_path: Path
    metadata_path: Path


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower())
    slug = slug.strip("-")
    return slug or "readings"


def build_book_files(readings: MassReadings, output_dir: Path) -> BookFiles:
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(readings.title)
    epub_path = output_dir / f"{readings.reading_date.isoformat()}-{slug}.epub"
    metadata_path = output_dir / f"{readings.reading_date.isoformat()}.json"
    generated_at = datetime.now(timezone.utc).replace(microsecond=0)

    write_epub(readings, epub_path, generated_at)
    metadata = build_metadata(readings, epub_path.name, generated_at)
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return BookFiles(epub_path=epub_path, metadata_path=metadata_path)


def build_metadata(readings: MassReadings, epub_filename: str, generated_at: datetime) -> dict[str, object]:
    return {
        "date": readings.reading_date.isoformat(),
        "title": readings.title,
        "lectionary": readings.lectionary,
        "source_url": readings.source_url,
        "epub_file": epub_filename,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "sections": [
            {
                "heading": section.heading,
                "source_label": section.source_label,
                "source_url": section.source_url,
                "text": section.text,
            }
            for section in readings.sections
        ],
    }


def write_epub(readings: MassReadings, epub_path: Path, generated_at: datetime) -> None:
    epub_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{readings.source_url}|{readings.reading_date.isoformat()}")
    chapter_docs = build_chapter_documents(readings)
    cover_svg = render_cover_svg(readings)
    cover_page_xhtml = render_cover_page_xhtml(readings)
    title_page_xhtml = render_title_page_xhtml(readings, chapter_docs)
    nav_xhtml = render_nav_xhtml(readings)
    stylesheet = render_stylesheet()
    content_opf = render_content_opf(readings, epub_id, generated_at, chapter_docs)
    container_xml = render_container_xml()

    with zipfile.ZipFile(epub_path, "w") as archive:
        mimetype = zipfile.ZipInfo("mimetype")
        mimetype.compress_type = zipfile.ZIP_STORED
        archive.writestr(mimetype, b"application/epub+zip")
        archive.writestr("META-INF/container.xml", container_xml)
        archive.writestr("OEBPS/style.css", stylesheet)
        archive.writestr("OEBPS/nav.xhtml", nav_xhtml)
        archive.writestr("OEBPS/cover.svg", cover_svg)
        archive.writestr("OEBPS/cover.xhtml", cover_page_xhtml)
        archive.writestr("OEBPS/title.xhtml", title_page_xhtml)
        for chapter in chapter_docs:
            archive.writestr(f"OEBPS/{chapter['filename']}", chapter["xhtml"])
        archive.writestr("OEBPS/content.opf", content_opf)


def build_chapter_documents(readings: MassReadings) -> list[dict[str, object]]:
    chapters: list[dict[str, object]] = []
    for index, section in enumerate(readings.sections, start=1):
        chapter_id = f"chapter-{index:02d}-{slugify(section.heading)}"
        filename = f"{chapter_id}.xhtml"
        chapters.append(
            {
                "id": chapter_id,
                "filename": filename,
                "heading": section.heading,
                "xhtml": render_chapter_xhtml(readings, section),
            }
        )
    return chapters


def render_cover_page_xhtml(readings: MassReadings) -> str:
    return f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<html xmlns=\"http://www.w3.org/1999/xhtml\" xmlns:epub=\"http://www.idpf.org/2007/ops\" xml:lang=\"en\" lang=\"en\">
  <head>
    <title>Cover - {escape(readings.title)}</title>
    <link rel=\"stylesheet\" type=\"text/css\" href=\"style.css\"/>
  </head>
  <body>
    <section class=\"cover-page\" epub:type=\"cover\">
      <img src=\"cover.svg\" alt=\"Cover for {escape(readings.title)}\" class=\"cover-image\"/>
    </section>
  </body>
</html>
"""


def render_cover_svg(readings: MassReadings) -> str:
    title_lines = split_cover_title(readings.title, max_chars=28)
    title_tspans = []
    for index, line in enumerate(title_lines):
        dy = "0" if index == 0 else "1.25em"
        title_tspans.append(f'<tspan x="600" dy="{dy}">{escape(line)}</tspan>')

    lectionary_text = f"Lectionary {readings.lectionary}" if readings.lectionary else "Daily Readings"

    return f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"1200\" height=\"1800\" viewBox=\"0 0 1200 1800\" role=\"img\" aria-label=\"Cover image for {escape(readings.title)}\">
  <defs>
    <linearGradient id=\"bg\" x1=\"0\" y1=\"0\" x2=\"1\" y2=\"1\">
      <stop offset=\"0%\" stop-color=\"#12263A\"/>
      <stop offset=\"100%\" stop-color=\"#2A4A68\"/>
    </linearGradient>
  </defs>
  <rect x=\"0\" y=\"0\" width=\"1200\" height=\"1800\" fill=\"url(#bg)\"/>
  <rect x=\"96\" y=\"96\" width=\"1008\" height=\"1608\" fill=\"none\" stroke=\"#E6D5A8\" stroke-width=\"3\"/>
  <text x=\"600\" y=\"320\" text-anchor=\"middle\" fill=\"#E6D5A8\" font-size=\"56\" font-family=\"Georgia, serif\" letter-spacing=\"2\">CATHOLIC MASS</text>
  <text x=\"600\" y=\"390\" text-anchor=\"middle\" fill=\"#E6D5A8\" font-size=\"56\" font-family=\"Georgia, serif\" letter-spacing=\"2\">READINGS</text>
  <line x1=\"260\" y1=\"460\" x2=\"940\" y2=\"460\" stroke=\"#E6D5A8\" stroke-width=\"2\"/>
  <text x=\"600\" y=\"620\" text-anchor=\"middle\" fill=\"#FFFFFF\" font-size=\"70\" font-family=\"Georgia, serif\" font-style=\"italic\">{''.join(title_tspans)}</text>
  <text x=\"600\" y=\"1450\" text-anchor=\"middle\" fill=\"#E6D5A8\" font-size=\"44\" font-family=\"Georgia, serif\">{escape(readings.reading_date.isoformat())}</text>
  <text x=\"600\" y=\"1520\" text-anchor=\"middle\" fill=\"#E6D5A8\" font-size=\"34\" font-family=\"Georgia, serif\">{escape(lectionary_text)}</text>
</svg>
"""


def split_cover_title(title: str, max_chars: int = 28) -> list[str]:
    words = title.split()
    if not words:
        return ["Daily Readings"]

    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines[:4]


def render_title_page_xhtml(readings: MassReadings, chapter_docs: list[dict[str, object]]) -> str:
    chapter_items = []
    for chapter in chapter_docs:
        chapter_items.append(
            f'<li><a href="{escape(str(chapter["filename"]))}">{escape(str(chapter["heading"]))}</a></li>'
        )

    lectionary_html = f"<p class=\"reading-meta\">Lectionary: {escape(readings.lectionary or 'N/A')}</p>" if readings.lectionary else ""

    return f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<html xmlns=\"http://www.w3.org/1999/xhtml\" xml:lang=\"en\" lang=\"en\">
  <head>
    <title>{escape(readings.title)}</title>
    <link rel=\"stylesheet\" type=\"text/css\" href=\"style.css\"/>
  </head>
  <body>
    <article class=\"title-page\">
      <header class=\"title-page-header\">
        <h1 class="mass-name">{escape(readings.title)}</h1>
        {lectionary_html}
        <p class=\"reading-source\"><a href=\"{escape(readings.source_url)}\">USCCB source page</a></p>
      </header>
      <section class=\"chapter-list\">
        <h2>Chapters</h2>
        <ol>
          {''.join(chapter_items)}
        </ol>
      </section>
    </article>
  </body>
</html>
"""


def render_chapter_xhtml(readings: MassReadings, section: ReadingSection) -> str:
    text_html = text_to_html(section.text, section.heading)
    return f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<html xmlns=\"http://www.w3.org/1999/xhtml\" xml:lang=\"en\" lang=\"en\">
  <head>
    <title>{escape(section.heading)} - {escape(readings.title)}</title>
    <link rel=\"stylesheet\" type=\"text/css\" href=\"style.css\"/>
  </head>
  <body>
    <article class=\"reading-book\">
      <header>
        <p class=\"reading-date\">Mass Date: {escape(readings.reading_date.isoformat())}</p>
        <h1>{escape(section.heading)}</h1>
      </header>
      <section class=\"reading-section\">
        <p class=\"reading-citation\"><a href=\"{escape(section.source_url)}\">{escape(section.source_label)}</a></p>
        <div class=\"reading-text\">{text_html}</div>
      </section>
    </article>
  </body>
</html>
"""


def render_nav_xhtml(readings: MassReadings) -> str:
    chapter_docs = build_chapter_documents(readings)
    nav_items = []
    for chapter in chapter_docs:
        nav_items.append(
            f'<li><a href="{escape(str(chapter["filename"]))}">{escape(str(chapter["heading"]))}</a></li>'
        )

    return f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
  <html xmlns=\"http://www.w3.org/1999/xhtml\" xmlns:epub=\"http://www.idpf.org/2007/ops\" xml:lang=\"en\" lang=\"en\">
  <head>
    <title>Table of Contents</title>
    <link rel=\"stylesheet\" type=\"text/css\" href=\"style.css\"/>
  </head>
  <body>
    <nav epub:type=\"toc\" id=\"toc\">
      <h1>Table of Contents</h1>
      <ol>
        <li><a href=\"title.xhtml\">{escape(readings.title)}</a></li>
        {''.join(nav_items)}
      </ol>
    </nav>
  </body>
</html>
"""


def render_content_opf(
    readings: MassReadings,
    epub_id: uuid.UUID,
    generated_at: datetime,
    chapter_docs: list[dict[str, object]],
) -> str:
    manifest_items = [
        '<item id="nav" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/>',
        '<item id="stylesheet" href="style.css" media-type="text/css"/>',
      '<item id="cover-image" href="cover.svg" media-type="image/svg+xml" properties="cover-image"/>',
      '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>',
        '<item id="title" href="title.xhtml" media-type="application/xhtml+xml"/>',
    ]
    spine_items = ['<itemref idref="cover"/>', '<itemref idref="title"/>']
    for chapter in chapter_docs:
        manifest_items.append(
            f'<item id="{escape(str(chapter["id"]))}" href="{escape(str(chapter["filename"]))}" media-type="application/xhtml+xml"/>'
        )
        spine_items.append(f'<itemref idref="{escape(str(chapter["id"]))}"/>')

    return f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<package xmlns=\"http://www.idpf.org/2007/opf\" unique-identifier=\"bookid\" version=\"3.0\" xml:lang=\"en\">
  <metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\">
    <dc:identifier id=\"bookid\">urn:uuid:{epub_id}</dc:identifier>
    <dc:title>{escape(readings.title)}</dc:title>
    <dc:language>en</dc:language>
    <meta name="cover" content="cover-image"/>
    <meta property=\"dcterms:modified\">{generated_at.isoformat().replace('+00:00', 'Z')}</meta>
  </metadata>
  <manifest>
    {''.join(manifest_items)}
  </manifest>
  <spine>
    {''.join(spine_items)}
  </spine>
</package>
"""


def render_container_xml() -> str:
    return """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<container version=\"1.0\" xmlns=\"urn:oasis:names:tc:opendocument:xmlns:container\">
  <rootfiles>
    <rootfile full-path=\"OEBPS/content.opf\" media-type=\"application/oebps-package+xml\"/>
  </rootfiles>
</container>
"""


def render_stylesheet() -> str:
    return """
body {
  font-family: serif;
  line-height: 1.55;
  margin: 1em;
}

.cover-page {
  margin: 0;
  padding: 0;
}

.cover-image {
  display: block;
  width: 100%;
  height: auto;
}

article.reading-book {
  max-width: 42rem;
  margin: 0 auto;
}

article.title-page {
  max-width: 42rem;
  margin: 10vh auto 0;
}

.title-page-header {
  margin-bottom: 2.5em;
}

header {
  margin-bottom: 2em;
}

.reading-date {
  color: #666;
  font-size: 0.95em;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.reading-meta,
.reading-citation,
.reading-source {
  color: #555;
  font-size: 0.95em;
}

.mass-name {
  font-size: 2.1em;
  font-style: italic;
  margin: 0.2em 0;
}

.reading-citation {
  font-style: italic;
}

.reading-text {
  white-space: pre-wrap;
}

.reading-text p {
  margin: 0.5em 0;
}

.reading-section {
  margin-top: 2em;
}

.chapter-list ol {
  padding-left: 1.2em;
}

.chapter-list li {
  margin: 0.35em 0;
}

a {
  color: inherit;
}
""".strip()


def text_to_html(text: str, heading: str | None = None) -> str:
    if not text:
        return ""

    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    html_blocks: list[str] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if heading in {"Responsorial Psalm", "Alleluia"}:
            html_blocks.extend(format_refrain_block(lines))
        else:
            html_blocks.append(f"<p>{escape(' '.join(lines))}</p>")

    return "".join(html_blocks)


def format_refrain_block(lines: list[str]) -> list[str]:
    chunks: list[str] = []
    running_lines: list[str] = []

    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("R."):
            if running_lines:
                chunks.append(f"<p>{escape(' '.join(running_lines))}</p>")
                running_lines = []

            # USCCB sometimes splits the response marker and text into separate lines.
            response_line = line
            marker_only = line == "R." or bool(re.fullmatch(r"R\.\s*\([^)]*\)", line))
            if marker_only and (index + 1) < len(lines) and not lines[index + 1].startswith("R."):
                response_line = f"{line} {lines[index + 1]}"
                index += 1

            chunks.append(f"<p><strong>{escape(response_line)}</strong></p>")
        else:
            running_lines.append(line)
        index += 1

    if running_lines:
        chunks.append(f"<p>{escape(' '.join(running_lines))}</p>")

    return chunks
