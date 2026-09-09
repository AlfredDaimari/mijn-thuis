"""Unit tests for the user-supplied query-service configuration."""

import pytest

from query_service.config import load_settings


pytestmark = pytest.mark.unit


def test_load_settings_reads_yahoo_credentials(tmp_path) -> None:
    """A valid values file exposes the email address and app password."""
    values = tmp_path / "values.yaml"
    values.write_text("email: person@yahoo.com\npassword: app-password\n")

    settings = load_settings(values)

    assert settings.email == "person@yahoo.com"
    assert settings.password == "app-password"


def test_load_settings_explains_when_password_is_missing(tmp_path) -> None:
    """A normal Yahoo password cannot be silently replaced by an absent value."""
    values = tmp_path / "values.yaml"
    values.write_text("email: person@yahoo.com\n")

    with pytest.raises(ValueError, match="password"):
        load_settings(values)


def test_load_settings_keeps_configured_database_paths(tmp_path) -> None:
    """Operators may keep queue databases outside the default data directory."""
    values = tmp_path / "values.yaml"
    values.write_text(
        "email: person@yahoo.com\npassword: app-password\n"
        "staging_database: state/staging.sqlite3\n"
        "listings_database: state/listings.sqlite3\n"
    )

    settings = load_settings(values)

    assert settings.staging_database == "state/staging.sqlite3"
    assert settings.listings_database == "state/listings.sqlite3"
