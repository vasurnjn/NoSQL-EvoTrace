"""Overview and system dashboard routes."""

from __future__ import annotations
from flask import Blueprint, render_template, request, redirect, url_for, flash
from evotrace.config import Config
from evotrace.database import get_db, get_connection_status
from evotrace.schema.inference import get_latest_snapshot
from evotrace.seed.demo_seeder import seed_demo_dataset

bp = Blueprint("overview", __name__)


def sort_collections(cols: list[str]) -> list[str]:
    """Sort collections: demo first, regular user collections next, eval collections last."""
    def sort_key(name: str):
        if name == Config.DEFAULT_DEMO_COLLECTION:
            return (0, name)
        if name.startswith("eval_"):
            return (2, name)
        return (1, name)
    return sorted(cols, key=sort_key)


@bp.route("/")
def index():
    """Render the main system overview dashboard with clear collection and global separation."""
    status = get_connection_status()
    if not status.get("connected"):
        return render_template(
            "overview.html",
            selected_collection=Config.DEFAULT_DEMO_COLLECTION,
            doc_count=0,
            latest_snapshot=None,
            col_ops_count=0,
            col_prov_count=0,
            col_query_count=0,
            global_ops_count=0,
            global_prov_count=0,
            global_query_count=0,
            global_collections_count=0,
            activity_scope="collection",
            recent_ops=[],
            user_collections=[Config.DEFAULT_DEMO_COLLECTION],
            # Backward-compatibility aliases
            ops_count=0,
            prov_count=0,
            query_count=0,
        )

    db = get_db()

    # Discover user collections
    try:
        all_cols = db.list_collection_names()
        raw_user_cols = [c for c in all_cols if c not in Config.get_system_collections()]
        user_cols = sort_collections(raw_user_cols)
    except Exception:
        user_cols = [Config.DEFAULT_DEMO_COLLECTION]
        all_cols = []

    # Selected collection defaults to demo_customers if present, else first collection
    requested_col = request.args.get("collection")
    if requested_col and requested_col in user_cols:
        selected_collection = requested_col
    elif Config.DEFAULT_DEMO_COLLECTION in user_cols:
        selected_collection = Config.DEFAULT_DEMO_COLLECTION
    elif user_cols:
        selected_collection = user_cols[0]
    else:
        selected_collection = Config.DEFAULT_DEMO_COLLECTION

    # A. Collection-Specific Statistics
    doc_count = 0
    try:
        if selected_collection in all_cols:
            col = db[selected_collection]
            doc_count = col.count_documents({}) if hasattr(col, "count_documents") else len(list(col.find({})))
    except Exception:
        doc_count = 0

    latest_snapshot = get_latest_snapshot(selected_collection, db=db)

    ops_col = db[Config.COLLECTION_OPERATIONS]
    prov_col = db[Config.COLLECTION_PROVENANCE]
    queries_col = db[Config.COLLECTION_QUERIES]

    col_ops_count = ops_col.count_documents({"collection_name": selected_collection}) if hasattr(ops_col, "count_documents") else len(list(ops_col.find({"collection_name": selected_collection})))
    col_prov_count = prov_col.count_documents({"collection_name": selected_collection}) if hasattr(prov_col, "count_documents") else len(list(prov_col.find({"collection_name": selected_collection})))
    col_query_count = queries_col.count_documents({"collection_name": selected_collection}) if hasattr(queries_col, "count_documents") else len(list(queries_col.find({"collection_name": selected_collection})))

    # B. Global System Statistics
    global_ops_count = ops_col.count_documents({}) if hasattr(ops_col, "count_documents") else len(list(ops_col.find({})))
    global_prov_count = prov_col.count_documents({}) if hasattr(prov_col, "count_documents") else len(list(prov_col.find({})))
    global_query_count = queries_col.count_documents({}) if hasattr(queries_col, "count_documents") else len(list(queries_col.find({})))
    global_collections_count = len(user_cols)

    # C. Recent Evolution Activity (Default: Filtered by Selected Collection)
    activity_scope = request.args.get("scope", "collection").lower()
    if activity_scope == "all":
        recent_ops_query = {}
    else:
        activity_scope = "collection"
        recent_ops_query = {"collection_name": selected_collection}

    recent_ops = list(ops_col.find(recent_ops_query).sort("created_at", -1).limit(8))
    for op in recent_ops:
        if "_id" in op:
            op["_id"] = str(op["_id"])

    return render_template(
        "overview.html",
        selected_collection=selected_collection,
        doc_count=doc_count,
        latest_snapshot=latest_snapshot,
        col_ops_count=col_ops_count,
        col_prov_count=col_prov_count,
        col_query_count=col_query_count,
        global_ops_count=global_ops_count,
        global_prov_count=global_prov_count,
        global_query_count=global_query_count,
        global_collections_count=global_collections_count,
        # Backward-compatibility aliases
        ops_count=global_ops_count,
        prov_count=global_prov_count,
        query_count=global_query_count,
        activity_scope=activity_scope,
        recent_ops=recent_ops,
        user_collections=user_cols,
    )


@bp.route("/seed-demo", methods=["POST"])
def seed_demo():
    """Seed or reset the synthetic demonstration dataset."""
    status = get_connection_status()
    if not status.get("connected"):
        flash(
            f"Cannot seed demonstration dataset: MongoDB Community Server is disconnected ({status.get('error', 'Service offline')}). "
            f"Please ensure the MongoDB service (mongod) is running at {status.get('uri')}.",
            "error",
        )
        return redirect(url_for("overview.index"))

    col_name = request.form.get("collection") or Config.DEFAULT_DEMO_COLLECTION
    try:
        res = seed_demo_dataset(collection_name=col_name, confirm_reset=True)
        flash(
            f"Successfully seeded demo collection '{col_name}' with {res['documents_inserted']} documents "
            f"and {res['queries_registered']} sample queries.",
            "success",
        )
    except Exception as exc:
        flash(f"Failed to seed demo dataset: {exc}", "error")

    return redirect(url_for("overview.index", collection=col_name))
