"""Finds the monthly CSVs on the Madrid open-data portal."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from bs4 import BeautifulSoup

from madridfines.cache import CacheURL
from madridfines.errors import CacheError, MadridError

DOWNLOADS_URL = (
    "https://datos.madrid.es/dataset/210104-0-multas-circulacion-detalle/downloads"
)
_PORTAL = "https://datos.madrid.es"
_DATASET_ID = "210104"

# Portal file titles use Spanish month names ("Detalle. Abril 2026").
_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

_FIRST_YEAR = 2017
_FIRST_MONTH = 6

_DETAIL_TITLE = re.compile(r"^detalle\.\s+([a-z]+)\s+(\d{4})\s*$", re.IGNORECASE)
_LEGACY_TITLE = re.compile(r"\b(20\d{2})\s+([a-z]+)\b", re.IGNORECASE)


@dataclass(frozen=True)
class CatalogMonth:
    """One published detail CSV for a year and a month."""

    year: int
    month: int
    url: str
    title: str


def validate_period(year: int, month: int) -> None:
    """Rejects impossible months and dates before June 2017.

    >>> from madridfines.catalog import validate_period
    >>> from madridfines.errors import MadridError
    >>> try:
    ...     validate_period(2017, 5)
    ... except MadridError as exc:
    ...     print(exc)
    Data is only available from June 2017
    """
    if not 1 <= month <= 12:
        raise MadridError(f"Month must be between 1 and 12, got: {month}")
    if year < _FIRST_YEAR or (year == _FIRST_YEAR and month < _FIRST_MONTH):
        raise MadridError("Data is only available from June 2017")


def fallback_csv_url(year: int, month: int) -> str:
    """Historical ``YYYYMMdetalle.csv`` path when the HTML has no link."""
    return (
        f"{_PORTAL}/egobfiles/MANUAL/{_DATASET_ID}/"
        f"{year}{month:02d}detalle.csv"
    )


def absolute_url(href: str) -> str:
    """Turns a portal href into an absolute URL."""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return _PORTAL + href
    return f"{_PORTAL}/{href}"


def load_catalog(cache: CacheURL, *, refresh: bool = False) -> list[CatalogMonth]:
    """Downloads the download index and returns only the detail CSVs."""
    try:
        html = cache.get(DOWNLOADS_URL, refresh=refresh)
    except CacheError as exc:
        raise MadridError(f"Could not reach the catalog: {exc}") from exc
    months = [
        month
        for month in parse_catalog_html(html)
        if (month.year, month.month) >= (_FIRST_YEAR, _FIRST_MONTH)
    ]
    if not months:
        raise MadridError("The catalog has no detail files")
    return months


def get_url(
    year: int,
    month: int,
    cache: CacheURL | None = None,
    *,
    refresh: bool = False,
) -> str:
    """URL of the detail CSV for ``year`` and ``month``.

    Reads the download page of dataset
    ``210104-0-multas-circulacion-detalle`` and keeps the resource whose
    title starts with ``Detalle.``.

    >>> from madridfines.catalog import get_url
    >>> from madridfines.errors import MadridError
    >>> try:
    ...     get_url(2024, 13)
    ... except MadridError as exc:
    ...     print(exc)
    Month must be between 1 and 12, got: 13
    """
    validate_period(year, month)
    if cache is None:
        cache = CacheURL("madridfines")
    for entry in load_catalog(cache, refresh=refresh):
        if entry.year == year and entry.month == month:
            return entry.url
    raise MadridError(
        f"No CSV file found for month {month} of year {year}"
    )


def parse_catalog_html(html: str) -> list[CatalogMonth]:
    """Extracts detail months from the download page or the old HTML."""
    soup = BeautifulSoup(html, "html.parser")
    blocks = soup.select(".resource-item")
    if blocks:
        found = _parse_resource_blocks(blocks)
    else:
        found = _parse_legacy_page(soup)
    return sorted(found.values(), key=lambda item: (item.year, item.month))


def _parse_resource_blocks(blocks: list) -> dict[tuple[int, int], CatalogMonth]:
    found: dict[tuple[int, int], CatalogMonth] = {}
    for block in blocks:
        title = _resource_title(block)
        if title is None or not title.casefold().startswith("detalle."):
            continue
        if _resource_format(block) == "txt":
            continue
        period = _parse_detalle_title(title)
        if period is None:
            continue
        year, month = period
        href = _download_href(block)
        if href is None:
            url = fallback_csv_url(year, month)
        elif not _is_csv_href(href):
            continue
        else:
            url = absolute_url(href)
        found[(year, month)] = CatalogMonth(year, month, url, title)
    return found


def _parse_legacy_page(soup: BeautifulSoup) -> dict[tuple[int, int], CatalogMonth]:
    found: dict[tuple[int, int], CatalogMonth] = {}
    for info_title in soup.find_all("p", class_="info-title"):
        text = info_title.get_text(" ", strip=True)
        period = _parse_legacy_title(text)
        if period is None:
            continue
        year, month = period
        parent = info_title.parent or info_title
        href = None
        for link in parent.find_all("a", href=True):
            candidate = link.get("href", "")
            if "detalle.csv" in candidate:
                href = candidate
                break
        url = absolute_url(href) if href else fallback_csv_url(year, month)
        found[(year, month)] = CatalogMonth(year, month, url, text)
    return found


def _resource_title(block) -> str | None:
    titled = block.find(attrs={"title": True})
    if titled and titled.get("title"):
        return str(titled["title"]).strip()
    paragraph = block.find("p")
    if paragraph:
        text = paragraph.get_text(" ", strip=True)
        return text or None
    return None


def _resource_format(block) -> str:
    marked = block.find(attrs={"data-format": True})
    if marked and marked.get("data-format"):
        return str(marked["data-format"]).strip().lower()
    return ""


def _download_href(block) -> str | None:
    for link in block.find_all("a", href=True):
        href = str(link["href"])
        if "/download/" in href:
            return href
    return None


def _is_csv_href(href: str) -> bool:
    path = href.split("?", 1)[0].lower()
    return path.endswith(".csv")


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return normalized.encode("ascii", "ignore").decode("ascii")


def _parse_detalle_title(title: str) -> tuple[int, int] | None:
    match = _DETAIL_TITLE.match(_fold(title).strip())
    if match is None:
        return None
    month = _MONTHS.get(match.group(1).lower())
    if month is None:
        return None
    return int(match.group(2)), month


def _parse_legacy_title(text: str) -> tuple[int, int] | None:
    match = _LEGACY_TITLE.search(_fold(text))
    if match is None:
        return None
    month = _MONTHS.get(match.group(2).lower())
    if month is None:
        return None
    return int(match.group(1)), month
