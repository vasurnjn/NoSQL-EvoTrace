"""Unit tests for nested dot-path operations and collision detection."""

import pytest
from evotrace.evolution.paths import (
    get_nested_path,
    set_nested_path,
    delete_nested_path,
    path_exists,
    split_path,
    is_ancestor_path,
    is_descendant_path,
    paths_overlap,
    PathStructureCollisionError,
)


def test_split_path():
    assert split_path("name") == (None, "name")
    assert split_path("address.city") == ("address", "city")
    assert split_path("a.b.c.d") == ("a.b.c", "d")


def test_get_nested_path():
    doc = {
        "user": {
            "name": "Alice",
            "profile": {
                "age": 30,
                "bio": None,
            },
        },
        "status": "active",
    }

    # Top-level existing
    val, exists, is_null = get_nested_path(doc, "status")
    assert exists is True
    assert is_null is False
    assert val == "active"

    # Nested existing
    val, exists, is_null = get_nested_path(doc, "user.name")
    assert exists is True
    assert val == "Alice"

    # Deep nested null
    val, exists, is_null = get_nested_path(doc, "user.profile.bio")
    assert exists is True
    assert is_null is True
    assert val is None

    # Missing leaf
    val, exists, is_null = get_nested_path(doc, "user.profile.website")
    assert exists is False
    assert is_null is False
    assert val is None

    # Missing intermediate
    val, exists, is_null = get_nested_path(doc, "user.location.city")
    assert exists is False


def test_set_nested_path_and_collision():
    doc = {"name": "Bob"}

    # Set top level
    set_nested_path(doc, "age", 25)
    assert doc["age"] == 25

    # Set new nested path (creates intermediate dicts)
    set_nested_path(doc, "address.city", "Boston")
    assert doc["address"]["city"] == "Boston"

    # Deep nested path
    set_nested_path(doc, "address.geo.lat", 42.36)
    assert doc["address"]["geo"]["lat"] == 42.36

    # Structural collision: setting subfield inside a primitive string
    with pytest.raises(PathStructureCollisionError):
        set_nested_path(doc, "name.first", "Robert")


def test_delete_nested_path_and_clean_empty_parents():
    doc = {
        "title": "Engineer",
        "contact": {
            "address": {
                "zip": "02138",
            },
        },
    }

    # Delete nested leaf
    val, existed = delete_nested_path(doc, "contact.address.zip", clean_empty_parents=True)
    assert existed is True
    assert val == "02138"
    # Because address and contact became empty, clean_empty_parents removed them
    assert "contact" not in doc

    # Delete non-existent
    val, existed = delete_nested_path(doc, "non_existent")
    assert existed is False
    assert val is None


def test_path_relationships():
    assert is_ancestor_path("address", "address.city") is True
    assert is_ancestor_path("address", "address") is False
    assert is_ancestor_path("address", "other.city") is False

    assert is_descendant_path("address.city", "address") is True
    assert is_descendant_path("address", "address.city") is False

    assert paths_overlap("address", "address.city") is True
    assert paths_overlap("address.city", "address") is True
    assert paths_overlap("city", "city") is True
    assert paths_overlap("city", "state") is False
