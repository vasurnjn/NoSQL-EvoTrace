"""Reproducible research evaluation and benchmark routes."""

from __future__ import annotations
from flask import Blueprint, render_template, request, redirect, url_for, flash, Response
from evotrace.database import get_db
from evotrace.evaluation.benchmark import run_benchmark_suite, get_all_experiment_results
from evotrace.evaluation.export import export_experiments_to_csv, export_experiments_to_json

bp = Blueprint("experiments", __name__)


@bp.route("/")
def index():
    """Display evaluation results, metrics, and runner controls."""
    db = get_db()
    results = get_all_experiment_results(db=db)
    return render_template("experiments.html", results=results)


@bp.route("/run", methods=["POST"])
def run():
    """Execute evaluation benchmark suite."""
    raw_sizes = request.form.get("sizes", "100,500")
    raw_seed = request.form.get("seed", "42")

    try:
        sizes = [int(s.strip()) for s in raw_sizes.split(",") if s.strip()]
        seed = int(raw_seed)
        new_runs = run_benchmark_suite(sizes=sizes, seed=seed)
        flash(f"Successfully executed benchmark suite ({len(new_runs)} scenario evaluations recorded).", "success")
    except Exception as exc:
        flash(f"Benchmark execution failed: {exc}", "error")

    return redirect(url_for("experiments.index"))


@bp.route("/export/csv")
def export_csv():
    """Download RFC-4180 CSV export of all benchmark runs."""
    csv_content = export_experiments_to_csv()
    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=nosql_evotrace_benchmark_results.csv"},
    )


@bp.route("/export/json")
def export_json():
    """Download JSON export of all benchmark runs."""
    json_content = export_experiments_to_json()
    return Response(
        json_content,
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=nosql_evotrace_benchmark_results.json"},
    )
