"""Synthetic dataset generator with deterministic ground truth labels for benchmarking."""

from __future__ import annotations
import random
from typing import Any, Dict, List, Tuple


def generate_benchmark_dataset(
    size: int = 100,
    seed: int = 42,
    collection_name: str = "eval_dataset",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Generate a reproducible synthetic dataset with controlled structural variation and exact ID labels.

    Returns:
        (documents, ground_truth_metadata)
    """
    rng = random.Random(seed)

    docs: List[Dict[str, Any]] = []
    discount_code_doc_ids: List[str] = []
    shipping_city_doc_ids: List[str] = []
    satisfaction_score_doc_ids: List[str] = []

    cities = ["Boston", "Seattle", "Austin", "Chicago", "Denver", "Atlanta"]
    tiers = ["bronze", "silver", "gold"]

    for i in range(size):
        doc_id = f"BENCH-{seed}-{i:05d}"

        # 70% of docs have discount_code
        has_discount = rng.random() < 0.70
        discount_code = f"SAVE-{rng.randint(10, 99)}" if has_discount else None
        if has_discount:
            discount_code_doc_ids.append(doc_id)

        # 60% of docs have shipping object with city
        has_shipping = rng.random() < 0.60
        if has_shipping:
            shipping_city_doc_ids.append(doc_id)
            shipping_obj = {
                "street": f"{rng.randint(100, 999)} Tech Way",
                "city": rng.choice(cities),
                "zip": f"{rng.randint(10000, 99999)}",
            }
        else:
            shipping_obj = None

        # 80% have score as string (e.g. '85'), 20% missing
        has_score = rng.random() < 0.80
        score_val = str(rng.randint(1, 100)) if has_score else None
        if has_score:
            satisfaction_score_doc_ids.append(doc_id)

        doc: Dict[str, Any] = {
            "_id": doc_id,
            "account_id": doc_id,
            "tier": rng.choice(tiers),
            "usage_count": rng.randint(0, 500),
            "is_active": rng.random() < 0.85,
        }

        if has_discount:
            doc["discount_code"] = discount_code
        if shipping_obj is not None:
            doc["shipping"] = shipping_obj
        if score_val is not None:
            doc["satisfaction_score"] = score_val

        docs.append(doc)

    ground_truth = {
        "dataset_size": size,
        "seed": seed,
        "all_doc_ids": [d["_id"] for d in docs],
        "discount_code_doc_ids": discount_code_doc_ids,
        "shipping_city_doc_ids": shipping_city_doc_ids,
        "satisfaction_score_doc_ids": satisfaction_score_doc_ids,
        "discount_code_docs_count": len(discount_code_doc_ids),
        "shipping_city_docs_count": len(shipping_city_doc_ids),
        "satisfaction_score_docs_count": len(satisfaction_score_doc_ids),
    }

    return docs, ground_truth
