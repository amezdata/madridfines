"""Parquet store partitioned by year and month, queried with DuckDB."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
from pyproj import Transformer

from madridfines.errors import MadridError

_MADRID_LAT = (40.15, 40.80)
_MADRID_LON = (-4.10, -3.40)
_HECTOMETRE_EASTING = 10000

_TRANSFORMER = Transformer.from_crs("EPSG:25830", "EPSG:4326", always_xy=True)

_GRADE_ORDER = ("LEVE", "GRAVE", "MUY GRAVE")


class FineStore:
    """Reads and writes one parquet per month under ``root/year=YYYY/month=MM``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, year: int, month: int) -> Path:
        """Path of that month's parquet, whether or not it exists."""
        return self.root / f"year={year}" / f"month={month:02d}" / "fines.parquet"

    def has(self, year: int, month: int) -> bool:
        """Whether that month is already stored."""
        return self.path_for(year, month).is_file()

    def list_months(self) -> list[tuple[int, int]]:
        """Months on disk, oldest first."""
        months: list[tuple[int, int]] = []
        if not self.root.exists():
            return months
        for year_dir in self.root.glob("year=*"):
            if not year_dir.is_dir():
                continue
            year = _parse_label(year_dir.name, "year=")
            if year is None:
                continue
            for month_dir in year_dir.glob("month=*"):
                month = _parse_label(month_dir.name, "month=")
                if month is None or not (month_dir / "fines.parquet").is_file():
                    continue
                months.append((year, month))
        return sorted(months)

    def write(self, year: int, month: int, frame: pd.DataFrame) -> Path:
        """Saves an already cleaned ``frame``. The date index becomes a column."""
        path = self.path_for(year, month)
        path.parent.mkdir(parents=True, exist_ok=True)
        output = frame.copy()
        if isinstance(output.index, pd.DatetimeIndex):
            if output.index.name is None:
                output.index.name = "fecha"
            output = output.reset_index()
        output.to_parquet(path, index=False)
        return path

    def summary(self, year: int, month: int) -> dict:
        """Count, estimated amounts, and the split by severity.

        ``recaudado`` is not money collected: the file does not say whether
        the fine was paid. The 50% rate applies only when ``DESCUENTO`` is ``SI``.
        """
        path = self._require(year, month)
        totals = _rows(
            path,
            """
            SELECT
                count(*) AS num_multas,
                coalesce(sum(IMP_BOL), 0) AS importe_max,
                coalesce(sum(
                    CASE WHEN DESCUENTO = 'SI' THEN IMP_BOL * 0.5 ELSE IMP_BOL END
                ), 0) AS importe_min,
                coalesce(sum(
                    CASE WHEN DESCUENTO = 'SI' THEN IMP_BOL * 0.5 ELSE 0 END
                ), 0) AS recaudado,  -- estimate: the file does not say whether it was paid
                coalesce(sum(CASE WHEN DESCUENTO = 'SI' THEN 1 ELSE 0 END), 0) AS con_descuento
            FROM read_parquet(?)
            """,
        )
        grades = _rows(
            path,
            """
            SELECT coalesce(CALIFICACION, '') AS calificacion, count(*) AS num_multas
            FROM read_parquet(?)
            GROUP BY 1
            ORDER BY 2 DESC
            """,
        )
        count, importe_max, importe_min, recaudado, con_descuento = totals[0]
        return {
            "year": year,
            "month": month,
            "num_multas": int(count),
            "importe_max": float(importe_max),
            "importe_min": float(importe_min),
            "recaudado": float(recaudado),
            "con_descuento": int(con_descuento),
            "calificacion": _ordered_grades(grades),
        }

    def by_hour(self, year: int, month: int) -> list[dict]:
        """Fines across the 24 hours. Hours with no cases are zero."""
        path = self._require(year, month)
        rows = _rows(
            path,
            """
            SELECT hour(fecha) AS hora, count(*) AS num_multas
            FROM read_parquet(?)
            WHERE fecha IS NOT NULL
            GROUP BY 1
            """,
        )
        counts = {int(hora): int(total) for hora, total in rows if hora is not None}
        return [{"hora": hour, "num_multas": counts.get(hour, 0)} for hour in range(24)]

    def by_denunciante(self, year: int, month: int) -> list[dict]:
        """Fines by reporter type, from least to most frequent."""
        path = self._require(year, month)
        rows = _rows(
            path,
            """
            SELECT coalesce(nullif(trim(DENUNCIANTE), ''), 'NO REPORTER') AS denunciante,
                   count(*) AS num_multas
            FROM read_parquet(?)
            GROUP BY 1
            ORDER BY num_multas ASC, denunciante
            """,
        )
        return [
            {"denunciante": str(name), "num_multas": int(total)}
            for name, total in rows
        ]

    def payments(self, year: int, month: int) -> dict:
        """Amounts for the month, full and with early payment."""
        report = self.summary(year, month)
        return {
            "year": year,
            "month": month,
            "num_multas": report["num_multas"],
            "importe_max": report["importe_max"],
            "importe_min": report["importe_min"],
            "recaudado": report["recaudado"],
            "con_descuento": report["con_descuento"],
        }

    def places(self, year: int, month: int, query: str = "", limit: int = 20) -> list[dict]:
        """Streets with the most fines. ``query`` filters by text."""
        path = self._require(year, month)
        rows = _rows(
            path,
            """
            SELECT LUGAR AS lugar, count(*) AS num_multas, coalesce(sum(IMP_BOL), 0) AS importe
            FROM read_parquet(?)
            WHERE LUGAR IS NOT NULL AND LUGAR <> '' AND LUGAR ILIKE ?
            GROUP BY LUGAR
            ORDER BY num_multas DESC
            LIMIT ?
            """,
            [f"%{query.strip()}%", limit],
        )
        return [
            {"lugar": lugar, "num_multas": int(total), "importe": float(importe)}
            for lugar, total, importe in rows
        ]

    def map_points(self, year: int, month: int, limit: int = 400) -> list[dict]:
        """Points aggregated by street, in latitude and longitude.

        The CSV uses ETRS89 / UTM zone 30. In recent files the figure is in
        hectometres; a value that is already in metres is not scaled.
        """
        path = self._require(year, month)
        rows = _rows(
            path,
            """
            SELECT
                LUGAR AS lugar,
                avg("COORDENADA-X") AS x,
                avg("COORDENADA-Y") AS y,
                count(*) AS num_multas,
                coalesce(sum(IMP_BOL), 0) AS importe
            FROM read_parquet(?)
            WHERE "COORDENADA-X" IS NOT NULL AND "COORDENADA-Y" IS NOT NULL
            GROUP BY LUGAR
            ORDER BY num_multas DESC
            LIMIT ?
            """,
            [limit],
        )
        points: list[dict] = []
        for lugar, easting, northing, total, importe in rows:
            longitude, latitude = project_utm(float(easting), float(northing))
            inside = (
                _MADRID_LON[0] <= longitude <= _MADRID_LON[1]
                and _MADRID_LAT[0] <= latitude <= _MADRID_LAT[1]
            )
            if not inside:
                continue
            points.append(
                {
                    "lugar": lugar,
                    "lon": longitude,
                    "lat": latitude,
                    "num_multas": int(total),
                    "importe": float(importe),
                }
            )
        return points

    def _require(self, year: int, month: int) -> Path:
        path = self.path_for(year, month)
        if not path.is_file():
            raise MadridError(f"Month {month:02d}/{year} is not downloaded")
        return path


def project_utm(easting: float, northing: float) -> tuple[float, float]:
    """Converts a file coordinate to WGS84 longitude and latitude."""
    if abs(easting) < _HECTOMETRE_EASTING:
        easting *= 100
        northing *= 100
    longitude, latitude = _TRANSFORMER.transform(easting, northing)
    return float(longitude), float(latitude)


def _parse_label(name: str, prefix: str) -> int | None:
    if not name.startswith(prefix):
        return None
    try:
        return int(name[len(prefix):])
    except ValueError:
        return None


def _rows(path: Path, sql: str, extra: list | None = None) -> list[tuple]:
    parameters: list = [path.as_posix(), *(extra or [])]
    connection = duckdb.connect()
    try:
        return connection.execute(sql, parameters).fetchall()
    except duckdb.Error as exc:
        raise MadridError(f"Could not query the saved month: {exc}") from exc
    finally:
        connection.close()


def _ordered_grades(rows: list[tuple]) -> list[dict]:
    pending = {str(name): int(total) for name, total in rows if name}
    ordered: list[dict] = []
    for name in _GRADE_ORDER:
        if name in pending:
            ordered.append({"calificacion": name, "num_multas": pending.pop(name)})
    for name, total in pending.items():
        ordered.append({"calificacion": name, "num_multas": total})
    return ordered
