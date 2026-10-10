"""Tests for the store, sync, and the commands."""

from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from madridfines.cache import CacheURL
from madridfines.catalog import DOWNLOADS_URL
from madridfines.cli import main
from madridfines.errors import MadridError
from madridfines.fines import MadridFines
from madridfines.store import FineStore, project_utm
from madridfines.sync import parse_start, perform_sync, select_months
from madridfines.catalog import CatalogMonth

CSV_URL = "https://datos.madrid.es/ficheros/202604detalle.csv"
HTML = f"""
<div class="resource-item">
  <a title="Detalle. Abril 2026"><span data-format="csv">CSV</span></a>
  <a href="{CSV_URL}">Descarga</a>
</div>
<div class="resource-item">
  <a title="Detalle. Marzo 2026"><span data-format="csv">CSV</span></a>
  <a href="https://datos.madrid.es/ficheros/202603detalle.csv">Descarga</a>
</div>
"""
CSV = (
    "CALIFICACION;LUGAR;MES;ANIO;HORA;IMP_BOL;DESCUENTO;PUNTOS;DENUNCIANTE;"
    "HECHO-BOL;VEL_LIMITE;VEL_CIRCULA;COORDENADA-X;COORDENADA-Y\n"
    "LEVE;SOL 1;04;2026;08.21;90;SI;0;SER;HECHO;;;4414.78;44735.65\n"
    "GRAVE;GRAN VIA 2;04;2026;18.05;200;NO;0;SER;HECHO;;;1;1\n"
    "OTRA;CALLE 3;04;2026;09.00;10;NO;0;POLICIA MUNICIPAL;HECHO;;;4414.90;44735.80\n"
)


def _sample():
    frame = pd.read_csv(pd.io.common.StringIO(CSV), sep=";")
    MadridFines.clean(frame)
    return frame


def test_store_roundtrip_and_queries(tmp_path):
    store = FineStore(tmp_path)
    assert store.list_months() == []
    (tmp_path / "year=no").mkdir()
    (tmp_path / "year=nota").write_text("x", encoding="utf-8")
    (tmp_path / "year=2020").mkdir()
    (tmp_path / "year=2020" / "month=x").mkdir()
    assert store.list_months() == []
    with pytest.raises(MadridError):
        store.summary(2026, 4)

    store.write(2026, 4, _sample())
    assert store.has(2026, 4)
    assert store.list_months() == [(2026, 4)]
    report = store.summary(2026, 4)
    assert report["num_multas"] == 3
    assert report["importe_max"] == 300
    assert report["importe_min"] == 255
    assert report["recaudado"] == 45
    assert report["con_descuento"] == 1
    names = [item["calificacion"] for item in report["calificacion"]]
    assert names == ["LEVE", "GRAVE", "OTRA"]
    hours = {item["hora"]: item["num_multas"] for item in store.by_hour(2026, 4)}
    assert hours[8] == 1
    assert hours[0] == 0
    reporters = store.by_denunciante(2026, 4)
    assert [item["denunciante"] for item in reporters] == ["POLICIA MUNICIPAL", "SER"]
    assert [item["num_multas"] for item in reporters] == [1, 2]
    assert store.payments(2026, 4)["importe_min"] == 255
    assert store.places(2026, 4, query="sol")[0]["lugar"] == "SOL 1"
    assert store.places(2026, 4, query="nada") == []
    points = store.map_points(2026, 4)
    assert {point["lugar"] for point in points} == {"SOL 1", "CALLE 3"}
    assert 40.3 < points[0]["lat"] < 40.6
    assert -3.9 < points[0]["lon"] < -3.5

    longitude, latitude = project_utm(440767, 4474829)
    assert 40.3 < latitude < 40.6
    unnamed = pd.DataFrame({"LUGAR": ["A"], "IMP_BOL": [1.0], "DESCUENTO": ["NO"], "CALIFICACION": ["LEVE"]})
    unnamed.index = pd.DatetimeIndex(["2026-01-01 00:00:00"])
    store.write(2026, 1, unnamed)
    from madridfines.store import _parse_label
    assert _parse_label("nope", "year=") is None


def test_store_query_error_when_columns_are_missing(tmp_path):
    store = FineStore(tmp_path)
    frame = pd.DataFrame({"CALIFICACION": ["LEVE"], "IMP_BOL": [10.0], "DESCUENTO": ["NO"], "LUGAR": ["SOL"]})
    frame.index = pd.to_datetime(["2026-04-01 08:00:00"])
    frame.index.name = "fecha"
    store.write(2026, 4, frame)
    with pytest.raises(MadridError):
        store.map_points(2026, 4)


def test_select_and_parse_start():
    catalog = [
        CatalogMonth(2026, 3, "https://example.com/3", "Detalle. Marzo 2026"),
        CatalogMonth(2026, 4, "https://example.com/4", "Detalle. Abril 2026"),
    ]
    assert select_months(catalog)[0].month == 4
    assert [item.month for item in select_months(catalog, year=2026)] == [3, 4]
    assert select_months(catalog, year=2026, month=3)[0].month == 3
    assert select_months(catalog, start="2026-04")[0].month == 4
    with pytest.raises(MadridError):
        select_months([])
    with pytest.raises(MadridError):
        select_months(catalog, year=2024)
    with pytest.raises(MadridError):
        select_months(catalog, year=2026, month=1)
    with pytest.raises(MadridError):
        select_months(catalog, start="2027-01")
    with pytest.raises(MadridError):
        parse_start("abril")
    with pytest.raises(MadridError):
        parse_start("2017-05")


def test_perform_sync_skips_fresh_copy(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        response = Mock()
        response.raise_for_status = Mock()
        if url == DOWNLOADS_URL:
            response.text = HTML
            response.content = HTML.encode("utf-8")
        else:
            response.text = CSV
            response.content = CSV.encode("latin-1")
        return response

    monkeypatch.setattr(requests, "get", fake_get)
    cache = CacheURL("consulta", cache_dir=tmp_path / "cache", obsolescence=7)
    store = FineStore(tmp_path / "data")
    written = perform_sync(store, cache, year=2026, month=4)
    assert [item.month for item in written] == [4]
    assert store.has(2026, 4)
    again = perform_sync(store, cache, year=2026, month=4)
    assert again == []
    refreshed = perform_sync(store, cache, year=2026, month=4, refresh=True)
    assert refreshed[0].year == 2026
    ranged = perform_sync(store, cache, start="2026-03", refresh=True)
    assert {item.month for item in ranged} == {3, 4}
    assert calls


def test_cli_sync_serve_and_errors(tmp_path, monkeypatch, capsys):
    def fake_sync(store, cache, **kwargs):
        if kwargs.get("year") == 1999:
            raise MadridError("no está")
        return [CatalogMonth(2026, 4, "https://example.com/a", "Detalle. Abril 2026")]

    monkeypatch.setattr("madridfines.cli.perform_sync", fake_sync)
    assert main(["sync", "--data-dir", str(tmp_path), "--cache-dir", str(tmp_path / "c")]) == 0
    assert "Saved 04/2026" in capsys.readouterr().out
    assert main(["sync", "--year", "1999", "--data-dir", str(tmp_path), "--cache-dir", str(tmp_path)]) == 1
    assert "no está" in capsys.readouterr().out

    monkeypatch.setattr("madridfines.cli.perform_sync", lambda *args, **kwargs: [])
    assert main(["sync", "--year", "2026", "--data-dir", str(tmp_path), "--cache-dir", str(tmp_path)]) == 0
    assert "still current" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        main(["sync", "--from", "2024-01", "--year", "2024"])
    with pytest.raises(SystemExit):
        main(["sync", "--month", "4"])

    captured = {}

    def fake_run(app, host, port):
        captured["host"] = host
        captured["port"] = port

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    assert main(["serve", "--host", "127.0.0.1", "--port", "8765", "--data-dir", str(tmp_path), "--cache-dir", str(tmp_path / "srv")]) == 0
    assert captured == {"host": "127.0.0.1", "port": 8765}

    monkeypatch.setattr(
        "madridfines.cli._cache",
        lambda args: (_ for _ in ()).throw(MadridError("caché")),
    )
    assert main(["sync"]) == 1
