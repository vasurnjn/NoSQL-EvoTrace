"""Impact Analysis routes."""

from __future__ import annotations
from flask import Blueprint, render_template, request, flash
from evotrace.config import Config
from evotrace.database import get_db
from evotrace.impact.analyzer import analyze_impact
from evotrace.impact.registry import list_registered_queries

bp = Blueprint("impact", __name__)


@bp.route("/")
def index():
    """Display the Impact Analysis dashboard."""
    db = get_db()
    all_cols = db.list_collection_names()
    user_cols = [c for c in all_cols if c not in Config.get_system_collections()]
    def col_key(c: str):
        if c == Config.DEFAULT_DEMO_COLLECTION:
            return (0, c)
        if c.startswith("eval_") or c.startswith("bench_"):
            return (2, c)
        return (1, c)
    user_cols = sorted(user_cols, key=col_key)
    selected_col = request.args.get("collection") or (user_cols[0] if user_cols else Config.DEFAULT_DEMO_COLLECTION)

    registered_queries = list_registered_queries(collection_name=selected_col, db=db)

    # If parameters provided for simulation or inspection
    op_type = request.args.get("operation_type")
    src_path = request.args.get("source_path") or None
    dst_path = request.args.get("destination_path") or None

    impact_results = None
    if op_type:
        try:
            impact_results = analyze_impact(
                collection_name=selected_col,
                operation_type=op_type,
                source_path=src_path,
                destination_path=dst_path,
                db=db,
            )
        except Exception as exc:
            flash(f"Impact analysis calculation error: {exc}", "error")

    return render_template(
        "impact.html",
        selected_collection=selected_col,
        user_collections=user_cols,
        registered_queries=registered_queries,
        impact_results=impact_results,
        op_type=op_type,
        src_path=src_path,
        dst_path=dst_path,
    )
