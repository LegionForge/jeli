"""Unit tests for normalize_sync_db_url()."""

from jeli_scoped_mcp.database.url import normalize_sync_db_url


def test_plain_postgresql_gets_psycopg2():
    assert (
        normalize_sync_db_url("postgresql://u:p@h:5432/db")
        == "postgresql+psycopg2://u:p@h:5432/db"
    )


def test_psycopg2_url_untouched():
    u = "postgresql+psycopg2://u@h/db"
    assert normalize_sync_db_url(u) == u


def test_psycopg3_url_untouched():
    u = "postgresql+psycopg://u@h/db"
    assert normalize_sync_db_url(u) == u
