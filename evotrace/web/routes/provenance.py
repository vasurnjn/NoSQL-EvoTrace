"""Fine-Grained Provenance Explorer routes."""

from __future__ import annotations
from flask import Blueprint, render_template, request, flash, redirect, url_for
from evotrace.config import Config
from evotrace.database import get_db
from evotrace.provenance.explorer import list_provenance_records, get_operation_provenance_details

bp = Blueprint("provenance", __name__)


@bp.route("/")
def index():
    """Display searchable, filterable history of fine-grained provenance records."""
    db = get_db()
    col_filter = request.args.get("collection") or None
    op_filter = request.args.get("operation_type") or None
    doc_filter = request.args.get("doc_id") or None
    redact = request.args.get("redact") == "1"

    records = list_provenance_records(
        collection_name=col_filter,
        operation_type=op_filter,
        doc_id=doc_filter,
        redact_values=redact,
        limit=50,
        db=db,
    )

    all_cols = db.list_collection_names()
    user_cols = [c for c in all_cols if c not in Config.get_system_collections()]
    def col_key(c: str):
        if c == Config.DEFAULT_DEMO_COLLECTION:
            return (0, c)
        if c.startswith("eval_") or c.startswith("bench_"):
            return (2, c)
        return (1, c)
    user_cols = sorted(user_cols, key=col_key)

    return render_template(
        "provenance.html",
        records=records,
        col_filter=col_filter,
        op_filter=op_filter,
        doc_filter=doc_filter,
        redact=redact,
        user_collections=user_cols,
    )


@bp.route("/<operation_id>")
def detail(operation_id: str):
    """View granular before/after document field states for a specific evolution operation."""
    db = get_db()
    redact = request.args.get("redact") == "1"
    details = get_operation_provenance_details(operation_id, redact_values=redact, db=db)

    if not details:
        flash(f"Provenance record for operation '{operation_id}' not found.", "warning")
        return redirect(url_for("provenance.index"))

    return render_template(
        "provenance_detail.html",
        prov=details,
        redact=redact,
    )
