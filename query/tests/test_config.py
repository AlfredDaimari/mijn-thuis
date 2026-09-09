"""Unit tests for the user-supplied query-service configuration."""

import pytest

from query_service.config import load_accounts, load_settings


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


def test_load_settings_keeps_configured_pipeline_database_path(tmp_path) -> None:
    """Operators may keep the one pipeline database outside the data directory."""
    values = tmp_path / "values.yaml"
    values.write_text(
        "email: person@yahoo.com\npassword: app-password\n"
        "database: state/pipeline.sqlite3\n"
    )

    settings = load_settings(values)

    assert settings.database == "state/pipeline.sqlite3"


def test_load_settings_reads_optional_applicant_and_gemini_settings(tmp_path) -> None:
    """Form settings stay optional for the mail-only worker but load when supplied."""
    values = tmp_path / "values.yaml"
    values.write_text(
        "email: person@yahoo.com\npassword: app-password\n"
        "gemini_api_key: test-key\ngemini_model: gemini-2.5-flash\n"
        "applicant:\n  first_name: Ada\n  last_name: Lovelace\n"
        "  phone: '+31600000000'\n  email: ada@example.com\n  message: Interested\n"
    )

    settings = load_settings(values)

    assert settings.applicant is not None
    assert settings.applicant.first_name == "Ada"
    assert settings.gemini_api_key == "test-key"
    assert settings.gemini_model == "gemini-2.5-flash"


def test_load_accounts_reads_only_the_configured_provider_credentials(tmp_path) -> None:
    """Credentials are separate from values.yaml and keyed by provider host."""
    accounts = tmp_path / "accounts.yaml"
    accounts.write_text(
        "accounts:\n  provider.example:\n    username: member@example.com\n"
        "    password: secret-password\n"
    )

    loaded = load_accounts(accounts)

    assert loaded["provider.example"].username == "member@example.com"
