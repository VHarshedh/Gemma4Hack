"""
Aegis — Shared pytest fixtures.
Seeds the SQLite GIS database once per session before any test module
imports the app (which calls GISDatabase at module level).
"""
import pytest
import setup_db
from config import DATABASE_PATH


@pytest.fixture(scope="session", autouse=True)
def seed_database():
    """Ensure the GIS database exists and is freshly seeded before any test runs."""
    setup_db.create_database(DATABASE_PATH, reset=True)
    yield
