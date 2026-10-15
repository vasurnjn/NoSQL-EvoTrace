"""Safe Recovery Center routes."""

from __future__ import annotations
from flask import Blueprint, render_template, request, redirect, url_for, flash
from evotrace.config import Config
from evotrace.database import get_db
from evotrace.recovery.engine import preview_recovery, execute_recovery
from evotrace.recovery.audit import get_all_recovery_audits, check_recovery_eligibility

bp = Blueprint("recovery", __name__)


@bp.route("/")
def index():
    """List executed operations available for recovery and historical recovery audit events."""
    db = get_db()

    # Find EXECUTED operations
    ops_col = db[Config.COLLECTION_OPERATIONS]
    executed_ops = list(ops_col.find({"status": {"$in": ["EXECUTED", "RECOVERED"]}}).sort("created_at", -1))
    for op in executed_ops:
        if "_id" in op:
            op["_id"] = str(op["_id"])

    # Historical recovery audits
    recovery_audits = get_all_recovery_audits(db=db)

    selected_op_id = request.args.get("operation_id")
    preview_data = None
    if selected_op_id:
        try:
            preview_data = preview_recovery(selected_op_id, db=db)
        except Exception as exc:
            flash(f"Failed to load recovery preview for operation {selected_op_id}: {exc}", "error")

    return render_template(
        "recovery.html",
        executed_ops=executed_ops,
        recovery_audits=recovery_audits,
        selected_op_id=selected_op_id,
        preview=preview_data,
    )


@bp.route("/execute/<operation_id>", methods=["POST"])
def execute(operation_id: str):
    """Execute safe recovery on compatible documents."""
    confirm = request.form.get("confirm_recovery")
    if not confirm:
        flash("Recovery aborted: You must check the explicit confirmation box before applying recovery.", "error")
        return redirect(url_for("recovery.index", operation_id=operation_id))

    try:
        res = execute_recovery(operation_id=operation_id)
        flash(
            f"Recovery completed successfully! Restored {res['restored_count']} documents. "
            f"Skipped {res['conflict_count']} conflicting documents. "
            f"Recovery Audit ID: {res['recovery_provenance_id']}",
            "success",
        )
    except Exception as exc:
        flash(f"Recovery failed: {exc}", "error")

    return redirect(url_for("recovery.index", operation_id=operation_id))
