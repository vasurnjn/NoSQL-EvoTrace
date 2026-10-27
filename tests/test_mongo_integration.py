"""Integration tests verifying real MongoDB connectivity, status reporting, and refusal to fallback silently."""

import pytest
import pymongo
from pymongo.errors import ServerSelectionTimeoutError
from evotrace.config import Config
from evotrace.database import get_client, get_db, get_connection_status, reset_db_connections


def test_mongodb_connection_status_reporting():
    """Verify that connection status truthfully reports active backend and diagnostic keys."""
    status = get_connection_status()

    # The status must always contain standard diagnostic keys
    assert "connected" in status
    assert "is_real_mongo" in status
    assert "is_mock" in status
    assert "backend_name" in status
    assert "server_version" in status
    assert "database" in status
    assert status["database"] == Config.MONGO_DB_NAME

    if status["is_real_mongo"]:
        assert status["connected"] is True
        assert "MongoDB Community Server" in status["backend_name"]
        client = pymongo.MongoClient(Config.MONGO_URI, serverSelectionTimeoutMS=2000)
        res = client.admin.command("ping")
        assert res.get("ok") == 1.0
    elif status["is_mock"]:
        assert status["connected"] is True
        assert "mongomock" in status["backend_name"]
    else:
        assert status["connected"] is False
        assert status["backend_name"] == "Disconnected"


def test_no_silent_fallback_when_mongo_unreachable(monkeypatch):
    """Verify that when real MongoDB is unreachable, the application fails clearly instead of silently switching to mongomock."""
    reset_db_connections()

    # Configure an unreachable MongoDB port with fallback strictly disabled
    monkeypatch.setattr(Config, "MONGO_URI", "mongodb://127.0.0.1:27999/")
    monkeypatch.setattr(Config, "MONGO_SERVER_SELECTION_TIMEOUT_MS", 300)
    monkeypatch.setattr(Config, "MONGO_CONNECT_TIMEOUT_MS", 300)
    monkeypatch.setattr(Config, "ALLOW_MOCK_FALLBACK", False)
    monkeypatch.setattr(Config, "FORCE_MOCK", False)

    # get_client() must raise ConnectionError with descriptive message
    with pytest.raises(ConnectionError) as exc_info:
        get_client()

    err_msg = str(exc_info.value)
    assert "Failed to connect to MongoDB Community Server" in err_msg
    assert "27999" in err_msg

    # get_connection_status() must report Disconnected without throwing an unhandled exception
    status = get_connection_status()
    assert status["connected"] is False
    assert status["is_real_mongo"] is False
    assert status["is_mock"] is False
    assert status["backend_name"] == "Disconnected"
    assert status["error"] is not None

    reset_db_connections()


def test_explicit_mongomock_testing_status(monkeypatch):
    """Verify that when FORCE_MOCK is active for test isolation, connection status clearly labels mongomock (Testing Only)."""
    reset_db_connections()
    monkeypatch.setattr(Config, "FORCE_MOCK", True)

    status = get_connection_status()
    assert status["connected"] is True
    assert status["is_real_mongo"] is False
    assert status["is_mock"] is True
    assert status["backend_name"] == "mongomock (Testing Only)"

    reset_db_connections()


def test_real_mongodb_live_crud(monkeypatch):
    """Verify real MongoDB Community Server CRUD operations if the local daemon is active."""
    reset_db_connections()
    monkeypatch.setattr(Config, "FORCE_MOCK", False)
    monkeypatch.setattr(Config, "ALLOW_MOCK_FALLBACK", False)

    try:
        real_client = pymongo.MongoClient(Config.MONGO_URI, serverSelectionTimeoutMS=1500)
        real_client.admin.command("ping")
    except Exception:
        pytest.skip("Local MongoDB Community Server daemon not currently reachable on 127.0.0.1:27017.")

    db = get_db(db_name="nosql_evotrace")
    test_col = db["_test_community_server_verification"]

    # Insert test document
    doc = {"test_key": "community_server_val", "version": 1}
    ins_res = test_col.insert_one(doc)
    assert ins_res.inserted_id is not None

    # Read back document
    found = test_col.find_one({"_id": ins_res.inserted_id})
    assert found is not None
    assert found["test_key"] == "community_server_val"

    # Cleanup test collection
    test_col.drop()
    reset_db_connections()
