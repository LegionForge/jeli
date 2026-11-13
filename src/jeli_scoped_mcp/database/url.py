"""Database URL helpers."""


def normalize_sync_db_url(url: str) -> str:
    """Pin a bare ``postgresql://`` URL to the psycopg2 driver.

    SQLAlchemy 2.1 defaults bare ``postgresql://`` to psycopg v3, but this
    project installs psycopg2. URLs that already name a driver are untouched.
    """
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url
