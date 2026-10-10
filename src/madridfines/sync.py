"""Syncs portal months into the local store."""

from __future__ import annotations

import io
import re

import pandas as pd

from madridfines.cache import CacheURL
from madridfines.catalog import CatalogMonth, get_url, load_catalog, validate_period
from madridfines.cleaning import clean
from madridfines.errors import MadridError
from madridfines.store import FineStore

_START = re.compile(r"^(\d{4})-(\d{2})$")


def parse_start(value: str) -> tuple[int, int]:
    """Parses ``YYYY-MM`` and checks that the period is valid."""
    match = _START.fullmatch(value.strip())
    if match is None:
        raise MadridError("Start must look like YYYY-MM")
    year, month = int(match.group(1)), int(match.group(2))
    validate_period(year, month)
    return year, month


def read_month_frame(
    year: int,
    month: int,
    cache: CacheURL,
    *,
    refresh: bool = False,
) -> pd.DataFrame:
    """Downloads and cleans one month's CSV."""
    url = get_url(year, month, cache=cache)
    content = cache.get(url, encoding="latin-1", refresh=refresh)
    frame = pd.read_csv(io.StringIO(content.replace("\x00", "")), sep=";")
    clean(frame)
    return frame


def select_months(
    catalog: list[CatalogMonth],
    *,
    year: int | None = None,
    month: int | None = None,
    start: str | None = None,
) -> list[CatalogMonth]:
    """Chooses which catalog entries to sync."""
    if not catalog:
        raise MadridError("The catalog has no detail files")
    if start:
        start_year, start_month = parse_start(start)
        chosen = [
            entry
            for entry in catalog
            if (entry.year, entry.month) >= (start_year, start_month)
        ]
        if not chosen:
            raise MadridError(f"No months have been published since {start}")
        return chosen
    if year is not None and month is not None:
        validate_period(year, month)
        chosen = [entry for entry in catalog if entry.year == year and entry.month == month]
        if not chosen:
            raise MadridError(
                f"No CSV file found for month {month} of year {year}"
            )
        return chosen
    if year is not None:
        chosen = [entry for entry in catalog if entry.year == year]
        if not chosen:
            raise MadridError(f"No months have been published for {year}")
        return chosen
    latest = max(catalog, key=lambda entry: (entry.year, entry.month))
    return [latest]


def perform_sync(
    store: FineStore,
    cache: CacheURL,
    *,
    year: int | None = None,
    month: int | None = None,
    start: str | None = None,
    refresh: bool = False,
) -> list[CatalogMonth]:
    """Saves the requested months. Skips those still current, unless ``refresh``."""
    catalog = load_catalog(cache, refresh=refresh)
    chosen = select_months(catalog, year=year, month=month, start=start)
    written: list[CatalogMonth] = []
    for entry in chosen:
        if not refresh and store.has(entry.year, entry.month) and _csv_is_fresh(cache, entry.url):
            continue
        frame = read_month_frame(entry.year, entry.month, cache, refresh=refresh)
        store.write(entry.year, entry.month, frame)
        written.append(entry)
    return written


def _csv_is_fresh(cache: CacheURL, url: str) -> bool:
    from madridfines.cache import Cache

    return Cache.is_fresh(cache, cache._get_hash_name(url))
