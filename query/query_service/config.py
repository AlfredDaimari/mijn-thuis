from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Settings:
    email: str
    password: str
    database: str = "data/pipeline.sqlite3"
    stekkies_cookie: str | None = None
    applicant: "ApplicantProfile | None" = None
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"


@dataclass(frozen=True)
class ApplicantProfile:
    """The only applicant values a form worker may use."""

    first_name: str
    last_name: str
    phone: str
    email: str
    message: str


@dataclass(frozen=True)
class AccountCredentials:
    """Per-provider credentials used only for a provider login page."""

    username: str
    password: str


def load_settings(path: str | Path) -> Settings:
    """Load the two required values without ever printing the password."""
    with Path(path).open("r", encoding="utf-8") as values_file:
        data = yaml.safe_load(values_file)

    if not isinstance(data, dict):
        raise ValueError("values.yaml must contain an email and password mapping")

    email = data.get("email")
    password = data.get("password")
    database = data.get("database", "data/pipeline.sqlite3")
    stekkies_cookie = data.get("stekkies_cookie")
    gemini_api_key = data.get("gemini_api_key")
    gemini_model = data.get("gemini_model", "gemini-2.5-flash")
    applicant_data = data.get("applicant")
    if not isinstance(email, str) or not email.strip():
        raise ValueError("values.yaml requires a non-empty email")
    if not isinstance(password, str) or not password.strip():
        raise ValueError("values.yaml requires a non-empty password")
    if not isinstance(database, str) or not database.strip():
        raise ValueError("values.yaml database must be a non-empty path")
    if stekkies_cookie is not None and (not isinstance(stekkies_cookie, str) or not stekkies_cookie.strip()):
        raise ValueError("values.yaml stekkies_cookie must be a non-empty string")
    if gemini_api_key is not None and (
        not isinstance(gemini_api_key, str) or not gemini_api_key.strip()
    ):
        gemini_api_key = None
    if not isinstance(gemini_model, str) or not gemini_model.strip():
        raise ValueError("values.yaml gemini_model must be a non-empty string")

    applicant = None
    if applicant_data is not None:
        if not isinstance(applicant_data, dict):
            raise ValueError("values.yaml applicant must be a mapping")
        profile_fields = ("first_name", "last_name", "phone", "email", "message")
        missing = [
            name
            for name in profile_fields
            if not isinstance(applicant_data.get(name), str) or not applicant_data[name].strip()
        ]
        if missing:
            raise ValueError("values.yaml applicant requires non-empty " + ", ".join(missing))
        applicant = ApplicantProfile(**{name: applicant_data[name].strip() for name in profile_fields})

    return Settings(
        email=email.strip(),
        password=password,
        database=database.strip(),
        stekkies_cookie=stekkies_cookie.strip() if stekkies_cookie else None,
        applicant=applicant,
        gemini_api_key=gemini_api_key.strip() if gemini_api_key else None,
        gemini_model=gemini_model.strip(),
    )


def load_accounts(path: str | Path) -> dict[str, AccountCredentials]:
    """Read ignored per-provider credentials without logging secret values."""
    account_path = Path(path)
    if not account_path.exists():
        return {}
    with account_path.open("r", encoding="utf-8") as accounts_file:
        data = yaml.safe_load(accounts_file)
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict):
        raise ValueError("accounts.yaml must contain an accounts mapping")
    accounts: dict[str, AccountCredentials] = {}
    for host, credentials in data["accounts"].items():
        if not isinstance(host, str) or not host.strip() or not isinstance(credentials, dict):
            raise ValueError("each accounts.yaml account needs a hostname and credential mapping")
        username = credentials.get("username")
        password = credentials.get("password")
        if not isinstance(username, str) or not username.strip() or not isinstance(password, str) or not password.strip():
            raise ValueError(f"accounts.yaml requires non-empty username and password for {host}")
        accounts[host.strip().lower()] = AccountCredentials(username.strip(), password.strip())
    return accounts
