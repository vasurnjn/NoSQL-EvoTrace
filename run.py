"""Application server startup entry point for NoSQL-EvoTrace."""

from __future__ import annotations
import logging
import sys
from evotrace.config import Config
from evotrace.database import get_db, get_connection_status
from evotrace.schema.inference import infer_schema, get_latest_snapshot
from evotrace.seed.demo_seeder import seed_demo_dataset
from evotrace.web.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("evotrace")


def initialize_demo_if_empty():
    """Ensure the default demonstration collection has initial data for instant usability."""
    try:
        db = get_db()
        col = db[Config.DEFAULT_DEMO_COLLECTION]
        count = col.count_documents({}) if hasattr(col, "count_documents") else len(list(col.find({})))
        if count == 0:
            logger.info("Initializing baseline demo collection '%s'...", Config.DEFAULT_DEMO_COLLECTION)
            seed_demo_dataset(Config.DEFAULT_DEMO_COLLECTION, confirm_reset=True, db=db)
        else:
            # Ensure at least one schema snapshot exists
            snap = get_latest_snapshot(Config.DEFAULT_DEMO_COLLECTION, db=db)
            if not snap:
                infer_schema(Config.DEFAULT_DEMO_COLLECTION, db=db, save_snapshot=True)
    except Exception as exc:
        logger.warning("Could not auto-seed demo collection on startup: %s", exc)


def main():
    app = create_app()

    # Verify database connection and auto-seed if needed
    status = get_connection_status()
    backend_label = status.get("backend_display", "Disconnected")
    print("\n" + "=" * 65)
    print("   NoSQL-EvoTrace: Academic Research Prototype")
    print("   Provenance-Aware Schema Evolution & Recovery for MongoDB")
    print("=" * 65)
    print(f" • Database Backend:     {backend_label}")
    print(f" • Database URI:         {status.get('uri')}")
    print(f" • Target Database:      {status.get('database')}")
    print(f" • Connected:            {status.get('connected')}")
    if not status.get("connected"):
        print(f" • Connection Error:     {status.get('error')}")
    print(f" • Multi-Doc Tx Support: {status.get('supports_transactions')}")
    print(f" • Local Web Dashboard:  http://{Config.HOST}:{Config.PORT}/")
    print("=" * 65 + "\n")

    if status.get("connected"):
        initialize_demo_if_empty()
    else:
        logger.warning(
            "MongoDB Community Server is not connected. "
            "Ensure MongoDB service is running (e.g. 'net start MongoDB') at %s",
            status.get("uri"),
        )

    app.run(
        host=Config.HOST,
        port=Config.PORT,
        debug=Config.DEBUG,
    )


if __name__ == "__main__":
    main()
