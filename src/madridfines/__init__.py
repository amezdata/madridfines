"""Download, clean, and browse Madrid traffic fines."""

from madridfines.cache import Cache, CacheURL
from madridfines.errors import CacheError, MadridError
from madridfines.fines import MadridFines, get_url

__all__ = [
    "Cache",
    "CacheError",
    "CacheURL",
    "MadridError",
    "MadridFines",
    "get_url",
]

__version__ = "1.0.0"
