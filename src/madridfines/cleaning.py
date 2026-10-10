"""Cleaning of the traffic-fine CSVs."""

from __future__ import annotations

import pandas as pd

from madridfines.errors import MadridError

_NUMERIC_COLUMNS = ("VEL_LIMITE", "VEL_CIRCULA", "IMP_BOL", "PUNTOS", "HORA", "MES", "ANIO")
_COORD_X = ("COORDENADA-X", "COORDENADA_X")
_COORD_Y = ("COORDENADA-Y", "COORDENADA_Y")


def clean(df: pd.DataFrame) -> None:
    """Normalizes a fines dataframe, in place.

    Strips spaces from text and from column names, converts amounts, speeds,
    and coordinates to numbers, and builds a ``fecha`` index from ``ANIO``,
    ``MES``, and ``HORA``. ``HORA`` arrives as a decimal: ``8.21`` is 08:21,
    not 8.21 hours.

    >>> import pandas as pd
    >>> from madridfines.cleaning import clean
    >>> frame = pd.DataFrame({
    ...     "CALIFICACION": [" LEVE "],
    ...     "MES": [12],
    ...     "ANIO": [2024],
    ...     "HORA": [10.5],
    ...     "IMP_BOL": [" 90.00"],
    ...     "DESCUENTO": [" SI "],
    ... })
    >>> clean(frame)
    >>> frame["CALIFICACION"].iloc[0]
    'LEVE'
    >>> frame.index[0].hour
    10
    >>> frame.index[0].minute
    50
    """
    try:
        df.rename(columns=str.strip, inplace=True)
        for column in df.columns:
            if _is_text(df[column]):
                df[column] = df[column].astype("string").str.strip()
        for column in _NUMERIC_COLUMNS:
            if column in df.columns:
                df[column] = pd.to_numeric(df[column], errors="coerce")
        _standardize_coordinate(df, _COORD_X, "COORDENADA-X")
        _standardize_coordinate(df, _COORD_Y, "COORDENADA-Y")
        _set_timestamp_index(df)
    except MadridError:
        raise
    except Exception as exc:
        raise MadridError(f"Could not clean the data: {exc}") from exc


def _is_text(column: pd.Series) -> bool:
    return bool(pd.api.types.is_string_dtype(column) or column.dtype == object)


def _standardize_coordinate(df: pd.DataFrame, aliases: tuple[str, ...], target: str) -> None:
    source = next((column for column in aliases if column in df.columns), None)
    if source is None:
        return
    df[source] = pd.to_numeric(df[source], errors="coerce")
    if source != target:
        df.rename(columns={source: target}, inplace=True)


def _set_timestamp_index(df: pd.DataFrame) -> None:
    if not all(column in df.columns for column in ("ANIO", "MES", "HORA")):
        return
    centihour = (df["HORA"] * 100).round()
    hour = (centihour // 100).clip(lower=0, upper=23).fillna(0).astype(int)
    minute = (centihour % 100).clip(lower=0, upper=59).fillna(0).astype(int)
    df["fecha"] = pd.to_datetime(
        {
            "year": df["ANIO"],
            "month": df["MES"],
            "day": 1,
            "hour": hour,
            "minute": minute,
        },
        errors="coerce",
    )
    if len(df) and df["fecha"].isna().all():
        raise MadridError("Could not build the fine timestamps")
    df.set_index("fecha", drop=True, inplace=True)
