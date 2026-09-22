"""Database connection and lifecycle manager for NoSQL-EvoTrace.

Provides access to real MongoDB instances via PyMongo, with transparent fallback
to mongomock when running in local development or test environments where MongoDB
service is not actively running. Supports file-backed persistence for the mock
client to ensure state survives across CLI invocations and web worker restarts.
"""

from __future__ import annotations
import atexit
import logging
from pathlib import Path
import re
from typing import Any, Dict, Optional, Tuple
from bson import json_util
import pymongo
from pymongo.database import Database
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError

from evotrace.config import Config, BASE_DIR

logger = logging.getLogger(__name__)

MOCK_STORE_FILE = BASE_DIR / ".evotrace_mock_store.json"

def sanitize_mongo_uri(uri: str) -> str:
    """Mask credentials in MongoDB connection string to avoid leaking passwords in UI or logs."""
    if not uri:
        return ""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", uri)

# Module-level singletons
_real_client: Optional[pymongo.MongoClient] = None
_mock_client: Optional[Any] = None
_is_using_mock: bool = False
_has_attempted_connection: bool = False
_connection_error_message: Optional[str] = None


def _load_mock_store(client: Any) -> None:
    """Load persisted BSON documents into mongomock client collections."""
    if not MOCK_STORE_FILE.exists():
        return
    try:
        raw_text = MOCK_STORE_FILE.read_text(encoding="utf-8")
        if not raw_text.strip():
            return
        data = json_util.loads(raw_text)
        if isinstance(data, dict):
            for db_name, collections in data.items():
                db = client[db_name]
                for col_name, docs in collections.items():
                    if docs:
                        db[col_name].drop()
                        db[col_name].insert_many(docs)
        logger.info("Restored persisted mongomock store from %s", MOCK_STORE_FILE)
    except Exception as exc:
        logger.warning("Failed to restore persisted mongomock store: %s", exc)


def save_mock_state() -> None:
    """Persist all collections from mongomock client to disk."""
    global _mock_client, _is_using_mock
    if not _is_using_mock or _mock_client is None:
        return
    try:
        store_data: Dict[str, Dict[str, list]] = {}
        target_db_name = Config.MONGO_DB_NAME
        db = _mock_client[target_db_name]
        col_names = db.list_collection_names()
        store_data[target_db_name] = {}
        for col in col_names:
            docs = list(db[col].find({}))
            store_data[target_db_name][col] = docs

        serialized = json_util.dumps(store_data, indent=2)
        MOCK_STORE_FILE.write_text(serialized, encoding="utf-8")
        logger.debug("Persisted mongomock state to %s", MOCK_STORE_FILE)
    except Exception as exc:
        logger.warning("Failed to persist mongomock state: %s", exc)


# Automatically persist mock state on program termination
atexit.register(save_mock_state)


def get_client(force_mock: bool = False) -> Any:
    """Obtain or initialize the MongoDB client instance.
    
    Backend Selection Rules:
    1. If force_mock=True or Config.FORCE_MOCK=True, uses mongomock for test isolation.
    2. Otherwise, connects directly to real MongoDB Community Server at Config.MONGO_URI.
    3. If MongoDB Community Server is unreachable and ALLOW_MOCK_FALLBACK is False (default),
       raises a clear ConnectionError and refuses to silently fallback to mongomock.
    """
    global _real_client, _mock_client, _is_using_mock, _has_attempted_connection, _connection_error_message

    if force_mock or Config.FORCE_MOCK:
        if _mock_client is None:
            import mongomock
            _mock_client = mongomock.MongoClient()
            _load_mock_store(_mock_client)
            logger.info("Initialized dedicated mongomock client for test isolation.")
        _is_using_mock = True
        return _mock_client

    if _mock_client is not None and _is_using_mock:
        return _mock_client

    if _real_client is not None:
        return _real_client

    # Attempt connection to real MongoDB Community Server
    try:
        client = pymongo.MongoClient(
            Config.MONGO_URI,
            serverSelectionTimeoutMS=Config.MONGO_SERVER_SELECTION_TIMEOUT_MS,
            connectTimeoutMS=Config.MONGO_CONNECT_TIMEOUT_MS,
        )
        client.admin.command("ping")
        _real_client = client
        _is_using_mock = False
        _connection_error_message = None
        _has_attempted_connection = True
        logger.info("Successfully connected to real MongoDB Community Server at %s", Config.MONGO_URI)
        return _real_client
    except (ServerSelectionTimeoutError, PyMongoError) as exc:
        _connection_error_message = str(exc)
        _has_attempted_connection = True
        logger.error(
            "Could not connect to MongoDB Community Server at %s: %s",
            Config.MONGO_URI,
            exc,
        )
        if Config.ALLOW_MOCK_FALLBACK:
            import mongomock
            if _mock_client is None:
                _mock_client = mongomock.MongoClient()
                _load_mock_store(_mock_client)
            _is_using_mock = True
            logger.warning("Fell back to mongomock embedded client (ALLOW_MOCK_FALLBACK=True).")
            return _mock_client
        raise ConnectionError(
            f"Failed to connect to MongoDB Community Server at {Config.MONGO_URI} "
            f"(Database: '{Config.MONGO_DB_NAME}'). Ensure the MongoDB service (mongod) is running. "
            f"Error details: {exc}"
        ) from exc


def get_db(db_name: Optional[str] = None, force_mock: bool = False) -> Database:
    """Get the database handle for the requested or default database name."""
    client = get_client(force_mock=force_mock)
    target_db_name = db_name or Config.MONGO_DB_NAME
    return client[target_db_name]


def get_connection_status() -> Dict[str, Any]:
    """Inspect and report the active MongoDB connection status and capabilities.
    
    Clearly distinguishes:
    - MongoDB Community Server (Live)
    - mongomock (Testing Only)
    - Disconnected
    """
    global _real_client, _is_using_mock, _connection_error_message

    try:
        client = get_client()
    except Exception as exc:
        return {
            "connected": False,
            "is_real_mongo": False,
            "is_mock": False,
            "backend_name": "Disconnected",
            "backend_display": "Disconnected",
            "uri": sanitize_mongo_uri(Config.MONGO_URI),
            "database": Config.MONGO_DB_NAME,
            "server_version": "N/A",
            "supports_transactions": False,
            "error": str(exc),
        }

    if _is_using_mock or (client.__class__.__name__ == "MongoClient" and "mongomock" in client.__class__.__module__):
        return {
            "connected": True,
            "is_real_mongo": False,
            "is_mock": True,
            "backend_name": "mongomock (Testing Only)",
            "backend_display": "mongomock (Testing Only)",
            "uri": "mongomock://in-memory",
            "database": Config.MONGO_DB_NAME,
            "server_version": "mongomock-4.x",
            "supports_transactions": False,
            "error": _connection_error_message,
        }

    try:
        build_info = client.admin.command("buildinfo")
        version = build_info.get("version", "unknown")
        hello_info = client.admin.command("hello") if "hello" in client.admin.command("listCommands") else client.admin.command("isMaster")
        is_replica_set = bool(hello_info.get("setName"))
        is_mongos = hello_info.get("msg") == "isdbgrid"
        supports_transactions = is_replica_set or is_mongos

        return {
            "connected": True,
            "is_real_mongo": True,
            "is_mock": False,
            "backend_name": "MongoDB Community Server (Live)",
            "backend_display": f"MongoDB Community Server v{version} (Live)",
            "uri": sanitize_mongo_uri(Config.MONGO_URI),
            "database": Config.MONGO_DB_NAME,
            "server_version": version,
            "supports_transactions": supports_transactions,
            "deployment_type": "ReplicaSet" if is_replica_set else ("Sharded" if is_mongos else "Standalone"),
            "error": None,
        }
    except Exception as exc:
        return {
            "connected": False,
            "is_real_mongo": False,
            "is_mock": False,
            "backend_name": "Disconnected",
            "backend_display": "Disconnected",
            "uri": sanitize_mongo_uri(Config.MONGO_URI),
            "database": Config.MONGO_DB_NAME,
            "server_version": "N/A",
            "supports_transactions": False,
            "error": str(exc),
        }


def reset_db_connections() -> None:
    """Reset cached client instances."""
    global _real_client, _mock_client, _is_using_mock, _has_attempted_connection, _connection_error_message
    if _real_client:
        try:
            _real_client.close()
        except Exception:
            pass
    _real_client = None
    _mock_client = None
    _is_using_mock = False
    _has_attempted_connection = False
    _connection_error_message = None
