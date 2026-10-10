"""Tests for Cache and CacheURL."""

import os
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from madridfines.cache import Cache, CacheURL
from madridfines.errors import CacheError


def test_cache_init_default_uses_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = Cache("consulta")
    assert cache.app_name == "consulta"
    assert cache.cache_dir.endswith(os.path.join(".my_cache", "consulta"))
    assert cache.obsolescence == 7


@pytest.mark.parametrize("obsolescence", [14, 30, 1, 0])
def test_cache_init_custom(tmp_path, obsolescence):
    cache = Cache("consulta", cache_dir=tmp_path, obsolescence=obsolescence)
    assert str(tmp_path) in cache.cache_dir
    assert cache.obsolescence == obsolescence


@pytest.mark.parametrize("app_name", ["", 123, None])
def test_cache_init_invalid_app_name(tmp_path, app_name):
    with pytest.raises(CacheError):
        Cache(app_name, cache_dir=tmp_path)


@pytest.mark.parametrize("obsolescence", [-1, -10])
def test_cache_init_invalid_obsolescence(tmp_path, obsolescence):
    with pytest.raises(CacheError):
        Cache("consulta", cache_dir=tmp_path, obsolescence=obsolescence)


def test_cache_set_load_exists_and_directory(tmp_path):
    cache = Cache("consulta", cache_dir=tmp_path)
    cache.set("nota", "hola")
    assert cache.exists("nota")
    assert cache.load("nota") == "hola"
    assert not cache.exists("")
    (Path(cache.cache_dir) / "carpeta").mkdir()
    assert not cache.exists("carpeta")
    assert not cache.is_fresh("ausente")
    assert cache.is_fresh("nota")


def test_cache_set_empty_name(tmp_path):
    cache = Cache("consulta", cache_dir=tmp_path)
    with pytest.raises(CacheError):
        cache.set("", "hola")


def test_cache_set_ioerror(tmp_path, monkeypatch):
    cache = Cache("consulta", cache_dir=tmp_path)

    def broken(self, *args, **kwargs):
        raise OSError("disco")

    monkeypatch.setattr(Path, "write_text", broken)
    with pytest.raises(CacheError):
        cache.set("nota", "hola")


def test_cache_load_missing(tmp_path):
    cache = Cache("consulta", cache_dir=tmp_path)
    with pytest.raises(CacheError):
        cache.load("no-esta")


def test_cache_load_ioerror(tmp_path, monkeypatch):
    cache = Cache("consulta", cache_dir=tmp_path)
    cache.set("nota", "hola")

    def broken(self, *args, **kwargs):
        raise OSError("disco")

    monkeypatch.setattr(Path, "read_text", broken)
    with pytest.raises(CacheError):
        cache.load("nota")


def test_cache_how_old(tmp_path):
    cache = Cache("consulta", cache_dir=tmp_path)
    cache.set("nota", "hola")
    age = cache.how_old("nota")
    assert -2000 < age < 5000
    with pytest.raises(CacheError):
        cache.how_old("no-esta")


def test_cache_delete_and_clear(tmp_path):
    cache = Cache("consulta", cache_dir=tmp_path)
    cache.set("uno", "a")
    cache.set("dos", "b")
    cache.delete("uno")
    cache.delete("ausente")
    cache.delete("")
    assert not cache.exists("uno")
    cache.clear()
    assert not cache.exists("dos")


def test_cache_delete_oserror_is_ignored(tmp_path, monkeypatch):
    cache = Cache("consulta", cache_dir=tmp_path)
    cache.set("nota", "hola")

    def broken(self):
        raise OSError("ocupado")

    monkeypatch.setattr(Path, "unlink", broken)
    cache.delete("nota")
    assert cache.exists("nota")


def test_cache_clear_oserror(tmp_path, monkeypatch):
    cache = Cache("consulta", cache_dir=tmp_path)
    cache.set("nota", "hola")

    def broken(self):
        raise OSError("ocupado")

    monkeypatch.setattr(Path, "iterdir", broken)
    with pytest.raises(CacheError):
        cache.clear()


def test_cacheurl_hash_is_stable(tmp_path):
    cache = CacheURL("consulta", cache_dir=tmp_path)
    url = "https://example.com/dato"
    assert len(cache._get_hash_name(url)) == 32
    first = cache._get_hash_name(url, b="2", a="1")
    second = cache._get_hash_name(url, a="1", b="2")
    assert first == second
    assert first != cache._get_hash_name(url)


def _response(text="cuerpo", content=None, error=None):
    response = Mock()
    response.text = text
    response.content = content if content is not None else text.encode("utf-8")
    if error is None:
        response.raise_for_status = Mock()
    else:
        response.raise_for_status = Mock(side_effect=error)
    return response


def test_cacheurl_get_stores_and_reuses(tmp_path, monkeypatch):
    cache = CacheURL("consulta", cache_dir=tmp_path, obsolescence=7)
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _response("cuerpo")

    monkeypatch.setattr(requests, "get", fake_get)
    url = "https://example.com/dato"
    assert cache.get(url) == "cuerpo"
    assert cache.get(url) == "cuerpo"
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 120
    assert cache.exists(url)
    assert cache.load(url) == "cuerpo"
    assert cache.how_old(url) >= 0


def test_cacheurl_get_refreshes_when_stale(tmp_path, monkeypatch):
    cache = CacheURL("consulta", cache_dir=tmp_path, obsolescence=1)
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _response("nuevo")

    monkeypatch.setattr(requests, "get", fake_get)
    url = "https://example.com/dato"
    cache.get(url)
    hashed = Path(cache.cache_dir) / cache._get_hash_name(url)
    old = 1_600_000_000
    os.utime(hashed, (old, old))
    assert cache.get(url) == "nuevo"
    assert len(calls) == 2


def test_cacheurl_refresh_forces_download(tmp_path, monkeypatch):
    cache = CacheURL("consulta", cache_dir=tmp_path)
    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs)
        return _response("otra")

    monkeypatch.setattr(requests, "get", fake_get)
    url = "https://example.com/dato"
    cache.get(url, timeout=30, headers={"User-Agent": "test"})
    cache.get(url, refresh=True, timeout=30, headers={"User-Agent": "test"})
    assert len(calls) == 2
    assert calls[0]["timeout"] == 30
    assert calls[0]["headers"]["User-Agent"] == "test"


def test_cacheurl_encoding_and_request_error(tmp_path, monkeypatch):
    cache = CacheURL("consulta", cache_dir=tmp_path)

    def fake_get(url, **kwargs):
        return _response(content="cañón".encode("latin-1"))

    monkeypatch.setattr(requests, "get", fake_get)
    assert cache.get("https://example.com/dato", encoding="latin-1") == "cañón"

    def broken(url, **kwargs):
        raise requests.RequestException("caído")

    monkeypatch.setattr(requests, "get", broken)
    with pytest.raises(CacheError):
        cache.get("https://example.com/otra", refresh=True)


def test_cacheurl_status_error(tmp_path, monkeypatch):
    cache = CacheURL("consulta", cache_dir=tmp_path)

    def fake_get(url, **kwargs):
        return _response(error=requests.HTTPError("500"))

    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(CacheError):
        cache.get("https://example.com/dato")


def test_cacheurl_load_missing_and_delete(tmp_path):
    cache = CacheURL("consulta", cache_dir=tmp_path)
    url = "https://example.com/dato"
    with pytest.raises(CacheError):
        cache.load(url)
    cache.set(cache._get_hash_name(url, q="1"), "dato")
    assert cache.exists(url, q="1")
    assert not cache.exists(url, q="2")
    cache.delete(url, q="1")
    assert not cache.exists(url, q="1")
