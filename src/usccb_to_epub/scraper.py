from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup, NavigableString, Tag
from curl_cffi import requests

USCCB_READINGS_URLS: tuple[str, ...] = (
    "https://bible.usccb.org/bible/readings/{slug}.cfm",
    "https://www.usccb.org/bible/readings/{slug}.cfm",
)
DEFAULT_CACHE_DIR = Path("data") / "library"
INLINE_TAGS = {"b", "strong", "i", "em", "sup", "sub", "br"}
SPECIAL_LINK_MARKERS = (
    "optional memorial",
    "memorial",
    "vigil mass",
    "mass during the night",
    "mass at dawn",
    "mass during the day",
    "extended vigil",
)
HEADING_ALIASES = {
    "Reading I": "Reading 1",
    "Reading II": "Reading 2",
}


@dataclass(frozen=True)
class ReadingSection:
    heading: str
    source_label: str
    source_url: str
    text: str
    html: str


@dataclass(frozen=True)
class MassReadings:
    reading_date: date
    title: str
    lectionary: str | None
    source_url: str
    sections: list[ReadingSection]
    categories: tuple[str, ...]


@dataclass(frozen=True)
class PageParseResult:
    masses: list[MassReadings]
    followup_links: list[str]


def readings_urls(reading_date: date) -> tuple[str, ...]:
    slug = reading_date.strftime("%m%d%y")
    return tuple(template.format(slug=slug) for template in USCCB_READINGS_URLS)


def readings_url(reading_date: date) -> str:
    return readings_urls(reading_date)[0]


def request_headers(source_url: str) -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": source_url,
    }


def fetch_readings_html(reading_date: date, timeout: int = 30) -> tuple[str, str]:
    last_error: Exception | None = None
    for source_url in readings_urls(reading_date):
        try:
            return fetch_html_url(source_url, timeout=timeout)
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unable to fetch readings page")


def fetch_html_url(source_url: str, timeout: int = 30) -> tuple[str, str]:
    headers = request_headers(source_url)
    try:
        response = requests.get(
            source_url,
            impersonate="chrome124",
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.text, source_url
    except Exception as exc:
        last_error: Exception = exc

    request = Request(source_url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace"), source_url
    except HTTPError as exc:
        if exc.code != 403:
            raise
        last_error = exc
    except Exception as exc:
        last_error = exc
    raise last_error


def fetch_readings_for_date(
    reading_date: date,
    timeout: int = 30,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    *,
    force_refresh: bool = False,
) -> list[MassReadings]:
    if cache_dir is not None and not force_refresh:
        cached = load_cached_readings(reading_date, cache_dir)
        if cached is not None:
            return cached

    try:
        html, source_url = fetch_readings_html(reading_date, timeout=timeout)
        masses = parse_readings_page(html, source_url, reading_date, timeout=timeout)
        if masses:
            return masses
    except Exception:
        if cache_dir is not None:
            cached = load_cached_readings(reading_date, cache_dir)
            if cached is not None:
                return cached
        raise

    if cache_dir is not None:
        cached = load_cached_readings(reading_date, cache_dir)
        if cached is not None:
            return cached
    raise ValueError("Could not find the reading sections on the page")


def fetch_readings(
    reading_date: date,
    timeout: int = 30,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    *,
    force_refresh: bool = False,
) -> MassReadings:
    return fetch_readings_for_date(
        reading_date,
        timeout=timeout,
        cache_dir=cache_dir,
        force_refresh=force_refresh,
    )[0]


def parse_readings_html(html: str, source_url: str, reading_date: date) -> list[MassReadings]:
    return parse_readings_page(html, source_url, reading_date)


def parse_readings_page(html: str, source_url: str, reading_date: date, *, timeout: int = 30) -> list[MassReadings]:
    pending = [(source_url, html)]
    visited: set[str] = set()
    masses: list[MassReadings] = []

    while pending:
        current_url, current_html = pending.pop(0)
        if current_url in visited:
            continue
        visited.add(current_url)
        result = parse_single_page(current_html, current_url, reading_date)
        masses.extend(result.masses)
        for followup_url in result.followup_links:
            if followup_url in visited:
                continue
            followup_html, final_url = fetch_html_url(followup_url, timeout=timeout)
            pending.append((final_url, followup_html))

    return dedupe_masses(masses)


def parse_single_page(html: str, source_url: str, reading_date: date) -> PageParseResult:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    verse_blocks = list(main.select("div.wr-block.b-verse"))
    followup_links = extract_followup_links(soup, source_url, include_all=not verse_blocks)

    if not verse_blocks:
        return PageParseResult(masses=[], followup_links=followup_links)

    title = extract_mass_title(soup, main)
    lectionary = extract_lectionary(main)
    source_is_special = is_special_url(source_url) or is_special_title(title)
    sections: list[ReadingSection] = []

    for block in verse_blocks:
        heading_tag = block.select_one("div.content-header h3.name") or block.find("h3")
        if heading_tag is None:
            continue
        heading = canonical_heading(normalize_text(heading_tag.get_text(" ", strip=True)))
        body = block.select_one("div.content-body") or block
        if section_is_empty(body):
            continue
        source_link = block.select_one("div.content-header div.address a[href]") or block.find("a", href=True)
        sections.append(
            ReadingSection(
                heading=heading,
                source_label=normalize_text(source_link.get_text(" ", strip=True)) if source_link else heading,
                source_url=urljoin(source_url, source_link["href"].strip()) if source_link else source_url,
                text=section_text(body),
                html=section_html(body),
            )
        )

    if not sections:
        return PageParseResult(masses=[], followup_links=followup_links)

    mass = MassReadings(
        reading_date=reading_date,
        title=title,
        lectionary=lectionary,
        source_url=source_url,
        sections=sections,
        categories=default_categories(reading_date, special_optional=source_is_special),
    )
    return PageParseResult(masses=[mass], followup_links=followup_links)


def extract_mass_title(soup: BeautifulSoup, main: Tag) -> str:
    lectionary_block = main.select_one("div.wr-block.b-lectionary")
    if lectionary_block is not None:
        title_tag = lectionary_block.find("h2")
        if title_tag is not None:
            lines = [line.strip() for line in title_tag.get_text("\n", strip=True).splitlines() if line.strip()]
            if lines:
                return normalize_text(" ".join(lines))

    meta_title = soup.find("meta", attrs={"property": "og:title"})
    if meta_title is not None and meta_title.get("content"):
        return normalize_text(str(meta_title["content"]))

    document_title = soup.find("title")
    if document_title is not None:
        return normalize_text(document_title.get_text(" ", strip=True).split("|")[0])

    title_tag = main.find("h2")
    if title_tag is None:
        raise ValueError("Could not find the readings title on the page")
    return normalize_text(title_tag.get_text(" ", strip=True))


def extract_lectionary(main: Tag) -> str | None:
    lectionary_block = main.select_one("div.wr-block.b-lectionary")
    search_root = lectionary_block or main
    for paragraph in search_root.find_all("p"):
        text = normalize_text(paragraph.get_text(" ", strip=True))
        if text.startswith("Lectionary:"):
            return text.partition(":")[2].strip() or None
    return None


def extract_followup_links(soup: BeautifulSoup, source_url: str, *, include_all: bool) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for tag in soup.select("ul.nested a[href]"):
        href = urljoin(source_url, tag.get("href", "").strip())
        if not href or href == source_url or href in seen:
            continue
        label = normalize_text(tag.get_text(" ", strip=True)).lower()
        if not include_all and not is_special_link(label, href):
            continue
        seen.add(href)
        links.append(href)
    return links


def is_special_link(label: str, href: str) -> bool:
    lowered_href = href.lower()
    return any(marker in label for marker in SPECIAL_LINK_MARKERS) or any(marker in lowered_href for marker in ("-vigil", "-night", "-dawn", "-day", "-memorial-"))


def is_special_title(title: str) -> bool:
    lowered = title.lower()
    return any(marker in lowered for marker in SPECIAL_LINK_MARKERS)


def is_special_url(source_url: str) -> bool:
    path = urlparse(source_url).path.lower()
    return any(marker in path for marker in ("-vigil", "-night", "-dawn", "-day", "-memorial-"))


def dedupe_masses(masses: list[MassReadings]) -> list[MassReadings]:
    unique: list[MassReadings] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for mass in masses:
        key = (mass.title, tuple(section.heading for section in mass.sections))
        if key in seen:
            continue
        seen.add(key)
        unique.append(mass)
    return unique


def load_cached_readings(reading_date: date, cache_dir: Path) -> list[MassReadings] | None:
    metadata_paths = sorted(cache_dir.glob(f"{reading_date.isoformat()}*.json"))
    if not metadata_paths:
        legacy_path = cache_dir / f"{reading_date.isoformat()}.json"
        metadata_paths = [legacy_path] if legacy_path.exists() else []
    if not metadata_paths:
        return None

    masses: list[MassReadings] = []
    for metadata_path in metadata_paths:
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            if data.get("date") != reading_date.isoformat():
                continue
            sections_data = list(data.get("sections", []))
            sections = [
                ReadingSection(
                    heading=str(section["heading"]),
                    source_label=str(section["source_label"]),
                    source_url=str(section["source_url"]),
                    text=str(section.get("text", "")),
                    html=str(section.get("html") or text_to_simple_html(str(section.get("text", "")))),
                )
                for section in sections_data
            ]
            if not sections:
                continue
            categories = tuple(str(category) for category in data.get("categories", []))
            if not categories:
                categories = default_categories(reading_date, special_optional=bool(data.get("special_optional")))
            masses.append(
                MassReadings(
                    reading_date=reading_date,
                    title=str(data["title"]),
                    lectionary=str(data["lectionary"]) if data.get("lectionary") else None,
                    source_url=str(data["source_url"]),
                    sections=sections,
                    categories=categories,
                )
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            continue

    return dedupe_masses(masses) or None


def default_categories(reading_date: date, *, special_optional: bool) -> tuple[str, ...]:
    categories = ["daily"]
    if reading_date.weekday() == 6:
        categories.append("sunday")
    if special_optional:
        categories.append("special-optional")
    return tuple(categories)


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def canonical_heading(heading: str) -> str:
    return HEADING_ALIASES.get(heading, heading)


def section_is_empty(body: Tag) -> bool:
    return not normalize_text(body.get_text(" ", strip=True))


def section_text(container: Tag) -> str:
    paragraphs = [paragraph_text(paragraph) for paragraph in container.find_all("p")]
    joined = "\n\n".join(text for text in paragraphs if text)
    return normalize_text_blocks(joined)


def normalize_text_blocks(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def paragraph_text(paragraph: Tag | None) -> str:
    if paragraph is None:
        return ""
    parts: list[str] = []
    for child in paragraph.children:
        parts.append(text_fragment(child))
    text = "".join(parts)
    return normalize_text_blocks(text.replace("\xa0", " "))


def text_fragment(node: Tag | NavigableString | None) -> str:
    if node is None:
        return ""
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""
    if node.name == "br":
        return "\n"
    return "".join(text_fragment(child) for child in node.children)


def section_html(container: Tag) -> str:
    blocks = [sanitize_block(paragraph) for paragraph in container.find_all("p")]
    return "".join(block for block in blocks if block)


def sanitize_block(tag: Tag) -> str:
    html = sanitize_inline_html(tag).strip()
    if not html:
        return ""
    return f"<p>{html}</p>"


def sanitize_inline_html(node: Tag | NavigableString | None) -> str:
    if node is None:
        return ""
    if isinstance(node, NavigableString):
        return escape(str(node).replace("\xa0", " "))
    if not isinstance(node, Tag):
        return ""
    if node.name == "a":
        return "".join(sanitize_inline_html(child) for child in node.children)
    if node.name == "br":
        return "<br/>"
    if node.name in INLINE_TAGS:
        inner = "".join(sanitize_inline_html(child) for child in node.children)
        return f"<{node.name}>{inner}</{node.name}>"
    return "".join(sanitize_inline_html(child) for child in node.children)


def text_to_simple_html(text: str) -> str:
    if not text:
        return ""
    paragraphs = [block.strip() for block in text.split("\n\n") if block.strip()]
    return "".join(
        f"<p>{escape(' '.join(line.strip() for line in paragraph.splitlines() if line.strip()))}</p>"
        for paragraph in paragraphs
    )
