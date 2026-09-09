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
    if not isinstance(email, str) or not email.strip():
        raise ValueError("values.yaml requires a non-empty email")
    if not isinstance(password, str) or not password.strip():
        raise ValueError("values.yaml requires a non-empty password")
    if not isinstance(database, str) or not database.strip():
        raise ValueError("values.yaml database must be a non-empty path")
    if stekkies_cookie is not None and (not isinstance(stekkies_cookie, str) or not stekkies_cookie.strip()):
        raise ValueError("values.yaml stekkies_cookie must be a non-empty string")

    return Settings(
        email=email.strip(),
        password=password,
        database=database.strip(),
        stekkies_cookie=stekkies_cookie.strip() if stekkies_cookie else None,
    )
