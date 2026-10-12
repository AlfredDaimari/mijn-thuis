from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Settings:
    email: str
    password: str
    database: str = "data/pipeline.sqlite3"
    stekkies_cookie: str | None = None
    applicant: "ApplicantProfile | None" = None
    openrouter_api_key: str | None = None
    openrouter_model: str = "openai/gpt-6-luna"
    frontend_auth: "FrontendAuth | None" = None


@dataclass(frozen=True)
class FrontendAuth:
    """Credentials shared by the dashboard Nginx layer and local API."""

    username: str
    password: str


@dataclass(frozen=True)
class ApplicantProfile:
    """The only applicant values a form worker may use."""

    first_name: str
    last_name: str
    phone: str
    email: str
    message_single_person: str
    message_two_person: str
    current_housing_situation: str | None = None
    registered_in_netherlands: bool | None = None

    def message_for_rooms(self, room_count: int | None) -> str:
        """Use the two-person wording for homes with two or more rooms."""
        return (
            self.message_two_person
            if room_count is not None and room_count >= 2
            else self.message_single_person
        )

    def navigation_context(self) -> dict[str, str | bool]:
        """Return only non-identifying context needed for provider navigation."""
        context: dict[str, str | bool] = {}
        if self.current_housing_situation:
            context["current_housing_situation"] = self.current_housing_situation
        if self.registered_in_netherlands is not None:
            context["registered_in_netherlands"] = self.registered_in_netherlands
        return context


@dataclass(frozen=True)
class AccountCredentials:
    """Per-provider credentials used only for a provider login page."""

    username: str
    password: str


def submission_profile(settings: Settings) -> ApplicantProfile | None:
    profile = settings.applicant
    if profile is None:
        return None
    return replace(
        profile,
        first_name=os.getenv("APPLICANT_FIRST_NAME") or profile.first_name,
        last_name=os.getenv("APPLICANT_LAST_NAME") or profile.last_name,
        current_housing_situation=os.getenv("APPLICANT_HOUSING_SITUATION")
        or profile.current_housing_situation,
        registered_in_netherlands=(
            os.getenv("APPLICANT_REGISTERED_NL", "").lower() == "true"
        )
        if "APPLICANT_REGISTERED_NL" in os.environ
        else profile.registered_in_netherlands,
    )


def submission_problems(profile: ApplicantProfile | None) -> list[str]:
    if profile is None:
        return ["Applicant profile is missing"]
    problems = []
    if profile.first_name.casefold() in {"test", "dry", "your first name"}:
        problems.append("applicant.first_name contains a test placeholder")
    if profile.last_name.casefold() in {"applicant", "run", "your last name"}:
        problems.append("applicant.last_name contains a test placeholder")
    return problems


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
    openrouter_api_key = data.get("openrouter_api_key")
    openrouter_model = data.get("openrouter_model", "openai/gpt-6-luna")
    frontend_auth_data = data.get("frontend_auth")
    applicant_data = data.get("applicant")
    if not isinstance(email, str) or not email.strip():
        raise ValueError("values.yaml requires a non-empty email")
    if not isinstance(password, str) or not password.strip():
        raise ValueError("values.yaml requires a non-empty password")
    if not isinstance(database, str) or not database.strip():
        raise ValueError("values.yaml database must be a non-empty path")
    if stekkies_cookie is not None and (
        not isinstance(stekkies_cookie, str) or not stekkies_cookie.strip()
    ):
        raise ValueError("values.yaml stekkies_cookie must be a non-empty string")
    if openrouter_api_key is not None and (
        not isinstance(openrouter_api_key, str) or not openrouter_api_key.strip()
    ):
        openrouter_api_key = None
    if not isinstance(openrouter_model, str) or not openrouter_model.strip():
        raise ValueError("values.yaml openrouter_model must be a non-empty string")

    frontend_auth = None
    if frontend_auth_data is not None:
        if not isinstance(frontend_auth_data, dict):
            raise ValueError("values.yaml frontend_auth must be a mapping")
        username = frontend_auth_data.get("username")
        frontend_password = frontend_auth_data.get("password")
        if (
            not isinstance(username, str)
            or not username.strip()
            or not isinstance(frontend_password, str)
            or not frontend_password.strip()
        ):
            raise ValueError(
                "values.yaml frontend_auth requires non-empty username and password"
            )
        frontend_auth = FrontendAuth(
            username=username.strip(), password=frontend_password.strip()
        )

    applicant = None
    if applicant_data is not None:
        if not isinstance(applicant_data, dict):
            raise ValueError("values.yaml applicant must be a mapping")
        required_profile_fields = ("first_name", "last_name", "phone", "email")
        missing = [
            name
            for name in required_profile_fields
            if not isinstance(applicant_data.get(name), str)
            or not applicant_data[name].strip()
        ]
        single_message = applicant_data.get(
            "message_single_person", applicant_data.get("message")
        )
        two_person_message = applicant_data.get("message_two_person")
        current_housing_situation = applicant_data.get("current_housing_situation")
        registered_in_netherlands = applicant_data.get("registered_in_netherlands")
        if current_housing_situation is not None and (
            not isinstance(current_housing_situation, str)
            or not current_housing_situation.strip()
        ):
            missing.append("current_housing_situation")
        if registered_in_netherlands is not None and not isinstance(
            registered_in_netherlands, bool
        ):
            missing.append("registered_in_netherlands")
        if not isinstance(single_message, str) or not single_message.strip():
            missing.append("message_single_person")
        if not isinstance(two_person_message, str) or not two_person_message.strip():
            missing.append("message_two_person")
        if missing:
            raise ValueError(
                "values.yaml applicant requires non-empty " + ", ".join(missing)
            )
        applicant = ApplicantProfile(
            **{name: applicant_data[name].strip() for name in required_profile_fields},
            message_single_person=single_message.strip(),
            message_two_person=two_person_message.strip(),
            current_housing_situation=current_housing_situation.strip()
            if current_housing_situation
            else None,
            registered_in_netherlands=registered_in_netherlands,
        )

    return Settings(
        email=email.strip(),
        password=password,
        database=database.strip(),
        stekkies_cookie=stekkies_cookie.strip() if stekkies_cookie else None,
        applicant=applicant,
        openrouter_api_key=openrouter_api_key.strip() if openrouter_api_key else None,
        openrouter_model=openrouter_model.strip(),
        frontend_auth=frontend_auth,
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
        if (
            not isinstance(host, str)
            or not host.strip()
            or not isinstance(credentials, dict)
        ):
            raise ValueError(
                "each accounts.yaml account needs a hostname and credential mapping"
            )
        username = credentials.get("username")
        password = credentials.get("password")
        if (
            not isinstance(username, str)
            or not username.strip()
            or not isinstance(password, str)
            or not password.strip()
        ):
            raise ValueError(
                f"accounts.yaml requires non-empty username and password for {host}"
            )
        accounts[host.strip().lower()] = AccountCredentials(
            username.strip(), password.strip()
        )
    return accounts
