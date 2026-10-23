from urllib.parse import urlsplit

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def assert_safe_test_database(url: str) -> None:
    """The suite TRUNCATEs and drops tables: refuse anything but a local *_test database."""
    parts = urlsplit(url.replace("+asyncpg", ""))
    name = parts.path.lstrip("/")
    if parts.hostname not in LOCAL_HOSTS or not name.endswith("_test"):
        raise RuntimeError(
            f"Refusing to run tests against {parts.hostname}/{name}: "
            "tests need a local database whose name ends in _test (see compose.test.yml)."
        )
