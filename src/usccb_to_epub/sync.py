from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .epub import BookFiles, build_book_files_for_date
from .scraper import fetch_readings_for_date

EASTERN_TIMEZONE = ZoneInfo("America/New_York")
DEFAULT_PAST_DAYS = 7
DEFAULT_FUTURE_DAYS = 14
MANAGED_EXTENSIONS = {".epub", ".json"}


@dataclass(frozen=True)
class SyncResult:
    generated: list[BookFiles]
    pruned: list[Path]
    failures: list[tuple[date, str]]


def eastern_today(now: datetime | None = None) -> date:
    moment = now.astimezone(EASTERN_TIMEZONE) if now is not None else datetime.now(EASTERN_TIMEZONE)
    return moment.date()


def target_dates(*, anchor_date: date | None = None, past_days: int = DEFAULT_PAST_DAYS, future_days: int = DEFAULT_FUTURE_DAYS) -> list[date]:
    base_date = anchor_date or eastern_today()
    start_date = base_date - timedelta(days=past_days)
    end_date = base_date + timedelta(days=future_days)
    days = (end_date - start_date).days + 1
    return [start_date + timedelta(days=offset) for offset in range(days)]


def sync_library(
    library_dir: Path,
    *,
    anchor_date: date | None = None,
    past_days: int = DEFAULT_PAST_DAYS,
    future_days: int = DEFAULT_FUTURE_DAYS,
    refresh: bool = True,
) -> SyncResult:
    library_dir.mkdir(parents=True, exist_ok=True)
    generated: list[BookFiles] = []
    failures: list[tuple[date, str]] = []

    for reading_date in target_dates(anchor_date=anchor_date, past_days=past_days, future_days=future_days):
        try:
            readings_items = fetch_readings_for_date(
                reading_date,
                cache_dir=library_dir,
                force_refresh=refresh,
            )
            generated.extend(build_book_files_for_date(readings_items, library_dir))
        except Exception as exc:
            failures.append((reading_date, str(exc)))

    managed_names = {book.epub_path.name for book in generated} | {book.metadata_path.name for book in generated}
    pruned = prune_library(library_dir, managed_names)
    return SyncResult(generated=generated, pruned=pruned, failures=failures)


def prune_library(library_dir: Path, managed_names: set[str]) -> list[Path]:
    pruned: list[Path] = []
    for path in sorted(library_dir.iterdir()):
        if not path.is_file() or path.suffix not in MANAGED_EXTENSIONS:
            continue
        if path.name in managed_names:
            continue
        path.unlink()
        pruned.append(path)
    return pruned
