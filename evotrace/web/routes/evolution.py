"""Schema Evolution Workspace routes (preview, validation, execution)."""

from __future__ import annotations
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from evotrace.config import Config
from evotrace.database import get_db
from evotrace.evolution.engine import preview_evolution, execute_evolution
from evotrace.evolution.validation import EvolutionValidationError
from evotrace.impact.analyzer import analyze_impact

bp = Blueprint("evolution", __name__)


def parse_default_value(raw_val: str, raw_type: str = "string") -> any:
    """Parse user-provided form string into appropriate Python/BSON type."""
    if not raw_val:
        return None
    val = raw_val.strip()
    if raw_type == "integer":
        return int(val)
    elif raw_type == "double":
        return float(val)
    elif raw_type == "boolean":
        return val.lower() in ("true", "1", "yes")
    elif raw_type == "json":
        return json.loads(val)
    return val


@bp.route("/")
def index():
    """Render the Evolution Workspace interface."""
    db = get_db()
    all_cols = db.list_collection_names()
    user_cols = [c for c in all_cols if c not in Config.get_system_collections()]
    
    # Prioritize demo_customers first, keep eval_* collections last
    def col_key(c: str):
        if c == Config.DEFAULT_DEMO_COLLECTION:
            return (0, c)
        if c.startswith("eval_") or c.startswith("bench_"):
            return (2, c)
        return (1, c)
    user_cols = sorted(user_cols, key=col_key)

    selected_col = request.args.get("collection") or (user_cols[0] if user_cols else Config.DEFAULT_DEMO_COLLECTION)

    return render_template(
        "evolution.html",
        selected_collection=selected_col,
        user_collections=user_cols,
    )


@bp.route("/preview", methods=["POST"])
def preview():
    """AJAX dry-run endpoint returning affected document counts, warnings, and diffs."""
    data = request.get_json() if request.is_json else request.form
    col_name = data.get("collection_name", Config.DEFAULT_DEMO_COLLECTION)
    op_type = data.get("operation_type")
    src_path = data.get("source_path") or None
    dst_path = data.get("destination_path") or None
    target_type = data.get("target_type") or None

    val_type = data.get("val_type", "string")
    raw_val = data.get("default_value", "")
    try:
        def_val = parse_default_value(raw_val, val_type)
    except Exception as exc:
        return jsonify({"success": False, "error": f"Invalid default value: {exc}"}), 400

    try:
        preview_res = preview_evolution(
            collection_name=col_name,
            operation_type=op_type,
            source_path=src_path,
            destination_path=dst_path,
            default_value=def_val,
            target_type=target_type,
        )

        impact_res = analyze_impact(
            collection_name=col_name,
            operation_type=op_type,
            source_path=src_path,
            destination_path=dst_path,
        )

        preview_res["impact_summary"] = {
            "total_queries": impact_res["total_registered_queries"],
            "affected_queries": impact_res["affected_queries_count"],
            "query_impacts": impact_res["query_impacts"],
        }

        return jsonify({"success": True, "preview": preview_res})
    except (EvolutionValidationError, Exception) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@bp.route("/apply", methods=["POST"])
def apply():
    """Execute evolution transformation after explicit user confirmation."""
    col_name = request.form.get("collection_name", Config.DEFAULT_DEMO_COLLECTION)
    op_type = request.form.get("operation_type")
    src_path = request.form.get("source_path") or None
    dst_path = request.form.get("destination_path") or None
    target_type = request.form.get("target_type") or None
    confirm = request.form.get("confirm_execution")

    if not confirm:
        flash("Execution rejected: You must check the explicit confirmation box before applying bulk schema mutations.", "error")
        return redirect(url_for("evolution.index", collection=col_name))

    val_type = request.form.get("val_type", "string")
    raw_val = request.form.get("default_value", "")
    try:
        def_val = parse_default_value(raw_val, val_type)
    except Exception as exc:
        flash(f"Invalid default value format: {exc}", "error")
        return redirect(url_for("evolution.index", collection=col_name))

    try:
        op_dict, prov_dict = execute_evolution(
            collection_name=col_name,
            operation_type=op_type,
            source_path=src_path,
            destination_path=dst_path,
            default_value=def_val,
            target_type=target_type,
            skip_conflicts=True,
        )

        flash(
            f"Successfully applied {op_type} on collection '{col_name}'. "
            f"Affected: {len(prov_dict.get('affected_doc_ids', []))} documents. "
            f"Operation ID: {op_dict['operation_id']}",
            "success",
        )
        return redirect(url_for("provenance.detail", operation_id=op_dict["operation_id"]))
    except Exception as exc:
        flash(f"Evolution execution failed: {exc}", "error")
        return redirect(url_for("evolution.index", collection=col_name))
