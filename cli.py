"""Command-line interface (CLI) for NoSQL-EvoTrace.

Usage:
    python cli.py status
    python cli.py seed [--collection <name>]
    python cli.py inspect [--collection <name>]
    python cli.py benchmark [--sizes 100,500,1000] [--seed 42] [--csv-out <file>]
"""

from __future__ import annotations
import argparse
import json
import sys
from evotrace.config import Config
from evotrace.database import get_db, get_connection_status
from evotrace.evaluation.benchmark import run_benchmark_suite, get_all_experiment_results
from evotrace.evaluation.export import export_experiments_to_csv
from evotrace.schema.inference import infer_schema
from evotrace.seed.demo_seeder import seed_demo_dataset


def cmd_status(args):
    """Report database connectivity and system status."""
    status = get_connection_status()
    print("=" * 60)
    print("NoSQL-EvoTrace System Status")
    print("=" * 60)
    print(f"Connected:             {status.get('connected')}")
    print(f"Backend Type:          {status.get('backend_name')}")
    print(f"Database URI:          {status.get('uri')}")
    print(f"Target Database:       {status.get('database')}")
    print(f"Server Version:        {status.get('server_version')}")
    print(f"Native Transactions:   {status.get('supports_transactions')}")
    if status.get("error"):
        print(f"Connection Notice:     {status.get('error')}")
    print("=" * 60)


def cmd_seed(args):
    """Seed the demonstration dataset and register sample queries."""
    col_name = args.collection or Config.DEFAULT_DEMO_COLLECTION
    print(f"Seeding demo collection '{col_name}'...")
    res = seed_demo_dataset(collection_name=col_name, confirm_reset=True)
    print(f"Successfully seeded {res['documents_inserted']} documents and {res['queries_registered']} sample queries.")
    print(f"Baseline Snapshot ID: {res['initial_snapshot_id']} (Fingerprint: {res['initial_fingerprint']})")


def cmd_inspect(args):
    """Inspect and infer observed schema for a collection."""
    col_name = args.collection or Config.DEFAULT_DEMO_COLLECTION
    print(f"Inferring observed schema for '{col_name}'...")
    snapshot = infer_schema(collection_name=col_name, save_snapshot=True)
    print(f"Document Count: {snapshot.document_count}")
    print(f"Schema Fingerprint: {snapshot.schema_fingerprint}")
    print("\nObserved Field Profiles:")
    print("-" * 70)
    for path, info in sorted(snapshot.fields.items()):
        types_str = ", ".join(f"{t}: {c}" for t, c in info.get("types", {}).items())
        ratio = info.get("presence_ratio", 0.0) * 100
        nulls = info.get("null_count", 0)
        mixed = " [MIXED TYPE!]" if info.get("is_mixed_type") else ""
        print(f"• {path:<28} | Types: {types_str:<18} | Presence: {ratio:5.1f}% | Nulls: {nulls}{mixed}")


def cmd_benchmark(args):
    """Run research evaluation suite."""
    raw_sizes = [int(s.strip()) for s in args.sizes.split(",") if s.strip()]
    seed_val = args.seed
    print(f"Executing benchmark evaluation suite across sizes {raw_sizes} (seed={seed_val})...")
    results = run_benchmark_suite(sizes=raw_sizes, seed=seed_val)
    print(f"\nCompleted {len(results)} scenario runs.")

    if args.csv_out:
        csv_data = export_experiments_to_csv()
        with open(args.csv_out, "w", encoding="utf-8") as f:
            f.write(csv_data)
        print(f"Wrote benchmark results to {args.csv_out}")
    else:
        print("\nBenchmark Summary:")
        print(f"{'Scenario':<16} | {'N':<6} | {'F1':<6} | {'Accuracy':<8} | {'Recovery':<8} | {'Op(ms)':<8}")
        print("-" * 65)
        for r in results:
            m = r.get("metrics", {})
            print(
                f"{r.get('scenario_name'):<16} | {r.get('dataset_size'):<6} | "
                f"{m.get('f1_score', 0):<6.2f} | {m.get('query_impact_accuracy', 0):<8.2f} | "
                f"{m.get('recovery_correctness', 0):<8.2f} | {m.get('operation_duration_ms', 0):<8.1f}"
            )


def main():
    parser = argparse.ArgumentParser(description="NoSQL-EvoTrace CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    p_status = subparsers.add_parser("status", help="Check database connectivity")
    p_status.set_defaults(func=cmd_status)

    # seed
    p_seed = subparsers.add_parser("seed", help="Seed demonstration dataset")
    p_seed.add_argument("--collection", "-c", default=Config.DEFAULT_DEMO_COLLECTION, help="Target collection")
    p_seed.set_defaults(func=cmd_seed)

    # inspect
    p_inspect = subparsers.add_parser("inspect", help="Infer and inspect collection schema")
    p_inspect.add_argument("--collection", "-c", default=Config.DEFAULT_DEMO_COLLECTION, help="Target collection")
    p_inspect.set_defaults(func=cmd_inspect)

    # benchmark
    p_bench = subparsers.add_parser("benchmark", help="Execute reproducible evaluation suite")
    p_bench.add_argument("--sizes", "-s", default="100,500,1000", help="Comma-separated dataset sizes")
    p_bench.add_argument("--seed", type=int, default=42, help="Random seed")
    p_bench.add_argument("--csv-out", "-o", help="Optional output path for results CSV")
    p_bench.set_defaults(func=cmd_benchmark)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
