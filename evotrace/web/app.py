"""Flask application factory and web server setup for NoSQL-EvoTrace."""

from __future__ import annotations
import secrets
from typing import Optional
from flask import Flask, request, session, abort, render_template

from evotrace.config import Config
from evotrace.database import get_db, get_connection_status


def create_app(test_config: Optional[dict] = None) -> Flask:
    """Create and configure the Flask web application."""
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    secret_key = None
    if test_config and "SECRET_KEY" in test_config:
        secret_key = test_config["SECRET_KEY"]
    else:
        secret_key = Config.get_secret_key()

    app.config.from_mapping(
        SECRET_KEY=secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

    if test_config:
        app.config.update(test_config)

    # Session-based CSRF protection for mutating POST/PUT/DELETE/PATCH requests
    @app.before_request
    def csrf_protect():
        if app.config.get("TESTING") and not app.config.get("WTF_CSRF_ENABLED"):
            return

        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(24)

        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            json_body = request.get_json(silent=True) if request.is_json else None
            json_token = json_body.get("csrf_token") if isinstance(json_body, dict) else None

            submitted_token = (
                request.form.get("csrf_token")
                or request.headers.get("X-CSRFToken")
                or request.headers.get("X-CSRF-Token")
                or json_token
            )
            expected_token = session.get("csrf_token")
            if not submitted_token or submitted_token != expected_token:
                abort(403, description="CSRF token validation failed. Please refresh and try again.")

    # Context processors for all Jinja templates
    @app.context_processor
    def inject_global_template_data():
        status = get_connection_status()
        user_cols = [Config.DEFAULT_DEMO_COLLECTION]

        # Discover non-system collections if database is connected
        if status.get("connected"):
            try:
                db = get_db()
                all_cols = db.list_collection_names()
                user_cols = [c for c in all_cols if c not in Config.get_system_collections()]
                if not user_cols:
                    user_cols = [Config.DEFAULT_DEMO_COLLECTION]
            except Exception:
                user_cols = [Config.DEFAULT_DEMO_COLLECTION]

        def sort_col_key(name: str):
            if name == Config.DEFAULT_DEMO_COLLECTION:
                return (0, name)
            if name.startswith("eval_"):
                return (2, name)
            return (1, name)

        sorted_cols = sorted(user_cols, key=sort_col_key)

        return {
            "connection_status": status,
            "system_collections": Config.get_system_collections(),
            "available_collections": sorted_cols,
            "csrf_token": session.get("csrf_token", ""),
            "app_title": "NoSQL-EvoTrace",
        }

    # Custom Jinja template filters
    @app.template_filter("truncate_id")
    def truncate_id_filter(val: str, length: int = 12) -> str:
        s = str(val)
        return s[:length] + "…" if len(s) > length else s

    @app.template_filter("percentage")
    def percentage_filter(val: float) -> str:
        try:
            return f"{float(val) * 100:.1f}%"
        except (ValueError, TypeError):
            return "0.0%"

    # Register blueprint routes
    from evotrace.web.routes.overview import bp as overview_bp
    from evotrace.web.routes.schema import bp as schema_bp
    from evotrace.web.routes.evolution import bp as evolution_bp
    from evotrace.web.routes.provenance import bp as provenance_bp
    from evotrace.web.routes.impact import bp as impact_bp
    from evotrace.web.routes.recovery import bp as recovery_bp
    from evotrace.web.routes.queries import bp as queries_bp
    from evotrace.web.routes.experiments import bp as experiments_bp
    from evotrace.web.routes.documentation import bp as doc_bp
    from evotrace.web.routes.api import bp as api_bp

    app.register_blueprint(overview_bp)
    app.register_blueprint(schema_bp, url_prefix="/schema")
    app.register_blueprint(evolution_bp, url_prefix="/evolution")
    app.register_blueprint(provenance_bp, url_prefix="/provenance")
    app.register_blueprint(impact_bp, url_prefix="/impact")
    app.register_blueprint(recovery_bp, url_prefix="/recovery")
    app.register_blueprint(queries_bp, url_prefix="/queries")
    app.register_blueprint(experiments_bp, url_prefix="/experiments")
    app.register_blueprint(doc_bp, url_prefix="/documentation")
    app.register_blueprint(api_bp, url_prefix="/api")

    # Global Error Handlers
    @app.errorhandler(404)
    def handle_not_found(e):
        return render_template("base.html", error_title="404 Not Found", error_message="The requested page does not exist."), 404

    @app.errorhandler(403)
    def handle_forbidden(e):
        return render_template("base.html", error_title="403 Forbidden", error_message=str(e.description)), 403

    @app.errorhandler(500)
    def handle_server_error(e):
        return render_template("base.html", error_title="500 Internal Error", error_message="An unexpected server error occurred."), 500

    return app
