"""Integration tests for web routes, forms, CSRF checks, and API endpoints."""

import json


def test_overview_and_seed_route(test_client):
    # GET Overview
    res = test_client.get("/")
    assert res.status_code == 200
    assert b"System Overview" in res.data

    # POST seed demo dataset
    res_seed = test_client.post("/seed-demo", data={"collection": "demo_customers"}, follow_redirects=True)
    assert res_seed.status_code == 200
    assert b"Successfully seeded" in res_seed.data


def test_schema_routes(test_client):
    test_client.post("/seed-demo", data={"collection": "demo_customers"}, follow_redirects=True)

    # GET schema explorer
    res = test_client.get("/schema/?collection=demo_customers")
    assert res.status_code == 200
    assert b"Observed Field Paths" in res.data

    # POST refresh schema
    res_ref = test_client.post("/schema/refresh", data={"collection": "demo_customers"}, follow_redirects=True)
    assert res_ref.status_code == 200
    assert b"Inferred schema profile" in res_ref.data


def test_evolution_routes_and_csrf_protection(test_client):
    test_client.post("/seed-demo", data={"collection": "demo_customers"}, follow_redirects=True)

    # GET evolution workspace
    res = test_client.get("/evolution/?collection=demo_customers")
    assert res.status_code == 200
    assert b"Evolution Workspace" in res.data

    # POST preview (AJAX)
    payload = {
        "collection_name": "demo_customers",
        "operation_type": "rename_field",
        "source_path": "postal_code",
        "destination_path": "zip_code",
    }
    res_prev = test_client.post(
        "/evolution/preview",
        data=json.dumps(payload),
        headers={"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"},
    )
    assert res_prev.status_code == 200
    data = res_prev.get_json()
    assert data["success"] is True
    assert data["preview"]["affected_count"] == 10

    # POST apply WITHOUT confirmation -> rejected!
    res_no_confirm = test_client.post("/evolution/apply", data={
        "collection_name": "demo_customers",
        "operation_type": "rename_field",
        "source_path": "postal_code",
        "destination_path": "zip_code",
    }, follow_redirects=True)
    assert b"Execution rejected: You must check the explicit confirmation box" in res_no_confirm.data

    # POST apply WITH confirmation -> executes!
    res_apply = test_client.post("/evolution/apply", data={
        "collection_name": "demo_customers",
        "operation_type": "rename_field",
        "source_path": "postal_code",
        "destination_path": "zip_code",
        "confirm_execution": "1",
    }, follow_redirects=True)
    assert res_apply.status_code == 200
    assert b"Operation Provenance Details" in res_apply.data


def test_provenance_and_recovery_routes(test_client):
    test_client.post("/seed-demo", data={"collection": "demo_customers"}, follow_redirects=True)

    # Execute an evolution
    test_client.post("/evolution/apply", data={
        "collection_name": "demo_customers",
        "operation_type": "add_field",
        "destination_path": "newsletter_opt_in",
        "default_value": "true",
        "val_type": "boolean",
        "confirm_execution": "1",
    }, follow_redirects=True)

    # GET provenance list
    res_prov = test_client.get("/provenance/")
    assert res_prov.status_code == 200
    assert b"Provenance Explorer" in res_prov.data
    assert b"add_field" in res_prov.data

    # GET recovery center
    res_rec = test_client.get("/recovery/")
    assert res_rec.status_code == 200
    assert b"Recovery Center" in res_rec.data


def test_query_registry_routes(test_client):
    # GET queries
    res = test_client.get("/queries/?collection=demo_customers")
    assert res.status_code == 200
    assert b"Application Query Registry" in res.data

    # POST register new query
    res_add = test_client.post("/queries/add", data={
        "collection_name": "demo_customers",
        "name": "High Value Customers",
        "description": "Points > 1000",
        "filter_spec": '{"loyalty_points": {"$gt": 1000}}',
        "referenced_paths": "loyalty_points",
    }, follow_redirects=True)
    assert res_add.status_code == 200
    assert b"High Value Customers" in res_add.data


def test_experiments_and_documentation_routes(test_client):
    # GET experiments
    res_exp = test_client.get("/experiments/")
    assert res_exp.status_code == 200
    assert b"Research Evaluation & Reproducible Experiments" in res_exp.data

    # GET documentation
    res_doc = test_client.get("/documentation/")
    assert res_doc.status_code == 200
    assert b"Research Documentation & Architecture" in res_doc.data

    # GET API status
    res_api = test_client.get("/api/status")
    assert res_api.status_code == 200
    api_data = res_api.get_json()
    assert "connected" in api_data


def test_csrf_protection_when_enabled():
    """Verify that CSRF protection rejects mutating requests without valid token across form, AJAX, and JSON."""
    from evotrace.web.app import create_app
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key",
        "WTF_CSRF_ENABLED": True,
    })
    with app.test_client() as client:
        # Establish session by visiting a GET endpoint
        get_res = client.get("/")
        assert get_res.status_code == 200
        with client.session_transaction() as sess:
            valid_token = sess.get("csrf_token")
        assert valid_token is not None

        # 1. Form request without token -> 403
        res_form_missing = client.post("/seed-demo", data={"collection": "demo_customers"})
        assert res_form_missing.status_code == 403
        assert b"CSRF token validation failed" in res_form_missing.data

        # 2. Form request with invalid token -> 403
        res_form_invalid = client.post("/seed-demo", data={"collection": "demo_customers", "csrf_token": "bad-token"})
        assert res_form_invalid.status_code == 403
        assert b"CSRF token validation failed" in res_form_invalid.data

        # 3. Form request with valid token -> 302 (redirect to overview)
        res_form_valid = client.post("/seed-demo", data={"collection": "demo_customers", "csrf_token": valid_token})
        assert res_form_valid.status_code == 302

        # 4. AJAX request without token -> 403
        res_ajax_missing = client.post(
            "/evolution/preview",
            data='{"collection_name": "demo_customers", "operation_type": "add_field"}',
            headers={"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"},
        )
        assert res_ajax_missing.status_code == 403
        assert b"CSRF token validation failed" in res_ajax_missing.data

        # 5. AJAX request with invalid token -> 403
        res_ajax_invalid = client.post(
            "/evolution/preview",
            data='{"collection_name": "demo_customers", "operation_type": "add_field"}',
            headers={"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest", "X-CSRFToken": "wrong-token"},
        )
        assert res_ajax_invalid.status_code == 403
        assert b"CSRF token validation failed" in res_ajax_invalid.data

        # 6. AJAX request with valid X-CSRFToken header -> 200
        res_ajax_valid = client.post(
            "/evolution/preview",
            data=json.dumps({"collection_name": "demo_customers", "operation_type": "add_field", "destination_path": "active_test"}),
            headers={"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest", "X-CSRFToken": valid_token},
        )
        assert res_ajax_valid.status_code == 200
        assert res_ajax_valid.get_json()["success"] is True

        # 7. JSON request without token -> 403
        res_json_missing = client.post(
            "/evolution/preview",
            data=json.dumps({"collection_name": "demo_customers", "operation_type": "add_field"}),
            headers={"Content-Type": "application/json"},
        )
        assert res_json_missing.status_code == 403

        # 8. JSON request with invalid token in payload -> 403
        res_json_invalid = client.post(
            "/evolution/preview",
            data=json.dumps({"collection_name": "demo_customers", "operation_type": "add_field", "csrf_token": "wrong"}),
            headers={"Content-Type": "application/json"},
        )
        assert res_json_invalid.status_code == 403

        # 9. JSON request with valid token in body -> 200
        res_json_valid = client.post(
            "/evolution/preview",
            data=json.dumps({"collection_name": "demo_customers", "operation_type": "add_field", "destination_path": "active_test", "csrf_token": valid_token}),
            headers={"Content-Type": "application/json"},
        )
        assert res_json_valid.status_code == 200
        assert res_json_valid.get_json()["success"] is True
