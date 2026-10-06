"""Deterministic synthetic demonstration dataset seeder for NoSQL-EvoTrace."""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional
from pymongo.database import Database

from evotrace.config import Config
from evotrace.database import get_db, save_mock_state
from evotrace.impact.registry import register_query, list_registered_queries, delete_registered_query
from evotrace.schema.inference import infer_schema

logger = logging.getLogger(__name__)

# Deterministic synthetic customer records
SAMPLE_CUSTOMERS: List[Dict[str, Any]] = [
    {
        "customer_id": "CUST-1001",
        "name": "Alice Montgomery",
        "email": "alice.montgomery@example.com",
        "status": "active",
        "loyalty_points": 850,
        "age_str": "34",  # Candidate for string -> integer conversion
        "postal_code": "02138",  # Candidate for rename to zip_code
        "city": "Cambridge",  # Candidate for move into address.city
        "address": {
            "street": "100 Main St",
            "state": "MA",
            "country": "USA",
        },
        "tags": ["vip", "tech"],
        "notes": "Prefers weekend deliveries.",
    },
    {
        "customer_id": "CUST-1002",
        "name": "Bob Henderson",
        "email": "bob.h@example.com",
        "status": "active",
        "loyalty_points": 230,
        "age_str": "45",
        "postal_code": "94107",
        "city": "San Francisco",
        "address": {
            "street": "500 Market St",
            "state": "CA",
            "country": "USA",
        },
        "tags": ["retail"],
        "notes": None,  # Explicitly null
    },
    {
        "customer_id": "CUST-1003",
        "name": "Clara Oswald",
        "email": "clara.o@example.com",
        "status": "pending",
        "loyalty_points": 0,
        "age_str": "28",
        "postal_code": "10001",
        "city": "New York",
        # address is completely missing (optional field)
        "tags": ["new-user"],
        # notes is absent
    },
    {
        "customer_id": "CUST-1004",
        "name": "David Tennant",
        "email": "david.t@example.com",
        "status": "active",
        "loyalty_points": 1250,
        "age_str": "52",
        "postal_code": "02139",
        "city": "Cambridge",
        "address": {
            "street": "450 Broadway",
            "state": "MA",
            "country": "USA",
        },
        "tags": ["vip", "enterprise"],
        "notes": "Key account executive.",
    },
    {
        "customer_id": "CUST-1005",
        "name": "Elena Rostova",
        "email": "elena.r@example.com",
        "status": "inactive",
        "loyalty_points": 45,
        "age_str": "31",
        "postal_code": "60601",
        "city": "Chicago",
        "address": {
            "street": "12 Michigan Ave",
            "state": "IL",
            "country": "USA",
        },
        "tags": [],
        "notes": None,
    },
    {
        "customer_id": "CUST-1006",
        "name": "Frank Castle",
        "email": "frank.c@example.com",
        "status": "active",
        "loyalty_points": 620,
        "age_str": "41",
        "postal_code": "10003",
        "city": "New York",
        "address": {
            "street": "88 5th Ave",
            "state": "NY",
            "country": "USA",
        },
        "tags": ["retail"],
    },
    {
        "customer_id": "CUST-1007",
        "name": "Grace Hopper",
        "email": "grace.h@example.com",
        "status": "active",
        "loyalty_points": 3400,
        "age_str": "65",
        "postal_code": "20001",
        "city": "Washington",
        "address": {
            "street": "Constitution Ave",
            "state": "DC",
            "country": "USA",
        },
        "tags": ["vip", "hall-of-fame"],
        "notes": "Founding member.",
    },
    {
        "customer_id": "CUST-1008",
        "name": "Henry Jekyll",
        "email": "henry.j@example.com",
        "status": "pending",
        "loyalty_points": 10,
        "age_str": "39",
        "postal_code": "98101",
        "city": "Seattle",
        "tags": ["lab"],
    },
    {
        "customer_id": "CUST-1009",
        "name": "Iris West",
        "email": "iris.w@example.com",
        "status": "active",
        "loyalty_points": 490,
        "age_str": "27",
        "postal_code": "63101",
        "city": "St. Louis",
        "address": {
            "street": "200 Gateway Blvd",
            "state": "MO",
            "country": "USA",
        },
        "tags": ["media"],
        "notes": "Journalist partner.",
    },
    {
        "customer_id": "CUST-1010",
        "name": "Jack Harkness",
        "email": "jack.h@example.com",
        "status": "active",
        "loyalty_points": 990,
        "age_str": "100",
        "postal_code": "02138",
        "city": "Cambridge",
        "address": {
            "street": "30 JFK St",
            "state": "MA",
            "country": "USA",
        },
        "tags": ["vip", "traveler"],
        "notes": None,
    },
]

DEMO_QUERIES = [
    {
        "name": "Active Cambridge Customers",
        "description": "Finds all active customers located in Cambridge. Depends on 'status' and 'city'.",
        "filter_spec": {"status": "active", "city": "Cambridge"},
        "referenced_paths": ["status", "city"],
    },
    {
        "name": "VIP & High Loyalty Customers",
        "description": "Selects customers with loyalty points >= 500. Depends on 'loyalty_points'.",
        "filter_spec": {"loyalty_points": {"$gte": 500}},
        "referenced_paths": ["loyalty_points"],
    },
    {
        "name": "Customers with Postal Code",
        "description": "Selects customers where postal_code is present. Directly affected by postal_code rename.",
        "filter_spec": {"postal_code": {"$exists": True}},
        "referenced_paths": ["postal_code"],
    },
    {
        "name": "Massachusetts State Address",
        "description": "Filters customers by nested address state. Depends on 'address.state'.",
        "filter_spec": {"address.state": "MA"},
        "referenced_paths": ["address.state"],
    },
    {
        "name": "Customers With Tags",
        "description": "Filters customers having the 'vip' tag. Unaffected by city or postal code evolution.",
        "filter_spec": {"tags": "vip"},
        "referenced_paths": ["tags"],
    },
]


def seed_demo_dataset(
    collection_name: str = Config.DEFAULT_DEMO_COLLECTION,
    confirm_reset: bool = True,
    db: Optional[Database] = None,
) -> Dict[str, Any]:
    """Populate the demonstration collection and register sample queries.

    Only touches the specified demo collection and its queries.
    Never modifies unrelated user collections.
    """
    if not confirm_reset:
        raise ValueError("confirm_reset must be True to seed/reset demo dataset.")

    if collection_name in Config.get_system_collections():
        raise ValueError(
            f"Cannot seed reserved system collection '{collection_name}'. "
            "Seeding is restricted to application document collections."
        )

    db = get_db() if db is None else db
    col = db[collection_name]

    # Clean existing documents in the dedicated demo collection only
    col.delete_many({})

    # Insert seeded documents
    import copy
    docs_to_insert = copy.deepcopy(SAMPLE_CUSTOMERS)
    insert_res = col.insert_many(docs_to_insert)

    # Clean prior demo queries for this collection to avoid duplicates
    existing_queries = list_registered_queries(collection_name=collection_name, db=db)
    for eq in existing_queries:
        delete_registered_query(eq["query_id"], db=db)

    # Register standard sample queries
    registered_list = []
    for q_spec in DEMO_QUERIES:
        q_doc = register_query(
            name=q_spec["name"],
            collection_name=collection_name,
            description=q_spec["description"],
            filter_spec=q_spec["filter_spec"],
            referenced_paths=q_spec["referenced_paths"],
            enabled=True,
            db=db,
        )
        registered_list.append(q_doc)

    # Compute baseline schema snapshot
    initial_snapshot = infer_schema(
        collection_name=collection_name,
        db=db,
        save_snapshot=True,
        description=f"Initial seeded baseline schema for {collection_name}",
    )

    logger.info(
        "Seeded demo collection '%s' with %d documents and %d registered queries.",
        collection_name,
        len(insert_res.inserted_ids),
        len(registered_list),
    )

    save_mock_state()

    return {
        "collection_name": collection_name,
        "documents_inserted": len(insert_res.inserted_ids),
        "queries_registered": len(registered_list),
        "initial_snapshot_id": initial_snapshot.snapshot_id,
        "initial_fingerprint": initial_snapshot.schema_fingerprint,
    }
