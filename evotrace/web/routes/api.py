"""Programmatic REST API routes for NoSQL-EvoTrace."""

from __future__ import annotations
from flask import Blueprint, jsonify, request
from evotrace.config import Config
from evotrace.database import get_db, get_connection_status
from evotrace.schema.inference import get_latest_snapshot
from evotrace.provenance.explorer import get_operation_provenance_details

bp = Blueprint("api", __name__)


@bp.route("/status")
def status():
    """Return JSON health and MongoDB connection capabilities."""
    return jsonify(get_connection_status())


@bp.route("/collections")
def collections():
    """Return list of accessible non-system collections."""
    db = get_db()
    all_cols = db.list_collection_names()
    user_cols = [c for c in all_cols if c not in Config.get_system_collections()]
    return jsonify({"collections": sorted(user_cols)})


@bp.route("/schema/<collection_name>")
def schema(collection_name: str):
    """Return latest schema snapshot profile for collection."""
    snap = get_latest_snapshot(collection_name)
    if not snap:
        return jsonify({"error": f"No schema snapshot recorded for collection '{collection_name}'"}), 404
    return jsonify(snap)


@bp.route("/provenance/<operation_id>")
def provenance(operation_id: str):
    """Return complete provenance document for operation."""
    redact = request.args.get("redact") == "1"
    details = get_operation_provenance_details(operation_id, redact_values=redact)
    if not details:
        return jsonify({"error": f"Provenance not found for operation '{operation_id}'"}), 404
    return jsonify(details)
