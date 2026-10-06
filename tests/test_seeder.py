"""Unit tests for demonstration dataset seeder and isolation safety."""

import pytest
from evotrace.seed.demo_seeder import seed_demo_dataset


def test_seed_demo_dataset_and_isolation(clean_database):
    # Insert unrelated user collection with critical data
    user_col = clean_database["user_production_data"]
    user_col.insert_one({"_id": "important_1", "sensitive": True})

    # Run seed
    res = seed_demo_dataset(collection_name="demo_customers", confirm_reset=True, db=clean_database)
    assert res["documents_inserted"] == 10
    assert res["queries_registered"] == 5
    assert res["initial_fingerprint"] is not None

    # Verify unrelated collection was completely preserved!
    assert user_col.count_documents({}) == 1
    assert user_col.find_one({"_id": "important_1"}) is not None

    # Repeat seed (idempotency check)
    res2 = seed_demo_dataset(collection_name="demo_customers", confirm_reset=True, db=clean_database)
    assert res2["documents_inserted"] == 10

    # User collection remains intact
    assert user_col.count_documents({}) == 1


def test_seeder_rejects_system_collections(clean_database):
    """Verify that seeding cannot be targeted at internal system collections."""
    from evotrace.config import Config

    for sys_col in Config.get_system_collections():
        with pytest.raises(ValueError, match="Cannot seed reserved system collection"):
            seed_demo_dataset(collection_name=sys_col, confirm_reset=True, db=clean_database)


def test_seeder_requires_confirmation(clean_database):
    """Verify that seeding without explicit confirmation is rejected."""
    with pytest.raises(ValueError, match="confirm_reset must be True"):
        seed_demo_dataset(collection_name="demo_customers", confirm_reset=False, db=clean_database)
