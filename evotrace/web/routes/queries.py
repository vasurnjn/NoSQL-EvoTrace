"""Query Registry routes."""

from __future__ import annotations
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash
from evotrace.config import Config
from evotrace.database import get_db
from evotrace.impact.registry import (
    register_query,
    list_registered_queries,
    get_query_by_id,
    update_query_status,
    delete_registered_query,
    execute_registered_query,
    QueryValidationError,
)

bp = Blueprint("queries", __name__)


@bp.route("/")
def index():
    """Display catalog of registered application queries."""
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

    queries = list_registered_queries(collection_name=selected_col, db=db)

    test_result = None
    tested_id = request.args.get("tested_id")
    if tested_id:
        try:
            test_result = execute_registered_query(tested_id, db=db)
        except Exception as exc:
            flash(f"Query execution test error: {exc}", "error")

    return render_template(
        "queries.html",
        selected_collection=selected_col,
        user_collections=user_cols,
        queries=queries,
        test_result=test_result,
    )


@bp.route("/add", methods=["POST"])
def add():
    """Register a new query with declarative JSON filter."""
    col_name = request.form.get("collection_name", Config.DEFAULT_DEMO_COLLECTION)
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    raw_filter = request.form.get("filter_spec", "").strip()
    raw_paths = request.form.get("referenced_paths", "").strip()

    referenced_paths = [p.strip() for p in raw_paths.split(",") if p.strip()] if raw_paths else None

    try:
        filter_spec = json.loads(raw_filter) if raw_filter else {}
        q = register_query(
            name=name,
            collection_name=col_name,
            description=description,
            filter_spec=filter_spec,
            referenced_paths=referenced_paths,
            enabled=True,
        )
        flash(f"Successfully registered query '{q['name']}' with {len(q['referenced_paths'])} path dependencies.", "success")
    except (json.JSONDecodeError, QueryValidationError, Exception) as exc:
        flash(f"Failed to register query: {exc}", "error")

    return redirect(url_for("queries.index", collection=col_name))


@bp.route("/toggle/<query_id>", methods=["POST"])
def toggle(query_id: str):
    """Enable or disable query."""
    q = get_query_by_id(query_id)
    if not q:
        flash("Query not found.", "warning")
        return redirect(url_for("queries.index"))

    new_state = not q.get("enabled", True)
    update_query_status(query_id, new_state)
    state_str = "enabled" if new_state else "disabled"
    flash(f"Query '{q['name']}' has been {state_str}.", "info")
    return redirect(url_for("queries.index", collection=q["collection_name"]))


@bp.route("/test/<query_id>", methods=["POST"])
def test_query(query_id: str):
    """Run registered query test."""
    q = get_query_by_id(query_id)
    col = q["collection_name"] if q else Config.DEFAULT_DEMO_COLLECTION
    return redirect(url_for("queries.index", collection=col, tested_id=query_id))


@bp.route("/delete/<query_id>", methods=["POST"])
def delete_query(query_id: str):
    """Delete registered query."""
    q = get_query_by_id(query_id)
    col = q["collection_name"] if q else Config.DEFAULT_DEMO_COLLECTION
    delete_registered_query(query_id)
    flash("Query removed from catalog.", "info")
    return redirect(url_for("queries.index", collection=col))
