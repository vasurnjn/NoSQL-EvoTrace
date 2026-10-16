"""Pytest test configuration and fixtures for NoSQL-EvoTrace."""

import pytest
import mongomock
from evotrace.config import Config
from evotrace.database import get_db, reset_db_connections
from evotrace.web.app import create_app


@pytest.fixture(autouse=True)
def clean_database(monkeypatch):
    """Ensure each test runs with an isolated, clean test database using mongomock."""
    reset_db_connections()
    # Explicitly configure mock mode for automated test suite isolation
    monkeypatch.setattr(Config, "FORCE_MOCK", True)
    test_db = get_db(db_name="test_evotrace", force_mock=True)
    # Drop any existing collections in test db
    for col in test_db.list_collection_names():
        test_db[col].drop()
    yield test_db
    reset_db_connections()


@pytest.fixture
def test_client(clean_database):
    """Provide a Flask test client configured for isolated testing."""
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key",
        "WTF_CSRF_ENABLED": False,
    })
    with app.test_client() as client:
        with app.app_context():
            yield client
