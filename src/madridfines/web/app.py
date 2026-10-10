"""API and page for the months already downloaded."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from madridfines.cache import CacheURL
from madridfines.catalog import CatalogMonth, load_catalog
from madridfines.errors import MadridError
from madridfines.store import FineStore
from madridfines.sync import perform_sync

_STATIC = Path(__file__).resolve().parent / "static"
_MONTH_NAMES = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


class SyncBody(BaseModel):
    """Request to download one month, or the latest when none is given."""

    year: int | None = None
    month: int | None = None
    refresh: bool = False


class SyncState:
    """State of the download in progress, shared with the page."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running = False
        self.message = ""
        self.error: str | None = None
        self.year: int | None = None
        self.month: int | None = None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "message": self.message,
                "error": self.error,
                "year": self.year,
                "month": self.month,
            }

    def start(self, year: int, month: int) -> None:
        with self._lock:
            if self.running:
                raise HTTPException(status_code=409, detail="A download is already running")
            self.running = True
            self.error = None
            self.message = f"Downloading {_MONTH_NAMES[month]} {year}"
            self.year = year
            self.month = month

    def finish(self, error: str | None) -> None:
        with self._lock:
            self.running = False
            self.error = error
            self.message = "Done" if error is None else "The download did not finish"


def create_app(
    data_dir: Path | None = None,
    *,
    cache_dir: Path | None = None,
    obsolescence: int = 7,
    catalog_loader=None,
    month_sync=None,
) -> FastAPI:
    """Builds the application. The optional callables are for tests."""
    store = FineStore(Path(data_dir) if data_dir is not None else Path("data"))
    cache = CacheURL("madridfines", cache_dir=cache_dir, obsolescence=obsolescence)
    app = FastAPI(title="Madrid Fines", version="1.0.0")
    app.state.store = store
    app.state.cache = cache
    app.state.sync_state = SyncState()
    app.state.catalog_loader = catalog_loader or (lambda refresh=False: load_catalog(cache, refresh=refresh))
    app.state.month_sync = month_sync or (
        lambda year, month, refresh: perform_sync(
            store,
            cache,
            year=year,
            month=month,
            refresh=refresh,
        )
    )

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_STATIC / "index.html")

    @app.get("/api/catalog")
    def catalog(refresh: bool = False) -> dict:
        local = [{"year": year, "month": month} for year, month in store.list_months()]
        try:
            published = app.state.catalog_loader(refresh=refresh)
        except MadridError as exc:
            return {"months": _merge(local, []), "latest": _latest(local), "error": str(exc)}
        remote = [
            {"year": entry.year, "month": entry.month, "title": entry.title}
            if isinstance(entry, CatalogMonth)
            else entry
            for entry in published
        ]
        return {"months": _merge(local, remote), "latest": _latest(remote or local), "error": None}

    @app.get("/api/status")
    def status() -> dict:
        report = app.state.sync_state.snapshot()
        report["local"] = [
            {"year": year, "month": month} for year, month in store.list_months()
        ]
        return report

    @app.post("/api/sync", status_code=202)
    def sync(body: SyncBody, background: BackgroundTasks) -> dict:
        year, month = _resolve_target(app, body)
        app.state.sync_state.start(year, month)
        background.add_task(_run_sync, app, year, month, body.refresh)
        return {"started": True, "year": year, "month": month}

    @app.get("/api/summary")
    def summary(
        year: int = Query(..., ge=2017, le=2100),
        month: int = Query(..., ge=1, le=12),
    ) -> dict:
        return _call(store.summary, year, month)

    @app.get("/api/by-hour")
    def by_hour(
        year: int = Query(..., ge=2017, le=2100),
        month: int = Query(..., ge=1, le=12),
    ) -> list:
        return _call(store.by_hour, year, month)

    @app.get("/api/by-denunciante")
    def by_denunciante(
        year: int = Query(..., ge=2017, le=2100),
        month: int = Query(..., ge=1, le=12),
    ) -> list:
        return _call(store.by_denunciante, year, month)

    @app.get("/api/by-calification")
    def by_calification(
        year: int = Query(..., ge=2017, le=2100),
        month: int = Query(..., ge=1, le=12),
    ) -> list:
        return _call(store.summary, year, month)["calificacion"]

    @app.get("/api/payments")
    def payments(
        year: int = Query(..., ge=2017, le=2100),
        month: int = Query(..., ge=1, le=12),
    ) -> dict:
        return _call(store.payments, year, month)

    @app.get("/api/places")
    def places(
        year: int = Query(..., ge=2017, le=2100),
        month: int = Query(..., ge=1, le=12),
        q: str = "",
        limit: int = Query(20, ge=1, le=500),
    ) -> list:
        return _call(store.places, year, month, q, limit)

    @app.get("/api/map")
    def map_points(
        year: int = Query(..., ge=2017, le=2100),
        month: int = Query(..., ge=1, le=12),
        limit: int = Query(400, ge=1, le=1000),
    ) -> list:
        return _call(store.map_points, year, month, limit)

    app.mount("/static", StaticFiles(directory=_STATIC), name="static")
    return app


def _run_sync(app: FastAPI, year: int, month: int, refresh: bool) -> None:
    try:
        app.state.month_sync(year, month, refresh)
    except MadridError as exc:
        app.state.sync_state.finish(str(exc))
        return
    except Exception as exc:
        app.state.sync_state.finish(str(exc))
        return
    app.state.sync_state.finish(None)


def _resolve_target(app: FastAPI, body: SyncBody) -> tuple[int, int]:
    if (body.year is None) != (body.month is None):
        raise HTTPException(
            status_code=400,
            detail="Provide both year and month, or neither to use the latest",
        )
    if body.year is not None and body.month is not None:
        return body.year, body.month
    try:
        published = app.state.catalog_loader(refresh=body.refresh)
    except MadridError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not published:
        raise HTTPException(status_code=503, detail="The catalog has no detail files")
    latest = max(published, key=_period_key)
    return _period_key(latest)


def _period_key(entry) -> tuple[int, int]:
    if isinstance(entry, CatalogMonth):
        return entry.year, entry.month
    return int(entry["year"]), int(entry["month"])


def _merge(local: list[dict], remote: list[dict]) -> list[dict]:
    found: dict[tuple[int, int], dict] = {}
    local_keys = {(item["year"], item["month"]) for item in local}
    for item in remote:
        key = (int(item["year"]), int(item["month"]))
        found[key] = {
            "year": key[0],
            "month": key[1],
            "title": item.get("title", ""),
            "local": key in local_keys,
        }
    for item in local:
        key = (item["year"], item["month"])
        if key not in found:
            found[key] = {
                "year": key[0],
                "month": key[1],
                "title": "",
                "local": True,
            }
    return [found[key] for key in sorted(found)]


def _latest(months: list[dict]) -> dict | None:
    if not months:
        return None
    year, month = max((item["year"], item["month"]) for item in months)
    return {"year": year, "month": month}


def _call(method, *args):
    try:
        return method(*args)
    except MadridError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
