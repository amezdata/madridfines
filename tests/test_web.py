"""API tests that stay offline."""

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from madridfines.catalog import CatalogMonth
from madridfines.errors import MadridError
from madridfines.fines import MadridFines
from madridfines.store import FineStore
from madridfines.web.app import create_app

CSV = (
    "CALIFICACION;LUGAR;MES;ANIO;HORA;IMP_BOL;DESCUENTO;PUNTOS;DENUNCIANTE;"
    "HECHO-BOL;VEL_LIMITE;VEL_CIRCULA;COORDENADA-X;COORDENADA-Y\n"
    "LEVE;SOL 1;04;2026;08.21;90;SI;0;SER;HECHO;;;4414.78;44735.65\n"
    "GRAVE;GRAN VIA 2;04;2026;18.05;200;NO;0;SER;HECHO;;;4412.00;44730.00\n"
    "MUY GRAVE;OTRA;04;2026;09.00;500;NO;6;SACE;HECHO;;;4413.00;44731.00\n"
)


def _frame():
    frame = pd.read_csv(pd.io.common.StringIO(CSV), sep=";")
    MadridFines.clean(frame)
    return frame


def _months(refresh=False):
    return [
        CatalogMonth(2025, 12, "https://example.com/202512", "Detalle. Diciembre 2025"),
        CatalogMonth(2026, 4, "https://example.com/202604", "Detalle. Abril 2026"),
    ]


@pytest.fixture
def client(tmp_path):
    FineStore(tmp_path).write(2026, 4, _frame())

    def sync(year, month, refresh):
        if year == 2017:
            raise MadridError("portal caído")
        if year == 2018:
            raise RuntimeError("disco lleno")
        FineStore(tmp_path).write(year, month, _frame())

    app = create_app(tmp_path, cache_dir=tmp_path / "cache", catalog_loader=_months, month_sync=sync)
    return TestClient(app), app


def test_page_catalog_and_queries(client):
    http, _app = client
    page = http.get("/")
    assert page.status_code == 200
    assert "Fines" in page.text
    assert http.get("/static/styles.css").status_code == 200

    catalog = http.get("/api/catalog").json()
    assert catalog["latest"] == {"year": 2026, "month": 4}
    assert any(item["local"] and item["month"] == 4 for item in catalog["months"])

    summary = http.get("/api/summary", params={"year": 2026, "month": 4}).json()
    assert summary["num_multas"] == 3
    assert summary["recaudado"] == 45
    assert summary["con_descuento"] == 1
    grades = http.get("/api/by-calification", params={"year": 2026, "month": 4}).json()
    assert [item["calificacion"] for item in grades][:3] == ["LEVE", "GRAVE", "MUY GRAVE"]
    hours = http.get("/api/by-hour", params={"year": 2026, "month": 4}).json()
    assert len(hours) == 24
    reporters = http.get("/api/by-denunciante", params={"year": 2026, "month": 4}).json()
    assert reporters == [
        {"denunciante": "SACE", "num_multas": 1},
        {"denunciante": "SER", "num_multas": 2},
    ]
    payments = http.get("/api/payments", params={"year": 2026, "month": 4}).json()
    assert payments["importe_max"] == 790
    places = http.get("/api/places", params={"year": 2026, "month": 4, "q": "sol"}).json()
    assert places[0]["lugar"] == "SOL 1"
    points = http.get("/api/map", params={"year": 2026, "month": 4}).json()
    assert points
    missing = http.get("/api/summary", params={"year": 2026, "month": 3})
    assert missing.status_code == 404
    assert http.get("/api/summary", params={"year": 2010, "month": 4}).status_code == 422


def test_catalog_error_and_dict_entries(tmp_path):
    local_dir = tmp_path / "local"
    FineStore(local_dir).write(2024, 6, _frame())

    def broken(refresh=False):
        raise MadridError("catálogo no disponible")

    http = TestClient(create_app(local_dir, catalog_loader=broken, month_sync=lambda *args: None))
    payload = http.get("/api/catalog").json()
    assert payload["error"] == "catálogo no disponible"
    assert payload["latest"] == {"year": 2024, "month": 6}
    assert payload["months"][0]["local"] is True

    def as_dicts(refresh=False):
        return [{"year": 2026, "month": 1, "title": "Detalle. Enero 2026"}]

    http = TestClient(create_app(tmp_path / "vacio", catalog_loader=as_dicts, month_sync=lambda *args: None))
    listed = http.get("/api/catalog").json()["months"]
    assert listed == [{"year": 2026, "month": 1, "title": "Detalle. Enero 2026", "local": False}]
    started = http.post("/api/sync", json={})
    assert started.status_code == 202
    assert started.json()["month"] == 1


def test_sync_flow(client):
    http, app = client
    started = http.post("/api/sync", json={"year": 2026, "month": 3})
    assert started.status_code == 202
    assert started.json()["month"] == 3
    assert http.get("/api/status").json()["error"] is None
    assert (Path(app.state.store.root) / "year=2026" / "month=03" / "fines.parquet").is_file()

    latest = http.post("/api/sync", json={})
    assert latest.status_code == 202
    assert latest.json()["year"] == 2026

    app.state.sync_state.running = True
    busy = http.post("/api/sync", json={"year": 2026, "month": 4})
    assert busy.status_code == 409
    app.state.sync_state.running = False

    failed = http.post("/api/sync", json={"year": 2017, "month": 6})
    assert failed.status_code == 202
    assert "portal" in http.get("/api/status").json()["error"]

    crashed = http.post("/api/sync", json={"year": 2018, "month": 1})
    assert "disco" in http.get("/api/status").json()["error"]

    half = http.post("/api/sync", json={"year": 2026})
    assert half.status_code == 400


def test_sync_without_catalog(tmp_path):
    def empty(refresh=False):
        return []

    http = TestClient(create_app(tmp_path, catalog_loader=empty, month_sync=lambda *a: None))
    assert http.get("/api/catalog").json()["latest"] is None
    response = http.post("/api/sync", json={})
    assert response.status_code == 503

    def broken(refresh=False):
        raise MadridError("sin portal")

    http = TestClient(create_app(tmp_path, catalog_loader=broken, month_sync=lambda *a: None))
    assert http.post("/api/sync", json={}).status_code == 503
