"""In-memory lookup of one or more months of fines."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from madridfines.cache import CacheURL
from madridfines.catalog import get_url
from madridfines.cleaning import clean
from madridfines.errors import MadridError

__all__ = ["MadridError", "MadridFines", "get_url"]


class MadridFines:
    """Downloads, cleans, and summarizes fines for one or more months.

    ``data`` and ``loaded`` are read, not reassigned from outside.
    The chart from :meth:`fines_hour` is saved to disk and not shown
    on screen.

    >>> import tempfile
    >>> fines = MadridFines("demo", cache_dir=tempfile.mkdtemp())
    >>> fines.loaded
    []
    >>> fines.data.empty
    True
    """

    def __init__(
        self,
        app_name: str,
        obsolescence: int = 7,
        cache_dir: Optional[Path] = None,
    ) -> None:
        """Prepares the cache. ``data`` starts empty."""
        self.cacheurl = CacheURL(
            app_name=app_name,
            obsolescence=obsolescence,
            cache_dir=cache_dir,
        )
        self._data = pd.DataFrame()
        self._loaded: list[tuple[int, int]] = []

    @property
    def data(self) -> pd.DataFrame:
        """Copy of the accumulated dataframe."""
        return self._data.copy()

    @property
    def loaded(self) -> list[tuple[int, int]]:
        """Months already added, as ``(month, year)`` pairs."""
        return self._loaded.copy()

    @staticmethod
    def load(year: int, month: int, cacheurl: CacheURL) -> pd.DataFrame:
        """Downloads the month's CSV and returns it uncleaned."""
        url = get_url(year, month, cache=cacheurl)
        try:
            content = cacheurl.get(url, encoding="latin-1")
            content = content.replace("\x00", "")
            return pd.read_csv(io.StringIO(content), sep=";")
        except Exception as exc:
            raise MadridError(f"Could not load the data: {exc}") from exc

    @staticmethod
    def clean(df: pd.DataFrame) -> None:
        """Cleans ``df`` in place. See :func:`madridfines.cleaning.clean`."""
        clean(df)

    def add(self, year: int, month: Optional[int] = None) -> None:
        """Adds one month, or every available month of a year.

        A month already in ``loaded`` is not downloaded again. A month
        missing from the catalog is skipped when the whole year is requested.
        """
        if month is None:
            for candidate in range(1, 13):
                try:
                    self.add(year, candidate)
                except MadridError:
                    continue
            return
        if (month, year) in self._loaded:
            return
        frame = self.load(year, month, self.cacheurl)
        self.clean(frame)
        if self._data.empty:
            self._data = frame
        else:
            self._data = pd.concat([self._data, frame], ignore_index=False)
        self._loaded.append((month, year))

    def fines_hour(self, fig_name: str) -> None:
        """Saves a PNG of fines by hour, one line per month."""
        if self._data.empty:
            raise MadridError("No data loaded to draw the chart")
        if not isinstance(self._data.index, pd.DatetimeIndex):
            raise MadridError("The data has no date index")
        frame = pd.DataFrame(
            {
                "hour": self._data.index.hour,
                "month": self._data.index.month,
                "year": self._data.index.year,
            }
        )
        counts = (
            frame.groupby(["hour", "month", "year"]).size().reset_index(name="num_multas")
        )
        figure, axis = plt.subplots(figsize=(10, 6))
        try:
            for (month, year), group in counts.groupby(["month", "year"]):
                axis.plot(
                    group["hour"],
                    group["num_multas"],
                    marker="o",
                    linewidth=2,
                    label=f"{int(month):02d}/{int(year)}",
                )
            axis.set_title("Fines by hour")
            axis.set_xlabel("Hour")
            axis.set_ylabel("Number of fines")
            axis.set_xticks(range(0, 24, 5))
            axis.set_xlim(-0.5, 23.5)
            axis.legend(title="Month/Year", loc="best")
            axis.grid(True, alpha=0.3, linestyle="--")
            figure.tight_layout()
            figure.savefig(fig_name)
        except Exception as exc:
            raise MadridError(f"Could not save the chart: {exc}") from exc
        finally:
            plt.close(figure)

    def fines_calification(self) -> pd.DataFrame:
        """Counts fines by severity, month, and year."""
        if self._data.empty:
            raise MadridError("No data loaded")
        if "CALIFICACION" not in self._data.columns:
            raise MadridError("Column CALIFICACION is not in the data")
        frame = self._data.copy()
        frame["MES"] = frame.index.month
        frame["ANIO"] = frame.index.year
        return frame.groupby(["MES", "ANIO", "CALIFICACION"]).size().unstack(fill_value=0)

    def total_payment(self) -> pd.DataFrame:
        """Full amount and early-payment estimate, by month and year.

        The file does not say whether the fine was paid. The minimum applies
        the 50% rate only to rows with ``DESCUENTO == 'SI'``.
        """
        if self._data.empty:
            raise MadridError("No data loaded")
        if "IMP_BOL" not in self._data.columns:
            raise MadridError("Column IMP_BOL is not in the data")
        frame = self._data.copy()
        frame["MES"] = frame.index.month
        frame["ANIO"] = frame.index.year
        discounted = frame["DESCUENTO"].eq("SI")
        frame["importe_minimo"] = frame["IMP_BOL"].where(~discounted, frame["IMP_BOL"] * 0.5)
        result = frame.groupby(["MES", "ANIO"], as_index=False).agg(
            num_multas=("IMP_BOL", "count"),
            importe_max=("IMP_BOL", "sum"),
            importe_min=("importe_minimo", "sum"),
        )
        return result
