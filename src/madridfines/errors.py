"""Package errors."""


class CacheError(Exception):
    """Failure while reading, writing, or downloading the cache."""


class MadridError(Exception):
    """Failure while locating, cleaning, or querying the fines."""
