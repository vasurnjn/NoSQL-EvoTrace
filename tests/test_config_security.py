"""Tests for Flask secret key resolution, security rules, and production startup enforcement."""

import logging
import pytest
from evotrace.config import Config
from evotrace.web.app import create_app


def test_dev_mode_without_configured_secret_generates_secure_key(monkeypatch, caplog):
    """Verify that development mode without SECRET_KEY generates a secure temporary secret."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("FLASK_ENV", "development")
    Config.reset_generated_dev_secret()

    with caplog.at_level(logging.WARNING):
        key = Config.get_secret_key(env_mode="development", raw_secret=None)

    assert key is not None
    assert len(key) >= 64  # 32 bytes hex = 64 characters
    assert "No SECRET_KEY configured in development mode" in caplog.text
    assert "sessions will reset across application restarts" in caplog.text.lower()


def test_dev_mode_with_explicit_secret(monkeypatch):
    """Verify that explicit secret key in development is used directly without generating random key."""
    explicit_secret = "custom-dev-secret-explicitly-configured-12345"
    monkeypatch.setenv("SECRET_KEY", explicit_secret)
    monkeypatch.setenv("FLASK_ENV", "development")
    Config.reset_generated_dev_secret()

    key = Config.get_secret_key(env_mode="development", raw_secret=explicit_secret)
    assert key == explicit_secret


def test_production_mode_without_secret_fails_startup(monkeypatch):
    """Verify that production mode without SECRET_KEY raises ValueError and prevents startup."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("FLASK_ENV", "production")
    Config.reset_generated_dev_secret()

    with pytest.raises(ValueError, match="SECRET_KEY must be explicitly configured"):
        Config.get_secret_key(env_mode="production", raw_secret="")

    # Also verify create_app fails in production when no secret is configured
    with pytest.raises(ValueError, match="SECRET_KEY must be explicitly configured"):
        create_app()


def test_production_mode_with_explicit_secret_succeeds(monkeypatch):
    """Verify that production mode with an explicit secret key initializes successfully."""
    prod_secret = "prod-strong-entropy-secret-key-999-secure-hex"
    monkeypatch.setenv("SECRET_KEY", prod_secret)
    monkeypatch.setenv("FLASK_ENV", "production")

    key = Config.get_secret_key(env_mode="production", raw_secret=prod_secret)
    assert key == prod_secret

    app = create_app()
    assert app.config["SECRET_KEY"] == prod_secret
