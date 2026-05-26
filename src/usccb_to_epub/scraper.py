from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from curl_cffi import requests

USCCB_READINGS_URL = "https://bible.usccb.org/bible/readings/{slug}.cfm"
DEFAULT_CACHE_DIR = Path("data") / "library"
READING_HEADINGS: tuple[str, ...] = (
    "Reading 1",
    "Responsorial Psalm",
    "Reading 2",
    "Alleluia",
    "Gospel",
)


@dataclass(frozen=True)
class ReadingSection:
    heading: str
    source_label: str
    source_url: str
    text: str


@dataclass(frozen=True)
class MassReadings:
    reading_date: date
    title: str
    lectionary: str | None
    source_url: str
    sections: list[ReadingSection]


def readings_url(reading_date: date) -> str:
    return USCCB_READINGS_URL.format(slug=reading_date.strftime("%m%d%y"))


def fetch_readings_html(reading_date: date, timeout: int = 30) -> str:
    url = readings_url(reading_date)
    try:
        response = requests.get(url, impersonate="chrome124", timeout=timeout)
        response.raise_for_status()
        return response.text
    except Exception:
        request = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            },
        )
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")


def parse_readings_html(html: str, source_url: str, reading_date: date) -> MassReadings:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup

    title_tag = main.find("h2")
    if title_tag is None:
        raise ValueError("Could not find the readings title on the page")
    title = normalize_text(title_tag.get_text(" ", strip=True))

    lectionary: str | None = None
    for paragraph in main.find_all("p"):
        text = normalize_text(paragraph.get_text(" ", strip=True))
        if text.startswith("Lectionary:"):
            lectionary = text.partition(":")[2].strip() or None
            break

    sections: list[ReadingSection] = []
    for heading_name in READING_HEADINGS:
        heading = find_heading(main, heading_name)
        if heading is None:
            continue
        container = find_section_container(heading, main)
        if container is None:
            continue
        source_link = container.find("a", href=True)
        sections.append(
            ReadingSection(
                heading=heading_name,
                source_label=normalize_text(source_link.get_text(" ", strip=True)) if source_link else heading_name,
                source_url=urljoin(source_url, source_link["href"].strip()) if source_link else source_url,
                text=section_text(container),
            )
        )

    if not sections:
        raise ValueError("Could not find the reading sections on the page")

    return MassReadings(
        reading_date=reading_date,
        title=title,
        lectionary=lectionary,
        source_url=source_url,
        sections=sections,
    )


def fetch_readings(
    reading_date: date,
    timeout: int = 30,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
) -> MassReadings:
    if cache_dir is not None:
        cached = load_cached_readings(reading_date, cache_dir)
        if cached is not None:
            return cached

    source_url = readings_url(reading_date)
    html = fetch_readings_html(reading_date, timeout=timeout)
    return parse_readings_html(html, source_url, reading_date)


def load_cached_readings(reading_date: date, cache_dir: Path) -> MassReadings | None:
    metadata_path = cache_dir / f"{reading_date.isoformat()}.json"
    if not metadata_path.exists():
        return None

    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        sections_data = list(data.get("sections", []))
        sections = [
            ReadingSection(
                heading=str(section["heading"]),
                source_label=str(section["source_label"]),
                source_url=str(section["source_url"]),
                text=str(section["text"]),
            )
            for section in sections_data
        ]
        if not sections:
            return None
        return MassReadings(
            reading_date=reading_date,
            title=str(data["title"]),
            lectionary=str(data["lectionary"]) if data.get("lectionary") else None,
            source_url=str(data["source_url"]),
            sections=sections,
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def find_heading(container, heading_name: str):
    for heading in container.find_all("h3"):
        if normalize_text(heading.get_text(" ", strip=True)) == heading_name:
            return heading
    return None


def find_section_container(heading, main):
    container = heading.parent
    while container is not None and container is not main:
        headings = container.find_all("h3")
        if len(headings) == 1 and headings[0] is heading and container.find("p") is not None:
            return container
        container = container.parent
    return heading.parent


def section_text(container) -> str:
    paragraphs = [paragraph_text(paragraph) for paragraph in container.find_all("p")]
    joined = "\n\n".join(text for text in paragraphs if text)
    return normalize_text_blocks(joined)


def normalize_text_blocks(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def paragraph_text(paragraph) -> str:
    if paragraph is None:
        return ""
    lines = [normalize_text(part) for part in paragraph.stripped_strings]
    return "\n".join(line for line in lines if line)
