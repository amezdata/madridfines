"""Tests for cleaning, MadridFines, and the chart saved to disk."""

from unittest.mock import Mock

import matplotlib.pyplot as plt
import pandas as pd
import pytest

from madridfines.cache import CacheURL
from madridfines.errors import CacheError, MadridError
from madridfines.fines import MadridFines

CSV = (
    "CALIFICACION;LUGAR;MES;ANIO;HORA;IMP_BOL;DESCUENTO; PUNTOS;"
    "DENUNCIANTE;HECHO-BOL;VEL_LIMITE;VEL_CIRCULA ;COORDENADA-X;COORDENADA-Y\n"
    "LEVE      ;DOCTOR VELASCO, 1                       ;04;2026;08.21;  90.00;SI;00;"
    "AGENTES DE MOVILIDAD          ;ESTACIONAR.\x00;   ;   ;    4414.78;   44735.65\n"
)


def _before_clean():
    return pd.DataFrame(
        {
            "CALIFICACION": [" LEVE ", "GRAVE"],
            "LUGAR": ["Calle Test", "Calle Otra"],
            "MES": [12, 12],
            "ANIO": [2024, 2024],
            "HORA": [10.5, 15.3],
            "IMP_BOL": [100.0, 200.0],
            "DESCUENTO": ["SI", "NO"],
            "VEL_LIMITE": ["30", "50"],
            "VEL_CIRCULA": ["35", "55"],
            "COORDENADA-X": ["4474540", "4474550"],
            "COORDENADA-Y": ["441089", "441090"],
        }
    )


def test_clean_real_shaped_row_and_coordinate_alias():
    frame = pd.read_csv(pd.io.common.StringIO(CSV.replace("\x00", "")), sep=";")
    MadridFines.clean(frame)
    assert frame["CALIFICACION"].iloc[0] == "LEVE"
    assert frame["LUGAR"].iloc[0] == "DOCTOR VELASCO, 1"
    assert frame["IMP_BOL"].iloc[0] == 90
    assert frame["PUNTOS"].iloc[0] == 0
    assert frame.index[0].hour == 8
    assert frame.index[0].minute == 21
    assert frame["COORDENADA-X"].iloc[0] == pytest.approx(4414.78)

    aliased = pd.DataFrame(
        {
            "MES": [5],
            "ANIO": [2019],
            "HORA": [9],
            "COORDENADA_X": [440000],
            "COORDENADA_Y": [4474000.0],
        }
    )
    MadridFines.clean(aliased)
    assert aliased["COORDENADA-X"].iloc[0] == 440000
    assert isinstance(aliased.index, pd.DatetimeIndex)


def test_clean_without_date_and_with_unusable_dates():
    bare = pd.DataFrame({"LUGAR": [" SOL "]})
    MadridFines.clean(bare)
    assert bare["LUGAR"].iloc[0] == "SOL"
    assert not isinstance(bare.index, pd.DatetimeIndex)

    broken = pd.DataFrame({"ANIO": ["no"], "MES": ["no"], "HORA": ["no"]})
    with pytest.raises(MadridError):
        MadridFines.clean(broken)


def test_clean_wraps_unexpected_errors(monkeypatch):
    frame = _before_clean()

    def boom(*args, **kwargs):
        raise RuntimeError("fecha")

    monkeypatch.setattr("madridfines.cleaning.pd.to_datetime", boom)
    with pytest.raises(MadridError):
        MadridFines.clean(frame)


def test_properties_are_copies(tmp_path):
    fines = MadridFines("consulta", cache_dir=tmp_path)
    assert isinstance(fines.cacheurl, CacheURL)
    assert fines.data.empty
    fines._loaded.append((4, 2026))
    exported = fines.loaded
    exported.append((5, 2026))
    assert fines.loaded == [(4, 2026)]


def test_load_reads_latin1_csv(tmp_path, monkeypatch):
    monkeypatch.setattr("madridfines.fines.get_url", lambda *args, **kwargs: "https://example.com/m.csv")
    cache = CacheURL("consulta", cache_dir=tmp_path)
    cache.get = Mock(return_value=CSV)
    frame = MadridFines.load(2026, 4, cache)
    assert "CALIFICACION" in frame.columns
    cache.get.assert_called_once()
    assert "\x00" not in frame["HECHO-BOL"].iloc[0]


def test_load_wraps_download_errors(tmp_path, monkeypatch):
    monkeypatch.setattr("madridfines.fines.get_url", lambda *args, **kwargs: "https://example.com/m.csv")
    cache = CacheURL("consulta", cache_dir=tmp_path)
    cache.get = Mock(side_effect=CacheError("caído"))
    with pytest.raises(MadridError):
        MadridFines.load(2026, 4, cache)


def test_add_month_duplicate_and_partial_year(tmp_path, monkeypatch):
    sample = _before_clean()

    def fake_load(year, month, cacheurl):
        if month == 2:
            raise MadridError("no publicado")
        return sample.copy()

    monkeypatch.setattr(MadridFines, "load", staticmethod(fake_load))
    fines = MadridFines("consulta", cache_dir=tmp_path)
    fines.add(2024, 12)
    fines.add(2024, 12)
    assert fines.loaded == [(12, 2024)]
    fines.add(2024, 11)
    assert len(fines.data) == 4
    fines.add(2024)
    assert (2, 2024) not in fines.loaded
    assert (1, 2024) in fines.loaded


def test_fines_hour_saves_png_without_showing(tmp_path, monkeypatch):
    def fail_show(*args, **kwargs):
        raise AssertionError("plt.show must not be called")

    monkeypatch.setattr(plt, "show", fail_show)
    fines = MadridFines("consulta", cache_dir=tmp_path)
    with pytest.raises(MadridError):
        fines.fines_hour("vacio.png")
    fines._data = pd.DataFrame({"CALIFICACION": ["LEVE"]})
    with pytest.raises(MadridError):
        fines.fines_hour("sin-fecha.png")

    fines._data = pd.DataFrame({"CALIFICACION": ["LEVE", "GRAVE"]})
    fines._data.index = pd.to_datetime(["2024-12-01 10:00:00", "2024-11-01 11:00:00"])
    output = tmp_path / "horas.png"
    fines.fines_hour(str(output))
    assert output.is_file()
    with pytest.raises(MadridError):
        fines.fines_hour(str(tmp_path / "no-existe" / "horas.png"))


def test_calification_and_payment(tmp_path):
    fines = MadridFines("consulta", cache_dir=tmp_path)
    with pytest.raises(MadridError):
        fines.fines_calification()
    with pytest.raises(MadridError):
        fines.total_payment()

    fines._data = pd.DataFrame({"IMP_BOL": [1.0]})
    fines._data.index = pd.to_datetime(["2024-12-01 10:00:00"])
    with pytest.raises(MadridError):
        fines.fines_calification()

    fines._data = pd.DataFrame({"CALIFICACION": ["LEVE"]})
    fines._data.index = pd.to_datetime(["2024-12-01 10:00:00"])
    with pytest.raises(MadridError):
        fines.total_payment()

    fines._data = pd.DataFrame(
        {
            "CALIFICACION": ["LEVE", "GRAVE", "LEVE", "MUY GRAVE"],
            "IMP_BOL": [100.0, 200.0, 150.0, 300.0],
            "DESCUENTO": ["SI", "SI", "NO", "NO"],
        }
    )
    fines._data.index = pd.to_datetime(
        ["2024-12-01 10:00:00", "2024-12-01 11:00:00", "2024-12-01 12:00:00", "2024-12-01 13:00:00"]
    )
    grades = fines.fines_calification()
    assert grades.loc[(12, 2024), "LEVE"] == 2
    payments = fines.total_payment()
    assert payments["num_multas"].iloc[0] == 4
    assert payments["importe_max"].iloc[0] == 750
    assert payments["importe_min"].iloc[0] == 100 * 0.5 + 200 * 0.5 + 150 + 300
