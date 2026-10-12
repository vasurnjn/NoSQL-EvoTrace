"""Schema Explorer and Snapshot Comparison routes."""

from __future__ import annotations
from flask import Blueprint, render_template, request, redirect, url_for, flash
from evotrace.config import Config
from evotrace.database import get_db
from evotrace.schema.inference import infer_schema, get_latest_snapshot, get_snapshot_history, get_snapshot_by_id
from evotrace.schema.comparator import compare_snapshots

bp = Blueprint("schema", __name__)


@bp.route("/")
def index():
    """Inspect observed schema for selected collection and view snapshot history."""
    db = get_db()
    all_cols = db.list_collection_names()
    user_cols = [c for c in all_cols if c not in Config.get_system_collections()]
    selected_col = request.args.get("collection") or (user_cols[0] if user_cols else Config.DEFAULT_DEMO_COLLECTION)

    # In case no snapshot exists yet, infer on first load
    snapshot = get_latest_snapshot(selected_col, db=db)
    if not snapshot and selected_col in all_cols:
        snapshot = infer_schema(selected_col, db=db, save_snapshot=True).to_dict()

    history = get_snapshot_history(selected_col, db=db)

    # Check comparison request or assign sensible distinct defaults if multiple snapshots exist
    snap_a_id = request.args.get("snap_a")
    snap_b_id = request.args.get("snap_b")
    comparison = None

    if len(history) >= 2:
        if not snap_b_id:
            snap_b_id = history[0]["snapshot_id"]
        if not snap_a_id:
            # If user selected snap_b as history[0], default snap_a to history[1]
            snap_a_id = history[1]["snapshot_id"] if history[0]["snapshot_id"] == snap_b_id else history[0]["snapshot_id"]

    if snap_a_id and snap_b_id:
        snap_a = get_snapshot_by_id(snap_a_id, db=db)
        snap_b = get_snapshot_by_id(snap_b_id, db=db)
        if snap_a and snap_b:
            comparison = compare_snapshots(snap_a, snap_b)
        else:
            flash("One or both requested snapshots were not found for comparison.", "warning")

    return render_template(
        "schema.html",
        selected_collection=selected_col,
        user_collections=user_cols,
        snapshot=snapshot,
        history=history,
        comparison=comparison,
        snap_a_id=snap_a_id,
        snap_b_id=snap_b_id,
    )


@bp.route("/refresh", methods=["POST"])
def refresh():
    """Trigger fresh schema inference on the collection."""
    col_name = request.form.get("collection") or Config.DEFAULT_DEMO_COLLECTION
    try:
        new_snap = infer_schema(col_name, save_snapshot=True, description="Manual schema profile refresh")
        flash(
            f"Inferred schema profile for '{col_name}' ({new_snap.document_count} docs). "
            f"Fingerprint: {new_snap.schema_fingerprint}",
            "success",
        )
    except Exception as exc:
        flash(f"Schema refresh failed: {exc}", "error")

    return redirect(url_for("schema.index", collection=col_name))
