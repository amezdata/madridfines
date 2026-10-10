"""Tests for the download catalog."""

import pytest

from madridfines.catalog import (
    DOWNLOADS_URL,
    absolute_url,
    fallback_csv_url,
    get_url,
    load_catalog,
    parse_catalog_html,
    validate_period,
)
from madridfines.errors import CacheError, MadridError

CURRENT_PAGE = """
<div class="resource-item">
  <a title="Detalle. Abril 2026"><span data-format="csv">CSV</span></a>
  <a href="/dataset/210104/resource/x/download/202604detalle.csv">Descarga</a>
</div>
<div class="resource-item">
  <a title="Agrupadas-excluidas. Abril 2026"><span data-format="csv">CSV</span></a>
  <a href="/dataset/210104/resource/y/download/agrupadas.csv">Descarga</a>
</div>
<div class="resource-item">
  <a title="Detalle. Marzo 2026"><span data-format="txt">TXT</span></a>
  <a href="/dataset/210104/resource/z/download/marzo.txt">Descarga</a>
</div>
<div class="resource-item">
  <p>Detalle. Mayo 2024</p>
  <span data-format="csv">CSV</span>
  <a href="https://datos.madrid.es/dataset/x/download/mayo.csv">Descarga</a>
</div>
<div class="resource-item">
  <a title="Detalle. Febrero 2026"><span data-format="csv">CSV</span></a>
</div>
<div class="resource-item">
  <a title="Detalle. Junio 2024"><span data-format="csv">CSV</span></a>
  <a href="/dataset/210104/resource/w/download/junio.txt">Descarga</a>
</div>
<div class="resource-item">
  <a title="Detalle. Foo 2026"><span data-format="csv">CSV</span></a>
  <a href="/dataset/210104/resource/v/download/foo.csv">Descarga</a>
</div>
<div class="resource-item">
  <a title="Detalle. Enero extra">CSV</a>
  <a href="/dataset/x/download/extra.csv">Descarga</a>
</div>
<div class="resource-item">
  <a title="Detalle. Mayo 2017"><span data-format="csv">CSV</span></a>
  <a href="/dataset/x/download/201705detalle.csv">Descarga</a>
</div>
<div class="resource-item">
  <span>nada</span>
</div>
"""

LEGACY_PAGE = """
<div>
  <p class="info-title">2024 Diciembre</p>
  <a href="/egobfiles/MANUAL/210104/202412detalle.csv">CSV</a>
</div>
<div>
  <p class="info-title">2024 Noviembre</p>
  <a href="https://datos.madrid.es/egobfiles/MANUAL/210104/202411detalle.csv">CSV</a>
</div>
<div>
  <p class="info-title">2023 Junio</p>
  <a href="egobfiles/MANUAL/210104/202306detalle.csv">CSV</a>
</div>
<div>
  <p class="info-title">2022 Mayo</p>
</div>
<div>
  <p class="info-title">sin fecha</p>
  <a href="/otro.csv">CSV</a>
</div>
<div>
  <p class="info-title">2024 Foobar</p>
</div>
"""


class StubCache:
    def __init__(self, html=CURRENT_PAGE, error=None):
        self.html = html
        self.error = error
        self.urls = []

    def get(self, url, encoding=None, refresh=False, **kwargs):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.html


def test_parse_current_page_keeps_only_detail_csv():
    months = parse_catalog_html(CURRENT_PAGE)
    found = {(item.year, item.month): item.url for item in months}
    assert found[2026, 4].endswith("202604detalle.csv")
    assert found[2024, 5] == "https://datos.madrid.es/dataset/x/download/mayo.csv"
    assert found[2026, 2] == fallback_csv_url(2026, 2)
    assert (2026, 3) not in found
    assert (2024, 6) not in found
    assert all("Agrupadas" not in item.title for item in months)


def test_parse_legacy_page_resolves_relative_and_absolute_links():
    months = {(item.year, item.month): item.url for item in parse_catalog_html(LEGACY_PAGE)}
    assert months[2024, 12] == "https://datos.madrid.es/egobfiles/MANUAL/210104/202412detalle.csv"
    assert months[2024, 11] == "https://datos.madrid.es/egobfiles/MANUAL/210104/202411detalle.csv"
    assert months[2023, 6] == "https://datos.madrid.es/egobfiles/MANUAL/210104/202306detalle.csv"
    assert months[2022, 5] == fallback_csv_url(2022, 5)


def test_absolute_url_and_invalid_periods():
    assert absolute_url("https://datos.madrid.es/a.csv").endswith("a.csv")
    assert absolute_url("/a.csv") == "https://datos.madrid.es/a.csv"
    assert absolute_url("a.csv") == "https://datos.madrid.es/a.csv"
    with pytest.raises(MadridError):
        validate_period(2024, 0)
    with pytest.raises(MadridError):
        validate_period(2017, 5)


def test_load_catalog_and_get_url():
    cache = StubCache()
    months = load_catalog(cache)
    assert months[0].year == 2024
    assert all((item.year, item.month) >= (2017, 6) for item in months)
    assert get_url(2026, 4, cache=cache) == (
        "https://datos.madrid.es/dataset/210104/resource/x/download/202604detalle.csv"
    )
    with pytest.raises(MadridError):
        get_url(2026, 1, cache=cache)


def test_catalog_errors():
    with pytest.raises(MadridError):
        load_catalog(StubCache(error=CacheError("caído")))
    with pytest.raises(MadridError):
        load_catalog(StubCache(html="<html></html>"))
    with pytest.raises(MadridError):
        get_url(2024, 13, cache=StubCache())


def test_get_url_uses_default_cache_only_after_validation(monkeypatch):
    created = {}

    class Boom:
        def __init__(self, *args, **kwargs):
            created["yes"] = True

        def get(self, *args, **kwargs):
            raise AssertionError("must not download")

    monkeypatch.setattr("madridfines.catalog.CacheURL", Boom)
    with pytest.raises(MadridError):
        get_url(2016, 12)
    assert "yes" not in created
    cache = StubCache()
    monkeypatch.setattr("madridfines.catalog.CacheURL", lambda *args, **kwargs: cache)
    assert "202604detalle.csv" in get_url(2026, 4)
    assert cache.urls == [DOWNLOADS_URL]
