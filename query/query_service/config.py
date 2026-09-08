from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Settings:
    email: str
    password: str
    staging_database: str = "data/staging.sqlite3"
    listings_database: str = "data/listings.sqlite3"
    resolved_database: str = "data/resolved.sqlite3"
    stekkies_cookie: str | None = None


def load_settings(path: str | Path) -> Settings:
    """Load the two required values without ever printing the password."""
    with Path(path).open("r", encoding="utf-8") as values_file:
        data = yaml.safe_load(values_file)

    if not isinstance(data, dict):
        raise ValueError("values.yaml must contain an email and password mapping")

    email = data.get("email")
    password = data.get("password")
    staging_database = data.get("staging_database", "data/staging.sqlite3")
    listings_database = data.get("listings_database", "data/listings.sqlite3")
    resolved_database = data.get("resolved_database", "data/resolved.sqlite3")
    stekkies_cookie = data.get("stekkies_cookie")
    if not isinstance(email, str) or not email.strip():
        raise ValueError("values.yaml requires a non-empty email")
    if not isinstance(password, str) or not password.strip():
        raise ValueError("values.yaml requires a non-empty password")
    if not isinstance(staging_database, str) or not staging_database.strip():
        raise ValueError("values.yaml staging_database must be a non-empty path")
    if not isinstance(listings_database, str) or not listings_database.strip():
        raise ValueError("values.yaml listings_database must be a non-empty path")
    if not isinstance(resolved_database, str) or not resolved_database.strip():
        raise ValueError("values.yaml resolved_database must be a non-empty path")
    if stekkies_cookie is not None and (not isinstance(stekkies_cookie, str) or not stekkies_cookie.strip()):
        raise ValueError("values.yaml stekkies_cookie must be a non-empty string")

    return Settings(
        email=email.strip(),
        password=password,
        staging_database=staging_database.strip(),
        listings_database=listings_database.strip(),
        resolved_database=resolved_database.strip(),
        stekkies_cookie=stekkies_cookie.strip() if stekkies_cookie else None,
    )
