from urllib.parse import urlsplit

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def assert_safe_test_database(uri: str, db_name: str) -> None:
    """The suite deletes every document: refuse anything but a local *_test database."""
    host = urlsplit(uri).hostname
    if not uri.startswith("mongodb://") or host not in LOCAL_HOSTS or not db_name.endswith("_test"):
        raise RuntimeError(
            f"Refusing to run tests against {host}/{db_name}: tests need a local MongoDB "
            "(mongodb://localhost...) and a database name ending in _test (see compose.test.yml)."
        )
