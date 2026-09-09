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
    openai_api_key: str | None = None
    luna_model: str = "gpt-5.6-luna"


@dataclass(frozen=True)
class ApplicantProfile:
    """The only applicant values a form worker may use."""

    first_name: str
    last_name: str
    phone: str
    email: str
    message: str


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
    openai_api_key = data.get("openai_api_key")
    luna_model = data.get("luna_model", "gpt-5.6-luna")
    applicant_data = data.get("applicant")
    if not isinstance(email, str) or not email.strip():
        raise ValueError("values.yaml requires a non-empty email")
    if not isinstance(password, str) or not password.strip():
        raise ValueError("values.yaml requires a non-empty password")
    if not isinstance(database, str) or not database.strip():
        raise ValueError("values.yaml database must be a non-empty path")
    if stekkies_cookie is not None and (not isinstance(stekkies_cookie, str) or not stekkies_cookie.strip()):
        raise ValueError("values.yaml stekkies_cookie must be a non-empty string")
    if openai_api_key is not None and (
        not isinstance(openai_api_key, str) or not openai_api_key.strip()
    ):
        openai_api_key = None
    if not isinstance(luna_model, str) or not luna_model.strip():
        raise ValueError("values.yaml luna_model must be a non-empty string")

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
        openai_api_key=openai_api_key.strip() if openai_api_key else None,
        luna_model=luna_model.strip(),
    )
