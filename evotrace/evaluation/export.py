"""Export utilities for benchmark evaluation results."""

from __future__ import annotations
import csv
import io
import json
from typing import Any, Dict, List, Optional
from pymongo.database import Database

from evotrace.evaluation.benchmark import get_all_experiment_results


def export_experiments_to_csv(db: Optional[Database] = None) -> str:
    """Generate a RFC-4180 compliant CSV string of all recorded benchmark runs."""
    results = get_all_experiment_results(db=db)
    if not results:
        return "experiment_id,scenario_name,dataset_size,precision,recall,f1_score,query_impact_accuracy,recovery_correctness,op_duration_ms,impact_duration_ms,provenance_storage_bytes,timestamp\n"

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "experiment_id",
        "scenario_name",
        "dataset_size",
        "precision",
        "recall",
        "f1_score",
        "query_impact_accuracy",
        "recovery_correctness",
        "restored_count",
        "conflict_count",
        "op_duration_ms",
        "impact_duration_ms",
        "recovery_duration_ms",
        "provenance_bytes",
        "bytes_per_doc",
        "backend",
        "timestamp",
    ])

    for r in results:
        m = r.get("metrics", {})
        env = r.get("environment_info", {})
        writer.writerow([
            r.get("experiment_id"),
            r.get("scenario_name"),
            r.get("dataset_size"),
            m.get("precision", "N/A"),
            m.get("recall", "N/A"),
            m.get("f1_score", "N/A"),
            m.get("query_impact_accuracy", "N/A"),
            m.get("recovery_correctness", "N/A"),
            m.get("recovery_restored_count", m.get("changed_count", "N/A")),
            m.get("recovery_conflict_count", m.get("conflict_count", 0)),
            m.get("operation_duration_ms", "N/A"),
            m.get("impact_analysis_duration_ms", "N/A"),
            m.get("recovery_duration_ms", "N/A"),
            m.get("provenance_storage_bytes", "N/A"),
            m.get("bytes_per_affected_doc", "N/A"),
            env.get("database_backend", "Unknown"),
            r.get("timestamp"),
        ])

    return output.getvalue()


def export_experiments_to_json(db: Optional[Database] = None) -> str:
    """Export all recorded benchmark runs as a pretty-printed JSON string."""
    results = get_all_experiment_results(db=db)
    return json.dumps(results, indent=2, default=str)
