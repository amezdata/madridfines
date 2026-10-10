"""Local cache of files and HTTP responses."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Optional

import requests
import truststore

from madridfines.errors import CacheError

truststore.inject_into_ssl()

__all__ = ["Cache", "CacheError", "CacheURL"]

_MS_PER_DAY = 24 * 60 * 60 * 1000
_DEFAULT_TIMEOUT_SECONDS = 120


class Cache:
    """Stores and retrieves text in a local directory.

    Each application uses its own subfolder. The data stays on disk
    after the process exits.

    >>> import tempfile
    >>> cache = Cache("demo", cache_dir=tempfile.mkdtemp())
    >>> cache.set("note.txt", "hello")
    >>> cache.load("note.txt")
    'hello'
    >>> cache.exists("note.txt")
    True
    >>> cache.exists("")
    False
    """

    def __init__(
        self,
        app_name: str,
        cache_dir: Optional[Path] = None,
        obsolescence: int = 7,
    ) -> None:
        """Creates the cache directory if it does not exist yet.

        app_name is the subfolder name. cache_dir, when omitted, sits in
        the user home, inside ``.my_cache``. obsolescence is the number of
        days an entry stays current.
        """
        if not app_name or not isinstance(app_name, str):
            raise CacheError("app_name must be a non-empty string")
        if obsolescence < 0:
            raise CacheError("obsolescence must not be negative")

        self._app_name = app_name
        root = Path.home() / ".my_cache" if cache_dir is None else Path(cache_dir)
        self._cache_dir = root / app_name
        self._obsolescence = obsolescence
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def app_name(self) -> str:
        """Name of the application that owns this cache."""
        return self._app_name

    @property
    def cache_dir(self) -> str:
        """Path of the subfolder where files are written."""
        return str(self._cache_dir)

    @property
    def obsolescence(self) -> int:
        """Days an entry stays current."""
        return self._obsolescence

    def set(self, name: str, data: str) -> None:
        """Writes ``data`` to a file named ``name``."""
        if not name:
            raise CacheError("The name cannot be empty")
        file_path = self._cache_dir / name
        try:
            file_path.write_text(data, encoding="utf-8")
        except OSError as exc:
            raise CacheError(f"Could not write to the cache: {exc}") from exc

    def _file_exists(self, name: str) -> bool:
        if not name:
            return False
        return (self._cache_dir / name).is_file()

    def exists(self, name: str) -> bool:
        """Whether ``name`` is a file present in the cache."""
        return self._file_exists(name)

    def load(self, name: str) -> str:
        """Returns the text stored under ``name``."""
        file_path = self._cache_dir / name
        if not self._file_exists(name):
            raise CacheError(f"File '{name}' is not in the cache")
        try:
            return file_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CacheError(f"Could not read from the cache: {exc}") from exc

    def _age_ms(self, name: str) -> float:
        file_path = self._cache_dir / name
        if not self._file_exists(name):
            raise CacheError(f"File '{name}' is not in the cache")
        return max(0.0, (time.time() - file_path.stat().st_mtime) * 1000)

    def how_old(self, name: str) -> float:
        """Age of the file in milliseconds."""
        return self._age_ms(name)

    def is_fresh(self, name: str) -> bool:
        """True when the file exists and is within ``obsolescence``.

        Uses the file path, not the subclass ``how_old`` method, so a hash
        is not hashed again.
        """
        if not self._file_exists(name):
            return False
        return self._age_ms(name) <= self._obsolescence * _MS_PER_DAY

    def delete(self, name: str) -> None:
        """Deletes a file. Does nothing when it is missing."""
        if not name:
            return
        file_path = self._cache_dir / name
        if not self._file_exists(name):
            return
        try:
            file_path.unlink()
        except OSError:
            return

    def clear(self) -> None:
        """Deletes every file in the cache."""
        try:
            for file_path in self._cache_dir.iterdir():
                if file_path.is_file():
                    file_path.unlink()
        except OSError as exc:
            raise CacheError(f"Could not clear the cache: {exc}") from exc


class CacheURL(Cache):
    """Cache of HTTP responses, named from an MD5 hash.

    The URL is not used as a file name: it can be long and contain
    characters the operating system rejects. Request parameters go into
    the hash, so two different queries do not share a file. ``encoding``
    and ``refresh`` are not part of the hash.
    """

    def _get_hash_name(self, url: str, **kwargs: object) -> str:
        """MD5 hash of the URL and the parameters, in a stable order."""
        hash_string = url
        if kwargs:
            hash_string += str(sorted(kwargs.items()))
        return hashlib.md5(hash_string.encode("utf-8")).hexdigest()

    def get(
        self,
        url: str,
        encoding: Optional[str] = None,
        refresh: bool = False,
        **kwargs: object,
    ) -> str:
        """Returns the body of ``url``, from the cache when it is still current.

        When the entry is past ``obsolescence``, it is downloaded again.
        ``encoding`` forces how the bytes are decoded (for example
        ``latin-1`` for the City Council CSVs). ``refresh=True`` ignores
        the local copy.
        """
        name = self._get_hash_name(url, **kwargs)
        if not refresh and super().is_fresh(name):
            return super().load(name)

        request_kwargs = dict(kwargs)
        request_kwargs.setdefault("timeout", _DEFAULT_TIMEOUT_SECONDS)
        try:
            response = requests.get(url, **request_kwargs)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise CacheError(f"Could not download the URL: {exc}") from exc

        if encoding:
            data = response.content.decode(encoding)
        else:
            data = response.text
        self.set(name, data)
        return data

    def exists(self, url: str, **kwargs: object) -> bool:
        """Whether there is a local copy of this request."""
        return super().exists(self._get_hash_name(url, **kwargs))

    def load(self, url: str, **kwargs: object) -> str:
        """Reads the local copy. Fails when it does not exist."""
        return super().load(self._get_hash_name(url, **kwargs))

    def how_old(self, url: str, **kwargs: object) -> float:
        """Age, in milliseconds, of the local copy of this request."""
        return super().how_old(self._get_hash_name(url, **kwargs))

    def delete(self, url: str, **kwargs: object) -> None:
        """Deletes the local copy of this request."""
        super().delete(self._get_hash_name(url, **kwargs))
